"""
Local dev tool — run once to authenticate with Google Chat API.

Opens a browser for Google sign-in, exchanges the code for a token,
and saves it to token.json.  Then run ``streamlit run app.py``.

This script is for LOCAL DEVELOPMENT ONLY and should not be used
in production / Streamlit Cloud.
"""

import json
import os
import secrets as _secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
import webbrowser

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET_PATH = os.path.join(BASE_DIR, "client_secret.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")

SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
]

REDIRECT_PORT = 8090
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code = None
    expected_state = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)

        # Validate CSRF state
        returned_state = query.get("state", [None])[0]
        if returned_state != self.expected_state:
            self.send_response(403)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Invalid state - possible CSRF. Try again.</h1>")
            return

        if "code" in query:
            OAuthCallbackHandler.auth_code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html><body style="font-family:Inter,sans-serif;display:flex;align-items:center;
            justify-content:center;height:100vh;background:#f8f9fb;">
            <div style="text-align:center;background:white;padding:40px;border-radius:16px;
            box-shadow:0 2px 8px rgba(0,0,0,0.1);">
            <h1 style="color:#22c55e;">Authenticated!</h1>
            <p>You can close this tab and go back to the Streamlit app.</p>
            </div></body></html>
            """)
        else:
            error = query.get("error", ["Unknown error"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<html><body><h1>Error: {error}</h1></body></html>".encode())

    def log_message(self, format, *args):
        pass


def main():
    if not os.path.exists(CLIENT_SECRET_PATH):
        print(f"ERROR: {CLIENT_SECRET_PATH} not found!")
        return

    with open(CLIENT_SECRET_PATH) as f:
        client_info = json.load(f)["web"]

    client_id = client_info["client_id"]
    client_secret = client_info["client_secret"]

    # Generate CSRF state
    state = _secrets.token_urlsafe(32)
    OAuthCallbackHandler.expected_state = state

    # Build auth URL with proper encoding
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    server = HTTPServer(("localhost", REDIRECT_PORT), OAuthCallbackHandler)
    print(f"\nOpening browser for Google sign-in...")
    print(f"If the browser doesn't open, go to:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("Waiting for Google callback...")
    while OAuthCallbackHandler.auth_code is None:
        server.handle_request()

    code = OAuthCallbackHandler.auth_code
    server.server_close()
    print("Got authorization code. Exchanging for token...")

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    token_data = token_resp.json()

    if "error" in token_data:
        print(f"\nERROR: {token_data['error']} - {token_data.get('error_description', '')}")
        return

    token_to_save = {
        "token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": SCOPES,
    }

    with open(TOKEN_PATH, "w") as f:
        json.dump(token_to_save, f, indent=2)

    print(f"\nToken saved to {TOKEN_PATH}")
    print("You can now run: streamlit run app.py")


if __name__ == "__main__":
    main()
