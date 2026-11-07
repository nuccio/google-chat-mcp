"""
`check`: does the GCP side of the remote connector match what the code needs?
(issue #71; docs/remote/06 "What is done only on GCP", 08 "Operator setup".)

    python -m google_chat_mcp.remote check     # gcm-mcp
    python -m google_chat_mcp.settings check   # gcm-settings

Meant to run as a Cloud Run job with the service's own service account and the
service's own environment, so each service checks only what it can see. It can
also run on a workstation with Application Default Credentials; the lines that
need the service's environment then fail, which is the point.

Read-only. It changes nothing in the project and **never prints a secret
value**: configuration lines name the variable, never its content; a secret in
Secret Manager is read only to learn whether the read is allowed, and the
answer is thrown away unseen (`GoogleGcp.secret_access`).

Output is one line per check::

    ok            config.deploy_mode  production (default)
    FAIL          oauth.redirect_uri  ...  -> docs/remote/08-operator-setup.md#step-4-...
    not-checkable consent.screen      ...  -> docs/...

The exit code is 1 when any check failed, 0 otherwise. `not-checkable` means
the check could not be done from here (no API for it, or the probe gave an
answer nobody has seen yet); it never turns into `ok` by itself, and the line
says what to do by hand. Checks that do not apply in development (the
organisation parent, the Internal screen) are in the `gcp` tests, which know the
environment.

Everything that touches the outside (Google's endpoints, Secret Manager,
Firestore) goes through the small `Http` and `Gcp` interfaces, so the logic is
tested with fakes (tests/test_remote_check.py). The real project is covered by
the `gcp` tests (tests/test_gcp.py).

Needs the `remote` extra. Local mode never imports this module (NFR-7).
"""

from __future__ import annotations

import enum
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

SETTINGS = "settings"
MCP = "mcp"

# Doc references, repo-relative with the GitHub anchor of the heading.
SETUP = "docs/remote/08-operator-setup.md"
DOC_PROJECT = f"{SETUP}#step-1--gcp-project-billing-and-region"
DOC_APIS = f"{SETUP}#step-2--enable-the-apis"
DOC_CONSENT = f"{SETUP}#step-3--consent-screen-google-auth-platform"
DOC_CLIENTS = f"{SETUP}#step-4--oauth-clients-two-web-application"
DOC_SECRETS = f"{SETUP}#step-5--secrets-secret-manager"
DOC_FIRESTORE = f"{SETUP}#step-6--firestore"
DOC_RUN_CHECK = f"{SETUP}#step-8--check-the-setup"
DOC_DEPLOY_SETTINGS = "docs/remote/10-deploy-settings.md#step-7--create-or-update-the-cloud-run-service"
DOC_DEPLOY_MCP = "docs/remote/11-deploy-gcm-mcp.md#step-4--the-cloud-run-service"
DOC_TTL_MCP = "docs/remote/11-deploy-gcm-mcp.md#step-5--firestore-ttl-policies-for-the-token-store"
DOC_DEPLOY = {SETTINGS: DOC_DEPLOY_SETTINGS, MCP: DOC_DEPLOY_MCP}

# What infra/modules/gcm-remote creates (a test keeps these in step with it).
SECRET_SETTINGS_CLIENT = "gcm-settings-client-secret"
SECRET_MCP_CLIENT = "gcm-mcp-client-secret"
SECRET_MCP_FERNET = "gcm-mcp-fernet-key"
SECRET_MCP_JWT = "gcm-mcp-jwt-signing-key"
OWN_SECRETS = {
    SETTINGS: (SECRET_SETTINGS_CLIENT,),
    MCP: (SECRET_MCP_CLIENT, SECRET_MCP_FERNET, SECRET_MCP_JWT),
}
FOREIGN_SECRETS = {SETTINGS: OWN_SECRETS[MCP], MCP: ()}

# (collection, TTL field) per service. gcm-settings owns the sessions, login
# transactions and audit records; gcm-mcp owns the token store (remote/oauth.py).
TTL_SETTINGS = (
    ("sessions", "expire_at"),
    ("login_transactions", "expire_at"),
    ("audit", "expire_at"),
)
TTL_MCP = tuple(
    (c, "expires_at")
    for c in (
        "mcp-oauth-proxy-clients",
        "mcp-oauth-transactions",
        "mcp-consent-csrf-tokens",
        "mcp-authorization-codes",
        "mcp-upstream-tokens",
        "mcp-jti-mappings",
        "mcp-refresh-tokens",
    )
)
TTL_FIELDS = {SETTINGS: TTL_SETTINGS, MCP: TTL_MCP}

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# A redirect URI that no client registers: the negative control of the probe.
CONTROL_REDIRECT_URI = "https://gcm-check.invalid/never-registered"
CLIENT_ID_SUFFIX = ".apps.googleusercontent.com"
ENV_HINT = "Run it as a Cloud Run job with the service's own service account and environment."


class Status(str, enum.Enum):
    OK = "ok"
    FAIL = "FAIL"
    NOT_CHECKABLE = "not-checkable"


@dataclass(frozen=True)
class Result:
    id: str
    status: Status
    message: str
    fix: str | None = None  # repo-relative doc reference: what to do by hand

    def line(self) -> str:
        text = f"{self.status.value:<13} {self.id:<44} {self.message}"
        if self.fix and self.status is not Status.OK:
            text += f"  -> {self.fix}"
        return text


def ok(id: str, message: str) -> Result:
    return Result(id, Status.OK, message)


def fail(id: str, message: str, fix: str) -> Result:
    return Result(id, Status.FAIL, message, fix)


def unknown(id: str, message: str, fix: str) -> Result:
    return Result(id, Status.NOT_CHECKABLE, message, fix)


# ---------------------------------------------------------------------------
# The outside world
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HttpResponse:
    status: int
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


class HttpError(Exception):
    """The request could not be made (DNS, TLS, timeout...)."""


class Http(Protocol):
    """Plain HTTP. Never follows redirects: the answer to the first request is
    what is examined."""

    def get(self, url: str, *, params: Mapping[str, str] | None = None) -> HttpResponse: ...

    def post(self, url: str, *, data: Mapping[str, str]) -> HttpResponse: ...


class GcpError(Exception):
    """A Google Cloud call failed. The message never holds a secret."""


class Denied(GcpError):
    """PERMISSION_DENIED (or UNAUTHENTICATED)."""


class NotFound(GcpError):
    """The resource does not exist."""


class SecretAccess(str, enum.Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    MISSING = "missing"  # no such secret, or no version


class Gcp(Protocol):
    @property
    def project(self) -> str | None: ...

    def credentials(self) -> None:
        """Raises `GcpError` when there are no usable credentials."""

    def firestore_read(self) -> None:
        """Reads one document that does not exist. Raises `Denied`, `NotFound`
        (no database) or `GcpError`."""

    def ttl_state(self, collection: str, field: str) -> str | None:
        """State of the TTL policy on `collection.field` (`ACTIVE`, `CREATING`,
        `NEEDS_REPAIR`) or None when there is none. Raises `Denied` or `GcpError`."""

    def secret_access(self, secret_id: str) -> SecretAccess:
        """Whether the latest version of the secret can be read. The value is
        discarded unseen. Raises `GcpError` for anything but those answers."""


class RequestsHttp:
    def __init__(self, timeout: float = 10.0) -> None:
        import requests

        self._requests = requests
        self._timeout = timeout

    def _wrap(self, response) -> HttpResponse:
        return HttpResponse(response.status_code, response.text, dict(response.headers))

    def get(self, url, *, params=None):
        try:
            return self._wrap(
                self._requests.get(url, params=params, timeout=self._timeout, allow_redirects=False)
            )
        except self._requests.RequestException as e:
            raise HttpError(type(e).__name__) from None

    def post(self, url, *, data):
        try:
            return self._wrap(
                self._requests.post(url, data=data, timeout=self._timeout, allow_redirects=False)
            )
        except self._requests.RequestException as e:
            raise HttpError(type(e).__name__) from None


class GoogleGcp:
    """The real `Gcp`, on Application Default Credentials. Nothing connects
    until a method is called."""

    SCOPE = "https://www.googleapis.com/auth/cloud-platform"

    def __init__(self, project: str | None, database: str) -> None:
        self._project = project
        self._database = database
        self._creds = None
        self._session = None

    @property
    def project(self) -> str | None:
        if self._project:
            return self._project
        try:
            self.credentials()
        except GcpError:
            return None
        return self._project

    def credentials(self) -> None:
        if self._creds is not None:
            return
        import google.auth
        from google.auth.exceptions import GoogleAuthError

        try:
            creds, adc_project = google.auth.default(scopes=[self.SCOPE])
        except GoogleAuthError as e:
            raise GcpError(f"no Application Default Credentials ({type(e).__name__})") from None
        self._creds = creds
        self._project = self._project or adc_project

    def _http(self):
        if self._session is None:
            self.credentials()
            from google.auth.transport.requests import AuthorizedSession

            self._session = AuthorizedSession(self._creds)
        return self._session

    def _get(self, url: str):
        import requests

        try:
            return self._http().get(url, timeout=15)
        except requests.RequestException as e:
            raise GcpError(f"request failed ({type(e).__name__})") from None
        except Exception as e:  # token refresh failures and the like
            raise GcpError(f"request failed ({type(e).__name__})") from None

    def firestore_read(self) -> None:
        from google.api_core import exceptions as gexc

        from .firestore import create_client

        try:
            client = create_client(self.project, self._database)
            client.collection("_check").document("probe").get()
        except gexc.PermissionDenied:
            raise Denied("PERMISSION_DENIED") from None
        except gexc.NotFound:
            raise NotFound("database not found") from None
        except gexc.GoogleAPICallError as e:
            raise GcpError(type(e).__name__) from None
        except Exception as e:
            raise GcpError(type(e).__name__) from None

    def ttl_state(self, collection: str, field: str) -> str | None:
        url = (
            f"https://firestore.googleapis.com/v1/projects/{self.project}/databases/"
            f"{self._database}/collectionGroups/{collection}/fields/{field}"
        )
        response = self._get(url)
        if response.status_code in (401, 403):
            raise Denied("PERMISSION_DENIED")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GcpError(f"HTTP {response.status_code}")
        try:
            return response.json().get("ttlConfig", {}).get("state")
        except ValueError:
            raise GcpError("unreadable answer") from None

    def secret_access(self, secret_id: str) -> SecretAccess:
        url = (
            f"https://secretmanager.googleapis.com/v1/projects/{self.project}/secrets/"
            f"{secret_id}/versions/latest:access"
        )
        response = self._get(url)
        status = response.status_code  # the body (the secret) is dropped here, unread
        if status == 200:
            return SecretAccess.ALLOWED
        if status in (401, 403):
            return SecretAccess.DENIED
        if status in (400, 404):
            return SecretAccess.MISSING
        raise GcpError(f"HTTP {status}")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class Parsed:
    """What the configuration lines managed to read, for the checks after them."""

    development: bool = False
    base_url: str | None = None  # own public origin, no trailing slash
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    redirect_uri: str | None = None
    project: str | None = None
    database: str = "(default)"


def check_config(service: str, env: Mapping[str, str]) -> tuple[list[Result], Parsed]:
    from ..settings import config as settings_config
    from .acl import AclConfigError, settings_link
    from .firestore import DEFAULT_DATABASE
    from .oauth import MIN_JWT_KEY_LENGTH, OAuthConfigError, parse_base_url, parse_fernet_keys
    from .policy import (
        DeployMode,
        PolicyConfigError,
        parse_bool,
        parse_deploy_mode,
        parse_domains,
        parse_space_allowlist,
    )

    deploy = DOC_DEPLOY[service]
    results: list[Result] = []
    parsed = Parsed(database=(env.get("FIRESTORE_DATABASE") or "").strip() or DEFAULT_DATABASE)

    # DEPLOY_MODE: the code falls back to production for any unknown word; here
    # an unknown word is an error, because it is almost always a typo.
    raw_mode = (env.get("DEPLOY_MODE") or "").strip().lower()
    mode = parse_deploy_mode(env.get("DEPLOY_MODE"))
    parsed.development = mode is DeployMode.DEVELOPMENT
    if raw_mode not in ("", "production", "development"):
        results.append(
            fail(
                "config.deploy_mode",
                "DEPLOY_MODE has an unknown value (the service would run as production)",
                deploy,
            )
        )
    elif parsed.development:
        results.append(
            ok("config.deploy_mode", "development: accounts without `hd` accepted (dev project only)")
        )
    else:
        results.append(ok("config.deploy_mode", "production"))

    def one(id: str, fix: str, parse: Callable[[], str]) -> str | None:
        try:
            message = parse()
        except (PolicyConfigError, OAuthConfigError, AclConfigError, settings_config.ConfigError) as e:
            results.append(fail(id, str(e), fix))
            return None
        results.append(ok(id, message))
        return message

    one(
        "config.allowed_domains",
        deploy,
        lambda: f"{len(parse_domains(env.get('ALLOWED_DOMAINS')))} domain(s)",
    )

    def policy() -> str:
        parse_bool("POLICY_ALLOW_WRITE", env.get("POLICY_ALLOW_WRITE"))
        parse_bool("POLICY_ALLOW_UNATTENDED", env.get("POLICY_ALLOW_UNATTENDED"))
        parse_space_allowlist(env.get("POLICY_SPACE_ALLOWLIST"))
        return "parses"

    one("config.policy", deploy, policy)

    project = (env.get("GOOGLE_CLOUD_PROJECT") or env.get("GCP_PROJECT") or "").strip()
    parsed.project = project or None
    results.append(
        ok("config.project", project)
        if project
        else Result(
            "config.project",
            Status.NOT_CHECKABLE,
            "GOOGLE_CLOUD_PROJECT is not set: the project of the credentials is used",
            deploy,
        )
    )

    if service == MCP:

        def base_url() -> str:
            raw = (env.get("BASE_URL") or "").strip()
            if not raw:
                raise OAuthConfigError("BASE_URL is required")
            return parse_base_url(raw, development=parsed.development)

        base = one("config.base_url", deploy, base_url)
        parsed.base_url = base
        parsed.redirect_uri = f"{base}/auth/callback" if base else None
        one("config.settings_base_url", deploy, lambda: settings_link(env.get("SETTINGS_BASE_URL")))
        parsed.client_id = _client_id(results, env, "GOOGLE_CLIENT_ID", DOC_CLIENTS)
        parsed.client_secret = _present(results, env, "GOOGLE_CLIENT_SECRET", DOC_SECRETS)

        def jwt() -> str:
            value = (env.get("OAUTH_JWT_SIGNING_KEY") or "").strip()
            if not value:
                raise OAuthConfigError("OAUTH_JWT_SIGNING_KEY is required")
            if len(value) < MIN_JWT_KEY_LENGTH:
                raise OAuthConfigError(
                    f"OAUTH_JWT_SIGNING_KEY must have at least {MIN_JWT_KEY_LENGTH} characters"
                )
            return "set, long enough"

        one("config.jwt_signing_key", DOC_SECRETS, jwt)

        def fernet() -> str:
            raw = (env.get("OAUTH_FERNET_KEYS") or "").strip()
            if not raw:
                raise OAuthConfigError("OAUTH_FERNET_KEYS is required")
            return f"{len(parse_fernet_keys(raw))} valid key(s)"

        one("config.fernet_keys", DOC_SECRETS, fernet)
    else:
        base = one(
            "config.settings_base_url",
            deploy,
            lambda: settings_config.parse_base_url(env.get("SETTINGS_BASE_URL")),
        )
        parsed.base_url = base
        parsed.redirect_uri = f"{base}/settings/callback" if base else None
        parsed.client_id = _client_id(results, env, "GOOGLE_OAUTH_CLIENT_ID", DOC_CLIENTS)

        def secret() -> str:
            parsed.client_secret = settings_config.read_client_secret(env)
            return "set"

        one("config.client_secret", DOC_SECRETS, secret)
    return results, parsed


def _client_id(results: list[Result], env: Mapping[str, str], var: str, fix: str) -> str | None:
    value = (env.get(var) or "").strip()
    id = "config.client_id"
    if not value:
        results.append(fail(id, f"{var} is required", fix))
        return None
    if not value.endswith(CLIENT_ID_SUFFIX):
        results.append(fail(id, f"{var} does not look like a Google client ID (…{CLIENT_ID_SUFFIX})", fix))
        return None
    results.append(ok(id, f"{var} set"))
    return value


def _present(results: list[Result], env: Mapping[str, str], var: str, fix: str) -> str | None:
    value = (env.get(var) or "").strip()
    results.append(
        ok("config.client_secret", f"{var} set")
        if value
        else fail("config.client_secret", f"{var} is required", fix)
    )
    return value or None


# ---------------------------------------------------------------------------
# Google's endpoints: client ID, redirect URI, client secret
# ---------------------------------------------------------------------------


def _classify_auth(response: HttpResponse) -> str:
    """What Google's authorization endpoint said, without signing anyone in:
    `mismatch`, `invalid_client`, `page` (the sign-in page or a redirect to
    it) or `other`. The strings are those Google puts in its error pages;
    the probe is unverified until it has run against a real project."""
    text = response.text
    if "redirect_uri_mismatch" in text:
        return "mismatch"
    if "invalid_client" in text or "deleted_client" in text:
        return "invalid_client"
    if response.status in (200, 302, 303, 307):
        return "page"
    return "other"


def check_redirect_uri(parsed: Parsed, http: Http) -> Result:
    id = "oauth.redirect_uri"
    if not (parsed.client_id and parsed.redirect_uri):
        return unknown(id, "needs a valid client ID and address (see config.* above)", DOC_CLIENTS)

    def ask(uri: str) -> str:
        params = {
            "client_id": parsed.client_id,
            "redirect_uri": uri,
            "response_type": "code",
            "scope": "openid",
            "state": "gcm-check",
        }
        return _classify_auth(http.get(AUTH_ENDPOINT, params=params))

    try:
        target = ask(parsed.redirect_uri)
        if target == "invalid_client":
            return fail(id, "Google does not know this client ID", DOC_CLIENTS)
        if target == "mismatch":
            return fail(
                id, f"{parsed.redirect_uri} is not registered on the client", DOC_CLIENTS
            )
        if target != "page":
            return unknown(id, "Google gave an answer this probe does not know", DOC_CLIENTS)
        control = ask(CONTROL_REDIRECT_URI)
    except HttpError as e:
        return unknown(id, f"cannot reach Google ({e})", DOC_CLIENTS)
    if control != "mismatch":
        return unknown(
            id,
            "accepted, but the negative control (an unregistered URI) was not refused: "
            "the probe cannot tell; compare the client's redirect URIs in the console",
            DOC_CLIENTS,
        )
    return ok(id, f"{parsed.redirect_uri} is registered (the control URI was refused)")


def check_client_secret(parsed: Parsed, http: Http) -> Result:
    id = "oauth.client_secret"
    if not (parsed.client_id and parsed.client_secret and parsed.redirect_uri):
        return unknown(id, "needs a valid client ID, secret and address (see config.* above)", DOC_CLIENTS)
    data = {
        "grant_type": "authorization_code",
        "code": "gcm-check-not-a-real-code",
        "redirect_uri": parsed.redirect_uri,
        "client_id": parsed.client_id,
        "client_secret": parsed.client_secret,
    }
    try:
        response = http.post(TOKEN_ENDPOINT, data=data)
    except HttpError as e:
        return unknown(id, f"cannot reach Google ({e})", DOC_CLIENTS)
    try:
        error = json.loads(response.text).get("error")
    except (ValueError, AttributeError):
        error = None
    if error == "invalid_grant":
        return ok(id, "Google accepts the client ID and secret (it refused only the made-up code)")
    if error == "invalid_client":
        return fail(id, "Google refuses the client ID or secret", DOC_CLIENTS)
    return unknown(id, f"Google answered HTTP {response.status} with an unexpected error", DOC_CLIENTS)


# ---------------------------------------------------------------------------
# Public reachability
# ---------------------------------------------------------------------------


def check_public(service: str, parsed: Parsed, http: Http) -> Result:
    id = "cloud_run.public"
    fix = DOC_DEPLOY[service]
    if not parsed.base_url:
        return unknown(id, "needs a valid address (see config.* above)", fix)
    path = "/mcp" if service == MCP else "/settings"
    try:
        response = http.get(f"{parsed.base_url}{path}")
    except HttpError as e:
        return fail(id, f"{parsed.base_url} is not reachable ({e})", fix)
    status = response.status
    if status == 403:
        return fail(
            id,
            "refused before reaching the application: the Invoker IAM check is still on "
            "(or the organisation policy forbids a public service)",
            fix,
        )
    if service == MCP:
        if status == 401 and response.header("www-authenticate"):
            return ok(id, "public, and an anonymous call is answered 401 with WWW-Authenticate")
        if status == 200:
            return fail(id, "/mcp answers an anonymous call with 200: authentication is not in place", fix)
        return unknown(id, f"anonymous GET /mcp answered HTTP {status}, not the expected 401", fix)
    if status in (200, 302, 303, 307):
        return ok(id, f"public: anonymous GET /settings answered HTTP {status}")
    return unknown(id, f"anonymous GET /settings answered HTTP {status}", fix)


# ---------------------------------------------------------------------------
# Google Cloud: credentials, Firestore, TTL, secrets
# ---------------------------------------------------------------------------


def check_gcp(service: str, parsed: Parsed, gcp: Gcp) -> list[Result]:
    results: list[Result] = []
    try:
        gcp.credentials()
    except GcpError as e:
        results.append(fail("gcp.credentials", f"{e}. {ENV_HINT}", DOC_RUN_CHECK))
        needs = "needs credentials (see gcp.credentials)"
        results.append(unknown("firestore.access", needs, DOC_FIRESTORE))
        for collection, fld in TTL_FIELDS[service]:
            results.append(unknown(f"firestore.ttl.{collection}", needs, DOC_FIRESTORE))
        for name in (*OWN_SECRETS[service], *FOREIGN_SECRETS[service]):
            results.append(unknown(f"secret.{name}", needs, DOC_SECRETS))
        return results
    project = gcp.project
    results.append(
        ok("gcp.credentials", f"found; project {project}")
        if project
        else fail("gcp.credentials", "credentials found but no project: set GOOGLE_CLOUD_PROJECT", DOC_PROJECT)
    )

    try:
        gcp.firestore_read()
        results.append(ok("firestore.access", f"reachable; database {parsed.database}"))
    except Denied:
        results.append(
            fail(
                "firestore.access",
                "this service account may not read the database "
                "(roles/datastore.user, with the condition on the database name)",
                DOC_FIRESTORE,
            )
        )
    except NotFound:
        results.append(fail("firestore.access", f"database {parsed.database} does not exist", DOC_FIRESTORE))
    except GcpError as e:
        results.append(fail("firestore.access", f"cannot read: {e}", DOC_FIRESTORE))

    ttl_doc = DOC_TTL_MCP if service == MCP else DOC_FIRESTORE
    for collection, fld in TTL_FIELDS[service]:
        results.append(_check_ttl(gcp, collection, fld, ttl_doc))

    for name in OWN_SECRETS[service]:
        results.append(_check_own_secret(gcp, name))
    for name in FOREIGN_SECRETS[service]:
        results.append(_check_foreign_secret(gcp, name))
    return results


def _check_ttl(gcp: Gcp, collection: str, fld: str, fix: str) -> Result:
    id = f"firestore.ttl.{collection}"
    try:
        state = gcp.ttl_state(collection, fld)
    except Denied:
        return unknown(
            id,
            "this service account may not read index settings; "
            f"run: gcloud firestore fields ttls list (look for {collection}.{fld})",
            fix,
        )
    except GcpError as e:
        return unknown(id, f"cannot read the policy: {e}", fix)
    if state in ("ACTIVE", "CREATING"):
        return ok(id, f"TTL on {fld} is {state}")
    if state is None:
        return fail(id, f"no TTL policy on {collection}.{fld}: entries never expire", fix)
    return fail(id, f"TTL policy on {collection}.{fld} is {state}", fix)


def _check_own_secret(gcp: Gcp, name: str) -> Result:
    id = f"secret.{name}"
    try:
        access = gcp.secret_access(name)
    except GcpError as e:
        return unknown(id, f"cannot tell: {e}", DOC_SECRETS)
    if access is SecretAccess.ALLOWED:
        return ok(id, "has an enabled version and this service account can read it")
    if access is SecretAccess.MISSING:
        return fail(id, "no such secret or no enabled version: add the value", DOC_SECRETS)
    return fail(id, "this service account may not read it (roles/secretmanager.secretAccessor)", DOC_SECRETS)


def _check_foreign_secret(gcp: Gcp, name: str) -> Result:
    """gcm-settings must NOT read the connector's secrets (04, 07)."""
    id = f"secret.{name}"
    try:
        access = gcp.secret_access(name)
    except GcpError as e:
        return unknown(id, f"cannot tell: {e}", DOC_SECRETS)
    if access is SecretAccess.DENIED:
        return ok(id, "denied to this service account, as intended")
    if access is SecretAccess.ALLOWED:
        return fail(
            id,
            "this service account CAN read the connector's secret: remove the binding",
            DOC_SECRETS,
        )
    return unknown(id, "no such secret, so access cannot be tested", DOC_SECRETS)


# ---------------------------------------------------------------------------
# What no API can tell
# ---------------------------------------------------------------------------


def check_consent_screen(parsed: Parsed) -> Result:
    expected = (
        "External in Testing, with the test users listed"
        if parsed.development
        else "Internal"
    )
    return unknown(
        "consent.screen",
        f"Google offers no API to read it. Expected: {expected}, with the scopes of the table. "
        "Do the checklist, then run the assisted sign-in: python -m google_chat_mcp.remote.signin",
        DOC_CONSENT,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_checks(service: str, env: Mapping[str, str], gcp: Gcp, http: Http) -> list[Result]:
    if service not in (SETTINGS, MCP):
        raise ValueError(f"unknown service {service!r}")
    results, parsed = check_config(service, env)
    results.append(check_consent_screen(parsed))
    results.append(check_redirect_uri(parsed, http))
    results.append(check_client_secret(parsed, http))
    results.append(check_public(service, parsed, http))
    results.extend(check_gcp(service, parsed, gcp))
    return results


def summary(results: list[Result]) -> str:
    counts = {s: sum(1 for r in results if r.status is s) for s in Status}
    return (
        f"{counts[Status.OK]} ok, {counts[Status.FAIL]} failed, "
        f"{counts[Status.NOT_CHECKABLE]} not checkable"
    )


def exit_code(results: list[Result]) -> int:
    return 1 if any(r.status is Status.FAIL for r in results) else 0


def main(service: str, argv: list[str], env: Mapping[str, str] | None = None) -> int:
    """Entry point of `... check`. Prints the report; returns the exit code."""
    import os

    if argv:
        raise SystemExit(f"check takes no arguments; use the environment (got {argv[0]!r})")
    try:
        import google.cloud.firestore  # noqa: F401
        import requests  # noqa: F401
        from cryptography.fernet import Fernet  # noqa: F401
    except ImportError:
        raise SystemExit(
            "The check command needs the 'remote' extra: pip install \"google-chat-mcp[remote]\""
        ) from None

    env = os.environ if env is None else env
    from .firestore import DEFAULT_DATABASE

    project = (env.get("GOOGLE_CLOUD_PROJECT") or env.get("GCP_PROJECT") or "").strip() or None
    database = (env.get("FIRESTORE_DATABASE") or "").strip() or DEFAULT_DATABASE
    results = run_checks(service, env, GoogleGcp(project, database), RequestsHttp())
    label = "gcm-mcp" if service == MCP else "gcm-settings"
    print(f"check {label}: read-only; no secret value is printed")
    for r in results:
        print(r.line())
    print(summary(results))
    return exit_code(results)


def run(service: str, argv: list[str]) -> None:
    sys.exit(main(service, argv))
