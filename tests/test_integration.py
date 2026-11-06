"""
Integration tests: call the real Google Chat APIs.

Require a valid OAuth token at ~/.config/google-chat-mcp/token.json.
Run with: pytest -m integration

Spaces, their roles and the environment variables (.env file or exported) with
their IDs are described in tests/test_env.toml; `python -m tests.setup_env`
builds them. See tests/README.md. A test whose variable is not set is skipped.

Messages and memberships can include email addresses, and so can the text of a
ChatAPIError: tests compare message names only, and keep that text out of the
output.
"""

import json
import os
import tomllib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv
from fastmcp.exceptions import ToolError

from google_chat_mcp.chat import ChatAPIError
from google_chat_mcp.config import LocalAclProvider, PermissionDeniedError, acl_from_args
from google_chat_mcp.tools import create_server

load_dotenv()

_TEST_ENV = tomllib.loads((Path(__file__).parent / "test_env.toml").read_text())
_SPACES = _TEST_ENV["spaces"]


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _space_from_env(env_var: str) -> str:
    v = os.environ.get(env_var)
    if not v:
        pytest.skip(f"{env_var} not set.")
    return v


@pytest.fixture(scope="session")
def read_only_space():
    return _space_from_env(_SPACES["read_only"]["env_var"])


@pytest.fixture(scope="session")
def write_only_space():
    return _space_from_env(_SPACES["write_only"]["env_var"])


@pytest.fixture(scope="session")
def confirm_space():
    return _space_from_env(_SPACES["read_write_confirm"]["env_var"])


@pytest.fixture(scope="session")
def unattended_space():
    return _space_from_env(_SPACES["read_write_unattended"]["env_var"])


@pytest.fixture(scope="session")
def unconfigured_space():
    return _space_from_env(_SPACES["unconfigured"]["env_var"])


class _LiveChatSource:
    """ChatSource around the real client of the integration session."""

    def __init__(self, chat):
        self._chat = chat

    async def client(self):
        return self._chat

    def auth_status(self) -> dict:
        return {}


class _Tools:
    """The server's tools as awaitable attributes: the tools are built by
    create_server() and are no longer module functions."""

    def __init__(self, chat, *space_args: str):
        self._mcp = create_server(LocalAclProvider(acl_from_args(list(space_args))), _LiveChatSource(chat))

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            tool = await self._mcp.get_tool(name)
            return await tool.fn(*args, **kwargs)

        return call


@pytest.fixture(scope="session")
def configured_server(live_chat):
    """
    Initializes server.py with the real ChatClient and an Acl built from
    tests/test_env.toml, with the spaces whose variable is set, so a missing
    variable skips only the tests that need that space.

    Writes go through send_message_unattended, which needs no MCP client:
    send_message asks for confirmation through the client, and that flow is
    covered by the unit tests in test_server.py.
    """
    return _Tools(
        live_chat,
        *[
            f"{os.environ[spec['env_var']]}:{spec['permissions']}"
            for spec in _SPACES.values()
            if "permissions" in spec and os.environ.get(spec["env_var"])
        ],
    )


def _latest_message_name(chat, space_name: str) -> str | None:
    """Name of the latest message; None when the space is empty, a valid state."""
    names = [m["name"] for m in chat.messages.list(space_name, page_size=1)["messages"]]
    return names[0] if names else None


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
@pytest.mark.anyio
async def test_send_message_unattended_to_rw_space(configured_server, unattended_space):
    result = await configured_server.send_message_unattended(unattended_space, "[test] integration write test")
    assert result["name"].startswith(unattended_space)


@pytest.mark.integration
@pytest.mark.anyio
async def test_sent_message_appears_in_list(configured_server, unattended_space):
    sent = await configured_server.send_message_unattended(unattended_space, "[test] visibility check")
    # page_size=1 must return the message just sent, not the oldest one (#33).
    messages = (await configured_server.list_messages(unattended_space, page_size=1))["messages"]
    assert [m["name"] for m in messages] == [sent["name"]]


@pytest.mark.integration
@pytest.mark.anyio
async def test_send_message_unattended_to_ro_space_is_blocked(configured_server, read_only_space):
    with pytest.raises(PermissionDeniedError, match="Write permission denied"):
        await configured_server.send_message_unattended(read_only_space, "this must not go through")


@pytest.mark.integration
@pytest.mark.anyio
async def test_list_members_rw_space(configured_server, unattended_space):
    members = await configured_server.list_members(unattended_space)
    assert isinstance(members, list)
    assert len(members) > 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_rw_space_has_messages(configured_server, unattended_space):
    """After the write tests, the space must contain at least one message."""
    messages = (await configured_server.list_messages(unattended_space, page_size=10))["messages"]
    assert len(messages) > 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_rw_space_display_name_in_list(configured_server, unattended_space):
    """list_spaces must include the space among the configured ones."""
    names = [s["name"] for s in await configured_server.list_spaces()]
    assert unattended_space in names


# ---------------------------------------------------------------------------
# Tests on the seeded read-only space: nothing writes to it, so its content is
# predictable
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"

_SEED_MESSAGES = json.loads((_FIXTURES / "read_only_seed_messages.json").read_text())


@pytest.mark.integration
@pytest.mark.anyio
async def test_ro_space_contains_seed_messages(configured_server, read_only_space):
    messages = (await configured_server.list_messages(read_only_space, page_size=1000))["messages"]
    texts = {m["text"] for m in messages if "text" in m}
    missing = set(_SEED_MESSAGES) - texts
    assert not missing, f"Missing messages: {missing}"


@pytest.mark.integration
@pytest.mark.anyio
async def test_ro_space_messages_are_in_chronological_order(configured_server, read_only_space):
    messages = (await configured_server.list_messages(read_only_space, page_size=1000))["messages"]
    times = [datetime.fromisoformat(m["createTime"]) for m in messages]
    assert len(times) >= len(_SEED_MESSAGES)
    assert times == sorted(times)


@pytest.mark.integration
@pytest.mark.anyio
async def test_ro_space_page_size_returns_the_latest_messages(configured_server, read_only_space):
    every = (await configured_server.list_messages(read_only_space, page_size=1000))["messages"]
    latest = (await configured_server.list_messages(read_only_space, page_size=2))["messages"]
    every_names = [m["name"] for m in every]
    latest_names = [m["name"] for m in latest]
    assert latest_names == every_names[-2:]
    assert len(latest_names) == 2


@pytest.mark.integration
@pytest.mark.anyio
async def test_ro_space_pagination_goes_back_in_time(configured_server, read_only_space):
    newest = await configured_server.list_messages(read_only_space, page_size=1)
    token = newest["next_page_token"]
    assert token, "the seeded space needs at least two messages"
    older = await configured_server.list_messages(read_only_space, page_size=1, page_token=token)
    older_time = datetime.fromisoformat(older["messages"][0]["createTime"])
    newest_time = datetime.fromisoformat(newest["messages"][0]["createTime"])
    assert older_time < newest_time


# ---------------------------------------------------------------------------
# Tests on a write-only space: `w` does not imply `r`
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_send_message_unattended_to_write_only_space(configured_server, write_only_space):
    result = await configured_server.send_message_unattended(write_only_space, "[test] write-only check")
    assert result["name"].startswith(write_only_space)


@pytest.mark.integration
@pytest.mark.anyio
async def test_list_messages_write_only_space_is_blocked(configured_server, write_only_space):
    with pytest.raises(PermissionDeniedError, match="Read permission denied"):
        await configured_server.list_messages(write_only_space)


@pytest.mark.integration
@pytest.mark.anyio
async def test_list_members_write_only_space_is_blocked(configured_server, write_only_space):
    with pytest.raises(PermissionDeniedError, match="Read permission denied"):
        await configured_server.list_members(write_only_space)


# ---------------------------------------------------------------------------
# Tests on a space that requires confirmation
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_read_from_confirm_space_is_allowed(configured_server, confirm_space):
    assert isinstance((await configured_server.list_messages(confirm_space, page_size=5))["messages"], list)


@pytest.mark.integration
@pytest.mark.anyio
async def test_send_message_unattended_to_confirm_space_is_refused(configured_server, live_chat, confirm_space):
    before = _latest_message_name(live_chat, confirm_space)
    with pytest.raises(ToolError, match="not configured ':unattended'"):
        await configured_server.send_message_unattended(confirm_space, "this must not go through")
    assert _latest_message_name(live_chat, confirm_space) == before


# ---------------------------------------------------------------------------
# Tests on an unconfigured space: all operations must be blocked
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_get_unconfigured_space_is_blocked(configured_server, unconfigured_space):
    with pytest.raises(PermissionDeniedError):
        await configured_server.get_space(unconfigured_space)


@pytest.mark.integration
@pytest.mark.anyio
async def test_list_messages_unconfigured_space_is_blocked(configured_server, unconfigured_space):
    with pytest.raises(PermissionDeniedError):
        await configured_server.list_messages(unconfigured_space)


@pytest.mark.integration
@pytest.mark.anyio
async def test_send_message_unattended_unconfigured_space_is_blocked(configured_server, unconfigured_space):
    with pytest.raises(PermissionDeniedError):
        await configured_server.send_message_unattended(unconfigured_space, "this must not go through")


@pytest.mark.integration
@pytest.mark.anyio
async def test_list_members_unconfigured_space_is_blocked(configured_server, unconfigured_space):
    with pytest.raises(PermissionDeniedError):
        await configured_server.list_members(unconfigured_space)


# ---------------------------------------------------------------------------
# Direct message attempts that must fail
#
# CHAT_DIRECT_MESSAGE_SPACE is a real direct message with an account dedicated
# to testing: a bug in the block would send it a real message, which is why the
# text says what it is. See tests/README.md.
# ---------------------------------------------------------------------------

_DM_TEST_TEXT = "[test] Automated test of google-chat-mcp: this message should never be delivered."
_DM_BLOCK = "named spaces only"
_ALLOWLIST_BLOCK = "not in allowlist"

# Placeholders, not real addresses. The API answer to a real one can differ: it
# is tried once by hand, see the "To verify" list in #41.
_PLACEHOLDER_ADDRESS = "mcp-test-placeholder@example.com"
_PLACEHOLDER_TARGETS = [_PLACEHOLDER_ADDRESS, f"users/{_PLACEHOLDER_ADDRESS}"]

_SEND_TOOLS = ["send_message", "send_message_unattended"]


@contextmanager
def _hide_api_text():
    """Re-raises a ChatAPIError without its text, which can include addresses."""
    try:
        yield
    except ChatAPIError as e:
        raise AssertionError(f"Unexpected Google Chat API error {e.status_code}") from None


def test_hide_api_text_drops_the_response_body():
    with pytest.raises(AssertionError) as excinfo:
        with _hide_api_text():
            raise ChatAPIError(403, "body with someone@example.com")
    assert "example.com" not in str(excinfo.value)
    assert excinfo.value.__suppress_context__


@pytest.fixture(scope="session")
def dm_space(live_chat):
    """The direct message, checked so a wrong ID can't turn these into tests of something else."""
    space_name = _space_from_env(_TEST_ENV["direct_message"]["env_var"])
    with _hide_api_text():
        space_type = live_chat.spaces.get(space_name).get("spaceType")
    if space_type != "DIRECT_MESSAGE":
        pytest.fail(f"{space_name} has spaceType {space_type!r}, expected 'DIRECT_MESSAGE'.")
    return space_name


@pytest.fixture
def dm_server(live_chat):
    """The server with the real client; each test sets the configuration it needs."""

    def configure(*space_args: str) -> _Tools:
        return _Tools(live_chat, *space_args)

    return configure


@contextmanager
def _dm_left_untouched(chat, space_name: str):
    """Checks that the latest message in the direct message does not change."""
    with _hide_api_text():
        before = _latest_message_name(chat, space_name)
        yield
        after = _latest_message_name(chat, space_name)
    assert after == before


async def _attempt_send(server: _Tools, tool: str, target: str) -> None:
    if tool == "send_message":
        ctx = MagicMock()  # a client without elicitation: nothing else asks to confirm
        ctx.session.check_client_capability.return_value = False
        await server.send_message(target, _DM_TEST_TEXT, ctx)
    else:
        await server.send_message_unattended(target, _DM_TEST_TEXT)


# In these first two the configuration allows the send: only the block can stop it.
@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "tool, permissions",
    [("send_message_unattended", "w:unattended"), ("send_message", "w")],
)
async def test_send_to_allowed_dm_is_refused_by_the_block(dm_server, live_chat, dm_space, tool, permissions):
    server = dm_server(f"{dm_space}:{permissions}")
    with _dm_left_untouched(live_chat, dm_space):
        with pytest.raises(ValueError, match=_DM_BLOCK):
            await _attempt_send(server, tool, dm_space)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("tool", _SEND_TOOLS)
async def test_send_to_unconfigured_dm_is_refused_by_the_allowlist(dm_server, live_chat, dm_space, tool):
    server = dm_server()
    with _dm_left_untouched(live_chat, dm_space):
        with pytest.raises(PermissionDeniedError, match=_ALLOWLIST_BLOCK):
            await _attempt_send(server, tool, dm_space)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("tool", _SEND_TOOLS)
@pytest.mark.parametrize("target", _PLACEHOLDER_TARGETS)
async def test_send_to_unconfigured_address_is_refused_by_the_allowlist(dm_server, live_chat, dm_space, tool, target):
    server = dm_server()
    with _dm_left_untouched(live_chat, dm_space):
        with pytest.raises(PermissionDeniedError, match=_ALLOWLIST_BLOCK):
            await _attempt_send(server, tool, target)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("target", _PLACEHOLDER_TARGETS)
async def test_send_to_allowed_address_fails(dm_server, live_chat, dm_space, target):
    server = dm_server(f"{target}:w:unattended")
    status = None
    with _dm_left_untouched(live_chat, dm_space):
        try:
            await server.send_message_unattended(target, _DM_TEST_TEXT)
        except ChatAPIError as e:
            status = e.status_code
    assert status is not None and 400 <= status < 500
