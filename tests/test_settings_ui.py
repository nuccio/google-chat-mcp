"""
The /settings pages (NiceGUI) with the in-memory backend.

Each test runs NiceGUI's user simulation: pages are built on the server and
events are dispatched as a browser would, without a real browser.
"""

from __future__ import annotations

import inspect
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

pytest.importorskip("nicegui")

from nicegui import core, ui  # noqa: E402
from nicegui.testing import User  # noqa: E402
from nicegui.testing.general import nicegui_reset_globals, prepare_simulation  # noqa: E402

from google_chat_mcp.settings import app as settings_app  # noqa: E402
from google_chat_mcp.settings.app import BASE, FlowError, SettingsApp  # noqa: E402
from google_chat_mcp.settings.mock import (  # noqa: E402
    ALICE,
    BOB,
    CAROL,
    MALICIOUS_NAME,
    create_mock_settings,
    install_mock,
)
from google_chat_mcp.settings.models import InvalidPermissions, Permissions  # noqa: E402

pytestmark = pytest.mark.anyio

ORIGIN = "http://test"


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def settings(clock) -> SettingsApp:
    return create_mock_settings(now=clock)


@pytest.fixture
async def user(settings):
    with nicegui_reset_globals():
        os.environ["NICEGUI_USER_SIMULATION"] = "true"
        try:
            install_mock(settings, {ORIGIN})
            prepare_simulation()
            ui.run(None, tailwind=False, binding_refresh_interval=None)
            async with core.app.router.lifespan_context(core.app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(core.app), base_url=ORIGIN
                ) as client:
                    yield User(client)
        finally:
            os.environ.pop("NICEGUI_USER_SIMULATION", None)


async def sign_in(user: User, account) -> None:
    await user.open(f"{BASE}/sign-in")  # follows the redirect to the dev sign-in page
    user.find(marker=f"account-{account.email}").click()
    await user.should_see(marker="account-email")
    await user.should_see(account.email)


def acl(settings, account=ALICE) -> dict[str, Permissions]:
    return {e.space_name: e.permissions for e in settings.acl.list_entries(account.sub)}


def session_of(user: User, settings: SettingsApp):
    return settings.session(user.http_client.cookies.get(settings.cookie_name))


async def choose(user: User, space_id: str, *, read=None, write=None, mode=None) -> None:
    """Open the permissions page, set the boxes, press Continue."""
    await user.open(f"{BASE}/spaces/{space_id}")
    if read is not None:
        user.find(marker="read").elements.pop().value = read
    if write is not None:
        user.find(marker="write").elements.pop().value = write
    if mode is not None:
        user.find(marker="mode").elements.pop().value = mode
    user.find(marker="continue").click()


# --- sign-in, session, headers ------------------------------------------------


async def test_landing_page_offers_sign_in(user):
    await user.open(BASE)
    await user.should_see("Sign in with Google")
    await user.should_not_see(marker="account-email")


async def test_security_headers(user):
    r = await user.http_client.get(BASE)
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "connect-src 'self'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"


async def test_static_files_keep_their_cache_headers(user):
    r = await user.http_client.get(BASE)
    import re

    static = re.search(r'src="(/_nicegui/[^"]+\.js)"', r.text).group(1)
    s = await user.http_client.get(static)
    assert s.status_code == 200
    assert s.headers["cache-control"] != "no-store"
    assert s.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://test.evil.example"])
async def test_websocket_from_another_origin_is_refused(user, origin):
    headers = {"origin": origin} if origin else {}
    r = await user.http_client.get(
        "/_nicegui_ws/socket.io/", params={"EIO": "4", "transport": "polling"}, headers=headers
    )
    assert r.status_code == 403


async def test_websocket_from_our_origin_reaches_nicegui(user):
    r = await user.http_client.get(
        "/_nicegui_ws/socket.io/",
        params={"EIO": "4", "transport": "polling"},
        headers={"origin": ORIGIN},
    )
    assert r.status_code != 403


async def test_session_cookie_flags(user, settings):
    r = await user.http_client.get(f"{BASE}/sign-in")
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{settings.cookie_name}=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie


def test_secure_cookie_name():
    s = create_mock_settings()
    s.secure_cookies = True
    assert SettingsApp(
        spaces=s.spaces, acl=s.acl, usage=s.usage, policy=s.policy, sign_in=s.sign_in
    ).cookie_name == "__Host-gcm_session"


async def test_sign_in_rotates_the_session(user, settings):
    await user.http_client.get(f"{BASE}/sign-in")
    before = user.http_client.cookies.get(settings.cookie_name)
    await sign_in(user, ALICE)
    after = user.http_client.cookies.get(settings.cookie_name)
    assert before and after and before != after
    assert settings.session(before) is None


async def test_dev_ticket_from_another_session_is_refused(user, settings):
    await user.http_client.get(f"{BASE}/sign-in")
    own = settings.issue_ticket(user.http_client.cookies.get(settings.cookie_name))
    assert own  # this browser has a ticket of its own, which must not match
    other = settings.sessions.create(settings.now())
    ticket = settings.issue_ticket(other.sid)
    settings.sign_in._chosen[ticket] = ALICE
    await user.open(f"{BASE}/dev/complete?ticket={ticket}")
    await user.should_see("Sign in with Google")
    assert session_of(user, settings).account is None


async def test_sign_out(user, settings):
    await sign_in(user, ALICE)
    sid = user.http_client.cookies.get(settings.cookie_name)
    user.find("Sign out").click()
    await user.should_see("Sign in with Google")
    assert settings.session(sid) is None
    assert user.http_client.cookies.get(settings.cookie_name) is None


async def test_expired_session_does_nothing_and_returns_to_sign_in(user, settings, clock):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/MMM111")
    user.find(marker="read").elements.pop().value = True
    clock.advance(minutes=31)
    user.find(marker="continue").click()
    await user.should_see("Sign in with Google")
    assert "spaces/MMM111" not in acl(settings)


# --- list (step 3) -------------------------------------------------------------


async def test_empty_state_and_account_warning(user):
    await sign_in(user, BOB)
    await user.should_see(marker="empty-state")
    await user.should_see("Claude cannot access any space yet")
    await user.should_see(marker="never-used")


async def test_list_for_a_linked_account(user):
    await sign_in(user, ALICE)
    await user.should_see("last used 2 hours ago")
    await user.should_see("Team General")
    await user.should_see("spaces/AAABBBCCC")
    await user.should_see("Write with approval")
    await user.should_see("Announcements")
    await user.should_see("Old project")
    await user.should_see("You are no longer a member")
    await user.should_not_see(marker="never-used")
    await user.should_see(marker="remove-ZZZ999")  # orphaned: only Remove
    await user.should_not_see(marker="edit-ZZZ999")
    await user.should_see(marker="edit-AAABBBCCC")


async def test_remove_orphaned_entry_writes_audit(user, settings):
    await sign_in(user, ALICE)
    user.find(marker="remove-ZZZ999").click()
    await user.should_see("«Old project» removed. Claude can no longer reach it.")
    assert "spaces/ZZZ999" not in acl(settings)
    (record,) = settings.acl.audit_log(ALICE.sub)
    assert record.space_name == "spaces/ZZZ999" and record.after is None


async def test_unattended_notice(user, settings):
    settings.acl.put_entry(
        ALICE.sub,
        settings_app.AclEntry("spaces/REPORTS01", Permissions(write=True, unattended=True), "Reports"),
        expected_before=None,
        now=settings.now(),
    )
    await sign_in(user, ALICE)
    await user.should_see(marker="unattended-notice")
    await user.should_see("Write without confirmation")


async def test_policy_suspended_entry_is_marked(user, settings):
    settings.acl.put_entry(
        CAROL.sub,
        settings_app.AclEntry("spaces/REPORTS01", Permissions(write=True, unattended=True), "Reports"),
        expected_before=None,
        now=settings.now(),
    )
    await sign_in(user, CAROL)
    await user.should_see("Suspended by your administrator")


async def test_history_page(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True)
    await user.should_see("added: Read")
    await user.open(f"{BASE}/history")
    await user.should_see(marker="audit-record")
    await user.should_see("nothing → Read")


# --- direct messages (decision 4 on #29) ------------------------------------------


async def test_dm_tab_shows_only_a_notice(user):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/dms")
    await user.should_see(marker="dm-notice")
    await user.should_see("Direct messages and group chats are not available yet")


@pytest.mark.parametrize("space_id", ["DM00001", "GRP0001"])
async def test_dm_permissions_page_is_refused(user, space_id):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/{space_id}")
    await user.should_see("Direct messages and group chats cannot be enabled.")
    await user.should_not_see(marker="continue")


@pytest.mark.parametrize("space_id", ["DM00001", "GRP0001"])
def test_dms_and_group_chats_are_refused_on_save(settings, space_id):
    s = settings.sessions.create(settings.now())
    s.account = ALICE
    with pytest.raises(FlowError, match="cannot be enabled"):
        settings.submit(s, f"spaces/{space_id}", read=True, write=False, mode="approval")
    assert f"spaces/{space_id}" not in acl(settings)


# --- picker (step 4) -------------------------------------------------------------


async def test_picker_lists_only_named_spaces_with_resource_names(user):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/add")
    await user.should_see("spaces/AAABBBCCC")
    await user.should_see("spaces/QQQ222")  # same name, different resource
    await user.should_see("Team Marketing")
    await user.should_not_see(marker="pick-DM00001")
    await user.should_not_see(marker="pick-GRP0001")
    await user.should_see("Already enabled")


async def test_picker_search(user):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/add")
    user.find(marker="search").type("market")
    await user.should_see("Team Marketing")
    await user.should_not_see(marker="pick-AAABBBCCC")


@pytest.mark.parametrize(
    "ref", ["https://chat.google.com/room/MMM111", "spaces/MMM111", "MMM111"]
)
async def test_paste_link(user, ref):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/add")
    user.find(marker="ref").type(ref)
    user.find("Use").click()
    await user.should_see(marker="space-name")
    await user.should_see("Team Marketing")


@pytest.mark.parametrize(
    "ref, message",
    [
        ("not a link", "This is not a Google Chat link or space ID."),
        ("spaces/NOPE01", "No space with this ID among the spaces you are a member of."),
        ("spaces/DM00001", "Direct messages and group chats cannot be added."),
    ],
)
async def test_paste_link_errors(user, ref, message):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/add")
    user.find(marker="ref").type(ref)
    user.find("Use").click()
    await user.should_see(message)


# --- permissions (step 5) ----------------------------------------------------------


async def test_nothing_preselected_on_a_new_space(user):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/MMM111")
    assert user.find(marker="read").elements.pop().value is False
    assert user.find(marker="write").elements.pop().value is False
    assert user.find(marker="mode").elements.pop().enabled is False
    await user.should_not_see(marker="remove")


async def test_edit_shows_current_permissions_and_remove(user):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/AAABBBCCC")
    assert user.find(marker="read").elements.pop().value is True
    assert user.find(marker="write").elements.pop().value is True
    await user.should_see(marker="remove")


async def test_write_mode_is_enabled_only_with_write(user):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/MMM111")
    mode = user.find(marker="mode").elements.pop()
    assert not mode.enabled
    user.find(marker="write").click()
    assert mode.enabled


async def test_policy_hides_unattended(user):
    await sign_in(user, CAROL)
    await user.open(f"{BASE}/spaces/REPORTS01")
    await user.should_see(marker="policy-unattended")
    mode = user.find(marker="mode").elements.pop()
    assert "unattended" not in mode.options


async def test_invalid_space_id_shows_not_found(user):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/bad$id")
    await user.should_see("This page does not exist.")


async def test_malicious_space_name_is_text_everywhere(user, settings):
    await sign_in(user, ALICE)
    for path in (f"{BASE}/spaces/add", f"{BASE}/spaces/XSS0001"):
        r = await user.http_client.get(path)
        assert "<script>alert" not in r.text
        assert "onerror=alert(1)>" not in r.text
        await user.open(path)
        await user.should_see(MALICIOUS_NAME)  # shown as text, verbatim
    await choose(user, "XSS0001", read=True)
    r = await user.http_client.get(BASE)
    assert "<script>alert" not in r.text
    await user.open(BASE)
    await user.should_see(MALICIOUS_NAME)


def test_pages_use_no_html_injection_helpers():
    """Rendering rules: none of the elements that render HTML or run code."""
    source = inspect.getsource(settings_app)
    code = source.split('"""', 2)[2]  # skip the module docstring, which names them
    for forbidden in ("ui.html", "ui.markdown", "run_javascript", "add_head_html", "add_body_html", 'props(":'):
        assert forbidden not in code, forbidden


def test_no_route_or_page_takes_a_user_identifier():
    install_source = inspect.getsource(settings_app.install)
    for name in ("sub", "user", "email", "uid"):
        assert f"{{{name}}}" not in install_source
        assert f", {name}: str" not in install_source


# --- saving (step 7) ------------------------------------------------------------------


async def test_read_without_unattended_spaces_saves_directly(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True)
    await user.should_see(marker="flash-success")
    await user.should_see("«Team Marketing» added: Read.")
    assert acl(settings)["spaces/MMM111"] == Permissions(read=True)


async def test_audit_record_per_change(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True)
    await user.should_see(marker="flash-success")
    (record,) = settings.acl.audit_log(ALICE.sub)
    assert (record.space_name, record.before, record.after) == ("spaces/MMM111", None, Permissions(read=True))


async def test_unchanged_permissions_write_nothing(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "DDDEEEFFF")
    await user.should_see("nothing changed")
    assert settings.acl.audit_log(ALICE.sub) == []


async def test_at_least_one_permission(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "MMM111")
    await user.should_see("Choose at least one permission.")
    assert "spaces/MMM111" not in acl(settings)


async def test_unattended_mode_without_write_is_ignored(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True, mode="unattended")
    await user.should_see(marker="flash-success")
    assert acl(settings)["spaces/MMM111"] == Permissions(read=True)


def test_unknown_write_mode_is_refused(settings):
    s = settings.sessions.create(settings.now())
    s.account = ALICE
    with pytest.raises(FlowError, match="not allowed"):
        settings.submit(s, "spaces/MMM111", read=True, write=True, mode="other")


def test_store_refuses_unattended_without_write(settings):
    with pytest.raises(InvalidPermissions):
        settings.acl.put_entry(
            ALICE.sub,
            settings_app.AclEntry("spaces/MMM111", Permissions(unattended=True)),
            expected_before=None,
            now=settings.now(),
        )


def test_policy_forbidden_permission_is_refused_on_the_server(settings):
    s = settings.sessions.create(settings.now())
    s.account = CAROL
    with pytest.raises(FlowError, match="not allowed"):
        settings.submit(s, "spaces/REPORTS01", read=False, write=True, mode="unattended")
    assert acl(settings, CAROL) == {}


async def test_space_the_user_is_not_member_of(user, settings):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/NOPE01")
    await user.should_see("You are no longer a member of this space.")
    await user.should_see("Add space")


async def test_left_the_space_between_picking_and_saving(user, settings):
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/MMM111")
    user.find(marker="read").elements.pop().value = True
    settings.spaces.leave(ALICE, "spaces/MMM111")
    user.find(marker="continue").click()
    await user.should_see("You are no longer a member of this space.")
    assert "spaces/MMM111" not in acl(settings)


# --- confirmation (step 6) ---------------------------------------------------------------


async def test_write_with_approval_needs_confirmation(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True, write=True)
    await user.should_see("Confirm write access")
    await user.should_see("You are allowing Claude to read and post in «Team Marketing» as alice@example.com.")
    assert "spaces/MMM111" not in acl(settings)
    user.find(marker="confirm").click()
    await user.should_see(marker="flash-success")
    assert acl(settings)["spaces/MMM111"] == Permissions(read=True, write=True)


async def test_read_with_unattended_spaces_warns(user, settings):
    settings.acl.put_entry(
        ALICE.sub,
        settings_app.AclEntry("spaces/REPORTS01", Permissions(write=True, unattended=True), "Reports"),
        expected_before=None,
        now=settings.now(),
    )
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True)
    await user.should_see("Confirm read access")
    await user.should_see(marker="warning-read_reaches_unattended")
    await user.should_see("• Reports")


async def test_unattended_confirmation_page_and_grant(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "REPORTS01", write=True, mode="unattended")
    await user.should_see("Confirm write without confirmation")
    await user.should_see(marker="warning-unattended_readers")
    await user.should_see("• Announcements")
    await user.should_see("• Team General")
    await user.should_see("Grant write without confirmation")
    await user.should_not_see("sign in to Google again")
    user.find(marker="confirm").click()  # no re-authentication (decision on #29)
    await user.should_see(marker="flash-success")
    assert acl(settings)["spaces/REPORTS01"] == Permissions(write=True, unattended=True)


async def test_read_and_unattended_on_the_same_space(user):
    await sign_in(user, BOB)
    await choose(user, "REPORTS01", read=True, write=True, mode="unattended")
    await user.should_see(marker="warning-same_space")


async def test_confirmation_is_bound_to_the_change(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True, write=True)
    await user.should_see(marker="confirm")
    token = next(iter(session_of(user, settings).pending))
    await user.open(f"{BASE}/spaces/QQQ222/confirm?c={token}")
    await user.should_see("This confirmation has expired")
    assert "spaces/QQQ222" not in acl(settings)


async def test_confirmation_is_bound_to_the_session(user, settings):
    other = settings.sessions.create(settings.now())
    other.account = ALICE
    token = settings.submit(other, "spaces/MMM111", read=True, write=True, mode="approval")
    assert token
    await sign_in(user, ALICE)
    await user.open(f"{BASE}/spaces/MMM111/confirm?c={token}")
    await user.should_see("This confirmation has expired")


async def test_confirmation_is_void_if_the_acl_changed(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True, write=True)
    await user.should_see(marker="confirm")
    settings.acl.put_entry(
        ALICE.sub,
        settings_app.AclEntry("spaces/QQQ222", Permissions(read=True), "Team General"),
        expected_before=None,
        now=settings.now(),
    )
    user.find(marker="confirm").click()
    await user.should_see("Your settings changed while you were confirming.")
    assert "spaces/MMM111" not in acl(settings)


async def test_confirmation_expires(user, settings, clock):
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True, write=True)
    await user.should_see(marker="confirm")
    clock.advance(minutes=11)
    user.find(marker="confirm").click()
    await user.should_see("This confirmation has expired")
    assert "spaces/MMM111" not in acl(settings)


async def test_membership_is_checked_again_on_confirm(user, settings):
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True, write=True)
    await user.should_see(marker="confirm")
    settings.spaces.leave(ALICE, "spaces/MMM111")
    user.find(marker="confirm").click()
    await user.should_see("You are no longer a member of this space.")
    assert "spaces/MMM111" not in acl(settings)


async def test_back_keeps_the_chosen_permissions(user):
    await sign_in(user, ALICE)
    await choose(user, "MMM111", read=True, write=True)
    await user.should_see(marker="confirm")
    user.find("Back").click()
    await user.should_see(marker="continue")
    assert user.find(marker="read").elements.pop().value is True
    assert user.find(marker="write").elements.pop().value is True


async def test_downgrading_unattended_needs_no_confirmation(user, settings):
    settings.acl.put_entry(
        ALICE.sub,
        settings_app.AclEntry("spaces/REPORTS01", Permissions(write=True, unattended=True), "Reports"),
        expected_before=None,
        now=settings.now(),
    )
    await sign_in(user, ALICE)
    await choose(user, "REPORTS01", mode="approval")
    await user.should_see(marker="flash-success")
    assert acl(settings)["spaces/REPORTS01"] == Permissions(write=True)
