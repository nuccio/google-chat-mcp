"""The Firestore AclProvider (remote.acl): docs/remote/07, M7-M10.

Every behaviour test runs on the in-memory reader and on the Firestore emulator
(`pytest -m emulator`) with the fixed dataset of tests/fixtures/users.py, so the
fake the tool tests use cannot drift from the real reader. The expected values
come from the dataset's literals.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from google_chat_mcp.permissions import Acl, PermissionDeniedError
from google_chat_mcp.remote import acl as remote_acl
from google_chat_mcp.remote.acl import (
    AclConfigError,
    RemoteAclProvider,
    settings_link,
    settings_link_from_env,
)
from google_chat_mcp.remote.policy import AdminPolicy, DeployMode, Policy
from tests.fakes import InMemoryAclReader
from tests.fixtures import users as ds

LINK = "https://settings.example.com/settings"
OPEN = AdminPolicy(DeployMode.PRODUCTION, frozenset({"example.com"}), Policy(**ds.POLICY_OPEN))
RESTRICTED = AdminPolicy(DeployMode.PRODUCTION, frozenset({"example.com"}), Policy(**ds.POLICY_RESTRICTED))


@pytest.fixture(
    params=["memory", pytest.param("firestore", marks=pytest.mark.emulator)]
)
def reader(request):
    """An AclReader holding the fixed dataset, in memory or on the emulator."""
    if request.param == "memory":
        return InMemoryAclReader.from_dataset(ds.documents())
    from google_chat_mcp.remote.acl import FirestoreAclReader
    from tests.fixtures import seed

    db = request.getfixturevalue("firestore_db")
    seed.seed(db)
    return FirestoreAclReader(db)


def provider(reader, sub, admin=OPEN):
    return RemoteAclProvider(reader, admin, LINK, lambda: sub)


def listed(acl: Acl) -> dict[str, tuple[bool, bool]]:
    return {s["name"]: (s["read"], s["write"]) for s in acl.list_spaces()}


# --- the fixed dataset --------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("user", [ds.ALICE, ds.CAROL], ids=lambda u: u.name)
async def test_open_policy_grants_what_is_stored_for_named_spaces(reader, user):
    acl = await provider(reader, user.sub).acl()
    assert listed(acl) == user.list_spaces_open
    for name, p in user.granted.items():
        assert acl.requires_confirmation(name) is (not p.unattended)


@pytest.mark.anyio
@pytest.mark.parametrize("user", [ds.ALICE, ds.CAROL], ids=lambda u: u.name)
async def test_restricted_policy_suspends_entries_and_loosening_restores_them(reader, user):
    acl = await provider(reader, user.sub, RESTRICTED).acl()
    assert listed(acl) == user.list_spaces_restricted
    for name in user.suspended_by_policy:
        with pytest.raises(PermissionDeniedError) as e:
            acl.require_write(name)
        # Reported as suspended, not as missing.
        assert "Suspended by your administrator's policy" in str(e.value)
        assert name in str(e.value)
    # Nothing was saved again; the policy alone brings the entry back.
    again = await provider(reader, user.sub, OPEN).acl()
    assert listed(again) == user.list_spaces_open


@pytest.mark.anyio
async def test_carol_dm_group_chat_and_orphan(reader):
    acl = await provider(reader, ds.CAROL.sub).acl()
    names = set(listed(acl))
    assert "spaces/DMENTRY01" not in names and "spaces/GROUPCHT1" not in names
    # The orphan is stored, so it is granted: the ACL does not call Chat.
    assert "spaces/ORPHAN001" in names


@pytest.mark.anyio
async def test_bob_without_a_document_gets_the_settings_link(reader):
    with pytest.raises(PermissionDeniedError) as e:
        await provider(reader, ds.BOB.sub).acl()
    assert LINK in str(e.value)


@pytest.mark.anyio
async def test_dave_with_a_newer_schema_fails_closed(reader, caplog):
    with caplog.at_level(logging.INFO), pytest.raises(PermissionDeniedError) as e:
        await provider(reader, ds.DAVE.sub).acl()
    assert "newer version" in str(e.value)
    assert "NEWSCHEMA" not in str(e.value)
    assert "NEWSCHEMA" not in caplog.text and ds.DAVE.sub not in caplog.text


@pytest.mark.anyio
async def test_one_read_per_call_and_none_between_calls():
    r = InMemoryAclReader.from_dataset(ds.documents())
    p = provider(r, ds.ALICE.sub)
    await p.acl()
    assert r.reads == [ds.ALICE.sub]
    # M7: a change saved on /settings applies on the next call.
    r.documents[ds.ALICE.sub]["entries"].pop("AAABBBCCC")
    assert "spaces/AAABBBCCC" not in listed(await p.acl())
    assert len(r.reads) == 2


# --- fail closed ---------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("name", sorted(ds.MALFORMED_ACL_DOCS))
async def test_every_malformed_document_is_denied_whole(name):
    r = InMemoryAclReader({"1": ds.MALFORMED_ACL_DOCS[name]})
    with pytest.raises(PermissionDeniedError) as e:
        await provider(r, "1").acl()
    assert "not valid" in str(e.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "version", [0, 2, "1", None, True, 1.5], ids=repr
)
async def test_unknown_schema_versions_are_denied(version):
    doc = {"schema_version": version, "entries": ds.ALICE.acl_doc["entries"]}
    with pytest.raises(PermissionDeniedError):
        await provider(InMemoryAclReader({"1": doc}), "1").acl()


@pytest.mark.anyio
async def test_a_document_without_schema_version_is_denied():
    with pytest.raises(PermissionDeniedError):
        await provider(InMemoryAclReader({"1": {"entries": {}}}), "1").acl()


@pytest.mark.anyio
@pytest.mark.parametrize("sub", [None, "", "a/b", "../acls", "x" * 256, 123], ids=repr)
async def test_a_missing_or_unusable_sub_is_denied_without_a_read(sub):
    r = InMemoryAclReader.from_dataset(ds.documents())
    with pytest.raises(PermissionDeniedError):
        await provider(r, sub).acl()
    assert r.reads == []


@pytest.mark.anyio
async def test_a_read_error_is_denied_and_not_logged_in_detail(caplog):
    class Broken:
        async def read(self, sub):
            raise RuntimeError(f"boom on acls/{sub}")

    with caplog.at_level(logging.DEBUG), pytest.raises(PermissionDeniedError) as e:
        await provider(Broken(), ds.ALICE.sub).acl()
    assert "Try again later" in str(e.value)
    assert ds.ALICE.sub not in caplog.text and "boom" not in caplog.text


@pytest.mark.anyio
async def test_a_policy_that_forbids_every_entry_says_so_and_links_settings():
    admin = AdminPolicy(
        DeployMode.PRODUCTION, frozenset({"example.com"}), Policy(space_allowlist=frozenset({"OTHER"}))
    )
    with pytest.raises(PermissionDeniedError) as e:
        await provider(InMemoryAclReader.from_dataset(ds.documents()), ds.ALICE.sub, admin).acl()
    assert "suspended" in str(e.value) and LINK in str(e.value)


@pytest.mark.anyio
async def test_denial_log_has_a_hash_of_the_sub_and_no_content(caplog):
    with caplog.at_level(logging.INFO):
        with pytest.raises(PermissionDeniedError):
            await provider(InMemoryAclReader.from_dataset(ds.documents()), ds.BOB.sub).acl()
    assert "reason=empty" in caplog.text
    assert ds.BOB.sub not in caplog.text


@pytest.mark.anyio
async def test_two_callers_in_flight_never_see_each_others_acl():
    r = InMemoryAclReader.from_dataset(ds.documents())
    current: dict[str, str] = {}

    class SlowReader:
        async def read(self, sub):
            await asyncio.sleep(0.01)
            return await r.read(sub)

    import contextvars

    var: contextvars.ContextVar[str] = contextvars.ContextVar("sub")
    p = RemoteAclProvider(SlowReader(), OPEN, LINK, lambda: var.get())

    async def call(user):
        var.set(user.sub)
        return listed(await p.acl())

    a, c = await asyncio.gather(
        asyncio.create_task(call(ds.ALICE)), asyncio.create_task(call(ds.CAROL))
    )
    assert a == ds.ALICE.list_spaces_open and c == ds.CAROL.list_spaces_open


# --- configuration ----------------------------------------------------------------


def test_settings_link_comes_from_the_base_url():
    assert settings_link("https://s.example.com/") == "https://s.example.com/settings"
    assert settings_link_from_env({"SETTINGS_BASE_URL": "http://localhost:8080"}) == "http://localhost:8080/settings"


@pytest.mark.parametrize("value", [None, "", "  ", "ftp://x.example.com", "not a url", "javascript:alert(1)"])
def test_settings_link_refuses_a_missing_or_odd_base_url(value):
    with pytest.raises(AclConfigError):
        settings_link(value)


# --- the read-only guarantee and the entry point ------------------------------------


def test_remote_acl_imports_neither_settings_nor_a_writer():
    tree = ast.parse(Path(remote_acl.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not [m for m in imported if "settings" in m.lower() or "AclStore" in m]


def test_remote_acl_has_no_write_call():
    source = Path(remote_acl.__file__).read_text(encoding="utf-8")
    for call in (".set(", ".update(", ".delete(", ".add(", ".create(", ".batch(", "transaction("):
        assert call not in source


def test_remote_entry_point_is_not_implemented_yet():
    result = subprocess.run(
        [sys.executable, "-m", "google_chat_mcp.remote"], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "not implemented yet" in result.stderr


# --- contract: what the settings AclStore writes, the provider grants --------------


@pytest.mark.emulator
@pytest.mark.anyio
async def test_documents_written_by_the_settings_store_grant_exactly_what_was_saved(firestore_db):
    pytest.importorskip("nicegui")
    from datetime import datetime, timezone

    from google_chat_mcp.remote.acl import FirestoreAclReader
    from google_chat_mcp.settings import firestore as fs
    from google_chat_mcp.settings.models import AclEntry

    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    store = fs.FirestoreAclStore(firestore_db)
    saved = {"spaces/SAVED0001": ds.RW, "spaces/SAVED0002": ds.R, "spaces/SAVED0003": ds.WU}
    for name, p in saved.items():
        store.put_entry("42", AclEntry(name, p, "Name", now), expected_before=None, now=now)
    acl = await provider(FirestoreAclReader(firestore_db), "42").acl()
    assert listed(acl) == {n: (p.read, p.write) for n, p in saved.items()}
    assert [n for n in saved if not acl.requires_confirmation(n)] == ["spaces/SAVED0003"]
