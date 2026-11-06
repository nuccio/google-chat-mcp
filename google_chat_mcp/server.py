"""
Local (stdio) entry point: builds the MCP server with --space flags and token.json.

Calls create_server() with the local implementations of AclProvider (backed by
--space flags) and ChatSource (backed by token.json). Remote mode has its own
entry point in google_chat_mcp.remote.__main__ and never imports this module.
"""
import logging

from .auth import LocalChatSource
from .config import LocalAclProvider, acl_from_args
from .permissions import Acl
from .tools import create_server

_log = logging.getLogger("google-chat-mcp.tools")

mcp = create_server(LocalAclProvider(Acl({})), LocalChatSource())


def init(space_args: list[str]) -> None:
    """Builds the server for the given --space arguments.

    Called once at startup by __main__.py before mcp.run(). Replaces the
    module-level mcp with a new instance configured for space_args.
    """
    global mcp
    acl = acl_from_args(space_args)
    for name in acl.readable_unattended_spaces():
        _log.warning(
            "Space %s is configured :rw:unattended: content read from it can "
            "influence messages posted without confirmation.",
            name,
        )
    mcp = create_server(LocalAclProvider(acl), LocalChatSource())
