"""
`users/{sub}.last_mcp_seen_at`, written by middleware (docs/remote/07, "users/{sub}").

A FastMCP middleware, not a tool: it runs inside the request, after the OAuth
layer has validated the token, before the tool, so the write is done before the
response is sent (Cloud Run may throttle the CPU afterwards).

- **At most once an hour per instance and per user**, decided by an in-memory
  map of the last attempt: no read is needed to decide. The gate is closed
  before the write starts, so concurrent calls of one user write once.
- **A failure is logged and ignored**: the call goes on. The next attempt for
  that user is after `RETRY_AFTER`, not after an hour, so a transient error does
  not hide the user for long, and an outage does not add a write attempt to
  every call.
- **Only `last_mcp_seen_at`** is updated when the document exists. When it does
  not, the first write also sets `schema_version`, `created_at` and `email`
  (`schema.user_to_dict`). Nothing else of any document is written by `gcm-mcp`
  except the proxy's own store.
- The log line has the exception class and a hash of the `sub`, never the
  document path, the email or the exception text.

Needs the `remote` extra for `FirestoreUsersWriter`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from fastmcp.server.middleware import Middleware

from .. import schema
from .chat import current_email, current_sub

log = logging.getLogger(__name__)

USERS_COLLECTION = "users"
SEEN_INTERVAL = 3600.0  # seconds between two writes for one user on one instance
RETRY_AFTER = 300.0  # seconds before trying again after a failed write
WRITE_TIMEOUT = 5.0  # seconds the call may wait for the write


class UsersWriter(Protocol):
    """Writes `users/{sub}`. The only write `gcm-mcp` makes besides the token store."""

    async def touch(self, sub: str, email: str, now: datetime) -> None:
        """Set `last_mcp_seen_at` to `now`; create the document (with `created_at`
        and `email`) if it does not exist. Any exception means it failed."""
        ...


class FirestoreUsersWriter:
    """`UsersWriter` on Firestore. The blocking client runs in a worker thread."""

    def __init__(self, client=None) -> None:
        if client is None:
            from .firestore import create_client

            client = create_client()
        self._db = client

    def _touch(self, sub: str, email: str, now: datetime) -> None:
        from google.api_core.exceptions import NotFound

        doc = self._db.collection(USERS_COLLECTION).document(sub)
        try:
            # Touches exactly one field, and fails if the document is missing.
            doc.update({"last_mcp_seen_at": now})
        except NotFound:
            doc.set(
                schema.user_to_dict(
                    schema.UserDoc(email=email, created_at=now, last_mcp_seen_at=now)
                ),
                merge=True,
            )

    async def touch(self, sub: str, email: str, now: datetime) -> None:
        await asyncio.to_thread(self._touch, sub, email, now)


def _hash(sub: str) -> str:
    return hashlib.sha256(sub.encode()).hexdigest()[:16]


class UsersMiddleware(Middleware):
    """Records that a user called the MCP server, at most once an hour."""

    def __init__(
        self,
        writer: UsersWriter,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        interval: float = SEEN_INTERVAL,
        retry_after: float = RETRY_AFTER,
    ) -> None:
        self._writer = writer
        self._monotonic = monotonic
        self._now = now
        self._interval = interval
        self._retry_after = retry_after
        # sub -> monotonic time before which no write is attempted.
        self._next_write: dict[str, float] = {}

    def _due(self, sub: str) -> bool:
        t = self._monotonic()
        if t < self._next_write.get(sub, float("-inf")):
            return False
        self._next_write[sub] = t + self._interval  # closed before the first await
        return True

    async def _record(self) -> None:
        sub = current_sub()
        if sub is None or not self._due(sub):
            return
        try:
            await asyncio.wait_for(
                self._writer.touch(sub, current_email() or "", self._now()), WRITE_TIMEOUT
            )
        except Exception as e:
            self._next_write[sub] = self._monotonic() + self._retry_after
            log.warning("users: last_mcp_seen_at not written: %s (sub=%s)", type(e).__name__, _hash(sub))

    async def on_call_tool(self, context, call_next):
        await self._record()
        return await call_next(context)
