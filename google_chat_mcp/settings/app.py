"""
The `/settings` pages, built with NiceGUI (docs/remote/04-settings-server-design.md).

`SettingsApp` holds the backends, the server-side sessions and the flow
logic; it knows nothing about NiceGUI. `install(settings, ...)` registers
the pages (`@ui.page`), the plain HTTP routes that set or clear the session
cookie, and the two middlewares (security headers, websocket Origin check)
on NiceGUI's app. The caller then starts NiceGUI with `ui.run(...)`.

It never installs a fake sign-in by itself: the development sign-in only
exists in `mock.py`.

Rendering rules (stored XSS, see the design document): text from the Chat
API, the ACL or the user goes only into `ui.label`, `ui.input` values and
radio/checkbox labels. Never `ui.html`, `ui.markdown`, `ui.run_javascript`,
`ui.add_*_html`, or props whose name starts with `:`. URLs passed to
`ui.navigate.to` are built only from space IDs that matched `SPACE_ID_RE`.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from nicegui import app as nicegui_app
from nicegui import ui
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import rules
from .backends import (
    AclStore,
    ConcurrentChange,
    ConnectorUsage,
    PolicySource,
    SignIn,
    SpacesSource,
)
from .models import SPACE, Account, AclEntry, InvalidPermissions, Permissions, Space

BASE = "/settings"
SESSION_IDLE_TIMEOUT = timedelta(minutes=30)
PENDING_MAX_AGE = timedelta(minutes=10)
MAX_PENDING_CHANGES = 10
TITLE = "Chat connector settings"

# NiceGUI needs 'unsafe-inline' and 'unsafe-eval' (inline element tree,
# eval in nicegui.js) and has no nonce support. What this CSP still does:
# no scripts, styles, fonts or connections from other origins, no framing,
# no <object>, no <base> rewriting, no form posts elsewhere.
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "object-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
WEBSOCKET_PREFIX = "/_nicegui_ws"


# ---------------------------------------------------------------------------
# Sessions


@dataclass
class PendingChange:
    """A change waiting for confirmation."""

    token: str
    space_name: str
    display_name: str
    before: Permissions | None
    after: Permissions
    acl_fingerprint: str
    created_at: datetime


@dataclass
class Session:
    sid: str
    last_seen: datetime
    account: Account | None = None
    flash: list[tuple[str, str]] = field(default_factory=list)  # (kind, text)
    pending: dict[str, PendingChange] = field(default_factory=dict)
    # One-time ticket that binds a finished sign-in to this browser (see
    # SettingsApp.issue_ticket). Kept here, never trusted from the request.
    ticket: str | None = None


class SessionStore:
    """
    In-memory server-side sessions. The cookie only carries a random ID.

    The real server (#52) keeps them in Firestore, keyed by the SHA-256 of
    the ID; this store has the same behaviour for the pages.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get(self, sid: str | None, now: datetime) -> Session | None:
        if not sid:
            return None
        with self._lock:
            s = self._sessions.get(sid)
            if s is None:
                return None
            if now - s.last_seen > SESSION_IDLE_TIMEOUT:
                del self._sessions[sid]
                return None
            s.last_seen = now
            return s

    def create(self, now: datetime) -> Session:
        s = Session(sid=secrets.token_urlsafe(32), last_seen=now)
        with self._lock:
            self._sessions[s.sid] = s
        return s

    def delete(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)


# ---------------------------------------------------------------------------
# Middleware


class SecurityHeadersMiddleware:
    """Security headers on every HTTP response; `no-store` on everything
    except NiceGUI's versioned static files."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        names = {h for h, _ in SECURITY_HEADERS} | {b"cache-control"}
        static = scope["path"].startswith("/_nicegui/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in names or (static and k.lower() == b"cache-control")
                ]
                headers.extend(SECURITY_HEADERS)
                if not static:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class WebsocketOriginMiddleware:
    """
    Rejects Socket.IO requests (websocket or polling) to NiceGUI whose
    `Origin` is not one of ours. NiceGUI's Socket.IO server accepts any
    origin (`cors_allowed_origins='*'`), which would allow cross-site
    WebSocket hijacking. A request without `Origin` is rejected too.
    """

    def __init__(self, app: ASGIApp, allowed_origins: frozenset[str]) -> None:
        self.app = app
        self.allowed = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and scope["path"].startswith(WEBSOCKET_PREFIX):
            origin = dict(scope.get("headers", [])).get(b"origin", b"").decode("latin-1")
            if origin not in self.allowed:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send({
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"text/plain")],
                    })
                    await send({"type": "http.response.body", "body": b"Forbidden"})
                return
        await self.app(scope, receive, send)


def origin_of(url: str) -> str:
    """`https://host[:port]` of a base URL, as browsers send it in `Origin`."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"Not an http(s) base URL: {url!r}")
    return f"{parts.scheme}://{parts.netloc}"


# ---------------------------------------------------------------------------
# Helpers


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ago(then: datetime, now: datetime) -> str:
    seconds = int((now - then).total_seconds())
    if seconds < 60:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"  # pragma: no cover


def space_id(space_name: str) -> str:
    return space_name.removeprefix("spaces/")


class FlowError(Exception):
    """A refused change: shown to the user, nothing is written."""

    def __init__(self, message: str, *, not_member: bool = False):
        super().__init__(message)
        self.message = message
        self.not_member = not_member


@dataclass
class Row:
    space_name: str
    display_name: str
    labels: list[str]
    unattended: bool
    orphaned: bool
    suspended: bool


def confirm_wording(p: PendingChange) -> tuple[str, str]:
    """Heading and verb of the confirmation page."""
    b = p.before or Permissions()
    adds_read = p.after.read and not b.read
    adds_write = p.after.write and not b.write
    adds_unattended = p.after.unattended and not b.unattended
    if adds_unattended:
        return "Confirm write without confirmation", "post in"
    if adds_write:
        return "Confirm write access", "read and post in" if adds_read else "post in"
    return "Confirm read access", "read"


# ---------------------------------------------------------------------------
# The flow, without UI


class SettingsApp:
    def __init__(
        self,
        *,
        spaces: SpacesSource,
        acl: AclStore,
        usage: ConnectorUsage,
        policy: PolicySource,
        sign_in: SignIn,
        secure_cookies: bool = True,
        dev_mode: bool = False,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.spaces = spaces
        self.acl = acl
        self.usage = usage
        self.policy = policy
        self.sign_in = sign_in
        self.now = now
        self.sessions = SessionStore()
        self.dev_mode = dev_mode
        self.secure_cookies = secure_cookies
        # __Host- requires Secure, which a plain-HTTP local run cannot use.
        self.cookie_name = "__Host-gcm_session" if secure_cookies else "gcm_session"

    # -- sessions and cookies ---------------------------------------------

    def session_id(self, request: Request) -> str | None:
        return request.cookies.get(self.cookie_name)

    def session(self, sid: str | None) -> Session | None:
        return self.sessions.get(sid, self.now())

    def signed_in(self, sid: str | None) -> Session | None:
        """The session, re-read on every call, if it has an account."""
        s = self.session(sid)
        return s if s is not None and s.account is not None else None

    def set_cookie(self, response: Response, session: Session) -> None:
        response.set_cookie(
            self.cookie_name,
            session.sid,
            httponly=True,
            samesite="lax",  # Strict would drop the cookie on the return from Google
            secure=self.secure_cookies,
            path="/",
        )

    def start_sign_in(self, request: Request, *, select_account: bool = False) -> Response:
        """Plain route: make sure the browser has a (pre-sign-in) session, then sign in."""
        s = self.session(self.session_id(request))
        if s is None:
            s = self.sessions.create(self.now())
        response = self.sign_in.start(request, select_account=select_account)
        self.set_cookie(response, s)
        return response

    def issue_ticket(self, sid: str | None) -> str | None:
        """
        For a sign-in finished in a NiceGUI event (the development sign-in):
        a one-time ticket, stored in this browser's session, that the plain
        route `finish_with_ticket` exchanges for the new session cookie.
        """
        s = self.session(sid)
        if s is None:
            return None
        s.ticket = secrets.token_urlsafe(32)
        return s.ticket

    def finish_sign_in(self, request: Request, account: Account) -> Response:
        """
        Called by the SignIn implementation, from a plain HTTP route, once
        the identity provider has answered. The session is replaced with a
        new one (new ID), so a session ID planted before sign-in is useless.
        """
        old = self.session(self.session_id(request))
        new = self.sessions.create(self.now())
        new.account = account
        if old is not None:
            self.sessions.delete(old.sid)
        response = RedirectResponse(BASE, status_code=303)
        self.set_cookie(response, new)
        return response

    def sign_out(self, sid: str | None) -> None:
        if sid:
            self.sessions.delete(sid)

    def signed_out_response(self) -> Response:
        response = RedirectResponse(BASE, status_code=303)
        response.delete_cookie(self.cookie_name, path="/")
        return response

    # -- reading ---------------------------------------------------------------

    def rows(self, account: Account) -> list[Row]:
        known = {sp.name: sp for sp in self.spaces.list_spaces(account)}
        policy = self.policy.policy_for(account)
        rows = []
        for e in self.acl.list_entries(account.sub):
            sp = known.get(e.space_name)
            rows.append(
                Row(
                    space_name=e.space_name,
                    display_name=sp.display_name if sp else (e.display_name or e.space_name),
                    labels=rules.permission_labels(e.permissions),
                    unattended=e.permissions.unattended,
                    orphaned=sp is None,
                    suspended=not policy.allows(e.permissions),
                )
            )
        rows.sort(key=lambda r: (r.orphaned, r.display_name.casefold(), r.space_name))
        return rows

    def last_used(self, account: Account) -> str | None:
        when = self.usage.last_used(account.sub)
        return _ago(when, self.now()) if when else None

    def candidates(self, account: Account) -> list[Space]:
        """Named spaces the user can add, sorted. DMs and group chats are never listed."""
        result = [sp for sp in self.spaces.list_spaces(account) if sp.space_type == SPACE]
        result.sort(key=lambda sp: (sp.display_name.casefold(), sp.name))
        return result

    def enabled(self, account: Account) -> set[str]:
        return {e.space_name for e in self.acl.list_entries(account.sub)}

    def resolve_ref(self, account: Account, ref: str) -> str:
        """Space ID for a pasted link or ID, or FlowError."""
        name = rules.parse_space_ref(ref)
        if name is None:
            raise FlowError("This is not a Google Chat link or space ID.")
        space = self.spaces.get_space(account, name)
        if space is None:
            raise FlowError("No space with this ID among the spaces you are a member of.")
        if space.space_type != SPACE:
            raise FlowError("Direct messages and group chats cannot be added.")
        return space_id(name)

    def names(self, account: Account) -> dict[str, str]:
        return {sp.name: sp.display_name for sp in self.spaces.list_spaces(account)}

    # -- validation (flow step 7) -----------------------------------------

    def space_for_change(self, account: Account, space_name: str) -> Space:
        space = self.spaces.get_space(account, space_name)
        if space is None:
            raise FlowError("You are no longer a member of this space.", not_member=True)
        if space.space_type != SPACE:
            raise FlowError("Direct messages and group chats cannot be enabled.")
        return space

    def check_permissions(self, account: Account, after: Permissions) -> None:
        try:
            after.validate()
        except InvalidPermissions as e:
            if "at least one" in str(e):
                raise FlowError("Choose at least one permission.")
            raise FlowError("This change is not allowed.")
        if not self.policy.policy_for(account).allows(after):
            raise FlowError("This change is not allowed.")

    # -- changing (flow steps 5 to 7) -------------------------------------

    def submit(
        self, s: Session, space_name: str, *, read: bool, write: bool, mode: str
    ) -> str | None:
        """
        The permissions page's Continue. Saves directly when no confirmation
        is needed (returns None); otherwise stores a pending change and
        returns its token. Raises FlowError.
        """
        assert s.account is not None
        account = s.account
        space = self.space_for_change(account, space_name)
        if mode not in ("approval", "unattended"):
            raise FlowError("This change is not allowed.")
        # The write mode only counts when Write is checked.
        after = Permissions(read=read, write=write, unattended=write and mode == "unattended")
        self.check_permissions(account, after)
        entry = self.acl.get_entry(account.sub, space_name)
        before = entry.permissions if entry else None
        if after == before:
            s.flash.append(("info", f"«{space.display_name}»: nothing changed."))
            return None
        acl = self.acl.list_entries(account.sub)
        req = rules.requirements(before, after, rules.acl_with_change(acl, space_name, after))
        if not req.confirmation:
            self.save(s, space, before, after)
            return None
        if len(s.pending) >= MAX_PENDING_CHANGES:
            s.pending.pop(next(iter(s.pending)))
        token = secrets.token_urlsafe(16)
        s.pending[token] = PendingChange(
            token=token,
            space_name=space_name,
            display_name=space.display_name,
            before=before,
            after=after,
            acl_fingerprint=rules.acl_fingerprint(acl),
            created_at=self.now(),
        )
        return token

    def pending(self, s: Session, space_name: str, token: str | None) -> PendingChange | None:
        p = s.pending.get(token or "")
        if p is None or p.space_name != space_name:
            return None
        if self.now() - p.created_at > PENDING_MAX_AGE:
            s.pending.pop(p.token, None)
            return None
        return p

    def confirm(self, s: Session, p: PendingChange) -> None:
        """The confirmation page's Grant / Confirm. Raises FlowError."""
        assert s.account is not None
        s.pending.pop(p.token, None)
        space = self.space_for_change(s.account, p.space_name)
        self.check_permissions(s.account, p.after)
        if rules.acl_fingerprint(self.acl.list_entries(s.account.sub)) != p.acl_fingerprint:
            raise FlowError("Your settings changed while you were confirming. Check the change again.")
        self.save(s, space, p.before, p.after)

    def save(self, s: Session, space: Space, before: Permissions | None, after: Permissions) -> None:
        assert s.account is not None
        try:
            self.acl.put_entry(
                s.account.sub,
                AclEntry(space.name, after, space.display_name),
                expected_before=before,
                now=self.now(),
            )
        except ConcurrentChange:
            raise FlowError("Your settings changed in the meantime. Check the change again.")
        except InvalidPermissions:
            raise FlowError("This change is not allowed.")
        verb = "added" if before is None else "updated"
        s.flash.append(
            ("success", f"«{space.display_name}» {verb}: {', '.join(rules.permission_labels(after))}.")
        )

    def remove(self, s: Session, space_name: str) -> None:
        assert s.account is not None
        entry = self.acl.get_entry(s.account.sub, space_name)
        if entry is None:
            raise FlowError("This space is not enabled.")
        label = self.names(s.account).get(space_name) or entry.display_name or space_name
        self.acl.delete_entry(s.account.sub, space_name, now=self.now())
        s.flash.append(("success", f"«{label}» removed. Claude can no longer reach it."))


# ---------------------------------------------------------------------------
# Pages


def install(
    settings: SettingsApp,
    *,
    allowed_origins: set[str] | frozenset[str],
) -> None:
    """
    Register everything on NiceGUI's app. Call `ui.run(...)` afterwards.

    `allowed_origins` are the origins the browser may open NiceGUI's
    websocket from: the settings service's own address
    (`origin_of(SETTINGS_BASE_URL)`).
    """
    if not allowed_origins:
        raise ValueError("allowed_origins is required (the settings service's own origin)")
    nicegui_app.add_middleware(SecurityHeadersMiddleware)
    nicegui_app.add_middleware(WebsocketOriginMiddleware, allowed_origins=frozenset(allowed_origins))

    # -- plain routes: the only places that set or clear the cookie --------

    @nicegui_app.get(f"{BASE}/sign-in")
    def sign_in_route(request: Request) -> Response:
        return settings.start_sign_in(request)

    @nicegui_app.get(f"{BASE}/switch-account")
    def switch_account_route(request: Request) -> Response:
        return settings.start_sign_in(request, select_account=True)

    @nicegui_app.get(f"{BASE}/signed-out")
    def signed_out_route() -> Response:
        # The session document is already gone (Sign out handler); this only
        # clears the cookie.
        return settings.signed_out_response()

    settings.sign_in.install(settings)

    # -- shared layout -----------------------------------------------------

    def go(url: str) -> None:
        ui.navigate.to(url)

    def header(s: Session | None, *, tab: str | None = None) -> None:
        if settings.dev_mode:
            ui.label(
                "Development mock: fake sign-in, in-memory data. Not a real Google account."
            ).classes("full-width q-pa-sm bg-amber-2 text-center")
        with ui.row().classes("full-width items-center justify-between q-px-md q-py-sm"):
            ui.label(TITLE).classes("text-h6")
            if s is not None and s.account is not None:
                sid = s.sid
                with ui.row().classes("items-center q-gutter-sm"):
                    ui.label("Configuring").classes("text-grey-7")
                    ui.label(s.account.email).classes("text-weight-bold").mark("account-email")
                    ui.link("Switch account", f"{BASE}/switch-account")

                    def do_sign_out() -> None:
                        settings.sign_out(sid)
                        go(f"{BASE}/signed-out")

                    ui.button("Sign out", on_click=do_sign_out).props("flat dense no-caps")
        if tab is not None:
            with ui.row().classes("q-px-md q-gutter-md"):
                for key, label, url in (("spaces", "Spaces", BASE), ("dms", "Direct messages", f"{BASE}/dms")):
                    link = ui.link(label, url).classes("text-subtitle1")
                    if key == tab:
                        link.classes("text-weight-bold")

    def flashes(s: Session) -> None:
        shown, s.flash = s.flash, []
        for kind, text in shown:
            colour = {"success": "bg-green-1", "error": "bg-red-1", "info": "bg-blue-1"}.get(kind, "bg-grey-2")
            with ui.row().classes(f"full-width q-pa-sm {colour}").mark(f"flash-{kind}"):
                ui.label(text)
                if kind == "success":
                    ui.label("Claude sees it from its next request; nothing needs restarting.")

    def body():
        return ui.column().classes("q-pa-md full-width").style("max-width: 960px; margin: 0 auto")

    def require(request: Request) -> Session | None:
        """The signed-in session of this request, or None after sending the user to sign in."""
        s = settings.signed_in(settings.session_id(request))
        if s is None:
            go(BASE)
        return s

    def current(sid: str) -> Session | None:
        """Re-read the session on every event (check 1): it may have expired or been signed out."""
        s = settings.signed_in(sid)
        if s is None:
            go(BASE)
        return s

    # -- list (flow step 3) and sign-in landing ----------------------------

    @ui.page(BASE, title=TITLE)
    def list_page(request: Request) -> None:
        s = settings.signed_in(settings.session_id(request))
        if s is None:
            header(None)
            with body():
                ui.label("Choose what Claude can reach in Google Chat").classes("text-h5")
                ui.label(
                    "Pick the spaces Claude can access through the connector, and whether it "
                    "can read, write, or both in each one."
                )
                ui.button("Sign in with Google", on_click=lambda: go(f"{BASE}/sign-in")).props("no-caps")
                ui.label(
                    "Use the same Google account you linked to the connector in Claude. "
                    "These settings apply only to that account."
                ).classes("text-caption text-grey-7")
            return
        assert s.account is not None
        sid = s.sid
        account = s.account
        header(s, tab="spaces")
        with body():
            flashes(s)
            last_used = settings.last_used(account)
            if last_used:
                ui.label(f"Connector linked to this account · last used {last_used}").classes("text-grey-7")
            else:
                with ui.column().classes("full-width q-pa-sm bg-orange-1").mark("never-used"):
                    ui.label("Claude has not used this account yet.").classes("text-weight-bold")
                    ui.label(
                        "If you linked a different Google account to the connector in Claude, "
                        "these settings will have no effect."
                    )
            rows = settings.rows(account)
            unattended = sum(r.unattended for r in rows)
            if unattended:
                spaces_word = "space accepts" if unattended == 1 else "spaces accept"
                ui.label(
                    f"{unattended} {spaces_word} messages without confirmation. In Claude, set "
                    "send_message_unattended to “Always allow” only if you need it for scheduled tasks."
                ).classes("full-width q-pa-sm bg-orange-1").mark("unattended-notice")
            if not rows:
                ui.label("Enabled spaces").classes("text-h5")
                with ui.card().classes("full-width items-center").mark("empty-state"):
                    ui.label("Claude cannot access any space yet").classes("text-h6")
                    ui.label(
                        "Add the spaces you want to make available. For each one, choose whether "
                        "Claude can read, write, or both."
                    )
                    ui.button("Add your first space", on_click=lambda: go(f"{BASE}/spaces/add")).props("no-caps")
                return
            with ui.row().classes("full-width items-center justify-between"):
                ui.label("Enabled spaces").classes("text-h5")
                ui.button("Add space", icon="add", on_click=lambda: go(f"{BASE}/spaces/add")).props("no-caps")
            with ui.card().classes("full-width"):
                for r in rows:
                    with ui.row().classes("full-width items-center q-py-xs").mark(f"row-{space_id(r.space_name)}"):
                        ui.label(r.display_name).classes("text-weight-medium col-3")
                        ui.label(r.space_name).classes("text-mono text-grey-7 col-3")
                        with ui.row().classes("col q-gutter-xs"):
                            if r.orphaned:
                                ui.label("You are no longer a member").classes("text-orange-9")
                            else:
                                for label in r.labels:
                                    colour = {
                                        "Read": "blue-1",
                                        "Write without confirmation": "amber-3",
                                    }.get(label, "green-1")
                                    ui.badge(label, color=colour, text_color="black")
                            if r.suspended:
                                ui.label("Suspended by your administrator").classes("text-red-8")
                        if r.orphaned:
                            name = r.space_name

                            def do_remove(name: str = name) -> None:
                                cur = current(sid)
                                if cur is None:
                                    return
                                try:
                                    settings.remove(cur, name)
                                except FlowError as e:
                                    cur.flash.append(("error", e.message))
                                go(BASE)

                            ui.button("Remove", on_click=do_remove).props(
                                "outline color=negative no-caps dense"
                            ).mark(f"remove-{space_id(r.space_name)}")
                        else:
                            sp_id = space_id(r.space_name)
                            ui.button("Edit", on_click=lambda sp_id=sp_id: go(f"{BASE}/spaces/{sp_id}")).props(
                                "flat no-caps dense"
                            ).mark(f"edit-{sp_id}")
            ui.label(
                "Only the spaces listed here are reachable by Claude. Google still applies your own "
                "membership: a space you leave stops working even if it stays in this list."
            ).classes("text-caption text-grey-7")
            ui.link("History of changes", f"{BASE}/history")

    # -- direct messages (decision 4 on #29: notice only) -------------------

    @ui.page(f"{BASE}/dms", title=TITLE)
    def dms_page(request: Request) -> None:
        s = require(request)
        if s is None:
            return
        header(s, tab="dms")
        with body():
            with ui.card().classes("full-width").mark("dm-notice"):
                ui.label("Direct messages and group chats are not available yet").classes("text-h6")
                ui.label("Claude cannot read or post in them. Only named spaces can be enabled for now.")

    # -- picker (flow step 4) ----------------------------------------------

    @ui.page(f"{BASE}/spaces/add", title=TITLE)
    def add_page(request: Request) -> None:
        s = require(request)
        if s is None:
            return
        assert s.account is not None
        sid = s.sid
        account = s.account
        header(s, tab="spaces")
        candidates = settings.candidates(account)
        enabled = settings.enabled(account)
        with body():
            flashes(s)
            ui.link("← Enabled spaces", BASE)
            ui.label("Add space").classes("text-h5")
            with ui.card().classes("full-width"):
                search = ui.input("Search your spaces by name", placeholder="e.g. Team").classes(
                    "full-width"
                ).mark("search")
                with ui.row().classes("full-width items-start"):
                    ref = ui.input(
                        "Or paste a Google Chat link or space ID",
                        placeholder="https://chat.google.com/room/… or spaces/…",
                    ).classes("col").mark("ref")

                    def use_ref() -> None:
                        cur = current(sid)
                        if cur is None or cur.account is None:
                            return
                        try:
                            go(f"{BASE}/spaces/{settings.resolve_ref(cur.account, ref.value or '')}")
                        except FlowError as e:
                            ref_error.set_text(e.message)
                            ref_error.set_visibility(True)

                    ui.button("Use", on_click=use_ref).props("no-caps")
                ref_error = ui.label("").classes("text-negative").mark("ref-error")
                ref_error.set_visibility(False)

            @ui.refreshable
            def results() -> None:
                q = (search.value or "").strip().casefold()
                shown = [sp for sp in candidates if q in sp.display_name.casefold()] if q else candidates
                heading = f"Spaces matching “{search.value.strip()}”" if q else "Your spaces"
                ui.label(f"{heading} · {len(shown)}").classes("text-subtitle1")
                ui.label("Names are not unique: check the resource name.").classes("text-caption text-grey-7")
                if not shown:
                    ui.label(
                        "No space matches. Try another name, or paste the link of the space."
                        if q else "You are not a member of any named space."
                    )
                    return
                with ui.card().classes("full-width"):
                    for sp in shown:
                        sp_id = space_id(sp.name)
                        with ui.row().classes("full-width items-center justify-between").mark(f"pick-{sp_id}"):
                            with ui.column().classes("q-gutter-none"):
                                ui.label(sp.display_name).classes("text-weight-medium")
                                ui.label(sp.name).classes("text-mono text-caption text-grey-7")
                            if sp.name in enabled:
                                with ui.row().classes("items-center"):
                                    ui.label("Already enabled").classes("text-caption text-grey-7")
                                    ui.button("Edit", on_click=lambda sp_id=sp_id: go(f"{BASE}/spaces/{sp_id}")).props(
                                        "flat no-caps dense"
                                    )
                            else:
                                ui.button("Choose", on_click=lambda sp_id=sp_id: go(f"{BASE}/spaces/{sp_id}")).props(
                                    "no-caps dense"
                                )

            results()
            search.on_value_change(results.refresh)
            ui.label(
                "Only named spaces you are a member of are listed. Direct messages and group chats "
                "are not available."
            ).classes("text-caption text-grey-7")

    # -- permissions (flow step 5) -----------------------------------------

    def not_found(s: Session | None, message: str = "This page does not exist.") -> None:
        header(s)
        with body():
            ui.label("Something went wrong").classes("text-h5")
            ui.label(message).classes("text-negative").mark("error")
            ui.link("← Enabled spaces", BASE)

    @ui.page(f"{BASE}/spaces/{{sp_id}}", title=TITLE)
    def permissions_page(request: Request, sp_id: str, c: str | None = None) -> None:
        s = require(request)
        if s is None:
            return
        assert s.account is not None
        if not rules.SPACE_ID_RE.match(sp_id):
            not_found(s)
            return
        sid = s.sid
        account = s.account
        name = f"spaces/{sp_id}"
        entry = settings.acl.get_entry(account.sub, name)
        try:
            space = settings.space_for_change(account, name)
        except FlowError as e:
            if e.not_member:
                s.flash.append(("error", e.message))
                go(BASE if entry else f"{BASE}/spaces/add")
                return
            not_found(s, e.message)
            return
        shown = entry.permissions if entry else Permissions()
        pending = settings.pending(s, name, c)
        if pending is not None:
            shown = pending.after
        policy = settings.policy.policy_for(account)

        header(s, tab="spaces")
        with body().style("max-width: 640px"):
            flashes(s)
            ui.link("← Enabled spaces" if entry else "← Add space", BASE if entry else f"{BASE}/spaces/add")
            ui.label(space.display_name).classes("text-h5").mark("space-name")
            ui.label(space.name).classes("text-mono text-grey-7")
            error = ui.label("").classes("full-width q-pa-sm bg-red-1").mark("form-error")
            error.set_visibility(False)
            with ui.card().classes("full-width"):
                ui.label("What can Claude do in this space?").classes("text-subtitle1")
                read = ui.checkbox("Read", value=shown.read).mark("read")
                ui.label("Claude can read messages and the member list.").classes("text-caption text-grey-7")
                if not policy.allow_read:
                    read.disable()
                    ui.label("Read not allowed by your administrator.").classes("text-red-8")
                write = ui.checkbox("Write", value=shown.write).mark("write")
                ui.label("Claude can post messages as you.").classes("text-caption text-grey-7")
                if not policy.allow_write:
                    write.disable()
                    ui.label("Write not allowed by your administrator.").classes("text-red-8")
                ui.label("How messages are posted (only if Write is checked)").classes("q-mt-sm")
                modes = {"approval": "With your approval in Claude (send_message)"}
                if policy.allow_unattended:
                    modes["unattended"] = (
                        "Without confirmation, for scheduled tasks (send_message_unattended)"
                    )
                mode = ui.radio(modes, value="unattended" if shown.unattended else "approval").mark("mode")
                if not policy.allow_unattended:
                    ui.label("Write without confirmation not allowed by your administrator.").classes(
                        "text-red-8"
                    ).mark("policy-unattended")
                # Disabled while Write is unchecked; the server ignores it then anyway.
                mode.set_enabled(bool(shown.write))
                write.on_value_change(lambda e: mode.set_enabled(bool(e.value)))

                def do_continue() -> None:
                    cur = current(sid)
                    if cur is None:
                        return
                    try:
                        token = settings.submit(
                            cur, name, read=bool(read.value), write=bool(write.value), mode=str(mode.value)
                        )
                    except FlowError as e:
                        if e.not_member:
                            cur.flash.append(("error", e.message))
                            go(f"{BASE}/spaces/add")
                            return
                        text = e.message
                        if entry and text == "Choose at least one permission.":
                            text += " To stop Claude reaching this space, use Remove below."
                        error.set_text(text)
                        error.set_visibility(True)
                        return
                    go(BASE if token is None else f"{BASE}/spaces/{sp_id}/confirm?c={token}")

                with ui.row():
                    ui.button("Cancel", on_click=lambda: go(BASE if entry else f"{BASE}/spaces/add")).props(
                        "flat no-caps"
                    )
                    ui.button("Continue", on_click=do_continue).props("no-caps").mark("continue")
            if entry:
                with ui.card().classes("full-width"):
                    ui.label("Remove from Claude").classes("text-subtitle1")
                    ui.label("Claude will no longer read or post in this space.").classes(
                        "text-caption text-grey-7"
                    )

                    def do_remove() -> None:
                        cur = current(sid)
                        if cur is None:
                            return
                        try:
                            settings.remove(cur, name)
                        except FlowError as e:
                            cur.flash.append(("error", e.message))
                        go(BASE)

                    ui.button("Remove", on_click=do_remove).props("outline color=negative no-caps").mark("remove")

    # -- confirmation (flow step 6) ----------------------------------------

    @ui.page(f"{BASE}/spaces/{{sp_id}}/confirm", title=TITLE)
    def confirm_page(request: Request, sp_id: str, c: str | None = None) -> None:
        s = require(request)
        if s is None:
            return
        assert s.account is not None
        if not rules.SPACE_ID_RE.match(sp_id):
            not_found(s)
            return
        sid = s.sid
        account = s.account
        name = f"spaces/{sp_id}"
        p = settings.pending(s, name, c)
        if p is None:
            s.flash.append(("error", "This confirmation has expired. Choose the permissions again."))
            go(f"{BASE}/spaces/{sp_id}")
            return
        acl_after = rules.acl_with_change(
            settings.acl.list_entries(account.sub), name, p.after, p.display_name
        )
        warnings = rules.warnings(name, p.before, p.after, acl_after, settings.names(account))
        heading, verb = confirm_wording(p)
        adds_unattended = p.after.unattended and not (p.before and p.before.unattended)
        token = p.token

        header(s, tab="spaces")
        with body().style("max-width: 640px"):
            ui.link("← Permissions", f"{BASE}/spaces/{sp_id}?c={token}")
            ui.label(heading).classes("text-h5")
            with ui.card().classes("full-width"):
                statement = (
                    f"You are allowing Claude to {verb} «{p.display_name}» as {account.email}"
                    + (", with nobody approving each message." if adds_unattended else ".")
                )
                ui.label(statement).mark("statement")
                ui.label(f"Space: {p.display_name} ({p.space_name})")
                ui.label(f"Read: {'Yes' if p.after.read else 'No'}")
                if not p.after.write:
                    write_text = "No"
                elif p.after.unattended:
                    write_text = "Without confirmation (send_message_unattended)"
                else:
                    write_text = "With your approval in Claude (send_message)"
                ui.label(f"Write: {write_text}")
            for w in warnings:
                with ui.card().classes("full-width bg-orange-1").mark(f"warning-{w.kind}"):
                    if w.kind == "same_space":
                        ui.label("Claude will read this space and post in it without asking you").classes(
                            "text-h6 text-red-9"
                        )
                        ui.label(
                            f"Any message in «{p.display_name}», including one written by someone else, can "
                            f"lead Claude to post back into «{p.display_name}» with nobody approving. This is "
                            "the riskiest combination: keep it only if the task needs to read this space."
                        )
                    elif w.kind == "unattended_readers":
                        ui.label("Text Claude reads elsewhere can make it post here").classes("text-h6")
                        what = "this space" if len(w.spaces) == 1 else f"these {len(w.spaces)} spaces"
                        ui.label(
                            f"Messages read from {what} can lead Claude to post in «{p.display_name}» "
                            "without asking you:"
                        )
                        for n in w.spaces:
                            ui.label(f"• {n}")
                        ui.label(
                            "Use this only for a space dedicated to automated output, where an unwanted "
                            "post does little harm."
                        )
                    elif w.kind == "read_reaches_unattended":
                        ui.label("Text in this space can make Claude post elsewhere").classes("text-h6")
                        ui.label(
                            f"Text in «{p.display_name}» will be able to make Claude post, without "
                            "confirmation, in:"
                        )
                        for n in w.spaces:
                            ui.label(f"• {n}")
            if adds_unattended and not warnings:
                with ui.card().classes("full-width bg-orange-1").mark("warning-nobody-approves"):
                    ui.label("Nobody approves these messages").classes("text-h6")
                    ui.label(
                        "Use this only for a space dedicated to automated output, where an unwanted post "
                        "does little harm."
                    )

            def do_confirm() -> None:
                cur = current(sid)
                if cur is None:
                    return
                pc = settings.pending(cur, name, token)
                if pc is None:
                    cur.flash.append(("error", "This confirmation has expired. Choose the permissions again."))
                    go(f"{BASE}/spaces/{sp_id}")
                    return
                try:
                    settings.confirm(cur, pc)
                except FlowError as e:
                    cur.flash.append(("error", e.message))
                    go(f"{BASE}/spaces/add" if e.not_member else f"{BASE}/spaces/{sp_id}")
                    return
                go(BASE)

            with ui.row():
                ui.button("Back", on_click=lambda: go(f"{BASE}/spaces/{sp_id}?c={token}")).props("flat no-caps")
                if adds_unattended:
                    # Dark amber, not the primary colour (mockup departure 2).
                    ui.button("Grant write without confirmation", on_click=do_confirm).props(
                        "color=amber-10 no-caps"
                    ).mark("confirm")
                else:
                    ui.button("Confirm", on_click=do_confirm).props("no-caps").mark("confirm")

    # -- history -----------------------------------------------------------

    @ui.page(f"{BASE}/history", title=TITLE)
    def history_page(request: Request) -> None:
        s = require(request)
        if s is None:
            return
        assert s.account is not None
        header(s, tab="spaces")
        records = sorted(settings.acl.audit_log(s.account.sub), key=lambda r: r.changed_at, reverse=True)[:50]
        names = settings.names(s.account)
        with body():
            ui.link("← Enabled spaces", BASE)
            ui.label("History of changes").classes("text-h5")
            if not records:
                ui.label("No change yet.")
            for r in records:
                before = ", ".join(rules.permission_labels(r.before)) if r.before else "nothing"
                after = ", ".join(rules.permission_labels(r.after)) if r.after else "removed"
                label = names.get(r.space_name, r.space_name)
                ui.label(
                    f"{r.changed_at:%Y-%m-%d %H:%M} UTC · {label} ({r.space_name}): {before} → {after}"
                ).mark("audit-record")
