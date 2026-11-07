"""The assisted sign-in's evaluation (remote/signin.py, issue #71), without Google."""

from __future__ import annotations

import socket
import threading
import urllib.request

import pytest

pytest.importorskip("google.cloud.firestore")
pytest.importorskip("google_auth_oauthlib")

from google_chat_mcp.remote import signin as s
from google_chat_mcp.remote.check import Status

CID = "c.apps.googleusercontent.com"
CLAIMS = {"aud": CID, "email_verified": True, "hd": "example.com", "email": "a@example.com"}
SCOPES = list(s.SCOPES["mcp"])


def statuses(results):
    return {r.id: r.status for r in results}


def test_a_good_sign_in_passes_every_check():
    got = statuses(s.evaluate_success(CLAIMS, SCOPES, SCOPES, CID, ["example.com"], False))
    assert set(got.values()) == {Status.OK}


def test_each_failure_is_reported():
    claims = dict(CLAIMS, aud="other", email_verified=False, hd="evil.com")
    got = statuses(s.evaluate_success(claims, SCOPES[:-1], SCOPES, CID, ["example.com"], False))
    assert set(got.values()) == {Status.FAIL}


def test_hd_is_required_in_production_not_in_development():
    claims = {k: v for k, v in CLAIMS.items() if k != "hd"}
    prod = statuses(s.evaluate_success(claims, SCOPES, SCOPES, CID, ["example.com"], False))
    dev = statuses(s.evaluate_success(claims, SCOPES, SCOPES, CID, [], True))
    assert prod["signin.hd"] is Status.FAIL
    assert dev["signin.hd"] is Status.OK


def test_the_audience_may_be_a_list():
    claims = dict(CLAIMS, aud=[CID])
    assert statuses(s.evaluate_success(claims, SCOPES, SCOPES, CID, ["example.com"], False))["signin.audience"] is Status.OK


def test_output_does_not_contain_the_email():
    text = "\n".join(r.line() for r in s.evaluate_success(CLAIMS, SCOPES, SCOPES, CID, ["example.com"], False))
    assert "a@example.com" not in text


@pytest.mark.parametrize(
    "params,status",
    [
        ({"error": "org_internal"}, Status.OK),
        ({"code": "x"}, Status.FAIL),
        ({"error": "access_denied"}, Status.NOT_CHECKABLE),
        (None, Status.NOT_CHECKABLE),
        ({}, Status.NOT_CHECKABLE),
    ],
)
def test_refusal_run(params, status):
    assert s.evaluate_refusal(params)[0].status is status


def test_callback_server_returns_the_query_and_ignores_strays():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    out = {}
    t = threading.Thread(target=lambda: out.update(p=s.receive_callback(port, 10)))
    t.start()
    import time

    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/favicon.ico", timeout=2)
            break
        except OSError:
            time.sleep(0.1)
    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc&state=st", timeout=2)
    t.join(10)
    assert out["p"] == {"code": "abc", "state": "st"}


def test_callback_server_times_out():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert s.receive_callback(port, 0.2) is None
