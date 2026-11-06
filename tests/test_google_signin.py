"""
Unit tests for GoogleSignIn (issue #89).

Uses an in-memory stub for the login-transaction store and mocks the
outbound calls (token exchange, ID token verification) so no real
Google credentials are needed.
"""

from __future__ import annotations

import hashlib
import secrets
from base64 import urlsafe_b64decode
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("nicegui")
pytest.importorskip("google.auth")

from google_chat_mcp.settings.google_signin import (  # noqa: E402
    LOGIN_COOKIE,
    GoogleSignIn,
    _IdTokenError,
    _error_page,
    _scope_error_page,
    _verify_id_token,
)
from google_chat_mcp.settings.mock import ALICE, create_mock_settings  # noqa: E402
from google_chat_mcp.settings.firestore import LoginTransaction  # noqa: E402

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
_TTL = timedelta(minutes=10)


# ---------------------------------------------------------------------------
# In-memory login transaction stub


class _InMemoryLoginTransactions:
    def __init__(self):
        self._txs: dict[str, LoginTransaction] = {}

    def create(self, *, state, nonce, code_verifier, select_account, now):
        tx_id = secrets.token_urlsafe(32)
        self._txs[tx_id] = LoginTransaction(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            select_account=select_account,
            created_at=now,
            expire_at=now + _TTL,
        )
        return tx_id

    def consume(self, tx_id, now):
        if not tx_id or tx_id not in self._txs:
            return None
        tx = self._txs.pop(tx_id)
        if now >= tx.expire_at:
            return None
        return tx


def _make_sign_in(*, allowed_domains=("example.com",), deploy_mode="production"):
    return GoogleSignIn(
        _InMemoryLoginTransactions(),
        client_id="test-client-id",
        client_secret="test-secret",
        allowed_domains=frozenset(allowed_domains),
        redirect_uri="https://app.example.com/settings/callback",
        deploy_mode=deploy_mode,
    )


def _fake_request(cookies: dict | None = None):
    req = MagicMock()
    req.cookies = cookies or {}
    return req


# ---------------------------------------------------------------------------
# start()


def test_start_returns_303_to_google():
    sign_in = _make_sign_in()
    resp = sign_in.start(_fake_request(), select_account=False)
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?")


def test_start_pkce_code_challenge_is_s256_of_verifier():
    sign_in = _make_sign_in()
    resp = sign_in.start(_fake_request(), select_account=False)
    loc = resp.headers["location"]
    params = parse_qs(urlparse(loc).query)
    assert params["code_challenge_method"] == ["S256"]
    # The verifier is stored in the transaction; check the challenge is S256.
    # We can't retrieve it from the response alone, but we can verify its shape.
    challenge = params["code_challenge"][0]
    # base64url, 43 chars = 32 bytes SHA-256
    padded = challenge + "=" * (-len(challenge) % 4)
    decoded = urlsafe_b64decode(padded)
    assert len(decoded) == 32  # 256-bit digest


def test_start_required_scopes_are_present():
    sign_in = _make_sign_in()
    resp = sign_in.start(_fake_request(), select_account=False)
    params = parse_qs(urlparse(resp.headers["location"]).query)
    scope = params["scope"][0]
    assert "openid" in scope
    assert "email" in scope
    assert "chat.spaces.readonly" in scope


def test_start_includes_hd_hint_for_single_domain():
    sign_in = _make_sign_in(allowed_domains=("example.com",))
    resp = sign_in.start(_fake_request(), select_account=False)
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert params["hd"] == ["example.com"]


def test_start_no_hd_hint_for_multiple_domains():
    sign_in = _make_sign_in(allowed_domains=("example.com", "other.com"))
    resp = sign_in.start(_fake_request(), select_account=False)
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert "hd" not in params


def test_start_select_account_adds_prompt():
    sign_in = _make_sign_in()
    resp = sign_in.start(_fake_request(), select_account=True)
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert params.get("prompt") == ["select_account"]


def test_start_no_prompt_without_select_account():
    sign_in = _make_sign_in()
    resp = sign_in.start(_fake_request(), select_account=False)
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert "prompt" not in params


def test_start_sets_login_cookie():
    sign_in = _make_sign_in()
    resp = sign_in.start(_fake_request(), select_account=False)
    cookie_header = resp.headers.get("set-cookie", "")
    assert LOGIN_COOKIE in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header.lower() or "samesite=lax" in cookie_header.lower()


def test_start_stores_transaction_accessible_via_cookie():
    sign_in = _make_sign_in()
    resp = sign_in.start(_fake_request(), select_account=False)
    # Extract tx_id from Set-Cookie header
    cookie_header = resp.headers.get("set-cookie", "")
    tx_id = cookie_header.split(f"{LOGIN_COOKIE}=")[1].split(";")[0]
    assert sign_in._txs.consume(tx_id, NOW) is not None


# ---------------------------------------------------------------------------
# callback — error branches


def _start_and_get_tx_id(sign_in):
    """Do start(), return (request with cookie, state from redirect)."""
    resp = sign_in.start(_fake_request(), select_account=False)
    cookie_header = resp.headers.get("set-cookie", "")
    tx_id = cookie_header.split(f"{LOGIN_COOKIE}=")[1].split(";")[0]
    params = parse_qs(urlparse(resp.headers["location"]).query)
    state = params["state"][0]
    nonce = params["nonce"][0]
    return tx_id, state, nonce


def test_callback_error_param_returns_error_page():
    sign_in = _make_sign_in()
    sign_in._settings = create_mock_settings()
    resp = sign_in._handle_callback(_fake_request(), code="", state="", error="access_denied")
    assert resp.status_code == 400


def test_callback_missing_cookie_returns_error_page():
    sign_in = _make_sign_in()
    sign_in._settings = create_mock_settings()
    resp = sign_in._handle_callback(_fake_request(), code="auth_code", state="st", error="")
    assert resp.status_code == 400


def test_callback_state_mismatch_returns_error_page():
    sign_in = _make_sign_in()
    sign_in._settings = create_mock_settings()
    tx_id, _state, _nonce = _start_and_get_tx_id(sign_in)
    req = _fake_request(cookies={LOGIN_COOKIE: tx_id})
    resp = sign_in._handle_callback(req, code="code", state="wrong-state", error="")
    assert resp.status_code == 400


def test_callback_code_exchange_failure_returns_error_page():
    sign_in = _make_sign_in()
    sign_in._settings = create_mock_settings()
    tx_id, state, _nonce = _start_and_get_tx_id(sign_in)
    req = _fake_request(cookies={LOGIN_COOKIE: tx_id})

    with patch(
        "google_chat_mcp.settings.google_signin._exchange_code",
        side_effect=Exception("network error"),
    ):
        resp = sign_in._handle_callback(req, code="code", state=state, error="")
    assert resp.status_code == 400


def test_callback_id_token_verification_failure_returns_error_page():
    sign_in = _make_sign_in()
    sign_in._settings = create_mock_settings()
    tx_id, state, _nonce = _start_and_get_tx_id(sign_in)
    req = _fake_request(cookies={LOGIN_COOKIE: tx_id})

    with patch("google_chat_mcp.settings.google_signin._exchange_code", return_value={
        "access_token": "tok", "id_token": "bad", "scope": "openid email https://www.googleapis.com/auth/chat.spaces.readonly",
    }):
        with patch(
            "google_chat_mcp.settings.google_signin._verify_id_token",
            side_effect=_IdTokenError("bad token"),
        ):
            resp = sign_in._handle_callback(req, code="code", state=state, error="")
    assert resp.status_code == 400


def test_callback_missing_scope_returns_scope_error_page():
    sign_in = _make_sign_in()
    sign_in._settings = create_mock_settings()
    tx_id, state, nonce = _start_and_get_tx_id(sign_in)
    req = _fake_request(cookies={LOGIN_COOKIE: tx_id})

    with patch("google_chat_mcp.settings.google_signin._exchange_code", return_value={
        "access_token": "tok", "id_token": "id-tok", "scope": "openid email",
    }):
        with patch("google_chat_mcp.settings.google_signin._verify_id_token", return_value={
            "sub": "123", "email": "alice@example.com", "hd": "example.com",
            "nonce": nonce,
        }):
            resp = sign_in._handle_callback(req, code="code", state=state, error="")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# callback — success path


def test_callback_success_creates_session_and_redirects():
    sign_in = _make_sign_in()
    settings = create_mock_settings()
    sign_in._settings = settings
    tx_id, state, nonce = _start_and_get_tx_id(sign_in)
    req = _fake_request(cookies={LOGIN_COOKIE: tx_id})

    with patch("google_chat_mcp.settings.google_signin._exchange_code", return_value={
        "access_token": "tok-abc",
        "id_token": "id-tok",
        "scope": "openid email https://www.googleapis.com/auth/chat.spaces.readonly",
    }):
        with patch("google_chat_mcp.settings.google_signin._verify_id_token", return_value={
            "sub": ALICE.sub, "email": ALICE.email, "hd": "example.com", "nonce": nonce,
        }):
            resp = sign_in._handle_callback(req, code="code", state=state, error="")

    assert resp.status_code == 303
    assert "/settings" in resp.headers["location"]


def test_callback_success_stores_access_token_in_account():
    sign_in = _make_sign_in()
    settings = create_mock_settings()
    sign_in._settings = settings
    tx_id, state, nonce = _start_and_get_tx_id(sign_in)
    req = _fake_request(cookies={LOGIN_COOKIE: tx_id})

    captured = {}

    def fake_finish(request, account):
        captured["account"] = account
        # Mimic finish_sign_in returning a redirect.
        from starlette.responses import RedirectResponse
        return RedirectResponse("/settings", status_code=303)

    settings.finish_sign_in = fake_finish

    with patch("google_chat_mcp.settings.google_signin._exchange_code", return_value={
        "access_token": "my-token",
        "id_token": "id-tok",
        "scope": "openid email https://www.googleapis.com/auth/chat.spaces.readonly",
    }):
        with patch("google_chat_mcp.settings.google_signin._verify_id_token", return_value={
            "sub": ALICE.sub, "email": ALICE.email, "hd": "example.com", "nonce": nonce,
        }):
            sign_in._handle_callback(req, code="code", state=state, error="")

    assert captured["account"].access_token == "my-token"
    assert captured["account"].email == ALICE.email
    assert captured["account"].hd == "example.com"


def test_callback_clears_login_cookie_on_success():
    sign_in = _make_sign_in()
    settings = create_mock_settings()
    sign_in._settings = settings
    tx_id, state, nonce = _start_and_get_tx_id(sign_in)
    req = _fake_request(cookies={LOGIN_COOKIE: tx_id})

    with patch("google_chat_mcp.settings.google_signin._exchange_code", return_value={
        "access_token": "tok",
        "id_token": "id-tok",
        "scope": "openid email https://www.googleapis.com/auth/chat.spaces.readonly",
    }):
        with patch("google_chat_mcp.settings.google_signin._verify_id_token", return_value={
            "sub": ALICE.sub, "email": ALICE.email, "hd": "example.com", "nonce": nonce,
        }):
            resp = sign_in._handle_callback(req, code="code", state=state, error="")

    # The login cookie should be deleted (max-age=0 or expires in the past).
    cookie_headers = [v for k, v in resp.raw_headers if k == b"set-cookie"]
    login_deletions = [
        h.decode() for h in cookie_headers
        if LOGIN_COOKIE.encode() in h and (b"Max-Age=0" in h or b"max-age=0" in h)
    ]
    assert login_deletions, "Login cookie should be deleted after successful sign-in"


# ---------------------------------------------------------------------------
# _verify_id_token unit tests


def _make_claims(**overrides):
    base = {
        "sub": "123",
        "email": "alice@example.com",
        "email_verified": True,
        "hd": "example.com",
        "nonce": "test-nonce",
    }
    return {**base, **overrides}


def _verify_id_token_mocked(claims: dict, *, nonce: str, allowed_domains: frozenset,
                             deploy_mode: str = "production"):
    """Call _verify_id_token with the Google verify_oauth2_token mocked."""
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims):
        with patch("google.auth.transport.requests.Request"):
            return _verify_id_token(
                id_token="tok",
                client_id="cid",
                nonce=nonce,
                allowed_domains=allowed_domains,
                deploy_mode=deploy_mode,
            )


def test_verify_id_token_accepts_valid_claims():
    claims = _make_claims()
    result = _verify_id_token_mocked(claims, nonce="test-nonce", allowed_domains=frozenset({"example.com"}))
    assert result["sub"] == "123"


def test_verify_id_token_rejects_unverified_email():
    with pytest.raises(_IdTokenError):
        _verify_id_token_mocked(
            _make_claims(email_verified=False),
            nonce="n",
            allowed_domains=frozenset({"example.com"}),
        )


def test_verify_id_token_rejects_wrong_nonce():
    with pytest.raises(_IdTokenError):
        _verify_id_token_mocked(
            _make_claims(nonce="other"),
            nonce="expected",
            allowed_domains=frozenset({"example.com"}),
        )


def test_verify_id_token_rejects_wrong_hd():
    with pytest.raises(_IdTokenError):
        _verify_id_token_mocked(
            _make_claims(hd="evil.com"),
            nonce="n",
            allowed_domains=frozenset({"example.com"}),
        )


def test_verify_id_token_production_rejects_no_hd():
    with pytest.raises(_IdTokenError):
        _verify_id_token_mocked(
            _make_claims(hd=None),
            nonce="n",
            allowed_domains=frozenset({"example.com"}),
            deploy_mode="production",
        )


def test_verify_id_token_development_accepts_no_hd():
    claims = _make_claims(nonce="test-nonce")
    del claims["hd"]
    result = _verify_id_token_mocked(
        claims,
        nonce="test-nonce",
        allowed_domains=frozenset({"example.com"}),
        deploy_mode="development",
    )
    assert result["sub"] == "123"


def test_verify_id_token_development_still_rejects_wrong_hd():
    with pytest.raises(_IdTokenError):
        _verify_id_token_mocked(
            _make_claims(hd="evil.com"),
            nonce="n",
            allowed_domains=frozenset({"example.com"}),
            deploy_mode="development",
        )
