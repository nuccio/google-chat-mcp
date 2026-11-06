"""The remote HTTP app end to end (remote.__main__, remote.chat, remote.users):
docs/remote/07 step 5.

L2: the real app from `build_app()` with a fake Google verifier and in-memory
stores, called over HTTP with tokens that the proxy accepts. The same scenarios
run against the Firestore emulator (`pytest -m emulator`) with the fixed
dataset, where the documents themselves are checked. The expected values come
from the dataset's literals.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import httpx2
import pytest

# The `remote` extra only: the local-only CI job skips this module.
pytest.importorskip("google.cloud.firestore")
pytest.importorskip("key_value.aio.stores.firestore")

from cryptography.fernet import Fernet
from key_value.aio.stores.memory import MemoryStore
from starlette.testclient import TestClient

from google_chat_mcp.remote.__main__ import build_app, build_server
from google_chat_mcp.remote.oauth import REQUIRED_SCOPES, OAuthSettings, build_verifier
from google_chat_mcp.remote.policy import AdminPolicy
from tests.fakes import InMemoryAclReader
from tests.fixtures import users as ds

BASE_URL = "https://gcm-mcp.example.com"
CLIENT_ID = "connector.apps.googleusercontent.com"
SETTINGS_URL = "https://settings.example.com"
HOST = {"Host": "gcm-mcp.example.com"}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

ENV = {
    "BASE_URL": BASE_URL,
    "GOOGLE_CLIENT_ID": CLIENT_ID,
    "GOOGLE_CLIENT_SECRET": "client-secret-value",
    "OAUTH_JWT_SIGNING_KEY": "j" * 40,
    "OAUTH_FERNET_KEYS": Fernet.generate_key().decode(),
    "SETTINGS_BASE_URL": SETTINGS_URL,
    "ALLOWED_DOMAINS": "example.com",
    "DEPLOY_MODE": "production",
}
USER_BY_TOKEN = {f"upstream-{u.sub}": u for u in ds.USERS}


def fake_google() -> httpx2.AsyncClient:
    """Google's tokeninfo and userinfo endpoints, answering from the dataset."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        user = USER_BY_TOKEN.get(bearer)
        if user is None:
            return httpx2.Response(400, json={"error": "invalid_token"})
        if request.url.host == "oauth2.googleapis.com":
            return httpx2.Response(
                200,
                json={
                    "aud": CLIENT_ID,
                    "scope": " ".join(REQUIRED_SCOPES),
                    "expires_in": "3000",
                    "sub": user.sub,
                    "email": user.email,
                },
            )
        return httpx2.Response(200, json={"email": user.email, "hd": "example.com"})

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


class RecordingChat:
    """Replaces `ChatClient`: records which access token each call carried."""

    calls: list[tuple[str, str]] = []

    def __init__(self, creds) -> None:
        assert creds.refresh_token is None and creds.client_secret is None
        self.token = creds.token
        outer = self

        class Spaces:
            def get(self, name):
                RecordingChat.calls.append((outer.token, name))
                return {"name": name, "displayName": "from-chat"}

        class Messages:
            def list(self, name, **kw):
                RecordingChat.calls.append((outer.token, name))
                return {"messages": [{"text": "hello"}], "next_page_token": None}

        self.spaces, self.messages, self.members = Spaces(), Messages(), None


@pytest.fixture(autouse=True)
def recording_chat(monkeypatch):
    RecordingChat.calls = []
    monkeypatch.setattr("google_chat_mcp.chat.ChatClient", RecordingChat)


class MemoryUsers:
    """In-memory `UsersWriter`: a dict of `users/{sub}` documents."""

    def __init__(self, documents=None, fail=False) -> None:
        self.documents = documents if documents is not None else {}
        self.touches: list[str] = []
        self.fail = fail

    async def touch(self, sub, email, now):
        if self.fail:
            raise RuntimeError("firestore down for user@example.com")
        self.touches.append(sub)
        doc = self.documents.setdefault(sub, {"email": email, "created_at": now})
        doc["last_mcp_seen_at"] = now


class Harness:
    def __init__(self, acl_reader, users_writer, *, environ=None, **kw) -> None:
        self.mcp, options = build_server(
            environ or ENV,
            acl_reader=acl_reader,
            users_writer=users_writer,
            token_store=MemoryStore(),
            verifier=build_verifier(
                OAuthSettings.from_env(AdminPolicy.from_env(environ or ENV), environ or ENV),
                AdminPolicy.from_env(environ or ENV),
                http_client=fake_google(),
            ),
            **kw,
        )
        self.app = self.mcp.http_app(stateless_http=True, **options)
        self.auth = self.mcp.auth
        self._ids = 0

    def mint(self, user) -> str:
        """A connector token for `user`, as a finished sign-in leaves it."""
        import asyncio

        from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet

        jti, upstream_id = secrets.token_urlsafe(16), secrets.token_urlsafe(16)

        async def seed() -> str:
            await self.auth._upstream_token_store.put(
                key=upstream_id,
                value=UpstreamTokenSet(
                    upstream_token_id=upstream_id,
                    access_token=f"upstream-{user.sub}",
                    refresh_token=None,
                    refresh_token_expires_at=None,
                    expires_at=time.time() + 3000,
                    token_type="Bearer",
                    scope=" ".join(REQUIRED_SCOPES),
                    client_id="client-1",
                    created_at=time.time(),
                ),
                ttl=3000,
            )
            await self.auth._jti_mapping_store.put(
                key=jti,
                value=JTIMapping(jti=jti, upstream_token_id=upstream_id, created_at=time.time()),
                ttl=3000,
            )
            return self.auth.jwt_issuer.issue_access_token(
                client_id="client-1", scopes=list(REQUIRED_SCOPES), jti=jti, expires_in=3000
            )

        return asyncio.run(seed())

    def call(self, client: TestClient, token: str | None, tool: str, **arguments) -> dict:
        self._ids += 1
        headers = {**MCP_HEADERS, **HOST, "mcp-protocol-version": "2025-06-18"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": self._ids,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            headers=headers,
        )
        if r.status_code != 200:
            return {"http": r.status_code}
        data = [ln[5:].strip() for ln in r.text.splitlines() if ln.startswith("data:")]
        message = json.loads(data[0] if data else r.text)
        return message["result"] | {"http": 200}


def result_text(result: dict) -> str:
    return " ".join(c.get("text", "") for c in result.get("content", []))


def result_json(result: dict):
    return result.get("structuredContent") or json.loads(result_text(result))


@pytest.fixture
def world():
    reader = InMemoryAclReader.from_dataset(ds.documents())
    users = MemoryUsers()
    h = Harness(reader, users)
    with TestClient(h.app, base_url=BASE_URL) as client:
        yield h, client, users


# --- the app -----------------------------------------------------------------


def test_build_app_refuses_incomplete_configuration():
    from google_chat_mcp.remote.acl import AclConfigError
    from google_chat_mcp.remote.oauth import OAuthConfigError
    from google_chat_mcp.remote.policy import PolicyConfigError

    with pytest.raises(OAuthConfigError):
        build_app({**ENV, "OAUTH_FERNET_KEYS": ""}, acl_reader=InMemoryAclReader(), users_writer=MemoryUsers())
    with pytest.raises(AclConfigError):
        build_app({**ENV, "SETTINGS_BASE_URL": ""}, acl_reader=InMemoryAclReader(), users_writer=MemoryUsers())
    with pytest.raises(PolicyConfigError):
        build_app({**ENV, "ALLOWED_DOMAINS": ""}, acl_reader=InMemoryAclReader(), users_writer=MemoryUsers())


def test_mcp_without_token_is_401_and_touches_nothing(world):
    h, client, users = world
    assert h.call(client, None, "list_spaces")["http"] == 401
    assert users.touches == []


def test_wrong_host_is_refused(world):
    h, client, _ = world
    r = client.post("/mcp", json={}, headers={**MCP_HEADERS, "Host": "evil.example.net"})
    assert r.status_code in (400, 421)


def test_server_is_stateless_and_has_no_cors(world):
    h, client, _ = world
    token = h.mint(ds.ALICE)
    # No `initialize` first: stateless mode needs no session.
    assert h.call(client, token, "list_spaces")["http"] == 200
    assert not any("cors" in type(m.cls).__name__.lower() for m in h.app.user_middleware)


def test_list_spaces_returns_the_callers_acl(world):
    h, client, _ = world
    got = result_json(h.call(client, h.mint(ds.ALICE), "list_spaces"))
    got = got if isinstance(got, list) else got["result"]
    assert {s["name"] for s in got} == set(ds.ALICE.list_spaces_open)


def test_chat_call_carries_the_callers_upstream_token(world):
    h, client, _ = world
    r = h.call(client, h.mint(ds.ALICE), "get_space", space_name="spaces/AAABBBCCC")
    assert r["http"] == 200 and not r.get("isError")
    assert RecordingChat.calls == [(f"upstream-{ds.ALICE.sub}", "spaces/AAABBBCCC")]


def test_space_outside_the_acl_makes_no_chat_call(world):
    h, client, _ = world
    r = h.call(client, h.mint(ds.ALICE), "get_space", space_name="spaces/NOTMINE01")
    assert r["isError"] is True
    assert SETTINGS_URL + "/settings" in result_text(r)
    assert RecordingChat.calls == []


def test_empty_acl_points_to_settings_but_auth_status_works(world):
    h, client, _ = world
    token = h.mint(ds.BOB)
    r = h.call(client, token, "list_spaces")
    assert r["isError"] is True and SETTINGS_URL + "/settings" in result_text(r)
    status = result_json(h.call(client, token, "chat_auth_status"))
    assert status["signed_in"] is True and status["email"] == ds.BOB.email
    assert status["domain"] == "example.com"


def test_unreadable_acl_fails_closed(world):
    h, client, _ = world
    r = h.call(client, h.mint(ds.DAVE), "list_spaces")
    assert r["isError"] is True
    assert RecordingChat.calls == []


def test_auth_status_has_no_token(world):
    h, client, _ = world
    token = h.mint(ds.ALICE)
    text = result_text(h.call(client, token, "chat_auth_status"))
    assert f"upstream-{ds.ALICE.sub}" not in text and token not in text
    assert "refresh_token" not in text


def test_two_callers_in_flight_never_see_each_other():
    class SlowReader(InMemoryAclReader):
        async def read(self, sub):
            import asyncio

            await asyncio.sleep(0.01)
            return await super().read(sub)

    reader = SlowReader.from_dataset(ds.documents())
    slow = Harness(reader, MemoryUsers())
    alice, carol = slow.mint(ds.ALICE), slow.mint(ds.CAROL)
    with TestClient(slow.app, base_url=BASE_URL) as c:

        def one(i):
            user, token = (ds.ALICE, alice) if i % 2 == 0 else (ds.CAROL, carol)
            got = result_json(slow.call(c, token, "list_spaces"))
            got = got if isinstance(got, list) else got["result"]
            return user, {s["name"] for s in got}

        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(one, range(24)))
    for user, names in results:
        assert names == set(user.list_spaces_open)
    seen = {sub: n for sub, n in [(r[0].sub, r[1]) for r in results]}
    assert seen[ds.ALICE.sub].isdisjoint(seen[ds.CAROL.sub])


# --- the users middleware over HTTP --------------------------------------------


def test_first_call_records_the_user_once_an_hour(world):
    h, client, users = world
    token = h.mint(ds.ALICE)
    for _ in range(3):
        h.call(client, token, "list_spaces")
    assert users.touches == [ds.ALICE.sub]
    assert users.documents[ds.ALICE.sub]["email"] == ds.ALICE.email


def test_a_failing_users_write_does_not_fail_the_call(caplog):
    reader = InMemoryAclReader.from_dataset(ds.documents())
    h = Harness(reader, MemoryUsers(fail=True))
    with caplog.at_level(logging.WARNING), TestClient(h.app, base_url=BASE_URL) as c:
        r = h.call(c, h.mint(ds.ALICE), "list_spaces")
    assert r["http"] == 200 and not r.get("isError")
    text = caplog.text
    assert "last_mcp_seen_at not written" in text
    assert "user@example.com" not in text and ds.ALICE.sub not in text


# --- the tool arguments never reach the log ------------------------------------


def test_tool_arguments_are_not_logged(world, caplog):
    h, client, _ = world
    with caplog.at_level(logging.INFO):
        h.call(client, h.mint(ds.ALICE), "list_messages", space_name="spaces/AAABBBCCC", filter="secret-filter")
    assert "tool=list_messages" in caplog.text
    assert "secret-filter" not in caplog.text and "AAABBBCCC" not in caplog.text


# --- Firestore emulator: nothing changes but users/{sub}.last_mcp_seen_at ---------


def snapshot(db) -> dict[str, dict]:
    """Every document of the database except the proxy's `mcp-*` store."""
    docs: dict[str, dict] = {}
    for col in db.collections():
        if col.id.startswith("mcp-"):
            continue
        for d in col.stream():
            docs[d.reference.path] = d.to_dict()
            for sub in d.reference.collections():
                for s in sub.stream():
                    docs[s.reference.path] = s.to_dict()
    return docs


@pytest.mark.emulator
def test_no_tool_changes_a_document_but_last_mcp_seen_at(firestore_db):
    from tests.fixtures import seed

    from google_chat_mcp.remote.acl import FirestoreAclReader
    from google_chat_mcp.remote.users import FirestoreUsersWriter

    seed.seed(firestore_db)
    before = snapshot(firestore_db)
    h = Harness(FirestoreAclReader(firestore_db), FirestoreUsersWriter(firestore_db))
    with TestClient(h.app, base_url=BASE_URL) as c:
        for user in (ds.ALICE, ds.BOB, ds.CAROL, ds.DAVE):
            token = h.mint(user)
            for tool, args in [
                ("chat_auth_status", {}),
                ("list_spaces", {}),
                ("get_space", {"space_name": "spaces/AAABBBCCC"}),
                ("list_messages", {"space_name": "spaces/AAABBBCCC"}),
                ("list_members", {"space_name": "spaces/AAABBBCCC"}),
                ("send_message", {"space_name": "spaces/AAABBBCCC", "text": "hi"}),
                ("send_message_unattended", {"space_name": "spaces/REPORTS01", "text": "hi"}),
            ]:
                h.call(c, token, tool, **args)
    after = snapshot(firestore_db)

    # Bob's `users` document did exist in the dataset: no document appears or goes.
    assert set(after) == set(before)
    changed = {p for p in after if after[p] != before.get(p)}
    assert changed <= {f"users/{u.sub}" for u in ds.USERS}
    for path in changed:
        old, new = before.get(path, {}), after[path]
        assert {k for k in new if new[k] != old.get(k)} <= {"last_mcp_seen_at"} | (
            {"email", "created_at", "schema_version"} if path not in before else set()
        )
        assert new["last_mcp_seen_at"] is not None
    for path in before:
        if not path.startswith("users/"):
            assert after[path] == before[path]
