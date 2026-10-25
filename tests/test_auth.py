"""
Unit tests for google_chat_mcp/auth.py.

Covers:
- load_credentials() with no token file -> RuntimeError with remedy command
- load_credentials() with RefreshError -> RuntimeError with remedy command
- load_credentials() preserves 'authorized_at' across a successful refresh
- run_auth_flow() writes authorized_at to token.json
- token.json is written 0600 (folder 0700) on both write paths, and an
  existing 0644 file is tightened on the next write (POSIX only)
"""

import json
import os
import stat
import sys
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError

from google_chat_mcp import auth


# ---------------------------------------------------------------------------
# load_credentials
# ---------------------------------------------------------------------------


def test_load_credentials_missing_token(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "nonexistent.json")
    with pytest.raises(RuntimeError, match="auth"):
        auth.load_credentials()


def test_load_credentials_refresh_error(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "token": "old_access",
        "refresh_token": "invalid_refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "id",
        "client_secret": "secret",
        "scopes": ["https://www.googleapis.com/auth/chat.messages"],
    }))
    monkeypatch.setattr(auth, "TOKEN_PATH", token_file)

    mock_creds = MagicMock()
    mock_creds.expired = True
    mock_creds.refresh_token = "invalid_refresh"
    mock_creds.refresh.side_effect = RefreshError("invalid_grant")

    with patch.object(auth.Credentials, "from_authorized_user_file", return_value=mock_creds):
        with pytest.raises(RuntimeError, match="auth"):
            auth.load_credentials()


def test_load_credentials_valid_not_expired(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text("{}")
    monkeypatch.setattr(auth, "TOKEN_PATH", token_file)

    mock_creds = MagicMock()
    mock_creds.expired = False

    with patch.object(auth.Credentials, "from_authorized_user_file", return_value=mock_creds):
        result = auth.load_credentials()

    assert result is mock_creds
    mock_creds.refresh.assert_not_called()


def test_load_credentials_refresh_preserves_authorized_at(monkeypatch, tmp_path):
    """Regression test: creds.to_json() doesn't know about 'authorized_at' and
    would drop it on every access token refresh unless explicitly restored."""
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "token": "old_access",
        "refresh_token": "valid_refresh",
        "authorized_at": "2026-01-01T00:00:00+00:00",
    }))
    monkeypatch.setattr(auth, "TOKEN_PATH", token_file)

    mock_creds = MagicMock()
    mock_creds.expired = True
    mock_creds.refresh_token = "valid_refresh"
    mock_creds.refresh.return_value = None
    mock_creds.to_json.return_value = json.dumps({"token": "new_access", "refresh_token": "valid_refresh"})

    with patch.object(auth.Credentials, "from_authorized_user_file", return_value=mock_creds):
        auth.load_credentials()

    saved = json.loads(token_file.read_text())
    assert saved["authorized_at"] == "2026-01-01T00:00:00+00:00"
    assert saved["token"] == "new_access"


# ---------------------------------------------------------------------------
# run_auth_flow
# ---------------------------------------------------------------------------


def test_run_auth_flow_writes_authorized_at(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    secrets_file = tmp_path / "client_secrets.json"
    secrets_file.write_text("{}")

    monkeypatch.setenv("GOOGLE_CLIENT_SECRETS", str(secrets_file))
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(auth, "TOKEN_PATH", token_file)

    mock_creds = MagicMock()
    mock_creds.to_json.return_value = json.dumps({"refresh_token": "tok"})

    mock_flow = MagicMock()
    mock_flow.run_local_server.return_value = mock_creds

    with patch.object(auth.InstalledAppFlow, "from_client_secrets_file", return_value=mock_flow):
        auth.run_auth_flow()

    # prompt='consent' must be passed to run_local_server
    call_kwargs = mock_flow.run_local_server.call_args
    assert call_kwargs.kwargs.get("prompt") == "consent"

    # authorized_at must be written to token.json
    saved = json.loads(token_file.read_text())
    assert "authorized_at" in saved


# ---------------------------------------------------------------------------
# token.json permissions
# ---------------------------------------------------------------------------

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@posix_only
def test_run_auth_flow_token_permissions(monkeypatch, tmp_path):
    config_dir = tmp_path / "google-chat-mcp"
    token_file = config_dir / "token.json"
    secrets_file = tmp_path / "client_secrets.json"
    secrets_file.write_text("{}")

    monkeypatch.setenv("GOOGLE_CLIENT_SECRETS", str(secrets_file))
    monkeypatch.setattr(auth, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(auth, "TOKEN_PATH", token_file)

    mock_creds = MagicMock()
    mock_creds.to_json.return_value = json.dumps({"refresh_token": "tok"})
    mock_flow = MagicMock()
    mock_flow.run_local_server.return_value = mock_creds

    with patch.object(auth.InstalledAppFlow, "from_client_secrets_file", return_value=mock_flow):
        auth.run_auth_flow()

    assert _mode(token_file) == 0o600
    assert _mode(config_dir) == 0o700
    assert list(config_dir.iterdir()) == [token_file]  # no temp file left behind


@posix_only
def test_load_credentials_refresh_tightens_existing_token(monkeypatch, tmp_path):
    """A token.json created 0644 by an earlier version becomes 0600 on refresh."""
    config_dir = tmp_path / "google-chat-mcp"
    config_dir.mkdir(mode=0o755)
    os.chmod(config_dir, 0o755)
    token_file = config_dir / "token.json"
    token_file.write_text(json.dumps({"refresh_token": "valid_refresh"}))
    os.chmod(token_file, 0o644)
    monkeypatch.setattr(auth, "TOKEN_PATH", token_file)

    mock_creds = MagicMock()
    mock_creds.expired = True
    mock_creds.refresh_token = "valid_refresh"
    mock_creds.to_json.return_value = json.dumps({"token": "new_access", "refresh_token": "valid_refresh"})

    with patch.object(auth.Credentials, "from_authorized_user_file", return_value=mock_creds):
        auth.load_credentials()

    assert _mode(token_file) == 0o600
    assert _mode(config_dir) == 0o700
    assert json.loads(token_file.read_text())["token"] == "new_access"


def test_write_token_failure_keeps_existing_token(monkeypatch, tmp_path):
    """If serialization fails mid-write, the old token.json is left intact."""
    token_file = tmp_path / "token.json"
    token_file.write_text('{"refresh_token": "old"}')
    monkeypatch.setattr(auth, "TOKEN_PATH", token_file)

    with pytest.raises(TypeError):
        auth._write_token({"bad": object()})

    assert json.loads(token_file.read_text()) == {"refresh_token": "old"}
    assert list(tmp_path.iterdir()) == [token_file]
