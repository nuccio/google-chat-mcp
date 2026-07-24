"""
Server MCP per Google Chat, basato su FastMCP.

Espone cinque tool:
  list_spaces    — spazi configurati con i loro permessi
  get_space      — dettagli di uno spazio (richiede read)
  list_messages  — messaggi di uno spazio (richiede read)
  send_message   — invia un messaggio a uno spazio (richiede write, blocca i DM)
  list_members   — membri di uno spazio (richiede read)

I permessi per spazio vengono passati come argomenti CLI al momento dell'avvio:
  google-chat-mcp --space spaces/AAA:rw --space spaces/BBB:r
"""

import logging

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

from .chat import ChatClient, ChatAPIError
from .config import SpaceConfig, PermissionDeniedError

mcp = FastMCP("google-chat")
_log = logging.getLogger("google-chat-mcp.tools")

_cfg: SpaceConfig = SpaceConfig([])
_chat: ChatClient | None = None


class _LoggingMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        msg = context.message
        _log.info("tool=%s args=%s", msg.name, msg.arguments or {})
        return await call_next(context)


mcp.add_middleware(_LoggingMiddleware())


def init(space_args: list[str]) -> None:
    global _cfg, _chat
    _cfg = SpaceConfig.from_args(space_args)
    _chat = ChatClient()


@mcp.tool()
def list_spaces() -> list[dict]:
    """Elenca gli spazi Google Chat configurati con i loro permessi (r=lettura, w=scrittura)."""
    return _cfg.list_spaces()


@mcp.tool()
def get_space(space_name: str) -> dict:
    """Restituisce i dettagli di uno spazio (richiede permesso di lettura).

    Args:
        space_name: resource name dello spazio, es. 'spaces/AAABBBCCC'
    """
    _cfg.require_read(space_name)
    return _chat.spaces.get(space_name)


@mcp.tool()
def list_messages(space_name: str, page_size: int = 25, filter: str = "") -> list[dict]:
    """Elenca i messaggi di uno spazio (richiede permesso di lettura).

    Args:
        space_name: resource name dello spazio, es. 'spaces/AAABBBCCC'
        page_size: numero massimo di messaggi (default 25, max 1000)
        filter: filtro opzionale, es. 'createTime > "2024-01-01T00:00:00Z"'
    """
    _cfg.require_read(space_name)
    return _chat.messages.list(space_name, page_size=page_size, filter_str=filter or None)


@mcp.tool()
def send_message(space_name: str, text: str) -> dict:
    """Invia un messaggio in uno spazio (richiede permesso di scrittura). Non invia DM.

    Args:
        space_name: resource name dello spazio, es. 'spaces/AAABBBCCC'
        text: testo del messaggio
    """
    _cfg.require_write(space_name)
    space = _chat.spaces.get(space_name)
    space_type = space.get("spaceType", "")
    if space_type in ("DIRECT_MESSAGE", "GROUP_CHAT"):
        raise ValueError(
            f"Invio messaggi a conversazioni dirette non consentito "
            f"(spaceType={space_type!r}). Usa solo spazi nominati."
        )
    return _chat.messages.send(space_name, text)


@mcp.tool()
def list_members(space_name: str) -> list[dict]:
    """Elenca i membri di uno spazio (richiede permesso di lettura).

    Args:
        space_name: resource name dello spazio, es. 'spaces/AAABBBCCC'
    """
    _cfg.require_read(space_name)
    return _chat.members.list(space_name)
