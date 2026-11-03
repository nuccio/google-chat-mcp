"""
DEVELOPMENT ONLY: the settings pages with an in-memory backend.

There is no Google sign-in here: whoever opens the page picks one of the
fixture accounts. Run it only on your own machine:

    google-chat-mcp settings-mock

The real server (#52) must not import this module.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from .app import BASE, SettingsApp, create_app
from .backends import ConcurrentChange
from .models import (
    Account,
    AclEntry,
    AuditRecord,
    Permissions,
    Policy,
    Space,
)

# ---------------------------------------------------------------------------
# Fixtures

ALICE = Account(sub="100000000000000000001", email="alice@example.com", hd="example.com")
# Has never used the connector: shows the account mismatch warning.
BOB = Account(sub="100000000000000000002", email="bob@example.com", hd="example.com")
# Her administrator does not allow write without confirmation.
CAROL = Account(sub="100000000000000000003", email="carol@restricted.example", hd="restricted.example")

ACCOUNTS = [ALICE, BOB, CAROL]

ACCOUNT_NOTES = {
    ALICE.sub: "Uses the connector; some spaces already enabled",
    BOB.sub: "Has never used the connector; nothing enabled",
    CAROL.sub: "Administrator forbids write without confirmation",
}

MALICIOUS_NAME = '<script>alert("pwned")</script><img src=x onerror=alert(1)> Launch'

_SHARED_SPACES = [
    Space("spaces/AAABBBCCC", "Team General", "SPACE"),
    Space("spaces/QQQ222", "Team General", "SPACE"),
    Space("spaces/DDDEEEFFF", "Announcements", "SPACE"),
    Space("spaces/REPORTS01", "Reports", "SPACE"),
    Space("spaces/MMM111", "Team Marketing", "SPACE"),
    Space("spaces/XSS0001", MALICIOUS_NAME, "SPACE"),
    Space("spaces/DM00001", "", "DIRECT_MESSAGE"),
    Space("spaces/GRP0001", "", "GROUP_CHAT"),
]


def fixture_spaces() -> dict[str, list[Space]]:
    return {a.sub: list(_SHARED_SPACES) for a in ACCOUNTS}


def fixture_acl() -> dict[str, dict[str, AclEntry]]:
    """Alice's ACL from the mockup; Bob and Carol start empty."""
    return {
        ALICE.sub: {
            "spaces/AAABBBCCC": AclEntry(
                "spaces/AAABBBCCC", Permissions(read=True, write=True), "Team General"
            ),
            "spaces/DDDEEEFFF": AclEntry(
                "spaces/DDDEEEFFF", Permissions(read=True), "Announcements"
            ),
            # Orphaned: Alice is no longer a member, Google does not return it.
            "spaces/ZZZ999": AclEntry("spaces/ZZZ999", Permissions(read=True), "Old project"),
        }
    }


# ---------------------------------------------------------------------------
# In-memory backends


class InMemorySpacesSource:
    def __init__(self, spaces_by_sub: dict[str, list[Space]]):
        self.spaces_by_sub = spaces_by_sub

    def list_spaces(self, account: Account) -> list[Space]:
        return list(self.spaces_by_sub.get(account.sub, []))

    def get_space(self, account: Account, space_name: str) -> Space | None:
        for sp in self.spaces_by_sub.get(account.sub, []):
            if sp.name == space_name:
                return sp
        return None

    def leave(self, account: Account, space_name: str) -> None:
        """Test helper: the user leaves a space."""
        self.spaces_by_sub[account.sub] = [
            sp for sp in self.spaces_by_sub.get(account.sub, []) if sp.name != space_name
        ]


class InMemoryAclStore:
    def __init__(self, entries: dict[str, dict[str, AclEntry]] | None = None):
        self._entries = entries or {}
        self._audit: list[AuditRecord] = []
        self._lock = threading.Lock()

    def list_entries(self, sub: str) -> list[AclEntry]:
        with self._lock:
            return list(self._entries.get(sub, {}).values())

    def get_entry(self, sub: str, space_name: str) -> AclEntry | None:
        with self._lock:
            return self._entries.get(sub, {}).get(space_name)

    def put_entry(
        self,
        sub: str,
        entry: AclEntry,
        *,
        expected_before: Permissions | None,
        now: datetime,
    ) -> AuditRecord:
        # The CHECK constraint of the data model, enforced by the writer.
        entry.permissions.validate()
        with self._lock:
            entries = self._entries.setdefault(sub, {})
            current = entries.get(entry.space_name)
            before = current.permissions if current else None
            if before != expected_before:
                raise ConcurrentChange(entry.space_name)
            record = AuditRecord(sub, entry.space_name, before, entry.permissions, now)
            entries[entry.space_name] = replace(entry, updated_at=now)
            self._audit.append(record)
            return record

    def delete_entry(self, sub: str, space_name: str, *, now: datetime) -> AuditRecord | None:
        with self._lock:
            current = self._entries.get(sub, {}).pop(space_name, None)
            if current is None:
                return None
            record = AuditRecord(sub, space_name, current.permissions, None, now)
            self._audit.append(record)
            return record

    def audit_log(self, sub: str) -> list[AuditRecord]:
        with self._lock:
            return [r for r in self._audit if r.sub == sub]


class InMemoryUsage:
    def __init__(self, last_used: dict[str, datetime]):
        self._last_used = last_used

    def last_used(self, sub: str) -> datetime | None:
        return self._last_used.get(sub)


class DomainPolicy:
    def __init__(self, by_domain: dict[str, Policy]):
        self.by_domain = by_domain

    def policy_for(self, account: Account) -> Policy:
        return self.by_domain.get(account.hd, Policy())


class DevSignIn:
    """
    Fake sign-in: a page listing the fixture accounts. Picking one stands
    for a successful Google sign-in; in re-authentication mode it stands for
    a fresh sign-in with `max_age` (auth_time = now).
    """

    def __init__(self, accounts: list[Account], now: Callable[[], datetime]):
        self.accounts = {a.sub: a for a in accounts}
        self.now = now
        self.routes = [
            Route(f"{BASE}/dev/sign-in", self.page, methods=["GET"]),
            Route(f"{BASE}/dev/sign-in", self.submit, methods=["POST"]),
        ]

    def start(
        self, request: Request, *, select_account: bool, reauth_for: Account | None
    ) -> Response:
        return RedirectResponse(f"{BASE}/dev/sign-in", status_code=303)

    async def page(self, request: Request) -> Response:
        settings: SettingsApp = request.app.state.settings
        s = settings.ensure_session(request)
        intent = s.sign_in_intent or {"mode": "sign_in"}
        return settings.render(
            request,
            "dev_sign_in.html",
            {
                "accounts": [
                    (a, ACCOUNT_NOTES.get(a.sub, "")) for a in self.accounts.values()
                ],
                "reauth": intent.get("mode") == "reauth",
            },
        )

    async def submit(self, request: Request) -> Response:
        settings: SettingsApp = request.app.state.settings
        s = settings.session(request)
        if s is None:
            return RedirectResponse(BASE, status_code=303)
        form = await settings.check_csrf(request, s)
        account = self.accounts.get(form.get("account", ""))
        if account is None:
            raise HTTPException(400, "Unknown account.")
        return settings.finish_sign_in(request, account, auth_time=self.now())


def create_mock_app(now: Callable[[], datetime] | None = None) -> Starlette:
    """The settings app with fixture data. Development and tests only."""
    clock = now or (lambda: datetime.now(timezone.utc))
    start = clock()
    app = create_app(
        spaces=InMemorySpacesSource(fixture_spaces()),
        acl=InMemoryAclStore(fixture_acl()),
        usage=InMemoryUsage(
            {ALICE.sub: start - timedelta(hours=2), CAROL.sub: start - timedelta(days=1)}
        ),
        policy=DomainPolicy({"restricted.example": Policy(allow_unattended=False)}),
        sign_in=DevSignIn(ACCOUNTS, clock),
        secure_cookies=False,  # plain HTTP on localhost
        dev_mode=True,
        now=clock,
    )

    async def root(request: Request) -> Response:
        return RedirectResponse(BASE, status_code=303)

    app.router.routes.append(Route("/", root, methods=["GET"]))
    return app
