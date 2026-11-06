"""
The remote `ChatSource`: the Chat client of the caller of this request
(docs/remote/07, M2, M4, M5).

`OAuthProxy` validates the connector token of each request and swaps it for
the caller's upstream Google access token (`AccessToken.token`) and claims.
This module only reads that validated token from FastMCP's request context:

- `client()` builds a `ChatClient` from the upstream access token. The
  credentials carry no refresh token and no client secret, so nothing here can
  refresh or store anything: the proxy refreshes the upstream token when the
  client refreshes its own (M5). An expired token makes the Chat call fail
  with an authentication error; Claude then refreshes and retries.
- `auth_status()` reports the signed-in session, never a token.
- `current_sub()` is the callable `RemoteAclProvider` and the users middleware
  take: the `sub` of the validated token, or None.

Nothing that belongs to a user is kept in this object or in the module (the
same trap as the NiceGUI one in 04): every method reads the request's token
again.

Needs the `remote` extra only through `fastmcp.server.dependencies`, which is a
core dependency, so importing this module needs nothing else. Local mode never
imports it (NFR-7).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp.server.auth import AccessToken

    from ..chat import ChatClient

# Google's `sub` is a string of digits. The document ID derived from it must
# never be able to address another path (same rule as remote.acl).
_SUB_RE = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


class NotSignedInError(RuntimeError):
    """The request carries no validated token. In a running service the HTTP
    layer refuses such a request before any tool runs; this is the backstop."""


def current_access_token() -> "AccessToken | None":
    """The validated token of this request, or None outside an authenticated one."""
    from fastmcp.server.dependencies import get_access_token

    try:
        return get_access_token()
    except Exception:
        # No request context (a log line outside a request, a test): not signed in.
        return None


def current_sub() -> str | None:
    """The `sub` of the validated token of this request, or None (M4).

    Only the `sub` claim counts, never a header or an argument. A value that is
    not a plain identifier is treated as missing.
    """
    token = current_access_token()
    if token is None:
        return None
    sub = token.claims.get("sub") or token.subject
    if isinstance(sub, str) and _SUB_RE.fullmatch(sub):
        return sub
    return None


def current_email() -> str | None:
    token = current_access_token()
    email = token.claims.get("email") if token is not None else None
    return email if isinstance(email, str) and email else None


class RemoteChatSource:
    """`ChatSource` for the caller of the current request. Holds no state."""

    async def client(self) -> "ChatClient":
        token = current_access_token()
        if token is None or not token.token or current_sub() is None:
            raise NotSignedInError("Not signed in: the request has no valid Google token.")

        from google.oauth2.credentials import Credentials

        from ..chat import ChatClient

        # Access token only: no refresh token, no token URI, no client secret.
        return ChatClient(Credentials(token=token.token))

    def auth_status(self) -> dict:
        token = current_access_token()
        if token is None or current_sub() is None:
            return {
                "signed_in": False,
                "warning": "This request carries no valid Google sign-in.",
            }
        expires_at = None
        if token.expires_at:
            expires_at = datetime.fromtimestamp(token.expires_at, tz=timezone.utc).isoformat()
        return {
            "signed_in": True,
            "email": current_email(),
            "domain": token.claims.get("hd"),
            "scopes": sorted(token.scopes),
            "access_token_expires_at": expires_at,
            "warning": None,
            "remedy": (
                "If Chat calls fail with an authentication error, remove the connector in "
                "Claude and add it again to sign in."
            ),
        }
