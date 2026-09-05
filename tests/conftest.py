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
    Istanza ChatClient connessa all'account Google reale, per i test di integrazione.

    Richiede che l'utente abbia già eseguito:
        google-chat-mcp auth
    (oppure: uvx "git+https://github.com/nuccio/google-chat-mcp" auth)

    Se il token non è presente la suite di integrazione viene saltata (skip),
    non fallisce: i test di integrazione sono opt-in, non devono rompere
    una run offline o su CI senza credenziali.
    """
    token_path = Path.home() / ".config" / "google-chat-mcp" / "token.json"
    if not token_path.exists():
        pytest.skip(
            f"Token OAuth non trovato in {token_path}. "
            "Esegui `google-chat-mcp auth` e riprova."
        )

    from google_chat_mcp.chat import ChatClient
    return ChatClient()


@pytest.fixture(scope="session")
def mcp_module():
    """
    Importa google_chat_mcp.server con un ChatClient mock iniettato direttamente,
    senza toccare la rete né richiedere un token OAuth reale.

    Con il caricamento lazy delle credenziali, init() non istanzia più ChatClient:
    basta settare srv._chat a un MagicMock dopo init() per isolare i tool dal disco.
    I singoli test rimpiazzano _chat.spaces, _chat.messages, ecc. via monkeypatch.
    """
    import google_chat_mcp.server as srv
    importlib.reload(srv)
    srv.init([])
    srv._chat = MagicMock()
    return srv
