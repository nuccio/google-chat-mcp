"""
Test unitari per google_chat_mcp/auth.py.

Verifica:
- load_credentials() con token assente → RuntimeError con comando di rimedio
- load_credentials() con RefreshError → RuntimeError con comando di rimedio
- load_credentials() preserva 'authorized_at' attraverso un refresh riuscito
- run_auth_flow() scrive authorized_at nel token.json
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError

from google_chat_mcp import auth


# ---------------------------------------------------------------------------
# load_credentials
# ---------------------------------------------------------------------------


def test_load_credentials_token_assente(monkeypatch, tmp_path):
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


def test_load_credentials_valide_non_scadute(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text("{}")
    monkeypatch.setattr(auth, "TOKEN_PATH", token_file)

    mock_creds = MagicMock()
    mock_creds.expired = False

    with patch.object(auth.Credentials, "from_authorized_user_file", return_value=mock_creds):
        result = auth.load_credentials()

    assert result is mock_creds
    mock_creds.refresh.assert_not_called()


def test_load_credentials_refresh_preserva_authorized_at(monkeypatch, tmp_path):
    """Regressione: creds.to_json() non conosce 'authorized_at' e lo perderebbe
    a ogni refresh dell'access token se non venisse ripristinato esplicitamente."""
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


def test_run_auth_flow_scrive_authorized_at(monkeypatch, tmp_path):
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

    # prompt='consent' deve essere passato a run_local_server
    call_kwargs = mock_flow.run_local_server.call_args
    assert call_kwargs.kwargs.get("prompt") == "consent"

    # authorized_at deve essere scritto nel token.json
    saved = json.loads(token_file.read_text())
    assert "authorized_at" in saved
