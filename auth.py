"""
Run this once to authenticate with Google Chat API.
It opens a browser, you sign in, and it saves token.json.
Then run the Streamlit app separately.
"""
import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
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
    """Handle the OAuth callback."""
    auth_code = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
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
        pass  # Suppress logs


def main():
    # Load client config
    if not os.path.exists(CLIENT_SECRET_PATH):
        print(f"ERROR: {CLIENT_SECRET_PATH} not found!")
        return

    with open(CLIENT_SECRET_PATH) as f:
        client_info = json.load(f)["web"]

    client_id = client_info["client_id"]
    client_secret = client_info["client_secret"]

    # Build auth URL
    scope_str = "+".join(s.replace(":", "%3A").replace("/", "%2F") for s in SCOPES)
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={client_id}"
        f"&redirect_uri=http%3A%2F%2Flocalhost%3A{REDIRECT_PORT}"
        f"&response_type=code"
        f"&scope={'%20'.join(SCOPES)}"
        f"&access_type=offline"
        f"&prompt=consent"
    )

    # Start local server
    server = HTTPServer(("localhost", REDIRECT_PORT), OAuthCallbackHandler)
    print(f"\nOpening browser for Google sign-in...")
    print(f"If the browser doesn't open, go to:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback
    print("Waiting for Google callback...")
    while OAuthCallbackHandler.auth_code is None:
        server.handle_request()

    code = OAuthCallbackHandler.auth_code
    server.server_close()
    print(f"Got authorization code. Exchanging for token...")

    # Exchange code for token
    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    )
    token_data = token_resp.json()

    if "error" in token_data:
        print(f"\nERROR: {token_data['error']} - {token_data.get('error_description', '')}")
        return

    # Save token
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
