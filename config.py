"""
Centralised configuration for ECC Space Monitor.

All tuneable constants live here so they can be adjusted without
touching business logic.
"""

import os

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
CLIENT_SECRET_PATH = os.path.join(BASE_DIR, "client_secret.json")

# ── Google API scopes ────────────────────────────────────────────────────────
CHAT_SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
]
LOGIN_SCOPES = ["openid", "email", "profile"]

# ── Spaces to monitor ────────────────────────────────────────────────────────
# Display names used for initial discovery; once found the stable space-id
# (e.g. "spaces/AAAA...") is cached in session state for the rest of the
# session so a rename won't break anything mid-session.
TARGET_SPACE_NAMES = [
    "(New) ECC DRI's Huddle",
    "(New) ECC Panic Room",
    "DRI's Huddle",
    "Panic Room",
]

# ── Auth / security ──────────────────────────────────────────────────────────
ALLOWED_DOMAIN = "exotel.com"

# ── Message fetching ─────────────────────────────────────────────────────────
MAX_MESSAGES_PER_SPACE = 1000      # safety cap per API pagination run
MAX_CONTEXT_CHARS = 500_000        # ~125K tokens sent to Claude

# ── Cache ────────────────────────────────────────────────────────────────────
CACHE_LOOKBACK_DAYS = 30           # pre-fetch window on startup
DEFAULT_LOOKBACK_DAYS = 7          # dashboard default view
CACHE_TTL_SECONDS = 3600           # auto-refresh interval (1 hour)

# ── Chat history ─────────────────────────────────────────────────────────────
MAX_CHAT_HISTORY_MESSAGES = 20     # keep last 20 messages (10 exchanges)

# ── Claude ───────────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
ANALYSIS_PROMPT_VERSION = "v1"     # bump when analysis prompt changes
