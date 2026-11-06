"""The fixed dataset (tests/fixtures/users.py) agrees with itself and with the
code that reads it: the expected values are literals, and these tests are what
keeps them honest."""

import pytest

from google_chat_mcp import schema
from google_chat_mcp.permissions import Acl
from google_chat_mcp.settings.models import Policy
from tests.fixtures import users as ds

READABLE = [u for u in ds.USERS if u.state in ("ok", "empty")]


def _ids(users):
    return [u.name for u in users]


def test_four_users_with_distinct_identities():
    assert _ids(ds.USERS) == ["alice", "bob", "carol", "dave"]
    assert len({u.sub for u in ds.USERS}) == 4
    assert len({u.email for u in ds.USERS}) == 4
    assert all(u.email.endswith("@example.com") for u in ds.USERS)


@pytest.mark.parametrize("user", ds.USERS, ids=_ids(ds.USERS))
def test_user_document_round_trips_and_matches_the_user(user):
    doc = schema.user_from_dict(user.user_doc)
    assert doc.email == user.email
    assert schema.user_to_dict(doc) == user.user_doc


@pytest.mark.parametrize("user", [u for u in ds.USERS if u.state == "ok"], ids=["alice", "carol"])
def test_acl_document_round_trips_and_grants_what_is_declared(user):
    doc = schema.acl_from_dict(user.acl_doc)
    assert schema.acl_to_dict(doc) == user.acl_doc
    assert schema.granted_permissions(doc) == user.granted


def test_bob_has_no_acl_document():
    assert ds.BOB.acl_doc is None
    assert ds.BOB.state == "empty"
    assert ds.BOB.granted == {}


def test_dave_is_unreadable_and_grants_nothing():
    with pytest.raises(schema.UnsupportedSchemaVersion):
        schema.acl_from_dict(ds.DAVE.acl_doc)
    assert ds.DAVE.state == "unreadable"
    assert ds.DAVE.granted == {}


def test_carol_holds_entries_that_are_not_granted():
    doc = schema.acl_from_dict(ds.CAROL.acl_doc)
    stored = {schema.space_name(sid): e.space_type for sid, e in doc.entries.items()}
    not_granted = {n for n, t in stored.items() if t != schema.SPACE}
    assert not_granted == {"spaces/DMENTRY01", "spaces/GROUPCHT1"}
    assert not_granted.isdisjoint(ds.CAROL.granted)


@pytest.mark.parametrize("user", [ds.ALICE, ds.CAROL], ids=["alice", "carol"])
def test_policy_expectations_agree_with_the_policy_rules(user):
    open_ = Policy(**ds.POLICY_OPEN)
    restricted = Policy(**ds.POLICY_RESTRICTED)
    assert all(open_.allows(p) for p in user.granted.values())
    assert user.suspended_by_policy == {
        n for n, p in user.granted.items() if not restricted.allows(p)
    }


@pytest.mark.parametrize("user", READABLE, ids=_ids(READABLE))
def test_list_spaces_expectations_agree_with_acl(user):
    def listed(granted):
        return {s["name"]: (s["read"], s["write"]) for s in Acl(granted).list_spaces()}

    assert listed(user.granted) == user.list_spaces_open
    unsuspended = {n: p for n, p in user.granted.items() if n not in user.suspended_by_policy}
    assert listed(unsuspended) == user.list_spaces_restricted


def test_the_dataset_covers_every_valid_combination():
    seen = {p for u in ds.USERS for p in u.granted.values()}
    assert {ds.R, ds.W, ds.RW, ds.WU} <= seen


def test_audit_records_round_trip_and_agree_with_the_stored_acl():
    alice_acl = schema.acl_from_dict(ds.ALICE.acl_doc)
    last_after = {}
    for raw in sorted(ds.ALICE.audit_docs, key=lambda d: d["changed_at"]):
        rec = schema.audit_from_dict(raw)
        assert schema.audit_to_dict(rec) == raw
        assert rec.expire_at > rec.changed_at
        last_after[rec.space_id] = rec.after
    assert last_after
    for sid, after in last_after.items():
        assert alice_acl.entries[sid].permissions == after


def test_before_of_the_first_audit_record_is_none_and_chains_after():
    first, second = sorted(ds.ALICE.audit_docs, key=lambda d: d["changed_at"])
    assert first["before"] is None
    assert second["before"] is not None


def test_orphans_are_the_stored_spaces_the_user_can_no_longer_see():
    def orphans(u):
        doc = schema.acl_from_dict(u.acl_doc)
        return set(doc.entries) - u.visible_space_ids

    assert orphans(ds.ALICE) == set()
    assert orphans(ds.CAROL) == {"ORPHAN001"}


def test_untrusted_display_names_are_part_of_the_dataset():
    names = [e["display_name"] for e in ds.CAROL.acl_doc["entries"].values()]
    assert any("<script>" in n and "🚀" in n for n in names)


@pytest.mark.parametrize("case", sorted(ds.MALFORMED_ACL_DOCS))
def test_malformed_documents_are_refused_whole(case):
    with pytest.raises(schema.InvalidDocument) as e:
        schema.acl_from_dict(ds.MALFORMED_ACL_DOCS[case])
    assert not isinstance(e.value, schema.UnsupportedSchemaVersion)


def test_documents_lists_every_path_once():
    docs = ds.documents()
    assert set(docs) == {
        f"users/{u.sub}" for u in ds.USERS
    } | {
        f"acls/{u.sub}" for u in (ds.ALICE, ds.CAROL, ds.DAVE)
    } | {
        f"acls/{ds.ALICE.sub}/audit/audit01",
        f"acls/{ds.ALICE.sub}/audit/audit02",
    }
    assert f"acls/{ds.BOB.sub}" not in docs


def test_documents_are_copies_so_a_test_cannot_change_the_dataset():
    docs = ds.documents()
    docs[f"acls/{ds.ALICE.sub}"]["entries"].clear()
    assert ds.ALICE.acl_doc["entries"]
    assert ds.documents()[f"acls/{ds.ALICE.sub}"] == ds.ALICE.acl_doc


def test_seed_refuses_to_run_without_the_emulator_variable():
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "FIRESTORE_EMULATOR_HOST"}
    r = subprocess.run(
        [sys.executable, "-m", "tests.fixtures.seed"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert r.returncode == 2
    assert "never writes to a real Firestore" in r.stderr
