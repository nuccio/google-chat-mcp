"""
The `/settings` pages: routes, server-side sessions, CSRF and security headers.

`create_app` takes the backends as arguments (see `backends.py`). It never
installs a fake sign-in by itself: the development sign-in only exists in
`mock.create_mock_app`.
"""

from __future__ import annotations

import hmac
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import jinja2
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
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

_HERE = Path(__file__).parent

BASE = "/settings"
SESSION_IDLE_TIMEOUT = timedelta(minutes=30)
# A re-authentication counts only if it happened after the user confirmed
# this change, and at most this long before the save (`max_age=300`).
REAUTH_MAX_AGE = timedelta(seconds=300)
MAX_PENDING_CHANGES = 10

SECURITY_HEADERS = [
    # form-action 'self' blocks form posts that redirect off-site: when #52
    # redirects a POST to Google for re-authentication, add the Google
    # accounts origin here.
    (
        b"content-security-policy",
        b"default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
    ),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


# ---------------------------------------------------------------------------
# Sessions


@dataclass
class PendingChange:
    """A change waiting for confirmation (and maybe re-authentication)."""

    token: str
    space_name: str
    display_name: str
    before: Permissions | None
    after: Permissions
    acl_fingerprint: str
    needs_reauth: bool
    created_at: datetime
    confirmed_at: datetime | None = None


@dataclass
class Session:
    sid: str
    csrf: str
    last_seen: datetime
    account: Account | None = None
    auth_time: datetime | None = None
    flash: list[tuple[str, str]] = field(default_factory=list)  # (kind, text)
    pending: dict[str, PendingChange] = field(default_factory=dict)
    # What the sign-in in progress is for: {"mode": "sign_in" | "reauth",
    # "pending": token}. Kept here, never in the request.
    sign_in_intent: dict | None = None


class SessionStore:
    """In-memory server-side sessions. The cookie only carries a random ID."""

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
        s = Session(sid=secrets.token_urlsafe(32), csrf=secrets.token_urlsafe(32), last_seen=now)
        with self._lock:
            self._sessions[s.sid] = s
        return s

    def delete(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)


# ---------------------------------------------------------------------------
# Middleware


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in {h for h, _ in SECURITY_HEADERS}
                ]
                headers.extend(SECURITY_HEADERS)
                if not scope["path"].startswith(f"{BASE}/static/"):
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


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


class FormError(Exception):
    """A rejected form: the page is shown again with this message."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _confirm_wording(p: PendingChange) -> dict:
    b = p.before or Permissions()
    adds_read = p.after.read and not b.read
    adds_write = p.after.write and not b.write
    if p.needs_reauth:
        heading, verb = "Confirm write without confirmation", "post in"
    elif adds_write:
        heading = "Confirm write access"
        verb = "read and post in" if adds_read else "post in"
    else:
        heading, verb = "Confirm read access", "read"
    return {"heading": heading, "verb": verb}


@dataclass
class Row:
    space_name: str
    display_name: str
    labels: list[str]
    unattended: bool
    orphaned: bool

    @property
    def space_id(self) -> str:
        return self.space_name.removeprefix("spaces/")


# ---------------------------------------------------------------------------
# The application


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
        self.cookie_name = "__Host-settings" if secure_cookies else "settings_session"
        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(_HERE / "templates"),
            autoescape=True,  # always on; never |safe on user data
            undefined=jinja2.StrictUndefined,
        )
        self.env.globals["dev_mode"] = dev_mode
        self.env.globals["base"] = BASE

    # -- sessions, CSRF, rendering ----------------------------------------

    def session(self, request: Request) -> Session | None:
        return self.sessions.get(request.cookies.get(self.cookie_name), self.now())

    def ensure_session(self, request: Request) -> Session:
        s = self.session(request)
        if s is None:
            s = self.sessions.create(self.now())
            request.state.new_session = s
        return s

    def set_cookie(self, response: Response, session: Session) -> None:
        response.set_cookie(
            self.cookie_name,
            session.sid,
            httponly=True,
            samesite="lax",  # Strict would drop the cookie on the return from Google
            secure=self.secure_cookies,
            path="/",
        )

    def render(
        self, request: Request, template: str, context: dict, status_code: int = 200
    ) -> HTMLResponse:
        s: Session | None = getattr(request.state, "session", None) or self.session(request)
        if s is None:
            s = self.ensure_session(request)
        flash, s.flash = s.flash, []
        ctx = {"session": s, "account": s.account, "csrf_token": s.csrf, "flash": flash}
        ctx.update(context)
        html = self.env.get_template(template).render(ctx)
        response = HTMLResponse(html, status_code=status_code)
        if getattr(request.state, "new_session", None) is s:
            self.set_cookie(response, s)
        return response

    async def post_session(self, request: Request) -> tuple[Session, dict] | Response:
        """
        Session and form of a POST from a signed-in user.

        Without a session (expired, or never signed in) nothing is done and
        the user is sent to sign in, then to the list: the interrupted step
        is not resumed (flow step 8). With a session, the form must carry
        its CSRF token.
        """
        s = self.signed_in(request)
        if s is None:
            return self.redirect(BASE)
        return s, await self.check_csrf(request, s)

    async def check_csrf(self, request: Request, session: Session) -> dict:
        """Parse the form and reject it unless it carries the session's token."""
        form = await request.form()
        token = form.get("csrf_token")
        if not isinstance(token, str) or not hmac.compare_digest(
            token.encode(), session.csrf.encode()
        ):
            raise HTTPException(403, "Invalid or missing CSRF token")
        return {k: v for k, v in form.items() if isinstance(v, str)}

    def redirect(self, url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def signed_in(self, request: Request) -> Session | None:
        s = self.session(request)
        if s is None or s.account is None:
            return None
        request.state.session = s
        return s

    # -- sign-in -----------------------------------------------------------

    def start_sign_in(
        self, request: Request, session: Session, *, select_account: bool = False,
        reauth_pending: str | None = None,
    ) -> Response:
        session.sign_in_intent = (
            {"mode": "reauth", "pending": reauth_pending}
            if reauth_pending
            else {"mode": "sign_in"}
        )
        response = self.sign_in.start(
            request,
            select_account=select_account,
            reauth_for=session.account if reauth_pending else None,
        )
        self.set_cookie(response, session)
        return response

    def finish_sign_in(
        self, request: Request, account: Account, auth_time: datetime
    ) -> Response:
        """
        Called by the SignIn implementation once the identity provider has
        answered. The session is replaced with a new one (new ID, new CSRF
        token), so a session ID planted before sign-in is useless.
        """
        old = self.session(request)
        intent = (old.sign_in_intent if old else None) or {"mode": "sign_in"}
        new = self.sessions.create(self.now())
        new.account = account
        new.auth_time = auth_time
        response: Response = self.redirect(BASE)
        if intent.get("mode") == "reauth" and old is not None:
            pending = old.pending.get(intent.get("pending") or "")
            if old.account is None or account.sub != old.account.sub:
                new.flash.append(
                    ("error", "You signed in with a different account. Nothing was changed.")
                )
            elif pending is None:
                new.flash.append(("error", "This change has expired. Nothing was changed."))
            else:
                try:
                    response = self.apply_pending(new, pending, reauth_time=auth_time)
                except FormError as e:
                    new.flash.append(("error", e.message))
        if old is not None:
            self.sessions.delete(old.sid)
        self.set_cookie(response, new)
        return response

    # -- validation (flow step 7) -----------------------------------------

    def space_for_change(self, account: Account, space_name: str) -> Space:
        space = self.spaces.get_space(account, space_name)
        if space is None:
            raise FormError("You are no longer a member of this space.", 409)
        if space.space_type != SPACE:
            raise FormError("Direct messages and group chats cannot be enabled.", 400)
        return space

    def check_permissions(self, account: Account, after: Permissions) -> None:
        try:
            after.validate()
        except InvalidPermissions as e:
            if "at least one" in str(e):
                raise FormError("Choose at least one permission.")
            raise FormError("This change is not allowed.")
        if not self.policy.policy_for(account).allows(after):
            raise FormError("This change is not allowed.", 403)

    def apply_pending(
        self, session: Session, pending: PendingChange, *, reauth_time: datetime | None
    ) -> Response:
        assert session.account is not None
        account = session.account
        space = self.space_for_change(account, pending.space_name)
        self.check_permissions(account, pending.after)
        acl = self.acl.list_entries(account.sub)
        if rules.acl_fingerprint(acl) != pending.acl_fingerprint:
            raise FormError(
                "Your settings changed while you were confirming. Check the change again."
            )
        if pending.needs_reauth:
            now = self.now()
            if (
                reauth_time is None
                or pending.confirmed_at is None
                or reauth_time < pending.confirmed_at
                or reauth_time - pending.confirmed_at > REAUTH_MAX_AGE
                or now - reauth_time > REAUTH_MAX_AGE
            ):
                raise FormError(
                    "The sign-in was not recent enough. Nothing was changed: confirm again.",
                    403,
                )
        session.pending.pop(pending.token, None)
        return self.save(session, space, pending.before, pending.after)

    def save(
        self, session: Session, space: Space, before: Permissions | None, after: Permissions
    ) -> Response:
        assert session.account is not None
        try:
            self.acl.put_entry(
                session.account.sub,
                AclEntry(space.name, after, space.display_name),
                expected_before=before,
                now=self.now(),
            )
        except ConcurrentChange:
            raise FormError("Your settings changed in the meantime. Check the change again.")
        except InvalidPermissions:
            raise FormError("This change is not allowed.")
        verb = "added" if before is None else "updated"
        session.flash.append(
            (
                "success",
                f"«{space.display_name}» {verb}: {', '.join(rules.permission_labels(after))}.",
            )
        )
        return self.redirect(BASE)

    # -- pages -------------------------------------------------------------

    async def landing(self, request: Request) -> Response:
        s = self.signed_in(request)
        if s is None:
            return self.render(request, "landing.html", {})
        return self.list_page(request, s)

    def names(self, account: Account) -> dict[str, str]:
        return {sp.name: sp.display_name for sp in self.spaces.list_spaces(account)}

    def list_page(self, request: Request, s: Session) -> Response:
        assert s.account is not None
        known = {sp.name: sp for sp in self.spaces.list_spaces(s.account)}
        rows = []
        for e in self.acl.list_entries(s.account.sub):
            sp = known.get(e.space_name)
            rows.append(
                Row(
                    space_name=e.space_name,
                    display_name=sp.display_name if sp else (e.display_name or e.space_name),
                    labels=rules.permission_labels(e.permissions),
                    unattended=e.permissions.unattended,
                    orphaned=sp is None,
                )
            )
        rows.sort(key=lambda r: (r.orphaned, r.display_name.casefold(), r.space_name))
        last_used = self.usage.last_used(s.account.sub)
        return self.render(
            request,
            "spaces_list.html",
            {
                "rows": rows,
                "unattended_count": sum(r.unattended for r in rows),
                "last_used": _ago(last_used, self.now()) if last_used else None,
                "never_used": last_used is None,
            },
        )

    async def add_space(self, request: Request) -> Response:
        s = self.signed_in(request)
        if s is None:
            return self.redirect(BASE)
        assert s.account is not None
        error = None
        ref = request.query_params.get("ref", "")
        if ref:
            name = rules.parse_space_ref(ref)
            space = self.spaces.get_space(s.account, name) if name else None
            if name is None:
                error = "This is not a Google Chat link or space ID."
            elif space is None:
                error = "No space with this ID among the spaces you are a member of."
            elif space.space_type != SPACE:
                error = "Direct messages and group chats cannot be added."
            else:
                return self.redirect(f"{BASE}/spaces/{name.removeprefix('spaces/')}")
        q = request.query_params.get("q", "").strip()
        enabled = {e.space_name for e in self.acl.list_entries(s.account.sub)}
        candidates = [sp for sp in self.spaces.list_spaces(s.account) if sp.space_type == SPACE]
        if q:
            candidates = [sp for sp in candidates if q.casefold() in sp.display_name.casefold()]
        candidates.sort(key=lambda sp: (sp.display_name.casefold(), sp.name))
        return self.render(
            request,
            "add_space.html",
            {"q": q, "ref": ref, "error": error, "spaces": candidates, "enabled": enabled},
            status_code=400 if error else 200,
        )

    def _space_name(self, request: Request) -> str:
        space_id = request.path_params["space_id"]
        if not rules.SPACE_ID_RE.match(space_id):
            raise HTTPException(404)
        return f"spaces/{space_id}"

    def permissions_page(
        self,
        request: Request,
        s: Session,
        space: Space,
        entry: AclEntry | None,
        shown: Permissions | None,
        error: str | None = None,
        status_code: int = 200,
    ) -> Response:
        assert s.account is not None
        return self.render(
            request,
            "permissions.html",
            {
                "space": space,
                "space_id": space.name.removeprefix("spaces/"),
                "entry": entry,
                "shown": shown or Permissions(),
                "policy": self.policy.policy_for(s.account),
                "error": error,
            },
            status_code=status_code,
        )

    async def edit_space(self, request: Request) -> Response:
        s = self.signed_in(request)
        if s is None:
            return self.redirect(BASE)
        assert s.account is not None
        name = self._space_name(request)
        entry = self.acl.get_entry(s.account.sub, name)
        try:
            space = self.space_for_change(s.account, name)
        except FormError as e:
            if e.status_code == 409:
                s.flash.append(("error", e.message))
                return self.redirect(f"{BASE}/spaces/add" if entry is None else BASE)
            raise HTTPException(404, e.message)
        shown = entry.permissions if entry else None
        pending = s.pending.get(request.query_params.get("c", ""))
        if pending is not None and pending.space_name == name:
            shown = pending.after
        return self.permissions_page(request, s, space, entry, shown)

    async def submit_permissions(self, request: Request) -> Response:
        got = await self.post_session(request)
        if isinstance(got, Response):
            return got
        s, form = got
        assert s.account is not None
        account = s.account
        name = self._space_name(request)
        entry = self.acl.get_entry(account.sub, name)
        before = entry.permissions if entry else None
        try:
            space = self.space_for_change(account, name)
        except FormError as e:
            if e.status_code == 409:
                s.flash.append(("error", e.message))
                return self.redirect(f"{BASE}/spaces/add")
            raise HTTPException(e.status_code, e.message)

        write = form.get("write") == "on"
        mode = form.get("mode", "approval")
        after = Permissions(read=form.get("read") == "on", write=write, unattended=False)
        try:
            if mode not in ("approval", "unattended"):
                raise FormError("This change is not allowed.")
            # The write mode only counts when Write is checked: without
            # JavaScript the radio cannot be disabled, so it is ignored.
            after = Permissions(after.read, write, write and mode == "unattended")
            self.check_permissions(account, after)
        except FormError as e:
            return self.permissions_page(
                request, s, space, entry, after, e.message, e.status_code
            )

        if after == before:
            s.flash.append(("info", f"«{space.display_name}»: nothing changed."))
            return self.redirect(BASE)

        acl = self.acl.list_entries(account.sub)
        acl_after = rules.acl_with_change(acl, name, after)
        req = rules.requirements(before, after, acl_after)
        if not req.confirmation:
            try:
                return self.save(s, space, before, after)
            except FormError as e:
                return self.permissions_page(request, s, space, entry, after, e.message)

        if len(s.pending) >= MAX_PENDING_CHANGES:
            s.pending.pop(next(iter(s.pending)))
        token = secrets.token_urlsafe(16)
        s.pending[token] = PendingChange(
            token=token,
            space_name=name,
            display_name=space.display_name,
            before=before,
            after=after,
            acl_fingerprint=rules.acl_fingerprint(acl),
            needs_reauth=req.reauthentication,
            created_at=self.now(),
        )
        return self.redirect(f"{BASE}/spaces/{space.name.removeprefix('spaces/')}/confirm?c={token}")

    def _pending(self, s: Session, name: str, token: str) -> PendingChange | None:
        p = s.pending.get(token)
        return p if p is not None and p.space_name == name else None

    async def confirm_page(self, request: Request) -> Response:
        s = self.signed_in(request)
        if s is None:
            return self.redirect(BASE)
        assert s.account is not None
        name = self._space_name(request)
        pending = self._pending(s, name, request.query_params.get("c", ""))
        if pending is None:
            s.flash.append(("error", "This confirmation has expired. Choose the permissions again."))
            return self.redirect(f"{BASE}/spaces/{name.removeprefix('spaces/')}")
        acl = self.acl.list_entries(s.account.sub)
        acl_after = rules.acl_with_change(acl, name, pending.after, pending.display_name)
        names = self.names(s.account)
        return self.render(
            request,
            "confirm.html",
            {
                "p": pending,
                **_confirm_wording(pending),
                "space_id": name.removeprefix("spaces/"),
                "warnings": rules.warnings(name, pending.before, pending.after, acl_after, names),
            },
        )

    async def submit_confirm(self, request: Request) -> Response:
        got = await self.post_session(request)
        if isinstance(got, Response):
            return got
        s, form = got
        assert s.account is not None
        name = self._space_name(request)
        pending = self._pending(s, name, form.get("change", ""))
        back = f"{BASE}/spaces/{name.removeprefix('spaces/')}"
        if pending is None:
            s.flash.append(("error", "This confirmation has expired. Choose the permissions again."))
            return self.redirect(back)
        if pending.needs_reauth:
            # Check everything now, so the user is not sent to Google for a
            # change that would be refused anyway.
            try:
                self.space_for_change(s.account, name)
                self.check_permissions(s.account, pending.after)
                if rules.acl_fingerprint(self.acl.list_entries(s.account.sub)) != pending.acl_fingerprint:
                    raise FormError(
                        "Your settings changed while you were confirming. Check the change again."
                    )
            except FormError as e:
                s.pending.pop(pending.token, None)
                s.flash.append(("error", e.message))
                return self.redirect(f"{BASE}/spaces/add" if e.status_code == 409 else back)
            pending.confirmed_at = self.now()
            return self.start_sign_in(request, s, reauth_pending=pending.token)
        try:
            return self.apply_pending(s, pending, reauth_time=None)
        except FormError as e:
            s.pending.pop(pending.token, None)
            s.flash.append(("error", e.message))
            return self.redirect(f"{BASE}/spaces/add" if e.status_code == 409 else back)

    async def remove_space(self, request: Request) -> Response:
        got = await self.post_session(request)
        if isinstance(got, Response):
            return got
        s, _ = got
        assert s.account is not None
        name = self._space_name(request)
        entry = self.acl.get_entry(s.account.sub, name)
        if entry is None:
            raise HTTPException(404, "This space is not enabled.")
        self.acl.delete_entry(s.account.sub, name, now=self.now())
        label = self.names(s.account).get(name) or entry.display_name or name
        s.flash.append(("success", f"«{label}» removed. Claude can no longer reach it."))
        return self.redirect(BASE)

    async def start_sign_in_route(self, request: Request) -> Response:
        s = self.ensure_session(request)
        return self.start_sign_in(request, s)

    async def switch_account(self, request: Request) -> Response:
        s = self.ensure_session(request)
        return self.start_sign_in(request, s, select_account=True)

    async def sign_out(self, request: Request) -> Response:
        s = self.session(request)
        if s is not None:
            await self.check_csrf(request, s)
            self.sessions.delete(s.sid)
        response = self.redirect(BASE)
        response.delete_cookie(self.cookie_name, path="/")
        return response

    async def http_error(self, request: Request, exc: Exception) -> Response:
        assert isinstance(exc, HTTPException)
        messages = {
            403: "The request was refused. Reload the page and try again.",
            404: "This page does not exist.",
            405: "This page does not accept this request.",
        }
        message = messages.get(exc.status_code, "Something went wrong.")
        # Details are only shown when the app chose them (never for 403,
        # which would tell an attacker which check failed).
        if exc.status_code not in (403, 405) and exc.detail not in (None, "Not Found"):
            message = exc.detail
        return self.render(
            request, "error.html", {"message": message}, status_code=exc.status_code
        )

    def routes(self) -> list:
        sid = "{space_id:str}"
        return [
            Route(BASE, self.landing, methods=["GET"]),
            Route(f"{BASE}/sign-in", self.start_sign_in_route, methods=["GET"]),
            Route(f"{BASE}/switch-account", self.switch_account, methods=["GET"]),
            Route(f"{BASE}/sign-out", self.sign_out, methods=["POST"]),
            Route(f"{BASE}/spaces/add", self.add_space, methods=["GET"]),
            Route(f"{BASE}/spaces/{sid}", self.edit_space, methods=["GET"]),
            Route(f"{BASE}/spaces/{sid}", self.submit_permissions, methods=["POST"]),
            Route(f"{BASE}/spaces/{sid}/confirm", self.confirm_page, methods=["GET"]),
            Route(f"{BASE}/spaces/{sid}/confirm", self.submit_confirm, methods=["POST"]),
            Route(f"{BASE}/spaces/{sid}/remove", self.remove_space, methods=["POST"]),
            *self.sign_in.routes,
            Mount(f"{BASE}/static", StaticFiles(directory=_HERE / "static"), name="static"),
        ]


def create_app(**kwargs) -> Starlette:
    """Build the ASGI app. Keyword arguments are those of `SettingsApp`."""
    settings = SettingsApp(**kwargs)
    app = Starlette(
        routes=settings.routes(),
        exception_handlers={HTTPException: settings.http_error},
    )
    app.state.settings = settings
    app.add_middleware(SecurityHeadersMiddleware)
    return app
