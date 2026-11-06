"""
Interfaces between the settings pages and the rest of the system.

`mock.py` implements them in memory. The real server (#52) implements them
with Google OAuth, the Chat API and the ACL database.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from starlette.requests import Request
from starlette.responses import Response

from .models import Account, AclEntry, AuditRecord, Permissions, Policy, Space

if TYPE_CHECKING:
    from .app import SettingsApp


class ConcurrentChange(Exception):
    """The ACL entry changed between reading it and writing it."""


class SpacesSource(Protocol):
    """The spaces the signed-in user can see, with the session's token."""

    def list_spaces(self, account: Account) -> list[Space]:
        """spaces.list: every space the user is a member of, of any type."""

    def get_space(self, account: Account, space_name: str) -> Space | None:
        """spaces.get: None when the space does not exist or the user is not a member."""


class AclStore(Protocol):
    def list_entries(self, sub: str) -> list[AclEntry]: ...

    def get_entry(self, sub: str, space_name: str) -> AclEntry | None: ...

    def put_entry(
        self,
        sub: str,
        entry: AclEntry,
        *,
        expected_before: Permissions | None,
        now: datetime,
        session: str = "",
    ) -> AuditRecord:
        """
        Write the entry and its audit record in one transaction.

        Raises ConcurrentChange if the stored permissions are not
        `expected_before`, and InvalidPermissions if the permissions break
        the data model rules. `session` is the short correlation ID stored in the
        audit record.
        """

    def delete_entry(
        self, sub: str, space_name: str, *, now: datetime, session: str = ""
    ) -> AuditRecord | None:
        """Remove the entry and write its audit record. None if there was no entry."""

    def audit_log(self, sub: str) -> list[AuditRecord]: ...


class ConnectorUsage(Protocol):
    def last_used(self, sub: str) -> datetime | None:
        """When the MCP connector last served this `sub`; None if never."""


class PolicySource(Protocol):
    def policy_for(self, account: Account) -> Policy: ...


class SignIn(Protocol):
    """
    Starts a sign-in and, once the identity provider answers, calls
    `SettingsApp.finish_sign_in` from a plain HTTP route, which sets the
    session cookie (a NiceGUI event handler cannot set cookies).

    There is no re-authentication (decision on #29).
    """

    def install(self, settings: "SettingsApp") -> None:
        """Register the routes (and pages, for the development sign-in) it needs."""

    def start(self, request: Request, *, select_account: bool) -> Response: ...
