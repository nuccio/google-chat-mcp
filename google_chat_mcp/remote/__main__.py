"""
Entry point of `gcm-mcp`, the remote MCP server (docs/remote/07, step 5):
`python -m google_chat_mcp.remote`. Needs the `remote` extra.

It serves Streamable HTTP at `/mcp` under uvicorn, stateless (M6), behind the
OAuth layer of `oauth.py`, and wires the shared tools (`create_server`) with the
Firestore `AclProvider` and the per-request `ChatSource`. The `users` middleware
records `last_mcp_seen_at`.

Configuration comes from the environment, where Cloud Run puts the Secret
Manager values it is told to reference (nothing is fetched from Secret Manager
by this code):

| Variable | |
|---|---|
| `BASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `OAUTH_JWT_SIGNING_KEY`, `OAUTH_FERNET_KEYS` | the connector's OAuth layer (`oauth.py`) |
| `ALLOWED_DOMAINS`, `DEPLOY_MODE`, `POLICY_*` | the admin policy (`policy.py`) |
| `SETTINGS_BASE_URL` | where the "no space configured" message sends the user (`acl.py`) |
| `GOOGLE_CLOUD_PROJECT`, `FIRESTORE_DATABASE` | the Firestore database (`firestore.py`) |
| `PORT` | port to listen on, set by Cloud Run (default 8080) |

A missing or invalid value stops the process at start-up with a message that
names the variable and never its value (fail closed). Nothing is read at import.
Logs are JSON on stdout with addresses masked, and there is no access log
(`logging.py`).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping

EXTRA_MESSAGE = (
    "The remote MCP server needs the 'remote' extra: pip install \"google-chat-mcp[remote]\""
)
DEFAULT_PORT = 8080


class ConfigError(ValueError):
    """A configuration value of the entry point is invalid."""


def parse_port(raw: str | None) -> int:
    text = (raw or "").strip() or str(DEFAULT_PORT)
    try:
        port = int(text)
    except ValueError:
        raise ConfigError(f"PORT: not a number: {text!r}") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"PORT: out of range: {port}")
    return port


def build_server(
    environ: Mapping[str, str] | None = None,
    *,
    acl_reader=None,
    users_writer=None,
    token_store=None,
    verifier=None,
):
    """`(server, http_options)`: the FastMCP server wired for the remote
    deployment, and the keyword arguments for its `http_app()`.

    The keyword arguments are for tests (in-memory readers and stores, a fake
    Google verifier); the entry point passes none, and the real Firestore
    clients are then created here, not at import.

    Raises `PolicyConfigError`, `OAuthConfigError` or `AclConfigError` for a bad
    configuration.
    """
    import os

    from ..tools import create_server
    from .acl import FirestoreAclReader, RemoteAclProvider, settings_link_from_env
    from .chat import RemoteChatSource, current_sub
    from .oauth import OAuthSettings, build_auth, http_protection_options
    from .policy import AdminPolicy
    from .users import FirestoreUsersWriter, UsersMiddleware

    env = os.environ if environ is None else environ
    admin = AdminPolicy.from_env(env)
    oauth = OAuthSettings.from_env(admin, env)
    link = settings_link_from_env(env)

    auth = build_auth(oauth, admin, token_store=token_store, verifier=verifier)
    provider = RemoteAclProvider(acl_reader or FirestoreAclReader(), admin, link, current_sub)
    # M11: no tool argument is logged in remote mode.
    mcp = create_server(provider, RemoteChatSource(), redact=lambda _arguments: {}, auth=auth)
    mcp.add_middleware(UsersMiddleware(users_writer or FirestoreUsersWriter()))
    return mcp, http_protection_options(oauth)


def build_app(environ: Mapping[str, str] | None = None, **kwargs):
    """The ASGI app: Streamable HTTP at `/mcp`, stateless (M6), Host/Origin
    protection on (09, N5). No CORS middleware is added."""
    mcp, http_options = build_server(environ, **kwargs)
    return mcp.http_app(stateless_http=True, **http_options)


def main() -> None:
    try:
        import google.cloud.firestore  # noqa: F401
        import key_value.aio.stores.firestore  # noqa: F401
        from cryptography.fernet import Fernet  # noqa: F401
    except ImportError:
        raise SystemExit(EXTRA_MESSAGE) from None

    import os

    import uvicorn

    from .acl import AclConfigError
    from .logging import log_config, setup_logging
    from .oauth import OAuthConfigError
    from .policy import PolicyConfigError

    setup_logging()
    try:
        port = parse_port(os.environ.get("PORT"))
        app = build_app()
    except (ConfigError, PolicyConfigError, OAuthConfigError, AclConfigError) as e:
        raise SystemExit(f"Configuration error: {e}") from None
    # fastmcp installs its handlers while the server is built: take them out again.
    from .logging import route_fastmcp_to_root

    route_fastmcp_to_root()
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=log_config(), access_log=False)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit("python -m google_chat_mcp.remote takes no arguments; use the environment")
    main()
