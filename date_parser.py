"""
Deterministic date-range extraction from natural-language queries.

Handles common patterns ("yesterday", "last 2 weeks", "since March 1")
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

MAX_RANGE_DAYS = 60


def _clamp(start: datetime.date, end: datetime.date, today: datetime.date) -> DateRange:
    """Ensure the range does not exceed MAX_RANGE_DAYS and doesn't go past today."""
    if end > today:
        end = today
    if (end - start).days > MAX_RANGE_DAYS:
        start = end - datetime.timedelta(days=MAX_RANGE_DAYS)
    return start.isoformat(), end.isoformat()


def parse_date_range(question: str, today: Optional[datetime.date] = None) -> Optional[DateRange]:
    """Try to deterministically extract a date range from *question*.

    Returns ``(start_str, end_str)`` in ISO format, or ``None`` if no
    time expression is detected (caller should use the default window).
    """
    today = today or datetime.date.today()
    q = question.lower().strip()

    # ── Exact keywords ───────────────────────────────────────────────────
    if "today" in q:
        return _clamp(today, today, today)

    if "yesterday" in q:
        d = today - datetime.timedelta(days=1)
        return _clamp(d, d, today)

    # ── "last N days/weeks/months" or "past N …" ────────────────────────
    m = re.search(r"(?:last|past)\s+(\d+)\s+(day|week|month)s?", q)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "day":
            delta = datetime.timedelta(days=n)
        elif unit == "week":
            delta = datetime.timedelta(weeks=n)
        else:  # month
            delta = datetime.timedelta(days=n * 30)
        start = today - delta
        return _clamp(start, today, today)

    # ── "last week" / "past week" (no number) ───────────────────────────
    if re.search(r"(?:last|past)\s+week\b", q):
        start = today - datetime.timedelta(weeks=1)
        return _clamp(start, today, today)

    # ── "last month" / "past month" ─────────────────────────────────────
    if re.search(r"(?:last|past)\s+month\b", q):
        start = today - datetime.timedelta(days=30)
        return _clamp(start, today, today)

    # ── "this week" ─────────────────────────────────────────────────────
    if "this week" in q:
        start = today - datetime.timedelta(days=today.weekday())  # Monday
        return _clamp(start, today, today)

    # ── "this month" ────────────────────────────────────────────────────
    if "this month" in q:
        start = today.replace(day=1)
        return _clamp(start, today, today)

    # ── "since <date>" or "from <date>" ─────────────────────────────────
    m = re.search(r"(?:since|from)\s+(\d{4}-\d{2}-\d{2})", q)
    if m:
        start = datetime.date.fromisoformat(m.group(1))
        return _clamp(start, today, today)

    # ── "since March 1" / "from Jan 15" ─────────────────────────────────
    m = re.search(r"(?:since|from)\s+(" + "|".join(_MONTH_MAP) + r")\s+(\d{1,2})", q)
    if m:
        month = _MONTH_MAP[m.group(1)]
        day = int(m.group(2))
        year = today.year if month <= today.month else today.year - 1
        try:
            start = datetime.date(year, month, day)
            return _clamp(start, today, today)
        except ValueError:
            pass

    # ── "in January" / "in March" ───────────────────────────────────────
    m = re.search(r"\bin\s+(" + "|".join(_MONTH_MAP) + r")\b", q)
    if m:
        month = _MONTH_MAP[m.group(1)]
        year = today.year if month <= today.month else today.year - 1
        start = datetime.date(year, month, 1)
        if month == 12:
            end = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            end = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
        return _clamp(start, end, today)

    # ── "off late" / "recently" / "lately" → last 14 days ───────────────
    if re.search(r"\b(off\s+late|recently|lately)\b", q):
        start = today - datetime.timedelta(days=14)
        return _clamp(start, today, today)

    # ── No match — caller should use default window ─────────────────────
    return None
