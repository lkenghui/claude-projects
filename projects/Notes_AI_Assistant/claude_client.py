import os
from datetime import date
import anthropic
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

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
        "Structure your response with exactly these four sections using clear headings:\n"
        "1. Context — 2-3 sentences summarising the relevant background\n"
        "2. Key Talking Points — concise bullets on what to raise or drive\n"
        "3. Commitments & Open Items — any past commitments or unresolved items from prior notes\n"
        "4. Strategic Questions — 2-3 sharp questions to ask in the meeting"
    ),
    'brief': (
        "Format as an executive brief:\n"
        "- One short paragraph of context\n"
        "- 3-5 key points to raise or drive\n"
        "- Any commitments or open items from prior notes\n"
        "- 2-3 strategic questions to ask"
    ),
    'narrative': "Format your response as a concise narrative in flowing paragraphs, suitable for reading aloud.",
}


def prepare_talking_points(notes: list, meeting_description: str,
                           fmt: str = 'bullets', extra_sources: str = '',
                           model: str = 'claude-haiku-4-5-20251001') -> tuple:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    notes_text = ""
    for note in notes:
        date_label = f" [{note['date']}]" if note.get('date') else ""
        notes_text += f"\n--- {note['title']}{date_label} ---\n{note['content']}\n"

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

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=messages
    )

    reply = response.content[0].text
    messages.append({"role": "assistant", "content": reply})
    return reply, messages


def ask_followup(question: str, history: list,
                 model: str = 'claude-haiku-4-5-20251001') -> tuple:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    messages = history.copy()
    messages.append({"role": "user", "content": question})

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=messages
    )

    reply = response.content[0].text
    messages.append({"role": "assistant", "content": reply})
    return reply, messages
