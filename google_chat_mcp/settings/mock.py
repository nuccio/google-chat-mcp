"""
DEVELOPMENT ONLY: the settings pages with an in-memory backend.

There is no Google sign-in here: whoever opens the page picks one of the
fixture accounts. Run it only on your own machine:

    google-chat-mcp settings-mock

The real server (#52) must not import this module.
"""

from __future__ import annotations

import hmac
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from nicegui import app as nicegui_app
from nicegui import ui

from .app import BASE, TITLE, SettingsApp, install
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
    for a successful Google sign-in.

    The choice is made in a NiceGUI event, which cannot set cookies, so the
    page asks the app for a one-time ticket bound to this browser's session
    and navigates to a plain route that exchanges it for the session cookie.
    """

    def __init__(self, accounts: list[Account]):
        self.accounts = {a.sub: a for a in accounts}
        self._chosen: dict[str, Account] = {}  # ticket -> account
        self._lock = threading.Lock()

    def start(self, request: Request, *, select_account: bool) -> Response:
        return RedirectResponse(f"{BASE}/dev/sign-in", status_code=303)

    def install(self, settings: SettingsApp) -> None:
        @ui.page(f"{BASE}/dev/sign-in", title=TITLE)
        def page(request: Request) -> None:
            sid = settings.session_id(request)
            ui.label(
                "Development only. This page stands in for Google sign-in. Nothing is checked: "
                "pick an account."
            ).classes("full-width q-pa-sm bg-amber-2")
            ui.label("Choose an account").classes("text-h5 q-pa-md")
            if settings.session(sid) is None:
                ui.link("Start from the sign-in page", f"{BASE}/sign-in")
                return
            for account in self.accounts.values():

                def choose(account: Account = account) -> None:
                    ticket = settings.issue_ticket(sid)
                    if ticket is None:
                        ui.navigate.to(f"{BASE}/sign-in")
                        return
                    with self._lock:
                        self._chosen[ticket] = account
                    ui.navigate.to(f"{BASE}/dev/complete?ticket={ticket}")

                with ui.row().classes("items-center q-px-md"):
                    ui.button(account.email, on_click=choose).props("no-caps").mark(f"account-{account.email}")
                    ui.label(ACCOUNT_NOTES.get(account.sub, "")).classes("text-caption text-grey-7")
            ui.link("Cancel", BASE).classes("q-pa-md")

        @nicegui_app.get(f"{BASE}/dev/complete")
        def complete(request: Request, ticket: str = "") -> Response:
            s = settings.session(settings.session_id(request))
            with self._lock:
                account = self._chosen.pop(ticket, None)
            if (
                s is None
                or s.ticket is None
                or account is None
                or not hmac.compare_digest(s.ticket.encode(), ticket.encode())
            ):
                return RedirectResponse(BASE, status_code=303)
            s.ticket = None
            return settings.finish_sign_in(request, account)


def create_mock_settings(now: Callable[[], datetime] | None = None) -> SettingsApp:
    """The settings app with fixture data. Development and tests only."""
    clock = now or (lambda: datetime.now(timezone.utc))
    start = clock()
    return SettingsApp(
        spaces=InMemorySpacesSource(fixture_spaces()),
        acl=InMemoryAclStore(fixture_acl()),
        usage=InMemoryUsage(
            {ALICE.sub: start - timedelta(hours=2), CAROL.sub: start - timedelta(days=1)}
        ),
        policy=DomainPolicy({"restricted.example": Policy(allow_unattended=False)}),
        sign_in=DevSignIn(ACCOUNTS),
        secure_cookies=False,  # plain HTTP on localhost
        dev_mode=True,
        now=clock,
    )


def install_mock(settings: SettingsApp, origins: set[str]) -> None:
    """Register the mock's pages and routes on NiceGUI's app."""
    install(settings, allowed_origins=origins)

    @nicegui_app.get("/")
    def root() -> Response:
        return RedirectResponse(BASE, status_code=303)


def run_mock(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve the mock with NiceGUI's own server (blocks)."""
    origins = {f"http://{host}:{port}"}
    if host in ("127.0.0.1", "localhost", "::1"):
        origins |= {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    install_mock(create_mock_settings(), origins)
    ui.run(
        host=host,
        port=port,
        title=TITLE,
        reload=False,
        show=False,
        tailwind=False,
        show_welcome_message=False,
        binding_refresh_interval=None,
        storage_secret=None,
    )
