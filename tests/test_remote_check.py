"""The `check` command (remote/check.py, issue #71) with fakes: no network, no GCP.

The real project is covered by tests/test_gcp.py (marker `gcp`).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

# The `remote` extra only: the local-only CI job skips this module.
pytest.importorskip("google.cloud.firestore")
pytest.importorskip("cryptography")

from cryptography.fernet import Fernet

from google_chat_mcp.remote import check as c
from google_chat_mcp.remote.check import Denied, GcpError, HttpError, HttpResponse, NotFound, SecretAccess, Status

ROOT = Path(__file__).resolve().parent.parent
CLIENT_ID = "123-abc.apps.googleusercontent.com"
SECRET_VALUE = "super-secret-client-value"
FERNET = Fernet.generate_key().decode()
JWT = "j" * 40

MCP_ENV = {
    "BASE_URL": "https://gcm-mcp-1.europe-west1.run.app",
    "SETTINGS_BASE_URL": "https://gcm-settings-1.europe-west1.run.app",
    "GOOGLE_CLIENT_ID": CLIENT_ID,
    "GOOGLE_CLIENT_SECRET": SECRET_VALUE,
    "OAUTH_JWT_SIGNING_KEY": JWT,
    "OAUTH_FERNET_KEYS": FERNET,
    "ALLOWED_DOMAINS": "example.com",
    "GOOGLE_CLOUD_PROJECT": "proj",
}
SETTINGS_ENV = {
    "SETTINGS_BASE_URL": "https://gcm-settings-1.europe-west1.run.app",
    "GOOGLE_OAUTH_CLIENT_ID": CLIENT_ID,
    "GOOGLE_OAUTH_CLIENT_SECRET": SECRET_VALUE,
    "ALLOWED_DOMAINS": "example.com",
    "GOOGLE_CLOUD_PROJECT": "proj",
}


class FakeHttp:
    """Answers by URL and, for the authorization endpoint, by redirect URI."""

    def __init__(self, *, registered=(), client_known=True, secret_ok=True, public=None, down=False):
        self.registered = set(registered)
        self.client_known = client_known
        self.secret_ok = secret_ok
        self.public = public  # HttpResponse for the service's own URL
        self.down = down
        self.posted: list[dict] = []

    def get(self, url, *, params=None):
        if self.down:
            raise HttpError("ConnectionError")
        if url == c.AUTH_ENDPOINT:
            if not self.client_known:
                return HttpResponse(401, "Error 401: invalid_client")
            if params["redirect_uri"] in self.registered:
                return HttpResponse(200, "<html>Sign in</html>")
            return HttpResponse(400, "Error 400: redirect_uri_mismatch")
        return self.public or HttpResponse(404)

    def post(self, url, *, data):
        if self.down:
            raise HttpError("ConnectionError")
        self.posted.append(dict(data))
        if self.secret_ok:
            return HttpResponse(400, json.dumps({"error": "invalid_grant"}))
        return HttpResponse(401, json.dumps({"error": "invalid_client"}))


class FakeGcp:
    def __init__(self, *, creds_error=None, firestore=None, ttl=None, secrets=None, project="proj"):
        self.project = project
        self.creds_error = creds_error
        self.firestore = firestore  # exception to raise, or None
        self.ttl = ttl if ttl is not None else {}
        self.secrets = secrets if secrets is not None else {}
        self.asked_secrets: list[str] = []

    def credentials(self):
        if self.creds_error:
            raise GcpError(self.creds_error)

    def firestore_read(self):
        if self.firestore:
            raise self.firestore

    def ttl_state(self, collection, field):
        value = self.ttl.get((collection, field), "ACTIVE")
        if isinstance(value, Exception):
            raise value
        return value

    def secret_access(self, secret_id):
        self.asked_secrets.append(secret_id)
        value = self.secrets.get(secret_id, SecretAccess.ALLOWED)
        if isinstance(value, Exception):
            raise value
        return value


def healthy_http(service):
    env = MCP_ENV if service == c.MCP else SETTINGS_ENV
    base = env["BASE_URL" if service == c.MCP else "SETTINGS_BASE_URL"]
    path = "/auth/callback" if service == c.MCP else "/settings/callback"
    public = (
        HttpResponse(401, "", {"WWW-Authenticate": 'Bearer resource_metadata="x"'})
        if service == c.MCP
        else HttpResponse(302)
    )
    return FakeHttp(registered={base + path}, public=public)


def healthy_gcp(service):
    secrets = {n: SecretAccess.DENIED for n in c.FOREIGN_SECRETS[service]}
    return FakeGcp(secrets=secrets)


def by_id(results):
    return {r.id: r for r in results}


@pytest.mark.parametrize("service,env", [(c.MCP, MCP_ENV), (c.SETTINGS, SETTINGS_ENV)])
def test_a_configured_project_passes_everything_that_can_be_checked(service, env):
    results = c.run_checks(service, env, healthy_gcp(service), healthy_http(service))
    assert [r for r in results if r.status is Status.FAIL] == []
    assert c.exit_code(results) == 0
    got = by_id(results)
    assert got["consent.screen"].status is Status.NOT_CHECKABLE
    for id in ("oauth.redirect_uri", "oauth.client_secret", "cloud_run.public", "firestore.access"):
        assert got[id].status is Status.OK, got[id]
    assert all(r.status is Status.OK for r in results if r.id != "consent.screen")


def test_unconfigured_project_fails_cleanly_and_without_network():
    http = FakeHttp(down=True)
    results = c.run_checks(
        c.MCP, {}, FakeGcp(creds_error="no Application Default Credentials"), http
    )
    got = by_id(results)
    assert c.exit_code(results) == 1
    assert got["gcp.credentials"].status is Status.FAIL
    assert got["config.allowed_domains"].status is Status.FAIL
    assert got["config.base_url"].status is Status.FAIL
    # what depends on a missing thing is "not checkable", not a traceback
    assert got["oauth.redirect_uri"].status is Status.NOT_CHECKABLE
    assert got["firestore.access"].status is Status.NOT_CHECKABLE
    assert http.posted == []
    # every failing or unknown line says what to do by hand
    assert all(r.fix for r in results if r.status is not Status.OK)


def test_no_secret_value_reaches_the_output():
    bad = dict(MCP_ENV, OAUTH_FERNET_KEYS="not-a-key", OAUTH_JWT_SIGNING_KEY="short-" + SECRET_VALUE[:3])
    http = FakeHttp(secret_ok=False, registered={MCP_ENV["BASE_URL"] + "/auth/callback"})
    results = c.run_checks(c.MCP, bad, healthy_gcp(c.MCP), http)
    text = "\n".join(r.line() for r in results)
    for secret in (SECRET_VALUE, "not-a-key", bad["OAUTH_JWT_SIGNING_KEY"]):
        assert secret not in text
    assert by_id(results)["config.fernet_keys"].status is Status.FAIL
    assert by_id(results)["config.jwt_signing_key"].status is Status.FAIL


def test_secret_values_read_from_secret_manager_are_never_requested_by_the_check_logic():
    # The Gcp interface returns only an enum: there is no way to get a value.
    assert {m.name for m in SecretAccess} == {"ALLOWED", "DENIED", "MISSING"}


# --- configuration ------------------------------------------------------


@pytest.mark.parametrize(
    "var,value,check",
    [
        ("DEPLOY_MODE", "dev", "config.deploy_mode"),
        ("ALLOWED_DOMAINS", "not a domain", "config.allowed_domains"),
        ("POLICY_ALLOW_WRITE", "maybe", "config.policy"),
        ("BASE_URL", "http://example.com", "config.base_url"),
        ("BASE_URL", "https://x.run.app/path", "config.base_url"),
        ("SETTINGS_BASE_URL", "", "config.settings_base_url"),
        ("GOOGLE_CLIENT_ID", "plain-id", "config.client_id"),
        ("GOOGLE_CLIENT_SECRET", "", "config.client_secret"),
    ],
)
def test_bad_mcp_configuration_is_reported_per_variable(var, value, check):
    results, _ = c.check_config(c.MCP, dict(MCP_ENV, **{var: value}))
    assert by_id(results)[check].status is Status.FAIL
    assert [r.id for r in results if r.status is Status.FAIL] == [check]


def test_development_mode_is_ok_and_allows_a_localhost_base_url():
    env = dict(MCP_ENV, DEPLOY_MODE="development", BASE_URL="http://localhost:8080")
    results, parsed = c.check_config(c.MCP, env)
    assert parsed.development
    assert not [r for r in results if r.status is Status.FAIL]


def test_settings_secret_can_come_from_a_file(tmp_path):
    f = tmp_path / "secret"
    f.write_text(SECRET_VALUE + "\n")
    env = {k: v for k, v in SETTINGS_ENV.items() if k != "GOOGLE_OAUTH_CLIENT_SECRET"}
    env["GOOGLE_OAUTH_CLIENT_SECRET_FILE"] = str(f)
    results, parsed = c.check_config(c.SETTINGS, env)
    assert not [r for r in results if r.status is Status.FAIL]
    assert parsed.client_secret == SECRET_VALUE
    assert SECRET_VALUE not in "\n".join(r.line() for r in results)


def test_redirect_uris_follow_the_docs():
    _, mcp = c.check_config(c.MCP, MCP_ENV)
    _, settings = c.check_config(c.SETTINGS, SETTINGS_ENV)
    assert mcp.redirect_uri == MCP_ENV["BASE_URL"] + "/auth/callback"
    assert settings.redirect_uri == SETTINGS_ENV["SETTINGS_BASE_URL"] + "/settings/callback"


# --- Google's endpoints ---------------------------------------------------


def parsed_for(service=c.MCP):
    env = MCP_ENV if service == c.MCP else SETTINGS_ENV
    return c.check_config(service, env)[1]


def test_unregistered_redirect_uri_fails():
    r = c.check_redirect_uri(parsed_for(), FakeHttp(registered=set()))
    assert r.status is Status.FAIL and "not registered" in r.message
    assert r.fix == c.DOC_CLIENTS


def test_unknown_client_id_fails():
    assert c.check_redirect_uri(parsed_for(), FakeHttp(client_known=False)).status is Status.FAIL


def test_a_probe_that_accepts_everything_is_not_trusted():
    class AcceptAll(FakeHttp):
        def get(self, url, *, params=None):
            return HttpResponse(200, "Sign in")

    r = c.check_redirect_uri(parsed_for(), AcceptAll())
    assert r.status is Status.NOT_CHECKABLE and "negative control" in r.message


def test_google_unreachable_is_not_checkable_not_a_crash():
    assert c.check_redirect_uri(parsed_for(), FakeHttp(down=True)).status is Status.NOT_CHECKABLE
    assert c.check_client_secret(parsed_for(), FakeHttp(down=True)).status is Status.NOT_CHECKABLE


def test_client_secret_accepted_and_refused():
    http = FakeHttp()
    assert c.check_client_secret(parsed_for(), http).status is Status.OK
    sent = http.posted[0]
    assert sent["code"] == "gcm-check-not-a-real-code"  # a made-up code, never a real grant
    assert c.check_client_secret(parsed_for(), FakeHttp(secret_ok=False)).status is Status.FAIL


def test_unexpected_token_answer_is_not_checkable():
    class Odd(FakeHttp):
        def post(self, url, *, data):
            return HttpResponse(500, "<html>")

    assert c.check_client_secret(parsed_for(), Odd()).status is Status.NOT_CHECKABLE


# --- public access ----------------------------------------------------------


@pytest.mark.parametrize(
    "service,response,status",
    [
        (c.MCP, HttpResponse(401, "", {"www-authenticate": "Bearer"}), Status.OK),
        (c.MCP, HttpResponse(403, "Forbidden"), Status.FAIL),  # Invoker IAM check still on
        (c.MCP, HttpResponse(200, "{}"), Status.FAIL),  # unauthenticated access
        (c.MCP, HttpResponse(401), Status.NOT_CHECKABLE),  # no WWW-Authenticate
        (c.SETTINGS, HttpResponse(302), Status.OK),
        (c.SETTINGS, HttpResponse(403), Status.FAIL),
        (c.SETTINGS, HttpResponse(500), Status.NOT_CHECKABLE),
    ],
)
def test_public_invoker_setting(service, response, status):
    assert c.check_public(service, parsed_for(service), FakeHttp(public=response)).status is status


def test_unreachable_service_fails():
    assert c.check_public(c.MCP, parsed_for(), FakeHttp(down=True)).status is Status.FAIL


# --- Google Cloud -----------------------------------------------------------


def test_firestore_errors_map_to_fail_lines():
    for exc in (Denied("x"), NotFound("x"), GcpError("Unavailable")):
        results = c.check_gcp(c.MCP, parsed_for(), FakeGcp(firestore=exc))
        assert by_id(results)["firestore.access"].status is Status.FAIL


def test_ttl_states():
    ttl = {
        ("mcp-oauth-transactions", "expires_at"): None,
        ("mcp-refresh-tokens", "expires_at"): "NEEDS_REPAIR",
        ("mcp-jti-mappings", "expires_at"): Denied("x"),
        ("mcp-upstream-tokens", "expires_at"): "CREATING",
    }
    got = by_id(c.check_gcp(c.MCP, parsed_for(), FakeGcp(ttl=ttl)))
    assert got["firestore.ttl.mcp-oauth-transactions"].status is Status.FAIL
    assert got["firestore.ttl.mcp-refresh-tokens"].status is Status.FAIL
    assert got["firestore.ttl.mcp-jti-mappings"].status is Status.NOT_CHECKABLE
    assert got["firestore.ttl.mcp-upstream-tokens"].status is Status.OK
    assert got["firestore.ttl.mcp-authorization-codes"].status is Status.OK


@pytest.mark.parametrize(
    "access,status",
    [(SecretAccess.ALLOWED, Status.OK), (SecretAccess.DENIED, Status.FAIL), (SecretAccess.MISSING, Status.FAIL)],
)
def test_own_secrets(access, status):
    got = by_id(c.check_gcp(c.MCP, parsed_for(), FakeGcp(secrets={c.SECRET_MCP_FERNET: access})))
    assert got[f"secret.{c.SECRET_MCP_FERNET}"].status is status


def test_settings_must_not_read_the_connectors_secrets():
    secrets = {n: SecretAccess.DENIED for n in c.FOREIGN_SECRETS[c.SETTINGS]}
    secrets[c.SECRET_MCP_FERNET] = SecretAccess.ALLOWED
    secrets[c.SECRET_MCP_JWT] = SecretAccess.MISSING
    got = by_id(c.check_gcp(c.SETTINGS, parsed_for(c.SETTINGS), FakeGcp(secrets=secrets)))
    assert got[f"secret.{c.SECRET_MCP_FERNET}"].status is Status.FAIL
    assert got[f"secret.{c.SECRET_MCP_CLIENT}"].status is Status.OK
    assert got[f"secret.{c.SECRET_MCP_JWT}"].status is Status.NOT_CHECKABLE
    assert got[f"secret.{c.SECRET_SETTINGS_CLIENT}"].status is Status.OK


def test_mcp_never_asks_for_the_settings_secret():
    gcp = healthy_gcp(c.MCP)
    c.check_gcp(c.MCP, parsed_for(), gcp)
    assert c.SECRET_SETTINGS_CLIENT not in gcp.asked_secrets


def test_no_project_is_a_failure():
    got = by_id(c.check_gcp(c.MCP, parsed_for(), FakeGcp(project=None)))
    assert got["gcp.credentials"].status is Status.FAIL


# --- output, exit code, entry points --------------------------------------


def test_line_format_and_exit_code():
    ok_line = c.ok("a.b", "fine").line()
    assert ok_line.startswith("ok ") and "->" not in ok_line
    failed = c.fail("a.c", "broken", "docs/x.md#y")
    assert failed.line().startswith("FAIL") and failed.line().endswith("-> docs/x.md#y")
    assert "\n" not in failed.line()
    assert c.exit_code([c.ok("a", "")]) == 0
    assert c.exit_code([c.unknown("a", "", "d")]) == 0
    assert c.exit_code([c.unknown("a", "", "d"), failed]) == 1
    assert c.summary([c.ok("a", ""), failed]) == "1 ok, 1 failed, 0 not checkable"


@pytest.mark.parametrize("module", ["google_chat_mcp.remote", "google_chat_mcp.settings"])
def test_cli_check_runs_and_fails_without_configuration(module, tmp_path):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "GCE_METADATA_HOST": "127.0.0.1:1",  # no metadata server to wait for
        "GOOGLE_APPLICATION_CREDENTIALS": "",
    }
    done = subprocess.run(
        [sys.executable, "-m", module, "check"],
        env=env, capture_output=True, text=True, timeout=60, cwd=ROOT,
    )
    assert done.returncode == 1
    assert "FAIL" in done.stdout and "gcp.credentials" in done.stdout
    assert "Traceback" not in done.stdout + done.stderr


def test_unknown_argument_is_still_refused():
    done = subprocess.run(
        [sys.executable, "-m", "google_chat_mcp.remote", "serve"],
        capture_output=True, text=True, timeout=60, cwd=ROOT,
    )
    assert done.returncode != 0 and "check" in done.stderr


# --- the constants follow the repository -----------------------------------


def _infra_main():
    return (ROOT / "infra/modules/gcm-remote/main.tf").read_text()


def test_ttl_tables_match_terraform():
    tf = _infra_main()
    token_store = re.search(r"token_store_collections = \[(.*?)\]", tf, re.S).group(1)
    assert set(re.findall(r'"([^"]+)"', token_store)) == {col for col, _ in c.TTL_MCP}
    pairs = set(re.findall(r'collection = "([^"]+)"\s+field\s+= "([^"]+)"', tf))
    assert pairs >= set(c.TTL_SETTINGS)
    assert {f for _, f in c.TTL_MCP} == {"expires_at"}


def test_secret_names_match_terraform():
    tf = _infra_main()
    block = re.search(r"secrets = \{(.*?)\n  \}", tf, re.S).group(1)
    names = set(re.findall(r'"(gcm-[a-z-]+)"\s+=', block))
    assert names == {*c.OWN_SECRETS[c.MCP], *c.OWN_SECRETS[c.SETTINGS]}


def _slug(heading: str) -> str:
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def test_every_doc_reference_points_at_a_real_heading():
    refs = {
        v for k, v in vars(c).items()
        if k.startswith("DOC_") and isinstance(v, str)
    }
    assert refs
    for ref in refs:
        path, anchor = ref.split("#")
        text = (ROOT / path).read_text()
        slugs = {_slug(m.group(1)) for m in re.finditer(r"^#+ (.+)$", text, re.M)}
        assert anchor in slugs, ref


def test_check_modules_are_not_imported_by_local_mode():
    for path in (ROOT / "google_chat_mcp").glob("*.py"):
        if path.name in ("__main__.py",):
            continue
        text = path.read_text()
        assert "remote.check" not in text and "remote import check" not in text, path
