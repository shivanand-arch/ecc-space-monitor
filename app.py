import streamlit as st
import json
import os
import re
import datetime
import hashlib
import secrets
import pandas as pd
import requests as req
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import anthropic

# ── Config ──────────────────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/chat.spaces.readonly",
          "https://www.googleapis.com/auth/chat.messages.readonly",
          "https://www.googleapis.com/auth/chat.memberships.readonly"]

LOGIN_SCOPES = ["openid", "email", "profile"]

TARGET_SPACES = [
    "(New) ECC DRI's Huddle",
    "(New) ECC Panic Room",
    "DRI's Huddle",
    "Panic Room",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
CLIENT_SECRET_PATH = os.path.join(BASE_DIR, "client_secret.json")

ALLOWED_DOMAIN = "exotel.com"

# OAuth client for user login (from secrets or client_secret.json)
def _get_login_client():
    try:
        return st.secrets["GOOGLE_CLIENT_ID"], st.secrets["GOOGLE_CLIENT_SECRET"]
    except Exception:
        pass
    if os.path.exists(CLIENT_SECRET_PATH):
        with open(CLIENT_SECRET_PATH) as f:
            info = json.load(f).get("web", {})
            return info.get("client_id", ""), info.get("client_secret", "")
    return "", ""

LOGIN_CLIENT_ID, LOGIN_CLIENT_SECRET = _get_login_client()

st.set_page_config(page_title="ECC Space Monitor", page_icon="📊", layout="wide")


# ── Google OAuth Login ──────────────────────────────────────────────────────
def _get_redirect_uri():
    """Detect the correct redirect URI based on environment."""
    # Check if running on Streamlit Cloud
    try:
        app_url = st.secrets.get("APP_URL", "")
        if app_url:
            return app_url.rstrip("/")
    except Exception:
        pass
    return "http://localhost:8501"


def _build_google_auth_url():
    """Build Google OAuth2 authorization URL for user login."""
    redirect_uri = _get_redirect_uri()
    state = secrets.token_urlsafe(32)
    st.session_state["oauth_login_state"] = state

    params = {
        "client_id": LOGIN_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(LOGIN_SCOPES),
        "access_type": "online",
        "state": state,
        "hd": ALLOWED_DOMAIN,  # Restrict to exotel.com in Google's UI
        "prompt": "select_account",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://accounts.google.com/o/oauth2/v2/auth?{qs}"


def _exchange_code_for_user_info(code):
    """Exchange authorization code for user info."""
    redirect_uri = _get_redirect_uri()
    token_resp = req.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": LOGIN_CLIENT_ID,
        "client_secret": LOGIN_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    token_data = token_resp.json()
    if "error" in token_data:
        return None, f"Token error: {token_data.get('error_description', token_data['error'])}"

    # Get user info
    userinfo_resp = req.get("https://www.googleapis.com/oauth2/v2/userinfo",
                            headers={"Authorization": f"Bearer {token_data['access_token']}"})
    userinfo = userinfo_resp.json()
    return userinfo, None


def check_google_auth():
    """Handle Google OAuth login flow. Returns True if authenticated."""
    # Already authenticated
    if "authenticated_email" in st.session_state and st.session_state["authenticated_email"]:
        return True

    # Check for OAuth callback
    params = st.query_params
    code = params.get("code")
    if code:
        # Prevent reuse
        if "last_login_code" not in st.session_state or st.session_state["last_login_code"] != code:
            st.session_state["last_login_code"] = code
            userinfo, error = _exchange_code_for_user_info(code)
            if error:
                st.error(error)
                st.query_params.clear()
                return False
            email = userinfo.get("email", "").lower()
            if not email.endswith(f"@{ALLOWED_DOMAIN}"):
                st.error(f"Access restricted to @{ALLOWED_DOMAIN} accounts. You signed in as {email}.")
                st.query_params.clear()
                return False
            st.session_state["authenticated_email"] = email
            st.session_state["user_name"] = userinfo.get("name", email)
            st.session_state["user_picture"] = userinfo.get("picture", "")
            st.query_params.clear()
            st.rerun()
        else:
            st.query_params.clear()
            return "authenticated_email" in st.session_state and bool(st.session_state["authenticated_email"])

    # Show login page
    st.markdown("""
    <style>
        .login-container {
            display: flex; align-items: center; justify-content: center;
            min-height: 70vh;
        }
        .login-box {
            max-width: 420px; width: 100%;
            background: white; border-radius: 16px;
            padding: 48px 40px; text-align: center;
            box-shadow: 0 4px 24px rgba(0,0,0,0.12);
        }
        .login-title { font-size: 26px; font-weight: 700; color: #1a1a2e; margin-bottom: 8px; }
        .login-subtitle { font-size: 14px; color: #666; margin-bottom: 32px; }
        .google-btn {
            display: inline-flex; align-items: center; justify-content: center; gap: 12px;
            background: #fff; color: #3c4043; border: 1px solid #dadce0;
            border-radius: 8px; padding: 12px 24px; font-size: 15px; font-weight: 500;
            text-decoration: none; transition: all 0.2s;
            width: 100%; box-sizing: border-box;
        }
        .google-btn:hover { background: #f7f8f8; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .google-icon { width: 20px; height: 20px; }
        .domain-note { font-size: 12px; color: #999; margin-top: 16px; }
    </style>
    """, unsafe_allow_html=True)

    auth_url = _build_google_auth_url()

    st.markdown(f"""
    <div class="login-container">
        <div class="login-box">
            <div class="login-title">ECC Space Monitor</div>
            <div class="login-subtitle">AI-powered Google Chat space analysis</div>
            <a href="{auth_url}" class="google-btn">
                <svg class="google-icon" viewBox="0 0 24 24">
                    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/>
                    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                </svg>
                Sign in with Google
            </a>
            <div class="domain-note">Restricted to @exotel.com accounts</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    return False


if not check_google_auth():
    st.stop()


# ── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background: #f8f9fb; }
    header[data-testid="stHeader"] {
        background: linear-gradient(90deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        color: white;
    }
    div[data-testid="stSidebarContent"] {
        background: #1a1a2e;
        color: white;
    }
    .space-card {
        background: white;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        border-left: 4px solid #0f3460;
    }
    .metric-card {
        background: white;
        border-radius: 12px;
        padding: 16px;
        text-align: center;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .metric-value { font-size: 28px; font-weight: 700; color: #0f3460; }
    .metric-label { font-size: 13px; color: #666; margin-top: 4px; }
    .analysis-section {
        background: white;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .msg-row { padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
    .msg-sender { font-weight: 600; color: #1a1a2e; font-size: 13px; }
    .msg-time { color: #999; font-size: 11px; }
    .msg-text { color: #333; font-size: 14px; margin-top: 4px; }
    .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
                  background: #22c55e; margin-right: 6px; }
</style>
""", unsafe_allow_html=True)


# ── Auth ────────────────────────────────────────────────────────────────────
def get_credentials():
    """Get or refresh Google OAuth credentials from session, token file, or Streamlit secrets."""
    # 1. Check session state
    if "google_creds" in st.session_state:
        creds = st.session_state["google_creds"]
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            st.session_state["google_creds"] = creds
            _save_token(creds)
            return creds

    # 2. Check local token file (for local dev)
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
            if creds and creds.valid:
                st.session_state["google_creds"] = creds
                return creds
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                st.session_state["google_creds"] = creds
                _save_token(creds)
                return creds
        except Exception:
            pass

    # 3. Check Streamlit secrets (for cloud deployment)
    try:
        token_data = None
        if "GOOGLE_TOKEN" in st.secrets:
            token_data = st.secrets["GOOGLE_TOKEN"]
        if token_data:
            if isinstance(token_data, str):
                info = json.loads(token_data)
            else:
                info = dict(token_data)
            creds = Credentials(
                token=info.get("token", ""),
                refresh_token=info.get("refresh_token"),
                token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=info.get("client_id"),
                client_secret=info.get("client_secret"),
                scopes=SCOPES,
            )
            if creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    pass
            st.session_state["google_creds"] = creds
            return creds
    except Exception as e:
        st.sidebar.warning(f"Token from secrets failed: {e}")

    return None


def _save_token(creds):
    try:
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    except Exception:
        pass  # On cloud, we can't write files


# ── Google Chat API helpers ─────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_spaces(_creds_json):
    creds = Credentials.from_authorized_user_info(json.loads(_creds_json), SCOPES)
    service = build("chat", "v1", credentials=creds)
    results = []
    page_token = None
    while True:
        resp = service.spaces().list(pageSize=100, pageToken=page_token).execute()
        results.extend(resp.get("spaces", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    matched = [s for s in results if s.get("displayName") in TARGET_SPACES]
    return matched


@st.cache_data(ttl=120)
def fetch_messages(_creds_json, space_name, start_date_str, end_date_str):
    """Fetch messages from a space within a date range.

    Uses the Chat API filter on createTime and orders newest-first so the
    most recent context is always captured even for very active spaces.
    """
    creds = Credentials.from_authorized_user_info(json.loads(_creds_json), SCOPES)
    service = build("chat", "v1", credentials=creds)

    # Build RFC-3339 timestamps for the filter
    start_ts = f"{start_date_str}T00:00:00Z"
    end_ts = f"{end_date_str}T23:59:59Z"
    api_filter = f'createTime > "{start_ts}" AND createTime < "{end_ts}"'

    MAX_MESSAGES_PER_SPACE = 1000  # safety cap to prevent runaway pagination

    messages = []
    page_token = None
    while True:
        resp = (service.spaces().messages()
                .list(parent=space_name,
                      pageSize=100,
                      filter=api_filter,
                      orderBy="createTime desc",
                      pageToken=page_token)
                .execute())
        messages.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token or len(messages) >= MAX_MESSAGES_PER_SPACE:
            break

    # Keep only the most recent messages if we hit the cap
    messages = messages[:MAX_MESSAGES_PER_SPACE]
    # Reverse so messages are in chronological order (oldest → newest)
    messages.reverse()
    return messages


def get_sender_name(msg):
    sender = msg.get("sender", {})
    return sender.get("displayName", sender.get("name", "Unknown"))


def format_time(time_str):
    try:
        dt = datetime.datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %I:%M %p")
    except Exception:
        return time_str


def extract_message_text(msg):
    """Extract all text content from a message, including quoted replies and annotations."""
    parts = []
    # Primary text content
    text = msg.get("text", "") or ""
    if not text:
        text = msg.get("formattedText", "") or ""
    if text.strip():
        parts.append(text.strip())
    # Quoted message (thread replies)
    quoted = msg.get("quotedMessageMetadata", {})
    if quoted and quoted.get("lastUpdateTime"):
        # The quoted text is usually in the main text already, but flag it
        pass
    # Attachment names (often contain context like "Screenshot", ticket IDs, etc.)
    for att in msg.get("attachment", []):
        att_name = att.get("contentName", "")
        if att_name:
            parts.append(f"[Attachment: {att_name}]")
    # Cards / card content
    for card in msg.get("cardsV2", msg.get("cards", [])):
        card_body = json.dumps(card) if isinstance(card, dict) else str(card)
        # Extract just text values from card JSON
        texts = re.findall(r'"text"\s*:\s*"([^"]+)"', card_body)
        if texts:
            parts.append(" | ".join(texts))
    return " ".join(parts)


def build_conversation_context(all_messages_by_space):
    """Build a combined context string from all spaces for the chatbot.

    Caps total output at ~500K characters (~125K tokens) to stay safely
    within Claude's 200K-token context window after accounting for the
    system prompt, user query, and response space.
    """
    MAX_CHARS = 500_000  # ~125K tokens (4 chars ≈ 1 token)
    total_chars = 0
    parts = []
    for space_name, messages in all_messages_by_space.items():
        msg_lines = []
        for m in messages:
            sender = get_sender_name(m)
            text = extract_message_text(m)
            time = format_time(m.get("createTime", ""))
            if text.strip():
                line = f"[{time}] {sender}: {text}"
                if total_chars + len(line) > MAX_CHARS:
                    msg_lines.append("[... truncated to fit context window ...]")
                    break
                msg_lines.append(line)
                total_chars += len(line) + 1  # +1 for newline
        if msg_lines:
            header = f"\n=== SPACE: {space_name} ===\n"
            total_chars += len(header)
            parts.append(header + "\n".join(msg_lines))
        if total_chars >= MAX_CHARS:
            break
    return "\n".join(parts)


# ── Claude helpers ──────────────────────────────────────────────────────────
def analyze_messages(messages, space_display_name, api_key):
    if not messages:
        return "No messages to analyze."

    MAX_CHARS = 500_000
    total_chars = 0
    msg_text = []
    for m in messages:
        sender = get_sender_name(m)
        text = extract_message_text(m)
        time = format_time(m.get("createTime", ""))
        if text.strip():
            line = f"[{time}] {sender}: {text}"
            if total_chars + len(line) > MAX_CHARS:
                msg_text.append("[... truncated to fit context window ...]")
                break
            msg_text.append(line)
            total_chars += len(line) + 1

    conversation = "\n".join(msg_text)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2048,
        temperature=0.2,
        messages=[{
            "role": "user",
            "content": f"""Analyze the following messages from the Google Chat space "{space_display_name}".

Provide a comprehensive analysis with these sections:

## Key Topics & Themes
Identify the main topics being discussed.

## Critical Issues / Escalations
Any urgent problems, outages, customer escalations, or blockers mentioned.

## Action Items
List specific action items with who is responsible (if mentioned).

## Unresolved Items
Things that were raised but not yet resolved or answered.

## Decisions Made
Any decisions or agreements reached.

## Sentiment & Engagement
Overall tone - is the team stressed, collaborative, calm? Who are the most active participants?

## Summary
A 3-4 sentence executive summary of what's happening in this space.

---
MESSAGES:
{conversation}"""
        }]
    )
    return response.content[0].text


def extract_date_range(user_question, api_key):
    """Use Claude to extract a date range from the user's question.

    Returns (start_date_str, end_date_str) in YYYY-MM-DD format,
    or None if no specific date range is mentioned (use default).
    """
    today = datetime.date.today()
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=200,
        temperature=0,
        messages=[{
            "role": "user",
            "content": f"""Today is {today.isoformat()} ({today.strftime("%A")}).

Extract the date range from this question. If no specific time period is mentioned, reply ONLY with "DEFAULT".
If a date range is mentioned (e.g. "last week", "past month", "in January", "last 3 days", "yesterday", "since March 1"), reply ONLY with two dates in this exact format:
START=YYYY-MM-DD
END=YYYY-MM-DD

Cap the range at 60 days maximum. If the user says "all time" or something very broad, use the last 60 days.

Question: {user_question}"""
        }]
    )
    text = response.content[0].text.strip()
    if text == "DEFAULT":
        return None
    try:
        lines = text.strip().split("\n")
        start_str = lines[0].split("=")[1].strip()
        end_str = lines[1].split("=")[1].strip()
        # Validate dates
        datetime.date.fromisoformat(start_str)
        datetime.date.fromisoformat(end_str)
        return start_str, end_str
    except Exception:
        return None


def chat_with_claude(user_question, conversation_context, date_label, chat_history, api_key):
    """Send a question to Claude with full space context and chat history."""
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = f"""You are the ECC Space Monitor AI assistant. You have access to messages from multiple Google Chat spaces at Exotel.
Answer questions accurately based on the messages below. If the information isn't in the messages, say so.
Be specific - mention names, dates, and quote relevant messages when helpful.
If asked about trends, patterns, or comparisons across spaces, analyze all available data.

TODAY'S DATE: {datetime.datetime.now().strftime("%B %d, %Y")}
MESSAGE WINDOW: {date_label}

AVAILABLE SPACE MESSAGES:
{conversation_context}"""

    # Build messages list from chat history
    messages = []
    for msg in chat_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_question})

    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4096,
        temperature=0.3,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text


# ── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    # User info
    user_name = st.session_state.get("user_name", "")
    user_email = st.session_state.get("authenticated_email", "")
    user_pic = st.session_state.get("user_picture", "")
    if user_pic:
        st.markdown(f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
                    f'<img src="{user_pic}" style="width:32px;height:32px;border-radius:50%;">'
                    f'<div><div style="font-weight:600;font-size:14px;color:white;">{user_name}</div>'
                    f'<div style="font-size:11px;color:#aaa;">{user_email}</div></div></div>',
                    unsafe_allow_html=True)
    elif user_email:
        st.markdown(f"**{user_email}**")
    if st.button("Logout", use_container_width=True, key="logout_btn"):
        for key in ["authenticated_email", "user_name", "user_picture", "last_login_code"]:
            st.session_state.pop(key, None)
        st.rerun()

    st.divider()
    st.markdown("## ECC Space Monitor")
    st.markdown(f'<span class="status-dot"></span> Monitoring {len(TARGET_SPACES)} spaces',
                unsafe_allow_html=True)
    st.divider()

    # Load API key from secrets
    _secrets_key = st.secrets.get("ANTHROPIC_API_KEY", "") if hasattr(st, "secrets") else ""
    _env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    default_key = _secrets_key or _env_key
    if default_key:
        api_key = default_key
        st.success("API Key loaded")
    else:
        api_key = st.text_input("Anthropic API Key", type="password")

    st.divider()
    st.markdown("**Target Spaces:**")
    for s in TARGET_SPACES:
        st.markdown(f"- {s}")

    st.divider()
    if st.button("Clear Chat History", use_container_width=True):
        st.session_state["chat_messages"] = []
        st.rerun()

    if st.button("Refresh Space Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ── Main ────────────────────────────────────────────────────────────────────
st.markdown("# ECC Space Monitor")
st.caption("Real-time monitoring, AI analysis, and Q&A for Google Chat spaces")

# Check auth
creds = get_credentials()

if not creds:
    st.warning("Not authenticated with Google Chat API.")
    if not os.path.exists(CLIENT_SECRET_PATH):
        st.error(f"Missing `client_secret.json` at:\n`{CLIENT_SECRET_PATH}`")
    st.info("Run this command in your terminal to authenticate:\n\n"
            f"```\ncd {BASE_DIR} && python3 auth.py\n```")
    if st.button("I've authenticated - Refresh", type="primary"):
        st.rerun()
    st.stop()

creds_json = creds.to_json()

# ── Fetch spaces ────────────────────────────────────────────────────────────
with st.spinner("Fetching spaces..."):
    try:
        spaces = fetch_spaces(creds_json)
    except Exception as e:
        st.error(f"Error fetching spaces: {e}")
        if "invalid_grant" in str(e).lower() or "expired" in str(e).lower():
            if os.path.exists(TOKEN_PATH):
                os.remove(TOKEN_PATH)
            if "google_creds" in st.session_state:
                del st.session_state["google_creds"]
            st.warning("Token expired. Please re-authenticate.")
            st.rerun()
        st.stop()

if not spaces:
    st.warning("No matching spaces found. Make sure you're a member of the target spaces.")
    with st.expander("Debug: All spaces found"):
        try:
            creds_obj = Credentials.from_authorized_user_info(json.loads(creds_json), SCOPES)
            service = build("chat", "v1", credentials=creds_obj)
            all_spaces = []
            page_token = None
            while True:
                resp = service.spaces().list(pageSize=100, pageToken=page_token).execute()
                all_spaces.extend(resp.get("spaces", []))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            for s in all_spaces:
                st.write(f"- **{s.get('displayName', 'N/A')}** ({s.get('name')}) type={s.get('type')}")
        except Exception as e:
            st.error(str(e))
    st.stop()

# ── Default fetch (last 7 days — used for dashboard & initial chat context) ─
DEFAULT_LOOKBACK_DAYS = 7
_today = datetime.date.today()
_default_start = _today - datetime.timedelta(days=DEFAULT_LOOKBACK_DAYS)

all_messages_by_space = {}
total_msg_count = 0
for space in spaces:
    display_name = space.get("displayName", space["name"])
    space_id = space["name"]
    try:
        msgs = fetch_messages(creds_json, space_id,
                              _default_start.isoformat(), _today.isoformat())
        all_messages_by_space[display_name] = msgs
        total_msg_count += len(msgs)
    except Exception as e:
        st.warning(f"Could not fetch messages from {display_name}: {e}")

# Build context for chatbot (default window)
conversation_context = build_conversation_context(all_messages_by_space)

# ── Metrics row ─────────────────────────────────────────────────────────────
st.markdown("---")
mcol1, mcol2, mcol3, mcol4 = st.columns(4)
with mcol1:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{len(spaces)}</div>'
                f'<div class="metric-label">Spaces Connected</div></div>', unsafe_allow_html=True)
with mcol2:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{total_msg_count}</div>'
                f'<div class="metric-label">Messages (7d)</div></div>', unsafe_allow_html=True)
with mcol3:
    now = datetime.datetime.now().strftime("%I:%M %p")
    st.markdown(f'<div class="metric-card"><div class="metric-value">{now}</div>'
                f'<div class="metric-label">Last Refresh</div></div>', unsafe_allow_html=True)
with mcol4:
    st.markdown(f'<div class="metric-card"><div class="metric-value">Claude</div>'
                f'<div class="metric-label">Analysis Engine</div></div>', unsafe_allow_html=True)

# ── Two main sections: Chat + Dashboard ─────────────────────────────────────
st.markdown("---")
tab_chat, tab_dashboard = st.tabs(["Ask Anything", "Space Dashboard"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: CHAT INTERFACE
# ══════════════════════════════════════════════════════════════════════════════
with tab_chat:
    st.markdown("### Ask anything about your spaces")
    st.caption("Ask questions, request analysis, compare spaces, find action items, track issues...")

    # Example queries
    with st.expander("Example questions you can ask"):
        st.markdown("""
- What are the top issues raised across all spaces today?
- Who has the most action items pending?
- Summarize what happened in Panic Room this week
- Are there any customer escalations that haven't been resolved?
- Compare the activity level between DRI's Huddle and ECC Panic Room
- What decisions were made in the last 24 hours?
- Is anyone blocked or waiting on something?
- What's the overall team sentiment across spaces?
- List all mentions of [customer name / topic]
- Give me an executive briefing for all spaces
        """)

    # Initialize chat history
    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = []

    # Display chat history
    for msg in st.session_state["chat_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    if prompt := st.chat_input("Ask about your Google Chat spaces..."):
        if not api_key:
            st.warning("Please enter your Anthropic API Key in the sidebar.")
        else:
            # Add user message
            st.session_state["chat_messages"].append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # Get Claude response
            with st.chat_message("assistant"):
                try:
                    # Step 1: Extract date range from the query
                    with st.spinner("Understanding your question..."):
                        date_range = extract_date_range(prompt, api_key)

                    if date_range:
                        q_start, q_end = date_range
                        date_label = f"{q_start} to {q_end}"
                        with st.spinner(f"Fetching messages from {date_label}..."):
                            # Fetch messages for the extracted date range
                            q_messages_by_space = {}
                            for space in spaces:
                                dn = space.get("displayName", space["name"])
                                sid = space["name"]
                                try:
                                    q_msgs = fetch_messages(creds_json, sid, q_start, q_end)
                                    q_messages_by_space[dn] = q_msgs
                                except Exception:
                                    pass
                            chat_context = build_conversation_context(q_messages_by_space)
                            total_q = sum(len(v) for v in q_messages_by_space.values())
                        st.caption(f"📅 Searched {date_label} — {total_q} messages found")
                    else:
                        # Use default 7-day context
                        chat_context = conversation_context
                        date_label = f"Last {DEFAULT_LOOKBACK_DAYS} days"

                    # Step 2: Answer the question
                    with st.spinner("Analyzing..."):
                        response = chat_with_claude(
                            prompt,
                            chat_context,
                            date_label,
                            st.session_state["chat_messages"][:-1],
                            api_key
                        )
                        st.markdown(response)
                        st.session_state["chat_messages"].append(
                            {"role": "assistant", "content": response}
                        )
                except Exception as e:
                    st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: SPACE DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
with tab_dashboard:
    space_names = [s.get("displayName", s["name"]) for s in spaces]
    space_tabs = st.tabs(space_names + ["All Spaces"])

    for stab, space in zip(space_tabs[:-1], spaces):
        with stab:
            display_name = space.get("displayName", space["name"])
            space_id = space["name"]
            messages = all_messages_by_space.get(display_name, [])

            if not messages:
                st.info(f"No messages found in {display_name}.")
                continue

            st.markdown(f'<div class="space-card">'
                        f'<h3>{display_name}</h3>'
                        f'<p>{len(messages)} messages fetched | Space ID: <code>{space_id}</code></p>'
                        f'</div>', unsafe_allow_html=True)

            left, right = st.columns([3, 2])

            with left:
                st.markdown("### AI Analysis")
                if api_key:
                    analysis_key = f"analysis_{space_id}"
                    if st.button(f"Analyse {display_name}", key=f"btn_{space_id}",
                                 type="primary", use_container_width=True):
                        with st.spinner("Claude is analyzing..."):
                            try:
                                result = analyze_messages(messages, display_name, api_key)
                                st.session_state[analysis_key] = result
                            except Exception as e:
                                st.error(f"Analysis error: {e}")

                    if analysis_key in st.session_state:
                        st.markdown(f'<div class="analysis-section">', unsafe_allow_html=True)
                        st.markdown(st.session_state[analysis_key])
                        st.markdown('</div>', unsafe_allow_html=True)
                else:
                    st.warning("Enter your Anthropic API Key in the sidebar.")

            with right:
                st.markdown("### Recent Messages")
                for m in messages[:30]:
                    sender = get_sender_name(m)
                    text = m.get("text", m.get("formattedText", ""))
                    time = format_time(m.get("createTime", ""))
                    if text.strip():
                        st.markdown(
                            f'<div class="msg-row">'
                            f'<span class="msg-sender">{sender}</span> '
                            f'<span class="msg-time">{time}</span>'
                            f'<div class="msg-text">{text[:300]}</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )

    # ── "All Spaces" tab ────────────────────────────────────────────────────
    with space_tabs[-1]:
        st.markdown("### Analyse All Spaces")
        if api_key:
            if st.button("Run Full Analysis", type="primary", use_container_width=True):
                progress = st.progress(0)
                for i, space in enumerate(spaces):
                    display_name = space.get("displayName", space["name"])
                    space_id = space["name"]
                    with st.spinner(f"Analyzing {display_name}..."):
                        try:
                            msgs = all_messages_by_space.get(display_name, [])
                            result = analyze_messages(msgs, display_name, api_key)
                            st.session_state[f"analysis_{space_id}"] = result
                        except Exception as e:
                            st.error(f"Error analyzing {display_name}: {e}")
                    progress.progress((i + 1) / len(spaces))
                st.success("All spaces analyzed!")
                st.rerun()

            # Show all analyses
            for space in spaces:
                display_name = space.get("displayName", space["name"])
                space_id = space["name"]
                analysis_key = f"analysis_{space_id}"
                if analysis_key in st.session_state:
                    with st.expander(f"{display_name}", expanded=True):
                        st.markdown(st.session_state[analysis_key])
