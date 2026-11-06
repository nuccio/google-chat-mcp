"""
Schema of the documents shared by the two remote services (plain data).

`gcm-settings` writes `acls/{sub}`, `gcm-mcp` reads it; `gcm-mcp` writes
`users/{sub}`, `gcm-settings` reads it. Both sides convert through this module,
so the field names and the rules live in one place. No Firestore import: the
functions work on dicts, and the Firestore clients are in `remote`.

See docs/remote/04-settings-server-design.md ("Data model", "Schema
versioning") and docs/remote/07-mcp-server-design.md.

Versioning: a reader accepts the current and the previous `schema_version`
and upgrades the older one in memory (there is no previous one yet). A version
it does not know, higher or not an integer, raises `UnsupportedSchemaVersion`:
the reader fails closed (the MCP server treats the ACL as empty, `/settings`
refuses to write).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .permissions import InvalidPermissions, Permissions

SCHEMA_VERSION = 1
ACCEPTED_VERSIONS = frozenset({SCHEMA_VERSION})

MAX_ENTRIES = 200
SPACE_PREFIX = "spaces/"
SPACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# The only spaceType the MCP server grants in v1.
SPACE = "SPACE"


class InvalidDocument(ValueError):
    """The document cannot be read: malformed, or it breaks an invariant."""


class UnsupportedSchemaVersion(InvalidDocument):
    """The document has a `schema_version` this code does not read."""


def space_id(space_name: str) -> str:
    """'spaces/AAA' -> 'AAA', the key used in the documents."""
    if not space_name.startswith(SPACE_PREFIX):
        raise InvalidDocument(f"not a space resource name: {space_name!r}")
    return check_space_id(space_name[len(SPACE_PREFIX):])


def check_space_id(sid: str) -> str:
    if not isinstance(sid, str) or not SPACE_ID_RE.fullmatch(sid):
        raise InvalidDocument(f"invalid space ID: {sid!r}")
    return sid


def space_name(sid: str) -> str:
    """'AAA' -> 'spaces/AAA'."""
    return SPACE_PREFIX + check_space_id(sid)


def check_version(data: dict) -> int:
    version = data.get("schema_version")
    if isinstance(version, bool) or version not in ACCEPTED_VERSIONS:
        raise UnsupportedSchemaVersion(f"unsupported schema_version: {version!r}")
    return version


def _bool(data: dict, key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise InvalidDocument(f"{key!r} must be a boolean")
    return value


def _optional_datetime(data: dict, key: str) -> datetime | None:
    value = data.get(key)
    if value is not None and not isinstance(value, datetime):
        raise InvalidDocument(f"{key!r} must be a datetime")
    return value


def permissions_to_dict(p: Permissions) -> dict:
    return p.as_dict()


def permissions_from_dict(data: object) -> Permissions:
    """Strict: every field present and boolean, and the invariants hold."""
    if not isinstance(data, dict):
        raise InvalidDocument("permissions must be a map")
    p = Permissions(
        read=_bool(data, "read"),
        write=_bool(data, "write"),
        unattended=_bool(data, "unattended"),
    )
    try:
        p.validate()
    except InvalidPermissions as e:
        raise InvalidDocument(str(e)) from e
    return p


# --- acls/{sub} -------------------------------------------------------------


@dataclass(frozen=True)
class AclEntryDoc:
    """One entry of `acls/{sub}.entries`, keyed by space ID."""

    space_id: str
    permissions: Permissions
    space_type: str = SPACE
    # Snapshot taken at save time. Untrusted text: never used for a decision.
    display_name: str = ""
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AclDoc:
    entries: dict[str, AclEntryDoc] = field(default_factory=dict)
    updated_at: datetime | None = None


def acl_to_dict(doc: AclDoc) -> dict:
    if len(doc.entries) > MAX_ENTRIES:
        raise InvalidDocument(f"more than {MAX_ENTRIES} entries")
    entries = {}
    for sid, e in doc.entries.items():
        check_space_id(sid)
        if e.space_id != sid:
            raise InvalidDocument(f"entry key {sid!r} differs from its space_id")
        e.permissions.validate()
        entries[sid] = {
            **permissions_to_dict(e.permissions),
            "space_type": e.space_type,
            "display_name": e.display_name,
            "updated_at": e.updated_at,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": doc.updated_at,
        "entries": entries,
    }


def acl_from_dict(data: dict) -> AclDoc:
    """Reads an `acls/{sub}` document. Raises InvalidDocument (or its subclass
    UnsupportedSchemaVersion) for anything it cannot read: the caller decides
    how to fail closed. An entry that breaks an invariant fails the whole
    document, so a hand-edited entry never half-works."""
    check_version(data)
    raw = data.get("entries", {})
    if not isinstance(raw, dict):
        raise InvalidDocument("'entries' must be a map")
    if len(raw) > MAX_ENTRIES:
        raise InvalidDocument(f"more than {MAX_ENTRIES} entries")
    entries: dict[str, AclEntryDoc] = {}
    for sid, e in raw.items():
        check_space_id(sid)
        if not isinstance(e, dict):
            raise InvalidDocument(f"entry {sid!r} must be a map")
        display_name = e.get("display_name", "")
        space_type = e.get("space_type", "")
        if not isinstance(display_name, str) or not isinstance(space_type, str):
            raise InvalidDocument(f"entry {sid!r}: text fields must be strings")
        entries[sid] = AclEntryDoc(
            space_id=sid,
            permissions=permissions_from_dict(e),
            space_type=space_type,
            display_name=display_name,
            updated_at=_optional_datetime(e, "updated_at"),
        )
    return AclDoc(entries=entries, updated_at=_optional_datetime(data, "updated_at"))


def granted_permissions(doc: AclDoc) -> dict[str, Permissions]:
    """What the stored document grants, by space name: only named spaces
    (`space_type == "SPACE"`). A missing or different type is not granted, so
    a hand-edited entry for a direct message gives nothing."""
    return {
        space_name(sid): e.permissions
        for sid, e in doc.entries.items()
        if e.space_type == SPACE
    }


# --- acls/{sub}/audit/{auto_id} ----------------------------------------------


@dataclass(frozen=True)
class AuditDoc:
    space_id: str
    before: Permissions | None  # None: the entry did not exist
    after: Permissions | None  # None: the entry was removed
    changed_at: datetime
    expire_at: datetime
    session: str = ""  # sha256(session_id)[:16]


def audit_to_dict(doc: AuditDoc) -> dict:
    check_space_id(doc.space_id)
    return {
        "schema_version": SCHEMA_VERSION,
        "changed_at": doc.changed_at,
        "expire_at": doc.expire_at,
        "space": doc.space_id,
        "before": None if doc.before is None else permissions_to_dict(doc.before),
        "after": None if doc.after is None else permissions_to_dict(doc.after),
        "session": doc.session,
    }


def _optional_permissions(data: dict, key: str) -> Permissions | None:
    value = data.get(key)
    return None if value is None else _permissions_loose(value)


def _permissions_loose(data: object) -> Permissions:
    # An audit record describes what was stored, so it is read as written.
    if not isinstance(data, dict):
        raise InvalidDocument("permissions must be a map")
    return Permissions(
        read=_bool(data, "read"),
        write=_bool(data, "write"),
        unattended=_bool(data, "unattended"),
    )


def audit_from_dict(data: dict) -> AuditDoc:
    check_version(data)
    changed_at = _optional_datetime(data, "changed_at")
    expire_at = _optional_datetime(data, "expire_at")
    if changed_at is None or expire_at is None:
        raise InvalidDocument("changed_at and expire_at are required")
    session = data.get("session", "")
    if not isinstance(session, str):
        raise InvalidDocument("'session' must be a string")
    return AuditDoc(
        space_id=check_space_id(data.get("space")),
        before=_optional_permissions(data, "before"),
        after=_optional_permissions(data, "after"),
        changed_at=changed_at,
        expire_at=expire_at,
        session=session,
    )


# --- users/{sub} -------------------------------------------------------------


@dataclass(frozen=True)
class UserDoc:
    email: str
    created_at: datetime | None = None
    last_mcp_seen_at: datetime | None = None


def user_to_dict(doc: UserDoc) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "email": doc.email,
        "created_at": doc.created_at,
        "last_mcp_seen_at": doc.last_mcp_seen_at,
    }


def user_from_dict(data: dict) -> UserDoc:
    check_version(data)
    email = data.get("email", "")
    if not isinstance(email, str):
        raise InvalidDocument("'email' must be a string")
    return UserDoc(
        email=email,
        created_at=_optional_datetime(data, "created_at"),
        last_mcp_seen_at=_optional_datetime(data, "last_mcp_seen_at"),
    )
