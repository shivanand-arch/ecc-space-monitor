"""
Google Chat API client — space discovery, message fetching, and persistent cache.

Architecture:
  - Each (space, month) chunk is fetched once and cached via @st.cache_data
  - Startup loads last 30 days (fast, ~1-2 chunks per space)
  - On-demand: older months fetched only when a query needs them
  - Cache survives across users/sessions within the same app instance
  - Only cleared on app reboot/redeploy
  - New messages: hourly incremental refresh (today's chunk is re-fetched)
"""

import datetime
import json
import logging
import os

import streamlit as st
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from config import (
    CHAT_SCOPES,
    MAX_MESSAGES_PER_SPACE,
    CACHE_TTL_SECONDS,
    STARTUP_LOOKBACK_DAYS,
    TOKEN_PATH,
)

logger = logging.getLogger(__name__)


# ── Credential helpers ───────────────────────────────────────────────────────

def get_credentials() -> Credentials | None:
    """Resolve Google OAuth credentials from session -> file -> secrets."""

    if "google_creds" in st.session_state:
        creds = st.session_state["google_creds"]
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                st.session_state["google_creds"] = creds
                _persist_token(creds)
                return creds
            except Exception as exc:
                logger.warning("Session creds refresh failed: %s", exc)

    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, CHAT_SCOPES)
            if creds and creds.valid:
                st.session_state["google_creds"] = creds
                return creds
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                st.session_state["google_creds"] = creds
                _persist_token(creds)
                return creds
        except (ValueError, FileNotFoundError) as exc:
            logger.warning("Token file invalid: %s", exc)
        except Exception as exc:
            logger.warning("Token file refresh failed: %s", exc)

    try:
        token_data = st.secrets.get("GOOGLE_TOKEN")
        if token_data:
            info = json.loads(token_data) if isinstance(token_data, str) else dict(token_data)
            creds = Credentials(
                token=info.get("token", ""),
                refresh_token=info.get("refresh_token"),
                token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=info.get("client_id"),
                client_secret=info.get("client_secret"),
                scopes=CHAT_SCOPES,
            )
            if creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as exc:
                    logger.warning("Secrets token refresh failed: %s", exc)
            st.session_state["google_creds"] = creds
            return creds
    except KeyError:
        pass
    except Exception as exc:
        st.sidebar.warning(f"Token from secrets failed: {exc}")

    return None


def _persist_token(creds: Credentials) -> None:
    try:
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    except OSError:
        pass


# ── API wrappers ─────────────────────────────────────────────────────────────

def _build_service(creds_json: str):
    creds = Credentials.from_authorized_user_info(json.loads(creds_json), CHAT_SCOPES)
    return build("chat", "v1", credentials=creds)


@st.cache_data(ttl=300)
def fetch_spaces(_creds_json: str) -> list[dict]:
    from config import TARGET_SPACE_NAMES

    service = _build_service(_creds_json)
    results: list[dict] = []
    page_token = None
    while True:
        resp = service.spaces().list(pageSize=100, pageToken=page_token).execute()
        results.extend(resp.get("spaces", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    target_lower = {n.lower() for n in TARGET_SPACE_NAMES}
    matched = [s for s in results if s.get("displayName", "").lower() in target_lower]

    if len(matched) < len(TARGET_SPACE_NAMES):
        found = {s.get("displayName", "").lower() for s in matched}
        missing = target_lower - found
        if missing:
            logger.warning("Spaces not found: %s", missing)

    return matched


# ── CACHED per-month message fetch ──────────────────────────────────────────
# The key insight: we fetch by (space_id, year, month) and cache each chunk
# for 24 hours. The current month is re-fetched each hour to pick up new
# messages. Past months are immutable and cached for a full day.

@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_month_cached(
    _creds_json: str,
    space_id: str,
    year: int,
    month: int,
    _is_current_month: bool,  # used to vary TTL via cache key
) -> list[dict]:
    """Fetch all messages from a space for a given month.

    Past months: cached 24h (immutable data).
    Current month: cache key includes _is_current_month=True, which changes
    the effective TTL via a separate cache entry.
    """
    start_date = datetime.date(year, month, 1)
    if month == 12:
        end_date = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        end_date = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

    service = _build_service(_creds_json)
    api_filter = (
        f'createTime > "{start_date.isoformat()}T00:00:00Z" '
        f'AND createTime < "{end_date.isoformat()}T23:59:59Z"'
    )

    messages: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.spaces()
            .messages()
            .list(
                parent=space_id,
                pageSize=1000,
                filter=api_filter,
                pageToken=page_token,
            )
            .execute()
        )
        messages.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token or len(messages) >= MAX_MESSAGES_PER_SPACE:
            break

    messages.sort(key=lambda m: m.get("createTime", ""))
    return messages


# Current month re-fetched more frequently
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_current_month_cached(
    _creds_json: str, space_id: str, year: int, month: int, _hour_key: int
) -> list[dict]:
    """Fetch current month's messages. Cached 1 hour. _hour_key rotates the cache."""
    return _fetch_month_cached(_creds_json, space_id, year, month, True)


def _months_between(start_date: datetime.date, end_date: datetime.date) -> list[tuple[int, int]]:
    """Return list of (year, month) tuples covering the range."""
    months = []
    d = start_date.replace(day=1)
    while d <= end_date:
        months.append((d.year, d.month))
        if d.month == 12:
            d = d.replace(year=d.year + 1, month=1)
        else:
            d = d.replace(month=d.month + 1)
    return months


def fetch_messages_for_range(
    creds_json: str,
    space_id: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch messages for a date range, using per-month caching.

    Each month is fetched independently and cached. Past months hit cache
    instantly. Only the current month refreshes hourly.
    """
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    today = datetime.date.today()

    all_msgs: list[dict] = []
    for year, month in _months_between(start, end):
        is_current = (year == today.year and month == today.month)
        if is_current:
            hour_key = datetime.datetime.now().hour
            msgs = _fetch_current_month_cached(creds_json, space_id, year, month, hour_key)
        else:
            msgs = _fetch_month_cached(creds_json, space_id, year, month, False)
        all_msgs.extend(msgs)

    # Filter to exact date range (months are coarse)
    filtered = [
        m for m in all_msgs
        if _msg_in_range(m, start_date, end_date)
    ]
    return filtered


# ── Repository (session-level index over cached data) ────────────────────────

def _get_repo() -> dict:
    if "message_repo" not in st.session_state:
        st.session_state["message_repo"] = {}
    return st.session_state["message_repo"]


def repo_last_refreshed() -> datetime.datetime | None:
    return st.session_state.get("repo_last_refreshed")


def repo_needs_refresh() -> bool:
    ts = repo_last_refreshed()
    if ts is None:
        return True
    return (datetime.datetime.now() - ts).total_seconds() > CACHE_TTL_SECONDS


def startup_load(creds_json: str, spaces: list[dict], progress_callback=None):
    """Fast startup: fetch only last STARTUP_LOOKBACK_DAYS days.

    Uses per-month caching so subsequent loads are instant.
    """
    repo = _get_repo()
    today = datetime.date.today()
    start = today - datetime.timedelta(days=STARTUP_LOOKBACK_DAYS)

    for i, space in enumerate(spaces):
        display_name = space.get("displayName", space["name"])
        space_id = space["name"]

        try:
            msgs = fetch_messages_for_range(
                creds_json, space_id, start.isoformat(), today.isoformat()
            )
            repo[display_name] = {
                "messages": msgs,
                "space_id": space_id,
                "earliest_fetched": start.isoformat(),
                "latest_fetched": today.isoformat(),
            }
        except Exception as exc:
            logger.warning("Startup load failed for %s: %s", display_name, exc)
            st.warning(f"Could not fetch messages from {display_name}: {exc}")
            if display_name not in repo:
                repo[display_name] = {
                    "messages": [],
                    "space_id": space_id,
                    "earliest_fetched": start.isoformat(),
                    "latest_fetched": today.isoformat(),
                }

        if progress_callback:
            progress_callback((i + 1) / len(spaces))

    st.session_state["repo_last_refreshed"] = datetime.datetime.now()


def expand_repo(creds_json: str, spaces: list[dict], needed_start: str):
    """Fetch older messages on-demand. Each month is independently cached.

    Returns the number of new messages added.
    """
    repo = _get_repo()
    today = datetime.date.today()
    total_new = 0

    for space in spaces:
        display_name = space.get("displayName", space["name"])
        space_id = space["name"]
        data = repo.get(display_name, {})
        current_earliest = data.get("earliest_fetched", today.isoformat())

        # Already have data going back far enough
        if current_earliest <= needed_start:
            continue

        # Fetch from needed_start to current_earliest (per-month cached)
        older_msgs = fetch_messages_for_range(
            creds_json, space_id, needed_start, current_earliest
        )

        # Merge and deduplicate
        existing_msgs = data.get("messages", [])
        existing_ids = {m.get("name") for m in existing_msgs}
        new_msgs = [m for m in older_msgs if m.get("name") not in existing_ids]
        total_new += len(new_msgs)

        merged = new_msgs + existing_msgs
        merged.sort(key=lambda m: m.get("createTime", ""))

        repo[display_name] = {
            "messages": merged,
            "space_id": space_id,
            "earliest_fetched": needed_start,
            "latest_fetched": data.get("latest_fetched", today.isoformat()),
        }

    return total_new


def incremental_refresh(creds_json: str, spaces: list[dict]):
    """Fetch only new messages since last refresh. Uses current-month cache."""
    repo = _get_repo()
    today = datetime.date.today()

    for space in spaces:
        display_name = space.get("displayName", space["name"])
        space_id = space["name"]
        data = repo.get(display_name, {})
        existing_msgs = data.get("messages", [])

        last_date = data.get("latest_fetched", today.isoformat())

        try:
            new_msgs = fetch_messages_for_range(
                creds_json, space_id, last_date, today.isoformat()
            )
            existing_ids = {m.get("name") for m in existing_msgs}
            truly_new = [m for m in new_msgs if m.get("name") not in existing_ids]

            if truly_new:
                merged = existing_msgs + truly_new
                merged.sort(key=lambda m: m.get("createTime", ""))
                data["messages"] = merged

            data["latest_fetched"] = today.isoformat()
            repo[display_name] = data
        except Exception as exc:
            logger.warning("Incremental refresh failed for %s: %s", display_name, exc)

    st.session_state["repo_last_refreshed"] = datetime.datetime.now()


# ── Query interface ──────────────────────────────────────────────────────────

def get_all_messages() -> dict[str, list[dict]]:
    """Return all currently loaded messages: {display_name: [messages]}."""
    repo = _get_repo()
    return {name: data.get("messages", []) for name, data in repo.items()}


def get_messages_in_range(
    start_str: str,
    end_str: str,
    creds_json: str,
    spaces: list[dict],
) -> tuple[dict[str, list[dict]], int]:
    """Get messages for a date range, expanding the repo if needed.

    Returns (messages_by_space, new_messages_fetched).
    """
    repo = _get_repo()

    # Check if expansion is needed
    needs_expansion = False
    for data in repo.values():
        current_earliest = data.get("earliest_fetched", "")
        if current_earliest and start_str < current_earliest:
            needs_expansion = True
            break

    new_count = 0
    if needs_expansion:
        new_count = expand_repo(creds_json, spaces, start_str)

    # Filter from repo
    result: dict[str, list[dict]] = {}
    for name, data in repo.items():
        result[name] = [
            m for m in data.get("messages", [])
            if _msg_in_range(m, start_str, end_str)
        ]
    return result, new_count


def get_repo_stats() -> dict:
    repo = _get_repo()
    total = 0
    earliest = None
    latest = None

    for data in repo.values():
        msgs = data.get("messages", [])
        total += len(msgs)
        ef = data.get("earliest_fetched")
        lf = data.get("latest_fetched")
        if ef and (earliest is None or ef < earliest):
            earliest = ef
        if lf and (latest is None or lf > latest):
            latest = lf

    return {"total": total, "earliest": earliest, "latest": latest}


def _msg_in_range(msg: dict, start_str: str, end_str: str) -> bool:
    create_time = msg.get("createTime", "")
    if not create_time:
        return False
    try:
        msg_date = create_time[:10]
        return start_str <= msg_date <= end_str
    except Exception:
        return False
