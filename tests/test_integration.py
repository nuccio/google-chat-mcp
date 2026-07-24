"""
Test di integrazione: chiamano le API Google Chat reali.

Richiedono un token OAuth valido in ~/.config/google-chat-mcp/token.json.
Eseguiti con: pytest -m integration
"""

import pytest


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
