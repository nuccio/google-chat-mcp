"""
Firestore implementations of the settings backends (step 3 of
docs/remote/04-settings-server-design.md).

- `FirestoreAclStore` (`acls/{sub}` and `acls/{sub}/audit/{id}`)
- `FirestoreUsage` (`users/{sub}.last_mcp_seen_at`, read only)
- `FirestoreSessionStore` (`sessions/{sha256(id)}`)
- `FirestoreLoginTransactions` (`login_transactions/{sha256(id)}`)

The ACL and user documents are converted only through `google_chat_mcp.schema`,
which the MCP service shares. Sessions and login transactions are private to
this service, so their layout lives here.

Needs the `remote` extra. The real server must not import `mock.py`.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from google.cloud import firestore

from .. import schema
from .backends import ConcurrentChange
from .models import SPACE, AclEntry, AuditRecord, Permissions

AUDIT_RETENTION = timedelta(days=365)
# The library retries a transaction that lost a race; the default of 5 is too few
# when several writers hit the same `acls/{sub}` document at once.
TRANSACTION_ATTEMPTS = 15

SESSION_IDLE_TIMEOUT = timedelta(minutes=30)
SESSION_MAX_LIFETIME = timedelta(minutes=60)
SESSION_TOKEN_MARGIN = timedelta(minutes=2)
SESSION_TOUCH_INTERVAL = timedelta(minutes=1)
LOGIN_TRANSACTION_TTL = timedelta(minutes=10)


def digest(identifier: str) -> str:
    """The document ID of a session or login transaction: sha256 of the secret
    ID the browser holds, so a copy of the database cannot be replayed."""
    return hashlib.sha256(identifier.encode()).hexdigest()


def session_correlation(session_id: str) -> str:
    """The short ID that ties the audit records of one session together."""
    return digest(session_id)[:16]


# ---------------------------------------------------------------------------
# ACL


def _acl_ref(db, sub: str):
    return db.collection("acls").document(sub)


def _read_acl(snapshot) -> schema.AclDoc:
    """A missing document is an empty ACL. One this code cannot read raises
    (`schema.InvalidDocument`): the settings never write over it."""
    if not snapshot.exists:
        return schema.AclDoc()
    return schema.acl_from_dict(snapshot.to_dict())


def _entry(doc: schema.AclEntryDoc) -> AclEntry:
    return AclEntry(
        space_name=schema.space_name(doc.space_id),
        permissions=doc.permissions,
        display_name=doc.display_name,
        updated_at=doc.updated_at,
    )


class FirestoreAclStore:
    """
    `AclStore` on Firestore. Only entries of named spaces (`space_type ==
    "SPACE"`) are visible: others, for example a hand-edited direct message,
    grant nothing, are not listed, and are left untouched by every write.
    """

    def __init__(self, db) -> None:
        self._db = db

    def list_entries(self, sub: str) -> list[AclEntry]:
        doc = _read_acl(_acl_ref(self._db, sub).get())
        return [_entry(e) for e in doc.entries.values() if e.space_type == SPACE]

    def get_entry(self, sub: str, space_name: str) -> AclEntry | None:
        sid = schema.space_id(space_name)
        e = _read_acl(_acl_ref(self._db, sub).get()).entries.get(sid)
        return _entry(e) if e is not None and e.space_type == SPACE else None

    def put_entry(
        self,
        sub: str,
        entry: AclEntry,
        *,
        expected_before: Permissions | None,
        now: datetime,
        session: str = "",
    ) -> AuditRecord:
        sid = schema.space_id(entry.space_name)
        entry.permissions.validate()
        ref = _acl_ref(self._db, sub)
        audit_ref = ref.collection("audit").document()

        @firestore.transactional
        def run(transaction) -> AuditRecord:
            doc = _read_acl(ref.get(transaction=transaction))
            current = doc.entries.get(sid)
            before = current.permissions if current and current.space_type == SPACE else None
            if before != expected_before:
                raise ConcurrentChange(entry.space_name)
            if current is not None and current.space_type != SPACE:
                # An entry of another type under this ID: not ours to overwrite.
                raise ConcurrentChange(entry.space_name)
            entries = dict(doc.entries)
            entries[sid] = schema.AclEntryDoc(
                sid, entry.permissions, SPACE, entry.display_name, now
            )
            transaction.set(ref, schema.acl_to_dict(schema.AclDoc(entries, updated_at=now)))
            transaction.set(
                audit_ref, _audit_dict(sid, before, entry.permissions, now, session)
            )
            return AuditRecord(sub, entry.space_name, before, entry.permissions, now)

        return run(self._db.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    def delete_entry(
        self, sub: str, space_name: str, *, now: datetime, session: str = ""
    ) -> AuditRecord | None:
        sid = schema.space_id(space_name)
        ref = _acl_ref(self._db, sub)
        audit_ref = ref.collection("audit").document()

        @firestore.transactional
        def run(transaction) -> AuditRecord | None:
            doc = _read_acl(ref.get(transaction=transaction))
            current = doc.entries.get(sid)
            if current is None or current.space_type != SPACE:
                return None
            entries = {k: v for k, v in doc.entries.items() if k != sid}
            transaction.set(ref, schema.acl_to_dict(schema.AclDoc(entries, updated_at=now)))
            transaction.set(
                audit_ref, _audit_dict(sid, current.permissions, None, now, session)
            )
            return AuditRecord(sub, space_name, current.permissions, None, now)

        return run(self._db.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    def audit_log(self, sub: str) -> list[AuditRecord]:
        snapshots = _acl_ref(self._db, sub).collection("audit").order_by("changed_at").stream()
        records = []
        for snap in snapshots:
            a = schema.audit_from_dict(snap.to_dict())
            records.append(
                AuditRecord(sub, schema.space_name(a.space_id), a.before, a.after, a.changed_at)
            )
        return records


def _audit_dict(
    sid: str, before: Permissions | None, after: Permissions | None, now: datetime, session: str
) -> dict:
    return schema.audit_to_dict(
        schema.AuditDoc(sid, before, after, now, now + AUDIT_RETENTION, session)
    )


# ---------------------------------------------------------------------------
# Connector usage


class FirestoreUsage:
    """`ConnectorUsage`: the MCP service writes `users/{sub}`, this only reads."""

    def __init__(self, db) -> None:
        self._db = db

    def last_used(self, sub: str) -> datetime | None:
        snap = self._db.collection("users").document(sub).get()
        if not snap.exists:
            return None
        return schema.user_from_dict(snap.to_dict()).last_mcp_seen_at


# ---------------------------------------------------------------------------
# Sessions


@dataclass(frozen=True)
class StoredSession:
    session_id: str  # the secret the browser holds; never stored
    sub: str
    email: str
    access_token: str
    token_expires_at: datetime
    created_at: datetime
    last_seen_at: datetime
    expire_at: datetime
    pending_change: dict | None = None


def _session_from(session_id: str, data: dict[str, Any]) -> StoredSession:
    return StoredSession(
        session_id=session_id,
        sub=data["sub"],
        email=data["email"],
        access_token=data["access_token"],
        token_expires_at=data["token_expires_at"],
        created_at=data["created_at"],
        last_seen_at=data["last_seen_at"],
        expire_at=data["expire_at"],
        pending_change=data.get("pending_change"),
    )


class FirestoreSessionStore:
    """
    Server-side sessions in `sessions/{sha256(session_id)}`. The cookie only
    carries the random ID.

    A session ends at the first of: the idle timeout (30 minutes without a
    request), `expire_at` = min(token expiry - 2 minutes, created + 60 minutes)
    (it never outlives the Chat token it carries), or logout. `expire_at` also
    drives the Firestore TTL policy, which removes the documents late.
    """

    def __init__(self, db) -> None:
        self._col = db.collection("sessions")

    def create(
        self,
        *,
        sub: str,
        email: str,
        access_token: str,
        token_expires_at: datetime,
        now: datetime,
    ) -> StoredSession:
        """Always a new ID, never one from before the login (session fixation)."""
        session_id = secrets.token_urlsafe(32)
        expire_at = min(token_expires_at - SESSION_TOKEN_MARGIN, now + SESSION_MAX_LIFETIME)
        data = {
            "sub": sub,
            "email": email,
            "access_token": access_token,
            "token_expires_at": token_expires_at,
            "created_at": now,
            "last_seen_at": now,
            "expire_at": expire_at,
            "pending_change": None,
        }
        self._col.document(digest(session_id)).set(data)
        return _session_from(session_id, data)

    def get(self, session_id: str | None, now: datetime) -> StoredSession | None:
        """The live session, or None. An expired one is deleted. `last_seen_at`
        is rewritten at most once a minute, to save writes."""
        if not session_id:
            return None
        ref = self._col.document(digest(session_id))
        snap = ref.get()
        if not snap.exists:
            return None
        s = _session_from(session_id, snap.to_dict())
        if now >= s.expire_at or now - s.last_seen_at > SESSION_IDLE_TIMEOUT:
            ref.delete()
            return None
        if now - s.last_seen_at >= SESSION_TOUCH_INTERVAL:
            ref.update({"last_seen_at": now})
            s = StoredSession(**{**s.__dict__, "last_seen_at": now})
        return s

    def set_pending_change(self, session_id: str, pending: dict | None) -> bool:
        """Replace the change waiting for confirmation. False if the session is gone."""
        try:
            self._col.document(digest(session_id)).update({"pending_change": pending})
        except Exception as e:  # google.api_core.exceptions.NotFound
            from google.api_core.exceptions import NotFound

            if isinstance(e, NotFound):
                return False
            raise
        return True

    def delete(self, session_id: str) -> None:
        self._col.document(digest(session_id)).delete()


# ---------------------------------------------------------------------------
# Login transactions


@dataclass(frozen=True)
class LoginTransaction:
    state: str
    nonce: str
    code_verifier: str
    select_account: bool
    created_at: datetime
    expire_at: datetime


class FirestoreLoginTransactions:
    """
    `login_transactions/{sha256(tx_id)}`: what the callback needs to check an
    answer from Google. Valid for 10 minutes and consumed once.
    """

    def __init__(self, db) -> None:
        self._db = db
        self._col = db.collection("login_transactions")

    def create(
        self,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
        select_account: bool,
        now: datetime,
    ) -> str:
        """Store the transaction; returns the `tx_id` for the login cookie."""
        tx_id = secrets.token_urlsafe(32)
        self._col.document(digest(tx_id)).set(
            {
                "state": state,
                "nonce": nonce,
                "code_verifier": code_verifier,
                "select_account": select_account,
                "created_at": now,
                "expire_at": now + LOGIN_TRANSACTION_TTL,
            }
        )
        return tx_id

    def consume(self, tx_id: str | None, now: datetime) -> LoginTransaction | None:
        """Read and delete in one transaction, so a `code` or `state` is used
        once even by two concurrent callbacks. None if missing or expired."""
        if not tx_id:
            return None
        ref = self._col.document(digest(tx_id))

        @firestore.transactional
        def run(transaction) -> LoginTransaction | None:
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return None
            transaction.delete(ref)
            d = snap.to_dict()
            if now >= d["expire_at"]:
                return None
            return LoginTransaction(
                d["state"], d["nonce"], d["code_verifier"], d["select_account"],
                d["created_at"], d["expire_at"],
            )

        return run(self._db.transaction(max_attempts=TRANSACTION_ATTEMPTS))
