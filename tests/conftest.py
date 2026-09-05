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
def mcp_module():
    """
    Imports google_chat_mcp.server with a mock ChatClient injected directly,
    without touching the network or requiring a real OAuth token.

    With lazy credential loading, init() no longer instantiates ChatClient:
    it's enough to set srv._chat to a MagicMock after init() to isolate the
    tools from disk. Individual tests replace _chat.spaces, _chat.messages,
    etc. via monkeypatch.
    """
    import google_chat_mcp.server as srv
    importlib.reload(srv)
    srv.init([])
    srv._chat = MagicMock()
    return srv
