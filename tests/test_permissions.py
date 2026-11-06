"""Acl and Permissions (google_chat_mcp/permissions.py), built through --space."""

import pytest

from google_chat_mcp.config import acl_from_args
from google_chat_mcp.permissions import (
    Acl,
    InvalidPermissions,
    PermissionDeniedError,
    Permissions,
)


# --- Permissions.validate ---


@pytest.mark.parametrize(
    "p",
    [
        Permissions(read=True),
        Permissions(write=True),
        Permissions(read=True, write=True),
        Permissions(write=True, unattended=True),
        Permissions(read=True, write=True, unattended=True),
    ],
)
def test_validate_accepts(p):
    p.validate()


@pytest.mark.parametrize(
    "p",
    [Permissions(), Permissions(unattended=True), Permissions(read=True, unattended=True)],
)
def test_validate_rejects(p):
    with pytest.raises(InvalidPermissions):
        p.validate()


def test_permissions_are_immutable():
    with pytest.raises(AttributeError):
        Permissions(read=True).write = True  # type: ignore[misc]


# --- Acl ---


def test_acl_entries_cannot_be_changed_through_the_source_mapping():
    source = {"spaces/A": Permissions(read=True)}
    acl = Acl(source)
    source["spaces/B"] = Permissions(read=True)
    assert [s["name"] for s in acl.list_spaces()] == ["spaces/A"]


def test_not_configured_hint_is_part_of_the_message():
    acl = Acl({}, not_configured_hint="Enable it on the settings page.")
    with pytest.raises(PermissionDeniedError, match="not in allowlist. Enable it"):
        acl.require_read("spaces/X")


def test_not_configured_without_hint():
    with pytest.raises(PermissionDeniedError) as e:
        Acl({}).require_write("spaces/X")
    assert str(e.value) == "Space 'spaces/X' not in allowlist."


# --- Acl.list_spaces ---


def test_list_spaces_returns_all_configured():
    cfg = acl_from_args(["spaces/A:r", "spaces/B:w", "spaces/C:rw"])
    result = cfg.list_spaces()
    assert len(result) == 3
    names = {s["name"] for s in result}
    assert names == {"spaces/A", "spaces/B", "spaces/C"}


def test_list_spaces_flags_correct():
    cfg = acl_from_args(["spaces/A:r", "spaces/B:w"])
    by_name = {s["name"]: s for s in cfg.list_spaces()}
    assert by_name["spaces/A"] == {"name": "spaces/A", "read": True, "write": False}
    assert by_name["spaces/B"] == {"name": "spaces/B", "read": False, "write": True}


def test_list_spaces_empty():
    cfg = acl_from_args([])
    assert cfg.list_spaces() == []


# --- requires_confirmation / readable_unattended_spaces ---


def test_requires_confirmation():
    cfg = acl_from_args(["spaces/A:w", "spaces/B:w:unattended"])
    assert cfg.requires_confirmation("spaces/A") is True
    assert cfg.requires_confirmation("spaces/B") is False


def test_requires_confirmation_unknown_space_defaults_to_true():
    cfg = acl_from_args([])
    assert cfg.requires_confirmation("spaces/X") is True


def test_readable_unattended_spaces():
    cfg = acl_from_args(
        ["spaces/A:rw:unattended", "spaces/B:w:unattended", "spaces/C:rw"]
    )
    assert cfg.readable_unattended_spaces() == ["spaces/A"]


# --- require_read ---


def test_require_read_ok():
    cfg = acl_from_args(["spaces/A:r"])
    cfg.require_read("spaces/A")  # should not raise


def test_require_read_no_read_permission():
    cfg = acl_from_args(["spaces/A:w"])
    with pytest.raises(PermissionDeniedError, match="Read permission"):
        cfg.require_read("spaces/A")


def test_require_read_not_in_allowlist():
    cfg = acl_from_args(["spaces/A:rw"])
    with pytest.raises(PermissionDeniedError, match="not in allowlist"):
        cfg.require_read("spaces/UNKNOWN")


# --- require_write ---


def test_require_write_ok():
    cfg = acl_from_args(["spaces/A:w"])
    cfg.require_write("spaces/A")  # should not raise


def test_require_write_no_write_permission():
    cfg = acl_from_args(["spaces/A:r"])
    with pytest.raises(PermissionDeniedError, match="Write permission"):
        cfg.require_write("spaces/A")


def test_require_write_not_in_allowlist():
    cfg = acl_from_args([])
    with pytest.raises(PermissionDeniedError, match="not in allowlist"):
        cfg.require_write("spaces/X")
