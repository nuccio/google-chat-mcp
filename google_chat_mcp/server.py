"""
MCP server for Google Chat, built on FastMCP.

Exposes seven tools:
  list_spaces             — configured spaces with their permissions
  get_space               — details of a space (requires read)
  list_messages           — messages of a space (requires read)
  send_message            — sends to a space without :unattended (requires
                            write, blocks DMs; asks the user to confirm when the
                            client supports elicitation)
  send_message_unattended — sends to a :unattended space, no confirmation
                            (requires write, blocks DMs)
  list_members            — members of a space (requires read)
  chat_auth_status        — OAuth token status (works even without a valid token)

send_message and send_message_unattended are separate so that MCP clients whose
only per-call gate is a per-tool approval setting (e.g. Claude Desktop) can
require approval for one and allow the other.

Per-space permissions are passed as CLI arguments at startup:
  google-chat-mcp --space spaces/AAA:rw --space spaces/BBB:r --space spaces/CCC:w:unattended
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation, parse_elicit_response_type
from fastmcp.server.middleware import Middleware
from mcp_types import (
    ClientCapabilities,
    ToolAnnotations,
    ElicitationCapability,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

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
        ctx = context.fastmcp_context
        protocol = elicitation = "?"
        if ctx is not None:
            try:
                protocol = ctx.session.protocol_version
                elicitation = _client_supports_elicitation(ctx)
            except RuntimeError:  # no active session
                pass
        _log.info(
            "tool=%s protocol=%s elicitation=%s args=%s",
            msg.name, protocol, elicitation, msg.arguments or {},
        )
        return await call_next(context)


mcp.add_middleware(_LoggingMiddleware())


def init(space_args: list[str]) -> None:
    global _cfg, _chat
    _cfg = SpaceConfig.from_args(space_args)
    _chat = None  # lazy: created on the first tool call that needs the API
    for name in _cfg.readable_unattended_spaces():
        _log.warning(
            "Space %s is configured :rw:unattended: content read from it can "
            "influence messages posted without confirmation.",
            name,
        )


def _ensure_chat() -> ChatClient:
    """Returns the ChatClient, creating it on first call (lazy credential loading).

    If the token is missing or expired, raises RuntimeError with the remedy command.
    """
    global _chat
    if _chat is None:
        _chat = ChatClient()
    return _chat


_CONFIRM_KEY = "confirm_send"


def _client_supports_elicitation(ctx: Context) -> bool:
    return ctx.session.check_client_capability(
        ClientCapabilities(elicitation=ElicitationCapability())
    )


def _is_modern_protocol(ctx: Context) -> bool:
    """True on 2026-07-28+ connections, where the server cannot push an
    elicitation request and must return an InputRequiredResult instead."""
    return ctx.session.protocol_version in MODERN_PROTOCOL_VERSIONS


def _send_digest(space_name: str, text: str) -> str:
    """Binds a confirmation to the exact space and text it was given for."""
    return hashlib.sha256(json.dumps([space_name, text]).encode()).hexdigest()


def _confirmation_prompt(space_name: str, space: dict, text: str) -> str:
    display_name = space.get("displayName") or "(no name)"
    return f"Send this message to {display_name} ({space_name})?\n\n{text}"


def _not_confirmed(space_name: str) -> ToolError:
    return ToolError(
        f"Message not sent: the user did not confirm the send to {space_name}. "
        "Do not retry unless the user asks to."
    )


async def _confirm_send(
    ctx: Context, space_name: str, space: dict, text: str
) -> InputRequiredResult | None:
    """Asks the user to confirm the exact text and target space before a send.

    Returns None when the user confirmed. On 2026-07-28+ connections the first
    call returns an InputRequiredResult that the tool must return as is: the
    client asks the user and retries the call with the answer.
    Raises ToolError when the user does not confirm.
    Meant to be reused by every write tool.
    """
    prompt = _confirmation_prompt(space_name, space, text)

    if not _is_modern_protocol(ctx):
        result = await ctx.elicit(
            prompt, response_type=bool, response_title="Send the message"
        )
        if isinstance(result, AcceptedElicitation) and result.data is True:
            return None
        raise _not_confirmed(space_name)

    digest = _send_digest(space_name, text)
    responses = ctx.input_responses
    if responses is None or _CONFIRM_KEY not in responses:
        config = parse_elicit_response_type(bool, response_title="Send the message")
        return InputRequiredResult(
            input_requests={
                _CONFIRM_KEY: ElicitRequest(
                    params=ElicitRequestFormParams(
                        message=prompt, requested_schema=config.schema
                    )
                )
            },
            request_state=digest,
        )
    answer = responses[_CONFIRM_KEY]
    if (
        ctx.request_state == digest
        and isinstance(answer, ElicitResult)
        and answer.action == "accept"
        and (answer.content or {}).get("value") is True
    ):
        return None
    raise _not_confirmed(space_name)


_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
# Posting a message adds content, it does not modify or delete existing content.
_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)


def _resolve_send_target(space_name: str) -> tuple[ChatClient, dict]:
    """Returns the Chat client and the space, refusing direct conversations."""
    chat = _ensure_chat()
    space = chat.spaces.get(space_name)
    space_type = space.get("spaceType", "")
    if space_type in ("DIRECT_MESSAGE", "GROUP_CHAT"):
        raise ValueError(
            f"Sending messages to direct conversations is not allowed "
            f"(spaceType={space_type!r}). Use named spaces only."
        )
    return chat, space


@mcp.tool(annotations=_READ_ONLY)
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


@mcp.tool(annotations=_READ_ONLY)
def list_spaces() -> list[dict]:
    """Lists the configured Google Chat spaces with their permissions (r=read, w=write)."""
    return _cfg.list_spaces()


@mcp.tool(annotations=_READ_ONLY)
def get_space(space_name: str) -> dict:
    """Returns the details of a space (requires read permission).

    Args:
        space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
    """
    _cfg.require_read(space_name)
    return _ensure_chat().spaces.get(space_name)


@mcp.tool(annotations=_READ_ONLY)
def list_messages(space_name: str, page_size: int = 25, filter: str = "") -> list[dict]:
    """Lists the messages in a space (requires read permission).

    Args:
        space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
        page_size: maximum number of messages (default 25, max 1000)
        filter: optional filter, e.g. 'createTime > "2024-01-01T00:00:00Z"'
    """
    _cfg.require_read(space_name)
    return _ensure_chat().messages.list(space_name, page_size=page_size, filter_str=filter or None)


@mcp.tool(annotations=_WRITE)
async def send_message(
    space_name: str, text: str, ctx: Context
) -> dict | InputRequiredResult:
    """Sends a message to a space (requires write permission). Does not send DMs.

    Only for spaces not configured ':unattended' (use send_message_unattended
    for those). If the MCP client supports it, the user is asked to confirm the
    exact text and target space before the message is sent.

    Args:
        space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
        text: message text
    """
    _cfg.require_write(space_name)
    if not _cfg.requires_confirmation(space_name):
        raise ToolError(
            f"{space_name} is configured ':unattended': "
            "use send_message_unattended to post to it."
        )
    chat, space = _resolve_send_target(space_name)
    if _client_supports_elicitation(ctx):
        pending = await _confirm_send(ctx, space_name, space, text)
        if pending is not None:
            return pending
    else:
        _log.info(
            "send_message to %s without server-side confirmation: the client "
            "does not support elicitation, relying on its tool approval.",
            space_name,
        )
    return chat.messages.send(space_name, text)


@mcp.tool(annotations=_WRITE)
def send_message_unattended(space_name: str, text: str) -> dict:
    """Sends a message to a space configured ':unattended', without confirmation.

    Meant for scheduled/automated tasks. Only spaces configured ':unattended'
    are accepted (use send_message for the others). Does not send DMs.

    Args:
        space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
        text: message text
    """
    _cfg.require_write(space_name)
    if _cfg.requires_confirmation(space_name):
        raise ToolError(
            f"{space_name} is not configured ':unattended': "
            "use send_message to post to it."
        )
    chat, _ = _resolve_send_target(space_name)
    return chat.messages.send(space_name, text)


@mcp.tool(annotations=_READ_ONLY)
def list_members(space_name: str) -> list[dict]:
    """Lists the members of a space (requires read permission).

    Args:
        space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
    """
    _cfg.require_read(space_name)
    return _ensure_chat().members.list(space_name)
