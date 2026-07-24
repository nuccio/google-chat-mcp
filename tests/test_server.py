"""
Test del server MCP (google_chat_mcp/server.py).

Verifica:
- quali tool sono esposti e che abbiano tutti una descrizione
- round-trip reale attraverso il protocollo MCP (FastMCP Client)
- che i controlli di permesso producano ToolError via MCP
- che i DM vengano bloccati
- che gli errori ChatAPIError vengano propagati come ToolError
"""

import pytest
from fastmcp import Client
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
}


# --- Registrazione tool ---


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
    assert not missing, f"Tool senza descrizione: {missing}"


# --- list_spaces ---


def test_list_spaces_delega_a_cfg(mcp_module, monkeypatch):
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


# --- Controllo permessi ---


@pytest.mark.anyio
async def test_send_message_write_false_solleva_tool_error(mcp_module, monkeypatch):
    from google_chat_mcp.config import PermissionDeniedError

    def deny(_):
        raise PermissionDeniedError("negato")

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
        raise PermissionDeniedError("negato")

    monkeypatch.setattr(mcp_module._cfg, "require_read", deny)
    async with Client(mcp_module.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_space", {"space_name": "spaces/A"})


# --- Blocco DM ---


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
    async with Client(mcp_module.mcp) as client:
        result = await client.call_tool(
            "send_message", {"space_name": "spaces/A", "text": "ciao"}
        )
    assert result.data == fake_result


# --- Propagazione errori API ---


@pytest.mark.anyio
async def test_chat_api_error_diventa_tool_error(mcp_module, monkeypatch):
    def raise_api_error(space, **kw):
        raise ChatAPIError(500, "Internal Server Error")

    monkeypatch.setattr(mcp_module._cfg, "require_read", lambda _: None)
    monkeypatch.setattr(mcp_module._chat.messages, "list", raise_api_error)
    async with Client(mcp_module.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("list_messages", {"space_name": "spaces/A"})
