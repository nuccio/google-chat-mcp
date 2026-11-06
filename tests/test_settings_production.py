"""
The production settings service (issue #94): configuration, entry point,
idle disconnect of tabs, and the whole app on the Firestore emulator
(`pytest -m emulator`) with the Chat API replaced by a fake.
"""

from __future__ import annotations

import builtins
import inspect
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("google.cloud.firestore")

import httpx  # noqa: E402
from nicegui import core, ui  # noqa: E402
from nicegui.testing import User  # noqa: E402
from nicegui.testing.general import nicegui_reset_globals, prepare_simulation  # noqa: E402

from google_chat_mcp.remote.policy import AdminPolicy  # noqa: E402
from google_chat_mcp.settings import __main__ as entry  # noqa: E402
from google_chat_mcp.settings import app as settings_app  # noqa: E402
from google_chat_mcp.settings import idle, production  # noqa: E402
from google_chat_mcp.settings.app import BASE, PendingChange  # noqa: E402
from google_chat_mcp.settings.config import ConfigError, SettingsConfig  # noqa: E402
from google_chat_mcp.settings.firestore import FirestoreSessionStore  # noqa: E402
from google_chat_mcp.settings.models import Account, Permissions, Space  # noqa: E402
from google_chat_mcp.settings.sessions import (  # noqa: E402
    PersistentSessions,
    pending_from_dict,
    pending_to_dict,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
BASE_URL = "https://gcm-settings-abc.europe-west1.run.app"
ENV = {
    "SETTINGS_BASE_URL": BASE_URL,
    "GOOGLE_OAUTH_CLIENT_ID": "id.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "s3cret",
}
ADMIN_ENV = {"ALLOWED_DOMAINS": "example.com"}


# --- configuration --------------------------------------------------------------


def test_config_from_env():
    c = SettingsConfig.from_env({**ENV, "PORT": "9000"})
    assert c.origin == BASE_URL
    assert c.redirect_uri == f"{BASE_URL}/settings/callback"
    assert c.secure and c.port == 9000 and c.host == "0.0.0.0"


def test_config_strips_trailing_slash_and_never_shows_the_secret():
    c = SettingsConfig.from_env({**ENV, "SETTINGS_BASE_URL": BASE_URL + "/"})
    assert c.base_url == BASE_URL
    assert "s3cret" not in repr(c)


@pytest.mark.parametrize(
    "override",
    [
        {"SETTINGS_BASE_URL": ""},
        {"SETTINGS_BASE_URL": "ftp://x.example"},
        {"SETTINGS_BASE_URL": "http://gcm.example.com"},  # plain http off localhost
        {"SETTINGS_BASE_URL": BASE_URL + "/settings"},
        {"GOOGLE_OAUTH_CLIENT_ID": ""},
        {"GOOGLE_OAUTH_CLIENT_SECRET": ""},
        {"PORT": "abc"},
        {"PORT": "70000"},
    ],
)
def test_config_rejects(override):
    with pytest.raises(ConfigError) as e:
        SettingsConfig.from_env({**ENV, **override})
    assert "s3cret" not in str(e.value)


def test_config_allows_plain_http_on_localhost():
    c = SettingsConfig.from_env({**ENV, "SETTINGS_BASE_URL": "http://localhost:8080"})
    assert not c.secure and c.origin == "http://localhost:8080"


def test_config_secret_from_a_file(tmp_path):
    f = tmp_path / "secret"
    f.write_text("from-file\n")
    env = {k: v for k, v in ENV.items() if k != "GOOGLE_OAUTH_CLIENT_SECRET"}
    assert SettingsConfig.from_env({**env, "GOOGLE_OAUTH_CLIENT_SECRET_FILE": str(f)}).client_secret == "from-file"
    with pytest.raises(ConfigError, match="only one"):
        SettingsConfig.from_env({**ENV, "GOOGLE_OAUTH_CLIENT_SECRET_FILE": str(f)})
    with pytest.raises(ConfigError, match="cannot read"):
        SettingsConfig.from_env({**env, "GOOGLE_OAUTH_CLIENT_SECRET_FILE": str(tmp_path / "nope")})


# --- entry point ----------------------------------------------------------------


def test_entry_point_names_the_extra_when_it_is_missing(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "nicegui" or name.startswith("google.cloud.firestore"):
            raise ImportError(name)
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SystemExit) as e:
        entry.main()
    assert "remote" in str(e.value) and "extra" in str(e.value)


def test_entry_point_stops_on_a_bad_configuration(monkeypatch):
    for k in list(os.environ):
        if k in ENV or k in ("ALLOWED_DOMAINS", "DEPLOY_MODE"):
            monkeypatch.delenv(k)
    with pytest.raises(SystemExit, match="Configuration error"):
        entry.main()


def test_production_never_uses_the_mock():
    for module in (entry, production):
        source = inspect.getsource(module)
        code = source.split('"""', 2)[2]
        assert "mock" not in code.replace("`mock.py`", "")


# --- sessions: pending changes round trip ----------------------------------------


def test_pending_changes_round_trip():
    p = PendingChange(
        "tok", "spaces/AAA", "Team", None, Permissions(read=True, write=True), "fp", NOW
    )
    q = PendingChange(
        "t2", "spaces/BBB", "Other", Permissions(read=True), Permissions(read=True, write=True, unattended=True), "fp2", NOW
    )
    assert pending_from_dict(pending_to_dict({"tok": p, "t2": q})) == {"tok": p, "t2": q}
    assert pending_to_dict({}) is None
    assert pending_from_dict(None) == {}


# --- idle disconnect --------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


class _FakeSessions:
    def __init__(self, alive=True):
        self.alive = alive

    def peek(self, sid, now):
        return object() if self.alive else None


def _tabs(alive=True):
    clock = _Clock()
    settings = SimpleNamespace(cookie_name="c", sessions=_FakeSessions(alive))
    return idle.IdleTabs(settings, now=clock), clock, settings


def _client(cid="c1"):
    return SimpleNamespace(id=cid, request=SimpleNamespace(cookies={"c": "sid"}))


def test_tab_closes_after_ten_minutes_without_events():
    tabs, clock, _ = _tabs()
    client = _client()
    tabs.touch(client.id)
    clock.t += timedelta(minutes=9, seconds=59)
    assert not tabs.due(client)
    clock.t += timedelta(seconds=2)
    assert tabs.due(client)


def test_an_event_keeps_the_tab_open():
    tabs, clock, _ = _tabs()
    client = _client()
    tabs.touch(client.id)
    clock.t += timedelta(minutes=9)
    tabs.touch(client.id)  # what the wrapped Client.handle_event does
    clock.t += timedelta(minutes=9)
    assert not tabs.due(client)


def test_tab_closes_when_its_session_has_ended():
    tabs, _, settings = _tabs(alive=False)
    client = _client()
    tabs.touch(client.id)
    assert tabs.due(client)


def test_a_tab_never_seen_before_starts_its_idle_clock_at_the_first_sweep():
    tabs, clock, _ = _tabs()
    client = _client()
    assert not tabs.due(client)
    clock.t += timedelta(minutes=11)
    assert tabs.due(client)


def test_events_are_noted_by_client_handle_event():
    from nicegui import Client

    tabs, _, _ = _tabs()
    original = Client.handle_event
    try:
        # install() wraps Client.handle_event; call the wrapper on a stub.
        calls = []
        Client.handle_event = lambda self, msg: calls.append(msg)  # type: ignore[method-assign]
        idle.install(SimpleNamespace(cookie_name="c", sessions=_FakeSessions()))
        stub = SimpleNamespace(id="tab-1")
        Client.handle_event(stub, {"id": 1})
        assert calls == [{"id": 1}]
    finally:
        Client.handle_event = original


# --- the whole app on the Firestore emulator ---------------------------------------


class FakeSpaces:
    def __init__(self, spaces):
        self.spaces = spaces

    def list_spaces(self, account):
        return list(self.spaces)

    def get_space(self, account, name):
        return next((s for s in self.spaces if s.name == name), None)


SPACES = [
    Space("spaces/AAABBBCCC", "Team General", "SPACE"),
    Space("spaces/DDDEEEFFF", "Announcements", "SPACE"),
]
ALICE = Account(sub="100000000000000000001", email="alice@example.com", hd="example.com")


class Clock:
    def __init__(self):
        self.t = datetime.now(timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app_env(firestore_db, clock):
    config = SettingsConfig.from_env(ENV)
    admin = AdminPolicy.from_env({**ADMIN_ENV, "POLICY_ALLOW_UNATTENDED": "false"})
    settings = production.create_settings(config, admin, firestore_db, now=clock)
    settings.spaces = FakeSpaces(SPACES)
    return SimpleNamespace(config=config, admin=admin, settings=settings, db=firestore_db)


@pytest.fixture
async def user(app_env):
    with nicegui_reset_globals():
        os.environ["NICEGUI_USER_SIMULATION"] = "true"
        try:
            app_env.tabs = production.install_production(app_env.settings, app_env.config)
            app_env.tabs.now = app_env.settings.now
            prepare_simulation()
            ui.run(None, tailwind=False, binding_refresh_interval=None)
            async with core.app.router.lifespan_context(core.app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(core.app), base_url=BASE_URL
                ) as client:
                    yield User(client)
        finally:
            os.environ.pop("NICEGUI_USER_SIMULATION", None)


def sign_in(app_env, user: User, account: Account = ALICE) -> str:
    """What the callback does after Google answered: a session in Firestore and its cookie."""
    s = app_env.settings.sessions.create_signed_in(
        Account(
            account.sub,
            account.email,
            account.hd,
            "ya29.fake",
            app_env.settings.now() + timedelta(hours=1),
        ),
        app_env.settings.now(),
    )
    user.http_client.cookies.set(app_env.settings.cookie_name, s.sid)
    return s.sid


def stored_acl(app_env):
    return {
        e.space_name: e.permissions
        for e in app_env.settings.acl.list_entries(ALICE.sub)
    }


@pytest.mark.emulator
async def test_sign_in_redirects_to_google_and_stores_a_login_transaction(user, app_env):
    r = await user.http_client.get(f"{BASE}/sign-in")
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("https://accounts.google.com/")
    assert "access_type=online" in loc and "code_challenge_method=S256" in loc
    assert "hd=example.com" in loc  # a single allowed domain
    assert len(list(app_env.db.collection("login_transactions").stream())) == 1
    assert "__Host-gcm_login" in r.headers["set-cookie"]
    # No session exists before Google has answered.
    assert list(app_env.db.collection("sessions").stream()) == []
    assert app_env.settings.cookie_name not in r.headers["set-cookie"]


@pytest.mark.emulator
async def test_signed_in_user_adds_a_space_and_it_lands_in_firestore(user, app_env):
    sign_in(app_env, user)
    await user.open(f"{BASE}/spaces/AAABBBCCC")
    user.find(marker="read").elements.pop().value = True
    user.find(marker="continue").click()
    await user.should_see("Team General")
    assert stored_acl(app_env) == {"spaces/AAABBBCCC": Permissions(read=True)}
    audit = app_env.settings.acl.audit_log(ALICE.sub)
    assert len(audit) == 1 and audit[0].after == Permissions(read=True)


@pytest.mark.emulator
async def test_policy_from_the_environment_is_applied(user, app_env):
    sign_in(app_env, user)
    with pytest.raises(settings_app.FlowError, match="not allowed"):
        s = app_env.settings.signed_in(user.http_client.cookies.get(app_env.settings.cookie_name))
        app_env.settings.submit(s, "spaces/AAABBBCCC", read=False, write=True, mode="unattended")


@pytest.mark.emulator
async def test_space_allowlist_hides_and_refuses_other_spaces(app_env, user):
    app_env.settings.policy = production.AdminPolicySource(
        AdminPolicy.from_env({**ADMIN_ENV, "POLICY_SPACE_ALLOWLIST": "DDDEEEFFF"})
    )
    sign_in(app_env, user)
    s = app_env.settings.signed_in(user.http_client.cookies.get(app_env.settings.cookie_name))
    assert [sp.name for sp in app_env.settings.candidates(s.account)] == ["spaces/DDDEEEFFF"]
    with pytest.raises(settings_app.FlowError, match="administrator"):
        app_env.settings.submit(s, "spaces/AAABBBCCC", read=True, write=False, mode="approval")


@pytest.mark.emulator
async def test_confirmation_survives_in_the_session_document(user, app_env):
    sid = sign_in(app_env, user)
    s = app_env.settings.signed_in(sid)
    token = app_env.settings.submit(s, "spaces/AAABBBCCC", read=True, write=True, mode="approval")
    assert token is not None
    # A new object, as another instance would build it from Firestore.
    again = app_env.settings.signed_in(sid)
    p = app_env.settings.pending(again, "spaces/AAABBBCCC", token)
    assert p is not None and p.after == Permissions(read=True, write=True)
    app_env.settings.confirm(again, p)
    assert stored_acl(app_env) == {"spaces/AAABBBCCC": Permissions(read=True, write=True)}
    assert app_env.settings.pending(app_env.settings.signed_in(sid), "spaces/AAABBBCCC", token) is None


@pytest.mark.emulator
async def test_sign_out_deletes_the_session_document(user, app_env):
    sid = sign_in(app_env, user)
    await user.open(BASE)
    await user.should_see("alice@example.com")
    assert len(list(app_env.db.collection("sessions").stream())) == 1
    user.find("Sign out").click()
    assert list(app_env.db.collection("sessions").stream()) == []
    assert app_env.settings.signed_in(sid) is None


@pytest.mark.emulator
async def test_session_ends_after_the_idle_timeout(user, app_env, clock):
    sid = sign_in(app_env, user)
    clock.advance(minutes=29)
    assert app_env.settings.signed_in(sid) is not None
    clock.advance(minutes=31)
    assert app_env.settings.signed_in(sid) is None


@pytest.mark.emulator
async def test_peek_is_not_activity(app_env, clock):
    store = FirestoreSessionStore(app_env.db)
    s = store.create(
        sub="1", email="a@example.com", access_token="t",
        token_expires_at=clock() + timedelta(hours=1), now=clock(),
    )
    clock.advance(minutes=20)
    assert store.peek(s.session_id, clock()) is not None
    assert store.get(s.session_id, clock()) is not None  # touched here
    clock.advance(minutes=20)
    assert store.peek(s.session_id, clock()) is not None  # 20 min since the touch


@pytest.mark.emulator
async def test_headers_hsts_and_origin_check(user, app_env):
    r = await user.http_client.get(BASE)
    assert r.headers["strict-transport-security"] == "max-age=31536000"
    assert r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    ws = "/_nicegui_ws/socket.io/?EIO=4&transport=polling"
    assert (await user.http_client.get(ws, headers={"Origin": "https://evil.example"})).status_code == 403
    assert (await user.http_client.get(ws)).status_code == 403
    assert (await user.http_client.get(ws, headers={"Origin": BASE_URL})).status_code != 403


@pytest.mark.emulator
async def test_paused_page_and_root(user):
    assert "Reload to continue" in (await user.http_client.get(f"{BASE}/paused")).text
    assert (await user.http_client.get("/")).headers["location"] == BASE


@pytest.mark.emulator
async def test_development_deploy_mode_shows_a_banner(app_env, user):
    app_env.settings.banner = production.DEVELOPMENT_BANNER
    await user.open(BASE)
    await user.should_see(marker="banner")


@pytest.mark.emulator
async def test_two_users_do_not_see_each_other(user, app_env):
    bob = Account("100000000000000000002", "bob@example.com", "example.com")
    sid = sign_in(app_env, user)
    s = app_env.settings.signed_in(sid)
    app_env.settings.submit(s, "spaces/AAABBBCCC", read=True, write=False, mode="approval")
    assert app_env.settings.acl.list_entries(bob.sub) == []
    sign_in(app_env, user, bob)
    await user.open(BASE)
    await user.should_see("bob@example.com")
    await user.should_not_see("Team General")


@pytest.mark.emulator
async def test_sweep_sends_an_idle_tab_to_the_paused_page(user, app_env, clock):
    sign_in(app_env, user)
    await user.open(BASE)
    await user.should_see("alice@example.com")
    assert app_env.tabs.sweep() == []  # just opened
    clock.advance(minutes=11)
    assert len(app_env.tabs.sweep()) == 1
