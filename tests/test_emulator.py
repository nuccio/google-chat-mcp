"""Tests against the Firestore emulator (level L1 of docs/remote/06-dev-cycle.md).

Run with an emulator started and `pytest -m emulator`; see tests/README.md.
"""

import pytest

from google_chat_mcp import schema
from tests.fixtures import seed
from tests.fixtures import users as ds

pytestmark = pytest.mark.emulator


def test_write_read_and_delete(firestore_db):
    ref = firestore_db.document("smoke/one")
    assert not ref.get().exists
    ref.set({"n": 1})
    assert ref.get().to_dict() == {"n": 1}
    ref.delete()
    assert not ref.get().exists


def test_reset_empties_the_database(firestore_db, firestore_emulator):
    firestore_db.document("smoke/two").set({"n": 2})
    seed.reset(firestore_emulator)
    assert list(firestore_db.collection("smoke").stream()) == []


def test_each_test_starts_on_an_empty_database(firestore_db):
    assert list(firestore_db.collections()) == []


def test_seed_writes_every_document_and_is_repeatable(firestore_db):
    first = seed.seed(firestore_db)
    second = seed.seed(firestore_db)
    assert first == second == sorted(ds.documents())
    assert len(list(firestore_db.collection("users").stream())) == 4
    assert len(list(firestore_db.collection("acls").stream())) == 3  # bob has none


@pytest.fixture
def seeded(firestore_db):
    seed.seed(firestore_db)
    return firestore_db


@pytest.mark.parametrize("user", ds.USERS, ids=[u.name for u in ds.USERS])
def test_user_document_is_read_back_as_written(seeded, user):
    stored = seeded.document(f"users/{user.sub}").get().to_dict()
    assert stored == user.user_doc
    assert schema.user_from_dict(stored).email == user.email


@pytest.mark.parametrize("user", [ds.ALICE, ds.CAROL], ids=["alice", "carol"])
def test_stored_acl_grants_what_the_dataset_declares(seeded, user):
    stored = seeded.document(f"acls/{user.sub}").get().to_dict()
    assert stored == user.acl_doc  # types, including datetimes, survive Firestore
    assert schema.granted_permissions(schema.acl_from_dict(stored)) == user.granted


def test_bob_has_no_acl_document(seeded):
    assert not seeded.document(f"acls/{ds.BOB.sub}").get().exists


def test_dave_acl_is_stored_but_refused_by_the_reader(seeded):
    stored = seeded.document(f"acls/{ds.DAVE.sub}").get().to_dict()
    assert stored["schema_version"] == schema.SCHEMA_VERSION + 1
    with pytest.raises(schema.UnsupportedSchemaVersion):
        schema.acl_from_dict(stored)


def test_audit_documents_are_in_the_subcollection_and_readable(seeded):
    records = [
        d.to_dict() for d in seeded.collection(f"acls/{ds.ALICE.sub}/audit").stream()
    ]
    assert len(records) == len(ds.ALICE.audit_docs)
    for raw in records:
        assert schema.audit_to_dict(schema.audit_from_dict(raw)) == raw


def test_malformed_documents_survive_storage_and_are_still_refused(firestore_db):
    for case, doc in ds.MALFORMED_ACL_DOCS.items():
        firestore_db.document(f"malformed/{case}").set(doc)
    for case in ds.MALFORMED_ACL_DOCS:
        stored = firestore_db.document(f"malformed/{case}").get().to_dict()
        with pytest.raises(schema.InvalidDocument):
            schema.acl_from_dict(stored)
