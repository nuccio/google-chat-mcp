"""
The Firestore backends of the settings app (issue #88).

The behaviour tests of `AclStore` run on the in-memory store and on the
Firestore store with the same code, so the mock the UI tests rely on cannot
drift from the real thing. The Firestore cases need the emulator
(`pytest -m emulator`); the in-memory ones run in a plain `pytest`.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("google.cloud.firestore")
pytest.importorskip("nicegui")

from google_chat_mcp import schema  # noqa: E402
from google_chat_mcp.permissions import InvalidPermissions, Permissions  # noqa: E402
from google_chat_mcp.settings import firestore as fs  # noqa: E402
from google_chat_mcp.settings.backends import ConcurrentChange  # noqa: E402
from google_chat_mcp.settings.mock import InMemoryAclStore  # noqa: E402
from google_chat_mcp.settings.models import AclEntry  # noqa: E402
from tests.fixtures import seed  # noqa: E402
from tests.fixtures import users as ds  # noqa: E402

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
R, W, RW, WU = ds.R, ds.W, ds.RW, ds.WU
ALICE = ds.ALICE.sub


def _alice_entries() -> dict[str, dict[str, AclEntry]]:
    doc = schema.acl_from_dict(ds.ALICE.acl_doc)
    return {
        ALICE: {
            schema.space_name(sid): AclEntry(
                schema.space_name(sid), e.permissions, e.display_name, e.updated_at
            )
            for sid, e in doc.entries.items()
        }
    }


@pytest.fixture(params=["memory", pytest.param("firestore", marks=pytest.mark.emulator)])
def store(request):
    """An AclStore holding alice's ACL from the fixed dataset."""
    if request.param == "memory":
        return InMemoryAclStore(_alice_entries())
    db = request.getfixturevalue("firestore_db")
    seed.seed(db, (ds.ALICE,))
    return fs.FirestoreAclStore(db)


def _names(entries):
    return {e.space_name for e in entries}


# --- behaviour shared by the mock and Firestore ------------------------------


def test_list_and_get(store):
    entries = {e.space_name: e.permissions for e in store.list_entries(ALICE)}
    assert entries == ds.ALICE.granted
    got = store.get_entry(ALICE, "spaces/AAABBBCCC")
    assert got.permissions == RW and got.display_name == "Team General"
    assert store.get_entry(ALICE, "spaces/NOSUCH") is None


def test_unknown_user_has_nothing(store):
    assert store.list_entries(ds.BOB.sub) == []
    assert store.get_entry(ds.BOB.sub, "spaces/AAABBBCCC") is None


def test_put_new_entry(store):
    rec = store.put_entry(
        ALICE, AclEntry("spaces/NEWSPACE1", R, "New"), expected_before=None, now=NOW
    )
    assert (rec.before, rec.after, rec.space_name, rec.changed_at) == (
        None, R, "spaces/NEWSPACE1", NOW,
    )
    got = store.get_entry(ALICE, "spaces/NEWSPACE1")
    assert got.permissions == R and got.display_name == "New" and got.updated_at == NOW
    assert len(store.list_entries(ALICE)) == 5


def test_put_changes_an_entry_and_audits_it(store):
    rec = store.put_entry(
        ALICE, AclEntry("spaces/ANNOUNCE1", RW, "Announcements"), expected_before=R, now=NOW
    )
    assert (rec.before, rec.after) == (R, RW)
    assert store.get_entry(ALICE, "spaces/ANNOUNCE1").permissions == RW
    assert store.audit_log(ALICE)[-1] == rec


def test_stale_expectation_is_a_concurrent_change(store):
    with pytest.raises(ConcurrentChange):
        store.put_entry(
            ALICE, AclEntry("spaces/ANNOUNCE1", RW), expected_before=W, now=NOW
        )
    with pytest.raises(ConcurrentChange):  # exists, but the caller thought it did not
        store.put_entry(
            ALICE, AclEntry("spaces/ANNOUNCE1", RW), expected_before=None, now=NOW
        )
    with pytest.raises(ConcurrentChange):  # does not exist, but the caller thought it did
        store.put_entry(ALICE, AclEntry("spaces/NOSUCH", R), expected_before=R, now=NOW)
    assert store.get_entry(ALICE, "spaces/ANNOUNCE1").permissions == R


def test_refused_put_writes_no_audit_record(store):
    before = len(store.audit_log(ALICE))
    with pytest.raises(ConcurrentChange):
        store.put_entry(ALICE, AclEntry("spaces/ANNOUNCE1", RW), expected_before=W, now=NOW)
    assert len(store.audit_log(ALICE)) == before


@pytest.mark.parametrize(
    "bad", [Permissions(), Permissions(read=True, unattended=True)], ids=["none", "unattended-r"]
)
def test_invalid_permissions_are_refused(store, bad):
    with pytest.raises(InvalidPermissions):
        store.put_entry(ALICE, AclEntry("spaces/NEWSPACE1", bad), expected_before=None, now=NOW)
    assert store.get_entry(ALICE, "spaces/NEWSPACE1") is None


def test_delete(store):
    rec = store.delete_entry(ALICE, "spaces/SUPPORT01", now=NOW)
    assert (rec.before, rec.after) == (W, None)
    assert store.get_entry(ALICE, "spaces/SUPPORT01") is None
    assert store.audit_log(ALICE)[-1] == rec
    assert len(store.list_entries(ALICE)) == 3


def test_delete_missing_entry_does_nothing(store):
    before = len(store.audit_log(ALICE))
    assert store.delete_entry(ALICE, "spaces/NOSUCH", now=NOW) is None
    assert len(store.audit_log(ALICE)) == before


def test_changes_are_audited_in_order(store):
    store.put_entry(ALICE, AclEntry("spaces/NEWSPACE1", R), expected_before=None, now=NOW)
    store.put_entry(
        ALICE, AclEntry("spaces/NEWSPACE1", RW), expected_before=R, now=NOW + timedelta(minutes=1)
    )
    store.delete_entry(ALICE, "spaces/NEWSPACE1", now=NOW + timedelta(minutes=2))
    log = [(r.before, r.after) for r in store.audit_log(ALICE) if r.space_name == "spaces/NEWSPACE1"]
    assert log == [(None, R), (R, RW), (RW, None)]


# --- Firestore only ------------------------------------------------------------

@pytest.fixture
def seeded_db(firestore_db):
    seed.seed(firestore_db)
    return firestore_db


def _snapshot(db) -> dict[str, dict]:
    """Every document of the database by path, including subcollections."""
    out = {}
    for col in db.collections():
        for snap in col.stream():
            out[snap.reference.path] = snap.to_dict()
            for sub in snap.reference.collections():
                for s in sub.stream():
                    out[s.reference.path] = s.to_dict()
    return out


@pytest.mark.emulator
def test_stored_entry_reads_back_as_written(seeded_db):
    st = fs.FirestoreAclStore(seeded_db)
    st.put_entry(
        ds.BOB.sub, AclEntry("spaces/BOBONLY01", WU, "Bob's space"), expected_before=None, now=NOW,
        session="0123456789abcdef",
    )
    stored = seeded_db.document(f"acls/{ds.BOB.sub}").get().to_dict()
    assert stored == {
        "schema_version": schema.SCHEMA_VERSION,
        "updated_at": NOW,
        "entries": {
            "BOBONLY01": {
                "read": False, "write": True, "unattended": True,
                "space_type": "SPACE", "display_name": "Bob's space", "updated_at": NOW,
            }
        },
    }
    assert schema.granted_permissions(schema.acl_from_dict(stored)) == {"spaces/BOBONLY01": WU}
    (audit,) = [d.to_dict() for d in seeded_db.collection(f"acls/{ds.BOB.sub}/audit").stream()]
    assert audit == {
        "schema_version": schema.SCHEMA_VERSION,
        "changed_at": NOW,
        "expire_at": NOW + timedelta(days=365),
        "space": "BOBONLY01",
        "before": None,
        "after": {"read": False, "write": True, "unattended": True},
        "session": "0123456789abcdef",
    }


@pytest.mark.emulator
def test_only_the_expected_documents_change(seeded_db):
    st = fs.FirestoreAclStore(seeded_db)
    before = _snapshot(seeded_db)
    st.put_entry(ALICE, AclEntry("spaces/ANNOUNCE1", RW, "Announcements"), expected_before=R, now=NOW)
    after = _snapshot(seeded_db)

    changed = {p for p in before if before[p] != after.get(p)}
    added = set(after) - set(before)
    assert changed == {f"acls/{ALICE}"}
    assert len(added) == 1 and added.pop().startswith(f"acls/{ALICE}/audit/")
    assert not set(before) - set(after)
    # Alice's other entries are as they were.
    old = before[f"acls/{ALICE}"]["entries"]
    new = after[f"acls/{ALICE}"]["entries"]
    assert {k: v for k, v in new.items() if k != "ANNOUNCE1"} == {
        k: v for k, v in old.items() if k != "ANNOUNCE1"
    }

    before = after
    st.delete_entry(ALICE, "spaces/SUPPORT01", now=NOW)
    after = _snapshot(seeded_db)
    assert {p for p in before if before[p] != after.get(p)} == {f"acls/{ALICE}"}
    assert len(set(after) - set(before)) == 1


@pytest.mark.emulator
def test_refused_writes_change_nothing(seeded_db):
    st = fs.FirestoreAclStore(seeded_db)
    before = _snapshot(seeded_db)
    with pytest.raises(ConcurrentChange):
        st.put_entry(ALICE, AclEntry("spaces/ANNOUNCE1", RW), expected_before=W, now=NOW)
    with pytest.raises(InvalidPermissions):
        st.put_entry(ALICE, AclEntry("spaces/NEW", Permissions()), expected_before=None, now=NOW)
    assert st.delete_entry(ALICE, "spaces/NOSUCH", now=NOW) is None
    assert _snapshot(seeded_db) == before


@pytest.mark.emulator
def test_entries_that_are_not_named_spaces_are_invisible_and_untouched(seeded_db):
    st = fs.FirestoreAclStore(seeded_db)
    carol = ds.CAROL.sub
    assert _names(st.list_entries(carol)) == set(ds.CAROL.granted)
    assert st.get_entry(carol, "spaces/DMENTRY01") is None
    assert st.delete_entry(carol, "spaces/DMENTRY01", now=NOW) is None
    # An ID held by a direct-message entry cannot be taken over by a put.
    with pytest.raises(ConcurrentChange):
        st.put_entry(carol, AclEntry("spaces/DMENTRY01", R), expected_before=None, now=NOW)
    st.put_entry(carol, AclEntry("spaces/NEWSPACE1", R), expected_before=None, now=NOW)
    entries = seeded_db.document(f"acls/{carol}").get().to_dict()["entries"]
    assert entries["DMENTRY01"]["space_type"] == "DIRECT_MESSAGE"
    assert entries["GROUPCHT1"]["space_type"] == "GROUP_CHAT"


@pytest.mark.emulator
def test_untrusted_display_name_is_stored_verbatim(seeded_db):
    st = fs.FirestoreAclStore(seeded_db)
    name = ds.CAROL.acl_doc["entries"]["ORPHAN001"]["display_name"]
    assert st.get_entry(ds.CAROL.sub, "spaces/ORPHAN001").display_name == name


@pytest.mark.emulator
def test_unreadable_document_is_never_written_over(seeded_db):
    st = fs.FirestoreAclStore(seeded_db)
    before = _snapshot(seeded_db)
    with pytest.raises(schema.UnsupportedSchemaVersion):
        st.put_entry(ds.DAVE.sub, AclEntry("spaces/NEWSPACE1", R), expected_before=None, now=NOW)
    with pytest.raises(schema.UnsupportedSchemaVersion):
        st.list_entries(ds.DAVE.sub)
    assert _snapshot(seeded_db) == before


@pytest.mark.emulator
def test_entry_limit(firestore_db):
    st = fs.FirestoreAclStore(firestore_db)
    sub = ds.BOB.sub
    firestore_db.document(f"acls/{sub}").set(
        schema.acl_to_dict(
            schema.AclDoc(
                {f"S{i:04d}": schema.AclEntryDoc(f"S{i:04d}", R, "SPACE", "", NOW)
                 for i in range(schema.MAX_ENTRIES)},
                updated_at=NOW,
            )
        )
    )
    before = _snapshot(firestore_db)
    with pytest.raises(schema.InvalidDocument):
        st.put_entry(sub, AclEntry("spaces/ONEMORE", R), expected_before=None, now=NOW)
    assert _snapshot(firestore_db) == before
    # Changing an existing entry at the limit is fine.
    st.put_entry(sub, AclEntry("spaces/S0000", RW), expected_before=R, now=NOW)


@pytest.mark.emulator
def test_two_concurrent_writers_only_one_wins(seeded_db):
    """Both read 'no entry' and write: the transaction lets exactly one through."""
    st = fs.FirestoreAclStore(seeded_db)
    barrier = threading.Barrier(2)
    results: list = []

    def writer(perm: Permissions) -> None:
        barrier.wait()
        try:
            results.append(
                st.put_entry(
                    ds.BOB.sub, AclEntry("spaces/RACE00001", perm), expected_before=None, now=NOW
                )
            )
        except ConcurrentChange as e:
            results.append(e)

    threads = [threading.Thread(target=writer, args=(p,)) for p in (R, W)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]

    wins = [r for r in results if not isinstance(r, Exception)]
    assert len(results) == 2 and len(wins) == 1
    assert sum(isinstance(r, ConcurrentChange) for r in results) == 1
    assert st.get_entry(ds.BOB.sub, "spaces/RACE00001").permissions == wins[0].after
    assert len(st.audit_log(ds.BOB.sub)) == 1


@pytest.mark.emulator
def test_concurrent_writers_on_different_spaces_both_land(seeded_db):
    st = fs.FirestoreAclStore(seeded_db)
    barrier = threading.Barrier(4)
    errors = []

    def writer(i: int) -> None:
        barrier.wait()
        try:
            st.put_entry(
                ds.BOB.sub, AclEntry(f"spaces/MANY{i:04d}", R), expected_before=None, now=NOW
            )
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert not errors
    assert len(st.list_entries(ds.BOB.sub)) == 4
    assert len(st.audit_log(ds.BOB.sub)) == 4


@pytest.mark.emulator
def test_audit_log_matches_the_seeded_records(seeded_db):
    log = fs.FirestoreAclStore(seeded_db).audit_log(ALICE)
    assert [(r.space_name, r.before, r.after, r.changed_at) for r in log] == [
        ("spaces/AAABBBCCC", None, RW, ds.T0),
        ("spaces/REPORTS01", W, WU, ds.T2),
    ]


# --- connector usage -------------------------------------------------------------


@pytest.mark.emulator
@pytest.mark.parametrize(
    "user, expected",
    [(ds.ALICE, ds.T2), (ds.BOB, None), (ds.CAROL, ds.T1)],
    ids=["alice", "bob-never", "carol"],
)
def test_connector_usage(seeded_db, user, expected):
    assert fs.FirestoreUsage(seeded_db).last_used(user.sub) == expected


@pytest.mark.emulator
def test_connector_usage_of_an_unknown_user(seeded_db):
    assert fs.FirestoreUsage(seeded_db).last_used("999") is None


@pytest.mark.emulator
def test_connector_usage_does_not_write(seeded_db):
    before = _snapshot(seeded_db)
    fs.FirestoreUsage(seeded_db).last_used(ALICE)
    assert _snapshot(seeded_db) == before


# --- sessions ----------------------------------------------------------------------


def _new_session(sessions, now=NOW, token_ttl=timedelta(hours=1)):
    return sessions.create(
        sub=ALICE, email="alice@example.com", access_token="tok", token_expires_at=now + token_ttl,
        now=now,
    )


@pytest.mark.emulator
def test_session_is_stored_under_the_hash_of_its_id(firestore_db):
    sessions = fs.FirestoreSessionStore(firestore_db)
    s = _new_session(sessions)
    assert not firestore_db.document(f"sessions/{s.session_id}").get().exists
    stored = firestore_db.document(f"sessions/{fs.digest(s.session_id)}").get().to_dict()
    assert stored == {
        "sub": ALICE, "email": "alice@example.com", "access_token": "tok",
        "token_expires_at": NOW + timedelta(hours=1), "created_at": NOW, "last_seen_at": NOW,
        "expire_at": NOW + timedelta(minutes=58), "pending_change": None,
    }
    assert sessions.get(s.session_id, NOW) == s


@pytest.mark.emulator
def test_session_ids_are_never_reused(firestore_db):
    sessions = fs.FirestoreSessionStore(firestore_db)
    assert _new_session(sessions).session_id != _new_session(sessions).session_id


@pytest.mark.emulator
def test_session_lifetime_is_the_shorter_of_token_and_hour(firestore_db):
    sessions = fs.FirestoreSessionStore(firestore_db)
    short = _new_session(sessions, token_ttl=timedelta(minutes=10))
    assert short.expire_at == NOW + timedelta(minutes=8)
    long = _new_session(sessions, token_ttl=timedelta(hours=5))
    assert long.expire_at == NOW + timedelta(minutes=60)


@pytest.mark.emulator
def test_unknown_or_missing_session(firestore_db):
    sessions = fs.FirestoreSessionStore(firestore_db)
    assert sessions.get(None, NOW) is None
    assert sessions.get("", NOW) is None
    assert sessions.get("nonexistent", NOW) is None


@pytest.mark.emulator
def test_session_idle_timeout_deletes_it(firestore_db):
    sessions = fs.FirestoreSessionStore(firestore_db)
    s = _new_session(sessions, token_ttl=timedelta(hours=5))
    # Used every 20 minutes: stays alive.
    assert sessions.get(s.session_id, NOW + timedelta(minutes=20)) is not None
    assert sessions.get(s.session_id, NOW + timedelta(minutes=40)) is not None
    # 31 minutes of silence: gone, and the document with it.
    assert sessions.get(s.session_id, NOW + timedelta(minutes=71)) is None
    assert not firestore_db.document(f"sessions/{fs.digest(s.session_id)}").get().exists


@pytest.mark.emulator
def test_session_never_outlives_its_token(firestore_db):
    sessions = fs.FirestoreSessionStore(firestore_db)
    s = _new_session(sessions, token_ttl=timedelta(minutes=10))
    assert sessions.get(s.session_id, NOW + timedelta(minutes=7)) is not None
    assert sessions.get(s.session_id, NOW + timedelta(minutes=8)) is None


@pytest.mark.emulator
def test_last_seen_is_rewritten_at_most_once_a_minute(firestore_db):
    sessions = fs.FirestoreSessionStore(firestore_db)
    s = _new_session(sessions)
    ref = firestore_db.document(f"sessions/{fs.digest(s.session_id)}")
    assert sessions.get(s.session_id, NOW + timedelta(seconds=30)).last_seen_at == NOW
    assert ref.get().to_dict()["last_seen_at"] == NOW
    later = NOW + timedelta(seconds=90)
    assert sessions.get(s.session_id, later).last_seen_at == later
    assert ref.get().to_dict()["last_seen_at"] == later


@pytest.mark.emulator
def test_pending_change_and_logout(firestore_db):
    sessions = fs.FirestoreSessionStore(firestore_db)
    s = _new_session(sessions)
    pending = {"token": "t", "space": "spaces/AAA", "created_at": NOW}
    assert sessions.set_pending_change(s.session_id, pending)
    assert sessions.get(s.session_id, NOW).pending_change == pending
    assert sessions.set_pending_change(s.session_id, None)
    assert sessions.get(s.session_id, NOW).pending_change is None
    sessions.delete(s.session_id)
    assert sessions.get(s.session_id, NOW) is None
    assert not sessions.set_pending_change(s.session_id, pending)
    assert list(firestore_db.collection("sessions").stream()) == []


@pytest.mark.emulator
def test_sessions_write_nothing_outside_their_collection(seeded_db):
    before = _snapshot(seeded_db)
    sessions = fs.FirestoreSessionStore(seeded_db)
    s = _new_session(sessions)
    sessions.get(s.session_id, NOW + timedelta(minutes=2))
    sessions.delete(s.session_id)
    assert _snapshot(seeded_db) == before


# --- login transactions ----------------------------------------------------------------


def _tx(txs, now=NOW):
    return txs.create(
        state="st", nonce="no", code_verifier="cv", select_account=True, now=now
    )


@pytest.mark.emulator
def test_login_transaction_round_trip_and_stored_shape(firestore_db):
    txs = fs.FirestoreLoginTransactions(firestore_db)
    tx_id = _tx(txs)
    stored = firestore_db.document(f"login_transactions/{fs.digest(tx_id)}").get().to_dict()
    assert stored == {
        "state": "st", "nonce": "no", "code_verifier": "cv", "select_account": True,
        "created_at": NOW, "expire_at": NOW + timedelta(minutes=10),
    }
    tx = txs.consume(tx_id, NOW + timedelta(minutes=1))
    assert (tx.state, tx.nonce, tx.code_verifier, tx.select_account) == ("st", "no", "cv", True)


@pytest.mark.emulator
def test_login_transaction_is_used_once(firestore_db):
    txs = fs.FirestoreLoginTransactions(firestore_db)
    tx_id = _tx(txs)
    assert txs.consume(tx_id, NOW) is not None
    assert txs.consume(tx_id, NOW) is None
    assert list(firestore_db.collection("login_transactions").stream()) == []


@pytest.mark.emulator
def test_login_transaction_expires_after_ten_minutes_and_is_removed(firestore_db):
    txs = fs.FirestoreLoginTransactions(firestore_db)
    tx_id = _tx(txs)
    assert txs.consume(tx_id, NOW + timedelta(minutes=10)) is None
    assert list(firestore_db.collection("login_transactions").stream()) == []


@pytest.mark.emulator
def test_login_transaction_missing(firestore_db):
    txs = fs.FirestoreLoginTransactions(firestore_db)
    assert txs.consume(None, NOW) is None
    assert txs.consume("nonexistent", NOW) is None


@pytest.mark.emulator
def test_two_concurrent_callbacks_cannot_both_consume(firestore_db):
    txs = fs.FirestoreLoginTransactions(firestore_db)
    tx_id = _tx(txs)
    barrier = threading.Barrier(2)
    results = []

    def callback() -> None:
        barrier.wait()
        results.append(txs.consume(tx_id, NOW))

    threads = [threading.Thread(target=callback) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(r is None for r in results) == [False, True]


# --- client factory ---------------------------------------------------------------------


def test_correlation_id_is_the_first_16_hex_digits_of_the_hash():
    cid = fs.session_correlation("abc")
    assert len(cid) == 16 and cid == fs.digest("abc")[:16]
