"""The `users/{sub}` middleware (remote.users): docs/remote/07, "users/{sub}".

The gate and the failure handling run on an in-memory writer; the writer itself
runs on the Firestore emulator (`pytest -m emulator`) with the fixed dataset.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("google.cloud.firestore")

from google_chat_mcp import schema
from google_chat_mcp.remote import users as users_mod
from google_chat_mcp.remote.users import FirestoreUsersWriter, UsersMiddleware
from tests.fixtures import users as ds

NOW = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)


class Writer:
    def __init__(self, fail=False, delay=0.0) -> None:
        self.calls: list[tuple[str, str, datetime]] = []
        self.fail, self.delay = fail, delay

    async def touch(self, sub, email, now):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("down: user@example.com spaces/ABC")
        self.calls.append((sub, email, now))


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def as_user(monkeypatch):
    current = {"sub": ds.ALICE.sub, "email": ds.ALICE.email}
    monkeypatch.setattr(users_mod, "current_sub", lambda: current["sub"])
    monkeypatch.setattr(users_mod, "current_email", lambda: current["email"])
    return current


async def call(mw: UsersMiddleware) -> list:
    seen: list = []

    async def next_(_ctx):
        seen.append(1)
        return "result"

    assert await mw.on_call_tool(None, next_) == "result"
    return seen


@pytest.mark.anyio
async def test_at_most_once_an_hour_per_user(as_user):
    clock, w = Clock(), Writer()
    mw = UsersMiddleware(w, monotonic=clock, now=lambda: NOW)
    for _ in range(5):
        await call(mw)
    clock.t += 3599
    await call(mw)
    assert [c[0] for c in w.calls] == [ds.ALICE.sub]
    clock.t += 2
    await call(mw)
    assert [c[0] for c in w.calls] == [ds.ALICE.sub] * 2
    as_user.update(sub=ds.CAROL.sub, email=ds.CAROL.email)
    await call(mw)
    assert w.calls[-1][:2] == (ds.CAROL.sub, ds.CAROL.email)


@pytest.mark.anyio
async def test_concurrent_calls_of_one_user_write_once(as_user):
    w = Writer(delay=0.01)
    mw = UsersMiddleware(w, now=lambda: NOW)
    await asyncio.gather(*(call(mw) for _ in range(10)))
    assert len(w.calls) == 1


@pytest.mark.anyio
async def test_no_sub_writes_nothing(as_user):
    as_user["sub"] = None
    w = Writer()
    await call(UsersMiddleware(w))
    assert w.calls == []


@pytest.mark.anyio
async def test_failure_is_logged_without_content_and_ignored(as_user, caplog):
    clock, w = Clock(), Writer(fail=True)
    mw = UsersMiddleware(w, monotonic=clock, retry_after=300)
    with caplog.at_level(logging.WARNING):
        assert await call(mw) == [1]  # the call went on
    assert "ConnectionError" in caplog.text
    assert "user@example.com" not in caplog.text and "ABC" not in caplog.text
    assert ds.ALICE.sub not in caplog.text
    # No new attempt right away, then one after the retry delay.
    w.fail = False
    await call(mw)
    assert w.calls == []
    clock.t += 301
    await call(mw)
    assert len(w.calls) == 1


@pytest.mark.anyio
async def test_slow_write_does_not_hold_the_call(as_user, monkeypatch):
    monkeypatch.setattr(users_mod, "WRITE_TIMEOUT", 0.05)
    w = Writer(delay=1)
    assert await call(UsersMiddleware(w)) == [1]


# --- the Firestore writer ---------------------------------------------------------


@pytest.mark.emulator
def test_first_write_creates_the_document(firestore_db):
    w = FirestoreUsersWriter(firestore_db)
    asyncio.run(w.touch("100000000000000000077", "new@example.com", NOW))
    data = firestore_db.document("users/100000000000000000077").get().to_dict()
    doc = schema.user_from_dict(data)
    assert (doc.email, doc.created_at, doc.last_mcp_seen_at) == ("new@example.com", NOW, NOW)


@pytest.mark.emulator
def test_later_writes_change_only_last_mcp_seen_at(firestore_db):
    from tests.fixtures import seed

    seed.seed(firestore_db)
    path = f"users/{ds.ALICE.sub}"
    before = firestore_db.document(path).get().to_dict()
    later = NOW + timedelta(hours=2)
    asyncio.run(FirestoreUsersWriter(firestore_db).touch(ds.ALICE.sub, "changed@example.com", later))
    after = firestore_db.document(path).get().to_dict()
    assert {k for k in after if after[k] != before.get(k)} == {"last_mcp_seen_at"}
    assert after["last_mcp_seen_at"] == later
    # The settings service reads what the middleware wrote.
    assert schema.user_from_dict(after).last_mcp_seen_at == later
