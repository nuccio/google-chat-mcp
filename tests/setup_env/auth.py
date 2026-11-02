"""
OAuth credentials of the setup, separate from the server's.

The setup can create spaces, which the server must never be able to do, so its
scopes are defined only here and its token lives outside the path the server
reads. It never reads or writes the server's token.json.
"""

import json
import os
import tempfile
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from google_chat_mcp.auth import _DEFAULT_SECRETS_PATH

TOKEN_PATH = Path.home() / ".config" / "google-chat-mcp-test-setup" / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.create",
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages",
]


def _write_token(creds: Credentials, token_path: Path) -> None:
    """Writes the token readable by the owner only (0600, folder 0700), atomically."""
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(token_path.parent, 0o700)
    # mkstemp creates the file with mode 0600.
    fd, tmp_path = tempfile.mkstemp(dir=token_path.parent, prefix=".token-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(creds.to_json())
        os.replace(tmp_path, token_path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _run_auth_flow() -> Credentials:
    secrets_path = Path(os.environ.get("GOOGLE_CLIENT_SECRETS", _DEFAULT_SECRETS_PATH))
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"client_secrets.json not found at {secrets_path}. "
            "Place it there or set GOOGLE_CLIENT_SECRETS."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    return flow.run_local_server(port=0, prompt="consent")


def load_credentials(token_path: Path = TOKEN_PATH) -> Credentials:
    """Returns the setup's credentials, asking for consent when there are none."""
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if not creds.expired:
            return creds
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = _run_auth_flow()
    else:
        creds = _run_auth_flow()
    _write_token(creds, token_path)
    return creds
