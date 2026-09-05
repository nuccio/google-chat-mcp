"""
Gestione credenziali OAuth per Google Chat.

Flusso una-tantum:
    google-chat-mcp auth
    (o: uvx "git+https://github.com/nuccio/google-chat-mcp" auth)

Il token viene salvato in ~/.config/google-chat-mcp/token.json e rinnovato
automaticamente ad ogni avvio del server se scaduto.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

CONFIG_DIR = Path.home() / ".config" / "google-chat-mcp"
TOKEN_PATH = CONFIG_DIR / "token.json"
_DEFAULT_SECRETS_PATH = CONFIG_DIR / "client_secrets.json"

SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
]

_REMEDY = 'uvx "git+https://github.com/nuccio/google-chat-mcp" auth'


def run_auth_flow() -> None:
    secrets_path = Path(os.environ.get("GOOGLE_CLIENT_SECRETS", _DEFAULT_SECRETS_PATH))
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"client_secrets.json non trovato in {secrets_path}.\n"
            "Scaricalo da Google Cloud Console (APIs & Services → Credentials → "
            "OAuth client ID, tipo Desktop app) e posizionalo in "
            f"{_DEFAULT_SECRETS_PATH} oppure imposta GOOGLE_CLIENT_SECRETS."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    # prompt='consent' garantisce che Google restituisca sempre un refresh token,
    # anche se ritiene il consenso ancora valido per questa sessione.
    creds = flow.run_local_server(port=0, prompt="consent")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    token_data = json.loads(creds.to_json())
    token_data["authorized_at"] = datetime.now(timezone.utc).isoformat()
    TOKEN_PATH.write_text(json.dumps(token_data))
    print(f"Token salvato in {TOKEN_PATH}")


def load_credentials() -> Credentials:
    if not TOKEN_PATH.exists():
        raise RuntimeError(
            f"Token OAuth non trovato. Esegui:\n  {_REMEDY}"
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        except RefreshError:
            raise RuntimeError(
                f"Il refresh token è scaduto o non è più valido (invalid_grant).\n"
                f"Esegui:\n  {_REMEDY}"
            )
    return creds
