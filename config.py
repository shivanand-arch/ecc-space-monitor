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
TARGET_SPACE_NAMES = [
    "(New) ECC DRI's Huddle",
    "(New) ECC Panic Room",
    "DRI's Huddle",
    "Panic Room",
]

# ── Auth / security ──────────────────────────────────────────────────────────
ALLOWED_DOMAIN = "exotel.com"

# ── Message fetching ─────────────────────────────────────────────────────────
MAX_MESSAGES_PER_SPACE = 50_000    # safety cap per API pagination run
MAX_CONTEXT_CHARS = 500_000        # ~125K tokens sent to Claude

# ── Cache / repository ───────────────────────────────────────────────────────
STARTUP_LOOKBACK_DAYS = 30         # fast startup: only last 30 days
CACHE_TTL_SECONDS = 3600           # hourly incremental refresh
DEFAULT_DASHBOARD_DAYS = 7         # dashboard Recent Messages panel

# ── Chat history ─────────────────────────────────────────────────────────────
MAX_CHAT_HISTORY_MESSAGES = 20     # keep last 20 messages (10 exchanges)

# ── Claude ───────────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
ANALYSIS_PROMPT_VERSION = "v1"     # bump when analysis prompt changes
