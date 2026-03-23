"""
Deterministic date-range extraction from natural-language queries.

Handles common patterns ("yesterday", "last 2 weeks", "since March 2025")
without an LLM call.  Falls back to Claude only when the deterministic
parser cannot match.
"""

import datetime
import re
from typing import Optional, Tuple

DateRange = Tuple[str, str]   # (YYYY-MM-DD, YYYY-MM-DD)

_MONTH_MAP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_MONTH_PATTERN = "|".join(_MONTH_MAP)


def _cap_end(end: datetime.date, today: datetime.date) -> datetime.date:
    """Don't go past today."""
    return min(end, today)


def parse_date_range(question: str, today: Optional[datetime.date] = None) -> Optional[DateRange]:
    """Try to deterministically extract a date range from *question*.

    Returns ``(start_str, end_str)`` in ISO format, or ``None`` if no
    time expression is detected (caller should fall back to LLM or default).

    NO clamping on range size — the query controls the range.
    """
    today = today or datetime.date.today()
    q = question.lower().strip()

    # ── Exact keywords ───────────────────────────────────────────────────
    if "today" in q:
        return today.isoformat(), today.isoformat()

    if "yesterday" in q:
        d = today - datetime.timedelta(days=1)
        return d.isoformat(), today.isoformat()

    # ── "last N days/weeks/months/years" or "past N …" ───────────────────
    m = re.search(r"(?:last|past)\s+(\d+)\s+(day|week|month|year)s?", q)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "day":
            delta = datetime.timedelta(days=n)
        elif unit == "week":
            delta = datetime.timedelta(weeks=n)
        elif unit == "month":
            delta = datetime.timedelta(days=n * 30)
        else:  # year
            delta = datetime.timedelta(days=n * 365)
        start = today - delta
        return start.isoformat(), today.isoformat()

    # ── "last week" / "past week" (no number) ────────────────────────────
    if re.search(r"(?:last|past)\s+week\b", q):
        start = today - datetime.timedelta(weeks=1)
        return start.isoformat(), today.isoformat()

    # ── "last month" / "past month" ──────────────────────────────────────
    if re.search(r"(?:last|past)\s+month\b", q):
        start = today - datetime.timedelta(days=30)
        return start.isoformat(), today.isoformat()

    # ── "last year" / "past year" ────────────────────────────────────────
    if re.search(r"(?:last|past)\s+year\b", q):
        start = today - datetime.timedelta(days=365)
        return start.isoformat(), today.isoformat()

    # ── "this week" ──────────────────────────────────────────────────────
    if "this week" in q:
        start = today - datetime.timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat()

    # ── "this month" ─────────────────────────────────────────────────────
    if "this month" in q:
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat()

    # ── "this year" ──────────────────────────────────────────────────────
    if "this year" in q:
        start = today.replace(month=1, day=1)
        return start.isoformat(), today.isoformat()

    # ── "since/from <YYYY-MM-DD>" ────────────────────────────────────────
    m = re.search(r"(?:since|from)\s+(\d{4}-\d{2}-\d{2})", q)
    if m:
        start = datetime.date.fromisoformat(m.group(1))
        return start.isoformat(), today.isoformat()

    # ── "since/from April 2025" or "from jan 2024" (month + year) ────────
    m = re.search(
        r"(?:since|from)\s+(" + _MONTH_PATTERN + r")\s+(\d{4})",
        q,
    )
    if m:
        month = _MONTH_MAP[m.group(1)]
        year = int(m.group(2))
        try:
            start = datetime.date(year, month, 1)
            return start.isoformat(), today.isoformat()
        except ValueError:
            pass

    # ── "since/from March 1" or "from Jan 15" (month + day, infer year) ──
    m = re.search(
        r"(?:since|from)\s+(" + _MONTH_PATTERN + r")\s+(\d{1,2})",
        q,
    )
    if m:
        month = _MONTH_MAP[m.group(1)]
        day = int(m.group(2))
        year = today.year if month <= today.month else today.year - 1
        try:
            start = datetime.date(year, month, day)
            return start.isoformat(), today.isoformat()
        except ValueError:
            pass

    # ── "since/from April" (month only, no day/year) ─────────────────────
    m = re.search(r"(?:since|from)\s+(" + _MONTH_PATTERN + r")\b", q)
    if m:
        month = _MONTH_MAP[m.group(1)]
        year = today.year if month <= today.month else today.year - 1
        start = datetime.date(year, month, 1)
        return start.isoformat(), today.isoformat()

    # ── "<month> <year>" anywhere — e.g. "in January 2025" or just "April 2025"
    m = re.search(r"(" + _MONTH_PATTERN + r")\s+(\d{4})", q)
    if m:
        month = _MONTH_MAP[m.group(1)]
        year = int(m.group(2))
        start = datetime.date(year, month, 1)
        if month == 12:
            end = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            end = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
        # If "onwards" or "since" or "from" is present, extend to today
        if re.search(r"\b(onwards|onward|since|from|after)\b", q):
            end = today
        return start.isoformat(), _cap_end(end, today).isoformat()

    # ── "in January" / "in March" (no year — infer) ──────────────────────
    m = re.search(r"\bin\s+(" + _MONTH_PATTERN + r")\b", q)
    if m:
        month = _MONTH_MAP[m.group(1)]
        year = today.year if month <= today.month else today.year - 1
        start = datetime.date(year, month, 1)
        if month == 12:
            end = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            end = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
        return start.isoformat(), _cap_end(end, today).isoformat()

    # ── Quarter references: "Q1 2025", "Q4", "last quarter" ─────────────
    m = re.search(r"q([1-4])\s*(\d{4})?", q)
    if m:
        quarter = int(m.group(1))
        year = int(m.group(2)) if m.group(2) else today.year
        q_start_month = (quarter - 1) * 3 + 1
        start = datetime.date(year, q_start_month, 1)
        if q_start_month + 3 > 12:
            end = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            end = datetime.date(year, q_start_month + 3, 1) - datetime.timedelta(days=1)
        return start.isoformat(), _cap_end(end, today).isoformat()

    if "last quarter" in q:
        current_q = (today.month - 1) // 3
        if current_q == 0:
            start = datetime.date(today.year - 1, 10, 1)
            end = datetime.date(today.year - 1, 12, 31)
        else:
            q_start_month = (current_q - 1) * 3 + 1
            start = datetime.date(today.year, q_start_month, 1)
            end = datetime.date(today.year, q_start_month + 3, 1) - datetime.timedelta(days=1)
        return start.isoformat(), _cap_end(end, today).isoformat()

    # ── "off late" / "recently" / "lately" → last 14 days ────────────────
    if re.search(r"\b(off\s+late|recently|lately)\b", q):
        start = today - datetime.timedelta(days=14)
        return start.isoformat(), today.isoformat()

    # ── "<month> <year> onwards" pattern (already handled above, but catch edge)
    # "April 2025 onwards" without from/since
    m = re.search(r"(" + _MONTH_PATTERN + r")\s+(\d{4})\s*(?:onwards|onward)", q)
    if m:
        month = _MONTH_MAP[m.group(1)]
        year = int(m.group(2))
        start = datetime.date(year, month, 1)
        return start.isoformat(), today.isoformat()

    # ── No match — caller should fall back to LLM or default window ──────
    return None
