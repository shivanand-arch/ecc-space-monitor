"""
Claude LLM client for analysis and Q&A.

All Anthropic API calls go through this module so the model, temperature,
and prompt templates are in one place.
"""

import datetime
import anthropic

from config import CLAUDE_MODEL, MAX_CHAT_HISTORY_MESSAGES
from message_utils import build_analysis_context


def analyze_messages(messages: list[dict], space_display_name: str, api_key: str) -> str:
    """Return a structured analysis of *messages* from a single space."""
    if not messages:
        return "No messages to analyze."

    conversation = build_analysis_context(messages)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        temperature=0.2,
        messages=[
            {
                "role": "user",
                "content": f"""Analyze the following messages from the Google Chat space "{space_display_name}".

Provide a comprehensive analysis with these sections:

## Key Topics & Themes
Identify the main topics being discussed.

## Critical Issues / Escalations
Any urgent problems, outages, customer escalations, or blockers mentioned.

## Action Items
List specific action items with who is responsible (if mentioned).

## Unresolved Items
Things that were raised but not yet resolved or answered.

## Decisions Made
Any decisions or agreements reached.

## Sentiment & Engagement
Overall tone - is the team stressed, collaborative, calm? Who are the most active participants?

## Summary
A 3-4 sentence executive summary of what's happening in this space.

---
MESSAGES:
{conversation}""",
            }
        ],
    )
    return response.content[0].text


def extract_date_range_llm(user_question: str, api_key: str):
    """Use Claude as a fallback date-range parser when the deterministic
    parser in ``date_parser.py`` cannot handle the query.

    Returns ``(start_str, end_str)`` or ``None``.
    """
    today = datetime.date.today()
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=200,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": f"""Today is {today.isoformat()} ({today.strftime("%A")}).

Extract the date range from this question. If no specific time period is mentioned, reply ONLY with "DEFAULT".
If a date range is mentioned (e.g. "last week", "past month", "in January", "last 3 days", "yesterday", "since March 1"), reply ONLY with two dates in this exact format:
START=YYYY-MM-DD
END=YYYY-MM-DD

Cap the range at 60 days maximum. If the user says "all time" or something very broad, use the last 60 days.

Question: {user_question}""",
            }
        ],
    )
    text = response.content[0].text.strip()
    if text == "DEFAULT":
        return None
    try:
        lines = text.strip().split("\n")
        start_str = lines[0].split("=")[1].strip()
        end_str = lines[1].split("=")[1].strip()
        datetime.date.fromisoformat(start_str)
        datetime.date.fromisoformat(end_str)
        return start_str, end_str
    except (IndexError, ValueError):
        return None


def chat_with_claude(
    user_question: str,
    conversation_context: str,
    date_label: str,
    chat_history: list[dict],
    api_key: str,
) -> str:
    """Answer a user question using space message context and chat history."""
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = f"""You are the ECC Space Monitor AI assistant. You have access to messages from multiple Google Chat spaces at Exotel.
Answer questions accurately based on the messages below. If the information isn't in the messages, say so.
Be specific - mention names, dates, and quote relevant messages when helpful.
If asked about trends, patterns, or comparisons across spaces, analyze all available data.

TODAY'S DATE: {datetime.datetime.now().strftime("%B %d, %Y")}
MESSAGE WINDOW: {date_label}

AVAILABLE SPACE MESSAGES:
{conversation_context}"""

    # Trim chat history to keep within budget
    trimmed_history = chat_history[-MAX_CHAT_HISTORY_MESSAGES:]

    messages = [{"role": m["role"], "content": m["content"]} for m in trimmed_history]
    messages.append({"role": "user", "content": user_question})

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        temperature=0.3,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text
