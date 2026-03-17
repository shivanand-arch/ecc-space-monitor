"""
Message text extraction and context building.

Every function that touches raw message content goes through here so
that extraction logic is consistent across chat, dashboard, and analysis.
"""

import html
import json
import re
import datetime
import hashlib

from config import MAX_CONTEXT_CHARS, ANALYSIS_PROMPT_VERSION


# ── Text extraction ──────────────────────────────────────────────────────────

def get_sender_name(msg: dict) -> str:
    sender = msg.get("sender", {})
    return sender.get("displayName", sender.get("name", "Unknown"))


def format_time(time_str: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %I:%M %p")
    except Exception:
        return time_str


def extract_text(msg: dict) -> str:
    """Extract all meaningful text from a Chat API message.

    Covers plain text, formatted text, attachment names, and card content.
    Uses recursive dict walking (not regex) for cards.
    """
    parts: list[str] = []

    # Primary text
    text = msg.get("text", "") or ""
    if not text:
        text = msg.get("formattedText", "") or ""
    if text.strip():
        parts.append(text.strip())

    # Attachment names
    for att in msg.get("attachment", []):
        name = att.get("contentName", "")
        if name:
            parts.append(f"[Attachment: {name}]")

    # Cards — walk recursively
    cards = msg.get("cardsV2", msg.get("cards", []))
    for card in cards:
        if isinstance(card, dict):
            texts = _extract_texts_from_dict(card)
            if texts:
                parts.append(" | ".join(texts))

    return " ".join(parts)


def _extract_texts_from_dict(d: dict, keys: set[str] | None = None) -> list[str]:
    """Recursively collect string values for text-like keys from a dict."""
    if keys is None:
        keys = {"text", "title", "subtitle", "content", "header"}
    results: list[str] = []
    for k, v in d.items():
        if k in keys and isinstance(v, str) and v.strip():
            results.append(v.strip())
        elif isinstance(v, dict):
            results.extend(_extract_texts_from_dict(v, keys))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    results.extend(_extract_texts_from_dict(item, keys))
    return results


# ── HTML-safe rendering ──────────────────────────────────────────────────────

def safe(text: str) -> str:
    """Escape text for safe embedding in HTML."""
    return html.escape(text, quote=True)


# ── Context building ─────────────────────────────────────────────────────────

def build_conversation_context(all_messages_by_space: dict[str, list[dict]]) -> str:
    """Concatenate messages into a single string for Claude, respecting token budget."""
    total_chars = 0
    parts: list[str] = []

    for space_name, messages in all_messages_by_space.items():
        msg_lines: list[str] = []
        for m in messages:
            sender = get_sender_name(m)
            text = extract_text(m)
            time = format_time(m.get("createTime", ""))
            if text.strip():
                line = f"[{time}] {sender}: {text}"
                if total_chars + len(line) > MAX_CONTEXT_CHARS:
                    msg_lines.append("[... truncated to fit context window ...]")
                    break
                msg_lines.append(line)
                total_chars += len(line) + 1
        if msg_lines:
            header = f"\n=== SPACE: {space_name} ===\n"
            total_chars += len(header)
            parts.append(header + "\n".join(msg_lines))
        if total_chars >= MAX_CONTEXT_CHARS:
            break

    return "\n".join(parts)


def build_analysis_context(messages: list[dict]) -> str:
    """Build a truncated message dump for a single-space analysis."""
    total_chars = 0
    lines: list[str] = []
    for m in messages:
        sender = get_sender_name(m)
        text = extract_text(m)
        time = format_time(m.get("createTime", ""))
        if text.strip():
            line = f"[{time}] {sender}: {text}"
            if total_chars + len(line) > MAX_CONTEXT_CHARS:
                lines.append("[... truncated to fit context window ...]")
                break
            lines.append(line)
            total_chars += len(line) + 1
    return "\n".join(lines)


# ── Cache key for analysis results ───────────────────────────────────────────

def analysis_cache_key(space_id: str, messages: list[dict]) -> str:
    """Deterministic hash so we can skip re-analysis when nothing changed."""
    payload = json.dumps(
        {
            "space_id": space_id,
            "prompt_version": ANALYSIS_PROMPT_VERSION,
            "message_ids": [m.get("name") for m in messages[-50:]],  # last 50 for speed
            "count": len(messages),
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]
