import os
import json
from datetime import date
import anthropic
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

DEFAULT_MODEL = 'claude-haiku-4-5-20251001'

SYSTEM_PROMPT = """You are a personal meeting preparation assistant for Prof Lim Keng Hui, \
Assistant Chief Executive of the Science & Engineering Research Council (SERC) at A*STAR, \
Singapore's national research and development agency.

Prof Lim oversees SERC's R&D portfolio of over 2,500 scientists, engineers, and staff, \
and is responsible for driving A*STAR's strategies across science and engineering — with a \
focus on industry and societal impact, and long-term R&D capabilities for Singapore. His \
portfolio spans AI and advanced computing, advanced manufacturing and materials, \
sustainability, and land-air-sea transport. He holds adjunct professor positions at NUS and NTU, \
and serves on multiple national R&D boards and committees.

When preparing for meetings:
1. Prioritise insights from the most recent notes — use today's date (provided in each request) \
to assess recency and weight recent notes more heavily than older ones
2. Extract key talking points, past decisions, and outstanding commitments most relevant to the meeting
3. Flag unresolved issues or important context from previous discussions
4. If no prior notes are available, explicitly state that you are working without prior context \
and generate general talking points based on the meeting description alone

Keep output concise and strategic — suitable for a senior leader who needs to walk in \
prepared, not briefed on every detail. Avoid operational minutiae unless directly relevant."""

FORMAT_INSTRUCTIONS = {
    'bullets': (
        "Structure your response with exactly these five sections using clear headings:\n"
        "1. Context — 2-3 sentences summarising the relevant background\n"
        "2. Key Talking Points — concise bullets on what to raise or drive\n"
        "3. Commitments & Open Items — any past commitments or unresolved items from prior notes\n"
        "4. Strategic Questions — 2-3 sharp questions to ask in the meeting\n"
        "5. References — list the exact titles of the past notes you drew on, as a bulleted list"
    ),
    'brief': (
        "Structure your response with exactly these five sections, each with a clear heading:\n"
        "1. Context — 2-3 sentences summarising the relevant background (prose).\n"
        "2. Key Talking Points — a short paragraph on what to raise or drive in this meeting (prose).\n"
        "3. Commitments & Open Items — a short paragraph on past commitments or unresolved issues (prose).\n"
        "4. Strategic Questions — 2-3 sharp questions written as a short paragraph (prose).\n"
        "5. References — a bulleted list of the exact titles of the past notes you drew on.\n"
        "Keep sections 1-4 tight — 3-4 sentences maximum."
    ),
}


def extract_search_keywords(description: str, model: str = DEFAULT_MODEL) -> list:
    """Use Claude to extract meaningful search keywords (proper nouns, acronyms, entities).
    Quoted phrases in the description are preserved as exact-match keywords."""
    import re as _re
    # Pull out quoted phrases first and remove them from description sent to Claude
    quoted = _re.findall(r'"([^"]+)"', description)
    clean_desc = _re.sub(r'"[^"]+"', '', description).strip()

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    try:
        response = client.messages.create(
            model=model,
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": (
                    "Extract 6-10 search keywords from this meeting description to search a personal notes archive. "
                    "Include: proper nouns, acronyms, organisation names, people names, project names, specific topics. "
                    "Exclude generic words like: meeting, discuss, with, update, review, session, call, agenda.\n"
                    "Return ONLY a comma-separated list of keywords, nothing else.\n\n"
                    f"Meeting: {clean_desc}"
                )
            }]
        )
        raw = response.content[0].text.strip()
        claude_keywords = [k.strip() for k in raw.split(',') if k.strip()]
    except Exception:
        claude_keywords = []

    # Quoted phrases go first so they are prioritised in the search (capped at 8 keywords total)
    return (quoted + claude_keywords)[:10]


def summarise_notes(notes: list, model: str = DEFAULT_MODEL) -> list:
    """Return [{summary, people}] for each note using a single Claude call."""
    if not notes:
        return []
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    notes_block = ""
    for i, note in enumerate(notes):
        non_empty = [l.strip() for l in note['content'].split('\n') if l.strip()]
        first_four_lines = '\n'.join(non_empty[:4])
        snippet = note['content'][:2000]
        emphasized = note.get('emphasized', '')
        notes_block += f"[{i}] Title: {note['title']}\nFirst lines: {first_four_lines}\n"
        if emphasized:
            notes_block += f"Bold/underlined terms: {emphasized}\n"
        notes_block += f"Content: {snippet}\n\n"
    prompt = (
        f"For each of the {len(notes)} notes below, return a JSON array with exactly {len(notes)} objects.\n"
        "Each object must have:\n"
        '  "summary": one complete sentence (max 30 words) describing what the note is about — prioritise bold/underlined terms as key topics — do not truncate or use ellipsis\n'
        '  "people": array of names of meeting ATTENDEES only — people who were PRESENT at the meeting, NOT people who were discussed or mentioned.\n'
        '  Rules to identify attendees:\n'
        '  - From the title: patterns like "Mtg with X and Y", "X and Y on Z", "Mtg X, Y, Z" → X and Y are attendees.\n'
        '  - From the title: patterns like "X on Y Issue/Topic/Problem/Update", "Update on X", "X on Y\'s Z" → only X attended; Y is the subject being discussed, NOT an attendee.\n'
        '  - From the first lines: bare lists of names (e.g. "Attendees: X, Y" or just "X, Y, Z" on its own line) indicate attendees. Names appearing in sentences about what was discussed are NOT attendees.\n'
        '  - A list may include role/org descriptors mixed in, e.g. "Kate Smaje, CTO McKinsey, Vivek, Tim" — extract the person names (Kate Smaje, Vivek, Tim) and ignore the role/org descriptor (CTO McKinsey).\n'
        '  - When uncertain, prefer fewer names over more names.\n'
        '  Empty array if no attendees can be confidently identified.\n\n'
        "Return ONLY valid JSON array, no explanation.\n\n"
        f"Notes:\n{notes_block}"
    )
    import logging as _log, re as _re
    try:
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        _log.info(f"summarise_notes raw response (first 300): {raw[:300]}")
        match = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if match:
            data = json.loads(match.group())
            _log.info(f"summarise_notes parsed {len(data)} items for {len(notes)} notes")
            if len(data) == len(notes):
                return data
            _log.warning(f"summarise_notes count mismatch: got {len(data)}, expected {len(notes)}")
        else:
            _log.warning("summarise_notes: no JSON array found in response")
    except Exception as e:
        _log.exception(f"summarise_notes error: {e}")
    return [{"summary": "", "people": []} for _ in notes]


def _rank_batch(client, notes: list, meeting_description: str, model: str) -> list:
    """Rank a single batch of notes (up to 10). Returns list of scores."""
    import logging as _log, re as _re
    notes_block = ""
    for i, note in enumerate(notes):
        snippet = note['content'][:2000]
        emphasized = note.get('emphasized', '')
        notes_block += f"[{i}] Title: {note['title']}\n"
        if emphasized:
            notes_block += f"Bold/underlined terms: {emphasized}\n"
        notes_block += f"{snippet}\n\n"
    prompt = (
        f"Rate how relevant each note is to this upcoming meeting: \"{meeting_description}\"\n\n"
        f"For each of the {len(notes)} notes, give a relevance score from 1-10:\n"
        "  10 = the note is directly and primarily about this meeting's specific topic and people\n"
        "  7-9 = the note is closely related background — same topic, same people, prior discussions\n"
        "  4-6 = the note mentions the topic or people but its PRIMARY subject is something else\n"
        "  1-3 = the note only incidentally mentions related organisations or keywords, but is really about a different topic\n\n"
        "Important rules:\n"
        "- Weight the note TITLE heavily — if the title directly names the meeting topic or key people, score 7+.\n"
        "- Bold/underlined terms (listed as 'Bold/underlined terms') are key points the author emphasised — if they match the meeting topic, treat them as strong relevance signals.\n"
        "- A note about appointments, leadership candidates, or hiring decisions for the SAME specific organisation and people mentioned in the meeting description is 7+.\n"
        "- A note about a SIMILAR TYPE of activity (e.g. another leadership search, another appointment) but for a DIFFERENT organisation or role should score 1-4 — do not conflate similar activities across different organisations.\n"
        "- A note that merely mentions the same organisation but is primarily about a DIFFERENT topic (e.g. sectorisation, infrastructure, workflows, budget) should score 1-4.\n\n"
        f"Return ONLY a JSON array of {len(notes)} integers (scores), in the same order as the notes. No explanation.\n\n"
        f"Notes:\n{notes_block}"
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        _log.info(f"rank_notes batch raw: {raw[:200]}")
        match = _re.search(r'\[.*?\]', raw, _re.DOTALL)
        if match:
            scores = json.loads(match.group())
            if len(scores) == len(notes):
                return [int(s) for s in scores]
            _log.warning(f"rank_notes batch count mismatch: {len(scores)} vs {len(notes)}")
    except Exception as e:
        _log.exception(f"rank_notes batch error: {e}")
    return [5] * len(notes)


def rank_notes(notes: list, meeting_description: str, model: str = DEFAULT_MODEL) -> list:
    """Score each note 1-10 for relevance. Ranks in batches of 10 to avoid lost-in-the-middle errors."""
    if not notes:
        return []
    import logging as _log
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    BATCH_SIZE = 10
    all_scores = []
    for start in range(0, len(notes), BATCH_SIZE):
        batch = notes[start:start + BATCH_SIZE]
        scores = _rank_batch(client, batch, meeting_description, model)
        all_scores.extend(scores)
        _log.info(f"rank_notes batch {start//BATCH_SIZE + 1}: scores {scores}")
    return all_scores


def prepare_talking_points(notes: list, meeting_description: str,
                           fmt: str = 'bullets', extra_sources: str = '',
                           model: str = DEFAULT_MODEL,
                           chunk_callback=None) -> tuple:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    notes_text = ""
    for note in notes:
        date_label = f" [{note['date']}]" if note.get('date') else ""
        notes_text += f"\n--- NOTE TITLE: {note['title']}{date_label} ---\n{note['content']}\n"

    context = ""
    if notes_text:
        context += f"\nNOTES FROM MY ARCHIVE (sorted most recent first):\n{notes_text}"
    else:
        context += "\nNO PRIOR NOTES FOUND for this meeting topic.\n"
    if extra_sources.strip():
        context += f"\nADDITIONAL SOURCES PROVIDED:\n{extra_sources.strip()}\n"

    format_instruction = FORMAT_INSTRUCTIONS.get(fmt, FORMAT_INSTRUCTIONS['bullets'])
    today = date.today().strftime("%-d %B %Y")

    user_message = f"""Today's date: {today}
Upcoming meeting: {meeting_description}
{context}
{format_instruction}

Please prepare talking points for this meeting."""

    messages = [{"role": "user", "content": user_message}]

    if chunk_callback:
        with client.messages.stream(model=model, max_tokens=2000,
                                    system=SYSTEM_PROMPT, messages=messages) as stream:
            reply = ''
            for text in stream.text_stream:
                reply += text
                chunk_callback(text)
    else:
        response = client.messages.create(model=model, max_tokens=2000,
                                          system=SYSTEM_PROMPT, messages=messages)
        reply = response.content[0].text

    messages.append({"role": "assistant", "content": reply})
    return reply, messages


def ask_followup(question: str, history: list,
                 model: str = DEFAULT_MODEL,
                 chunk_callback=None) -> tuple:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    messages = history.copy()
    messages.append({"role": "user", "content": question})

    if chunk_callback:
        with client.messages.stream(model=model, max_tokens=2000,
                                    system=SYSTEM_PROMPT, messages=messages) as stream:
            reply = ''
            for text in stream.text_stream:
                reply += text
                chunk_callback(text)
    else:
        response = client.messages.create(model=model, max_tokens=2000,
                                          system=SYSTEM_PROMPT, messages=messages)
        reply = response.content[0].text

    messages.append({"role": "assistant", "content": reply})
    return reply, messages
