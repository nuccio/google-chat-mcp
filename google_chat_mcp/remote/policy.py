"""
The admin policy and `DEPLOY_MODE`, shared by `gcm-settings` and `gcm-mcp`.

Read from the environment (docs/remote/04, "Admin policy"):

- `ALLOWED_DOMAINS`: required, comma-separated domains of the one organisation.
- `DEPLOY_MODE`: `production` (default, and the fallback for any unknown value)
  or `development` (#86).
- `POLICY_ALLOW_WRITE`, `POLICY_ALLOW_UNATTENDED`: `true` or `false`, default
  `true`. `false` disables the option for everyone.
- `POLICY_SPACE_ALLOWLIST`: optional, comma-separated space IDs. If set, only
  these spaces are granted. Empty means not set.

There is no variable for `Policy.allow_read`: the design has no way for the
administrator to forbid reading (docs/remote/07, "to verify" item 10). The flag
stays `True` when the policy is read from the environment.

An unparsable value raises `PolicyConfigError`; callers let it stop the service
at start-up. Fail closed: nothing falls back to "allow" except where documented.

Needs no third-party package. Remote code only: local mode never imports this
module (NFR-7).
"""

from __future__ import annotations

import enum
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from ..permissions import Permissions
from ..schema import SPACE_ID_RE, space_id

log = logging.getLogger(__name__)

# A DNS name: dot-separated labels, at least two, letters/digits/hyphen.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
)


class PolicyConfigError(ValueError):
    """A policy variable is missing or cannot be parsed. Stops the service."""


class DeployMode(str, enum.Enum):
    PRODUCTION = "production"
    DEVELOPMENT = "development"


@dataclass(frozen=True)
class Policy:
    """What the domain administrator allows. A False flag disables the option.

    `space_allowlist` holds space IDs; None means every space is allowed.
    """

    allow_read: bool = True
    allow_write: bool = True
    allow_unattended: bool = True
    space_allowlist: frozenset[str] | None = None

    def allows(self, p: Permissions) -> bool:
        return (
            (self.allow_read or not p.read)
            and (self.allow_write or not p.write)
            and (self.allow_unattended or not p.unattended)
        )

    def allows_space(self, name: str) -> bool:
        """True if the space (`spaces/ID`) may be granted at all."""
        if self.space_allowlist is None:
            return True
        try:
            return space_id(name) in self.space_allowlist
        except ValueError:
            return False

    def allows_entry(self, name: str, p: Permissions) -> bool:
        return self.allows_space(name) and self.allows(p)


@dataclass(frozen=True)
class Effective:
    """Stored entries ∩ policy. A suspended entry is stored but not granted:
    it is reported as suspended, never as missing, and comes back by itself
    when the policy is loosened."""

    granted: Mapping[str, Permissions]
    suspended: Mapping[str, Permissions]


@dataclass(frozen=True)
class AdminPolicy:
    """The whole deployment configuration: mode, domains and the policy."""

    mode: DeployMode
    allowed_domains: frozenset[str]
    policy: Policy

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AdminPolicy:
        """Read and validate the configuration. Raises `PolicyConfigError`.
        Logs a warning when the mode is `development`."""
        env = os.environ if environ is None else environ
        mode = parse_deploy_mode(env.get("DEPLOY_MODE"))
        domains = parse_domains(env.get("ALLOWED_DOMAINS"))
        policy = Policy(
            allow_write=parse_bool("POLICY_ALLOW_WRITE", env.get("POLICY_ALLOW_WRITE")),
            allow_unattended=parse_bool(
                "POLICY_ALLOW_UNATTENDED", env.get("POLICY_ALLOW_UNATTENDED")
            ),
            space_allowlist=parse_space_allowlist(env.get("POLICY_SPACE_ALLOWLIST")),
        )
        admin = cls(mode=mode, allowed_domains=domains, policy=policy)
        if mode is DeployMode.DEVELOPMENT:
            log.warning(
                "DEPLOY_MODE=development: accounts without an `hd` claim are accepted. "
                "Never use this mode in production."
            )
        return admin

    @property
    def development(self) -> bool:
        return self.mode is DeployMode.DEVELOPMENT

    def domain_allowed(self, hd: str | None) -> bool:
        """The domain check on the ID token's `hd` claim (both services).

        An `hd` must be in `ALLOWED_DOMAINS`: exact, case-insensitive, no
        subdomains. No `hd` is refused in production and accepted in
        development, where Google's list of test users is the barrier.
        """
        if hd is None or not isinstance(hd, str) or not hd.strip():
            return self.development
        return hd.strip().lower() in self.allowed_domains

    def effective(self, stored: Mapping[str, Permissions]) -> Effective:
        """Intersect stored entries (by space name) with the policy."""
        granted: dict[str, Permissions] = {}
        suspended: dict[str, Permissions] = {}
        for name, p in stored.items():
            (granted if self.policy.allows_entry(name, p) else suspended)[name] = p
        return Effective(granted=granted, suspended=suspended)


def parse_deploy_mode(raw: str | None) -> DeployMode:
    """`development` only for exactly that word (case-insensitive); anything
    else, including a missing value, is `production`."""
    if raw is not None and raw.strip().lower() == DeployMode.DEVELOPMENT.value:
        return DeployMode.DEVELOPMENT
    if raw is not None and raw.strip().lower() not in ("", DeployMode.PRODUCTION.value):
        log.warning("Unknown DEPLOY_MODE value: using 'production'")
    return DeployMode.PRODUCTION


def parse_domains(raw: str | None) -> frozenset[str]:
    if raw is None or not raw.strip():
        raise PolicyConfigError("ALLOWED_DOMAINS is required (at least one domain)")
    domains = set()
    for part in raw.split(","):
        d = part.strip().lower()
        if not _DOMAIN_RE.fullmatch(d):
            raise PolicyConfigError(f"ALLOWED_DOMAINS: not a domain name: {part.strip()!r}")
        domains.add(d)
    return frozenset(domains)


def parse_bool(var: str, raw: str | None, default: bool = True) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise PolicyConfigError(f"{var}: expected 'true' or 'false', got {raw!r}")


def parse_space_allowlist(raw: str | None) -> frozenset[str] | None:
    if raw is None or not raw.strip():
        return None
    ids = set()
    for part in raw.split(","):
        sid = part.strip()
        if not SPACE_ID_RE.fullmatch(sid):
            raise PolicyConfigError(f"POLICY_SPACE_ALLOWLIST: not a space ID: {sid!r}")
        ids.add(sid)
    return frozenset(ids)


__all__ = [
    "AdminPolicy",
    "DeployMode",
    "Effective",
    "Policy",
    "PolicyConfigError",
    "parse_bool",
    "parse_deploy_mode",
    "parse_domains",
    "parse_space_allowlist",
]
