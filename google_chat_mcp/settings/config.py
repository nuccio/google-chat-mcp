"""
Configuration of the production settings service, from the environment.

Cloud Run sets these from Terraform; secrets come from Secret Manager
through Cloud Run's own mechanism (a secret exposed as an environment
variable or as a mounted file), so the service needs no Secret Manager
client of its own.

| Variable | Meaning |
|---|---|
| `SETTINGS_BASE_URL` | The service's public address, `https://gcm-settings-….run.app`. Required. Plain `http` only for localhost. |
| `GOOGLE_OAUTH_CLIENT_ID` | Client ID of the settings OAuth client. Required. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Its secret (a Secret Manager secret exposed as a variable)... |
| `GOOGLE_OAUTH_CLIENT_SECRET_FILE` | ...or the path of a file holding it (a mounted secret). |
| `PORT` | Port to listen on, set by Cloud Run (default 8080). |
| `HOST` | Address to listen on (default `0.0.0.0`). |

`ALLOWED_DOMAINS`, `DEPLOY_MODE` and `POLICY_*` are read by
`remote.policy.AdminPolicy.from_env`; the Firestore client reads
`GOOGLE_CLOUD_PROJECT` and `FIRESTORE_DATABASE` (`remote.firestore`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class ConfigError(ValueError):
    """The configuration is missing or invalid. Messages never hold a secret."""


@dataclass(frozen=True)
class SettingsConfig:
    base_url: str  # no trailing slash
    client_id: str
    client_secret: str = field(repr=False)
    host: str = "0.0.0.0"
    port: int = 8080

    @property
    def origin(self) -> str:
        parts = urlsplit(self.base_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def secure(self) -> bool:
        return urlsplit(self.base_url).scheme == "https"

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url}/settings/callback"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SettingsConfig:
        env = os.environ if environ is None else environ
        base_url = _base_url(env.get("SETTINGS_BASE_URL"))
        client_id = (env.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
        if not client_id:
            raise ConfigError("GOOGLE_OAUTH_CLIENT_ID is required")
        client_secret = _client_secret(env)
        port_raw = (env.get("PORT") or "8080").strip()
        try:
            port = int(port_raw)
        except ValueError:
            raise ConfigError(f"PORT: not a number: {port_raw!r}") from None
        if not 0 < port < 65536:
            raise ConfigError(f"PORT: out of range: {port}")
        return cls(
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
            host=(env.get("HOST") or "0.0.0.0").strip(),
            port=port,
        )


def _base_url(raw: str | None) -> str:
    if not raw or not raw.strip():
        raise ConfigError("SETTINGS_BASE_URL is required (https://gcm-settings-….run.app)")
    url = raw.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("SETTINGS_BASE_URL: not an http(s) address")
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        raise ConfigError("SETTINGS_BASE_URL: expected only scheme, host and port")
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        raise ConfigError("SETTINGS_BASE_URL: plain http is only allowed for localhost")
    return url


def _client_secret(env: Mapping[str, str]) -> str:
    secret = env.get("GOOGLE_OAUTH_CLIENT_SECRET")
    path = env.get("GOOGLE_OAUTH_CLIENT_SECRET_FILE")
    if secret and path:
        raise ConfigError(
            "Set only one of GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_CLIENT_SECRET_FILE"
        )
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                secret = f.read()
        except OSError as e:
            raise ConfigError(
                f"GOOGLE_OAUTH_CLIENT_SECRET_FILE: cannot read the file ({e.strerror})"
            ) from None
    secret = (secret or "").strip()
    if not secret:
        raise ConfigError(
            "GOOGLE_OAUTH_CLIENT_SECRET (or GOOGLE_OAUTH_CLIENT_SECRET_FILE) is required"
        )
    return secret


# Public names for the `check` command (remote/check.py), which validates each variable on its own.
parse_base_url = _base_url
read_client_secret = _client_secret
