"""
The Firestore `AclProvider` of `gcm-mcp` (read only; docs/remote/07, M7-M10).

For each call of a tool, `RemoteAclProvider.acl()`:

1. takes the caller's `sub` from the validated token of this request;
2. reads `acls/{sub}` once, with no cache between requests (M7);
3. checks `schema_version` and parses the document (`schema.acl_from_dict`);
4. keeps the entries of named spaces only, then applies the admin policy
   ceiling (`AdminPolicy.effective`): an entry the policy forbids is
   suspended, never missing (M8);
5. raises `PermissionDeniedError` with the `/settings` link when nothing is
   granted (M10).

Fail closed (M9): a missing `sub`, a read error, an unknown schema version or
a malformed document denies the call. Nothing falls back to "allow".

This module has no write method and imports neither `settings` nor `AclStore`.
Needs the `remote` extra only for `FirestoreAclReader`, which imports the
Firestore library when it is created. Logs never contain document content or
the `sub` itself, only a short hash of it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections.abc import Callable, Mapping
from typing import Protocol

from ..permissions import Acl, PermissionDeniedError
from ..schema import InvalidDocument, UnsupportedSchemaVersion, acl_from_dict, granted_permissions
from .policy import AdminPolicy

log = logging.getLogger(__name__)

ACLS_COLLECTION = "acls"

# Google's `sub` is a string of digits; the document ID must never be able to
# address another path, so anything with a slash is refused.
_SUB_RE = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


class AclConfigError(ValueError):
    """`SETTINGS_BASE_URL` is missing or not a URL. Stops the service at start-up."""


class AclReader(Protocol):
    """Reads the raw `acls/{sub}` document. Read only by construction."""

    async def read(self, sub: str) -> dict | None:
        """The document as a dict, or None when it does not exist.
        Any exception means the ACL could not be read."""
        ...


class FirestoreAclReader:
    """`AclReader` on Firestore. The blocking client runs in a worker thread."""

    def __init__(self, client=None) -> None:
        if client is None:
            from .firestore import create_client

            client = create_client()
        self._db = client

    def _get(self, sub: str) -> dict | None:
        snapshot = self._db.collection(ACLS_COLLECTION).document(sub).get()
        return snapshot.to_dict() if snapshot.exists else None

    async def read(self, sub: str) -> dict | None:
        return await asyncio.to_thread(self._get, sub)


def settings_link(base_url: str | None) -> str:
    """`{SETTINGS_BASE_URL}/settings`. The base comes from configuration only."""
    base = (base_url or "").strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s/?#]+(/[^\s?#]*)?", base):
        raise AclConfigError("SETTINGS_BASE_URL is required and must be an http(s) URL")
    return f"{base}/settings"


def settings_link_from_env(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return settings_link(env.get("SETTINGS_BASE_URL"))


def _hash(sub: str) -> str:
    return hashlib.sha256(sub.encode()).hexdigest()[:16]


class RemoteAclProvider:
    """`AclProvider` for the caller of the current request.

    `current_sub` returns the `sub` of the validated token of this request, or
    None when there is none (the provider then denies). It is a callable, not a
    value, so the provider holds no per-user state: two callers in flight never
    see each other's ACL.
    """

    def __init__(
        self,
        reader: AclReader,
        admin: AdminPolicy,
        settings_link: str,
        current_sub: Callable[[], str | None],
    ) -> None:
        self._reader = reader
        self._admin = admin
        self._link = settings_link
        self._current_sub = current_sub

    def _deny(self, reason: str, sub: str | None, message: str) -> PermissionDeniedError:
        log.warning("acl denied: reason=%s sub=%s", reason, _hash(sub) if sub else "-")
        return PermissionDeniedError(message)

    async def acl(self) -> Acl:
        sub = self._current_sub()
        if not isinstance(sub, str) or not _SUB_RE.fullmatch(sub):
            raise self._deny("no_sub", None, "Access denied: the request has no valid user.")
        try:
            data = await self._reader.read(sub)
        except Exception as e:  # fail closed on any read error
            # Only the exception type: its text may hold a document path.
            log.warning("acl read failed: %s (sub=%s)", type(e).__name__, _hash(sub))
            raise self._deny(
                "read_error", sub, "Access denied: your permissions could not be read. Try again later."
            ) from None
        if data is None:
            stored = {}
        else:
            try:
                stored = granted_permissions(acl_from_dict(data))
            except UnsupportedSchemaVersion:
                raise self._deny(
                    "unsupported_schema", sub,
                    "Access denied: your permissions were saved by a newer version of this "
                    "service that this server does not understand yet.",
                ) from None
            except InvalidDocument:
                raise self._deny(
                    "invalid_document", sub,
                    f"Access denied: your saved permissions are not valid. "
                    f"Save them again at {self._link}.",
                ) from None
        effective = self._admin.effective(stored)
        if not effective.granted:
            if effective.suspended:
                raise self._deny(
                    "suspended", sub,
                    "No space is available: your administrator's policy has suspended "
                    f"all your configured spaces. See {self._link}.",
                )
            raise self._deny(
                "empty", sub,
                f"No space is configured for your account. Choose the spaces Claude may use at {self._link}.",
            )
        hint = f"Add it at {self._link}."
        if effective.suspended:
            names = ", ".join(sorted(effective.suspended))
            hint += f" Suspended by your administrator's policy: {names}."
        return Acl(effective.granted, not_configured_hint=hint)
