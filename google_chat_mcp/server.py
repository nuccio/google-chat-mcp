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
    """Restituisce lo stato del token OAuth senza richiedere credenziali valide.

    Utile per diagnosticare problemi di autenticazione prima di chiamare altri tool.
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
        result["warning"] = "token.json non trovato. Esegui il comando in 'remedy'."
        return result

    try:
        data = json.loads(TOKEN_PATH.read_text())
    except Exception:
        result["warning"] = "token.json non leggibile o malformato. Esegui il comando in 'remedy'."
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
                    f"Consenso dato {days} giorni fa. In modalità Testing, Google invalida "
                    "i refresh token dopo 7 giorni. Esegui il comando in 'remedy' prima che "
                    "il server smetta di funzionare."
                )
        except ValueError:
            result["warning"] = "Campo 'authorized_at' non valido nel token.json."
    else:
        result["warning"] = (
            "Campo 'authorized_at' assente (token emesso prima dell'aggiornamento). "
            "Data del consenso sconosciuta: impossibile stimare la scadenza."
        )

    return result


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
    return _ensure_chat().spaces.get(space_name)


@mcp.tool()
def list_messages(space_name: str, page_size: int = 25, filter: str = "") -> list[dict]:
    """Elenca i messaggi di uno spazio (richiede permesso di lettura).

    Args:
        space_name: resource name dello spazio, es. 'spaces/AAABBBCCC'
        page_size: numero massimo di messaggi (default 25, max 1000)
        filter: filtro opzionale, es. 'createTime > "2024-01-01T00:00:00Z"'
    """
    _cfg.require_read(space_name)
    return _ensure_chat().messages.list(space_name, page_size=page_size, filter_str=filter or None)


@mcp.tool()
def send_message(space_name: str, text: str) -> dict:
    """Invia un messaggio in uno spazio (richiede permesso di scrittura). Non invia DM.

    Args:
        space_name: resource name dello spazio, es. 'spaces/AAABBBCCC'
        text: testo del messaggio
    """
    _cfg.require_write(space_name)
    chat = _ensure_chat()
    space = chat.spaces.get(space_name)
    space_type = space.get("spaceType", "")
    if space_type in ("DIRECT_MESSAGE", "GROUP_CHAT"):
        raise ValueError(
            f"Invio messaggi a conversazioni dirette non consentito "
            f"(spaceType={space_type!r}). Usa solo spazi nominati."
        )
    return chat.messages.send(space_name, text)


@mcp.tool()
def list_members(space_name: str) -> list[dict]:
    """Elenca i membri di uno spazio (richiede permesso di lettura).

    Args:
        space_name: resource name dello spazio, es. 'spaces/AAABBBCCC'
    """
    _cfg.require_read(space_name)
    return _ensure_chat().members.list(space_name)
