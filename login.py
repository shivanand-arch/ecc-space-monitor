"""
Google OAuth login gate for Streamlit.

Handles:
- OAuth URL construction (properly URL-encoded)
- CSRF state validation
- Domain restriction to @exotel.com
- Session management
"""

import json
import os
import secrets
from urllib.parse import urlencode

import streamlit as st
import requests

from config import ALLOWED_DOMAIN, LOGIN_SCOPES, CLIENT_SECRET_PATH


# ── Client credentials ───────────────────────────────────────────────────────

def _get_login_client() -> tuple[str, str]:
    """Load OAuth client ID + secret from secrets or local file.

    Fails loud with a clear message if neither source is available.
    """
    try:
        client_id = st.secrets["GOOGLE_CLIENT_ID"]
        client_secret = st.secrets["GOOGLE_CLIENT_SECRET"]
        if client_id and client_secret:
            return client_id, client_secret
    except KeyError:
        pass

    if os.path.exists(CLIENT_SECRET_PATH):
        with open(CLIENT_SECRET_PATH) as f:
            info = json.load(f).get("web", {})
            cid = info.get("client_id", "")
            csec = info.get("client_secret", "")
            if cid and csec:
                return cid, csec

    st.error(
        "Google OAuth client credentials not found. "
        "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in Streamlit secrets, "
        f"or place client_secret.json at `{CLIENT_SECRET_PATH}`."
    )
    st.stop()


LOGIN_CLIENT_ID, LOGIN_CLIENT_SECRET = _get_login_client()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_redirect_uri() -> str:
    try:
        app_url = st.secrets.get("APP_URL", "")
        if app_url:
            return app_url.rstrip("/")
    except Exception:
        pass
    return "http://localhost:8501"


def _build_auth_url() -> str:
    """Build the Google OAuth2 authorization URL with proper encoding."""
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
        "hd": ALLOWED_DOMAIN,
        "prompt": "select_account",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def _exchange_code(code: str) -> tuple[dict | None, str | None]:
    """Exchange authorization code for user info."""
    redirect_uri = _get_redirect_uri()
    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": LOGIN_CLIENT_ID,
            "client_secret": LOGIN_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    token_data = token_resp.json()
    if "error" in token_data:
        return None, f"Token error: {token_data.get('error_description', token_data['error'])}"

    userinfo_resp = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    )
    return userinfo_resp.json(), None


# ── Main gate ────────────────────────────────────────────────────────────────

def check_google_auth() -> bool:
    """Handle the full Google OAuth login flow. Returns True if authenticated."""

    # Already authenticated
    if st.session_state.get("authenticated_email"):
        return True

    # Check for OAuth callback
    params = st.query_params
    code = params.get("code")
    if code:
        # ── CSRF state validation ────────────────────────────────────────
        returned_state = params.get("state")
        expected_state = st.session_state.get("oauth_login_state")
        if not returned_state or returned_state != expected_state:
            st.error("Invalid OAuth state — possible CSRF attempt. Please try again.")
            st.query_params.clear()
            return False

        # Prevent code reuse on Streamlit reruns
        if st.session_state.get("last_login_code") == code:
            st.query_params.clear()
            return bool(st.session_state.get("authenticated_email"))

        st.session_state["last_login_code"] = code
        userinfo, error = _exchange_code(code)

        if error:
            st.error(error)
            st.query_params.clear()
            return False

        email = (userinfo or {}).get("email", "").lower()
        # Verify email is from allowed domain AND verified by Google
        if not email.endswith(f"@{ALLOWED_DOMAIN}"):
            st.error(f"Access restricted to @{ALLOWED_DOMAIN} accounts. You signed in as {email}.")
            st.query_params.clear()
            return False

        if not (userinfo or {}).get("verified_email", False):
            st.error("Email address is not verified by Google.")
            st.query_params.clear()
            return False

        st.session_state["authenticated_email"] = email
        st.session_state["user_name"] = userinfo.get("name", email)
        st.session_state["user_picture"] = userinfo.get("picture", "")
        st.query_params.clear()
        st.rerun()

    # ── Show login page ──────────────────────────────────────────────────
    st.markdown(
        """
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
    """,
        unsafe_allow_html=True,
    )

    auth_url = _build_auth_url()

    st.markdown(
        f"""
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
    """,
        unsafe_allow_html=True,
    )

    return False
