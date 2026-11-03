"""
HTTP tests of the /settings pages against the mocked backend.

They follow docs/remote/03-settings-ui-flow.md: sign-in, list, picker,
permissions, confirmation, re-authentication, server-side checks on every
POST, CSRF, escaping and security headers.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("jinja2")

from starlette.testclient import TestClient  # noqa: E402

from google_chat_mcp.settings.mock import (  # noqa: E402
    ALICE,
    BOB,
    CAROL,
    MALICIOUS_NAME,
    create_mock_app,
)
from google_chat_mcp.settings.models import (  # noqa: E402
    AclEntry,
    InvalidPermissions,
    Permissions,
)


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(clock):
    return create_mock_app(now=clock)


@pytest.fixture
def settings(app):
    return app.state.settings


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def sign_in(client, account):
    r = client.get("/settings/sign-in")
    assert r.status_code == 303
    page = client.get(r.headers["location"])
    r = client.post(
        "/settings/dev/sign-in", data={"csrf_token": csrf_of(page.text), "account": account.sub}
    )
    assert r.status_code == 303
    return r


def token(client) -> str:
    return csrf_of(client.get("/settings").text)


def post(client, url, **data):
    data.setdefault("csrf_token", token(client))
    return client.post(url, data=data)


def acl(settings, account=ALICE):
    return {e.space_name: e.permissions for e in settings.acl.list_entries(account.sub)}


def flash_after(client, response):
    assert response.status_code == 303
    return client.get(response.headers["location"]).text


# --- sign-in, headers, cookies -----------------------------------------------


def test_landing_page_offers_sign_in(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Sign in with Google" in r.text
    assert "Development mock" in r.text


def test_security_headers(client):
    for url in ("/settings", "/settings/static/settings.css", "/settings/nope"):
        r = client.get(url)
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/settings").headers["cache-control"] == "no-store"


def test_pages_have_no_javascript(client):
    sign_in(client, ALICE)
    for url in (
        "/settings",
        "/settings/spaces/add",
        "/settings/spaces/MMM111",
    ):
        assert "<script" not in client.get(url).text.lower()


def test_session_cookie_flags(client):
    r = sign_in(client, ALICE)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_sign_in_rotates_the_session(client):
    client.get("/settings/sign-in")
    before = client.cookies.get("settings_session")
    sign_in(client, ALICE)
    assert client.cookies.get("settings_session") != before


def test_dev_sign_in_is_not_part_of_the_plain_app():
    from google_chat_mcp.settings import app as app_module

    assert "dev/sign-in" not in open(app_module.__file__).read()


def test_sign_out(client):
    sign_in(client, ALICE)
    r = post(client, "/settings/sign-out")
    assert r.status_code == 303
    assert "Sign in with Google" in client.get("/settings").text


# --- enabled spaces (step 3) ---------------------------------------------------


def test_empty_state_and_account_warning(client):
    sign_in(client, BOB)
    html = client.get("/settings").text
    assert "Claude cannot access any space yet" in html
    assert "Add your first space" in html
    assert "Claude has not used this account yet." in html


def test_list_for_a_linked_account(client):
    sign_in(client, ALICE)
    html = client.get("/settings").text
    assert "Claude has not used this account yet" not in html
    assert "last used 2 hours ago" in html
    assert "spaces/AAABBBCCC" in html
    assert "Write with approval" in html
    # Orphaned entry: only Remove, no Edit link.
    assert "You are no longer a member" in html
    assert 'action="/settings/spaces/ZZZ999/remove"' in html
    assert 'href="/settings/spaces/ZZZ999"' not in html
    assert "accept messages without confirmation" not in html
    assert "accepts messages without confirmation" not in html


def test_remove_orphaned_entry_writes_audit(client, settings):
    sign_in(client, ALICE)
    r = post(client, "/settings/spaces/ZZZ999/remove")
    assert "«Old project» removed." in flash_after(client, r)
    assert "spaces/ZZZ999" not in acl(settings)
    last = settings.acl.audit_log(ALICE.sub)[-1]
    assert (last.space_name, last.before, last.after) == (
        "spaces/ZZZ999",
        Permissions(read=True),
        None,
    )


def test_remove_unknown_entry_is_404(client):
    sign_in(client, ALICE)
    assert post(client, "/settings/spaces/MMM111/remove").status_code == 404


# --- picker (step 4) -----------------------------------------------------------


def test_picker_lists_only_named_spaces_with_resource_names(client):
    sign_in(client, ALICE)
    html = client.get("/settings/spaces/add").text
    assert html.count('<span class="name">Team General</span>') == 2
    assert "spaces/AAABBBCCC" in html and "spaces/QQQ222" in html
    assert "DM00001" not in html and "GRP0001" not in html
    # Already enabled: Edit instead of Choose.
    assert 'href="/settings/spaces/AAABBBCCC">Edit' in html


def test_picker_search(client):
    sign_in(client, ALICE)
    html = client.get("/settings/spaces/add", params={"q": "market"}).text
    assert "spaces/MMM111" in html
    assert "spaces/AAABBBCCC" not in html


@pytest.mark.parametrize(
    "ref", ["https://chat.google.com/room/MMM111", "spaces/MMM111", "MMM111"]
)
def test_paste_link(client, ref):
    sign_in(client, ALICE)
    r = client.get("/settings/spaces/add", params={"ref": ref})
    assert r.status_code == 303
    assert r.headers["location"] == "/settings/spaces/MMM111"


@pytest.mark.parametrize(
    "ref, message",
    [
        ("spaces/DM00001", "Direct messages and group chats cannot be added."),
        ("spaces/NOTMINE", "No space with this ID"),
        ("not a link!", "This is not a Google Chat link"),
    ],
)
def test_paste_link_errors(client, ref, message):
    sign_in(client, ALICE)
    r = client.get("/settings/spaces/add", params={"ref": ref})
    assert r.status_code == 400
    assert message in r.text


# --- permissions page (step 5) -------------------------------------------------


def test_nothing_preselected_on_a_new_space(client):
    sign_in(client, ALICE)
    html = client.get("/settings/spaces/MMM111").text
    assert not re.search(r'name="(read|write)"[^>]*checked', html)
    assert re.search(r'value="approval" checked', html)


def test_edit_shows_current_permissions_and_remove(client):
    sign_in(client, ALICE)
    html = client.get("/settings/spaces/AAABBBCCC").text
    assert re.search(r'name="read" checked', html)
    assert re.search(r'name="write" checked', html)
    assert 'action="/settings/spaces/AAABBBCCC/remove"' in html


def test_policy_disables_unattended(client):
    sign_in(client, CAROL)
    html = client.get("/settings/spaces/REPORTS01").text
    assert re.search(r'value="unattended"[^>]*disabled', html)
    assert "Write without confirmation not allowed by your administrator." in html


def test_dm_permissions_page_is_not_shown(client):
    sign_in(client, ALICE)
    assert client.get("/settings/spaces/DM00001").status_code == 404


def test_invalid_space_id_is_404(client):
    sign_in(client, ALICE)
    assert client.get("/settings/spaces/a.b").status_code == 404


# --- escaping ------------------------------------------------------------------


def assert_escaped(html):
    assert "<script>alert" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;alert(&#34;pwned&#34;)&lt;/script&gt;" in html


def test_malicious_space_name_is_escaped_everywhere(client, settings):
    sign_in(client, ALICE)
    assert_escaped(client.get("/settings/spaces/add").text)
    assert_escaped(client.get("/settings/spaces/add", params={"q": "script"}).text)
    assert_escaped(client.get("/settings/spaces/XSS0001").text)
    # Add it for reading (no confirmation needed): flash message and list.
    r = post(client, "/settings/spaces/XSS0001", read="on", mode="approval")
    assert_escaped(flash_after(client, r))
    assert_escaped(client.get("/settings").text)
    assert settings.acl.get_entry(ALICE.sub, "spaces/XSS0001").display_name == MALICIOUS_NAME
    # And in the confirmation warnings of another space.
    r = post(client, "/settings/spaces/REPORTS01", write="on", mode="unattended")
    assert_escaped(client.get(r.headers["location"]).text)


def test_search_query_is_escaped(client):
    sign_in(client, ALICE)
    html = client.get("/settings/spaces/add", params={"q": '"><script>x()</script>'}).text
    assert "<script>x()" not in html


# --- CSRF ----------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "", "forged"])
def test_post_without_valid_csrf_is_refused(client, settings, bad):
    sign_in(client, ALICE)
    before = acl(settings)
    data = {"read": "on", "mode": "approval"}
    if bad is not None:
        data["csrf_token"] = bad
    for url in (
        "/settings/spaces/MMM111",
        "/settings/spaces/ZZZ999/remove",
        "/settings/sign-out",
    ):
        r = client.post(url, data=data)
        assert r.status_code == 403
    assert acl(settings) == before


def test_csrf_token_of_another_session_is_refused(app, settings):
    eve = TestClient(app, follow_redirects=False)
    sign_in(eve, BOB)
    stolen = token(eve)
    alice = TestClient(app, follow_redirects=False)
    sign_in(alice, ALICE)
    r = alice.post("/settings/spaces/MMM111", data={"csrf_token": stolen, "read": "on"})
    assert r.status_code == 403
    assert "spaces/MMM111" not in acl(settings)


def test_dev_sign_in_needs_csrf(client):
    client.get("/settings/sign-in")
    r = client.post("/settings/dev/sign-in", data={"account": ALICE.sub})
    assert r.status_code == 403


# --- save and PRG (step 7) -------------------------------------------------------


def test_read_without_unattended_spaces_saves_directly_with_prg(client, settings):
    sign_in(client, ALICE)
    r = post(client, "/settings/spaces/MMM111", read="on", mode="approval")
    assert r.status_code == 303
    assert r.headers["location"] == "/settings"
    assert acl(settings)["spaces/MMM111"] == Permissions(read=True)
    html = client.get("/settings").text
    assert "«Team Marketing» added: Read." in html
    assert "Claude sees it from its next request; nothing needs restarting." in html
    # The flash message is shown once: reloading does not repeat it.
    assert "«Team Marketing» added" not in client.get("/settings").text


def test_audit_record_per_change(client, settings):
    sign_in(client, ALICE)
    post(client, "/settings/spaces/MMM111", read="on", mode="approval")
    post(client, "/settings/spaces/AAABBBCCC", read="on", mode="approval")  # drop write
    log = settings.acl.audit_log(ALICE.sub)
    assert [(r.space_name, r.before, r.after) for r in log] == [
        ("spaces/MMM111", None, Permissions(read=True)),
        ("spaces/AAABBBCCC", Permissions(read=True, write=True), Permissions(read=True)),
    ]


def test_unchanged_permissions_write_nothing(client, settings):
    sign_in(client, ALICE)
    r = post(client, "/settings/spaces/DDDEEEFFF", read="on", mode="approval")
    assert "nothing changed" in flash_after(client, r)
    assert settings.acl.audit_log(ALICE.sub) == []


# --- server-side checks on every POST (step 7) -----------------------------------


def test_at_least_one_permission(client, settings):
    sign_in(client, ALICE)
    r = post(client, "/settings/spaces/MMM111", mode="approval")
    assert r.status_code == 400
    assert "Choose at least one permission." in r.text
    assert "spaces/MMM111" not in acl(settings)


def test_unattended_mode_without_write_is_ignored(client, settings):
    """The radio cannot be disabled without JavaScript: it only counts with Write."""
    sign_in(client, ALICE)
    r = post(client, "/settings/spaces/MMM111", read="on", mode="unattended")
    assert r.status_code == 303
    assert acl(settings)["spaces/MMM111"] == Permissions(read=True)


def test_unattended_alone_is_refused(client, settings):
    sign_in(client, ALICE)
    r = post(client, "/settings/spaces/MMM111", mode="unattended")
    assert r.status_code == 400
    assert "spaces/MMM111" not in acl(settings)


def test_store_refuses_unattended_without_write(settings):
    with pytest.raises(InvalidPermissions):
        settings.acl.put_entry(
            ALICE.sub,
            AclEntry("spaces/MMM111", Permissions(read=True, unattended=True)),
            expected_before=None,
            now=datetime.now(timezone.utc),
        )


def test_unknown_write_mode_is_refused(client, settings):
    sign_in(client, ALICE)
    r = post(client, "/settings/spaces/MMM111", write="on", mode="always")
    assert r.status_code == 400
    assert "spaces/MMM111" not in acl(settings)


@pytest.mark.parametrize("space_id", ["DM00001", "GRP0001"])
def test_dms_and_group_chats_are_refused(client, settings, space_id):
    sign_in(client, ALICE)
    r = post(client, f"/settings/spaces/{space_id}", read="on", mode="approval")
    assert r.status_code == 400
    assert f"spaces/{space_id}" not in acl(settings)


def test_space_the_user_is_not_member_of(client, settings):
    sign_in(client, ALICE)
    r = post(client, "/settings/spaces/NOTMINE", read="on", mode="approval")
    assert r.headers["location"] == "/settings/spaces/add"
    assert "You are no longer a member of this space." in flash_after(client, r)
    assert "spaces/NOTMINE" not in acl(settings)


def test_left_the_space_between_picking_and_saving(client, settings):
    sign_in(client, ALICE)
    assert client.get("/settings/spaces/MMM111").status_code == 200
    settings.spaces.leave(ALICE, "spaces/MMM111")
    r = post(client, "/settings/spaces/MMM111", read="on", mode="approval")
    assert r.headers["location"] == "/settings/spaces/add"
    assert "You are no longer a member of this space." in flash_after(client, r)
    assert "spaces/MMM111" not in acl(settings)


def test_policy_forbidden_permission(client, settings):
    sign_in(client, CAROL)
    r = post(client, "/settings/spaces/REPORTS01", write="on", mode="unattended")
    assert r.status_code == 403
    assert "This change is not allowed." in r.text
    assert 'action="/settings/spaces/REPORTS01"' in r.text  # the page is shown again
    assert acl(settings, CAROL) == {}


def test_user_id_comes_from_the_session_only(client, settings):
    sign_in(client, BOB)
    post(client, "/settings/spaces/MMM111", read="on", mode="approval", sub=ALICE.sub)
    assert "spaces/MMM111" in acl(settings, BOB)
    assert "spaces/MMM111" not in acl(settings, ALICE)


def test_expired_session_does_nothing_and_returns_to_sign_in(client, settings, clock):
    sign_in(client, ALICE)
    csrf = token(client)
    clock.advance(minutes=31)
    r = client.post(
        "/settings/spaces/MMM111", data={"csrf_token": csrf, "read": "on", "mode": "approval"}
    )
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    assert "Sign in with Google" in client.get("/settings").text
    assert "spaces/MMM111" not in acl(settings)


# --- confirmation (step 6) ---------------------------------------------------------


def start_change(client, space_id, **form):
    r = post(client, f"/settings/spaces/{space_id}", **form)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith(f"/settings/spaces/{space_id}/confirm?c=")
    return location, location.split("c=", 1)[1]


def test_write_with_approval_needs_confirmation(client, settings):
    sign_in(client, ALICE)
    url, change = start_change(client, "MMM111", write="on", mode="approval")
    assert "spaces/MMM111" not in acl(settings)  # nothing written yet
    html = client.get(url).text
    assert "Confirm write access" in html
    assert "You are allowing Claude to post in <strong>«Team Marketing»</strong> as" in html
    r = post(client, "/settings/spaces/MMM111/confirm", change=change)
    assert "«Team Marketing» added: Write with approval." in flash_after(client, r)
    assert acl(settings)["spaces/MMM111"] == Permissions(write=True)


def test_read_with_unattended_spaces_warns(client, settings):
    sign_in(client, ALICE)
    # Make Reports unattended first.
    settings.acl.put_entry(
        ALICE.sub,
        AclEntry("spaces/REPORTS01", Permissions(write=True, unattended=True), "Reports"),
        expected_before=None,
        now=datetime.now(timezone.utc),
    )
    url, change = start_change(client, "MMM111", read="on", mode="approval")
    html = client.get(url).text
    assert "Confirm read access" in html
    assert "Text in «Team Marketing» will be able to make Claude post, without confirmation, in:" in html
    assert "<li>Reports</li>" in html


def test_confirmation_is_bound_to_the_change(client, settings):
    sign_in(client, ALICE)
    _, change = start_change(client, "MMM111", write="on", mode="approval")
    # Another space, a forged token, or a used token: nothing is written.
    for space_id, c in (("QQQ222", change), ("MMM111", "forged")):
        r = post(client, f"/settings/spaces/{space_id}/confirm", change=c)
        assert r.status_code == 303
        assert "This confirmation has expired" in flash_after(client, r)
    assert "spaces/QQQ222" not in acl(settings)
    assert "spaces/MMM111" not in acl(settings)
    post(client, "/settings/spaces/MMM111/confirm", change=change)
    assert acl(settings)["spaces/MMM111"] == Permissions(write=True)
    settings.acl.delete_entry(ALICE.sub, "spaces/MMM111", now=datetime.now(timezone.utc))
    r = post(client, "/settings/spaces/MMM111/confirm", change=change)
    assert "spaces/MMM111" not in acl(settings)


def test_confirmation_is_bound_to_the_session(app, settings):
    a1 = TestClient(app, follow_redirects=False)
    sign_in(a1, ALICE)
    _, change = start_change(a1, "MMM111", write="on", mode="approval")
    a2 = TestClient(app, follow_redirects=False)
    sign_in(a2, ALICE)
    post(a2, "/settings/spaces/MMM111/confirm", change=change)
    assert "spaces/MMM111" not in acl(settings)


def test_confirmation_is_void_if_the_acl_changed(client, settings):
    sign_in(client, ALICE)
    _, change = start_change(client, "MMM111", write="on", mode="approval")
    settings.acl.put_entry(
        ALICE.sub,
        AclEntry("spaces/QQQ222", Permissions(read=True)),
        expected_before=None,
        now=datetime.now(timezone.utc),
    )
    r = post(client, "/settings/spaces/MMM111/confirm", change=change)
    assert "Your settings changed" in flash_after(client, r)
    assert "spaces/MMM111" not in acl(settings)


def test_membership_is_checked_again_on_confirm(client, settings):
    sign_in(client, ALICE)
    _, change = start_change(client, "MMM111", write="on", mode="approval")
    settings.spaces.leave(ALICE, "spaces/MMM111")
    r = post(client, "/settings/spaces/MMM111/confirm", change=change)
    assert r.headers["location"] == "/settings/spaces/add"
    assert "spaces/MMM111" not in acl(settings)


def test_back_keeps_the_chosen_permissions(client):
    sign_in(client, ALICE)
    _, change = start_change(client, "MMM111", read="on", write="on", mode="unattended")
    html = client.get(f"/settings/spaces/MMM111?c={change}").text
    assert re.search(r'name="read" checked', html)
    assert re.search(r'value="unattended" checked', html)


# --- unattended write: warnings and re-authentication ---------------------------------


def test_unattended_confirmation_page(client):
    sign_in(client, ALICE)
    url, _ = start_change(client, "REPORTS01", write="on", mode="unattended")
    html = client.get(url).text
    assert "Confirm write without confirmation" in html
    assert "with nobody approving each message" in html
    assert "Messages read from these 3 spaces can lead Claude to post in «Reports» without asking you:" in html
    assert "<li>Announcements</li>" in html and "<li>Team General</li>" in html
    # Orphaned entries are still in the ACL, so they are listed too.
    assert "<li>Old project</li>" in html
    assert "you will be asked to sign in to Google again" in html
    assert "button-amber" in html and "Grant write without confirmation" in html


def test_read_and_unattended_on_the_same_space(client):
    sign_in(client, ALICE)
    url, _ = start_change(client, "REPORTS01", read="on", write="on", mode="unattended")
    html = client.get(url).text
    assert "risk-danger" in html
    assert html.index("risk-danger") < html.index("Text Claude reads elsewhere")


def reauth(client, account):
    page = client.get("/settings/dev/sign-in")
    assert "Sign in again to confirm" in page.text
    return client.post(
        "/settings/dev/sign-in", data={"csrf_token": csrf_of(page.text), "account": account.sub}
    )


def test_unattended_requires_reauthentication(client, settings, clock):
    sign_in(client, ALICE)
    _, change = start_change(client, "REPORTS01", write="on", mode="unattended")
    r = post(client, "/settings/spaces/REPORTS01/confirm", change=change)
    assert r.headers["location"] == "/settings/dev/sign-in"
    assert "spaces/REPORTS01" not in acl(settings)  # not before re-authentication
    clock.advance(seconds=30)
    r = reauth(client, ALICE)
    html = flash_after(client, r)
    assert "«Reports» added: Write without confirmation." in html
    assert "1 space accepts messages without confirmation." in html
    assert acl(settings)["spaces/REPORTS01"] == Permissions(write=True, unattended=True)
    last = settings.acl.audit_log(ALICE.sub)[-1]
    assert last.after.unattended and last.before is None


def test_reauthentication_with_another_account_changes_nothing(client, settings):
    sign_in(client, ALICE)
    _, change = start_change(client, "REPORTS01", write="on", mode="unattended")
    post(client, "/settings/spaces/REPORTS01/confirm", change=change)
    r = reauth(client, BOB)
    assert "You signed in with a different account. Nothing was changed." in flash_after(client, r)
    assert "spaces/REPORTS01" not in acl(settings)
    assert "spaces/REPORTS01" not in acl(settings, BOB)


def test_late_reauthentication_changes_nothing(client, settings, clock):
    sign_in(client, ALICE)
    _, change = start_change(client, "REPORTS01", write="on", mode="unattended")
    post(client, "/settings/spaces/REPORTS01/confirm", change=change)
    clock.advance(seconds=301)
    r = reauth(client, ALICE)
    assert "not recent enough" in flash_after(client, r)
    assert "spaces/REPORTS01" not in acl(settings)


def test_stale_auth_time_changes_nothing(client, settings, clock):
    """An identity token whose auth_time predates the confirmation does not count."""
    sign_in(client, ALICE)
    _, change = start_change(client, "REPORTS01", write="on", mode="unattended")
    clock.advance(seconds=10)
    post(client, "/settings/spaces/REPORTS01/confirm", change=change)
    stale = clock() - timedelta(seconds=5)
    settings.sign_in.now = lambda: stale
    r = reauth(client, ALICE)
    assert "not recent enough" in flash_after(client, r)
    assert "spaces/REPORTS01" not in acl(settings)


def test_unattended_without_confirm_post_is_never_saved(client, settings):
    """Re-authenticating without having clicked Grant does not apply anything."""
    sign_in(client, ALICE)
    start_change(client, "REPORTS01", write="on", mode="unattended")
    client.get("/settings/switch-account")
    sign_in(client, ALICE)
    assert "spaces/REPORTS01" not in acl(settings)


def test_downgrading_unattended_needs_no_confirmation(client, settings):
    sign_in(client, ALICE)
    _, change = start_change(client, "REPORTS01", write="on", mode="unattended")
    post(client, "/settings/spaces/REPORTS01/confirm", change=change)
    reauth(client, ALICE)
    r = post(client, "/settings/spaces/REPORTS01", write="on", mode="approval")
    assert r.headers["location"] == "/settings"
    assert acl(settings)["spaces/REPORTS01"] == Permissions(write=True)
