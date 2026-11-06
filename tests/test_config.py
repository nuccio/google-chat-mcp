import pytest

from google_chat_mcp.config import acl_from_args, parse_space_arg
from google_chat_mcp.permissions import Permissions


# --- parse_space_arg: valid input ---


def test_parse_rw():
    assert parse_space_arg("spaces/AAA:rw") == (
        "spaces/AAA",
        Permissions(read=True, write=True),
    )


def test_parse_r_only():
    assert parse_space_arg("spaces/BBB:r") == ("spaces/BBB", Permissions(read=True))


def test_parse_w_only():
    assert parse_space_arg("spaces/CCC:w") == ("spaces/CCC", Permissions(write=True))


def test_parse_wr_same_as_rw():
    _, p = parse_space_arg("spaces/DDD:wr")
    assert p == Permissions(read=True, write=True)


def test_parse_not_unattended_by_default():
    assert parse_space_arg("spaces/A:w")[1].unattended is False
    assert parse_space_arg("spaces/A:rw")[1].unattended is False


def test_parse_w_unattended():
    assert parse_space_arg("spaces/A:w:unattended") == (
        "spaces/A",
        Permissions(write=True, unattended=True),
    )


def test_parse_rw_unattended():
    _, p = parse_space_arg("spaces/A:rw:unattended")
    assert p == Permissions(read=True, write=True, unattended=True)


# --- parse_space_arg: invalid input ---


def test_parse_unattended_without_write():
    with pytest.raises(ValueError, match="no 'w' permission"):
        parse_space_arg("spaces/A:r:unattended")


def test_parse_unknown_marker():
    with pytest.raises(ValueError, match="Invalid marker"):
        parse_space_arg("spaces/A:w:unatended")


@pytest.mark.parametrize("marker", ["", "UNATTENDED", " unattended"])
def test_parse_marker_must_match_exactly(marker):
    with pytest.raises(ValueError, match="Invalid marker"):
        parse_space_arg(f"spaces/A:w:{marker}")


def test_list_spaces_does_not_expose_unattended():
    cfg = acl_from_args(["spaces/A:w:unattended"])
    assert cfg.list_spaces() == [{"name": "spaces/A", "read": False, "write": True}]


def test_parse_too_many_fields():
    with pytest.raises(ValueError, match="Expected format"):
        parse_space_arg("spaces/A:w:unattended:x")


def test_parse_missing_colon():
    with pytest.raises(ValueError, match="Expected format"):
        parse_space_arg("spaces/AAA")


@pytest.mark.parametrize(
    "perms", ["", "x", "write", "read", "rwx", "rr", "ww", "rwr", "R", "W", "RW", " r"]
)
def test_parse_unknown_perms(perms):
    arg = f"spaces/AAA:{perms}"
    with pytest.raises(ValueError, match="Invalid permissions") as exc:
        parse_space_arg(arg)
    assert repr(arg) in str(exc.value)


def test_parse_unknown_perms_with_unattended():
    with pytest.raises(ValueError, match="Invalid permissions"):
        parse_space_arg("spaces/AAA:write:unattended")


def test_parse_empty_name():
    with pytest.raises(ValueError, match="Empty space name"):
        parse_space_arg(":rw")




def test_acl_from_args_names_the_space_flag_for_unknown_spaces():
    from google_chat_mcp.permissions import PermissionDeniedError

    with pytest.raises(PermissionDeniedError, match="Add it with --space"):
        acl_from_args([]).require_read("spaces/X")
