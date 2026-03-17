"""
Google Chat API client — space discovery, message fetching, and tiered repository.

Tier 1 (startup):  Last 30 days — fetched on first load, instant queries.
Tier 2 (on-demand): Older messages — fetched only when a query needs them.

Both tiers are stored in session state and merged seamlessly.
Hourly refresh adds new messages incrementally.
"""

import datetime
import json
import logging

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

import os

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


def _fetch_messages_for_period(
    creds_json: str, space_name: str, start_date: str, end_date: str
) -> list[dict]:
    """Fetch messages from a space between start_date and end_date (inclusive).

    Paginates fully. Returns messages in chronological order.
    """
    service = _build_service(creds_json)
    api_filter = f'createTime > "{start_date}T00:00:00Z" AND createTime < "{end_date}T23:59:59Z"'

    messages: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.spaces()
            .messages()
            .list(
                parent=space_name,
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


# ── Tiered message repository ────────────────────────────────────────────────

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
    """Fast startup: fetch only last STARTUP_LOOKBACK_DAYS days."""
    repo = _get_repo()
    today = datetime.date.today()
    start = today - datetime.timedelta(days=STARTUP_LOOKBACK_DAYS)

    for i, space in enumerate(spaces):
        display_name = space.get("displayName", space["name"])
        space_id = space["name"]

        try:
            msgs = _fetch_messages_for_period(
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


def _expand_repo_if_needed(creds_json: str, spaces: list[dict], needed_start: str):
    """Fetch older messages on-demand if the query needs data before what we have.

    Merges seamlessly with existing repository data.
    """
    repo = _get_repo()

    for space in spaces:
        display_name = space.get("displayName", space["name"])
        space_id = space["name"]
        data = repo.get(display_name, {})
        current_earliest = data.get("earliest_fetched", "")

        # Already have data going back far enough
        if current_earliest and current_earliest <= needed_start:
            continue

        # Need to fetch older data: from needed_start to current_earliest
        fetch_end = current_earliest if current_earliest else datetime.date.today().isoformat()

        try:
            older_msgs = _fetch_messages_for_period(
                creds_json, space_id, needed_start, fetch_end
            )
            # Merge: deduplicate by message ID
            existing_msgs = data.get("messages", [])
            existing_ids = {m.get("name") for m in existing_msgs}
            new_msgs = [m for m in older_msgs if m.get("name") not in existing_ids]
            merged = new_msgs + existing_msgs
            merged.sort(key=lambda m: m.get("createTime", ""))

            repo[display_name] = {
                "messages": merged,
                "space_id": space_id,
                "earliest_fetched": needed_start,
                "latest_fetched": data.get("latest_fetched", datetime.date.today().isoformat()),
            }
        except Exception as exc:
            logger.warning("Expansion failed for %s: %s", display_name, exc)


def incremental_refresh(creds_json: str, spaces: list[dict]):
    """Fetch only new messages since the last refresh. Fast."""
    repo = _get_repo()
    today = datetime.date.today().isoformat()

    for space in spaces:
        display_name = space.get("displayName", space["name"])
        space_id = space["name"]
        data = repo.get(display_name, {})
        existing_msgs = data.get("messages", [])

        # Fetch from last known date
        last_date = data.get("latest_fetched", today)

        try:
            new_msgs = _fetch_messages_for_period(creds_json, space_id, last_date, today)
            existing_ids = {m.get("name") for m in existing_msgs}
            truly_new = [m for m in new_msgs if m.get("name") not in existing_ids]

            if truly_new:
                merged = existing_msgs + truly_new
                merged.sort(key=lambda m: m.get("createTime", ""))
                data["messages"] = merged

            data["latest_fetched"] = today
            repo[display_name] = data
        except Exception as exc:
            logger.warning("Incremental refresh failed for %s: %s", display_name, exc)

    st.session_state["repo_last_refreshed"] = datetime.datetime.now()


# ── Query interface ──────────────────────────────────────────────────────────

def get_all_messages() -> dict[str, list[dict]]:
    """Return all cached messages: {display_name: [messages]}."""
    repo = _get_repo()
    return {name: data.get("messages", []) for name, data in repo.items()}


def get_messages_for_range(
    start_str: str,
    end_str: str,
    creds_json: str = None,
    spaces: list[dict] = None,
) -> dict[str, list[dict]]:
    """Filter messages to [start_str, end_str].

    If the requested range extends before what's cached, automatically
    fetches older data (requires creds_json and spaces).
    """
    repo = _get_repo()

    # Check if we need to expand the repo
    if creds_json and spaces:
        for data in repo.values():
            current_earliest = data.get("earliest_fetched", "")
            if current_earliest and start_str < current_earliest:
                _expand_repo_if_needed(creds_json, spaces, start_str)
                break

    result: dict[str, list[dict]] = {}
    for name, data in repo.items():
        result[name] = [
            m for m in data.get("messages", [])
            if _msg_in_range(m, start_str, end_str)
        ]
    return result


def get_repo_stats() -> dict:
    """Return summary stats about the repository."""
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
