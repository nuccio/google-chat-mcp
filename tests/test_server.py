"""
Tests for the MCP server (google_chat_mcp/server.py).

Verifies:
- which tools are exposed and that they all have a description
- a real round-trip through the MCP protocol (FastMCP Client)
- that permission checks produce a ToolError via MCP
- that DMs are blocked
- that ChatAPIError errors are propagated as ToolError
- that send_message asks for confirmation (elicitation) unless the space is :unattended
"""

import json

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError
from unittest.mock import MagicMock

from google_chat_mcp.config import SpaceConfig
from google_chat_mcp.chat.client import ChatAPIError

EXPECTED_TOOLS = {
    "list_spaces",
    "get_space",
    "list_messages",
    "send_message",
    "list_members",
    "chat_auth_status",
}


# --- Tool registration ---


@pytest.mark.anyio
async def test_espone_esattamente_i_tool_attesi(mcp_module):
    async with Client(mcp_module.mcp) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


@pytest.mark.anyio
async def test_ogni_tool_ha_una_descrizione(mcp_module):
    async with Client(mcp_module.mcp) as client:
        tools = await client.list_tools()
    missing = [t.name for t in tools if not t.description]
    assert not missing, f"Tool without a description: {missing}"


# --- list_spaces ---


def test_list_spaces_delegates_to_cfg(mcp_module, monkeypatch):
    fake = [{"name": "spaces/X", "read": True, "write": False}]
    monkeypatch.setattr(mcp_module._cfg, "list_spaces", lambda: fake)
    assert mcp_module.list_spaces() == fake


# --- list_messages: round-trip MCP ---


@pytest.mark.anyio
async def test_list_messages_round_trip(mcp_module, monkeypatch):
    fake_msgs = [{"name": "spaces/A/messages/1", "text": "ciao"}]
    monkeypatch.setattr(mcp_module._cfg, "require_read", lambda _: None)
    monkeypatch.setattr(
        mcp_module._chat.messages, "list", lambda space, **kw: fake_msgs
    )
    async with Client(mcp_module.mcp) as client:
        result = await client.call_tool("list_messages", {"space_name": "spaces/A"})
    assert result.data == fake_msgs


# --- Permission checks ---


@pytest.mark.anyio
async def test_send_message_write_false_solleva_tool_error(mcp_module, monkeypatch):
    from google_chat_mcp.config import PermissionDeniedError

    def deny(_):
        raise PermissionDeniedError("denied")

    monkeypatch.setattr(mcp_module._cfg, "require_write", deny)
    async with Client(mcp_module.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )


@pytest.mark.anyio
async def test_get_space_read_false_solleva_tool_error(mcp_module, monkeypatch):
    from google_chat_mcp.config import PermissionDeniedError

    def deny(_):
        raise PermissionDeniedError("denied")

    monkeypatch.setattr(mcp_module._cfg, "require_read", deny)
    async with Client(mcp_module.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_space", {"space_name": "spaces/A"})


# --- DM blocking ---


@pytest.mark.anyio
async def test_send_message_blocca_direct_message(mcp_module, monkeypatch):
    monkeypatch.setattr(mcp_module._cfg, "require_write", lambda _: None)
    monkeypatch.setattr(
        mcp_module._chat.spaces, "get", lambda _: {"spaceType": "DIRECT_MESSAGE"}
    )
    async with Client(mcp_module.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )


@pytest.mark.anyio
async def test_send_message_blocca_group_chat(mcp_module, monkeypatch):
    monkeypatch.setattr(mcp_module._cfg, "require_write", lambda _: None)
    monkeypatch.setattr(
        mcp_module._chat.spaces, "get", lambda _: {"spaceType": "GROUP_CHAT"}
    )
    async with Client(mcp_module.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )


@pytest.mark.anyio
async def test_send_message_space_type_space_ok(mcp_module, monkeypatch):
    fake_result = {"name": "spaces/A/messages/42"}
    monkeypatch.setattr(mcp_module._cfg, "require_write", lambda _: None)
    monkeypatch.setattr(
        mcp_module._chat.spaces, "get", lambda _: {"spaceType": "SPACE"}
    )
    monkeypatch.setattr(
        mcp_module._chat.messages, "send", lambda space, text: fake_result
    )
    async with Client(mcp_module.mcp, elicitation_handler=_accept) as client:
        result = await client.call_tool(
            "send_message", {"space_name": "spaces/A", "text": "ciao"}
        )
    assert result.data == fake_result


# --- send_message: confirmation (elicitation) ---
#
# Each test runs on both protocol eras: "legacy" (the server pushes an
# elicitation/create request) and "auto" (2026-07-28: the tool returns an
# InputRequiredResult and the client retries with the answer).


@pytest.fixture(params=["legacy", "auto"])
def client_mode(request):
    return request.param


async def _accept(message, response_type, params, context):
    return True


def _configure(mcp_module, monkeypatch, space_arg):
    """Real SpaceConfig for one space, mocked Chat API; returns the list of sends."""
    monkeypatch.setattr(mcp_module, "_cfg", SpaceConfig.from_args([space_arg]))
    monkeypatch.setattr(
        mcp_module._chat.spaces,
        "get",
        lambda _: {"spaceType": "SPACE", "displayName": "Team General"},
    )
    sent = []

    def fake_send(space, text):
        sent.append((space, text))
        return {"name": f"{space}/messages/1"}

    monkeypatch.setattr(mcp_module._chat.messages, "send", fake_send)
    return sent


@pytest.mark.anyio
async def test_send_message_shows_space_and_text_in_confirmation(mcp_module, monkeypatch, client_mode):
    sent = _configure(mcp_module, monkeypatch, "spaces/A:w")
    prompts = []

    async def handler(message, response_type, params, context):
        prompts.append(message)
        return True

    async with Client(mcp_module.mcp, elicitation_handler=handler, mode=client_mode) as client:
        await client.call_tool(
            "send_message", {"space_name": "spaces/A", "text": "hello team"}
        )
    assert len(prompts) == 1
    assert "spaces/A" in prompts[0]
    assert "Team General" in prompts[0]
    assert "hello team" in prompts[0]
    assert sent == [("spaces/A", "hello team")]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        ElicitResult(action="decline"),
        ElicitResult(action="cancel"),
        ElicitResult(action="accept", content={"value": False}),
    ],
    ids=["decline", "cancel", "accept-false"],
)
async def test_send_message_not_confirmed_does_not_send(mcp_module, monkeypatch, response, client_mode):
    sent = _configure(mcp_module, monkeypatch, "spaces/A:w")

    async def handler(message, response_type, params, context):
        return response

    async with Client(mcp_module.mcp, elicitation_handler=handler, mode=client_mode) as client:
        with pytest.raises(ToolError, match="did not confirm"):
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )
    assert sent == []


@pytest.mark.anyio
async def test_send_message_without_elicitation_support_refuses(mcp_module, monkeypatch, client_mode):
    sent = _configure(mcp_module, monkeypatch, "spaces/A:rw")
    async with Client(mcp_module.mcp, mode=client_mode) as client:
        with pytest.raises(ToolError, match="does not support elicitation"):
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )
    assert sent == []


@pytest.mark.anyio
async def test_send_message_unattended_sends_without_elicitation(mcp_module, monkeypatch, client_mode):
    sent = _configure(mcp_module, monkeypatch, "spaces/A:w:unattended")
    async with Client(mcp_module.mcp, mode=client_mode) as client:
        await client.call_tool(
            "send_message", {"space_name": "spaces/A", "text": "task done"}
        )
    assert sent == [("spaces/A", "task done")]


@pytest.mark.anyio
async def test_send_message_unattended_never_asks(mcp_module, monkeypatch, client_mode):
    """:unattended applies to every caller, including clients that support elicitation."""
    sent = _configure(mcp_module, monkeypatch, "spaces/A:w:unattended")
    prompts = []

    async def handler(message, response_type, params, context):
        prompts.append(message)
        return True

    async with Client(mcp_module.mcp, elicitation_handler=handler, mode=client_mode) as client:
        await client.call_tool(
            "send_message", {"space_name": "spaces/A", "text": "task done"}
        )
    assert prompts == []
    assert sent == [("spaces/A", "task done")]


@pytest.mark.anyio
async def test_confirmation_is_per_space(mcp_module, monkeypatch, client_mode):
    """In the same configuration, only the space without :unattended asks."""
    sent = _configure(mcp_module, monkeypatch, "spaces/A:w")
    monkeypatch.setattr(
        mcp_module, "_cfg", SpaceConfig.from_args(["spaces/A:w", "spaces/B:w:unattended"])
    )
    prompts = []

    async def handler(message, response_type, params, context):
        prompts.append(message)
        return True

    async with Client(mcp_module.mcp, elicitation_handler=handler, mode=client_mode) as client:
        await client.call_tool("send_message", {"space_name": "spaces/B", "text": "auto"})
        assert prompts == []
        await client.call_tool("send_message", {"space_name": "spaces/A", "text": "manual"})
    assert len(prompts) == 1
    assert "spaces/A" in prompts[0]
    assert sent == [("spaces/B", "auto"), ("spaces/A", "manual")]


@pytest.mark.anyio
async def test_confirmation_is_bound_to_space_and_text(mcp_module, monkeypatch):
    """An answer given for one text does not confirm a different text."""
    from types import SimpleNamespace
    from mcp_types import ElicitResult as WireElicitResult

    monkeypatch.setattr(mcp_module, "_is_modern_protocol", lambda ctx: True)
    ctx = SimpleNamespace(
        input_responses={
            mcp_module._CONFIRM_KEY: WireElicitResult(
                action="accept", content={"value": True}
            )
        },
        request_state=mcp_module._send_digest("spaces/A", "confirmed text"),
    )
    assert await mcp_module._confirm_send(ctx, "spaces/A", {}, "confirmed text") is None
    with pytest.raises(ToolError, match="did not confirm"):
        await mcp_module._confirm_send(ctx, "spaces/A", {}, "other text")
    with pytest.raises(ToolError, match="did not confirm"):
        await mcp_module._confirm_send(ctx, "spaces/B", {}, "confirmed text")


@pytest.mark.anyio
async def test_tool_call_log_includes_protocol_and_elicitation(mcp_module, caplog, client_mode):
    with caplog.at_level("INFO", logger="google-chat-mcp.tools"):
        async with Client(mcp_module.mcp, elicitation_handler=_accept, mode=client_mode) as client:
            await client.call_tool("list_spaces", {})
    from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

    expected = HANDSHAKE_PROTOCOL_VERSIONS if client_mode == "legacy" else MODERN_PROTOCOL_VERSIONS
    line = next(r.getMessage() for r in caplog.records if "tool=list_spaces" in r.getMessage())
    assert any(f"protocol={v} " in line for v in expected)
    assert "elicitation=True" in line


@pytest.mark.anyio
async def test_tool_call_log_without_elicitation_support(mcp_module, caplog, client_mode):
    with caplog.at_level("INFO", logger="google-chat-mcp.tools"):
        async with Client(mcp_module.mcp, mode=client_mode) as client:
            await client.call_tool("list_spaces", {})
    line = next(r.getMessage() for r in caplog.records if "tool=list_spaces" in r.getMessage())
    assert "elicitation=False" in line


def test_init_warns_for_rw_unattended(mcp_module, monkeypatch, caplog):
    # init() replaces _cfg and _chat: restore them for the other tests
    monkeypatch.setattr(mcp_module, "_cfg", mcp_module._cfg)
    monkeypatch.setattr(mcp_module, "_chat", mcp_module._chat)

    with caplog.at_level("WARNING", logger="google-chat-mcp.tools"):
        mcp_module.init(["spaces/A:rw:unattended", "spaces/B:w:unattended", "spaces/C:rw"])
    warned = [r.getMessage() for r in caplog.records]
    assert len(warned) == 1
    assert "spaces/A" in warned[0]


# --- API error propagation ---


@pytest.mark.anyio
async def test_chat_api_error_diventa_tool_error(mcp_module, monkeypatch):
    def raise_api_error(space, **kw):
        raise ChatAPIError(500, "Internal Server Error")

    monkeypatch.setattr(mcp_module._cfg, "require_read", lambda _: None)
    monkeypatch.setattr(mcp_module._chat.messages, "list", raise_api_error)
    async with Client(mcp_module.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("list_messages", {"space_name": "spaces/A"})


# --- Lazy loading e auth hardening ---


def test_init_does_not_create_chat_client(monkeypatch, tmp_path):
    """init() must not touch the credentials: the server starts even without a token."""
    import importlib
    import google_chat_mcp.server as srv
    importlib.reload(srv)

    # No token.json present
    monkeypatch.setattr("google_chat_mcp.auth.TOKEN_PATH", tmp_path / "nonexistent.json")
    srv.init(["spaces/AAA:rw"])  # should not raise
    assert srv._chat is None


@pytest.mark.anyio
async def test_tool_senza_token_restituisce_tool_error(monkeypatch, tmp_path):
    """A tool that requires the API fails with a readable ToolError if the token is missing."""
    import importlib
    import google_chat_mcp.server as srv
    importlib.reload(srv)

    monkeypatch.setattr("google_chat_mcp.auth.TOKEN_PATH", tmp_path / "nonexistent.json")
    srv.init(["spaces/AAA:r"])

    async with Client(srv.mcp) as client:
        with pytest.raises(ToolError, match="auth"):
            await client.call_tool("get_space", {"space_name": "spaces/AAA"})


# --- chat_auth_status ---


def test_auth_status_missing_token(monkeypatch, tmp_path, mcp_module):
    monkeypatch.setattr("google_chat_mcp.server.TOKEN_PATH", tmp_path / "nonexistent.json")
    result = mcp_module.chat_auth_status()
    assert result["token_exists"] is False
    assert result["warning"] is not None
    assert "remedy" in result


def test_auth_status_legacy_token_without_authorized_at(monkeypatch, tmp_path, mcp_module):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"refresh_token": "xxx"}))
    monkeypatch.setattr("google_chat_mcp.server.TOKEN_PATH", token_file)
    result = mcp_module.chat_auth_status()
    assert result["token_exists"] is True
    assert result["authorized_at"] is None
    assert "authorized_at" in result["warning"]


def test_auth_status_recent_token(monkeypatch, tmp_path, mcp_module):
    from datetime import datetime, timezone, timedelta
    authorized_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"refresh_token": "xxx", "authorized_at": authorized_at}))
    monkeypatch.setattr("google_chat_mcp.server.TOKEN_PATH", token_file)
    result = mcp_module.chat_auth_status()
    assert result["days_since_consent"] == 2
    assert result["warning"] is None


def test_auth_status_token_close_to_expiry(monkeypatch, tmp_path, mcp_module):
    from datetime import datetime, timezone, timedelta
    authorized_at = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"refresh_token": "xxx", "authorized_at": authorized_at}))
    monkeypatch.setattr("google_chat_mcp.server.TOKEN_PATH", token_file)
    result = mcp_module.chat_auth_status()
    assert result["days_since_consent"] >= 6
    assert result["warning"] is not None
