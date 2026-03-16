import streamlit as st
import json
import os
import datetime
import pandas as pd
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import anthropic

# ── Config ──────────────────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/chat.spaces.readonly",
          "https://www.googleapis.com/auth/chat.messages.readonly",
          "https://www.googleapis.com/auth/chat.memberships.readonly"]

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

st.set_page_config(page_title="ECC Space Monitor", page_icon="📊", layout="wide")


# ── Email Gate ──────────────────────────────────────────────────────────────
def check_email_access():
    """Require @exotel.com email to access the app."""
    if "authenticated_email" in st.session_state and st.session_state["authenticated_email"]:
        return True

    st.markdown("""
    <style>
        .login-box {
            max-width: 450px;
            margin: 80px auto;
            background: white;
            border-radius: 16px;
            padding: 40px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
            text-align: center;
        }
        .login-title { font-size: 24px; font-weight: 700; color: #1a1a2e; margin-bottom: 8px; }
        .login-subtitle { font-size: 14px; color: #666; margin-bottom: 24px; }
    </style>
    <div class="login-box">
        <div class="login-title">ECC Space Monitor</div>
        <div class="login-subtitle">Sign in with your Exotel email to continue</div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        email = st.text_input("Enter your Exotel email", placeholder="yourname@exotel.com")
        if st.button("Sign In", type="primary", use_container_width=True):
            if not email:
                st.error("Please enter your email.")
            elif not email.strip().lower().endswith(f"@{ALLOWED_DOMAIN}"):
                st.error("Access restricted to @exotel.com email addresses only.")
            else:
                st.session_state["authenticated_email"] = email.strip().lower()
                st.rerun()
    return False


if not check_email_access():
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
def fetch_messages(_creds_json, space_name, max_messages=200):
    creds = Credentials.from_authorized_user_info(json.loads(_creds_json), SCOPES)
    service = build("chat", "v1", credentials=creds)
    messages = []
    page_token = None
    while len(messages) < max_messages:
        resp = (service.spaces().messages()
                .list(parent=space_name, pageSize=min(100, max_messages - len(messages)),
                      pageToken=page_token)
                .execute())
        messages.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
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


def build_conversation_context(all_messages_by_space):
    """Build a combined context string from all spaces for the chatbot."""
    parts = []
    for space_name, messages in all_messages_by_space.items():
        msg_lines = []
        for m in messages[:150]:
            sender = get_sender_name(m)
            text = m.get("text", m.get("formattedText", ""))
            time = format_time(m.get("createTime", ""))
            if text.strip():
                msg_lines.append(f"[{time}] {sender}: {text}")
        if msg_lines:
            parts.append(f"\n=== SPACE: {space_name} ===\n" + "\n".join(msg_lines))
    return "\n".join(parts)


# ── Claude helpers ──────────────────────────────────────────────────────────
def analyze_messages(messages, space_display_name, api_key):
    if not messages:
        return "No messages to analyze."

    msg_text = []
    for m in messages[:150]:
        sender = get_sender_name(m)
        text = m.get("text", m.get("formattedText", ""))
        time = format_time(m.get("createTime", ""))
        if text.strip():
            msg_text.append(f"[{time}] {sender}: {text}")

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


def chat_with_claude(user_question, conversation_context, chat_history, api_key):
    """Send a question to Claude with full space context and chat history."""
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = f"""You are the ECC Space Monitor AI assistant. You have access to messages from multiple Google Chat spaces at Exotel.
Answer questions accurately based on the messages below. If the information isn't in the messages, say so.
Be specific - mention names, dates, and quote relevant messages when helpful.
If asked about trends, patterns, or comparisons across spaces, analyze all available data.

TODAY'S DATE: {datetime.datetime.now().strftime("%B %d, %Y")}

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

    msg_count = st.slider("Messages to fetch per space", 50, 500, 200, step=50)

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

# ── Fetch all messages ──────────────────────────────────────────────────────
all_messages_by_space = {}
total_msg_count = 0
for space in spaces:
    display_name = space.get("displayName", space["name"])
    space_id = space["name"]
    try:
        msgs = fetch_messages(creds_json, space_id, max_messages=msg_count)
        all_messages_by_space[display_name] = msgs
        total_msg_count += len(msgs)
    except Exception as e:
        st.warning(f"Could not fetch messages from {display_name}: {e}")

# Build context for chatbot
conversation_context = build_conversation_context(all_messages_by_space)

# ── Metrics row ─────────────────────────────────────────────────────────────
st.markdown("---")
mcol1, mcol2, mcol3, mcol4 = st.columns(4)
with mcol1:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{len(spaces)}</div>'
                f'<div class="metric-label">Spaces Connected</div></div>', unsafe_allow_html=True)
with mcol2:
    st.markdown(f'<div class="metric-card"><div class="metric-value">{total_msg_count}</div>'
                f'<div class="metric-label">Total Messages</div></div>', unsafe_allow_html=True)
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
                with st.spinner("Analyzing spaces..."):
                    try:
                        response = chat_with_claude(
                            prompt,
                            conversation_context,
                            st.session_state["chat_messages"][:-1],  # exclude current msg
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
