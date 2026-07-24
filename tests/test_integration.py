"""
Test di integrazione: chiamano le API Google Chat reali.

Richiedono un token OAuth valido in ~/.config/google-chat-mcp/token.json.
Eseguiti con: pytest -m integration

Per i test di scrittura serve anche impostare CHAT_TEST_SPACE nell'ambiente
o nel file .env (es. CHAT_TEST_SPACE=spaces/AAQA3aH7CGY).
"""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()


# --- Fixture ---


@pytest.fixture(scope="session")
def test_write_space():
    space = os.environ.get("CHAT_TEST_SPACE")
    if not space:
        pytest.skip("CHAT_TEST_SPACE non impostata: test di scrittura saltati.")
    return space


# --- Lettura ---


@pytest.mark.integration
def test_list_spaces_returns_results(live_chat):
    spaces = live_chat.spaces.list()
    assert isinstance(spaces, list)
    assert len(spaces) > 0


@pytest.mark.integration
def test_each_space_has_name_and_type(live_chat):
    spaces = live_chat.spaces.list()
    for space in spaces:
        assert "name" in space, f"Spazio senza 'name': {space}"
        assert space["name"].startswith("spaces/"), f"name non valido: {space['name']}"
        assert "spaceType" in space or "type" in space, f"Spazio senza tipo: {space}"


@pytest.mark.integration
def test_get_space_matches_list(live_chat):
    spaces = live_chat.spaces.list()
    first = spaces[0]
    detail = live_chat.spaces.get(first["name"])
    assert detail["name"] == first["name"]


# --- Scrittura ---


@pytest.mark.integration
def test_send_message_returns_message_resource(live_chat, test_write_space):
    result = live_chat.messages.send(test_write_space, "[test] integration test message")
    assert "name" in result
    assert result["name"].startswith(test_write_space)


@pytest.mark.integration
def test_sent_message_appears_in_list(live_chat, test_write_space):
    sent = live_chat.messages.send(test_write_space, "[test] message visibility check")
    sent_name = sent["name"]
    messages = live_chat.messages.list(test_write_space, page_size=10)
    names = [m["name"] for m in messages]
    assert sent_name in names, f"Messaggio inviato {sent_name!r} non trovato in {names}"


@pytest.mark.integration
def test_list_members_returns_results(live_chat, test_write_space):
    members = live_chat.members.list(test_write_space)
    assert isinstance(members, list)
    assert len(members) > 0
