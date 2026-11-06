"""
The production session store: `SessionStore`'s interface on Firestore.

`SettingsApp` talks to a store with `get`, `peek`, `create_signed_in`,
`persist` and `delete`. `app.SessionStore` keeps everything in memory (the
mock and the UI tests); this one keeps what must survive a change of
instance in `sessions/{sha256(id)}` (`firestore.FirestoreSessionStore`):
the account, the Chat access token and the change waiting for confirmation
(docs/remote/04, "Session" and "Confirmation").

Only the flash messages of a page transition stay in this process: they are
shown once on the next page and lose nothing if the instance changes.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from .app import PendingChange, Session
from .firestore import FirestoreSessionStore, StoredSession
from .models import Account, Permissions

# The most flash lists kept at once. They are dropped with their session; the
# bound only stops a flood of abandoned sessions from growing the dictionary.
MAX_FLASH_SESSIONS = 1000


def _perm(p: Permissions | None) -> dict[str, bool] | None:
    if p is None:
        return None
    return {"read": p.read, "write": p.write, "unattended": p.unattended}


def _perm_from(d: dict[str, Any] | None) -> Permissions | None:
    if d is None:
        return None
    return Permissions(
        read=bool(d["read"]), write=bool(d["write"]), unattended=bool(d["unattended"])
    )


def pending_to_dict(pending: dict[str, PendingChange]) -> dict[str, Any] | None:
    if not pending:
        return None
    return {
        "changes": {
            token: {
                "space_name": p.space_name,
                "display_name": p.display_name,
                "before": _perm(p.before),
                "after": _perm(p.after),
                "acl_fingerprint": p.acl_fingerprint,
                "created_at": p.created_at,
            }
            for token, p in pending.items()
        }
    }


def pending_from_dict(data: dict[str, Any] | None) -> dict[str, PendingChange]:
    result: dict[str, PendingChange] = {}
    for token, d in ((data or {}).get("changes") or {}).items():
        after = _perm_from(d["after"])
        assert after is not None
        result[token] = PendingChange(
            token=token,
            space_name=d["space_name"],
            display_name=d["display_name"],
            before=_perm_from(d["before"]),
            after=after,
            acl_fingerprint=d["acl_fingerprint"],
            created_at=d["created_at"],
        )
    return result


class PersistentSessions:
    """`app.SessionStore` on Firestore."""

    # Google's sign-in keeps its own login transaction and cookie, so no
    # session exists before the sign-in has finished.
    presession = False

    def __init__(self, store: FirestoreSessionStore) -> None:
        self._store = store
        self._flash: dict[str, list[tuple[str, str]]] = {}
        self._lock = threading.Lock()

    def _session(self, stored: StoredSession) -> Session:
        with self._lock:
            if len(self._flash) >= MAX_FLASH_SESSIONS and stored.session_id not in self._flash:
                self._flash.pop(next(iter(self._flash)))
            flash = self._flash.setdefault(stored.session_id, [])
        return Session(
            sid=stored.session_id,
            last_seen=stored.last_seen_at,
            account=Account(
                sub=stored.sub,
                email=stored.email,
                access_token=stored.access_token,
                token_expires_at=stored.token_expires_at,
            ),
            flash=flash,
            pending=pending_from_dict(stored.pending_change),
        )

    def get(self, sid: str | None, now: datetime) -> Session | None:
        stored = self._store.get(sid, now)
        if stored is None:
            if sid:
                with self._lock:
                    self._flash.pop(sid, None)
            return None
        return self._session(stored)

    def peek(self, sid: str | None, now: datetime) -> Session | None:
        stored = self._store.peek(sid, now)
        return None if stored is None else self._session(stored)

    def create_signed_in(self, account: Account, now: datetime) -> Session:
        if account.token_expires_at is None:
            raise ValueError("A session needs the expiry of the access token")
        stored = self._store.create(
            sub=account.sub,
            email=account.email,
            access_token=account.access_token,
            token_expires_at=account.token_expires_at,
            now=now,
        )
        return self._session(stored)

    def persist(self, s: Session) -> None:
        self._store.set_pending_change(s.sid, pending_to_dict(s.pending))

    def delete(self, sid: str) -> None:
        self._store.delete(sid)
        with self._lock:
            self._flash.pop(sid, None)
