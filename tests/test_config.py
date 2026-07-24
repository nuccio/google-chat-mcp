import pytest

from google_chat_mcp.config import (
    PermissionDeniedError,
    SpaceConfig,
    SpaceEntry,
    parse_space_arg,
)


# --- parse_space_arg: input validi ---


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


# --- parse_space_arg: input invalidi ---


def test_parse_missing_colon():
    with pytest.raises(ValueError, match="Formato atteso"):
        parse_space_arg("spaces/AAA")


def test_parse_empty_perms():
    with pytest.raises(ValueError, match="Permessi non validi"):
        parse_space_arg("spaces/AAA:x")


def test_parse_empty_name():
    with pytest.raises(ValueError, match="Nome spazio vuoto"):
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


# --- require_read ---


def test_require_read_ok():
    cfg = SpaceConfig.from_args(["spaces/A:r"])
    cfg.require_read("spaces/A")  # non deve sollevare


def test_require_read_no_read_permission():
    cfg = SpaceConfig.from_args(["spaces/A:w"])
    with pytest.raises(PermissionDeniedError, match="lettura"):
        cfg.require_read("spaces/A")


def test_require_read_not_in_allowlist():
    cfg = SpaceConfig.from_args(["spaces/A:rw"])
    with pytest.raises(PermissionDeniedError, match="non in allowlist"):
        cfg.require_read("spaces/UNKNOWN")


# --- require_write ---


def test_require_write_ok():
    cfg = SpaceConfig.from_args(["spaces/A:w"])
    cfg.require_write("spaces/A")  # non deve sollevare


def test_require_write_no_write_permission():
    cfg = SpaceConfig.from_args(["spaces/A:r"])
    with pytest.raises(PermissionDeniedError, match="scrittura"):
        cfg.require_write("spaces/A")


def test_require_write_not_in_allowlist():
    cfg = SpaceConfig.from_args([])
    with pytest.raises(PermissionDeniedError, match="non in allowlist"):
        cfg.require_write("spaces/X")
