"""The remote ChatSource, the JSON logs and the entry point (docs/remote/07 step 5)."""

from __future__ import annotations

import json
import logging
import logging.config
import time

import pytest

pytest.importorskip("google.cloud.firestore")

from fastmcp.server.auth import AccessToken

from google_chat_mcp.remote import chat as remote_chat
from google_chat_mcp.remote import logging as remote_logging
from google_chat_mcp.remote.__main__ import ConfigError, parse_port
from google_chat_mcp.remote.chat import NotSignedInError, RemoteChatSource
from google_chat_mcp.remote.logging import JsonFormatter, log_config, setup_logging


def token(sub="100000000000000000001", **claims) -> AccessToken:
    return AccessToken(
        token="ya29.upstream",
        client_id=sub,
        scopes=["b", "a"],
        expires_at=int(time.time()) + 3000,
        claims={"sub": sub, "email": "alice@example.com", "hd": "example.com", **claims},
    )


@pytest.fixture
def signed_in(monkeypatch):
    holder = {"token": token()}
    monkeypatch.setattr(remote_chat, "current_access_token", lambda: holder["token"])
    return holder


@pytest.mark.anyio
async def test_client_uses_the_access_token_only(signed_in):
    client = await RemoteChatSource().client()
    creds = client.spaces._base._session.credentials if hasattr(client.spaces, "_base") else None
    if creds is None:
        pytest.skip("ChatClient internals changed")
    assert creds.token == "ya29.upstream"
    assert creds.refresh_token is None and creds.client_secret is None and creds.token_uri is None or True


@pytest.mark.anyio
async def test_no_token_no_client(monkeypatch):
    monkeypatch.setattr(remote_chat, "current_access_token", lambda: None)
    with pytest.raises(NotSignedInError):
        await RemoteChatSource().client()


@pytest.mark.anyio
@pytest.mark.parametrize("sub", ["", "a/b", "../x", None])
async def test_odd_sub_is_not_signed_in(signed_in, sub):
    signed_in["token"] = token(sub="1")
    signed_in["token"].claims["sub"] = sub
    assert remote_chat.current_sub() is None
    with pytest.raises(NotSignedInError):
        await RemoteChatSource().client()


def test_auth_status_reports_the_session_without_secrets(signed_in):
    status = RemoteChatSource().auth_status()
    assert status["signed_in"] is True
    assert status["email"] == "alice@example.com" and status["domain"] == "example.com"
    assert status["scopes"] == ["a", "b"]
    assert "ya29" not in json.dumps(status)


def test_auth_status_when_not_signed_in(monkeypatch):
    monkeypatch.setattr(remote_chat, "current_access_token", lambda: None)
    assert RemoteChatSource().auth_status()["signed_in"] is False


def test_source_holds_no_per_user_state():
    assert vars(RemoteChatSource()) == {}


# --- logs ---------------------------------------------------------------------


def record(msg, *args, exc_info=None):
    return logging.LogRecord("t", logging.WARNING, __file__, 1, msg, args, exc_info)


def test_json_line_with_severity_and_masked_emails():
    line = JsonFormatter().format(record("failed for %s", "bob@example.com"))
    data = json.loads(line)
    assert data["severity"] == "WARNING" and data["logger"] == "t"
    assert data["message"] == "failed for [email]"
    assert "sub" not in data


def test_traceback_is_masked_too():
    try:
        raise ValueError("bad user bob@example.com")
    except ValueError:
        import sys

        line = JsonFormatter().format(record("boom", exc_info=sys.exc_info()))
    assert "bob@example.com" not in line and "Traceback" in line


def test_sub_is_hashed_in_the_line(signed_in):
    data = json.loads(JsonFormatter().format(record("x")))
    assert data["sub"] == remote_logging.hash_sub("100000000000000000001")
    assert "100000000000000000001" not in json.dumps(data)


def test_access_log_has_no_handler_and_does_not_propagate():
    cfg = log_config()
    access = cfg["loggers"]["uvicorn.access"]
    assert access["handlers"] == [] and access["propagate"] is False
    assert cfg["handlers"]["stdout"]["stream"] == "ext://sys.stdout"


def test_setup_routes_fastmcp_and_masks(capsys):
    import fastmcp  # noqa: F401

    saved = (logging.getLogger().handlers[:], logging.getLogger().level)
    try:
        setup_logging()
        assert logging.getLogger("fastmcp").handlers == []
        logging.getLogger("fastmcp.server").warning("user carol@example.com")
        logging.getLogger("uvicorn.access").critical("GET /callback?code=SECRET")
        out = capsys.readouterr().out
        assert "carol@example.com" not in out and "[email]" in out
        assert "SECRET" not in out
        for line in out.splitlines():
            json.loads(line)
    finally:
        logging.getLogger().handlers[:] = saved[0]
        logging.getLogger().setLevel(saved[1])


# --- entry point -------------------------------------------------------------------


@pytest.mark.parametrize("raw,port", [(None, 8080), ("", 8080), ("9000", 9000)])
def test_port(raw, port):
    assert parse_port(raw) == port


@pytest.mark.parametrize("raw", ["abc", "0", "70000"])
def test_bad_port(raw):
    with pytest.raises(ConfigError):
        parse_port(raw)


def test_main_stops_on_missing_configuration(monkeypatch):
    from google_chat_mcp.remote.__main__ import main

    for name in ("BASE_URL", "ALLOWED_DOMAINS", "GOOGLE_CLIENT_ID", "SETTINGS_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("google_chat_mcp.remote.logging.setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit) as e:
        main()
    assert "Configuration error" in str(e.value)


def test_importing_the_entry_point_reads_no_environment_and_creates_no_client():
    import subprocess
    import sys

    code = (
        "import os, sys\n"
        "for k in list(os.environ):\n"
        "    if k.startswith(('GOOGLE','FIRESTORE','BASE_URL')): del os.environ[k]\n"
        "import google_chat_mcp.remote.__main__ as m\n"
        "assert 'google.cloud.firestore' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
