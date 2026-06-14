import subprocess
import re
import tempfile
import os


def run_applescript(script: str, timeout: int = 60) -> str:
    with tempfile.NamedTemporaryFile(mode='w', suffix='.applescript', delete=False, encoding='utf-8') as f:
        f.write(script)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ['osascript', tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Notes.app took too long to respond (>{timeout}s). Your notes library may be too large — try a more specific search.")
    finally:
        os.unlink(tmp_path)


def strip_html(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_date(date_str: str) -> tuple:
    try:
        parts = date_str.split('-')
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return (0, 0, 0)


def extract_keywords(meeting_description: str) -> list:
    stopwords = {'with', 'for', 'the', 'and', 'or', 'a', 'an', 'my', 'about',
                 'on', 'in', 'at', 'to', 'of', 'meeting', 'sync', 'call', 'session',
                 'this', 'mtg', 'is', 'discuss', 'attendees', 'summarise', 'key',
                 'points', 'from', 'previous', 'meetings', 'captured', 'notes',
                 'then', 'propose', 'talking', 'format', 'them', 'manner', 'that',
                 'can', 'cut', 'paste', 'into', 'capture', 'also'}
    words = re.findall(r'\b\w+\b', meeting_description)
    return [w for w in words if w.lower() not in stopwords and len(w) > 2]


def get_relevant_notes(meeting_description: str) -> list:
    """Search notes in a single pass, sorted by modification date (most recent first)."""
    keywords = extract_keywords(meeting_description)
    if not keywords:
        return []

    SEPARATOR = "|||NOTE_SEP|||"
    FIELD_SEP = "|||FIELD_SEP|||"
    DATE_SEP = "|||DATE_SEP|||"

    kw_conditions = " or ".join([
        f'noteName contains "{k.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}"'
        f' or notePreview contains "{k.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}"'
        for k in keywords[:8]
    ])

    script = f'''tell application "Notes"
\tset output to ""
\tset noteCount to 0
\trepeat with aNote in every note
\t\tif noteCount >= 20 then exit repeat
\t\tset noteName to name of aNote
\t\tif noteName starts with "Meeting Prep:" then
\t\t\t-- skip previous outputs
\t\telse
\t\t\tset noteBody to body of aNote
\t\t\tif length of noteBody > 300 then
\t\t\t\tset notePreview to text 1 thru 300 of noteBody
\t\t\telse
\t\t\t\tset notePreview to noteBody
\t\t\tend if
\t\t\tif {kw_conditions} then
\t\t\t\tset noteDate to modification date of aNote
\t\t\t\tset dateStr to ((year of noteDate) as string) & "-" & ((month of noteDate as integer) as string) & "-" & ((day of noteDate) as string)
\t\t\t\tset output to output & noteName & "{FIELD_SEP}" & dateStr & "{DATE_SEP}" & noteBody & "{SEPARATOR}"
\t\t\t\tset noteCount to noteCount + 1
\t\t\tend if
\t\tend if
\tend repeat
\treturn output
end tell'''

    raw = run_applescript(script, timeout=120)
    if not raw:
        return []

    notes = []
    seen = set()
    for chunk in raw.split(SEPARATOR):
        chunk = chunk.strip()
        if FIELD_SEP not in chunk:
            continue
        title, rest = chunk.split(FIELD_SEP, 1)
        title = title.strip()
        if DATE_SEP in rest:
            date_str, body = rest.split(DATE_SEP, 1)
        else:
            date_str, body = '', rest
        content = strip_html(body).strip()
        if title and content and title not in seen:
            seen.add(title)
            notes.append({'title': title, 'content': content, 'date': date_str.strip()})

    notes.sort(key=lambda n: parse_date(n['date']), reverse=True)
    return notes
