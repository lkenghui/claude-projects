import os
import anthropic
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

SYSTEM_PROMPT = """You are a personal meeting preparation assistant for a senior leader at A*STAR \
(Agency for Science, Technology and Research), Singapore's national research and development agency. \
A*STAR funds research, develops talent, and drives innovation across Singapore's key industries.

Your role is to help prepare for meetings by:
1. Analysing the provided notes and identifying what is most relevant
2. Extracting key talking points, past decisions, and outstanding commitments
3. Flagging important context and unresolved issues from previous meetings
4. Suggesting strategic questions to raise

Tailor your output to a senior leadership context — be strategic, concise, and actionable. \
Avoid operational detail unless directly relevant to the meeting."""

FORMAT_INSTRUCTIONS = {
    'bullets': "Format your response with clear section headings and concise bullet points.",
    'brief': "Format as an executive brief: one short paragraph of context, followed by 3-5 key points, followed by 2-3 recommended questions to raise.",
    'narrative': "Format your response as a concise narrative in flowing paragraphs, suitable for reading aloud.",
}


def prepare_talking_points(notes: list, meeting_description: str,
                           fmt: str = 'bullets', extra_sources: str = '') -> tuple:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    notes_text = ""
    for note in notes:
        date_label = f" [{note['date']}]" if note.get('date') else ""
        notes_text += f"\n--- {note['title']}{date_label} ---\n{note['content']}\n"

    context = ""
    if notes_text:
        context += f"\nNOTES FROM MY ARCHIVE (sorted most recent first):\n{notes_text}"
    if extra_sources.strip():
        context += f"\nADDITIONAL SOURCES PROVIDED:\n{extra_sources.strip()}\n"

    format_instruction = FORMAT_INSTRUCTIONS.get(fmt, FORMAT_INSTRUCTIONS['bullets'])

    user_message = f"""Upcoming meeting: {meeting_description}
{context}
{format_instruction}

Please prepare talking points for this meeting."""

    messages = [{"role": "user", "content": user_message}]

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=messages
    )

    reply = response.content[0].text
    messages.append({"role": "assistant", "content": reply})
    return reply, messages


def ask_followup(question: str, history: list) -> tuple:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    messages = history.copy()
    messages.append({"role": "user", "content": question})

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=messages
    )

    reply = response.content[0].text
    messages.append({"role": "assistant", "content": reply})
    return reply, messages
