"""
The settings service as deployed on Cloud Run (step 5 of
docs/remote/04-settings-server-design.md): the real backends wired to the
pages, and NiceGUI started on the configured port.

Started by `python -m google_chat_mcp.settings` (`__main__.py`). It never
imports `mock.py` and never falls back to it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from nicegui import app as nicegui_app
from nicegui import ui
from starlette.responses import RedirectResponse, Response

from ..remote.policy import AdminPolicy
from . import idle
from .app import BASE, TITLE, SettingsApp, install
from .chat_spaces import ChatApiSpacesSource
from .config import SettingsConfig
from .firestore import (
    FirestoreAclStore,
    FirestoreLoginTransactions,
    FirestoreSessionStore,
    FirestoreUsage,
)
from .google_signin import GoogleSignIn
from .models import Account, Policy
from .sessions import PersistentSessions

log = logging.getLogger(__name__)

DEVELOPMENT_BANNER = (
    "Development deployment (DEPLOY_MODE=development): accounts without a Workspace "
    "domain are accepted. Use test data only."
)


class AdminPolicySource:
    """`PolicySource`: the one policy of the deployment, the same for everyone."""

    def __init__(self, admin: AdminPolicy) -> None:
        self._policy = admin.policy

    def policy_for(self, account: Account) -> Policy:
        return self._policy


def create_settings(
    config: SettingsConfig,
    admin: AdminPolicy,
    db,
    *,
    now: Callable[[], datetime] | None = None,
) -> SettingsApp:
    """The settings app on Firestore, Google sign-in and the Chat API."""
    kwargs = {"now": now} if now is not None else {}
    return SettingsApp(
        spaces=ChatApiSpacesSource(),
        acl=FirestoreAclStore(db),
        usage=FirestoreUsage(db),
        policy=AdminPolicySource(admin),
        sign_in=GoogleSignIn(
            FirestoreLoginTransactions(db),
            client_id=config.client_id,
            client_secret=config.client_secret,
            admin_policy=admin,
            redirect_uri=config.redirect_uri,
            secure_cookies=config.secure,
        ),
        sessions=PersistentSessions(FirestoreSessionStore(db)),
        secure_cookies=config.secure,
        banner=DEVELOPMENT_BANNER if admin.development else None,
        **kwargs,
    )


def install_production(settings: SettingsApp, config: SettingsConfig) -> idle.IdleTabs:
    """Register pages, routes and middlewares on NiceGUI's app."""
    install(settings, allowed_origins={config.origin}, hsts=config.secure)

    @nicegui_app.get("/")
    def root() -> Response:
        return RedirectResponse(BASE, status_code=303)

    return idle.install(settings)


def run(config: SettingsConfig, admin: AdminPolicy, db) -> None:
    """Serve the settings service (blocks)."""
    install_production(create_settings(config, admin, db), config)
    ui.run(
        host=config.host,
        port=config.port,
        title=TITLE,
        reload=False,
        show=False,
        tailwind=False,
        show_welcome_message=False,
        binding_refresh_interval=None,
        storage_secret=None,
        # Cloud Run terminates TLS in front of the container.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
