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
from notes_reader import get_relevant_notes
from claude_client import prepare_talking_points, ask_followup, DEFAULT_MODEL
from file_reader import extract_text

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
    except Exception:
        return {}


def save_config(data: dict):
    try:
        existing = load_config()
        existing.update(data)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(existing, f)
    except Exception:
        pass



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

    # Single JSON string argument — avoids pywebview multi-arg issues
    def run_prepare(self, payload_json):
        payload = json.loads(payload_json)
        meeting_desc = payload['meeting_desc']
        fmt = payload.get('fmt', 'bullets')
        extra_sources = payload.get('extra_sources', '')
        self._model = payload.get('model', 'claude-haiku-4-5-20251001')

        def run():
            try:
                self._history = []
                self._all_content = []
                logging.info(f"Preparing: {meeting_desc!r} fmt={fmt!r} model={self._model!r}")
                self.window.evaluate_js("setStatus('searching')")

                notes = get_relevant_notes(meeting_desc)
                logging.info(f"Got {len(notes)} notes")

                if notes:
                    self.window.evaluate_js(f"setStatus('thinking', {len(notes)})")
                else:
                    self.window.evaluate_js("setStatus('nothinking')")

                self.window.evaluate_js("startStream('Analysis')")

                def on_chunk(text):
                    self.window.evaluate_js(f"appendChunk({json.dumps(text)})")

                result, history = prepare_talking_points(
                    notes, meeting_desc, fmt, extra_sources, self._model, on_chunk)
                self._history = history
                self._all_content = [result]
                self._meeting_title = f"Meeting Prep: {meeting_desc[:40]} ({datetime.now().strftime('%d %b %Y %H:%M')})"

                logging.info("Got talking points")
                self.window.evaluate_js(f"finalizeStream({json.dumps(result)})")

            except Exception as e:
                logging.exception("Error in prepare")
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

    def run_clear(self):
        self._history = []
        self._all_content = []
        self._meeting_title = ''

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
  body { font-family: -apple-system, Helvetica, sans-serif; font-size: 14px; background: #f5f5f7; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
  #header { background: white; padding: 16px 20px 12px; border-bottom: 1px solid #e0e0e0; flex-shrink: 0; }
  h1 { font-size: 17px; font-weight: 600; color: #1d1d1f; margin-bottom: 10px; }
  #input-row { display: flex; gap: 8px; margin-bottom: 8px; }
  #meeting-input { flex: 1; padding: 8px 12px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 14px; outline: none; }
  #meeting-input:focus { border-color: #007AFF; }
  #format-select, #model-select { padding: 8px 10px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 13px; background: white; outline: none; cursor: pointer; }
  #model-select { color: #555; }
  #prepare-btn { padding: 8px 16px; background: #007AFF; color: white; border: none; border-radius: 8px; font-size: 14px; cursor: pointer; white-space: nowrap; }
  #prepare-btn:disabled { background: #a0c4ff; cursor: default; }
  #header-controls { display: flex; gap: 12px; align-items: center; }
  #sources-toggle { background: none; border: none; color: #007AFF; font-size: 13px; cursor: pointer; padding: 0; }
  #clear-btn { background: none; border: none; color: #FF3B30; font-size: 13px; cursor: pointer; padding: 0; display: none; }
  #sources-area { display: none; margin-top: 8px; }
  #sources-input { width: 100%; height: 72px; padding: 8px 10px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 13px; resize: vertical; font-family: inherit; outline: none; }
  #sources-input:focus { border-color: #007AFF; }
  #sources-input.drag-over { border-color: #007AFF; background: #f0f7ff; }
  #file-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .file-chip { background: #e8f0fe; color: #1a56db; border-radius: 12px; padding: 3px 10px; font-size: 12px; display: flex; align-items: center; gap: 6px; }
  .file-chip button { background: none; border: none; color: #888; font-size: 13px; cursor: pointer; padding: 0; line-height: 1; }
  #status { padding: 8px 20px; font-size: 13px; color: #666; flex-shrink: 0; min-height: 32px; display: flex; align-items: center; }
  #conversation { flex: 1; overflow-y: auto; padding: 0 20px 16px; }
  .response-block { background: white; border-radius: 10px; padding: 16px; margin-top: 12px; line-height: 1.7; font-size: 14px; user-select: text; -webkit-user-select: text; }
  .response-label { font-size: 11px; color: #999; margin-bottom: 8px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .stream-content { white-space: pre-wrap; color: #333; }
  .markdown h1, .markdown h2, .markdown h3 { font-size: 14px; font-weight: 600; color: #1d1d1f; margin: 12px 0 4px; }
  .markdown h1:first-child, .markdown h2:first-child, .markdown h3:first-child { margin-top: 0; }
  .markdown p { margin: 6px 0; }
  .markdown ul, .markdown ol { padding-left: 20px; margin: 4px 0; }
  .markdown li { margin: 3px 0; }
  .markdown strong { font-weight: 600; }
  .markdown em { font-style: italic; }
  .markdown code { background: #f0f0f0; padding: 1px 5px; border-radius: 4px; font-family: monospace; font-size: 13px; }
  .followup-q { background: #007AFF; color: white; border-radius: 18px; padding: 8px 14px; display: inline-block; max-width: 75%; font-size: 14px; margin-top: 12px; }
  #followup-row { display: flex; gap: 8px; margin-top: 12px; }
  #followup-input { flex: 1; padding: 8px 12px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 14px; outline: none; display: none; }
  #followup-input:focus { border-color: #007AFF; }
  #followup-btn { padding: 8px 14px; background: #34C759; color: white; border: none; border-radius: 8px; font-size: 14px; cursor: pointer; display: none; }
  #followup-btn:disabled { background: #a0e0b0; cursor: default; }
  #action-bar { padding: 10px 20px; background: white; border-top: 1px solid #e0e0e0; display: none; justify-content: flex-end; gap: 8px; flex-shrink: 0; }
  #action-bar button { padding: 7px 14px; border-radius: 8px; border: none; font-size: 13px; cursor: pointer; }
  #btn-save { background: #34C759; color: white; }
  #btn-save:disabled { background: #a0e0b0; cursor: default; }
</style>
</head>
<body>
<div id="header">
  <h1>📋 Notes AI Assistant</h1>
  <div id="input-row">
    <input id="meeting-input" type="text" placeholder="Describe your meeting..." />
    <select id="format-select">
      <option value="bullets">Bullet Points</option>
      <option value="brief">Executive Brief</option>
    </select>
    <select id="model-select">
      <option value="claude-haiku-4-5-20251001">Haiku (Fast)</option>
      <option value="claude-sonnet-4-6">Sonnet (Sharp)</option>
    </select>
    <button id="prepare-btn" onclick="prepare()">Prepare</button>
  </div>
  <div id="header-controls">
    <button id="sources-toggle" onclick="toggleSources()">＋ Add Sources</button>
    <button id="clear-btn" onclick="clearConversation()">✕ Clear</button>
  </div>
  <div id="sources-area">
    <textarea id="sources-input" placeholder="Paste text or drop files here (PDF, DOCX, TXT, MD)..."></textarea>
    <div id="file-chips"></div>
  </div>
</div>
<div id="status">Enter your meeting description above and click Prepare.</div>
<div id="conversation">
  <div id="followup-row">
    <input id="followup-input" type="text" placeholder="Ask a follow-up question..." />
    <button id="followup-btn" onclick="askFollowup()">Ask</button>
  </div>
</div>
<div id="action-bar">
  <button id="btn-save" onclick="saveToNotes()">Save to Notes</button>
</div>

<script>

  var _fileTexts = {};

  document.getElementById('meeting-input').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') prepare();
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
    document.getElementById('followup-input').style.display = 'none';
    document.getElementById('followup-btn').style.display = 'none';
    document.getElementById('action-bar').style.display = 'none';
    document.getElementById('clear-btn').style.display = 'none';
    document.getElementById('status').textContent = 'Enter your meeting description above and click Prepare.';
    document.getElementById('meeting-input').value = '';
    document.getElementById('prepare-btn').disabled = false;
    _fileTexts = {};
    document.getElementById('file-chips').innerHTML = '';
    pywebview.api.run_clear();
  }

  function prepare() {
    var desc = document.getElementById('meeting-input').value.trim();
    if (!desc) return;
    var fmt = document.getElementById('format-select').value;
    var model = document.getElementById('model-select').value;
    var extra = document.getElementById('sources-input').value;
    var nl = String.fromCharCode(10);
    var fileTexts = Object.entries(_fileTexts).map(function(e) { return "--- " + e[0] + " ---" + nl + e[1]; }).join(nl + nl);
    if (fileTexts) extra = extra ? extra + nl + nl + fileTexts : fileTexts;
    document.getElementById('prepare-btn').disabled = true;
    document.getElementById('action-bar').style.display = 'none';
    document.getElementById('clear-btn').style.display = 'none';
    document.getElementById('followup-input').style.display = 'none';
    document.getElementById('followup-btn').style.display = 'none';
    var conv = document.getElementById('conversation');
    var blocks = conv.querySelectorAll('.response-block, .followup-q-wrap');
    blocks.forEach(function(b) { b.remove(); });
    var payload = JSON.stringify({meeting_desc: desc, fmt: fmt, model: model, extra_sources: extra});
    pywebview.api.run_prepare(payload);
  }

  function setStatus(state, count) {
    var el = document.getElementById('status');
    if (state === 'searching') el.innerHTML = '🔍 Searching your notes...';
    else if (state === 'thinking') el.innerHTML = '🤔 Found ' + count + ' relevant notes. Generating talking points...';
    else if (state === 'nothinking') el.innerHTML = '🤔 No matching notes found. Generating general talking points...';
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
    _streamBlock = null;
    _streamEl = null;
    _streamText = '';
    document.getElementById('followup-input').style.display = 'block';
    document.getElementById('followup-btn').style.display = 'block';
    document.getElementById('status').innerHTML = '✅ Ready';
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
    document.getElementById('status').innerHTML = '❌ Error: ' + msg;
    document.getElementById('prepare-btn').disabled = false;
    document.getElementById('followup-btn').disabled = false;
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

  function onFileLoaded(filename, text) {
    _fileTexts[filename] = text;
    var chips = document.getElementById('file-chips');
    var chipId = "chip_" + filename.replace(/[^a-z0-9]/gi, "_");
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
