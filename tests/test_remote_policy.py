"""remote.policy: the admin policy, DEPLOY_MODE and the domain check."""

import logging

import pytest

from google_chat_mcp.permissions import Permissions
from google_chat_mcp.remote.policy import (
    AdminPolicy,
    DeployMode,
    Policy,
    PolicyConfigError,
)
from tests.fixtures import users as ds

R = Permissions(read=True)
W = Permissions(write=True)
RW = Permissions(read=True, write=True)
WU = Permissions(write=True, unattended=True)

BASE = {"ALLOWED_DOMAINS": "example.com"}


def load(**extra):
    return AdminPolicy.from_env({**BASE, **extra})


# --- parsing and defaults ---------------------------------------------------


def test_defaults():
    a = load()
    assert a.mode is DeployMode.PRODUCTION
    assert a.allowed_domains == {"example.com"}
    assert a.policy == Policy()
    assert a.policy.allow_read and a.policy.allow_write and a.policy.allow_unattended
    assert a.policy.space_allowlist is None


def test_values_are_read_and_normalised():
    a = AdminPolicy.from_env(
        {
            "ALLOWED_DOMAINS": " Example.COM, example.it ",
            "POLICY_ALLOW_WRITE": "FALSE",
            "POLICY_ALLOW_UNATTENDED": " true ",
            "POLICY_SPACE_ALLOWLIST": "AAABBBCCC, DDD-EEE_1",
        }
    )
    assert a.allowed_domains == {"example.com", "example.it"}
    assert a.policy.allow_write is False
    assert a.policy.allow_unattended is True
    assert a.policy.allow_read is True  # no variable: reading is never disabled
    assert a.policy.space_allowlist == {"AAABBBCCC", "DDD-EEE_1"}


def test_reads_the_process_environment(monkeypatch):
    monkeypatch.setenv("ALLOWED_DOMAINS", "example.com")
    monkeypatch.setenv("POLICY_ALLOW_UNATTENDED", "false")
    assert AdminPolicy.from_env().policy.allow_unattended is False


def test_empty_allowlist_means_not_set():
    assert load(POLICY_SPACE_ALLOWLIST="  ").policy.space_allowlist is None


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"ALLOWED_DOMAINS": ""},
        {"ALLOWED_DOMAINS": " "},
        {"ALLOWED_DOMAINS": "example.com,"},
        {"ALLOWED_DOMAINS": "*.example.com"},
        {"ALLOWED_DOMAINS": "user@example.com"},
        {"ALLOWED_DOMAINS": "localhost"},
        {"ALLOWED_DOMAINS": "example.com", "POLICY_ALLOW_WRITE": "maybe"},
        {"ALLOWED_DOMAINS": "example.com", "POLICY_ALLOW_WRITE": ""},
        {"ALLOWED_DOMAINS": "example.com", "POLICY_ALLOW_UNATTENDED": "1"},
        {"ALLOWED_DOMAINS": "example.com", "POLICY_SPACE_ALLOWLIST": "spaces/AAA"},
        {"ALLOWED_DOMAINS": "example.com", "POLICY_SPACE_ALLOWLIST": "AAA,,BBB"},
    ],
)
def test_unparsable_configuration_stops_the_service(env):
    with pytest.raises(PolicyConfigError):
        AdminPolicy.from_env(env)


# --- DEPLOY_MODE and the domain check ---------------------------------------


@pytest.mark.parametrize("raw", [None, "", "production", "PRODUCTION", "prod", "staging", "dev"])
def test_production_is_the_default_and_the_fallback(raw):
    env = {} if raw is None else {"DEPLOY_MODE": raw}
    assert load(**env).mode is DeployMode.PRODUCTION


@pytest.mark.parametrize("raw", ["development", " Development "])
def test_development_mode(raw):
    assert load(DEPLOY_MODE=raw).mode is DeployMode.DEVELOPMENT


def test_development_logs_a_start_up_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="google_chat_mcp.remote.policy"):
        load(DEPLOY_MODE="development")
    assert "DEPLOY_MODE=development" in caplog.text


def test_production_logs_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="google_chat_mcp.remote.policy"):
        load()
    assert "DEPLOY_MODE=development" not in caplog.text


@pytest.mark.parametrize("hd", [None, "", "  "])
def test_production_refuses_an_account_without_hd(hd):
    assert load().domain_allowed(hd) is False


@pytest.mark.parametrize("hd", [None, "", "  "])
def test_development_accepts_an_account_without_hd(hd):
    assert load(DEPLOY_MODE="development").domain_allowed(hd) is True


@pytest.mark.parametrize("mode", ["production", "development", "bogus"])
def test_a_foreign_hd_is_refused_in_every_mode(mode):
    a = load(DEPLOY_MODE=mode)
    assert a.domain_allowed("other.org") is False
    assert a.domain_allowed("eu.example.com") is False  # no subdomains
    assert a.domain_allowed("example.com.evil.org") is False
    assert a.domain_allowed("xexample.com") is False


@pytest.mark.parametrize("mode", ["production", "development"])
def test_an_allowed_hd_is_accepted_case_insensitively(mode):
    a = AdminPolicy.from_env({"ALLOWED_DOMAINS": "example.com,Example.IT", "DEPLOY_MODE": mode})
    assert a.domain_allowed("example.com")
    assert a.domain_allowed("EXAMPLE.com")
    assert a.domain_allowed("example.it")


# --- Policy and the intersection --------------------------------------------


def test_policy_allows():
    assert Policy().allows(WU)
    assert not Policy(allow_unattended=False).allows(WU)
    assert Policy(allow_unattended=False).allows(W)
    assert not Policy(allow_write=False).allows(RW)
    assert Policy(allow_write=False).allows(R)
    assert not Policy(allow_read=False).allows(R)


def test_space_allowlist():
    p = Policy(space_allowlist=frozenset({"AAA"}))
    assert p.allows_space("spaces/AAA")
    assert not p.allows_space("spaces/BBB")
    assert not p.allows_space("AAA")  # not a resource name
    assert Policy().allows_space("spaces/ANY")


def test_effective_reports_suspended_not_missing():
    a = load(POLICY_ALLOW_UNATTENDED="false", POLICY_SPACE_ALLOWLIST="AAA,BBB,CCC")
    stored = {
        "spaces/AAA": R,
        "spaces/BBB": WU,  # unattended forbidden
        "spaces/CCC": RW,
        "spaces/OUT": R,  # outside the allowlist
    }
    eff = a.effective(stored)
    assert dict(eff.granted) == {"spaces/AAA": R, "spaces/CCC": RW}
    assert dict(eff.suspended) == {"spaces/BBB": WU, "spaces/OUT": R}
    assert set(eff.granted) | set(eff.suspended) == set(stored)


def test_loosening_the_policy_restores_the_entry():
    stored = {"spaces/BBB": WU}
    assert load(POLICY_ALLOW_UNATTENDED="false").effective(stored).granted == {}
    assert dict(load().effective(stored).granted) == stored


def test_effective_of_nothing_is_empty():
    eff = load().effective({})
    assert not eff.granted and not eff.suspended


# --- the fixed dataset -------------------------------------------------------


@pytest.mark.parametrize("user", [ds.ALICE, ds.CAROL], ids=["alice", "carol"])
def test_dataset_policies(user):
    open_ = AdminPolicy(DeployMode.PRODUCTION, frozenset({"example.com"}), Policy(**ds.POLICY_OPEN))
    restricted = AdminPolicy(
        DeployMode.PRODUCTION, frozenset({"example.com"}), Policy(**ds.POLICY_RESTRICTED)
    )
    assert dict(open_.effective(user.granted).granted) == user.granted
    assert not open_.effective(user.granted).suspended
    eff = restricted.effective(user.granted)
    assert set(eff.suspended) == user.suspended_by_policy
    assert set(eff.granted) == set(user.granted) - user.suspended_by_policy


def test_dataset_users_pass_the_production_domain_check():
    a = load()
    assert all(a.domain_allowed("example.com") for _ in ds.USERS)
