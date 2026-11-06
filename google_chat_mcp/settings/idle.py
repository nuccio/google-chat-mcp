"""
Idle disconnect of open tabs (docs/remote/04, "Runtime and cost").

Cloud Run bills an instance while a request is open, and an open websocket
is a request. So the server closes a tab that has seen no user event for
`TAB_IDLE_TIMEOUT`, and every tab at its session's end (the session never
outlives the Chat token, 60 minutes at most). The tab is sent to a static
page, which opens no websocket; a socket that is still open a moment later
is disconnected by force.

NiceGUI has no hook for "an event arrived", so `install` wraps
`Client.handle_event` to note the time. No `ui.timer` is used: one
server-side task checks all tabs every `SWEEP_INTERVAL`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from nicegui import Client, core, ui
from nicegui import app as nicegui_app

from .app import BASE, SettingsApp

log = logging.getLogger(__name__)

TAB_IDLE_TIMEOUT = timedelta(minutes=10)
SWEEP_INTERVAL = 30.0  # seconds
PAUSED_URL = f"{BASE}/paused"
FORCE_DISCONNECT_AFTER = 3.0  # seconds


class IdleTabs:
    def __init__(
        self,
        settings: SettingsApp,
        *,
        idle: timedelta = TAB_IDLE_TIMEOUT,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.idle = idle
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_event: dict[str, datetime] = {}  # client id -> time

    def touch(self, client_id: str) -> None:
        self.last_event[client_id] = self.now()

    def forget(self, client_id: str) -> None:
        self.last_event.pop(client_id, None)

    def due(self, client: Client) -> bool:
        """Should this tab be closed now?"""
        now = self.now()
        started = self.last_event.setdefault(client.id, now)
        if now - started >= self.idle:
            return True
        sid = client.request.cookies.get(self.settings.cookie_name) if client.request else None
        # A look at the session, not activity: the sweep must not keep it alive.
        return self.settings.sessions.peek(sid, now) is None

    def sweep(self) -> list[Client]:
        """Send every tab that is due to the paused page; returns them."""
        closed = []
        for client in list(Client.instances.values()):
            if client.has_socket_connection and self.due(client):
                try:
                    with client:
                        ui.navigate.to(PAUSED_URL)
                except Exception:  # a tab that is going away anyway
                    log.debug("Could not navigate an idle tab", exc_info=True)
                closed.append(client)
        return closed

    async def force_disconnect(self, client: Client) -> None:
        await asyncio.sleep(FORCE_DISCONNECT_AFTER)
        for socket_id in list(client._socket_to_document_id):  # pylint: disable=protected-access
            await core.sio.disconnect(socket_id)
        self.forget(client.id)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL)
            try:
                for client in self.sweep():
                    asyncio.ensure_future(self.force_disconnect(client))
            except Exception:
                log.exception("Idle sweep failed")


def install(settings: SettingsApp, **kwargs) -> IdleTabs:
    """Track user events and start the sweep with the app."""
    tabs = IdleTabs(settings, **kwargs)

    # Wrap the original, never an earlier wrapper (tests build several apps).
    original = getattr(Client.handle_event, "__wrapped__", Client.handle_event)

    def tracked(self: Client, msg: dict) -> None:
        tabs.touch(self.id)
        original(self, msg)

    tracked.__wrapped__ = original  # type: ignore[attr-defined]
    Client.handle_event = tracked  # type: ignore[method-assign]

    nicegui_app.on_connect(lambda client: tabs.touch(client.id))
    nicegui_app.on_disconnect(lambda client: tabs.forget(client.id))

    task: list[asyncio.Task] = []

    async def start() -> None:
        task.append(asyncio.ensure_future(tabs.run()))

    async def stop() -> None:
        for t in task:
            t.cancel()

    nicegui_app.on_startup(start)
    nicegui_app.on_shutdown(stop)
    return tabs
