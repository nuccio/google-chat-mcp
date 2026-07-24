import importlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def mcp_module():
    """
    Importa google_chat_mcp.server con auth e HTTP session mockati, senza
    toccare la rete né richiedere un token OAuth reale. I singoli test
    rimpiazzano poi _chat.spaces, _chat.messages, ecc. via monkeypatch.
    """
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds.valid = True

    with patch("google_chat_mcp.chat.client.load_credentials", return_value=mock_creds):
        with patch("google_chat_mcp.chat.client.AuthorizedSession"):
            import google_chat_mcp.server as srv
            importlib.reload(srv)
            srv.init([])  # SpaceConfig vuota; i singoli test la sovrascrivono

    return srv
