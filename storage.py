"""
Persistent message storage: SQLite locally + GCS for durability.

Flow:
  1. On startup, download SQLite DB from GCS (if exists)
  2. Before any API fetch, check if the (space, month) is already in SQLite
  3. After fetching from API, store in SQLite AND upload to GCS
  4. DB survives across Streamlit Cloud reboots via GCS sync

SQLite schema:
  space_months(space_id TEXT, year INT, month INT, messages_json TEXT,
               fetched_at TEXT, PRIMARY KEY(space_id, year, month))
"""

import datetime
import gzip
import json
import logging
import os
import sqlite3
import tempfile

import streamlit as st

logger = logging.getLogger(__name__)

GCS_BUCKET = "ecc-space-monitor-cache"
GCS_DB_PATH = "messages.db.gz"
LOCAL_DB_PATH = os.path.join(tempfile.gettempdir(), "ecc_messages.db")


# ── GCS helpers ──────────────────────────────────────────────────────────────

def _get_gcs_client():
    """Build a GCS client from Streamlit secrets or local key file."""
    from google.cloud import storage as gcs
    from google.oauth2 import service_account

    # Try Streamlit secrets first
    try:
        key_data = st.secrets.get("GCS_SERVICE_ACCOUNT")
        if key_data:
            info = json.loads(key_data) if isinstance(key_data, str) else dict(key_data)
            creds = service_account.Credentials.from_service_account_info(info)
            return gcs.Client(credentials=creds, project=info.get("project_id"))
    except (KeyError, json.JSONDecodeError):
        pass

    # Fall back to local key file
    key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gcs-key.json")
    if os.path.exists(key_path):
        creds = service_account.Credentials.from_service_account_file(key_path)
        return gcs.Client(credentials=creds, project=creds.project_id)

    return None


def _download_db_from_gcs() -> bool:
    """Download the DB from GCS. Returns True if successful."""
    try:
        client = _get_gcs_client()
        if not client:
            logger.info("No GCS credentials available — skipping download")
            return False

        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(GCS_DB_PATH)

        if not blob.exists():
            logger.info("No existing DB in GCS — starting fresh")
            return False

        # Download compressed
        compressed = blob.download_as_bytes()
        db_bytes = gzip.decompress(compressed)

        with open(LOCAL_DB_PATH, "wb") as f:
            f.write(db_bytes)

        logger.info("Downloaded DB from GCS (%d bytes)", len(db_bytes))
        return True

    except Exception as exc:
        logger.warning("GCS download failed: %s", exc)
        return False


def _upload_db_to_gcs() -> bool:
    """Upload the local DB to GCS (gzipped). Returns True if successful."""
    try:
        client = _get_gcs_client()
        if not client:
            return False

        if not os.path.exists(LOCAL_DB_PATH):
            return False

        with open(LOCAL_DB_PATH, "rb") as f:
            db_bytes = f.read()

        compressed = gzip.compress(db_bytes, compresslevel=6)

        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(GCS_DB_PATH)
        blob.upload_from_string(compressed, content_type="application/gzip")

        logger.info("Uploaded DB to GCS (%d bytes compressed)", len(compressed))
        return True

    except Exception as exc:
        logger.warning("GCS upload failed: %s", exc)
        return False


# ── SQLite helpers ───────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """Get or create the SQLite connection (cached in session state)."""
    if "sqlite_conn" not in st.session_state:
        conn = sqlite3.connect(LOCAL_DB_PATH, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS space_months (
                space_id TEXT,
                year INTEGER,
                month INTEGER,
                messages_json TEXT,
                message_count INTEGER DEFAULT 0,
                fetched_at TEXT,
                PRIMARY KEY (space_id, year, month)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fetched ON space_months(space_id, year, month)
        """)
        conn.commit()
        st.session_state["sqlite_conn"] = conn
    return st.session_state["sqlite_conn"]


# ── Public API ───────────────────────────────────────────────────────────────

def init_storage():
    """Initialize storage on app startup. Download DB from GCS if available."""
    if st.session_state.get("storage_initialized"):
        return

    if not os.path.exists(LOCAL_DB_PATH):
        _download_db_from_gcs()

    _get_conn()
    st.session_state["storage_initialized"] = True


def get_cached_month(space_id: str, year: int, month: int) -> list[dict] | None:
    """Check if we have messages for this (space, year, month) in SQLite.

    Returns the messages list or None if not cached.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT messages_json FROM space_months WHERE space_id=? AND year=? AND month=?",
        (space_id, year, month),
    ).fetchone()

    if row is None:
        return None

    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def store_month(space_id: str, year: int, month: int, messages: list[dict]):
    """Store messages for a (space, year, month) in SQLite."""
    conn = _get_conn()
    messages_json = json.dumps(messages, ensure_ascii=False)
    conn.execute(
        """INSERT OR REPLACE INTO space_months
           (space_id, year, month, messages_json, message_count, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (space_id, year, month, messages_json, len(messages),
         datetime.datetime.now().isoformat()),
    )
    conn.commit()


def sync_to_gcs():
    """Upload the current DB to GCS for durability."""
    _upload_db_to_gcs()


def get_storage_stats() -> dict:
    """Return storage statistics."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*), SUM(message_count), MIN(year||'-'||printf('%02d',month)), "
        "MAX(year||'-'||printf('%02d',month)) FROM space_months"
    ).fetchone()

    db_size = os.path.getsize(LOCAL_DB_PATH) if os.path.exists(LOCAL_DB_PATH) else 0

    return {
        "chunks": row[0] or 0,
        "total_messages": row[1] or 0,
        "earliest_month": row[2],
        "latest_month": row[3],
        "db_size_mb": round(db_size / 1024 / 1024, 1),
    }


def is_current_month(year: int, month: int) -> bool:
    today = datetime.date.today()
    return year == today.year and month == today.month
