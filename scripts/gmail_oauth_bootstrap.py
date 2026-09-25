"""Local, one-time authorization for mpc@tce.pb.gov.br.

Reads a desktop OAuth client from tmp/gmail_oauth_client.json or from the
environment variables GMAIL_OAUTH_CLIENT_ID and GMAIL_OAUTH_CLIENT_SECRET.
Requests only gmail.send, prints the refresh token once, and does not write it.
"""

from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
CLIENT_FILE = ROOT / "tmp" / "gmail_oauth_client.json"
SCOPE = "https://www.googleapis.com/auth/gmail.send"
SENDER = "mpc@tce.pb.gov.br"


def _client():
    file_id = file_secret = ""
    if CLIENT_FILE.is_file():
        payload = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
        block = payload.get("installed") or payload.get("web") or payload
        file_id = str(block.get("client_id") or "").strip()
        file_secret = str(block.get("client_secret") or "").strip()
    client_id = os.environ.get("GMAIL_OAUTH_CLIENT_ID") or file_id
    client_secret = os.environ.get("GMAIL_OAUTH_CLIENT_SECRET") or file_secret
    if not client_id or not client_secret:
        print(
            "Informe o cliente OAuth em tmp/gmail_oauth_client.json "
            "ou nas variáveis GMAIL_OAUTH_CLIENT_ID e GMAIL_OAUTH_CLIENT_SECRET.",
            file=sys.stderr,
        )
        return None
    return client_id, client_secret


def main():
    loaded = _client()
    if loaded is None:
        return 1
    client_id, client_secret = loaded
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Instale google-auth-oauthlib antes de autorizar.", file=sys.stderr)
        return 1
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=[SCOPE],
    )
    credentials = flow.run_local_server(
        access_type="offline",
        prompt="consent",
        open_browser=True,
    )
    email = ""
    identity = getattr(credentials, "id_token", None)
    if isinstance(identity, dict):
        email = str(identity.get("email") or "").strip().lower()
    if email and email != SENDER:
        print(
            "A conta autorizada não é " + SENDER + ". Nenhum token será exibido.",
            file=sys.stderr,
        )
        return 1
    if not email:
        print(
            "Este escopo não informa o e-mail da conta. "
            "Confirme no navegador que a autorização foi feita por " + SENDER + "."
        )
    token = credentials.refresh_token
    if not token:
        print(
            "O Google não devolveu um refresh token. Revogue o acesso do aplicativo "
            "e execute novamente.",
            file=sys.stderr,
        )
        return 1
    print()
    print("Cole somente no Streamlit Secret [gmail_send], nunca no Git:")
    print("enabled = true")
    print('mode = "user_oauth"')
    print('sender = "' + SENDER + '"')
    print("client_id = \"...\"")
    print("client_secret = \"...\"")
    print("refresh_token = \"" + token + "\"")
    print()
    print("O refresh token foi mostrado apenas nesta tela. Ele não foi gravado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
