"""Contract tests on the documents shared by gcm-settings and gcm-mcp
(google_chat_mcp/schema.py).

The golden documents are the stored shape of schema_version 1. When the schema
changes, a golden document for the previous version is added next to them and
must still be read: that is what makes the "readers first, writers second"
rule testable.
"""

from datetime import datetime, timezone

import pytest

from google_chat_mcp import schema
from google_chat_mcp.permissions import Permissions

T = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)

GOLDEN_ACL_V1 = {
    "schema_version": 1,
    "updated_at": T,
    "entries": {
        "AAABBBCCC": {
            "read": True,
            "write": True,
            "unattended": False,
            "space_type": "SPACE",
            "display_name": "Team General",
            "updated_at": T,
        },
        "REPORTS01": {
            "read": False,
            "write": True,
            "unattended": True,
            "space_type": "SPACE",
            "display_name": "Reports",
            "updated_at": T,
        },
    },
}

GOLDEN_AUDIT_V1 = {
    "schema_version": 1,
    "changed_at": T,
    "expire_at": T,
    "space": "AAABBBCCC",
    "before": None,
    "after": {"read": True, "write": False, "unattended": False},
    "session": "0123456789abcdef",
}

GOLDEN_USER_V1 = {
    "schema_version": 1,
    "email": "alice@example.com",
    "created_at": T,
    "last_mcp_seen_at": T,
}


# --- golden documents: round trip ---


def test_golden_acl_is_read_and_written_back_identically():
    doc = schema.acl_from_dict(GOLDEN_ACL_V1)
    assert schema.acl_to_dict(doc) == GOLDEN_ACL_V1


def test_golden_audit_round_trip():
    assert schema.audit_to_dict(schema.audit_from_dict(GOLDEN_AUDIT_V1)) == GOLDEN_AUDIT_V1


def test_golden_user_round_trip():
    assert schema.user_to_dict(schema.user_from_dict(GOLDEN_USER_V1)) == GOLDEN_USER_V1


def test_what_the_mcp_server_grants_is_what_settings_saved():
    granted = schema.granted_permissions(schema.acl_from_dict(GOLDEN_ACL_V1))
    assert granted == {
        "spaces/AAABBBCCC": Permissions(read=True, write=True),
        "spaces/REPORTS01": Permissions(write=True, unattended=True),
    }


def test_missing_acl_document_content_is_an_empty_acl():
    doc = schema.acl_from_dict({"schema_version": 1})
    assert doc.entries == {}
    assert schema.granted_permissions(doc) == {}


# --- named spaces only ---


@pytest.mark.parametrize("space_type", ["DIRECT_MESSAGE", "GROUP_CHAT", "", "space"])
def test_only_named_spaces_are_granted(space_type):
    data = {
        "schema_version": 1,
        "entries": {
            "DM1": {**GOLDEN_ACL_V1["entries"]["AAABBBCCC"], "space_type": space_type}
        },
    }
    assert schema.granted_permissions(schema.acl_from_dict(data)) == {}


def test_missing_space_type_is_not_granted():
    entry = {k: v for k, v in GOLDEN_ACL_V1["entries"]["AAABBBCCC"].items() if k != "space_type"}
    doc = schema.acl_from_dict({"schema_version": 1, "entries": {"AAA": entry}})
    assert schema.granted_permissions(doc) == {}


# --- fail closed ---


@pytest.mark.parametrize("version", [2, 99, 0, None, "1", True])
def test_unknown_schema_version_is_refused(version):
    data = {**GOLDEN_ACL_V1, "schema_version": version}
    with pytest.raises(schema.UnsupportedSchemaVersion):
        schema.acl_from_dict(data)


def test_missing_schema_version_is_refused():
    with pytest.raises(schema.UnsupportedSchemaVersion):
        schema.acl_from_dict({"entries": {}})


@pytest.mark.parametrize("version", [2, None])
def test_users_and_audit_refuse_unknown_versions_too(version):
    with pytest.raises(schema.UnsupportedSchemaVersion):
        schema.user_from_dict({**GOLDEN_USER_V1, "schema_version": version})
    with pytest.raises(schema.UnsupportedSchemaVersion):
        schema.audit_from_dict({**GOLDEN_AUDIT_V1, "schema_version": version})


def _with_entry(**changes):
    entry = {**GOLDEN_ACL_V1["entries"]["AAABBBCCC"], **changes}
    return {"schema_version": 1, "entries": {"AAABBBCCC": entry}}


@pytest.mark.parametrize(
    "changes",
    [
        {"read": False, "write": False},  # no permission
        {"read": True, "write": False, "unattended": True},  # unattended without write
        {"read": "yes"},  # not a boolean
        {"write": None},
        {"display_name": 5},
        {"space_type": None},
        {"updated_at": "yesterday"},
    ],
)
def test_an_entry_that_breaks_an_invariant_fails_the_whole_document(changes):
    with pytest.raises(schema.InvalidDocument):
        schema.acl_from_dict(_with_entry(**changes))


def test_a_missing_permission_field_is_refused():
    data = _with_entry()
    del data["entries"]["AAABBBCCC"]["unattended"]
    with pytest.raises(schema.InvalidDocument):
        schema.acl_from_dict(data)


@pytest.mark.parametrize("sid", ["", "spaces/AAA", "a b", "a.b", "x" * 129, "AAA/../B"])
def test_invalid_space_id_is_refused(sid):
    data = {"schema_version": 1, "entries": {sid: GOLDEN_ACL_V1["entries"]["AAABBBCCC"]}}
    with pytest.raises(schema.InvalidDocument):
        schema.acl_from_dict(data)


def test_entries_must_be_a_map():
    with pytest.raises(schema.InvalidDocument):
        schema.acl_from_dict({"schema_version": 1, "entries": []})


def test_more_than_the_maximum_entries_is_refused_on_read_and_write():
    entry = GOLDEN_ACL_V1["entries"]["AAABBBCCC"]
    data = {
        "schema_version": 1,
        "entries": {f"S{i}": entry for i in range(schema.MAX_ENTRIES + 1)},
    }
    with pytest.raises(schema.InvalidDocument):
        schema.acl_from_dict(data)
    doc = schema.AclDoc(
        {
            f"S{i}": schema.AclEntryDoc(f"S{i}", Permissions(read=True))
            for i in range(schema.MAX_ENTRIES + 1)
        }
    )
    with pytest.raises(schema.InvalidDocument):
        schema.acl_to_dict(doc)


# --- writing refuses what the reader would refuse ---


def test_writing_an_invalid_permission_is_refused():
    doc = schema.AclDoc({"A": schema.AclEntryDoc("A", Permissions())})
    with pytest.raises(Exception):
        schema.acl_to_dict(doc)


def test_writing_a_key_that_differs_from_the_space_id_is_refused():
    doc = schema.AclDoc({"A": schema.AclEntryDoc("B", Permissions(read=True))})
    with pytest.raises(schema.InvalidDocument):
        schema.acl_to_dict(doc)


def test_writers_always_write_the_current_version():
    assert schema.acl_to_dict(schema.AclDoc())["schema_version"] == schema.SCHEMA_VERSION


# --- space names and IDs ---


def test_space_name_and_id_convert_both_ways():
    assert schema.space_id("spaces/AAA") == "AAA"
    assert schema.space_name("AAA") == "spaces/AAA"


@pytest.mark.parametrize("name", ["AAA", "users/AAA", "spaces/", "spaces/a b"])
def test_space_id_rejects_other_names(name):
    with pytest.raises(schema.InvalidDocument):
        schema.space_id(name)
