"""The connector's authentication (remote.oauth): docs/remote/07 step 4, 08 N5.

L0/L2: a fake Google upstream (an httpx2 mock transport behind the verifier) and
in-memory stores. The Firestore layout of the token store runs on the emulator
(`pytest -m emulator`).
"""

from __future__ import annotations

import json
import logging
import time

import httpx2
import pytest

# The `remote` extra only: the local-only CI job skips this module.
pytest.importorskip("google.cloud.firestore")
pytest.importorskip("key_value.aio.stores.firestore")

from cryptography.fernet import Fernet
from key_value.aio.stores.memory import MemoryStore

from google_chat_mcp import auth as local_auth
from google_chat_mcp.remote import oauth
from google_chat_mcp.remote.oauth import (
    CHAT_SCOPES,
    CLAUDE_REDIRECT_URIS,
    OAuthConfigError,
    OAuthSettings,
    REQUIRED_SCOPES,
    build_auth,
    bound_ttl,
    build_verifier,
    encrypt_store,
    http_protection_options,
    parse_base_url,
    parse_fernet_keys,
)
from google_chat_mcp.remote.policy import AdminPolicy, DeployMode, Policy

CLIENT_ID = "connector.apps.googleusercontent.com"
BASE_URL = "https://gcm-mcp-abc.europe-west1.run.app"
KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
PROD = AdminPolicy(DeployMode.PRODUCTION, frozenset({"example.com"}), Policy())
DEV = AdminPolicy(DeployMode.DEVELOPMENT, frozenset({"example.com"}), Policy())


def settings(**over) -> OAuthSettings:
    values = dict(
        base_url=BASE_URL,
        client_id=CLIENT_ID,
        client_secret="client-secret-value",
        jwt_signing_key="j" * 40,
        fernet_keys=(KEY_A,),
    )
    values.update(over)
    return OAuthSettings(**values)


ENV = {
    "BASE_URL": BASE_URL,
    "GOOGLE_CLIENT_ID": CLIENT_ID,
    "GOOGLE_CLIENT_SECRET": "client-secret-value",
    "OAUTH_JWT_SIGNING_KEY": "j" * 40,
    "OAUTH_FERNET_KEYS": KEY_A,
}


# --- configuration --------------------------------------------------------


def test_settings_from_env():
    s = OAuthSettings.from_env(PROD, ENV)
    assert s.base_url == BASE_URL
    assert s.host == "gcm-mcp-abc.europe-west1.run.app"
    assert s.fernet_keys == (KEY_A,)


def test_settings_repr_has_no_secret():
    text = repr(OAuthSettings.from_env(PROD, ENV))
    assert "client-secret-value" not in text and KEY_A not in text and "jjjj" not in text


@pytest.mark.parametrize(
    "name",
    ["BASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "OAUTH_JWT_SIGNING_KEY", "OAUTH_FERNET_KEYS"],
)
def test_each_variable_is_required(name):
    env = {k: v for k, v in ENV.items() if k != name}
    with pytest.raises(OAuthConfigError, match=name):
        OAuthSettings.from_env(PROD, env)


def test_short_jwt_key_refused():
    with pytest.raises(OAuthConfigError, match="at least"):
        OAuthSettings.from_env(PROD, {**ENV, "OAUTH_JWT_SIGNING_KEY": "short"})


def test_invalid_fernet_key_refused_without_echoing_it():
    with pytest.raises(OAuthConfigError) as e:
        parse_fernet_keys(f"{KEY_A},not-a-key-at-all")
    assert "not-a-key-at-all" not in str(e.value)


def test_fernet_key_list_keeps_order():
    assert parse_fernet_keys(f" {KEY_B} , {KEY_A} ") == (KEY_B, KEY_A)


@pytest.mark.parametrize(
    "raw,dev,ok",
    [
        ("https://x.run.app", False, True),
        ("https://x.run.app/", False, True),
        ("http://x.run.app", False, False),
        ("http://localhost:8000", False, False),
        ("http://localhost:8000", True, True),
        ("http://evil.example.com", True, False),
        ("https://x.run.app/path", False, False),
        ("x.run.app", False, False),
    ],
)
def test_base_url_rules(raw, dev, ok):
    if ok:
        assert parse_base_url(raw, development=dev) == raw.rstrip("/")
    else:
        with pytest.raises(OAuthConfigError):
            parse_base_url(raw, development=dev)


def test_scopes_match_local_mode_plus_identity():
    assert set(CHAT_SCOPES) == set(local_auth.SCOPES)
    assert set(REQUIRED_SCOPES) == {"openid", oauth.GOOGLE_EMAIL_SCOPE, *local_auth.SCOPES}


# --- the verifier against a fake upstream ----------------------------------


def fake_google(*, aud=CLIENT_ID, hd="example.com", sub="1001", userinfo_ok=True, scopes=None):
    """An httpx2 client whose Google endpoints answer from memory."""
    scope = " ".join(scopes if scopes is not None else REQUIRED_SCOPES)
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if request.url.host == "oauth2.googleapis.com":
            if request.headers.get("authorization") != "Bearer good-token":
                return httpx2.Response(400, json={"error": "invalid_token"})
            data = {"aud": aud, "scope": scope, "expires_in": "3000", "email": "a@example.com"}
            if sub:
                data["sub"] = sub
            return httpx2.Response(200, json=data)
        if not userinfo_ok:
            return httpx2.Response(500)
        profile = {"email": "a@example.com", "name": "A"}
        if hd:
            profile["hd"] = hd
        return httpx2.Response(200, json=profile)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client.calls = calls  # type: ignore[attr-defined]
    return client


def verifier(admin=PROD, **fake):
    return build_verifier(settings(), admin, http_client=fake_google(**fake))


@pytest.mark.anyio
async def test_valid_token_of_allowed_domain():
    access = await verifier().verify_token("good-token")
    assert access is not None
    assert access.claims["sub"] == "1001"
    assert access.claims["hd"] == "example.com"


@pytest.mark.anyio
async def test_unknown_token_refused():
    assert await verifier().verify_token("other-token") is None


@pytest.mark.anyio
async def test_audience_is_this_clients_id():
    assert await verifier(aud="someone-elses.apps.googleusercontent.com").verify_token("good-token") is None


@pytest.mark.anyio
async def test_token_without_sub_refused():
    assert await verifier(sub=None).verify_token("good-token") is None


@pytest.mark.anyio
async def test_missing_chat_scope_refused():
    assert await verifier(scopes=["openid", oauth.GOOGLE_EMAIL_SCOPE]).verify_token("good-token") is None


@pytest.mark.anyio
@pytest.mark.parametrize("hd", ["other.com", "sub.example.com", "EXAMPLE.org"])
async def test_disallowed_domain_refused_in_both_modes(hd):
    assert await verifier(PROD, hd=hd).verify_token("good-token") is None
    assert await verifier(DEV, hd=hd).verify_token("good-token") is None


@pytest.mark.anyio
async def test_case_insensitive_domain_accepted():
    assert await verifier(hd="Example.COM").verify_token("good-token") is not None


@pytest.mark.anyio
async def test_no_hd_refused_in_production_accepted_in_development():
    assert await verifier(PROD, hd=None).verify_token("good-token") is None
    assert await verifier(DEV, hd=None).verify_token("good-token") is not None


@pytest.mark.anyio
async def test_unreadable_profile_fails_closed_in_production():
    assert await verifier(PROD, userinfo_ok=False).verify_token("good-token") is None


@pytest.mark.anyio
async def test_domain_is_checked_on_every_call_even_with_the_verifier_cache():
    client = fake_google()
    v = oauth._verifier_class()(
        admin=PROD,
        required_scopes=list(REQUIRED_SCOPES),
        audience=CLIENT_ID,
        http_client=client,
        cache_ttl_seconds=300,
    )
    assert await v.verify_token("good-token") is not None
    n = len(client.calls)
    # Same token again: served from the cache, no new upstream call...
    assert await v.verify_token("good-token") is not None
    assert len(client.calls) == n
    # ...but the domain is removed from the policy: refused at once.
    v._admin = AdminPolicy(DeployMode.PRODUCTION, frozenset({"another.com"}), Policy())
    assert await v.verify_token("good-token") is None


@pytest.mark.anyio
async def test_refusal_log_has_no_email_or_domain(caplog):
    with caplog.at_level(logging.DEBUG):
        await verifier(hd="other.com").verify_token("good-token")
    text = caplog.text
    assert "a@example.com" not in text and "other.com" not in text and "1001" not in text


# --- the provider ---------------------------------------------------------


def provider(**kw):
    return build_auth(settings(), PROD, token_store=MemoryStore(), **kw)


def test_provider_configuration():
    p = provider()
    assert p._allowed_client_redirect_uris == list(CLAUDE_REDIRECT_URIS)
    assert p._require_authorization_consent is True
    assert p._token_validator.audience == CLIENT_ID
    assert p._token_validator is p.token_verifier
    assert type(p._token_validator).__name__ == "DomainCheckedGoogleVerifier"
    assert set(p.required_scopes) == set(REQUIRED_SCOPES)


def test_claude_callbacks_are_the_only_redirects():
    assert CLAUDE_REDIRECT_URIS == (
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
    )


def test_refuses_to_start_with_ssrf_trust_proxy(monkeypatch):
    import fastmcp

    monkeypatch.setattr(fastmcp.settings, "ssrf_trust_proxy", True)
    with pytest.raises(OAuthConfigError, match="SSRF"):
        provider()


def test_host_protection_options():
    assert http_protection_options(settings()) == {
        "host_origin_protection": True,
        "allowed_hosts": ["gcm-mcp-abc.europe-west1.run.app"],
    }


def app():
    from fastmcp import FastMCP

    mcp = FastMCP("t", auth=build_auth(settings(), PROD, token_store=MemoryStore(), verifier=verifier()))

    @mcp.tool
    def ping() -> str:
        return "pong"

    return mcp.http_app(stateless_http=True, **http_protection_options(settings()))


HOST = {"Host": "gcm-mcp-abc.europe-west1.run.app"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def client():
    from starlette.testclient import TestClient

    with TestClient(app(), base_url=BASE_URL) as c:
        yield c


def test_mcp_without_token_is_401_with_resource_metadata(client):
    r = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **HOST})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]


def test_mcp_with_unknown_bearer_is_401(client):
    r = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **HOST, "Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_wrong_host_refused(client):
    r = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "evil.example.com"})
    assert r.status_code == 421


def test_wrong_origin_refused_and_no_origin_accepted(client):
    r = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **HOST, "Origin": "https://evil.example.com"})
    assert r.status_code == 403
    # A server-to-server call has no Origin: it gets as far as the token check.
    r = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **HOST})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "method,path",
    [
        ("options", "/mcp"),
        ("get", "/.well-known/oauth-authorization-server"),
        ("get", "/.well-known/oauth-protected-resource/mcp"),
        ("options", "/token"),
        ("options", "/register"),
        ("get", "/authorize"),
    ],
)
def test_foreign_origin_gets_403_without_cors_headers_on_every_route(client, method, path):
    """We add no CORS middleware. FastMCP's own OAuth routes (metadata, /token,
    /register) answer `Access-Control-Allow-Origin: *` to a request that reaches
    them, but the Origin guard refuses any foreign Origin before that: so no
    other website can read a response (08, item 1)."""
    r = getattr(client, method)(
        path,
        headers={**HOST, "Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 403
    assert not any(h.lower().startswith("access-control-") for h in r.headers)


def test_metadata_advertises_this_server_and_the_chat_scopes(client):
    r = client.get("/.well-known/oauth-authorization-server", headers=HOST)
    assert r.status_code == 200
    meta = r.json()
    assert meta["issuer"].startswith(BASE_URL)
    assert meta["authorization_endpoint"].startswith(BASE_URL)
    assert meta["token_endpoint"].startswith(BASE_URL)
    assert meta["code_challenge_methods_supported"] == ["S256"]


def test_routes_are_the_documented_ones():
    """To verify (08, item 2): the real route list, compared with the endpoint table."""
    paths = sorted({getattr(r, "path", None) for r in app().routes if getattr(r, "path", None)})
    assert "/mcp" in paths
    allowed = {
        "/mcp",
        "/authorize",
        "/token",
        "/register",
        "/consent",
        "/auth/callback",
        "/revoke",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server/mcp",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
    }
    assert set(paths) <= allowed, sorted(set(paths) - allowed)


def test_dcr_rejects_a_foreign_redirect(client):
    r = client.post(
        "/register",
        headers=HOST,
        json={
            "client_name": "x",
            "redirect_uris": ["https://evil.example.com/cb"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert r.status_code == 400


def test_dcr_accepts_claudes_callback(client):
    r = client.post(
        "/register",
        headers=HOST,
        json={
            "client_name": "Claude",
            "redirect_uris": [CLAUDE_REDIRECT_URIS[0]],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert r.status_code in (200, 201), r.text


# --- CIMD fetch (08, item 3) ----------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/client.json",  # not HTTPS
        "https://127.0.0.1/client.json",
        "https://169.254.169.254/client.json",  # metadata server
        "https://10.0.0.5/client.json",
        "https://[::1]/client.json",
        "https://[::ffff:169.254.169.254]/client.json",
        "https://100.64.0.1/client.json",
    ],
)
async def test_cimd_fetch_refuses_private_and_non_https_addresses(url):
    from fastmcp.server.auth.cimd import CIMDFetcher, CIMDValidationError

    with pytest.raises(CIMDValidationError):
        await CIMDFetcher().fetch(url)


def test_cimd_fetch_limits():
    from fastmcp.server.auth.cimd import CIMDFetcher

    assert CIMDFetcher.MAX_RESPONSE_SIZE == 5120
    assert CIMDFetcher().timeout == 10.0
    assert CIMDFetcher.MAX_CACHE_SIZE == 1000


# --- the encrypted store ---------------------------------------------------


@pytest.mark.anyio
async def test_encrypted_store_round_trip_and_ciphertext_at_rest():
    raw = MemoryStore()
    store = encrypt_store(raw, (KEY_A,))
    await store.put(key="k", value={"refresh_token": "secret-refresh"}, collection="c", ttl=60)
    assert await store.get(key="k", collection="c") == {"refresh_token": "secret-refresh"}
    stored = json.dumps(await raw.get(key="k", collection="c"))
    assert "secret-refresh" not in stored


@pytest.mark.anyio
async def test_key_rotation_new_key_first_old_key_still_reads():
    raw = MemoryStore()
    await encrypt_store(raw, (KEY_A,)).put(key="k", value={"v": 1}, collection="c")
    rotated = encrypt_store(raw, (KEY_B, KEY_A))
    assert await rotated.get(key="k", collection="c") == {"v": 1}
    await rotated.put(key="n", value={"v": 2}, collection="c")
    # New writes use the new key: a store that knows only KEY_B reads them.
    assert await encrypt_store(raw, (KEY_B,)).get(key="n", collection="c") == {"v": 2}


@pytest.mark.anyio
async def test_removed_key_reads_as_missing_not_as_error():
    raw = MemoryStore()
    await encrypt_store(raw, (KEY_A,)).put(key="k", value={"v": 1}, collection="c")
    assert await encrypt_store(raw, (KEY_B,)).get(key="k", collection="c") is None


@pytest.mark.anyio
async def test_entries_without_ttl_get_one_so_registrations_cannot_pile_up():
    store = bound_ttl(MemoryStore())
    await store.put(key="client", value={"v": 1}, collection="mcp-oauth-proxy-clients")
    ttl = await store.ttl(key="client", collection="mcp-oauth-proxy-clients")
    assert ttl is not None and ttl[1] is not None
    assert 89 * 24 * 3600 < ttl[1] <= oauth.MISSING_TTL_SECONDS
    await store.put(key="long", value={"v": 1}, collection="c", ttl=10 * oauth.MAX_TTL_SECONDS)
    assert (await store.ttl(key="long", collection="c"))[1] <= oauth.MAX_TTL_SECONDS


# --- the Firestore store on the emulator -----------------------------------


@pytest.mark.emulator
@pytest.mark.anyio
async def test_firestore_layout_expiry_field_and_ciphertext(firestore_db):
    from datetime import datetime

    from google.cloud import firestore

    from tests.fixtures import seed

    client = firestore.AsyncClient(project=seed.client().project, database=seed.client()._database)
    store = oauth.build_token_store(settings(), client)
    await store.put(key="jti-1", value={"access_token": "upstream-secret"}, collection="mcp-upstream-tokens", ttl=600)
    await store.put(key="no-ttl", value={"x": 1}, collection="mcp-oauth-proxy-clients")

    docs = list(firestore_db.collection("mcp-upstream-tokens").stream())
    assert len(docs) == 1
    doc = docs[0].to_dict()
    assert set(doc) == {"version", "value", "created_at", "expires_at"}
    # A Firestore timestamp, so a TTL policy on `expires_at` can delete the entry.
    assert isinstance(doc["expires_at"], datetime)
    assert 590 < doc["expires_at"].timestamp() - time.time() < 610
    assert "upstream-secret" not in json.dumps(doc, default=str)

    assert await store.get(key="jti-1", collection="mcp-upstream-tokens") == {"access_token": "upstream-secret"}
    assert await store.get(key="no-ttl", collection="mcp-oauth-proxy-clients") == {"x": 1}
    (no_ttl,) = firestore_db.collection("mcp-oauth-proxy-clients").stream()
    assert isinstance(no_ttl.to_dict()["expires_at"], datetime)  # bounded, never kept for ever

    # An entry that has expired reads as missing, even before the TTL policy removes it.
    await store.put(key="gone", value={"x": 1}, collection="mcp-upstream-tokens", ttl=1)
    time.sleep(1.2)
    assert await store.get(key="gone", collection="mcp-upstream-tokens") is None

    # Nothing outside the store's own collections was written.
    names = {c.id for c in firestore_db.collections()}
    assert names == {"mcp-upstream-tokens", "mcp-oauth-proxy-clients"}
    client.close()


@pytest.mark.emulator
@pytest.mark.anyio
async def test_firestore_store_rotation(firestore_db):
    from google.cloud import firestore

    from tests.fixtures import seed

    client = firestore.AsyncClient(project=seed.client().project, database=seed.client()._database)
    old = oauth.build_token_store(settings(fernet_keys=(KEY_A,)), client)
    await old.put(key="k", value={"v": 1}, collection="mcp-jti-mappings", ttl=60)
    rotated = oauth.build_token_store(settings(fernet_keys=(KEY_B, KEY_A)), client)
    assert await rotated.get(key="k", collection="mcp-jti-mappings") == {"v": 1}
    dropped = oauth.build_token_store(settings(fernet_keys=(KEY_B,)), client)
    assert await dropped.get(key="k", collection="mcp-jti-mappings") is None


# --- an authenticated stateless call (07, "To verify" item 5) --------------


def _authenticated_app(admin=PROD):
    """The HTTP app plus a FastMCP access token the proxy accepts, minted by
    seeding the proxy's own stores as a finished sign-in would."""
    import asyncio
    import secrets

    from fastmcp import FastMCP
    from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet
    from fastmcp.server.dependencies import get_access_token

    v = build_verifier(settings(), admin, http_client=fake_google())
    auth = build_auth(settings(), admin, token_store=MemoryStore(), verifier=v)
    mcp = FastMCP("t", auth=auth)

    @mcp.tool
    def whoami() -> str:
        token = get_access_token()
        return f"{token.claims['sub']}|{token.claims['hd']}"

    jti, upstream_id = secrets.token_urlsafe(16), secrets.token_urlsafe(16)

    async def seed() -> str:
        await auth._upstream_token_store.put(
            key=upstream_id,
            value=UpstreamTokenSet(
                upstream_token_id=upstream_id,
                access_token="good-token",
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
        await auth._jti_mapping_store.put(
            key=jti,
            value=JTIMapping(jti=jti, upstream_token_id=upstream_id, created_at=time.time()),
            ttl=3000,
        )
        return auth.jwt_issuer.issue_access_token(
            client_id="client-1", scopes=list(REQUIRED_SCOPES), jti=jti, expires_in=3000
        )

    http = mcp.http_app(stateless_http=True, **http_protection_options(settings()))  # creates the JWT issuer
    token = asyncio.run(seed())
    return http, token, v


CALL = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}}


def _call(client, token):
    return client.post(
        "/mcp",
        json=CALL,
        headers={**MCP_HEADERS, **HOST, "Authorization": f"Bearer {token}", "mcp-protocol-version": "2025-06-18"},
    )


def test_stateless_call_with_proxy_token_sees_the_validated_sub():
    from starlette.testclient import TestClient

    http, token, _ = _authenticated_app()
    with TestClient(http, base_url=BASE_URL) as c:
        # No `initialize` was sent: stateless mode needs no session.
        r = _call(c, token)
        assert r.status_code == 200, r.text
        assert "1001|example.com" in r.text
        # A second request on a fresh "instance" state works the same.
        assert _call(c, token).status_code == 200


def test_domain_removed_after_sign_in_stops_the_next_call():
    from starlette.testclient import TestClient

    http, token, v = _authenticated_app()
    with TestClient(http, base_url=BASE_URL) as c:
        assert _call(c, token).status_code == 200
        v._admin = AdminPolicy(DeployMode.PRODUCTION, frozenset({"another.com"}), Policy())
        assert _call(c, token).status_code == 401
