"""
Server MCP per Google Chat, basato su FastMCP.

Espone sei tool:
  list_spaces      — spazi configurati con i loro permessi
  get_space        — dettagli di uno spazio (richiede read)
  list_messages    — messaggi di uno spazio (richiede read)
  send_message     — invia un messaggio a uno spazio (richiede write, blocca i DM)
  list_members     — membri di uno spazio (richiede read)
  chat_auth_status — stato del token OAuth (funziona anche senza token valido)

I permessi per spazio vengono passati come argomenti CLI al momento dell'avvio:
  google-chat-mcp --space spaces/AAA:rw --space spaces/BBB:r
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

from .auth import TOKEN_PATH, _REMEDY
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
    _chat = None  # lazy: caricato alla prima chiamata di tool che serve l'API


def _ensure_chat() -> ChatClient:
    """Restituisce il ChatClient, creandolo alla prima chiamata (caricamento credenziali lazy).

    Se il token è assente o scaduto, solleva RuntimeError con il comando di rimedio.
    """
    global _chat
    if _chat is None:
        _chat = ChatClient()
    return _chat


@mcp.tool()
def chat_auth_status() -> dict:
    """Returns the OAuth token status without requiring valid credentials.

    Useful for diagnosing auth issues before calling other tools.
    """
    result: dict = {
        "token_path": str(TOKEN_PATH),
        "token_exists": TOKEN_PATH.exists(),
        "refresh_token_present": False,
        "authorized_at": None,
        "days_since_consent": None,
        "warning": None,
        "remedy": _REMEDY,
    }

    if not TOKEN_PATH.exists():
        result["warning"] = "token.json not found. Run the command in 'remedy'."
        return result

    try:
        data = json.loads(TOKEN_PATH.read_text())
    except Exception:
        result["warning"] = "token.json is unreadable or malformed. Run the command in 'remedy'."
        return result

    result["refresh_token_present"] = bool(data.get("refresh_token"))

    authorized_at_str = data.get("authorized_at")
    if authorized_at_str:
        try:
            authorized_at = datetime.fromisoformat(authorized_at_str)
            result["authorized_at"] = authorized_at_str
            days = (datetime.now(timezone.utc) - authorized_at).days
            result["days_since_consent"] = days
            if days >= 6:
                result["warning"] = (
                    f"Consent given {days} days ago. In Testing mode, Google invalidates "
                    "refresh tokens after 7 days. Run the command in 'remedy' before "
                    "the server stops working."
                )
        except ValueError:
            result["warning"] = "Invalid 'authorized_at' field in token.json."
    else:
        result["warning"] = (
            "'authorized_at' field missing (token issued before the update). "
            "Consent date unknown: expiry cannot be estimated."
        )

    return result


@mcp.tool()
def list_spaces() -> list[dict]:
    """Lists the configured Google Chat spaces with their permissions (r=read, w=write)."""
    return _cfg.list_spaces()


@mcp.tool()
def get_space(space_name: str) -> dict:
    """Returns the details of a space (requires read permission).

    Args:
        space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
    """
    _cfg.require_read(space_name)
    return _ensure_chat().spaces.get(space_name)


@mcp.tool()
def list_messages(space_name: str, page_size: int = 25, filter: str = "") -> list[dict]:
    """Lists the messages in a space (requires read permission).

    Args:
        space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
        page_size: maximum number of messages (default 25, max 1000)
        filter: optional filter, e.g. 'createTime > "2024-01-01T00:00:00Z"'
    """
    _cfg.require_read(space_name)
    return _ensure_chat().messages.list(space_name, page_size=page_size, filter_str=filter or None)


@mcp.tool()
def send_message(space_name: str, text: str) -> dict:
    """Sends a message to a space (requires write permission). Does not send DMs.

    Args:
        space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
        text: message text
    """
    _cfg.require_write(space_name)
    chat = _ensure_chat()
    space = chat.spaces.get(space_name)
    space_type = space.get("spaceType", "")
    if space_type in ("DIRECT_MESSAGE", "GROUP_CHAT"):
        raise ValueError(
            f"Sending messages to direct conversations is not allowed "
            f"(spaceType={space_type!r}). Use named spaces only."
        )
    return chat.messages.send(space_name, text)


@mcp.tool()
def list_members(space_name: str) -> list[dict]:
    """Lists the members of a space (requires read permission).

    Args:
        space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
    """
    _cfg.require_read(space_name)
    return _ensure_chat().members.list(space_name)
