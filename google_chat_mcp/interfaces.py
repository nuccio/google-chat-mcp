"""
Protocols that the MCP tools depend on.

Both local mode (--space flags, token.json) and remote mode (Firestore ACL,
per-request token) implement these two interfaces. tools.py imports only this
module from the core: no auth, config, remote or settings.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .chat import ChatClient
    from .permissions import Acl


class AclProvider(Protocol):
    """Returns the ACL for the caller of the current request."""

    async def acl(self) -> "Acl": ...


class ChatSource(Protocol):
    """Returns a ChatClient and the authentication status for the caller."""

    async def client(self) -> "ChatClient": ...

    def auth_status(self) -> dict: ...
