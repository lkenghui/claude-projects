import os
import json
import subprocess
import tempfile
import threading
import logging
import webview
from datetime import datetime
from dotenv import load_dotenv
from notes_reader import get_relevant_notes
from claude_client import prepare_talking_points, ask_followup

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


def copy_to_clipboard(text: str):
    subprocess.run(['pbcopy'], input=text.encode('utf-8'))


def save_to_notes(title: str, content: str):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(content)
        content_path = f.name
    safe_title = title.replace('"', '\\"')
    script = f'''tell application "Notes"
\tset noteContent to read POSIX file "{content_path}"
\tmake new note with properties {{name:"{safe_title}", body:noteContent}}
end tell'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.applescript', delete=False, encoding='utf-8') as f:
        f.write(script)
        script_path = f.name
    subprocess.run(['osascript', script_path], capture_output=True)
    os.unlink(script_path)
    os.unlink(content_path)


class Api:
    def __init__(self):
        self.window = None
        self._history = []
        self._all_content = []
        self._meeting_title = ''
        self._model = 'claude-haiku-4-5-20251001'

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

                result, history = prepare_talking_points(notes, meeting_desc, fmt, extra_sources, self._model)
                self._history = history
                self._all_content = [result]
                self._meeting_title = f"Meeting Prep: {meeting_desc[:40]} ({datetime.now().strftime('%d %b %Y %H:%M')})"

                logging.info("Got talking points")
                self.window.evaluate_js(f"appendResponse({json.dumps(result)})")

            except Exception as e:
                logging.exception("Error in prepare")
                self.window.evaluate_js(f"setError({json.dumps(str(e))})")

        threading.Thread(target=run, daemon=True).start()

    def run_followup(self, question):
        def run():
            try:
                logging.info(f"Follow-up: {question!r}")
                self.window.evaluate_js("setFollowupThinking()")

                result, history = ask_followup(question, self._history, self._model)
                self._history = history
                self._all_content.append(f"Q: {question}\n\n{result}")

                self.window.evaluate_js(f"appendFollowup({json.dumps(question)}, {json.dumps(result)})")

            except Exception as e:
                logging.exception("Error in followup")
                self.window.evaluate_js(f"setError({json.dumps(str(e))})")

        threading.Thread(target=run, daemon=True).start()

    def run_copy(self):
        copy_to_clipboard(self._all_content[-1] if self._all_content else '')

    def run_save(self):
        save_to_notes(self._meeting_title, '\n\n---\n\n'.join(self._all_content))

    def on_shown(self):
        config = load_config()
        x, y = config.get('x'), config.get('y')
        w, h = config.get('width'), config.get('height')
        if all(v is not None for v in (x, y, w, h)):
            self.window.resize(int(w), int(h))
            self.window.move(int(x), int(y))
        self._win_x = self.window.x
        self._win_y = self.window.y
        self._win_w = self.window.width
        self._win_h = self.window.height

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
  #sources-toggle { background: none; border: none; color: #007AFF; font-size: 13px; cursor: pointer; padding: 0; }
  #sources-area { display: none; margin-top: 8px; }
  #sources-input { width: 100%; height: 72px; padding: 8px 10px; border: 1px solid #d0d0d0; border-radius: 8px; font-size: 13px; resize: vertical; font-family: inherit; outline: none; }
  #sources-input:focus { border-color: #007AFF; }
  #status { padding: 8px 20px; font-size: 13px; color: #666; flex-shrink: 0; min-height: 32px; display: flex; align-items: center; }
  #conversation { flex: 1; overflow-y: auto; padding: 0 20px 16px; }
  .response-block { background: white; border-radius: 10px; padding: 16px; margin-top: 12px; line-height: 1.7; font-size: 14px; }
  .response-label { font-size: 11px; color: #999; margin-bottom: 8px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
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
  #btn-copy { background: #007AFF; color: white; }
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
      <option value="narrative">Narrative</option>
    </select>
    <select id="model-select">
      <option value="claude-haiku-4-5-20251001">Haiku (Fast)</option>
      <option value="claude-sonnet-4-6">Sonnet (Sharp)</option>
    </select>
    <button id="prepare-btn" onclick="prepare()">Prepare</button>
  </div>
  <button id="sources-toggle" onclick="toggleSources()">＋ Add Sources</button>
  <div id="sources-area">
    <textarea id="sources-input" placeholder="Paste additional context here — emails, documents, notes from other apps..."></textarea>
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
  <button id="btn-copy" onclick="copyAll()">Copy</button>
</div>

<script>

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

  function prepare() {
    var desc = document.getElementById('meeting-input').value.trim();
    if (!desc) return;
    var fmt = document.getElementById('format-select').value;
    var model = document.getElementById('model-select').value;
    var extra = document.getElementById('sources-input').value;
    document.getElementById('prepare-btn').disabled = true;
    document.getElementById('action-bar').style.display = 'none';
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

  function renderMarkdown(content) {
    return marked.parse(content);
  }

  function appendResponse(content) {
    var conv = document.getElementById('conversation');
    var fuRow = document.getElementById('followup-row');
    var block = document.createElement('div');
    block.className = 'response-block';
    var label = document.createElement('div');
    label.className = 'response-label';
    label.textContent = 'Analysis';
    var text = document.createElement('div');
    text.className = 'markdown';
    text.innerHTML = renderMarkdown(content);
    block.appendChild(label);
    block.appendChild(text);
    conv.insertBefore(block, fuRow);
    document.getElementById('followup-input').style.display = 'block';
    document.getElementById('followup-btn').style.display = 'block';
    document.getElementById('status').innerHTML = '✅ Ready';
    document.getElementById('action-bar').style.display = 'flex';
    document.getElementById('prepare-btn').disabled = false;
    conv.scrollTop = conv.scrollHeight;
  }

  function appendFollowup(question, content) {
    var conv = document.getElementById('conversation');
    var fuRow = document.getElementById('followup-row');
    var qWrap = document.createElement('div');
    qWrap.className = 'followup-q-wrap';
    var qBubble = document.createElement('div');
    qBubble.className = 'followup-q';
    qBubble.textContent = question;
    qWrap.appendChild(qBubble);
    conv.insertBefore(qWrap, fuRow);
    var rBlock = document.createElement('div');
    rBlock.className = 'response-block';
    var label = document.createElement('div');
    label.className = 'response-label';
    label.textContent = 'Response';
    var text = document.createElement('div');
    text.className = 'markdown';
    text.innerHTML = renderMarkdown(content);
    rBlock.appendChild(label);
    rBlock.appendChild(text);
    conv.insertBefore(rBlock, fuRow);
    document.getElementById('followup-input').value = '';
    document.getElementById('followup-btn').disabled = false;
    document.getElementById('status').innerHTML = '✅ Ready';
    conv.scrollTop = conv.scrollHeight;
  }

  function setFollowupThinking() {
    document.getElementById('followup-btn').disabled = true;
    document.getElementById('status').innerHTML = '🤔 Thinking...';
  }

  function setError(msg) {
    document.getElementById('status').innerHTML = '❌ Error: ' + msg;
    document.getElementById('prepare-btn').disabled = false;
    document.getElementById('followup-btn').disabled = false;
  }

  function askFollowup() {
    var q = document.getElementById('followup-input').value.trim();
    if (!q) return;
    pywebview.api.run_followup(q);
  }

  function copyAll() {
    pywebview.api.run_copy();
    var btn = document.getElementById('btn-copy');
    btn.textContent = 'Copied ✓';
    setTimeout(function() { btn.textContent = 'Copy'; }, 2000);
  }

  function saveToNotes() {
    var btn = document.getElementById('btn-save');
    btn.textContent = 'Saving...';
    btn.disabled = true;
    pywebview.api.run_save().then(function() {
      btn.textContent = 'Saved ✓';
    });
  }
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
    )
    api.window = window
    window.events.shown += api.on_shown
    window.events.moved += api.on_moved
    window.events.resized += api.on_resized
    window.events.closed += api.on_closed
    webview.start()
