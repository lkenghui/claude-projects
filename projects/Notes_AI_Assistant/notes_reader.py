import subprocess
import re
import tempfile
import os
from datetime import date, timedelta


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
        raise RuntimeError(f"Notes.app search timed out after {timeout}s — try a more specific description or narrower date range.")
    finally:
        os.unlink(tmp_path)


def strip_html(html: str) -> str:
    text = re.sub(r'<(?:p|div|h[1-6]|br|li|ul|ol)(?:\s[^>]*)?>',  '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#39;', "'", text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_emphasized(html: str) -> str:
    """Extract text from bold and underlined tags as a comma-separated string."""
    terms = []
    for tag in ('b', 'strong', 'u'):
        matches = re.findall(rf'<{tag}[^>]*>(.*?)</{tag}>', html, re.IGNORECASE | re.DOTALL)
        for m in matches:
            text = re.sub(r'<[^>]+>', '', m).strip()
            if text and len(text) < 120:
                terms.append(text)
    seen = set()
    unique = []
    for t in terms:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique.append(t)
    return ', '.join(unique[:20])


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
    quoted = re.findall(r'"([^"]+)"', meeting_description)
    clean = re.sub(r'"[^"]+"', '', meeting_description)
    words = [w for w in re.findall(r'\b\w+\b', clean)
             if w.lower() not in stopwords and len(w) > 2]
    return quoted + words


MAX_NOTE_WORDS = 600


def truncate_content(content: str) -> str:
    words = content.split()
    if len(words) <= MAX_NOTE_WORDS:
        return content
    return ' '.join(words[:MAX_NOTE_WORDS]) + '\n[…truncated]'


def count_notes(folder: str = '', years: int = 0) -> int:
    """Fast note count — simple metadata query, no body loading."""
    if folder:
        script = f'tell application "Notes" to return count of notes of folder "{folder}"'
    else:
        script = 'tell application "Notes" to return count of notes'
    import logging as _log
    try:
        raw = run_applescript(script, timeout=10)
        return int(raw.strip())
    except Exception as e:
        _log.warning(f"count_notes failed: {e}")
        return 0


def get_folders() -> list:
    """Return all Notes.app folder names, excluding Recently Deleted."""
    import logging as _log
    script = 'tell application "Notes" to get name of every folder'
    try:
        raw = run_applescript(script, timeout=10)
        return [f.strip() for f in raw.split(',') if f.strip() and f.strip() != 'Recently Deleted']
    except Exception as e:
        _log.warning(f"get_folders failed: {e}")
        return []


def _get_all_names_and_dates(folder: str) -> list:
    """
    Get all note (name, YYYYMMDD_int) pairs in ~2s for large libraries.
    Uses bulk property fetch (instant) then in-memory list iteration (fast).
    Returns list of (name_str, date_int, one_based_index).
    """
    notes_target = f'every note of folder "{folder}"' if folder else 'every note'
    script = f'''tell application "Notes"
\tset nl to name of {notes_target}
\tset dl to modification date of {notes_target}
\tset output to ""
\tset c to count of nl
\trepeat with i from 1 to c
\t\tset noteDate to item i of dl
\t\tset dateInt to (year of noteDate) * 10000 + (month of noteDate as integer) * 100 + (day of noteDate)
\t\tset output to output & (item i of nl) & "\t" & (dateInt as string) & "\n"
\tend repeat
\treturn output
end tell'''
    raw = run_applescript(script, timeout=30)
    result = []
    for idx, line in enumerate(raw.split('\n'), start=1):
        line = line.strip()
        if '\t' not in line:
            continue
        parts = line.split('\t', 1)
        if len(parts) == 2:
            name, date_str = parts
            try:
                result.append((name.strip(), int(date_str.strip()), idx))
            except ValueError:
                pass
    return result


def _fetch_bodies_by_index(indices: list, folder: str) -> list:
    """
    Fetch note bodies for notes at given 1-based positions.
    Returns list of (name, date_int, body_html).
    ~1s per note — use for small sets only.
    """
    if not indices:
        return []
    notes_target = f'every note of folder "{folder}"' if folder else 'every note'
    FIELD = '|||F|||'
    SEP = '|||S|||'
    idx_as = '{' + ', '.join(str(i) for i in indices) + '}'
    script = f'''tell application "Notes"
\tset allNotes to {notes_target}
\tset total to count of allNotes
\tset output to ""
\trepeat with idx in {idx_as}
\t\tif idx <= total then
\t\t\tset aNote to item idx of allNotes
\t\t\tset noteName to name of aNote
\t\t\tset noteBody to body of aNote
\t\t\tset noteDate to modification date of aNote
\t\t\tset dateInt to (year of noteDate) * 10000 + (month of noteDate as integer) * 100 + (day of noteDate)
\t\t\tset output to output & noteName & "{FIELD}" & (dateInt as string) & "{FIELD}" & noteBody & "{SEP}"
\t\tend if
\tend repeat
\treturn output
end tell'''
    timeout = max(30, len(indices) * 3 + 10)
    import logging as _log
    try:
        raw = run_applescript(script, timeout=timeout)
    except Exception as e:
        _log.warning(f"_fetch_bodies_by_index failed for {len(indices)} notes: {e}")
        return []
    results = []
    for chunk in raw.split(SEP):
        chunk = chunk.strip()
        parts = chunk.split(FIELD, 2)
        if len(parts) == 3:
            name, date_str, body = parts
            try:
                results.append((name.strip(), int(date_str.strip()), body))
            except ValueError:
                pass
    return results


def _int_date_to_str(d: int) -> str:
    y, m, day = d // 10000, (d % 10000) // 100, d % 100
    return f'{y}-{m}-{day}'


def _recover_full_title(title: str, body_html: str) -> str:
    if not title.endswith('…'):
        return title
    prefix = title[:-1]
    for tag in ('p', 'h1', 'h2', 'h3', 'div'):
        m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', body_html, re.IGNORECASE | re.DOTALL)
        if m:
            candidate = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if candidate.startswith(prefix) and len(candidate) > len(prefix):
                return candidate
    return prefix


# Module-level stats populated by get_relevant_notes for callers to read
_last_search_stats = {'title_matched': 0, 'body_matched': 0}


def get_relevant_notes(meeting_description: str, keywords: list = None,
                       folder: str = '', years: int = 2,
                       on_filtered_count=None) -> list:
    """
    Two-phase search:
    Phase 1 (~2s): Bulk-fetch all names+dates, filter in Python, fetch bodies
                   only for title-matched notes.
    Phase 2 (~30s): Fetch bodies for 30 most-recent unmatched notes, search
                    for keywords in full content (catches body-only phrases).
    """
    import logging as _log

    if not keywords:
        keywords = extract_keywords(meeting_description)
    if not keywords:
        return []
    keywords = keywords[:10]

    # Cutoff as YYYYMMDD int
    if years and years > 0:
        cutoff_date = date.today() - timedelta(days=years * 365)
        cutoff_int = cutoff_date.year * 10000 + cutoff_date.month * 100 + cutoff_date.day
    else:
        cutoff_int = 0

    kw_lower = [k.lower() for k in keywords]

    def title_matches(title: str) -> bool:
        tl = title.lower()
        return any(k in tl for k in kw_lower)

    def body_matches(plain: str) -> bool:
        pl = plain.lower()
        return any(k in pl for k in kw_lower)

    # ── Phase 1: bulk name+date (~2s) ────────────────────────────────────────
    _log.info("Phase 1: bulk name+date fetch")
    all_entries = _get_all_names_and_dates(folder)
    _log.info(f"Got {len(all_entries)} entries")

    # Filter by date and skip Meeting Prep notes
    dated = [(idx, name, d) for idx, name, d in
             [(e[2], e[0], e[1]) for e in all_entries]
             if not name.startswith('Meeting Prep:')
             and (cutoff_int == 0 or d >= cutoff_int)]

    # Sort most-recent first
    dated.sort(key=lambda x: x[2], reverse=True)
    _log.info(f"After date filter: {len(dated)} notes within {years} years")
    if on_filtered_count:
        on_filtered_count(len(dated))

    # Split into title-matched and unmatched
    title_matched = [(idx, name, d) for idx, name, d in dated if title_matches(name)]
    unmatched = [(idx, name, d) for idx, name, d in dated if not title_matches(name)]
    _log.info(f"Title-matched: {len(title_matched)}, unmatched: {len(unmatched)}")

    seen_titles: set = set()
    results = []
    _phase1_count = [0]  # count of notes added in phase 1

    def process_fetched(fetched):
        for name, date_int, body_html in fetched:
            full_title = _recover_full_title(name, body_html)
            if full_title in seen_titles:
                continue
            seen_titles.add(full_title)
            full_content = strip_html(body_html).strip()
            content = truncate_content(full_content)
            emphasized = extract_emphasized(body_html)
            results.append({
                'title': full_title, 'content': content,
                'full_content': full_content, 'emphasized': emphasized,
                'date': _int_date_to_str(date_int)
            })

    # Fetch bodies for title-matched notes (small set, fast)
    if title_matched:
        title_indices = [idx for idx, _, _ in title_matched[:20]]
        _log.info(f"Fetching bodies for {len(title_indices)} title-matched notes")
        process_fetched(_fetch_bodies_by_index(title_indices, folder))
    _phase1_count[0] = len(results)

    # ── Phase 2: body search on 30 most-recent unmatched notes (~30s) ────────
    recent_unmatched_indices = [idx for idx, _, _ in unmatched[:40]]
    _log.info(f"Phase 2: body search on {len(recent_unmatched_indices)} recent notes")

    if recent_unmatched_indices:
        fetched2 = _fetch_bodies_by_index(recent_unmatched_indices, folder)
        for name, date_int, body_html in fetched2:
            full_title = _recover_full_title(name, body_html)
            if full_title in seen_titles:
                continue
            full_content = strip_html(body_html).strip()
            if not body_matches(full_content):
                continue
            seen_titles.add(full_title)
            content = truncate_content(full_content)
            emphasized = extract_emphasized(body_html)
            results.append({
                'title': full_title, 'content': content,
                'full_content': full_content, 'emphasized': emphasized,
                'date': _int_date_to_str(date_int)
            })

    results.sort(key=lambda n: parse_date(n['date']), reverse=True)
    final = results[:20]

    # Populate module-level stats for callers to inspect
    tm_count = min(_phase1_count[0], len(final))
    bm_count = max(0, len(final) - tm_count)
    _last_search_stats['title_matched'] = tm_count
    _last_search_stats['body_matched'] = bm_count

    _log.info(f"Got {len(final)} notes (title_matched={tm_count}, body_matched={bm_count})")
    return final
