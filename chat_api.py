"""
Google Chat API client — space discovery, message fetching, and full message repository.

On first load the app fetches ALL messages from every monitored space (full
history).  The repository is stored in ``st.session_state`` and refreshed
hourly to pick up new messages.  Date filtering happens in-memory on the
already-cached dataset — no repeated API calls.
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
    TOKEN_PATH,
    CLIENT_SECRET_PATH,
)

import os

logger = logging.getLogger(__name__)


# ── Credential helpers ───────────────────────────────────────────────────────

def get_credentials() -> Credentials | None:
    """Resolve Google OAuth credentials from session -> file -> secrets."""

    # 1. Session state (already authenticated this session)
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

    # 2. Local token file (for local dev)
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

    # 3. Streamlit Cloud secrets
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
    """Best-effort save to local file (no-op on Streamlit Cloud)."""
    try:
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    except OSError:
        pass


# ── API wrappers ─────────────────────────────────────────────────────────────

def _build_service(creds_json: str):
    """Build a Chat API service from serialised creds."""
    creds = Credentials.from_authorized_user_info(json.loads(creds_json), CHAT_SCOPES)
    return build("chat", "v1", credentials=creds)


@st.cache_data(ttl=300)
def fetch_spaces(_creds_json: str) -> list[dict]:
    """Return the subset of spaces whose display names match TARGET_SPACE_NAMES."""
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

    # Normalise comparison so minor casing differences don't cause mismatches
    target_lower = {n.lower() for n in TARGET_SPACE_NAMES}
    matched = [s for s in results if s.get("displayName", "").lower() in target_lower]

    if len(matched) < len(TARGET_SPACE_NAMES):
        found = {s.get("displayName", "").lower() for s in matched}
        missing = target_lower - found
        if missing:
            logger.warning("Spaces not found: %s", missing)

    return matched


def _fetch_all_messages(creds_json: str, space_name: str) -> list[dict]:
    """Fetch ALL messages from a space. No date filter. Paginate until done.

    Returns messages in chronological order (oldest first).
    """
    service = _build_service(creds_json)
    messages: list[dict] = []
    page_token = None

    while True:
        resp = (
            service.spaces()
            .messages()
            .list(
                parent=space_name,
                pageSize=1000,
                pageToken=page_token,
            )
            .execute()
        )
        batch = resp.get("messages", [])
        messages.extend(batch)
        page_token = resp.get("nextPageToken")
        if not page_token or len(messages) >= MAX_MESSAGES_PER_SPACE:
            break

    # API returns newest-first by default; sort chronologically
    messages.sort(key=lambda m: m.get("createTime", ""))
    return messages


def _fetch_messages_since(creds_json: str, space_name: str, since: str) -> list[dict]:
    """Fetch only messages newer than *since* (ISO date string).

    Used for incremental refresh so we don't re-fetch the entire history.
    """
    service = _build_service(creds_json)
    api_filter = f'createTime > "{since}T00:00:00Z"'

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
        if not page_token:
            break

    messages.sort(key=lambda m: m.get("createTime", ""))
    return messages


# ── Full message repository (session-state backed) ──────────────────────────

def _get_repo() -> dict:
    """Get the message repository from session state."""
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


def load_full_repository(creds_json: str, spaces: list[dict], progress_callback=None):
    """Fetch ALL messages from all spaces and store in the repository.

    First load fetches everything; subsequent refreshes only fetch new
    messages since the last known message.
    """
    repo = _get_repo()

    for i, space in enumerate(spaces):
        display_name = space.get("displayName", space["name"])
        space_id = space["name"]
        existing = repo.get(display_name, {})
        existing_msgs = existing.get("messages", [])

        try:
            if existing_msgs:
                # Incremental: fetch only new messages since last known
                last_time = existing_msgs[-1].get("createTime", "")[:10]
                new_msgs = _fetch_messages_since(creds_json, space_id, last_time)
                # Deduplicate by message ID
                existing_ids = {m.get("name") for m in existing_msgs}
                truly_new = [m for m in new_msgs if m.get("name") not in existing_ids]
                all_msgs = existing_msgs + truly_new
                all_msgs.sort(key=lambda m: m.get("createTime", ""))
            else:
                # First load: fetch everything
                all_msgs = _fetch_all_messages(creds_json, space_id)

            repo[display_name] = {
                "messages": all_msgs,
                "space_id": space_id,
            }
        except Exception as exc:
            logger.warning("Repository load failed for %s: %s", display_name, exc)
            st.warning(f"Could not fetch messages from {display_name}: {exc}")
            if display_name not in repo:
                repo[display_name] = {"messages": [], "space_id": space_id}

        if progress_callback:
            progress_callback((i + 1) / len(spaces))

    st.session_state["repo_last_refreshed"] = datetime.datetime.now()
    return repo


def get_all_messages() -> dict[str, list[dict]]:
    """Return the full repository: {display_name: [messages]}."""
    repo = _get_repo()
    return {name: data.get("messages", []) for name, data in repo.items()}


def get_messages_for_range(start_str: str, end_str: str) -> dict[str, list[dict]]:
    """Filter the repository to messages within [start_str, end_str].

    No API calls — purely in-memory filtering.
    """
    repo = _get_repo()
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
        for m in msgs:
            ct = m.get("createTime", "")[:10]
            if ct:
                if earliest is None or ct < earliest:
                    earliest = ct
                if latest is None or ct > latest:
                    latest = ct

    return {"total": total, "earliest": earliest, "latest": latest}


def _msg_in_range(msg: dict, start_str: str, end_str: str) -> bool:
    """Check whether a message falls within [start_str, end_str]."""
    create_time = msg.get("createTime", "")
    if not create_time:
        return False
    try:
        msg_date = create_time[:10]
        return start_str <= msg_date <= end_str
    except Exception:
        return False  # exclude unparseable messages
