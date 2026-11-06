"""
Google OAuth sign-in for the settings app (step 4 of
docs/remote/04-settings-server-design.md).

Needs the `remote` extra. Never imported by mock.py or local mode.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import requests as _requests
from fastapi import Request
from fastapi.responses import HTMLResponse
from nicegui import app as nicegui_app
from starlette.responses import Response
from starlette.responses import RedirectResponse

from .app import BASE, SettingsApp
from .models import Account

if TYPE_CHECKING:
    pass

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"

_SCOPES = "openid email https://www.googleapis.com/auth/chat.spaces.readonly"
_REQUIRED_SCOPE = "https://www.googleapis.com/auth/chat.spaces.readonly"

LOGIN_COOKIE = "__Host-gcm_login"
_LOGIN_COOKIE_MAX_AGE = 600  # 10 minutes


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) with S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


class _IdTokenError(Exception):
    pass


def _exchange_code(
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    r = _requests.post(
        _TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _verify_id_token(
    *,
    id_token: str,
    client_id: str,
    nonce: str,
    allowed_domains: frozenset[str],
    deploy_mode: str,
) -> dict:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    try:
        claims = google_id_token.verify_oauth2_token(
            id_token, google_requests.Request(), client_id
        )
    except Exception as exc:
        raise _IdTokenError("ID token verification failed.") from exc

    if claims.get("email_verified") is not True:
        raise _IdTokenError("Email not verified.")
    if not secrets.compare_digest(claims.get("nonce", ""), nonce):
        raise _IdTokenError("Nonce mismatch.")

    hd = (claims.get("hd") or "").lower()
    if hd:
        if hd not in allowed_domains:
            raise _IdTokenError("Account domain not allowed.")
    elif deploy_mode == "production":
        raise _IdTokenError("Account domain not allowed.")

    return claims


def _error_page(message: str) -> Response:
    body = (
        "<!doctype html><html><head><meta charset=utf-8>"
        f"<title>Sign-in error</title></head><body>"
        f"<h1>Sign-in error</h1><p>Sign-in could not be completed.</p>"
        f"<p><a href=\"{BASE}/sign-in\">Try again</a></p>"
        "</body></html>"
    )
    return HTMLResponse(content=body, status_code=400)


def _scope_error_page() -> Response:
    body = (
        "<!doctype html><html><head><meta charset=utf-8>"
        "<title>Permission required</title></head><body>"
        "<h1>Permission required</h1>"
        "<p>The connector needs access to your space list to work. "
        "Please sign in again and allow the requested permission.</p>"
        f"<p><a href=\"{BASE}/sign-in\">Try again</a></p>"
        "</body></html>"
    )
    return HTMLResponse(content=body, status_code=403)


class GoogleSignIn:
    """
    Real SignIn implementation using Google OAuth 2.0 with PKCE (S256).

    Takes a login-transaction store (FirestoreLoginTransactions or any object
    with the same create/consume interface), OAuth client credentials, and the
    list of allowed domains.

    ``deploy_mode`` is "production" (default) or "development" (#86).
    In development mode, accounts without an ``hd`` claim are accepted.
    """

    def __init__(
        self,
        login_transactions,
        *,
        client_id: str,
        client_secret: str,
        allowed_domains: frozenset[str],
        redirect_uri: str,
        deploy_mode: str = "production",
    ) -> None:
        self._txs = login_transactions
        self._client_id = client_id
        self._client_secret = client_secret
        self._allowed_domains = frozenset(d.lower() for d in allowed_domains)
        self._redirect_uri = redirect_uri
        self._deploy_mode = deploy_mode
        self._settings: SettingsApp | None = None

    def start(self, request: Request, *, select_account: bool) -> Response:
        code_verifier, code_challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        nonce = secrets.token_urlsafe(16)

        now = datetime.now(timezone.utc)
        tx_id = self._txs.create(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            select_account=select_account,
            now=now,
        )

        params: dict[str, str] = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": _SCOPES,
            "access_type": "online",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if len(self._allowed_domains) == 1:
            params["hd"] = next(iter(self._allowed_domains))
        if select_account:
            params["prompt"] = "select_account"

        auth_url = f"{_AUTH_URL}?{urlencode(params)}"
        response = RedirectResponse(auth_url, status_code=303)
        response.set_cookie(
            LOGIN_COOKIE,
            tx_id,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=_LOGIN_COOKIE_MAX_AGE,
            path="/",
        )
        return response

    def install(self, settings: SettingsApp) -> None:
        self._settings = settings

        @nicegui_app.get(f"{BASE}/callback")
        def callback(
            request: Request,
            code: str = "",
            state: str = "",
            error: str = "",
        ) -> Response:
            return self._handle_callback(
                request, code=code, state=state, error=error
            )

    def _handle_callback(
        self,
        request: Request,
        *,
        code: str,
        state: str,
        error: str,
    ) -> Response:
        assert self._settings is not None

        if error:
            return _error_page("Sign-in cancelled.")

        tx_id = request.cookies.get(LOGIN_COOKIE)
        now = datetime.now(timezone.utc)
        tx = self._txs.consume(tx_id, now)
        if tx is None:
            return _error_page("The sign-in session expired or was already used.")
        if not secrets.compare_digest(tx.state, state):
            return _error_page("Invalid sign-in state.")

        try:
            token_data = _exchange_code(
                code=code,
                code_verifier=tx.code_verifier,
                client_id=self._client_id,
                client_secret=self._client_secret,
                redirect_uri=self._redirect_uri,
            )
        except Exception:
            return _error_page("Failed to complete sign-in.")

        id_token = token_data.get("id_token", "")
        try:
            claims = _verify_id_token(
                id_token=id_token,
                client_id=self._client_id,
                nonce=tx.nonce,
                allowed_domains=self._allowed_domains,
                deploy_mode=self._deploy_mode,
            )
        except _IdTokenError:
            return _error_page("Sign-in refused.")

        granted_scopes = set(token_data.get("scope", "").split())
        if _REQUIRED_SCOPE not in granted_scopes:
            return _scope_error_page()

        account = Account(
            sub=claims["sub"],
            email=claims["email"],
            hd=claims.get("hd") or "",
            access_token=token_data.get("access_token", ""),
        )
        response = self._settings.finish_sign_in(request, account)
        response.delete_cookie(LOGIN_COOKIE, path="/")
        return response
