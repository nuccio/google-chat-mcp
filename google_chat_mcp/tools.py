"""
MCP tools for Google Chat: the create_server() factory.

Imports only from the core (permissions, interfaces, chat, log_masking) and
third-party libraries. Does not import auth, config, remote or settings, and
does not read os.environ or sys.argv. The local and remote entry points both
call create_server() with their own implementations of AclProvider and
ChatSource.
"""

import hashlib
import json
import logging
from collections.abc import Callable

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

from .chat import ChatAPIError
from .interfaces import AclProvider, ChatSource
from .log_masking import EMAIL_MARKER
from .permissions import PermissionDeniedError

_log = logging.getLogger("google-chat-mcp.tools")

# Tool arguments that hold an email address: logged as EMAIL_MARKER.
_EMAIL_ARGUMENTS: frozenset[str] = frozenset()

_CONFIRM_KEY = "confirm_send"

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)


def _loggable_arguments(arguments: dict | None) -> dict:
    return {
        name: EMAIL_MARKER if name in _EMAIL_ARGUMENTS else value
        for name, value in (arguments or {}).items()
    }


def _client_supports_elicitation(ctx: Context) -> bool:
    return ctx.session.check_client_capability(
        ClientCapabilities(elicitation=ElicitationCapability())
    )


def _is_modern_protocol(ctx: Context) -> bool:
    """True on 2026-07-28+ connections, where the server returns InputRequiredResult."""
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
    """Asks the user to confirm before a send.

    Returns None when the user confirmed. On 2026-07-28+ connections the first
    call returns an InputRequiredResult; the client asks the user and retries
    with the answer. Raises ToolError when the user does not confirm.
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


def _resolve_send_target(client, space_name: str) -> dict:
    """Returns the space dict, refusing anything but named spaces."""
    space = client.spaces.get(space_name)
    space_type = space.get("spaceType")
    if space_type != "SPACE":
        raise ValueError(
            f"Sending messages is allowed to named spaces only "
            f"(expected spaceType 'SPACE', got {space_type!r})."
        )
    return space


def create_server(
    provider: AclProvider,
    chat: ChatSource,
    redact: Callable[[dict | None], dict] | None = None,
    auth=None,
) -> FastMCP:
    """Builds the FastMCP server with the seven Google Chat tools.

    provider and chat are resolved per request; local mode passes the same
    object on every call, remote mode builds them from the request token.
    redact replaces the argument-logging function; None uses the default
    (redact email arguments, keep the rest). auth is the FastMCP auth provider
    of the HTTP transport (remote mode); local mode passes none.
    """
    _redact = redact if redact is not None else _loggable_arguments
    mcp = FastMCP("google-chat", auth=auth)

    class _LoggingMiddleware(Middleware):
        async def on_call_tool(self, context, call_next):
            msg = context.message
            ctx = context.fastmcp_context
            protocol = elicitation = "?"
            if ctx is not None:
                try:
                    protocol = ctx.session.protocol_version
                    elicitation = _client_supports_elicitation(ctx)
                except RuntimeError:
                    pass
            _log.info(
                "tool=%s protocol=%s elicitation=%s args=%s",
                msg.name, protocol, elicitation, _redact(msg.arguments),
            )
            return await call_next(context)

    mcp.add_middleware(_LoggingMiddleware())

    @mcp.tool(annotations=_READ_ONLY)
    def chat_auth_status() -> dict:
        """Returns the OAuth token status without requiring valid credentials.

        Useful for diagnosing auth issues before calling other tools.
        """
        return chat.auth_status()

    @mcp.tool(annotations=_READ_ONLY)
    async def list_spaces() -> list[dict]:
        """Lists the configured Google Chat spaces with their permissions (r=read, w=write)."""
        acl = await provider.acl()
        return acl.list_spaces()

    @mcp.tool(annotations=_READ_ONLY)
    async def get_space(space_name: str) -> dict:
        """Returns the details of a space (requires read permission).

        Args:
            space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
        """
        acl = await provider.acl()
        acl.require_read(space_name)
        client = await chat.client()
        return client.spaces.get(space_name)

    @mcp.tool(annotations=_READ_ONLY)
    async def list_messages(
        space_name: str, page_size: int = 25, filter: str = "", page_token: str = ""
    ) -> dict:
        """Lists the most recent messages in a space (requires read permission).

        Returns {"messages": [...], "next_page_token": ...}. The first page holds
        the latest `page_size` messages (matching `filter`, if given) in
        chronological order: its last element is the most recent message.

        If next_page_token is not null, there are older messages: pass it as
        page_token, with the same filter and page_size, to get the previous
        `page_size` messages, again in chronological order.

        Args:
            space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
            page_size: maximum number of messages per page (default 25, max 1000)
            filter: optional filter, e.g. 'createTime > "2024-01-01T00:00:00Z"'
            page_token: next_page_token from a previous call, to get older messages
        """
        acl = await provider.acl()
        acl.require_read(space_name)
        client = await chat.client()
        return client.messages.list(
            space_name,
            page_size=page_size,
            filter_str=filter or None,
            page_token=page_token or None,
        )

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
        acl = await provider.acl()
        acl.require_write(space_name)
        if not acl.requires_confirmation(space_name):
            raise ToolError(
                f"{space_name} is configured ':unattended': "
                "use send_message_unattended to post to it."
            )
        client = await chat.client()
        space = _resolve_send_target(client, space_name)
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
        return client.messages.send(space_name, text)

    @mcp.tool(annotations=_WRITE)
    async def send_message_unattended(space_name: str, text: str) -> dict:
        """Sends a message to a space configured ':unattended', without confirmation.

        Meant for scheduled/automated tasks. Only spaces configured ':unattended'
        are accepted (use send_message for the others). Does not send DMs.

        Args:
            space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
            text: message text
        """
        acl = await provider.acl()
        acl.require_write(space_name)
        if acl.requires_confirmation(space_name):
            raise ToolError(
                f"{space_name} is not configured ':unattended': "
                "use send_message to post to it."
            )
        client = await chat.client()
        space = _resolve_send_target(client, space_name)
        return client.messages.send(space_name, text)

    @mcp.tool(annotations=_READ_ONLY)
    async def list_members(space_name: str) -> list[dict]:
        """Lists the members of a space (requires read permission).

        Args:
            space_name: resource name of the space, e.g. 'spaces/AAABBBCCC'
        """
        acl = await provider.acl()
        acl.require_read(space_name)
        client = await chat.client()
        return client.members.list(space_name)

    return mcp
