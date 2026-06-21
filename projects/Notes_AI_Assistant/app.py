import os
import re
import json
import subprocess
import tempfile
import threading
import logging
import webview
from datetime import datetime
from dotenv import load_dotenv
from notes_reader import get_relevant_notes, get_folders, count_notes
from claude_client import prepare_talking_points, ask_followup, extract_search_keywords, summarise_notes, rank_notes, DEFAULT_MODEL


load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

logging.basicConfig(
    filename='/tmp/notes_ai.log',
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s'
)

CONFIG_PATH = os.path.expanduser('~/.notesai_config.json')


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        logging.warning(f"Config file corrupted ({e}) — backing up and resetting")
        try:
            os.rename(CONFIG_PATH, CONFIG_PATH + '.bak')
        except Exception:
            pass
        return {}
    except Exception as e:
        logging.warning(f"Failed to load config: {e}")
        return {}


def save_config(data: dict):
    try:
        existing = load_config()
        existing.update(data)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(existing, f)
    except Exception as e:
        logging.warning(f"Failed to save config: {e}")



def markdown_to_notes_html(content: str) -> str:
    """Convert markdown to HTML suitable for Apple Notes.
    - h1 → Title style (only used for note title)
    - h2/h3 → <p><b> (Body Bold) to stay in Body font
    - bullet lists, bold, paragraphs preserved
    """
    lines = content.split('\n')
    html_lines = []
    in_list = False
    for line in lines:
        if re.match(r'^#{1,3}\s+', line):
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            heading = re.sub(r'^#{1,3}\s+', '', line).strip()
            html_lines.append(f'<p><b>{heading}</b></p>')
        elif re.match(r'^-{3,}\s*$', line):
            # Horizontal rule — convert to spacing, don't render literally
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            html_lines.append('<p></p>')
        elif re.match(r'^[-*]\s+', line):
            if not in_list:
                html_lines.append('<ul>')
                in_list = True
            item = re.sub(r'^[-*]\s+', '', line).strip()
            item = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', item)
            html_lines.append(f'<li>{item}</li>')
        elif line.strip() == '':
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            html_lines.append('<p></p>')
        else:
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            line = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', line)
            html_lines.append(f'<p>{line}</p>')
    if in_list:
        html_lines.append('</ul>')
    return '\n'.join(html_lines)


def save_to_notes(title: str, content: str):
    body_html = markdown_to_notes_html(content)
    full_html = f'<h1>{title}</h1>{body_html}'
    html_path = None
    script_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
            f.write(full_html)
            html_path = f.name
        script = f'''tell application "Notes"
\tset noteBody to read POSIX file "{html_path}" as «class utf8»
\tmake new note with properties {{body:noteBody}}
end tell'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.applescript', delete=False, encoding='utf-8') as f:
            f.write(script)
            script_path = f.name
        result = subprocess.run(['osascript', script_path], capture_output=True)
        if result.returncode != 0:
            err = result.stderr.decode('utf-8', errors='replace').strip()
            raise RuntimeError(err or 'AppleScript failed to save note')
    finally:
        if script_path and os.path.exists(script_path):
            os.unlink(script_path)
        if html_path and os.path.exists(html_path):
            os.unlink(html_path)


class Api:
    def __init__(self):
        self.window = None
        self._history = []
        self._all_content = []
        self._meeting_title = ''
        self._model = DEFAULT_MODEL
        self._found_notes = []
        self._search_payload = {}

    # Single JSON string argument — avoids pywebview multi-arg issues
    def run_search(self, payload_json):
        logging.info(f"run_search called: {payload_json[:80]}")
        payload = json.loads(payload_json)
        self._search_payload = payload
        meeting_desc = payload['meeting_desc']
        self._model = payload.get('model', DEFAULT_MODEL)
        self.window.evaluate_js("document.getElementById('status').textContent = 'Starting…'")

        def run():
            try:
                self._history = []
                self._all_content = []
                self._found_notes = []
                logging.info(f"Searching: {meeting_desc!r} model={self._model!r}")
                self.window.evaluate_js("setStatus('keywords')")

                keywords = extract_search_keywords(meeting_desc, self._model)
                logging.info(f"Extracted keywords: {keywords}")

                folder = payload.get('folder', '')
                years = int(payload.get('years', 2))
                total = count_notes(folder)
                self.window.evaluate_js(f"setStatus('searching', {total}, {json.dumps(keywords)})")

                def on_filtered(filtered_count):
                    self.window.evaluate_js(f"setStatus('searching', {filtered_count}, {json.dumps(keywords)})")

                notes = get_relevant_notes(meeting_desc, keywords=keywords, folder=folder, years=years,
                                           on_filtered_count=on_filtered)
                import notes_reader as _nr_mod
                search_stats = dict(_nr_mod._last_search_stats)
                logging.info(f"Got {len(notes)} notes (stats={search_stats})")

                if notes:
                    self.window.evaluate_js(f"setStatus('summarising', {len(notes)})")
                    summaries = summarise_notes(notes, self._model)
                    self.window.evaluate_js(f"setStatus('ranking', {len(notes)})")
                    scores = rank_notes(notes, meeting_desc, self._model)
                else:
                    summaries = []
                    scores = []

                # Sort by score descending, keep original index for generate step
                indexed = list(enumerate(notes))
                if scores:
                    indexed.sort(key=lambda x: scores[x[0]], reverse=True)

                self._found_notes = notes  # keep original order for index lookup
                notes_meta = [{'title': notes[i]['title'], 'date': notes[i]['date'], 'orig_idx': i} for i, _ in indexed]
                summaries_sorted = [summaries[i] if summaries else {"summary": "", "people": []} for i, _ in indexed]
                scores_sorted = [scores[i] if scores else 5 for i, _ in indexed]

                # Save current settings to config (Feature 3)
                save_config({
                    'last_folder': payload.get('folder', ''),
                    'last_years': payload.get('years', 2),
                    'last_fmt': payload.get('fmt', 'bullets'),
                    'last_model': payload.get('model', DEFAULT_MODEL),
                })

                self.window.evaluate_js(
                    f"showNotesList({json.dumps(notes_meta)}, {json.dumps(summaries_sorted)}, {json.dumps(scores_sorted)}, {json.dumps(search_stats)})"
                )
                if notes:
                    self.window.evaluate_js(f"setStatus('review', {len(notes)})")
                else:
                    self.window.evaluate_js("setStatus('nothinking')")

            except Exception as e:
                logging.exception("Error in search")
                self.window.evaluate_js(f"setError({json.dumps(str(e))})")

        threading.Thread(target=run, daemon=True).start()

    def run_generate(self, gen_payload_json):
        gen = json.loads(gen_payload_json)
        selected = gen.get('selected', [])
        extra_sources = gen.get('extra_sources', '')

        payload = self._search_payload
        meeting_desc = payload['meeting_desc']
        fmt = payload.get('fmt', 'bullets')

        # Use full untruncated content for generation
        filtered = []
        for i in selected:
            if i < len(self._found_notes):
                note = dict(self._found_notes[i])
                note['content'] = note.get('full_content', note['content'])
                filtered.append(note)

        def run():
            try:
                if filtered:
                    self.window.evaluate_js(f"setStatus('thinking', {len(filtered)})")
                else:
                    self.window.evaluate_js("setStatus('nothinking')")

                self.window.evaluate_js("startStream('Analysis')")

                def on_chunk(text):
                    self.window.evaluate_js(f"appendChunk({json.dumps(text)})")

                result, history = prepare_talking_points(
                    filtered, meeting_desc, fmt, extra_sources, self._model, on_chunk)
                self._history = history
                self._all_content = [result]
                self._meeting_title = f"Meeting Prep: {meeting_desc[:40]} ({datetime.now().strftime('%d %b %Y %H:%M')})"

                logging.info("Got talking points")
                self.window.evaluate_js(f"finalizeStream({json.dumps(result)})")

            except Exception as e:
                logging.exception("Error in generate")
                self.window.evaluate_js(f"setError({json.dumps(str(e))})")

        threading.Thread(target=run, daemon=True).start()

    def run_followup(self, question):
        def run():
            try:
                logging.info(f"Follow-up: {question!r}")
                self.window.evaluate_js("startFollowupStream()")

                def on_chunk(text):
                    self.window.evaluate_js(f"appendChunk({json.dumps(text)})")

                result, history = ask_followup(question, self._history, self._model, on_chunk)
                self._history = history
                self._all_content.append(f"Q: {question}\n\n{result}")

                self.window.evaluate_js(f"finalizeFollowupStream({json.dumps(question)}, {json.dumps(result)})")

            except Exception as e:
                logging.exception("Error in followup")
                self.window.evaluate_js(f"setError({json.dumps(str(e))})")

        threading.Thread(target=run, daemon=True).start()

    def run_save(self):
        try:
            save_to_notes(self._meeting_title, '\n\n---\n\n'.join(self._all_content))
            return {'ok': True}
        except Exception as e:
            logging.exception("Error saving to Notes")
            return {'ok': False, 'error': str(e)}

    def process_binary_file(self, filename, b64data, ext):
        def run():
            try:
                import base64, io
                raw = base64.b64decode(b64data.split(',', 1)[-1])
                if ext == 'pdf':
                    from pypdf import PdfReader
                    reader = PdfReader(io.BytesIO(raw))
                    text = '\n\n'.join(p.extract_text() or '' for p in reader.pages).strip()
                    if not text:
                        logging.warning(f"PDF {filename}: no text extracted (may be scanned image)")
                        self.window.evaluate_js(f"setError({json.dumps('No text could be extracted from ' + filename + ' — it may be a scanned image.')})")
                        return
                elif ext == 'docx':
                    from docx import Document
                    doc = Document(io.BytesIO(raw))
                    text = '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
                else:
                    text = raw.decode('utf-8', errors='replace')
                self.window.evaluate_js(f"onFileLoaded({json.dumps(filename)}, {json.dumps(text)})")
            except Exception as e:
                logging.exception("Error processing file")
                self.window.evaluate_js(f"setError({json.dumps('File error: ' + str(e))})")
        threading.Thread(target=run, daemon=True).start()

    def open_note(self, title):
        safe = title.replace('\\', '\\\\').replace('"', '\\"')
        script = f'''tell application "Notes"
\tactivate
\tset matchNote to first note whose name is "{safe}"
\tshow matchNote
end tell'''
        try:
            subprocess.run(['osascript', '-e', script], capture_output=True, timeout=10)
        except Exception as e:
            logging.warning(f"open_note failed: {e}")

    def get_folders(self):
        return get_folders()

    def get_saved_settings(self):
        """Return persisted folder/years/fmt/model for UI restore (Feature 3)."""
        cfg = load_config()
        return {
            'last_folder': cfg.get('last_folder'),
            'last_years': cfg.get('last_years'),
            'last_fmt': cfg.get('last_fmt'),
            'last_model': cfg.get('last_model'),
        }

    def save_settings(self, payload_json):
        """Save folder/years/fmt/model to config (Feature 3)."""
        try:
            data = json.loads(payload_json)
            save_config({
                'last_folder': data.get('folder', ''),
                'last_years': data.get('years', 2),
                'last_fmt': data.get('fmt', 'bullets'),
                'last_model': data.get('model', DEFAULT_MODEL),
            })
        except Exception as e:
            logging.warning(f"save_settings failed: {e}")

    def run_clear(self):
        self._history = []
        self._all_content = []
        self._meeting_title = ''
        self._found_notes = []
        self._search_payload = {}

    def on_shown(self):
        config = load_config()
        x, y = config.get('x'), config.get('y')
        w, h = config.get('width'), config.get('height')
        if all(v is not None for v in (x, y, w, h)):
            self.window.resize(int(w), int(h))
            self.window.move(int(x), int(y))
        self._win_x = getattr(self.window, 'x', None)
        self._win_y = getattr(self.window, 'y', None)
        self._win_w = getattr(self.window, 'width', None)
        self._win_h = getattr(self.window, 'height', None)

    def on_moved(self, x, y):
        self._win_x = x
        self._win_y = y

    def on_resized(self, width, height):
        self._win_w = width
        self._win_h = height

    def on_closed(self):
        x = getattr(self, '_win_x', None)
        y = getattr(self, '_win_y', None)
        w = getattr(self, '_win_w', None)
        h = getattr(self, '_win_h', None)
        if all(v is not None for v in (x, y, w, h)):
            save_config({'x': x, 'y': y, 'width': w, 'height': h})


HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --ink:    #1a1a2e;
    --ink2:   #2d2d44;
    --accent: #4f8ef7;
    --green:  #30b87a;
    --red:    #e5534b;
    --bg:     #f7f6f3;
    --card:   #ffffff;
    --border: rgba(0,0,0,0.08);
    --muted:  #9494a8;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, Helvetica, sans-serif;
    font-size: 14px;
    background: var(--bg);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    color: var(--ink);
  }

  /* ── Dark header ── */
  #header {
    background: var(--ink);
    padding: 18px 20px 14px;
    flex-shrink: 0;
  }

  .header-top {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
  }

  h1 {
    font-size: 15px;
    font-weight: 600;
    color: #ffffff;
    letter-spacing: -0.2px;
    flex: 1;
  }

  .h1-sub {
    font-size: 11px;
    font-weight: 400;
    color: rgba(255,255,255,0.4);
    margin-left: 6px;
    letter-spacing: 0.2px;
  }

  #clear-btn {
    background: rgba(229,83,75,0.15);
    border: 1px solid rgba(229,83,75,0.3);
    color: #f87171;
    font-size: 12px;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
    padding: 4px 10px;
    border-radius: 6px;
    display: none;
    transition: background 0.15s;
  }
  #clear-btn:hover { background: rgba(229,83,75,0.25); }

  /* ── Meeting input (full width, multiline) ── */
  #meeting-input {
    width: 100%;
    height: 60px;
    padding: 10px 13px;
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 9px;
    font-size: 14px;
    font-family: inherit;
    outline: none;
    background: rgba(255,255,255,0.08);
    color: #ffffff;
    resize: vertical;
    transition: border-color 0.15s, background 0.15s;
    margin-bottom: 8px;
    display: block;
  }
  #meeting-input::placeholder { color: rgba(255,255,255,0.3); }
  #meeting-input:focus {
    border-color: var(--accent);
    background: rgba(255,255,255,0.12);
  }

  /* ── Controls row (dropdowns + button) ── */
  #input-row {
    display: flex;
    gap: 8px;
    margin-bottom: 10px;
    align-items: center;
  }

  select {
    appearance: none;
    -webkit-appearance: none;
    padding: 9px 14px;
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 9px;
    font-size: 13px;
    font-family: inherit;
    background: rgba(255,255,255,0.08);
    color: rgba(255,255,255,0.85);
    outline: none;
    cursor: pointer;
    transition: border-color 0.15s;
  }
  select:focus { border-color: var(--accent); }
  select option { background: #2a2a40; color: #fff; }

  #prepare-btn {
    padding: 9px 18px;
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 9px;
    font-size: 14px;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
    white-space: nowrap;
    transition: opacity 0.15s, transform 0.1s;
    letter-spacing: -0.1px;
  }
  #prepare-btn:hover:not(:disabled) { opacity: 0.88; }
  #prepare-btn:active:not(:disabled) { transform: scale(0.97); }
  #prepare-btn:disabled { opacity: 0.4; cursor: default; }

  /* ── Sources toggle ── */
  #header-controls { margin-top: 2px; }

  #sources-toggle {
    background: none;
    border: none;
    color: rgba(255,255,255,0.45);
    font-size: 12px;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
    padding: 0;
    transition: color 0.15s;
    letter-spacing: 0.1px;
  }
  #sources-toggle:hover { color: rgba(255,255,255,0.75); }

  #sources-area { display: none; margin-top: 10px; }

  #sources-input {
    width: 100%;
    height: 44px;
    padding: 7px 11px;
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 9px;
    font-size: 13px;
    font-family: inherit;
    resize: vertical;
    outline: none;
    background: rgba(255,255,255,0.07);
    color: #fff;
    transition: border-color 0.15s;
  }
  #sources-input::placeholder { color: rgba(255,255,255,0.3); }
  #sources-input:focus { border-color: var(--accent); }
  #sources-input.drag-over { border-color: var(--accent); background: rgba(79,142,247,0.15); }

  #file-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }

  .file-chip {
    background: rgba(79,142,247,0.2);
    color: #93bbfd;
    border: 1px solid rgba(79,142,247,0.3);
    border-radius: 20px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .file-chip button {
    background: none;
    border: none;
    color: rgba(255,255,255,0.35);
    font-size: 12px;
    cursor: pointer;
    padding: 0;
    line-height: 1;
    transition: color 0.15s;
  }
  .file-chip button:hover { color: var(--red); }

  /* ── Status bar ── */
  #status {
    padding: 6px 20px;
    font-size: 12px;
    color: var(--muted);
    flex-shrink: 0;
    min-height: 28px;
    display: flex;
    align-items: center;
    background: #eeecea;
    border-bottom: 1px solid var(--border);
    letter-spacing: 0.1px;
  }

  /* ── Conversation ── */
  #conversation {
    flex: 1;
    overflow-y: auto;
    padding: 18px 20px;
    scroll-behavior: smooth;
  }
  #conversation::-webkit-scrollbar { width: 5px; }
  #conversation::-webkit-scrollbar-track { background: transparent; }
  #conversation::-webkit-scrollbar-thumb { background: #ccc; border-radius: 3px; }

  /* ── Response cards ── */
  .response-block {
    background: var(--card);
    border-radius: 12px;
    padding: 16px 18px 18px;
    margin-top: 14px;
    line-height: 1.75;
    font-size: 14px;
    user-select: text;
    -webkit-user-select: text;
    border-left: 3px solid var(--accent);
    box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 6px 18px rgba(0,0,0,0.05);
    animation: fadeUp 0.2s ease;
  }

  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .response-label {
    font-size: 10px;
    color: var(--accent);
    margin-bottom: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
  }

  .stream-content { white-space: pre-wrap; color: #444; }

  .markdown { color: var(--ink); }
  .markdown h1, .markdown h2, .markdown h3 {
    font-size: 14px;
    font-weight: 700;
    color: var(--ink);
    margin: 14px 0 4px;
  }
  .markdown h1:first-child,
  .markdown h2:first-child,
  .markdown h3:first-child { margin-top: 0; }
  .markdown p { margin: 5px 0; color: #3a3a4a; }
  .markdown ul, .markdown ol { padding-left: 20px; margin: 6px 0; }
  .markdown li { margin: 4px 0; color: #3a3a4a; }
  .markdown strong { font-weight: 700; color: var(--ink); }
  .markdown em { font-style: italic; }
  .markdown code {
    background: #f0eeff;
    color: #6c47d4;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: 'SF Mono', Menlo, monospace;
    font-size: 12.5px;
  }
  .markdown hr { border: none; border-top: 1px solid #ece9e4; margin: 12px 0; }

  /* ── Follow-up ── */
  .followup-q-wrap {
    display: flex;
    justify-content: flex-end;
    margin-top: 14px;
    animation: fadeUp 0.2s ease;
  }

  .followup-q {
    background: var(--ink);
    color: rgba(255,255,255,0.9);
    border-radius: 16px 16px 3px 16px;
    padding: 9px 15px;
    max-width: 70%;
    font-size: 14px;
    line-height: 1.5;
  }

  #followup-row { display: flex; gap: 8px; margin-top: 14px; }

  #followup-input {
    flex: 1;
    padding: 9px 13px;
    border: 1.5px solid #dddad6;
    border-radius: 9px;
    font-size: 14px;
    font-family: inherit;
    outline: none;
    background: white;
    color: var(--ink);
    display: none;
    transition: border-color 0.15s;
  }
  #followup-input::placeholder { color: #bbb; }
  #followup-input:focus { border-color: var(--accent); }

  #followup-btn {
    padding: 9px 16px;
    background: var(--green);
    color: white;
    border: none;
    border-radius: 9px;
    font-size: 14px;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
    display: none;
    transition: opacity 0.15s;
  }
  #followup-btn:hover:not(:disabled) { opacity: 0.85; }
  #followup-btn:disabled { opacity: 0.4; cursor: default; }

  /* ── Action bar ── */
  #action-bar {
    padding: 10px 20px;
    background: #eeecea;
    border-top: 1px solid var(--border);
    display: none;
    justify-content: flex-end;
    gap: 8px;
    flex-shrink: 0;
  }

  #btn-save {
    padding: 8px 18px;
    background: var(--green);
    color: white;
    border: none;
    border-radius: 9px;
    font-size: 13.5px;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
    transition: opacity 0.15s;
  }
  #btn-save:hover:not(:disabled) { opacity: 0.85; }
  #btn-save:disabled { opacity: 0.4; cursor: default; }


  /* ── Notes checklist card ── */
  .notes-checklist {
    background: var(--card);
    border-radius: 12px;
    padding: 16px 18px 14px;
    margin-top: 14px;
    border-left: 3px solid var(--accent);
    box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 6px 18px rgba(0,0,0,0.05);
    animation: fadeUp 0.2s ease;
  }

  .note-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 0;
    border-bottom: 1px solid #f2f0ed;
  }
  .note-item:last-of-type { border-bottom: none; }
  .note-item input[type="checkbox"] {
    margin-top: 3px;
    accent-color: var(--accent);
    width: 15px;
    height: 15px;
    flex-shrink: 0;
    cursor: pointer;
  }

  .note-info { flex: 1; min-width: 0; }

  .note-title {
    font-size: 13.5px;
    font-weight: 600;
    color: var(--ink);
    margin-bottom: 2px;
  }
  .note-date {
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 4px;
  }
  .note-summary {
    font-size: 13px;
    color: #4a4a5a;
    line-height: 1.5;
    margin-bottom: 5px;
  }
  .note-people {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .person-chip {
    background: rgba(79,142,247,0.1);
    color: #3a6cc4;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11.5px;
    font-weight: 500;
  }

  .checklist-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid #f2f0ed;
  }
  .checklist-hint {
    font-size: 12px;
    color: var(--muted);
  }
  .generate-btn {
    padding: 9px 18px;
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 9px;
    font-size: 13.5px;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
    transition: opacity 0.15s;
  }
  .generate-btn:hover { opacity: 0.85; }

  .open-note-btn {
    background: rgba(251,146,60,0.12);
    border: 1px solid rgba(251,146,60,0.35);
    color: #c2610a;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    padding: 2px 8px;
    border-radius: 6px;
    line-height: 1.4;
    flex-shrink: 0;
    transition: background 0.15s, border-color 0.15s;
    text-decoration: none;
  }
  .open-note-btn:hover { background: rgba(251,146,60,0.22); border-color: rgba(251,146,60,0.55); }

  .score-badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 10px;
    margin-left: 8px;
    vertical-align: middle;
    flex-shrink: 0;
  }
</style>
</head>
<body>
<div id="header">
  <div class="header-top">
    <h1>📋 Notes AI Assistant<span class="h1-sub">Meeting Prep</span></h1>
    <button id="clear-btn" onclick="clearConversation()">✕ Clear</button>
  </div>
  <textarea id="meeting-input" placeholder="Describe your meeting…"></textarea>
  <div id="input-row">
    <select id="folder-select">
      <option value="">All Folders</option>
    </select>
    <select id="years-select">
      <option value="1">1 yr</option>
      <option value="2" selected>2 yrs</option>
      <option value="3">3 yrs</option>
      <option value="5">5 yrs</option>
      <option value="0">All time</option>
    </select>
    <select id="format-select">
      <option value="bullets">Bullets</option>
      <option value="brief">Brief</option>
    </select>
    <select id="model-select">
      <option value="claude-haiku-4-5-20251001">Haiku</option>
      <option value="claude-sonnet-4-6">Sonnet</option>
    </select>
    <button id="prepare-btn" style="margin-left:auto;">Prepare →</button>
  </div>
  <div id="header-controls">
    <button id="sources-toggle" onclick="toggleSources()">＋ Add Sources</button>
  </div>
  <div id="sources-area">
    <textarea id="sources-input" placeholder="Paste text or drop files here (PDF, DOCX, TXT, MD)…"></textarea>
    <div id="file-chips"></div>
  </div>
</div>
<div id="status">Enter your meeting description above and click Prepare.</div>
<div id="conversation">
  <div id="followup-row">
    <input id="followup-input" type="text" placeholder="Ask a follow-up question…" />
    <button id="followup-btn" onclick="askFollowup()">Ask</button>
  </div>
</div>
<div id="action-bar">
<button id="btn-save" onclick="saveToNotes()">Save to Notes</button>
</div>

<script>
  window.onerror = function(msg, src, line, col, err) {
    document.getElementById('status').style.color = '#e5534b';
    document.getElementById('status').textContent = 'JS Error: ' + msg + ' (line ' + line + ')';
    return false;
  };

  var _fileTexts = {};

  function buildExtraSources(pastedText) {
    var nl = String.fromCharCode(10);
    var fileTexts = Object.entries(_fileTexts).map(function(e) {
      return '--- ' + e[0] + ' ---' + nl + e[1];
    }).join(nl + nl);
    var extra = pastedText || '';
    if (fileTexts) extra = extra ? extra + nl + nl + fileTexts : fileTexts;
    return extra;
  }

  document.getElementById('prepare-btn').addEventListener('click', prepare);
  document.getElementById('meeting-input').addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) prepare();
  });

  window.addEventListener('pywebviewready', function() {
    // Feature 3: load saved settings first, then populate folders and restore
    pywebview.api.get_saved_settings().then(function(saved) {
      pywebview.api.get_folders().then(function(folders) {
        var sel = document.getElementById('folder-select');
        folders.forEach(function(f) {
          var opt = document.createElement('option');
          opt.value = f;
          opt.textContent = f;
          if (f === 'A*STAR') opt.selected = true;
          sel.appendChild(opt);
        });
        // Restore last-used folder (overrides the A*STAR default if saved)
        if (saved && saved.last_folder != null) {
          var fSel = document.getElementById('folder-select');
          for (var fi = 0; fi < fSel.options.length; fi++) {
            if (fSel.options[fi].value === saved.last_folder) { fSel.selectedIndex = fi; break; }
          }
        }
      });
      // Restore years, format, model (don't need folders to be loaded first)
      if (saved && saved.last_years != null) {
        var ySel = document.getElementById('years-select');
        for (var yi = 0; yi < ySel.options.length; yi++) {
          if (ySel.options[yi].value === String(saved.last_years)) { ySel.selectedIndex = yi; break; }
        }
      }
      if (saved && saved.last_fmt != null) {
        var fmtSel = document.getElementById('format-select');
        for (var fmti = 0; fmti < fmtSel.options.length; fmti++) {
          if (fmtSel.options[fmti].value === saved.last_fmt) { fmtSel.selectedIndex = fmti; break; }
        }
      }
      if (saved && saved.last_model != null) {
        var mSel = document.getElementById('model-select');
        for (var mi = 0; mi < mSel.options.length; mi++) {
          if (mSel.options[mi].value === saved.last_model) { mSel.selectedIndex = mi; break; }
        }
      }
    });
  });

  document.getElementById('followup-input').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') askFollowup();
  });

  function toggleSources() {
    var area = document.getElementById('sources-area');
    var btn = document.getElementById('sources-toggle');
    if (area.style.display === 'none' || !area.style.display) {
      area.style.display = 'block';
      btn.textContent = '− Hide Sources';
    } else {
      area.style.display = 'none';
      btn.textContent = '＋ Add Sources';
    }
  }

  function clearConversation() {
    var conv = document.getElementById('conversation');
    var blocks = conv.querySelectorAll('.response-block, .followup-q-wrap');
    blocks.forEach(function(b) { b.remove(); });
    var cl = document.getElementById('notes-checklist'); if (cl) cl.remove();
    document.getElementById('followup-input').style.display = 'none';
    document.getElementById('followup-btn').style.display = 'none';
    document.getElementById('action-bar').style.display = 'none';
    document.getElementById('clear-btn').style.display = 'none';
    document.getElementById('status').textContent = 'Enter your meeting description above and click Prepare.';
    document.getElementById('meeting-input').value = '';
    document.getElementById('meeting-input').style.height = '60px';
    document.getElementById('prepare-btn').disabled = false;
    _fileTexts = {};
    document.getElementById('file-chips').innerHTML = '';
    pywebview.api.run_clear();
  }

  function prepare() {
    try {
    var desc = document.getElementById('meeting-input').value.trim();
    if (!desc) { document.getElementById('status').textContent = 'Type a meeting description first.'; return; }
    var fmt = document.getElementById('format-select').value;
    var model = document.getElementById('model-select').value;
    var extra = buildExtraSources(document.getElementById('sources-input').value);
    document.getElementById('prepare-btn').disabled = true;
    document.getElementById('action-bar').style.display = 'none';
    document.getElementById('clear-btn').style.display = 'none';
    document.getElementById('followup-input').style.display = 'none';
    document.getElementById('followup-btn').style.display = 'none';
    var conv = document.getElementById('conversation');
    var blocks = conv.querySelectorAll('.response-block, .followup-q-wrap');
    blocks.forEach(function(b) { b.remove(); });
    var cl = document.getElementById('notes-checklist'); if (cl) cl.remove();
    var folder = document.getElementById('folder-select').value;
    var years = parseInt(document.getElementById('years-select').value, 10);
    var payload = JSON.stringify({meeting_desc: desc, fmt: fmt, model: model, extra_sources: extra, folder: folder, years: years});
    pywebview.api.run_search(payload);
    } catch(e) {
      document.getElementById('status').style.color = '#e5534b';
      document.getElementById('status').textContent = 'prepare() error: ' + e.message;
      document.getElementById('prepare-btn').disabled = false;
    }
  }

  var _searchTimer = null;

  function clearSearchTimer() {
    if (_searchTimer) { clearInterval(_searchTimer); _searchTimer = null; }
  }

  function setStatus(state, count, keywords) {
    var el = document.getElementById('status');
    el.style.color = '';
    clearSearchTimer();
    if (state === 'keywords') {
      el.innerHTML = '🔑 Extracting keywords…';
    } else if (state === 'searching') {
      var kStr = (keywords && keywords.length) ? ' — <em>' + keywords.join(', ') + '</em>' : '';
      var totalNotes = count || 0;
      var screened = 0;
      var update = function() {
        if (totalNotes) {
          screened = Math.min(screened + Math.ceil(totalNotes / 30), totalNotes);
          el.innerHTML = '🔍 Screening notes' + kStr + '… <span style="color:#aeaeb2">(' + screened + ' / ' + totalNotes + ')</span>';
        } else {
          el.innerHTML = '🔍 Screening notes' + kStr + '…';
        }
      };
      update();
      _searchTimer = setInterval(update, 1200);
    } else if (state === 'summarising') {
      el.innerHTML = '📝 Summarising ' + count + ' note' + (count === 1 ? '' : 's') + '…';
    } else if (state === 'ranking') {
      el.innerHTML = '🎯 Ranking ' + count + ' note' + (count === 1 ? '' : 's') + ' by relevance…';
    } else if (state === 'review') {
      el.innerHTML = '✔ Found ' + count + ' note' + (count === 1 ? '' : 's') + ' · review below, then click Generate';
    } else if (state === 'thinking') {
      el.innerHTML = '🤔 Generating from ' + count + ' note' + (count === 1 ? '' : 's') + '…';
    } else if (state === 'nothinking') {
      el.innerHTML = '⚠️ No matching notes found · generating from description only…';
    }
  }

  // Streaming state
  var _streamBlock = null;
  var _streamEl = null;
  var _streamText = '';
  var _streamLabel = '';

  function startStream(label) {
    _streamLabel = label || 'Analysis';
    _streamText = '';
    var conv = document.getElementById('conversation');
    var fuRow = document.getElementById('followup-row');
    _streamBlock = document.createElement('div');
    _streamBlock.className = 'response-block';
    var labelEl = document.createElement('div');
    labelEl.className = 'response-label';
    labelEl.textContent = _streamLabel;
    _streamEl = document.createElement('div');
    _streamEl.className = 'stream-content';
    _streamBlock.appendChild(labelEl);
    _streamBlock.appendChild(_streamEl);
    conv.insertBefore(_streamBlock, fuRow);
  }

  function appendChunk(chunk) {
    _streamText += chunk;
    _streamEl.textContent = _streamText;
    var conv = document.getElementById('conversation');
    conv.scrollTop = conv.scrollHeight;
  }

  function finalizeStream(content) {
    _streamEl.className = 'markdown';
    _streamEl.innerHTML = marked.parse(content);

    // Feature 4: parse References section and add ↗ Open buttons for matched notes
    (function addRefOpenButtons(mdEl) {
      if (!mdEl) return;
      var children = mdEl.children;
      var inRefs = false;
      for (var ci = 0; ci < children.length; ci++) {
        var child = children[ci];
        var tag = child.tagName ? child.tagName.toUpperCase() : '';
        if (!inRefs) {
          // Look for heading or strong/p containing "References"
          var txt = child.textContent || '';
          if ((tag === 'H2' || tag === 'H3' || tag === 'H4' || tag === 'STRONG' || tag === 'P') &&
              txt.trim().toLowerCase().indexOf('references') !== -1) {
            inRefs = true;
          }
        } else {
          if (tag === 'UL' || tag === 'OL') {
            var items = child.querySelectorAll('li');
            items.forEach(function(li) {
              var liText = (li.textContent || '').trim();
              // Find a matching note in _currentNotes
              var matched = null;
              for (var ni = 0; ni < _currentNotes.length; ni++) {
                var noteTitle = _currentNotes[ni].title || '';
                if (liText === noteTitle || liText.indexOf(noteTitle) !== -1 || noteTitle.indexOf(liText) !== -1) {
                  matched = noteTitle;
                  break;
                }
              }
              if (matched) {
                var openBtn = document.createElement('button');
                openBtn.className = 'open-note-btn';
                openBtn.title = 'Open in Notes.app';
                openBtn.textContent = '\u2197 Open';
                openBtn.style.marginLeft = '8px';
                (function(t) {
                  openBtn.addEventListener('click', function(e) {
                    e.preventDefault(); e.stopPropagation();
                    pywebview.api.open_note(t);
                  });
                })(matched);
                li.appendChild(openBtn);
              }
            });
            break; // only process first list after References heading
          } else if (tag === 'H2' || tag === 'H3' || tag === 'H4') {
            break; // hit another heading, stop
          }
        }
      }
    })((_streamEl || document.querySelector('.markdown:last-of-type')) || null);

    _streamBlock = null;
    _streamEl = null;
    _streamText = '';
    document.getElementById('followup-input').style.display = 'block';
    document.getElementById('followup-btn').style.display = 'block';
    var stEl = document.getElementById('status');
    stEl.innerHTML = '✅ Ready';
    stEl.style.color = '';
    document.getElementById('action-bar').style.display = 'flex';
    document.getElementById('clear-btn').style.display = 'inline';
    document.getElementById('prepare-btn').disabled = false;
    var conv = document.getElementById('conversation');
    conv.scrollTop = conv.scrollHeight;
  }

  function startFollowupStream() {
    _streamText = '';
    var conv = document.getElementById('conversation');
    var fuRow = document.getElementById('followup-row');
    // Add question bubble placeholder (will be filled on finalize)
    var qWrap = document.createElement('div');
    qWrap.className = 'followup-q-wrap';
    qWrap.id = 'pending-q-wrap';
    conv.insertBefore(qWrap, fuRow);
    // Start stream block for answer
    _streamBlock = document.createElement('div');
    _streamBlock.className = 'response-block';
    var labelEl = document.createElement('div');
    labelEl.className = 'response-label';
    labelEl.textContent = 'Response';
    _streamEl = document.createElement('div');
    _streamEl.className = 'stream-content';
    _streamBlock.appendChild(labelEl);
    _streamBlock.appendChild(_streamEl);
    conv.insertBefore(_streamBlock, fuRow);
    document.getElementById('followup-btn').disabled = true;
    document.getElementById('status').innerHTML = '🤔 Thinking...';
  }

  function finalizeFollowupStream(question, content) {
    // Fill in question bubble
    var qWrap = document.getElementById('pending-q-wrap');
    if (qWrap) {
      qWrap.removeAttribute('id');
      var qBubble = document.createElement('div');
      qBubble.className = 'followup-q';
      qBubble.textContent = question;
      qWrap.appendChild(qBubble);
    }
    // Render markdown in answer
    _streamEl.className = 'markdown';
    _streamEl.innerHTML = marked.parse(content);
    _streamBlock = null;
    _streamEl = null;
    _streamText = '';
    document.getElementById('followup-input').value = '';
    document.getElementById('followup-btn').disabled = false;
    document.getElementById('status').innerHTML = '✅ Ready';
    var conv = document.getElementById('conversation');
    conv.scrollTop = conv.scrollHeight;
  }

  function setError(msg) {
    clearSearchTimer();
    var el = document.getElementById('status');
    el.textContent = '❌ ' + msg;
    el.style.color = '#e5534b';
    document.getElementById('prepare-btn').disabled = false;
    document.getElementById('followup-btn').disabled = false;
    document.getElementById('clear-btn').style.display = 'inline';
    if (_streamBlock) {
      _streamBlock.remove();
      _streamBlock = null;
      _streamEl = null;
    }
  }

  function askFollowup() {
    var q = document.getElementById('followup-input').value.trim();
    if (!q) return;
    pywebview.api.run_followup(q);
  }

  function saveToNotes() {
    var btn = document.getElementById('btn-save');
    btn.textContent = 'Saving...';
    btn.disabled = true;
    pywebview.api.run_save().then(function(result) {
      if (result && result.ok) {
        btn.textContent = 'Saved ✓';
      } else {
        btn.textContent = 'Save to Notes';
        btn.disabled = false;
        setError(result && result.error ? result.error : 'Failed to save to Notes');
      }
    });
  }

  // Feature 6: Copy conversation as plain text

  function onFileLoaded(filename, text) {
    if (!text || !text.trim()) { setError('File is empty: ' + filename); return; }
    _fileTexts[filename] = text;
    var chips = document.getElementById('file-chips');
    var chipId = "chip_" + filename.replace(/[^a-z0-9]/gi, "_") + "_" + Date.now();
    if (!document.getElementById(chipId)) {
      var chip = document.createElement('div');
      chip.className = 'file-chip';
      chip.id = chipId;
      var label = document.createElement('span');
      label.textContent = '📄 ' + filename;
      var btn = document.createElement('button');
      btn.textContent = '✕';
      btn.onclick = (function(fn, id) { return function() {
        delete _fileTexts[fn];
        var el = document.getElementById(id);
        if (el) el.remove();
      }; })(filename, chipId);
      chip.appendChild(label);
      chip.appendChild(btn);
      chips.appendChild(chip);
    }
  }

  function formatNoteDate(dateStr) {
    if (!dateStr) return '';
    var parts = dateStr.split('-');
    if (parts.length < 3) return dateStr;
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var m = parseInt(parts[1], 10) - 1;
    if (m < 0 || m > 11) return dateStr;
    return parts[2] + ' ' + months[m] + ' ' + parts[0];
  }

  // Feature 4: store notes meta so finalizeStream can add Open buttons to References
  var _currentNotes = [];

  function showNotesList(notes, summaries, scores, stats) {
    // Feature 4: store for use by finalizeStream
    _currentNotes = notes || [];

    var conv = document.getElementById('conversation');
    var fuRow = document.getElementById('followup-row');

    var existing = document.getElementById('notes-checklist');
    if (existing) existing.remove();

    var card = document.createElement('div');
    card.className = 'notes-checklist';
    card.id = 'notes-checklist';

    var labelEl = document.createElement('div');
    labelEl.className = 'response-label';
    // Feature 5: show breakdown of title-matched vs body-matched
    if (notes.length) {
      var labelParts = [];
      if (stats && (stats.title_matched > 0 || stats.body_matched > 0)) {
        if (stats.title_matched > 0) labelParts.push(stats.title_matched + ' from titles');
        if (stats.body_matched > 0) labelParts.push(stats.body_matched + ' from body search');
      }
      var breakdownStr = labelParts.length ? ' \u00b7 ' + labelParts.join(', ') : '';
      labelEl.textContent = "Matched Notes \u00b7 " + notes.length + " notes" + breakdownStr + " \u00b7 uncheck any that aren't relevant";
    } else {
      labelEl.textContent = "No matching notes found";
    }
    card.appendChild(labelEl);

    notes.forEach(function(note, i) {
      var meta = (summaries && summaries[i]) ? summaries[i] : {};

      var item = document.createElement('div');
      item.className = 'note-item';

      var cb = document.createElement('input');
      cb.type = 'checkbox';
      var score = (scores && scores[i] != null) ? scores[i] : 5;
      cb.checked = (score >= 5);
      cb.value = String(note.orig_idx != null ? note.orig_idx : i);

      var info = document.createElement('div');
      info.className = 'note-info';

      var titleEl = document.createElement('div');
      titleEl.className = 'note-title';
      titleEl.textContent = note.title;

      var titleLine = document.createElement('div');
      titleLine.style.cssText = 'display:flex;align-items:center;gap:6px;margin-bottom:2px;';
      titleEl.style.fontWeight = '600';
      titleEl.style.fontSize = '13.5px';
      titleEl.style.color = 'var(--ink)';
      // score badge
      var badge = document.createElement('span');
      badge.className = 'score-badge';
      badge.textContent = score;
      if (score >= 7) { badge.style.background = '#d1fae5'; badge.style.color = '#065f46'; }
      else if (score >= 4) { badge.style.background = '#fef9c3'; badge.style.color = '#713f12'; }
      else { badge.style.background = '#fee2e2'; badge.style.color = '#991b1b'; }
      var openBtn = document.createElement('button');
      openBtn.className = 'open-note-btn';
      openBtn.title = 'Open in Notes.app';
      openBtn.textContent = '↗ Open';
      (function(t) {
        openBtn.addEventListener('click', function(e) {
          e.preventDefault(); e.stopPropagation();
          pywebview.api.open_note(t);
        });
      })(note.title);
      titleLine.appendChild(titleEl);
      titleLine.appendChild(badge);
      titleLine.appendChild(openBtn);
      info.appendChild(titleLine);

      var dateEl = document.createElement('div');
      dateEl.className = 'note-date';
      dateEl.textContent = formatNoteDate(note.date);
      info.appendChild(dateEl);

      if (meta.summary) {
        var sumEl = document.createElement('div');
        sumEl.className = 'note-summary';
        sumEl.textContent = meta.summary;
        info.appendChild(sumEl);
      }

      if (meta.people && meta.people.length > 0) {
        var peopleEl = document.createElement('div');
        peopleEl.className = 'note-people';
        meta.people.forEach(function(person) {
          var chip = document.createElement('span');
          chip.className = 'person-chip';
          chip.textContent = '👤 ' + person;
          peopleEl.appendChild(chip);
        });
        info.appendChild(peopleEl);
      }

      item.appendChild(cb);
      item.appendChild(info);
      card.appendChild(item);
    });

    var footer = document.createElement('div');
    footer.className = 'checklist-footer';
    var hint = document.createElement('span');
    hint.className = 'checklist-hint';
    var initialChecked = notes.filter(function(_, i) { return (scores && scores[i] != null ? scores[i] : 5) >= 5; }).length;
    hint.textContent = notes.length ? (initialChecked + ' note' + (initialChecked === 1 ? '' : 's') + ' selected') : 'Will generate from meeting description only';
    var genBtn = document.createElement('button');
    genBtn.className = 'generate-btn';
    genBtn.textContent = notes.length ? 'Generate Talking Points →' : 'Generate anyway →';
    genBtn.onclick = generateWithSelected;
    footer.appendChild(hint);
    footer.appendChild(genBtn);
    card.appendChild(footer);

    // Update hint as checkboxes change
    card.addEventListener('change', function() {
      var checked = card.querySelectorAll('input[type="checkbox"]:checked').length;
      hint.textContent = checked + ' note' + (checked === 1 ? '' : 's') + ' selected';
    });

    conv.insertBefore(card, fuRow);
    conv.scrollTop = conv.scrollHeight;
    document.getElementById('clear-btn').style.display = 'inline';
  }

  function generateWithSelected() {
    var checklist = document.getElementById('notes-checklist');
    var selected = [];
    if (checklist) {
      checklist.querySelectorAll('input[type="checkbox"]:checked').forEach(function(cb) {
        selected.push(parseInt(cb.value, 10));
      });
      checklist.remove();
    }

    var extra = buildExtraSources(document.getElementById('sources-input').value);
    document.getElementById('clear-btn').style.display = 'none';
    pywebview.api.run_generate(JSON.stringify({selected: selected, extra_sources: extra}));
  }

  // Drag-and-drop onto sources textarea
  var srcEl = document.getElementById('sources-input');
  srcEl.addEventListener('dragover', function(e) {
    e.preventDefault();
    srcEl.classList.add('drag-over');
  });
  srcEl.addEventListener('dragleave', function() {
    srcEl.classList.remove('drag-over');
  });
  srcEl.addEventListener('drop', function(e) {
    e.preventDefault();
    srcEl.classList.remove('drag-over');
    var files = e.dataTransfer.files;
    for (var i = 0; i < files.length; i++) {
      (function(file) {
        var name = file.name;
        var ext = name.split('.').pop().toLowerCase();
        if (ext === 'txt' || ext === 'md' || ext === 'csv') {
          var reader = new FileReader();
          reader.onload = function(ev) { onFileLoaded(name, ev.target.result); };
          reader.readAsText(file);
        } else if (ext === 'pdf' || ext === 'docx') {
          var reader = new FileReader();
          reader.onload = function(ev) {
            pywebview.api.process_binary_file(name, ev.target.result, ext);
          };
          reader.readAsDataURL(file);
        } else {
          setError('Unsupported file type: ' + ext);
        }
      })(files[i]);
    }
  });
</script>
</body>
</html>"""


if __name__ == "__main__":
    api = Api()
    window = webview.create_window(
        'Notes AI Assistant',
        html=HTML,
        js_api=api,
        width=860,
        height=680,
        min_size=(600, 480),
        text_select=True,
    )
    api.window = window
    window.events.shown += api.on_shown
    window.events.moved += api.on_moved
    window.events.resized += api.on_resized
    window.events.closed += api.on_closed
    webview.start()
