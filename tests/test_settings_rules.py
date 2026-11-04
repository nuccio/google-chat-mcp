"""Unit tests for the rules of the settings flow (docs/remote/03-settings-ui-flow.md)."""

import pytest

from google_chat_mcp.settings.models import (
    AclEntry,
    InvalidPermissions,
    Permissions,
    Policy,
)
from google_chat_mcp.settings.rules import (
    acl_fingerprint,
    acl_with_change,
    parse_space_ref,
    permission_labels,
    requirements,
    warnings,
)

R = Permissions(read=True)
W = Permissions(write=True)
RW = Permissions(read=True, write=True)
WU = Permissions(write=True, unattended=True)
RWU = Permissions(read=True, write=True, unattended=True)


def entry(name, p):
    return AclEntry(f"spaces/{name}", p)


# --- permissions model -----------------------------------------------------


@pytest.mark.parametrize("p", [R, W, RW, WU, RWU])
def test_valid_permissions(p):
    p.validate()


@pytest.mark.parametrize(
    "p", [Permissions(), Permissions(unattended=True), Permissions(read=True, unattended=True)]
)
def test_invalid_permissions(p):
    """Same rules as parse_space_arg: at least one permission, unattended requires write."""
    with pytest.raises(InvalidPermissions):
        p.validate()


def test_policy_allows():
    no_unattended = Policy(allow_unattended=False)
    assert no_unattended.allows(RW)
    assert not no_unattended.allows(WU)
    assert not Policy(allow_write=False).allows(W)
    assert Policy(allow_write=False).allows(R)


def test_labels():
    assert permission_labels(R) == ["Read"]
    assert permission_labels(RW) == ["Read", "Write with approval"]
    assert permission_labels(WU) == ["Write without confirmation"]


# --- when to confirm (table in flow step 6) ---------------------------------


def test_read_without_unattended_spaces_needs_nothing():
    acl = acl_with_change([entry("A", RW)], "spaces/B", R)
    req = requirements(None, R, acl)
    assert not req.confirmation


def test_read_with_unattended_spaces_needs_confirmation():
    acl = acl_with_change([entry("REP", WU)], "spaces/B", R)
    req = requirements(None, R, acl)
    assert req.confirmation


def test_write_with_approval_needs_confirmation():
    req = requirements(None, W, [entry("B", W)])
    assert req.confirmation


def test_unattended_needs_confirmation_only():
    req = requirements(None, WU, [entry("B", WU)])
    assert req.confirmation
    assert not hasattr(req, "reauthentication")  # decision on #29: no re-authentication


def test_switching_approval_to_unattended_needs_confirmation():
    req = requirements(W, WU, [entry("B", WU)])
    assert req.confirmation


@pytest.mark.parametrize(
    "before, after",
    [(RW, R), (RW, W), (WU, W), (RWU, R), (RWU, WU)],
)
def test_removing_permissions_needs_nothing(before, after):
    acl = [entry("B", after), entry("REP", WU)]
    req = requirements(before, after, acl)
    assert not req.confirmation


def test_adding_read_to_an_unattended_space_needs_confirmation():
    req = requirements(WU, RWU, [entry("B", RWU)])
    assert req.confirmation


# --- warnings, computed on the whole ACL -------------------------------------


NAMES = {"spaces/A": "Team General", "spaces/N": "Announcements", "spaces/REP": "Reports"}


def test_unattended_lists_readable_spaces():
    acl = acl_with_change([entry("A", RW), entry("N", R)], "spaces/REP", WU)
    ws = warnings("spaces/REP", None, WU, acl, NAMES)
    assert [w.kind for w in ws] == ["unattended_readers"]
    assert ws[0].spaces == ["Announcements", "Team General"]


def test_unattended_without_readable_spaces_has_no_reader_warning():
    acl = acl_with_change([entry("X", W)], "spaces/REP", WU)
    assert warnings("spaces/REP", None, WU, acl, NAMES) == []


def test_granting_read_while_unattended_spaces_exist():
    acl = acl_with_change([entry("REP", WU)], "spaces/A", R)
    ws = warnings("spaces/A", None, R, acl, NAMES)
    assert [w.kind for w in ws] == ["read_reaches_unattended"]
    assert ws[0].spaces == ["Reports"]


def test_read_and_unattended_on_the_same_space_is_first():
    acl = acl_with_change([entry("A", R)], "spaces/REP", RWU)
    ws = warnings("spaces/REP", None, RWU, acl, NAMES)
    assert [w.kind for w in ws] == ["same_space", "unattended_readers"]


def test_unknown_names_fall_back_to_snapshot_then_resource_name():
    acl = [AclEntry("spaces/OLD", R, "Old project"), entry("Z", R), entry("REP", WU)]
    ws = warnings("spaces/REP", None, WU, acl, {})
    assert ws[0].spaces == ["Old project", "spaces/Z"]


def test_no_warning_when_nothing_is_added():
    acl = [entry("A", R), entry("REP", WU)]
    assert warnings("spaces/REP", WU, WU, acl, NAMES) == []


def test_fingerprint_changes_with_any_entry():
    a = [entry("A", R), entry("B", W)]
    assert acl_fingerprint(a) == acl_fingerprint(list(reversed(a)))
    assert acl_fingerprint(a) != acl_fingerprint([entry("A", RW), entry("B", W)])
    assert acl_fingerprint(a) != acl_fingerprint(a[:1])


# --- pasted links ------------------------------------------------------------


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("spaces/AAABBBCCC", "spaces/AAABBBCCC"),
        ("  AAABBBCCC ", "spaces/AAABBBCCC"),
        ("https://chat.google.com/room/AAABBBCCC?cls=7", "spaces/AAABBBCCC"),
        ("https://mail.google.com/chat/u/0/#chat/space/AAABBBCCC", "spaces/AAABBBCCC"),
        ("https://chat.google.com/u/1/app/chat/spaces/QQQ222", "spaces/QQQ222"),
        ("", None),
        ("spaces/../etc", None),
        ("<script>", None),
        ("https://example.com/", None),
    ],
)
def test_parse_space_ref(ref, expected):
    assert parse_space_ref(ref) == expected
