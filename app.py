"""
ECC Space Monitor — main Streamlit app.

Thin UI layer; business logic lives in:
  config.py        — constants
  login.py         — Google OAuth login gate
  chat_api.py      — Chat API client + tiered message repository
  llm_client.py    — Claude analysis & Q&A
  message_utils.py — text extraction, context building, HTML escaping
  date_parser.py   — deterministic date-range extraction
"""

import datetime
import os

import streamlit as st

from config import (
    BASE_DIR,
    CLIENT_SECRET_PATH,
    STARTUP_LOOKBACK_DAYS,
    TARGET_SPACE_NAMES,
    TOKEN_PATH,
    MAX_CHAT_HISTORY_MESSAGES,
)
from login import check_google_auth
from chat_api import (
    get_credentials,
    fetch_spaces,
    startup_load,
    incremental_refresh,
    repo_needs_refresh,
    repo_last_refreshed,
    get_all_messages,
    get_messages_for_range,
    get_repo_stats,
)
from message_utils import (
    get_sender_name,
    extract_text,
    format_time,
    safe,
    build_conversation_context,
    analysis_cache_key,
)
from llm_client import analyze_messages, chat_with_claude
from date_parser import parse_date_range


# ── Page config (must be first Streamlit call) ───────────────────────────────
st.set_page_config(page_title="ECC Space Monitor", page_icon="📊", layout="wide")


# ── Login gate ───────────────────────────────────────────────────────────────
if not check_google_auth():
    st.stop()


# ── CSS ──────────────────────────────────────────────────────────────────────
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
        background: white; border-radius: 12px; padding: 20px;
        margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        border-left: 4px solid #0f3460;
    }
    .metric-card {
        background: white; border-radius: 12px; padding: 16px;
        text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .metric-value { font-size: 28px; font-weight: 700; color: #0f3460; }
    .metric-label { font-size: 13px; color: #666; margin-top: 4px; }
    .analysis-section {
        background: white; border-radius: 12px; padding: 20px;
        margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .msg-row { padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
    .msg-sender { font-weight: 600; color: #1a1a2e; font-size: 13px; }
    .msg-time { color: #999; font-size: 11px; }
    .msg-text { color: #333; font-size: 14px; margin-top: 4px; }
    .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
                  background: #22c55e; margin-right: 6px; }
</style>
""", unsafe_allow_html=True)


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    user_name = safe(st.session_state.get("user_name", ""))
    user_email = safe(st.session_state.get("authenticated_email", ""))
    user_pic = st.session_state.get("user_picture", "")
    if user_pic:
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
            f'<img src="{safe(user_pic)}" style="width:32px;height:32px;border-radius:50%;">'
            f'<div><div style="font-weight:600;font-size:14px;color:white;">{user_name}</div>'
            f'<div style="font-size:11px;color:#aaa;">{user_email}</div></div></div>',
            unsafe_allow_html=True,
        )
    elif user_email:
        st.markdown(f"**{user_email}**")
    if st.button("Logout", use_container_width=True, key="logout_btn"):
        for key in ["authenticated_email", "user_name", "user_picture", "last_login_code",
                     "oauth_login_state"]:
            st.session_state.pop(key, None)
        st.rerun()

    st.divider()
    st.markdown("## ECC Space Monitor")
    st.markdown(
        f'<span class="status-dot"></span> Monitoring {len(TARGET_SPACE_NAMES)} spaces',
        unsafe_allow_html=True,
    )
    st.divider()

    # API key
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
    for s in TARGET_SPACE_NAMES:
        st.markdown(f"- {s}")

    # Repository status
    _rr = repo_last_refreshed()
    if _rr:
        stats = get_repo_stats()
        st.divider()
        st.markdown("**Message Repository**")
        st.caption(f"{stats['total']:,} messages loaded")
        if stats["earliest"] and stats["latest"]:
            st.caption(f"Coverage: {stats['earliest']} to {stats['latest']}")
        st.caption(f"Last refresh: {_rr.strftime('%I:%M %p')}")
        st.caption("Older data fetched automatically on demand")

    st.divider()
    if st.button("Clear Chat History", use_container_width=True):
        st.session_state["chat_messages"] = []
        st.rerun()

    if st.button("Refresh Space Data", use_container_width=True):
        st.cache_data.clear()
        st.session_state.pop("message_repo", None)
        st.session_state.pop("repo_last_refreshed", None)
        st.rerun()


# ── Main ─────────────────────────────────────────────────────────────────────
st.markdown("# ECC Space Monitor")
st.caption("AI-powered search and analysis across Google Chat spaces")

# Chat API credentials
creds = get_credentials()
if not creds:
    st.warning("Not authenticated with Google Chat API.")
    if not os.path.exists(CLIENT_SECRET_PATH):
        st.error(f"Missing `client_secret.json` at:\n`{CLIENT_SECRET_PATH}`")
    st.info(f"Run this command in your terminal to authenticate:\n\n"
            f"```\ncd {BASE_DIR} && python3 auth.py\n```")
    if st.button("I've authenticated - Refresh", type="primary"):
        st.rerun()
    st.stop()

creds_json = creds.to_json()

# ── Fetch spaces ─────────────────────────────────────────────────────────────
with st.spinner("Fetching spaces..."):
    try:
        spaces = fetch_spaces(creds_json)
    except Exception as e:
        st.error(f"Error fetching spaces: {e}")
        if "invalid_grant" in str(e).lower() or "expired" in str(e).lower():
            if os.path.exists(TOKEN_PATH):
                os.remove(TOKEN_PATH)
            st.session_state.pop("google_creds", None)
            st.warning("Token expired. Please re-authenticate.")
            st.rerun()
        st.stop()

if not spaces:
    st.warning("No matching spaces found. Make sure you're a member of the target spaces.")
    from googleapiclient.discovery import build as _build
    from google.oauth2.credentials import Credentials as _Creds
    import json as _json
    from config import CHAT_SCOPES as _SCOPES
    with st.expander("Debug: All spaces found"):
        try:
            _c = _Creds.from_authorized_user_info(_json.loads(creds_json), _SCOPES)
            _svc = _build("chat", "v1", credentials=_c)
            _all = []
            _pt = None
            while True:
                _r = _svc.spaces().list(pageSize=100, pageToken=_pt).execute()
                _all.extend(_r.get("spaces", []))
                _pt = _r.get("nextPageToken")
                if not _pt:
                    break
            for s in _all:
                st.write(f"- **{s.get('displayName', 'N/A')}** ({s.get('name')}) type={s.get('type')}")
        except Exception as e:
            st.error(str(e))
    st.stop()

# ── Load message repository ──────────────────────────────────────────────────
# Tier 1: Fast startup with last 30 days
if repo_last_refreshed() is None:
    progress_bar = st.progress(0, text=f"Loading last {STARTUP_LOOKBACK_DAYS} days of messages...")
    startup_load(creds_json, spaces, progress_callback=progress_bar.progress)
    progress_bar.empty()
elif repo_needs_refresh():
    with st.spinner("Refreshing new messages..."):
        incremental_refresh(creds_json, spaces)

# Get current data
all_messages_by_space = get_all_messages()
total_msg_count = sum(len(v) for v in all_messages_by_space.values())
stats = get_repo_stats()

# Default conversation context: all currently loaded messages
conversation_context = build_conversation_context(all_messages_by_space)

# ── Metrics row ──────────────────────────────────────────────────────────────
st.markdown("---")
mcol1, mcol2, mcol3, mcol4 = st.columns(4)
with mcol1:
    st.markdown(
        f'<div class="metric-card"><div class="metric-value">{len(spaces)}</div>'
        f'<div class="metric-label">Spaces Connected</div></div>',
        unsafe_allow_html=True,
    )
with mcol2:
    st.markdown(
        f'<div class="metric-card"><div class="metric-value">{total_msg_count:,}</div>'
        f'<div class="metric-label">Messages Loaded</div></div>',
        unsafe_allow_html=True,
    )
with mcol3:
    coverage = stats.get("earliest", "N/A") or "N/A"
    st.markdown(
        f'<div class="metric-card"><div class="metric-value">{coverage}</div>'
        f'<div class="metric-label">Data Since</div></div>',
        unsafe_allow_html=True,
    )
with mcol4:
    st.markdown(
        f'<div class="metric-card"><div class="metric-value">Claude</div>'
        f'<div class="metric-label">Analysis Engine</div></div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("---")
tab_chat, tab_dashboard = st.tabs(["Ask Anything", "Space Dashboard"])


# ── TAB 1: CHAT ──────────────────────────────────────────────────────────────
with tab_chat:
    st.markdown("### Ask anything about your spaces")
    st.caption(
        f"{total_msg_count:,} messages loaded"
        + (f" (since {stats['earliest']})" if stats.get("earliest") else "")
        + ". Older data fetched automatically when needed."
    )

    with st.expander("Example questions you can ask"):
        st.markdown("""
- What are the top issues raised across all spaces today?
- What happened with Central Registrar issues in the last 2 weeks?
- Summarize what happened in Panic Room this week
- Show me all customer escalations from January 2024
- Compare the activity between DRI's Huddle and ECC Panic Room
- What decisions were made in the last 24 hours?
- List all mentions of [customer name / topic]
- Give me an executive briefing for the past month
- What were the major incidents in Q4 2025?
        """)

    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = []

    for msg in st.session_state["chat_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Ask about your Google Chat spaces..."):
        if not api_key:
            st.warning("Please enter your Anthropic API Key in the sidebar.")
        else:
            st.session_state["chat_messages"].append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                try:
                    # Step 1: Deterministic date parsing (no LLM call)
                    date_range = parse_date_range(prompt)

                    # Step 2: Fall back to Claude only if deterministic parser can't handle it
                    if date_range is None:
                        from llm_client import extract_date_range_llm
                        with st.spinner("Understanding your question..."):
                            date_range = extract_date_range_llm(prompt, api_key)

                    if date_range:
                        q_start, q_end = date_range
                        date_label = f"{q_start} to {q_end}"

                        # This automatically fetches older data if needed
                        with st.spinner(f"Searching messages ({date_label})..."):
                            q_messages_by_space = get_messages_for_range(
                                q_start, q_end,
                                creds_json=creds_json,
                                spaces=spaces,
                            )
                            chat_context = build_conversation_context(q_messages_by_space)
                            total_q = sum(len(v) for v in q_messages_by_space.values())
                        st.caption(f"📅 {date_label} — {total_q:,} messages")
                    else:
                        # No date in query — use all loaded messages
                        chat_context = conversation_context
                        date_label = f"All loaded data ({stats.get('earliest', '?')} to {stats.get('latest', '?')})"

                    # Step 3: Answer
                    with st.spinner("Analyzing..."):
                        response = chat_with_claude(
                            prompt,
                            chat_context,
                            date_label,
                            st.session_state["chat_messages"][:-1],
                            api_key,
                        )
                        st.markdown(response)
                        st.session_state["chat_messages"].append(
                            {"role": "assistant", "content": response}
                        )

                    # Trim history
                    if len(st.session_state["chat_messages"]) > MAX_CHAT_HISTORY_MESSAGES:
                        st.session_state["chat_messages"] = (
                            st.session_state["chat_messages"][-MAX_CHAT_HISTORY_MESSAGES:]
                        )

                except Exception as e:
                    st.error(f"Error: {e}")


# ── TAB 2: DASHBOARD ─────────────────────────────────────────────────────────
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

            st.markdown(
                f'<div class="space-card">'
                f'<h3>{safe(display_name)}</h3>'
                f'<p>{len(messages):,} messages loaded | Space ID: <code>{safe(space_id)}</code></p>'
                f'</div>',
                unsafe_allow_html=True,
            )

            left, right = st.columns([3, 2])

            with left:
                st.markdown("### AI Analysis")
                if api_key:
                    cache_key = analysis_cache_key(space_id, messages)
                    analysis_state_key = f"analysis_{cache_key}"

                    if st.button(f"Analyse {display_name}", key=f"btn_{space_id}",
                                 type="primary", use_container_width=True):
                        with st.spinner("Claude is analyzing..."):
                            try:
                                result = analyze_messages(messages, display_name, api_key)
                                st.session_state[analysis_state_key] = result
                            except Exception as e:
                                st.error(f"Analysis error: {e}")

                    if analysis_state_key in st.session_state:
                        st.markdown('<div class="analysis-section">', unsafe_allow_html=True)
                        st.markdown(st.session_state[analysis_state_key])
                        st.markdown('</div>', unsafe_allow_html=True)
                else:
                    st.warning("Enter your Anthropic API Key in the sidebar.")

            with right:
                st.markdown("### Recent Messages")
                for m in messages[-30:]:
                    sender = safe(get_sender_name(m))
                    text = safe(extract_text(m)[:300])
                    time = safe(format_time(m.get("createTime", "")))
                    if text.strip():
                        st.markdown(
                            f'<div class="msg-row">'
                            f'<span class="msg-sender">{sender}</span> '
                            f'<span class="msg-time">{time}</span>'
                            f'<div class="msg-text">{text}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

    # ── All Spaces tab ───────────────────────────────────────────────────
    with space_tabs[-1]:
        st.markdown("### Analyse All Spaces")
        if api_key:
            if st.button("Run Full Analysis", type="primary", use_container_width=True):
                progress = st.progress(0)
                for i, space in enumerate(spaces):
                    dn = space.get("displayName", space["name"])
                    sid = space["name"]
                    with st.spinner(f"Analyzing {dn}..."):
                        try:
                            msgs = all_messages_by_space.get(dn, [])
                            result = analyze_messages(msgs, dn, api_key)
                            ck = analysis_cache_key(sid, msgs)
                            st.session_state[f"analysis_{ck}"] = result
                        except Exception as e:
                            st.error(f"Error analyzing {dn}: {e}")
                    progress.progress((i + 1) / len(spaces))
                st.success("All spaces analyzed!")
                st.rerun()

            for space in spaces:
                dn = space.get("displayName", space["name"])
                sid = space["name"]
                msgs = all_messages_by_space.get(dn, [])
                ck = analysis_cache_key(sid, msgs)
                ak = f"analysis_{ck}"
                if ak in st.session_state:
                    with st.expander(f"{dn}", expanded=True):
                        st.markdown(st.session_state[ak])
