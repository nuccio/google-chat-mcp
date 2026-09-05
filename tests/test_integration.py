"""
Integration tests: call the real Google Chat APIs.

Require a valid OAuth token at ~/.config/google-chat-mcp/token.json.
Run with: pytest -m integration

Environment variables (.env file or exported):
    CHAT_READ_WRITE_SPACE    resource name of the space configured as rw
                             (e.g. spaces/AAQA3aH7CGY  →  TestSpace1)
    CHAT_READ_ONLY_SPACE     resource name of the space configured as r
                             (e.g. spaces/AAQAZNkyulE  →  TestSpace2)
    CHAT_UNCONFIGURED_SPACE  resource name of a space not configured in the server;
                             all operations on it must be blocked
"""

import json
import os
from pathlib import Path

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
        pytest.skip("CHAT_READ_WRITE_SPACE not set.")
    return v


@pytest.fixture(scope="session")
def ro_space():
    v = os.environ.get("CHAT_READ_ONLY_SPACE")
    if not v:
        pytest.skip("CHAT_READ_ONLY_SPACE not set.")
    return v


@pytest.fixture(scope="session")
def unconfigured_space():
    v = os.environ.get("CHAT_UNCONFIGURED_SPACE")
    if not v:
        pytest.skip("CHAT_UNCONFIGURED_SPACE not set.")
    return v


@pytest.fixture(scope="session")
def configured_server(live_chat, rw_space, ro_space):
    """
    Initializes server.py with the real ChatClient and a SpaceConfig that
    mirrors the configuration declared in the environment variables:
      - rw_space  →  read + write
      - ro_space  →  read only
    """
    _server._cfg = SpaceConfig.from_args([f"{rw_space}:rw", f"{ro_space}:r"])
    _server._chat = live_chat
    return _server


# ---------------------------------------------------------------------------
# Read tests (use ChatClient directly)
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
# Write and permission tests (go through configured_server)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_send_message_to_rw_space(configured_server, rw_space):
    result = configured_server.send_message(rw_space, "[test] integration write test")
    assert "name" in result
    assert result["name"].startswith(rw_space)


@pytest.mark.integration
def test_sent_message_appears_in_list(configured_server, rw_space):
    sent = configured_server.send_message(rw_space, "[test] visibility check")
    # page_size=1000 because the API orders chronologically (oldest first) and
    # messages accumulated from previous runs can exceed 10.
    messages = configured_server.list_messages(rw_space, page_size=1000)
    names = [m["name"] for m in messages]
    assert sent["name"] in names


@pytest.mark.integration
def test_send_message_to_ro_space_is_blocked(configured_server, ro_space):
    with pytest.raises(PermissionDeniedError):
        configured_server.send_message(ro_space, "this must not go through")


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
# Tests on known content in configured spaces
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"

# Known messages present in CHAT_READ_WRITE_SPACE (TestSpace1) from previous runs.
# The set is stable: tests add messages but never delete them.
_KNOWN_MESSAGES = set(
    json.loads((_FIXTURES / "rw_space_known_messages.json").read_text())
)


@pytest.mark.integration
def test_rw_space_has_messages(configured_server, rw_space):
    """After the write tests, the rw space must contain at least one message."""
    messages = configured_server.list_messages(rw_space, page_size=10)
    assert len(messages) > 0


@pytest.mark.integration
def test_rw_space_contains_known_messages(configured_server, rw_space):
    """The known messages from previous runs must still be present."""
    messages = configured_server.list_messages(rw_space, page_size=50)
    texts = {m["text"] for m in messages if "text" in m}
    missing = _KNOWN_MESSAGES - texts
    assert not missing, f"Missing messages: {missing}"


@pytest.mark.integration
def test_ro_space_has_messages(configured_server, ro_space):
    messages = configured_server.list_messages(ro_space, page_size=10)
    assert len(messages) > 0


@pytest.mark.integration
def test_rw_space_display_name_in_list(configured_server):
    """list_spaces must include the rw space among the configured ones."""
    spaces = configured_server.list_spaces()
    names = [s["name"] for s in spaces]
    rw = os.environ.get("CHAT_READ_WRITE_SPACE", "")
    assert rw in names


# ---------------------------------------------------------------------------
# Tests on an unconfigured space: all operations must be blocked
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
        configured_server.send_message(unconfigured_space, "this must not go through")


@pytest.mark.integration
def test_list_members_unconfigured_space_is_blocked(configured_server, unconfigured_space):
    with pytest.raises(PermissionDeniedError):
        configured_server.list_members(unconfigured_space)
