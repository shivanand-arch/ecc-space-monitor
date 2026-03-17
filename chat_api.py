"""
Google Chat API client — space discovery, message fetching, and message cache.

The cache stores all previously-fetched messages in ``st.session_state`` so
repeated queries within the cached date window are instant.
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
    CACHE_LOOKBACK_DAYS,
    CACHE_TTL_SECONDS,
    TOKEN_PATH,
    CLIENT_SECRET_PATH,
)

import os

logger = logging.getLogger(__name__)


# ── Credential helpers ───────────────────────────────────────────────────────

def get_credentials() -> Credentials | None:
    """Resolve Google OAuth credentials from session → file → secrets."""

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


def fetch_messages_from_api(
    creds_json: str,
    space_name: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch messages from *space_name* between *start_date* and *end_date*.

    Paginates up to ``MAX_MESSAGES_PER_SPACE`` messages; newest first so
    the most recent context is always captured.
    """
    service = _build_service(creds_json)

    start_ts = f"{start_date}T00:00:00Z"
    end_ts = f"{end_date}T23:59:59Z"
    api_filter = f'createTime > "{start_ts}" AND createTime < "{end_ts}"'

    messages: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.spaces()
            .messages()
            .list(
                parent=space_name,
                pageSize=100,
                filter=api_filter,
                orderBy="createTime desc",
                pageToken=page_token,
            )
            .execute()
        )
        messages.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token or len(messages) >= MAX_MESSAGES_PER_SPACE:
            break

    messages = messages[:MAX_MESSAGES_PER_SPACE]
    messages.reverse()  # chronological order
    return messages


# ── Session-state message cache ──────────────────────────────────────────────

def _get_cache() -> dict:
    if "message_cache" not in st.session_state:
        st.session_state["message_cache"] = {}
    return st.session_state["message_cache"]


def cache_last_refreshed() -> datetime.datetime | None:
    return st.session_state.get("cache_last_refreshed")


def cache_needs_refresh() -> bool:
    ts = cache_last_refreshed()
    if ts is None:
        return True
    return (datetime.datetime.now() - ts).total_seconds() > CACHE_TTL_SECONDS


def refresh_cache(creds_json: str, spaces: list[dict], lookback_days: int = CACHE_LOOKBACK_DAYS):
    """Pre-populate the cache with the last *lookback_days* of messages."""
    cache = _get_cache()
    today = datetime.date.today()
    start = today - datetime.timedelta(days=lookback_days)

    for space in spaces:
        display_name = space.get("displayName", space["name"])
        space_id = space["name"]
        try:
            msgs = fetch_messages_from_api(creds_json, space_id,
                                           start.isoformat(), today.isoformat())
            cache[display_name] = {
                "messages": msgs,
                "space_id": space_id,
                "earliest": start.isoformat(),
                "latest": today.isoformat(),
            }
        except Exception as exc:
            logger.warning("Cache refresh failed for %s: %s", display_name, exc)
            st.warning(f"Could not fetch messages from {display_name}: {exc}")
            if display_name not in cache:
                cache[display_name] = {
                    "messages": [],
                    "space_id": space_id,
                    "earliest": start.isoformat(),
                    "latest": today.isoformat(),
                }

    st.session_state["cache_last_refreshed"] = datetime.datetime.now()
    st.session_state["cache_lookback_days"] = lookback_days
    return cache


def get_messages_for_range(
    creds_json: str,
    spaces: list[dict],
    start_str: str,
    end_str: str,
) -> dict[str, list[dict]]:
    """Return messages per-space for the requested date range.

    Serves from cache when possible; fetches and merges otherwise.
    """
    cache = _get_cache()
    result: dict[str, list[dict]] = {}

    for space in spaces:
        display_name = space.get("displayName", space["name"])
        space_id = space["name"]
        cached = cache.get(display_name, {})
        cached_earliest = cached.get("earliest", "")
        cached_latest = cached.get("latest", "")
        cached_msgs = cached.get("messages", [])

        req_start = datetime.date.fromisoformat(start_str)
        req_end = datetime.date.fromisoformat(end_str)

        # Fully within cache — filter in memory
        if cached_earliest and cached_latest:
            cache_start = datetime.date.fromisoformat(cached_earliest)
            cache_end = datetime.date.fromisoformat(cached_latest)
            if req_start >= cache_start and req_end <= cache_end:
                result[display_name] = [
                    m for m in cached_msgs if _msg_in_range(m, start_str, end_str)
                ]
                continue

        # Outside cache — live fetch + merge
        try:
            msgs = fetch_messages_from_api(creds_json, space_id, start_str, end_str)
            result[display_name] = msgs

            if cached_msgs:
                existing_ids = {m.get("name") for m in cached_msgs}
                new_msgs = [m for m in msgs if m.get("name") not in existing_ids]
                merged = cached_msgs + new_msgs
                merged.sort(key=lambda m: m.get("createTime", ""))
                new_earliest = min(cached_earliest, start_str)
                new_latest = max(cached_latest, end_str)
            else:
                merged = msgs
                new_earliest = start_str
                new_latest = end_str

            cache[display_name] = {
                "messages": merged,
                "space_id": space_id,
                "earliest": new_earliest,
                "latest": new_latest,
            }
        except Exception as exc:
            logger.warning("Fetch failed for %s: %s", display_name, exc)
            st.warning(f"Could not fetch messages from {display_name}: {exc}")
            result[display_name] = []

    return result


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
