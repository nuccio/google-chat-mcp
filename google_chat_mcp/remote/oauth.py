"""
The connector's authentication: `OAuthProxy` in front of Google (docs/remote/07,
"Transport and connector authentication" and "Token store"; 08, N5).

Claude is the OAuth client of `gcm-mcp`; `gcm-mcp` is the OAuth client of
Google. `build_auth()` returns a FastMCP `GoogleProvider` configured as 07 and
08 require:

- `audience` of the Google token verifier = this service's client ID (a token
  issued to another OAuth client is refused);
- the **domain check on every request**: the verifier is wrapped so that an
  account whose `hd` is not in `ALLOWED_DOMAINS` is refused, and so is an
  account with no `hd` unless `DEPLOY_MODE=development` (`AdminPolicy.domain_allowed`).
  The wrapper runs after FastMCP's own check each time a token is used, so
  removing a domain applies at once;
- `require_authorization_consent=True`;
- `allowed_client_redirect_uris` = Claude's callbacks only;
- state (transactions, codes, token map, refresh tokens) lives in a Firestore
  store encrypted with Fernet (`MultiFernet`: a list of keys, newest first,
  for rotation), so `gcm-mcp` is stateless between requests (M6);
- `jwt_signing_key` and the Fernet keys come from the environment, where Cloud
  Run puts the Secret Manager values it is told to reference (07, "Runtime and
  cost"). `gcm-settings` has no access to either secret.

`http_protection_options()` returns the arguments the HTTP entry point (#80)
must pass to `FastMCP.http_app()`: Host/Origin protection on, allowed host =
host of `BASE_URL` (08, N5). No CORS middleware is ever added.

Environment (all required unless noted):

- `BASE_URL`: public HTTPS address of `gcm-mcp`. `http://localhost` only in
  `DEPLOY_MODE=development`.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`: the connector's Google client.
- `OAUTH_JWT_SIGNING_KEY`: at least 32 characters.
- `OAUTH_FERNET_KEYS`: one or more Fernet keys, comma-separated, newest first.

A missing or invalid value raises `OAuthConfigError`; the entry point lets it
stop the service at start-up (fail closed). No value is ever logged.

Needs the `remote` extra. Local mode never imports this module (NFR-7).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .policy import AdminPolicy

log = logging.getLogger(__name__)

# Claude's OAuth callbacks. Only the first is named in Anthropic's current
# documentation (read 2026-11-07); the second is the address the help center
# warned about earlier and is kept so a move does not break sign-in. Claude
# Code uses a loopback redirect and is not supported as a client of this server.
CLAUDE_REDIRECT_URIS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)

# `openid` and `email` give the connector a `sub`; the four Chat scopes are the
# ones local mode uses (auth.SCOPES; a test keeps the two lists equal).
GOOGLE_EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
CHAT_SCOPES = (
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.messages.create",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
)
REQUIRED_SCOPES = ("openid", GOOGLE_EMAIL_SCOPE, *CHAT_SCOPES)

MIN_JWT_KEY_LENGTH = 32


class OAuthConfigError(ValueError):
    """The OAuth configuration is missing or invalid. Stops the service at start-up."""


@dataclass(frozen=True)
class OAuthSettings:
    base_url: str
    client_id: str
    client_secret: str = field(repr=False)
    jwt_signing_key: str = field(repr=False)
    fernet_keys: tuple[str, ...] = field(repr=False)

    @property
    def host(self) -> str:
        """Host (with port, if any) of `BASE_URL`, as it appears in a Host header."""
        return urlparse(self.base_url).netloc.lower()

    @classmethod
    def from_env(
        cls, admin: AdminPolicy, environ: Mapping[str, str] | None = None
    ) -> OAuthSettings:
        env = os.environ if environ is None else environ

        def need(name: str) -> str:
            value = (env.get(name) or "").strip()
            if not value:
                raise OAuthConfigError(f"{name} is required")
            return value

        base_url = parse_base_url(need("BASE_URL"), development=admin.development)
        jwt_key = need("OAUTH_JWT_SIGNING_KEY")
        if len(jwt_key) < MIN_JWT_KEY_LENGTH:
            raise OAuthConfigError(
                f"OAUTH_JWT_SIGNING_KEY must have at least {MIN_JWT_KEY_LENGTH} characters"
            )
        return cls(
            base_url=base_url,
            client_id=need("GOOGLE_CLIENT_ID"),
            client_secret=need("GOOGLE_CLIENT_SECRET"),
            jwt_signing_key=jwt_key,
            fernet_keys=parse_fernet_keys(need("OAUTH_FERNET_KEYS")),
        )


def parse_base_url(raw: str, *, development: bool) -> str:
    """`BASE_URL` without a trailing slash. HTTPS only; `http://localhost` and
    `http://127.0.0.1` are accepted in development mode."""
    url = raw.strip().rstrip("/")
    parsed = urlparse(url)
    if not parsed.hostname or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise OAuthConfigError("BASE_URL must be an origin: scheme and host, no path")
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and development and parsed.hostname in ("localhost", "127.0.0.1"):
        return url
    raise OAuthConfigError("BASE_URL must be an https:// address")


def parse_fernet_keys(raw: str) -> tuple[str, ...]:
    """Validate a comma-separated list of Fernet keys, newest first."""
    from cryptography.fernet import Fernet

    keys = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not keys:
        raise OAuthConfigError("OAUTH_FERNET_KEYS holds no key")
    for key in keys:
        try:
            Fernet(key)
        except (ValueError, TypeError):
            # Never include the value: it is a secret.
            raise OAuthConfigError("OAUTH_FERNET_KEYS: not a valid Fernet key") from None
    return keys


# ---------------------------------------------------------------------------
# Token verifier with the domain check
# ---------------------------------------------------------------------------


def _verifier_class():
    from fastmcp.server.auth.providers.google import GoogleTokenVerifier

    class DomainCheckedGoogleVerifier(GoogleTokenVerifier):
        """`GoogleTokenVerifier` plus the `hd` check, on every call.

        FastMCP's verifier checks the token with Google (audience, scopes,
        `sub`) and reads the profile from the userinfo endpoint, whose `hd`
        field names the Workspace domain. `hd` is checked here after each
        verification, including the ones served from FastMCP's own cache, so
        the cache can never keep an account whose domain was removed.

        If the profile could not be read there is no `hd`, and in production
        that refuses the request (M9: fail closed).
        """

        def __init__(self, *, admin: AdminPolicy, **kwargs) -> None:
            super().__init__(**kwargs)
            self._admin = admin

        async def verify_token(self, token: str):
            access = await super().verify_token(token)
            if access is None:
                return None
            profile = access.claims.get("google_user_data") or {}
            hd = profile.get("hd") if isinstance(profile, dict) else None
            if not self._admin.domain_allowed(hd):
                # No email, no sub, no domain value in the log (M11).
                log.warning("Refused a token: account domain not allowed (hd present: %s)", bool(hd))
                return None
            access.claims["hd"] = hd
            return access

    return DomainCheckedGoogleVerifier


def build_verifier(settings: OAuthSettings, admin: AdminPolicy, *, http_client=None):
    """The Google token verifier of this service (audience + domain check)."""
    return _verifier_class()(
        admin=admin,
        required_scopes=list(REQUIRED_SCOPES),
        audience=settings.client_id,
        http_client=http_client,
    )


# ---------------------------------------------------------------------------
# Encrypted Firestore token store
# ---------------------------------------------------------------------------


def create_async_client(project: str | None = None, database: str | None = None):
    """An async Firestore client configured like `firestore.create_client`."""
    from google.cloud import firestore

    from .firestore import DEFAULT_DATABASE, EMULATOR_PROJECT, using_emulator

    project = (
        project
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or (EMULATOR_PROJECT if using_emulator() else None)
    )
    database = database or os.environ.get("FIRESTORE_DATABASE") or DEFAULT_DATABASE
    return firestore.AsyncClient(project=project, database=database)


def _store_class():
    from key_value.aio._utils.serialization import BasicSerializationAdapter
    from key_value.aio.stores.firestore import (
        FirestoreStore,
        FirestoreV1CollectionSanitizationStrategy,
        FirestoreV1KeySanitizationStrategy,
    )

    class ExpiringFirestoreStore(FirestoreStore):
        """`FirestoreStore` whose `created_at` and `expires_at` are Firestore
        timestamps, not ISO strings, so a Firestore TTL policy on `expires_at`
        can delete expired entries (07, "Token store: TTL").

        Layout: one collection per FastMCP collection (`mcp-oauth-transactions`,
        `mcp-upstream-tokens`, ... : none collides with `acls`, `users`, `audit`, `sessions`), one
        document per key (sanitised), fields `version`, `value` (a Fernet token
        once wrapped: the store is always used through `build_token_store`),
        `created_at`, `expires_at`.
        """

        def __init__(self, client) -> None:
            super().__init__(
                client,
                key_sanitization_strategy=FirestoreV1KeySanitizationStrategy(),
                collection_sanitization_strategy=FirestoreV1CollectionSanitizationStrategy(),
            )
            self._serialization_adapter = BasicSerializationAdapter(
                date_format="datetime", value_format="string"
            )

    return ExpiringFirestoreStore


def encrypt_store(store, fernet_keys):
    """Wrap any `AsyncKeyValue` in Fernet encryption.

    `MultiFernet` encrypts with the first key and decrypts with any, so a key
    is rotated by putting the new one first (07, "Rotation"). A value that
    cannot be decrypted (a key that was removed) reads as missing, so the user
    signs in again: losing the key is not a disaster (07).
    """
    from cryptography.fernet import Fernet, MultiFernet
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    return FernetEncryptionWrapper(
        key_value=store,
        fernet=MultiFernet([Fernet(k) for k in fernet_keys]),
        raise_on_decryption_error=False,
    )


# FastMCP stores a client registration (`POST /register`) with no expiry, so an
# anonymous caller could add documents for ever (08, "Registration spam").
# Every entry written without a TTL gets this one, and the Firestore TTL policy
# on `expires_at` then removes it. Chosen well above the 7-day refresh-token
# life of apps in Testing; whether Claude registers again by itself when a
# client has expired is a `[GCP]` check (08, "To verify").
MISSING_TTL_SECONDS = 90 * 24 * 3600
MAX_TTL_SECONDS = 400 * 24 * 3600  # above FastMCP's one-year refresh-token default


def bound_ttl(store):
    """Give every entry an expiry, so nothing stays in the store for ever."""
    from key_value.aio.wrappers.ttl_clamp import TTLClampWrapper

    return TTLClampWrapper(
        store, min_ttl=0, max_ttl=MAX_TTL_SECONDS, missing_ttl=MISSING_TTL_SECONDS
    )


def build_token_store(settings: OAuthSettings, client=None):
    """The proxy's `client_storage`: Firestore, every entry with an expiry,
    encrypted with Fernet."""
    store = bound_ttl(_store_class()(client or create_async_client()))
    return encrypt_store(store, settings.fernet_keys)


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------


def _refuse_unsafe_environment() -> None:
    """FastMCP can skip its SSRF checks on CIMD fetches when a trusted outbound
    proxy is declared. Nothing here sets up such a proxy, so refuse to start."""
    import fastmcp

    if fastmcp.settings.ssrf_trust_proxy:
        raise OAuthConfigError(
            "FASTMCP_SSRF_TRUST_PROXY is set: it disables the SSRF checks on client metadata "
            "fetches. Unset it."
        )


def build_auth(
    settings: OAuthSettings,
    admin: AdminPolicy,
    *,
    token_store=None,
    verifier=None,
    enable_cimd: bool = True,
):
    """The `GoogleProvider` to pass as `auth=` to `FastMCP`.

    `token_store` and `verifier` are for tests (an in-memory store, a fake
    upstream); the entry point passes neither.
    """
    _refuse_unsafe_environment()
    from fastmcp.server.auth.providers.google import GoogleProvider

    provider = GoogleProvider(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        base_url=settings.base_url,
        required_scopes=list(REQUIRED_SCOPES),
        allowed_client_redirect_uris=list(CLAUDE_REDIRECT_URIS),
        client_storage=token_store or build_token_store(settings),
        jwt_signing_key=settings.jwt_signing_key,
        require_authorization_consent=True,
        enable_cimd=enable_cimd,
    )
    # GoogleProvider builds its own verifier and offers no hook for another.
    # Both attributes name the same object in FastMCP 4.1.5 (checked by a test).
    provider._token_validator = provider.token_verifier = verifier or build_verifier(
        settings, admin
    )
    return provider


def http_protection_options(settings: OAuthSettings) -> dict:
    """Keyword arguments for `FastMCP.http_app()` (08, N5): Host and Origin
    validation on, the only allowed host is the host of `BASE_URL`."""
    return {"host_origin_protection": True, "allowed_hosts": [settings.host]}


__all__ = [
    "CHAT_SCOPES",
    "CLAUDE_REDIRECT_URIS",
    "OAuthConfigError",
    "OAuthSettings",
    "REQUIRED_SCOPES",
    "build_auth",
    "build_token_store",
    "bound_ttl",
    "build_verifier",
    "encrypt_store",
    "http_protection_options",
    "parse_base_url",
    "parse_fernet_keys",
]
