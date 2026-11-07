"""
Assisted sign-in: one person signs in once, the script checks the result
(issue #71; docs/remote/08, Step 3 and Step 8).

    python -m google_chat_mcp.remote.signin --client-secrets dev-client.json \
        --service mcp --domain example.com
    python -m google_chat_mcp.remote.signin ... --expect refused

It is not a driven browser (Google's bot detection and two-step verification
make that fragile). It prints the sign-in URL, you open it and sign in, and it
receives the callback on `http://localhost:PORT/callback` (the **development**
OAuth client, level L3 of docs/remote/06, with that redirect URI registered).
Then it checks, with what Google returned:

- the granted scopes include every scope the service asks for;
- the ID token's audience is this client and its signature is Google's;
- `email_verified` is true;
- `hd` is one of the allowed domains (in `--development`, where the consent
  screen is External and personal accounts have no `hd`, it is not required).

`--expect refused` is the negative run, for **production** only: sign in with
an account *outside* the organisation. On an Internal consent screen Google
refuses it with `org_internal` and sends no code, which is what proves the
screen is Internal. A code arriving is a failure. If Google only shows its own
error page and sends nothing back, the script times out and says that this has
to be confirmed by reading the page (it cannot see it).

Tokens, codes and the client secret are never printed, and the e-mail address
is not printed either (only its domain). Nothing is stored. Needs the `remote`
extra.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Collection, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from .check import DOC_CONSENT, Result, Status, fail, ok, unknown
from .oauth import REQUIRED_SCOPES

# What gcm-settings asks (docs/remote/04, "OAuth client").
SETTINGS_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/chat.spaces.readonly",
)
SCOPES = {"settings": SETTINGS_SCOPES, "mcp": REQUIRED_SCOPES}
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 300


def evaluate_success(
    claims: Mapping[str, object],
    granted_scopes: Collection[str],
    requested_scopes: Collection[str],
    client_id: str,
    domains: Collection[str],
    development: bool,
) -> list[Result]:
    """The checks on a completed sign-in. `claims` come from a verified ID token."""
    results: list[Result] = []
    missing = sorted(set(requested_scopes) - set(granted_scopes))
    results.append(
        ok("signin.scopes", f"all {len(set(requested_scopes))} requested scopes granted")
        if not missing
        else fail("signin.scopes", f"not granted: {', '.join(missing)}", DOC_CONSENT)
    )
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    results.append(
        ok("signin.audience", "the ID token is for this client")
        if client_id in audiences
        else fail("signin.audience", "the ID token is for another client", DOC_CONSENT)
    )
    results.append(
        ok("signin.email_verified", "true")
        if claims.get("email_verified") is True
        else fail("signin.email_verified", "email_verified is not true", DOC_CONSENT)
    )
    hd = claims.get("hd")
    wanted = {d.lower() for d in domains}
    if isinstance(hd, str) and hd.strip():
        if hd.strip().lower() in wanted:
            results.append(ok("signin.hd", f"hd is {hd.strip().lower()}, an allowed domain"))
        else:
            results.append(
                fail("signin.hd", f"hd is {hd.strip().lower()}, not in the allowed domains", DOC_CONSENT)
            )
    elif development:
        results.append(ok("signin.hd", "no hd claim; accepted in development (a test user)"))
    else:
        results.append(
            fail(
                "signin.hd",
                "the token has no hd claim: the account is not in a Workspace domain "
                "(production refuses it)",
                DOC_CONSENT,
            )
        )
    return results


def evaluate_refusal(params: Mapping[str, str] | None) -> list[Result]:
    """The negative run. `params` are the callback's query parameters, or None
    when nothing arrived before the timeout."""
    id = "signin.refused"
    if params is None:
        return [
            unknown(
                id,
                "nothing came back. Google usually shows its own error page and sends no code: "
                "the page must say `Error 403: org_internal`. Read it; this script cannot",
                DOC_CONSENT,
            )
        ]
    if "code" in params:
        return [
            fail(
                id,
                "Google sent a code for an account outside the organisation: "
                "the consent screen is not Internal",
                DOC_CONSENT,
            )
        ]
    error = params.get("error", "")
    if error == "org_internal":
        return [ok(id, "refused with org_internal: the consent screen is Internal")]
    if error:
        return [unknown(id, f"refused with `{error}`, not the expected org_internal", DOC_CONSENT)]
    return [unknown(id, "the callback carried neither a code nor an error", DOC_CONSENT)]


def receive_callback(port: int, timeout: float) -> dict[str, str] | None:
    """Serve one request on 127.0.0.1 and return its query parameters; None on timeout."""
    received: dict[str, dict[str, str]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            if urlsplit(self.path).path == "/callback":
                received["params"] = query
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"You can close this tab and go back to the terminal.\n")

        def log_message(self, *args) -> None:  # no access log: the query holds the code
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    deadline = time.monotonic() + timeout
    try:
        # A stray request (a favicon) must not end the wait; the deadline does.
        while "params" not in received:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            server.timeout = remaining
            server.handle_request()
    finally:
        server.server_close()
    return received.get("params")


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="python -m google_chat_mcp.remote.signin",
        description="Sign in once with the development OAuth client and check the result.",
    )
    ap.add_argument("--client-secrets", required=True, help="JSON of the development client")
    ap.add_argument("--service", choices=sorted(SCOPES), default="mcp")
    ap.add_argument("--domain", action="append", default=[], help="allowed domain (repeatable)")
    ap.add_argument("--expect", choices=("success", "refused"), default="success")
    ap.add_argument("--development", action="store_true", help="External/Testing screen: hd not required")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds to wait for the sign-in")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        raise SystemExit(
            "The sign-in script needs the 'remote' extra: pip install \"google-chat-mcp[remote]\""
        ) from None
    import os

    # Compare the scopes ourselves instead of letting oauthlib raise on a difference.
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    if not args.development and not args.domain and args.expect == "success":
        raise SystemExit("Give at least one --domain (or --development).")

    scopes = list(SCOPES[args.service])
    redirect_uri = f"http://localhost:{args.port}/callback"
    try:
        flow = Flow.from_client_secrets_file(args.client_secrets, scopes=scopes, redirect_uri=redirect_uri)
    except (OSError, ValueError) as e:
        raise SystemExit(f"Cannot read the client file: {type(e).__name__}") from None
    url, state = flow.authorization_url(prompt="select_account", access_type="online")
    client_id = flow.client_config["client_id"]

    print(f"Redirect URI used: {redirect_uri} (it must be registered on the development client)")
    print("Open this address, sign in once, and come back here:\n")
    print(url + "\n")
    params = receive_callback(args.port, args.timeout)

    if args.expect == "refused":
        results = evaluate_refusal(params)
    elif params is None:
        results = [fail("signin.callback", "no callback before the timeout", DOC_CONSENT)]
    elif params.get("state") != state:
        results = [fail("signin.callback", "the callback's state does not match", DOC_CONSENT)]
    elif "code" not in params:
        results = [fail("signin.callback", f"Google answered `{params.get('error', 'no code')}`", DOC_CONSENT)]
    else:
        try:
            flow.fetch_token(code=params["code"])
            token = flow.oauth2session.token
            claims = id_token.verify_oauth2_token(
                flow.credentials.id_token, google.auth.transport.requests.Request(), audience=client_id
            )
        except Exception as e:  # never show the message: it could quote a token
            results = [fail("signin.token", f"token exchange or verification failed ({type(e).__name__})", DOC_CONSENT)]
        else:
            granted = str(token.get("scope", "")).split()
            if isinstance(token.get("scope"), list):
                granted = list(token["scope"])
            results = evaluate_success(claims, granted, scopes, client_id, args.domain, args.development)

    for r in results:
        print(r.line())
    return 1 if any(r.status is Status.FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
