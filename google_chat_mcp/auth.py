"""
OAuth credential management for Google Chat and the local ChatSource.

One-time flow:
    google-chat-mcp auth
    (or: uvx "git+https://github.com/nuccio/google-chat-mcp@stable" auth)

The token is saved to ~/.config/google-chat-mcp/token.json and refreshed
automatically on server startup if expired.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .chat import ChatClient

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

CONFIG_DIR = Path.home() / ".config" / "google-chat-mcp"
TOKEN_PATH = CONFIG_DIR / "token.json"
_DEFAULT_SECRETS_PATH = CONFIG_DIR / "client_secrets.json"

SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.messages.create",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
]

_REMEDY = 'uvx "git+https://github.com/nuccio/google-chat-mcp@stable" auth'


def has_current_scopes(token_data: dict) -> bool:
    """True if a token.json was authorized for exactly SCOPES.

    Credentials.from_authorized_user_file() ignores the scopes stored in the
    file when it is given SCOPES, so nothing else detects a token issued for
    other scopes: it would keep its broader grant, or fail on refresh with an
    unrelated error.
    """
    scopes = token_data.get("scopes")
    return isinstance(scopes, list) and set(scopes) == set(SCOPES)


def _write_token(token_data: dict) -> None:
    """Writes token.json readable by the owner only (0600, folder 0700).

    The file holds the refresh token and the client secret. It is written to
    a 0600 temporary file in the same folder and then moved into place, so it
    never exists with wider permissions and an interrupted write cannot leave
    a truncated token behind. The chmod calls also fix folders and files
    created with the default umask by earlier versions.
    """
    token_dir = TOKEN_PATH.parent
    token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(token_dir, 0o700)
    # mkstemp creates the file with mode 0600.
    fd, tmp_path = tempfile.mkstemp(dir=token_dir, prefix=".token-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(token_data, f)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, TOKEN_PATH)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def run_auth_flow() -> None:
    secrets_path = Path(os.environ.get("GOOGLE_CLIENT_SECRETS", _DEFAULT_SECRETS_PATH))
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"client_secrets.json not found at {secrets_path}.\n"
            "Download it from Google Cloud Console (APIs & Services → Credentials → "
            "OAuth client ID, Desktop app type) and place it at "
            f"{_DEFAULT_SECRETS_PATH}, or set GOOGLE_CLIENT_SECRETS."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    # prompt='consent' ensures Google always returns a refresh token, even if
    # it considers consent already granted for this session.
    creds = flow.run_local_server(port=0, prompt="consent")
    token_data = json.loads(creds.to_json())
    token_data["authorized_at"] = datetime.now(timezone.utc).isoformat()
    _write_token(token_data)
    print(f"Token saved to {TOKEN_PATH}")


def load_credentials() -> Credentials:
    if not TOKEN_PATH.exists():
        raise RuntimeError(
            f"OAuth token not found. Run:\n  {_REMEDY}"
        )
    existing = json.loads(TOKEN_PATH.read_text())
    if not has_current_scopes(existing):
        raise RuntimeError(
            "The OAuth token was authorized for different scopes than this "
            f"version uses.\nRun:\n  {_REMEDY}"
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # creds.to_json() only serializes fields known to the library and
            # has no idea about 'authorized_at': without this step it would be
            # lost on every access token refresh (roughly once an hour).
            token_data = json.loads(creds.to_json())
            if "authorized_at" in existing:
                token_data["authorized_at"] = existing["authorized_at"]
            _write_token(token_data)
        except RefreshError:
            raise RuntimeError(
                f"The refresh token has expired or is no longer valid (invalid_grant).\n"
                f"Run:\n  {_REMEDY}"
            )
    return creds


class LocalChatSource:
    """ChatSource backed by token.json: credentials loaded lazily, cached per instance."""

    def __init__(self) -> None:
        self._client: "ChatClient | None" = None

    async def client(self) -> "ChatClient":
        if self._client is None:
            from .chat import ChatClient
            creds = load_credentials()
            self._client = ChatClient(creds)
        return self._client

    def auth_status(self) -> dict:
        result: dict = {
            "token_path": str(TOKEN_PATH),
            "token_exists": TOKEN_PATH.exists(),
            "refresh_token_present": False,
            "authorized_at": None,
            "days_since_consent": None,
            "warning": None,
            "remedy": _REMEDY,
        }

        if not TOKEN_PATH.exists():
            result["warning"] = "token.json not found. Run the command in 'remedy'."
            return result

        try:
            data = json.loads(TOKEN_PATH.read_text())
        except Exception:
            result["warning"] = "token.json is unreadable or malformed. Run the command in 'remedy'."
            return result

        result["refresh_token_present"] = bool(data.get("refresh_token"))

        authorized_at_str = data.get("authorized_at")
        if authorized_at_str:
            try:
                authorized_at = datetime.fromisoformat(authorized_at_str)
                result["authorized_at"] = authorized_at_str
                days = (datetime.now(timezone.utc) - authorized_at).days
                result["days_since_consent"] = days
                if days >= 6:
                    result["warning"] = (
                        f"Consent given {days} days ago. In Testing mode, Google invalidates "
                        "refresh tokens after 7 days. Run the command in 'remedy' before "
                        "the server stops working."
                    )
            except ValueError:
                result["warning"] = "Invalid 'authorized_at' field in token.json."
        else:
            result["warning"] = (
                "'authorized_at' field missing (token issued before the update). "
                "Consent date unknown: expiry cannot be estimated."
            )

        if not has_current_scopes(data):
            result["warning"] = (
                "token.json was authorized for different OAuth scopes than this version "
                "uses, so the server will refuse it. Run the command in 'remedy'."
            )

        return result
