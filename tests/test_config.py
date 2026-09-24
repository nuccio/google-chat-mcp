import pytest

from google_chat_mcp.config import (
    PermissionDeniedError,
    SpaceConfig,
    SpaceEntry,
    parse_space_arg,
)


# --- parse_space_arg: valid input ---


def test_parse_rw():
    e = parse_space_arg("spaces/AAA:rw")
    assert e == SpaceEntry(name="spaces/AAA", read=True, write=True)


def test_parse_r_only():
    e = parse_space_arg("spaces/BBB:r")
    assert e == SpaceEntry(name="spaces/BBB", read=True, write=False)


def test_parse_w_only():
    e = parse_space_arg("spaces/CCC:w")
    assert e == SpaceEntry(name="spaces/CCC", read=False, write=True)


def test_parse_wr_same_as_rw():
    e = parse_space_arg("spaces/DDD:wr")
    assert e.read is True
    assert e.write is True


def test_parse_requires_confirmation_by_default():
    assert parse_space_arg("spaces/A:w").require_confirmation is True
    assert parse_space_arg("spaces/A:rw").require_confirmation is True


def test_parse_w_unattended():
    e = parse_space_arg("spaces/A:w:unattended")
    assert e == SpaceEntry(
        name="spaces/A", read=False, write=True, require_confirmation=False
    )


def test_parse_rw_unattended():
    e = parse_space_arg("spaces/A:rw:unattended")
    assert e.read is True
    assert e.write is True
    assert e.require_confirmation is False


# --- parse_space_arg: invalid input ---


def test_parse_unattended_without_write():
    with pytest.raises(ValueError, match="no 'w' permission"):
        parse_space_arg("spaces/A:r:unattended")


def test_parse_unknown_marker():
    with pytest.raises(ValueError, match="Invalid marker"):
        parse_space_arg("spaces/A:w:unatended")


def test_parse_too_many_fields():
    with pytest.raises(ValueError, match="Expected format"):
        parse_space_arg("spaces/A:w:unattended:x")


def test_parse_missing_colon():
    with pytest.raises(ValueError, match="Expected format"):
        parse_space_arg("spaces/AAA")


def test_parse_empty_perms():
    with pytest.raises(ValueError, match="Invalid permissions"):
        parse_space_arg("spaces/AAA:x")


def test_parse_empty_name():
    with pytest.raises(ValueError, match="Empty space name"):
        parse_space_arg(":rw")


# --- SpaceConfig.list_spaces ---


def test_list_spaces_returns_all_configured():
    cfg = SpaceConfig.from_args(["spaces/A:r", "spaces/B:w", "spaces/C:rw"])
    result = cfg.list_spaces()
    assert len(result) == 3
    names = {s["name"] for s in result}
    assert names == {"spaces/A", "spaces/B", "spaces/C"}


def test_list_spaces_flags_correct():
    cfg = SpaceConfig.from_args(["spaces/A:r", "spaces/B:w"])
    by_name = {s["name"]: s for s in cfg.list_spaces()}
    assert by_name["spaces/A"] == {"name": "spaces/A", "read": True, "write": False}
    assert by_name["spaces/B"] == {"name": "spaces/B", "read": False, "write": True}


def test_list_spaces_empty():
    cfg = SpaceConfig.from_args([])
    assert cfg.list_spaces() == []


# --- requires_confirmation / readable_unattended_spaces ---


def test_requires_confirmation():
    cfg = SpaceConfig.from_args(["spaces/A:w", "spaces/B:w:unattended"])
    assert cfg.requires_confirmation("spaces/A") is True
    assert cfg.requires_confirmation("spaces/B") is False


def test_requires_confirmation_unknown_space_defaults_to_true():
    cfg = SpaceConfig.from_args([])
    assert cfg.requires_confirmation("spaces/X") is True


def test_readable_unattended_spaces():
    cfg = SpaceConfig.from_args(
        ["spaces/A:rw:unattended", "spaces/B:w:unattended", "spaces/C:rw"]
    )
    assert cfg.readable_unattended_spaces() == ["spaces/A"]


# --- require_read ---


def test_require_read_ok():
    cfg = SpaceConfig.from_args(["spaces/A:r"])
    cfg.require_read("spaces/A")  # should not raise


def test_require_read_no_read_permission():
    cfg = SpaceConfig.from_args(["spaces/A:w"])
    with pytest.raises(PermissionDeniedError, match="Read permission"):
        cfg.require_read("spaces/A")


def test_require_read_not_in_allowlist():
    cfg = SpaceConfig.from_args(["spaces/A:rw"])
    with pytest.raises(PermissionDeniedError, match="not in allowlist"):
        cfg.require_read("spaces/UNKNOWN")


# --- require_write ---


def test_require_write_ok():
    cfg = SpaceConfig.from_args(["spaces/A:w"])
    cfg.require_write("spaces/A")  # should not raise


def test_require_write_no_write_permission():
    cfg = SpaceConfig.from_args(["spaces/A:r"])
    with pytest.raises(PermissionDeniedError, match="Write permission"):
        cfg.require_write("spaces/A")


def test_require_write_not_in_allowlist():
    cfg = SpaceConfig.from_args([])
    with pytest.raises(PermissionDeniedError, match="not in allowlist"):
        cfg.require_write("spaces/X")
