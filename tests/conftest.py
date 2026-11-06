import importlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def live_chat():
    """
    ChatClient instance connected to the real Google account, for integration tests.

    Requires that the user has already run:
        google-chat-mcp auth
    (or: uvx "git+https://github.com/nuccio/google-chat-mcp" auth)

    If the token is missing, the integration suite is skipped, not failed:
    integration tests are opt-in and must not break an offline run or a CI
    run without credentials.
    """
    token_path = Path.home() / ".config" / "google-chat-mcp" / "token.json"
    if not token_path.exists():
        pytest.skip(
            f"OAuth token not found at {token_path}. "
            "Run `google-chat-mcp auth` and try again."
        )

    from google_chat_mcp.chat import ChatClient
    return ChatClient()


@pytest.fixture(scope="session")
def _mcp_server():
    """google_chat_mcp.server, reloaded and initialised once per session."""
    import google_chat_mcp.server as srv
    importlib.reload(srv)
    srv.init([])
    return srv


@pytest.fixture
def mcp_module(_mcp_server, monkeypatch):
    """
    Imports google_chat_mcp.server with a mock ChatClient injected directly,
    without touching the network or requiring a real OAuth token.

    With lazy credential loading, init() no longer instantiates ChatClient:
    it's enough to set srv._chat to a MagicMock after init() to isolate the
    tools from disk. Individual tests replace _chat.spaces, _chat.messages,
    etc. via monkeypatch.

    Each test gets a fresh MagicMock: since pytest 9.2, undoing a monkeypatch
    on an attribute the mock created on access deletes it, so a mock shared
    across tests would lose spaces.get, messages.list, ... after the first one.
    """
    monkeypatch.setattr(_mcp_server, "_chat", MagicMock())
    return _mcp_server


@pytest.fixture(scope="session")
def firestore_emulator():
    """The Firestore emulator, shared by the tests of both remote services.

    Used with `pytest -m emulator`. It fails, never skips, when the emulator is
    not reachable, so a CI job that was meant to test Firestore cannot pass
    without having tested it.
    """
    import socket

    from tests.fixtures import seed

    try:
        host = seed.emulator_host()
    except seed.NotAnEmulator as e:
        pytest.fail(str(e))
    name, _, port = host.rpartition(":")
    try:
        socket.create_connection((name, int(port)), timeout=3).close()
    except OSError as e:
        pytest.fail(f"The Firestore emulator is not reachable at {host}: {e}")
    return host


@pytest.fixture
def firestore_db(firestore_emulator):
    """A client bound to the emulator, on an empty database for each test."""
    from tests.fixtures import seed

    seed.reset(firestore_emulator)
    return seed.client()
