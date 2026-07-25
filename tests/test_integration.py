"""
Test di integrazione: chiamano le API Google Chat reali.

Richiedono un token OAuth valido in ~/.config/google-chat-mcp/token.json.
Eseguiti con: pytest -m integration

Variabili d'ambiente (file .env o esportate):
    CHAT_READ_WRITE_SPACE    resource name dello spazio configurato come rw
                             (es. spaces/AAQA3aH7CGY  →  TestSpace1)
    CHAT_READ_ONLY_SPACE     resource name dello spazio configurato come r
                             (es. spaces/AAQAZNkyulE  →  TestSpace2)
    CHAT_UNCONFIGURED_SPACE  resource name di uno spazio non configurato nel server;
                             tutte le operazioni su di esso devono essere bloccate
"""

import os

import pytest
from dotenv import load_dotenv

from google_chat_mcp.config import PermissionDeniedError, SpaceConfig
from google_chat_mcp import server as _server

load_dotenv()


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def rw_space():
    v = os.environ.get("CHAT_READ_WRITE_SPACE")
    if not v:
        pytest.skip("CHAT_READ_WRITE_SPACE non impostata.")
    return v


@pytest.fixture(scope="session")
def ro_space():
    v = os.environ.get("CHAT_READ_ONLY_SPACE")
    if not v:
        pytest.skip("CHAT_READ_ONLY_SPACE non impostata.")
    return v


@pytest.fixture(scope="session")
def unconfigured_space():
    v = os.environ.get("CHAT_UNCONFIGURED_SPACE")
    if not v:
        pytest.skip("CHAT_UNCONFIGURED_SPACE non impostata.")
    return v


@pytest.fixture(scope="session")
def configured_server(live_chat, rw_space, ro_space):
    """
    Inizializza server.py con il ChatClient reale e una SpaceConfig che
    rispecchia la configurazione dichiarata nelle variabili d'ambiente:
      - rw_space  →  lettura + scrittura
      - ro_space  →  sola lettura
    """
    _server._cfg = SpaceConfig.from_args([f"{rw_space}:rw", f"{ro_space}:r"])
    _server._chat = live_chat
    return _server


# ---------------------------------------------------------------------------
# Test di lettura (usano il ChatClient direttamente)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_list_spaces_returns_results(live_chat):
    spaces = live_chat.spaces.list()
    assert isinstance(spaces, list)
    assert len(spaces) > 0


@pytest.mark.integration
def test_each_space_has_name_and_type(live_chat):
    spaces = live_chat.spaces.list()
    for space in spaces:
        assert "name" in space
        assert space["name"].startswith("spaces/")
        assert "spaceType" in space or "type" in space


@pytest.mark.integration
def test_get_space_matches_list(live_chat):
    spaces = live_chat.spaces.list()
    first = spaces[0]
    detail = live_chat.spaces.get(first["name"])
    assert detail["name"] == first["name"]


# ---------------------------------------------------------------------------
# Test di scrittura e permessi (passano per configured_server)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_send_message_to_rw_space(configured_server, rw_space):
    result = configured_server.send_message(rw_space, "[test] integration write test")
    assert "name" in result
    assert result["name"].startswith(rw_space)


@pytest.mark.integration
def test_sent_message_appears_in_list(configured_server, rw_space):
    sent = configured_server.send_message(rw_space, "[test] visibility check")
    # page_size=1000 perché l'API ordina cronologicamente (oldest first) e
    # i messaggi accumulati dai run precedenti possono superare 10.
    messages = configured_server.list_messages(rw_space, page_size=1000)
    names = [m["name"] for m in messages]
    assert sent["name"] in names


@pytest.mark.integration
def test_send_message_to_ro_space_is_blocked(configured_server, ro_space):
    with pytest.raises(PermissionDeniedError):
        configured_server.send_message(ro_space, "questo non deve arrivare")


@pytest.mark.integration
def test_read_from_ro_space_is_allowed(configured_server, ro_space):
    messages = configured_server.list_messages(ro_space, page_size=5)
    assert isinstance(messages, list)


@pytest.mark.integration
def test_list_members_rw_space(configured_server, rw_space):
    members = configured_server.list_members(rw_space)
    assert isinstance(members, list)
    assert len(members) > 0


# ---------------------------------------------------------------------------
# Test su contenuto noto negli spazi configurati
# ---------------------------------------------------------------------------

# Messaggi noti presenti in CHAT_READ_WRITE_SPACE (TestSpace1) da run precedenti.
# Il set è stabile: i test aggiungono messaggi ma non ne cancellano.
_KNOWN_MESSAGES = {
    "[test] integration test message",
    "[test] message visibility check",
    "[test] integration write test",
    "[test] visibility check",
}


@pytest.mark.integration
def test_rw_space_has_messages(configured_server, rw_space):
    """Dopo i test di scrittura, lo spazio rw deve contenere almeno un messaggio."""
    messages = configured_server.list_messages(rw_space, page_size=10)
    assert len(messages) > 0


@pytest.mark.integration
def test_rw_space_contains_known_messages(configured_server, rw_space):
    """I messaggi noti da run precedenti devono essere ancora presenti."""
    messages = configured_server.list_messages(rw_space, page_size=50)
    texts = {m["text"] for m in messages if "text" in m}
    missing = _KNOWN_MESSAGES - texts
    assert not missing, f"Messaggi mancanti: {missing}"


@pytest.mark.integration
def test_ro_space_has_messages(configured_server, ro_space):
    messages = configured_server.list_messages(ro_space, page_size=10)
    assert len(messages) > 0


@pytest.mark.integration
def test_rw_space_display_name_in_list(configured_server):
    """list_spaces deve includere lo spazio rw tra quelli configurati."""
    spaces = configured_server.list_spaces()
    names = [s["name"] for s in spaces]
    rw = os.environ.get("CHAT_READ_WRITE_SPACE", "")
    assert rw in names


# ---------------------------------------------------------------------------
# Test su spazio non configurato: tutte le operazioni devono essere bloccate
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_unconfigured_space_is_blocked(configured_server, unconfigured_space):
    with pytest.raises(PermissionDeniedError):
        configured_server.get_space(unconfigured_space)


@pytest.mark.integration
def test_list_messages_unconfigured_space_is_blocked(configured_server, unconfigured_space):
    with pytest.raises(PermissionDeniedError):
        configured_server.list_messages(unconfigured_space)


@pytest.mark.integration
def test_send_message_unconfigured_space_is_blocked(configured_server, unconfigured_space):
    with pytest.raises(PermissionDeniedError):
        configured_server.send_message(unconfigured_space, "questo non deve arrivare")


@pytest.mark.integration
def test_list_members_unconfigured_space_is_blocked(configured_server, unconfigured_space):
    with pytest.raises(PermissionDeniedError):
        configured_server.list_members(unconfigured_space)
