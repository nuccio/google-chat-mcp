import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from google_chat_mcp.config import LocalAclProvider, acl_from_args
from google_chat_mcp.permissions import Acl
from google_chat_mcp.tools import create_server


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

    from google_chat_mcp.auth import load_credentials
    from google_chat_mcp.chat import ChatClient
    return ChatClient(load_credentials())


class _TestAclProvider:
    """Mutable AclProvider for tests: swap acl_value between test cases."""

    def __init__(self, acl: Acl) -> None:
        self.acl_value = acl

    async def acl(self) -> Acl:
        return self.acl_value


class _FakeChatSource:
    """ChatSource backed by a MagicMock, for unit tests."""

    def __init__(self, mock: MagicMock) -> None:
        self._mock = mock

    async def client(self):
        return self._mock

    def auth_status(self) -> dict:
        return {
            "token_path": "/fake/token.json",
            "token_exists": True,
            "refresh_token_present": True,
            "authorized_at": "2026-01-01T00:00:00+00:00",
            "days_since_consent": 1,
            "warning": None,
            "remedy": "...",
        }


@pytest.fixture
def mode():
    """
    Server built from create_server() with a mutable AclProvider and a fake
    ChatSource. Each test gets a fresh MagicMock for the Chat API.

    mode.mcp         — the FastMCP instance for Client(mode.mcp)
    mode.provider    — _TestAclProvider; set mode.provider.acl_value to change the ACL
    mode.fake_chat   — MagicMock; set .spaces.get.return_value etc. as needed
    mode.chat_source — the _FakeChatSource instance
    """
    fake_chat = MagicMock()
    provider = _TestAclProvider(Acl({}))
    chat_source = _FakeChatSource(fake_chat)
    mcp_instance = create_server(provider, chat_source)
    return SimpleNamespace(
        mcp=mcp_instance,
        provider=provider,
        fake_chat=fake_chat,
        chat_source=chat_source,
    )
