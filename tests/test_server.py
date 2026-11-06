"""
Tests for the MCP server tools (google_chat_mcp/tools.py) and the local
builder (google_chat_mcp/server.py).

Verifies:
- which tools are exposed and that they all have a description
- a real round-trip through the MCP protocol (FastMCP Client)
- that permission checks produce a ToolError via MCP
- that sends are allowed to named spaces only (DMs and group chats are blocked)
- that ChatAPIError errors are propagated as ToolError
- that send_message asks for confirmation (elicitation) unless the space is :unattended
- that tools.py imports only core modules (AST check)
"""

import ast
import importlib
import json
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError

from google_chat_mcp.auth import SCOPES, LocalChatSource
from google_chat_mcp.chat.client import ChatAPIError
from google_chat_mcp.config import acl_from_args
from google_chat_mcp.permissions import Acl, Permissions

EXPECTED_TOOLS = {
    "list_spaces",
    "get_space",
    "list_messages",
    "send_message",
    "send_message_unattended",
    "list_members",
    "chat_auth_status",
}


# ---------------------------------------------------------------------------
# AST check: tools.py must not import auth, config, remote or settings
# ---------------------------------------------------------------------------


def test_tools_module_imports_only_core():
    tools_path = Path(__file__).parent.parent / "google_chat_mcp" / "tools.py"
    tree = ast.parse(tools_path.read_text())
    forbidden_prefixes = (
        "google_chat_mcp.auth",
        "google_chat_mcp.config",
        "google_chat_mcp.remote",
        "google_chat_mcp.settings",
    )
    forbidden_relative = ("auth", "config", "remote", "settings")
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name.startswith(p) for p in forbidden_prefixes):
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # absolute imports
            if any(module.startswith(p) for p in forbidden_prefixes):
                violations.append(module)
            # relative imports (level > 0, module is the relative part)
            if node.level and node.level > 0 and module in forbidden_relative:
                violations.append(f".{module}")
    assert not violations, f"tools.py must not import: {violations}"


def test_tools_module_does_not_read_environ_or_argv():
    tools_path = Path(__file__).parent.parent / "google_chat_mcp" / "tools.py"
    tree = ast.parse(tools_path.read_text())
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id == "os" and node.attr == "environ") or (
                node.value.id == "sys" and node.attr == "argv"
            ):
                violations.append(f"{node.value.id}.{node.attr}")
    assert not violations, f"tools.py must not access: {violations}"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_espone_esattamente_i_tool_attesi(mode):
    async with Client(mode.mcp) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


@pytest.mark.anyio
async def test_ogni_tool_ha_una_descrizione(mode):
    async with Client(mode.mcp) as client:
        tools = await client.list_tools()
    missing = [t.name for t in tools if not t.description]
    assert not missing, f"Tool without a description: {missing}"


# ---------------------------------------------------------------------------
# list_spaces
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_spaces_uses_acl(mode):
    mode.provider.acl_value = Acl({"spaces/X": Permissions(read=True, write=False)})
    async with Client(mode.mcp) as client:
        result = await client.call_tool("list_spaces", {})
    assert result.data == [{"name": "spaces/X", "read": True, "write": False}]


# ---------------------------------------------------------------------------
# list_messages: round-trip MCP
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_messages_round_trip(mode):
    fake_page = {
        "messages": [{"name": "spaces/A/messages/1", "text": "hello"}],
        "next_page_token": "tok",
    }
    mode.provider.acl_value = Acl({"spaces/A": Permissions(read=True)})
    mode.fake_chat.messages.list.return_value = fake_page
    async with Client(mode.mcp) as client:
        result = await client.call_tool("list_messages", {"space_name": "spaces/A"})
    assert result.data == fake_page


@pytest.mark.anyio
async def test_list_messages_passes_page_token(mode):
    calls = []

    def fake_list(space, **kw):
        calls.append(kw)
        return {"messages": [], "next_page_token": None}

    mode.provider.acl_value = Acl({"spaces/A": Permissions(read=True)})
    mode.fake_chat.messages.list.side_effect = fake_list
    async with Client(mode.mcp) as client:
        await client.call_tool("list_messages", {"space_name": "spaces/A"})
        await client.call_tool(
            "list_messages", {"space_name": "spaces/A", "page_token": "tok"}
        )
    assert [c["page_token"] for c in calls] == [None, "tok"]


@pytest.mark.anyio
async def test_list_messages_page_token_does_not_skip_read_check(mode):
    async with Client(mode.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "list_messages", {"space_name": "spaces/A", "page_token": "tok"}
            )
    mode.fake_chat.messages.list.assert_not_called()


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_message_write_false_solleva_tool_error(mode):
    async with Client(mode.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )


@pytest.mark.anyio
async def test_get_space_read_false_solleva_tool_error(mode):
    async with Client(mode.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_space", {"space_name": "spaces/A"})


# ---------------------------------------------------------------------------
# DM blocking
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_message_blocca_direct_message(mode):
    mode.provider.acl_value = acl_from_args(["spaces/A:w"])
    mode.fake_chat.spaces.get.return_value = {"spaceType": "DIRECT_MESSAGE"}
    async with Client(mode.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )


@pytest.mark.anyio
async def test_send_message_blocca_group_chat(mode):
    mode.provider.acl_value = acl_from_args(["spaces/A:w"])
    mode.fake_chat.spaces.get.return_value = {"spaceType": "GROUP_CHAT"}
    async with Client(mode.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )


@pytest.mark.anyio
async def test_send_message_space_type_space_ok(mode):
    fake_result = {"name": "spaces/A/messages/42"}
    mode.provider.acl_value = acl_from_args(["spaces/A:w"])
    mode.fake_chat.spaces.get.return_value = {"spaceType": "SPACE"}
    mode.fake_chat.messages.send.return_value = fake_result
    async with Client(mode.mcp, elicitation_handler=_accept) as client:
        result = await client.call_tool(
            "send_message", {"space_name": "spaces/A", "text": "hello"}
        )
    assert result.data == fake_result


# ---------------------------------------------------------------------------
# send_message: confirmation (elicitation)
#
# Each test runs on both protocol eras: "legacy" (the server pushes an
# elicitation/create request) and "auto" (2026-07-28: the tool returns an
# InputRequiredResult and the client retries with the answer).
# ---------------------------------------------------------------------------


@pytest.fixture(params=["legacy", "auto"])
def client_mode(request):
    return request.param


async def _accept(message, response_type, params, context):
    return True


def _configure(mode, space_arg):
    """Real Acl for one space, mocked Chat API; returns the list of sends."""
    mode.provider.acl_value = acl_from_args([space_arg])
    mode.fake_chat.spaces.get.return_value = {
        "spaceType": "SPACE",
        "displayName": "Team General",
    }
    sent = []

    def fake_send(space, text):
        sent.append((space, text))
        return {"name": f"{space}/messages/1"}

    mode.fake_chat.messages.send.side_effect = fake_send
    return sent


@pytest.mark.anyio
async def test_send_message_shows_space_and_text_in_confirmation(mode, client_mode):
    sent = _configure(mode, "spaces/A:w")
    prompts = []

    async def handler(message, response_type, params, context):
        prompts.append(message)
        return True

    async with Client(mode.mcp, elicitation_handler=handler, mode=client_mode) as client:
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
async def test_send_message_not_confirmed_does_not_send(mode, response, client_mode):
    sent = _configure(mode, "spaces/A:w")

    async def handler(message, response_type, params, context):
        return response

    async with Client(mode.mcp, elicitation_handler=handler, mode=client_mode) as client:
        with pytest.raises(ToolError, match="did not confirm"):
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )
    assert sent == []


@pytest.mark.anyio
async def test_send_message_without_elicitation_support_sends(mode, client_mode, caplog):
    """Without elicitation the server relies on the client's tool approval and sends."""
    sent = _configure(mode, "spaces/A:rw")
    with caplog.at_level("INFO", logger="google-chat-mcp.tools"):
        async with Client(mode.mcp, mode=client_mode) as client:
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )
    assert sent == [("spaces/A", "test")]
    assert any(
        "without server-side confirmation" in r.getMessage() for r in caplog.records
    )


@pytest.mark.anyio
async def test_send_message_refuses_unattended_space(mode, client_mode):
    sent = _configure(mode, "spaces/A:w:unattended")
    async with Client(mode.mcp, elicitation_handler=_accept, mode=client_mode) as client:
        with pytest.raises(ToolError, match="use send_message_unattended"):
            await client.call_tool(
                "send_message", {"space_name": "spaces/A", "text": "test"}
            )
    assert sent == []


@pytest.mark.anyio
async def test_send_message_unattended_refuses_space_requiring_confirmation(mode, client_mode):
    """send_message_unattended cannot be used to skip the approval of send_message."""
    sent = _configure(mode, "spaces/A:rw")
    async with Client(mode.mcp, mode=client_mode) as client:
        with pytest.raises(ToolError, match="use send_message to post"):
            await client.call_tool(
                "send_message_unattended", {"space_name": "spaces/A", "text": "test"}
            )
    assert sent == []


@pytest.mark.anyio
async def test_send_message_unattended_sends_without_elicitation(mode, client_mode):
    sent = _configure(mode, "spaces/A:w:unattended")
    async with Client(mode.mcp, mode=client_mode) as client:
        await client.call_tool(
            "send_message_unattended", {"space_name": "spaces/A", "text": "task done"}
        )
    assert sent == [("spaces/A", "task done")]


@pytest.mark.anyio
async def test_send_message_unattended_never_asks(mode, client_mode):
    """:unattended applies to every caller, including clients that support elicitation."""
    sent = _configure(mode, "spaces/A:w:unattended")
    prompts = []

    async def handler(message, response_type, params, context):
        prompts.append(message)
        return True

    async with Client(mode.mcp, elicitation_handler=handler, mode=client_mode) as client:
        await client.call_tool(
            "send_message_unattended", {"space_name": "spaces/A", "text": "task done"}
        )
    assert prompts == []
    assert sent == [("spaces/A", "task done")]


@pytest.mark.anyio
@pytest.mark.parametrize("space_type", ["DIRECT_MESSAGE", "GROUP_CHAT"])
async def test_send_message_unattended_blocks_direct_conversations(mode, space_type):
    _configure(mode, "spaces/A:w:unattended")
    mode.fake_chat.spaces.get.return_value = {"spaceType": space_type}
    async with Client(mode.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "send_message_unattended", {"space_name": "spaces/A", "text": "test"}
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tool, space_arg",
    [("send_message", "spaces/A:w"), ("send_message_unattended", "spaces/A:w:unattended")],
    ids=["send_message", "send_message_unattended"],
)
@pytest.mark.parametrize(
    "space",
    [
        {},
        {"spaceType": ""},
        {"spaceType": "SPACE_TYPE_UNSPECIFIED"},
        {"spaceType": "SOME_FUTURE_TYPE"},
        {"type": "ROOM"},
    ],
    ids=["missing", "empty", "unspecified", "unknown", "deprecated-type-only"],
)
async def test_send_refuses_space_not_known_to_be_named(mode, tool, space_arg, space):
    _configure(mode, space_arg)
    mode.fake_chat.spaces.get.return_value = space
    prompts = []

    async def handler(message, response_type, params, context):
        prompts.append(message)
        return True

    async with Client(mode.mcp, elicitation_handler=handler) as client:
        with pytest.raises(ToolError, match="named spaces only"):
            await client.call_tool(tool, {"space_name": "spaces/A", "text": "test"})
    assert prompts == []


@pytest.mark.anyio
async def test_confirmation_is_per_space(mode, client_mode):
    """In the same configuration, only the space without :unattended asks."""
    _configure(mode, "spaces/A:w")
    mode.provider.acl_value = acl_from_args(["spaces/A:w", "spaces/B:w:unattended"])
    prompts = []

    async def handler(message, response_type, params, context):
        prompts.append(message)
        return True

    async with Client(mode.mcp, elicitation_handler=handler, mode=client_mode) as client:
        await client.call_tool(
            "send_message_unattended", {"space_name": "spaces/B", "text": "auto"}
        )
        assert prompts == []
        await client.call_tool(
            "send_message", {"space_name": "spaces/A", "text": "manual"}
        )
    assert len(prompts) == 1
    assert "spaces/A" in prompts[0]


@pytest.mark.anyio
async def test_tool_annotations(mode):
    async with Client(mode.mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in ("list_spaces", "get_space", "list_messages", "list_members", "chat_auth_status"):
        assert tools[name].annotations.read_only_hint is True, name
    for name in ("send_message", "send_message_unattended"):
        assert tools[name].annotations.read_only_hint is False, name
        assert tools[name].annotations.destructive_hint is False, name


@pytest.mark.anyio
async def test_confirmation_is_bound_to_space_and_text():
    """An answer given for one text does not confirm a different text."""
    import google_chat_mcp.tools as tools
    from types import SimpleNamespace
    from mcp_types import ElicitResult as WireElicitResult

    ctx = SimpleNamespace(
        input_responses={
            tools._CONFIRM_KEY: WireElicitResult(
                action="accept", content={"value": True}
            )
        },
        request_state=tools._send_digest("spaces/A", "confirmed text"),
    )

    orig = tools._is_modern_protocol
    tools._is_modern_protocol = lambda _: True
    try:
        assert await tools._confirm_send(ctx, "spaces/A", {}, "confirmed text") is None
        with pytest.raises(ToolError, match="did not confirm"):
            await tools._confirm_send(ctx, "spaces/A", {}, "other text")
        with pytest.raises(ToolError, match="did not confirm"):
            await tools._confirm_send(ctx, "spaces/B", {}, "confirmed text")
    finally:
        tools._is_modern_protocol = orig


@pytest.mark.anyio
async def test_tool_call_log_includes_protocol_and_elicitation(mode, caplog, client_mode):
    with caplog.at_level("INFO", logger="google-chat-mcp.tools"):
        async with Client(mode.mcp, elicitation_handler=_accept, mode=client_mode) as client:
            await client.call_tool("list_spaces", {})
    from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

    expected = HANDSHAKE_PROTOCOL_VERSIONS if client_mode == "legacy" else MODERN_PROTOCOL_VERSIONS
    line = next(r.getMessage() for r in caplog.records if "tool=list_spaces" in r.getMessage())
    assert any(f"protocol={v} " in line for v in expected)
    assert "elicitation=True" in line


@pytest.mark.anyio
async def test_tool_call_log_without_elicitation_support(mode, caplog, client_mode):
    with caplog.at_level("INFO", logger="google-chat-mcp.tools"):
        async with Client(mode.mcp, mode=client_mode) as client:
            await client.call_tool("list_spaces", {})
    line = next(r.getMessage() for r in caplog.records if "tool=list_spaces" in r.getMessage())
    assert "elicitation=False" in line


@pytest.mark.anyio
async def test_tool_call_log_masks_only_email_arguments(mode, monkeypatch, caplog):
    import google_chat_mcp.tools as tools
    monkeypatch.setattr(tools, "_EMAIL_ARGUMENTS", frozenset({"filter"}))
    mode.provider.acl_value = Acl({"spaces/A": Permissions(read=True)})
    mode.fake_chat.messages.list.return_value = {"messages": []}
    with caplog.at_level("INFO", logger="google-chat-mcp.tools"):
        async with Client(mode.mcp) as client:
            await client.call_tool(
                "list_messages",
                {"space_name": "spaces/A", "filter": "not even an address"},
            )
    line = next(r.getMessage() for r in caplog.records if "tool=list_messages" in r.getMessage())
    assert "'filter': '[email]'" in line
    assert "not even an address" not in line
    assert "'space_name': 'spaces/A'" in line


@pytest.mark.anyio
async def test_tool_call_log_keeps_arguments_by_default(mode, caplog):
    mode.provider.acl_value = Acl({"spaces/A": Permissions(read=True)})
    mode.fake_chat.messages.list.return_value = {"messages": []}
    with caplog.at_level("INFO", logger="google-chat-mcp.tools"):
        async with Client(mode.mcp) as client:
            await client.call_tool(
                "list_messages", {"space_name": "spaces/A", "filter": "some filter"}
            )
    line = next(r.getMessage() for r in caplog.records if "tool=list_messages" in r.getMessage())
    assert "'filter': 'some filter'" in line
    assert "'space_name': 'spaces/A'" in line


# ---------------------------------------------------------------------------
# API error propagation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_chat_api_error_diventa_tool_error(mode):
    mode.provider.acl_value = Acl({"spaces/A": Permissions(read=True)})
    mode.fake_chat.messages.list.side_effect = ChatAPIError(500, "Internal Server Error")
    async with Client(mode.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("list_messages", {"space_name": "spaces/A"})


# ---------------------------------------------------------------------------
# Local builder (server.py): lazy loading and init()
# ---------------------------------------------------------------------------


def test_init_does_not_create_chat_client(monkeypatch, tmp_path):
    """init() must not touch credentials: the server starts even without a token."""
    import google_chat_mcp.server as srv
    importlib.reload(srv)

    monkeypatch.setattr("google_chat_mcp.auth.TOKEN_PATH", tmp_path / "nonexistent.json")
    srv.init(["spaces/AAA:rw"])  # LocalChatSource is lazy; no credentials loaded yet


@pytest.mark.anyio
async def test_tool_senza_token_restituisce_tool_error(monkeypatch, tmp_path):
    """A tool that requires the API fails with a readable ToolError if the token is missing."""
    import google_chat_mcp.server as srv
    importlib.reload(srv)

    monkeypatch.setattr("google_chat_mcp.auth.TOKEN_PATH", tmp_path / "nonexistent.json")
    srv.init(["spaces/AAA:r"])

    async with Client(srv.mcp) as client:
        with pytest.raises(ToolError, match="auth"):
            await client.call_tool("get_space", {"space_name": "spaces/AAA"})


def test_init_warns_for_rw_unattended(caplog):
    import google_chat_mcp.server as srv
    importlib.reload(srv)

    with caplog.at_level("WARNING", logger="google-chat-mcp.tools"):
        srv.init(["spaces/A:rw:unattended", "spaces/B:w:unattended", "spaces/C:rw"])
    warned = [r.getMessage() for r in caplog.records]
    assert len(warned) == 1
    assert "spaces/A" in warned[0]


# ---------------------------------------------------------------------------
# chat_auth_status via LocalChatSource (tests the token-reading logic)
# ---------------------------------------------------------------------------


def test_auth_status_missing_token(monkeypatch, tmp_path):
    monkeypatch.setattr("google_chat_mcp.auth.TOKEN_PATH", tmp_path / "nonexistent.json")
    result = LocalChatSource().auth_status()
    assert result["token_exists"] is False
    assert result["warning"] is not None
    assert "remedy" in result


def test_auth_status_legacy_token_without_authorized_at(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"refresh_token": "xxx", "scopes": SCOPES}))
    monkeypatch.setattr("google_chat_mcp.auth.TOKEN_PATH", token_file)
    result = LocalChatSource().auth_status()
    assert result["token_exists"] is True
    assert result["authorized_at"] is None
    assert "authorized_at" in result["warning"]


def test_auth_status_recent_token(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta
    authorized_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "refresh_token": "xxx",
        "authorized_at": authorized_at,
        "scopes": SCOPES,
    }))
    monkeypatch.setattr("google_chat_mcp.auth.TOKEN_PATH", token_file)
    result = LocalChatSource().auth_status()
    assert result["days_since_consent"] == 2
    assert result["warning"] is None


def test_auth_status_token_close_to_expiry(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta
    authorized_at = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "refresh_token": "xxx",
        "authorized_at": authorized_at,
        "scopes": SCOPES,
    }))
    monkeypatch.setattr("google_chat_mcp.auth.TOKEN_PATH", token_file)
    result = LocalChatSource().auth_status()
    assert result["days_since_consent"] >= 6
    assert result["warning"] is not None


def test_auth_status_token_with_old_scopes(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "refresh_token": "xxx",
        "authorized_at": datetime.now(timezone.utc).isoformat(),
        "scopes": ["https://www.googleapis.com/auth/chat.messages"],
    }))
    monkeypatch.setattr("google_chat_mcp.auth.TOKEN_PATH", token_file)
    result = LocalChatSource().auth_status()
    assert "scopes" in result["warning"]
