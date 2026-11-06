"""
Fixed test dataset of the remote services: four users with different configurations.

Used by every test that needs users, ACLs or audit records (in-memory, Firestore
emulator, contract tests between `gcm-settings` and `gcm-mcp`) and by the seed
script. See tests/README.md ("Fixed dataset") and issue #83.

Rules:
- Documents are built with `google_chat_mcp.schema`, never written by hand. The
  exception is dave, whose documents are meant to be unreadable.
- The expected values are declared literally next to the data. Tests compare
  against them and never recompute the logic they check.
- The values are made up: example emails, numeric `sub`s that look like
  Google's, space IDs that exist nowhere.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from google_chat_mcp import schema
from google_chat_mcp.permissions import Permissions

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=2)
T2 = T0 + timedelta(days=5)

# Two admin policies the expectations are written for. With no restriction,
# every stored entry of a named space is granted. With RESTRICTED, unattended
# sends are switched off (reading and writing stay allowed): an entry that was
# saved before the policy tightened is suspended, not missing, and comes back
# when the policy is loosened.
POLICY_OPEN = {"allow_read": True, "allow_write": True, "allow_unattended": True}
POLICY_RESTRICTED = {"allow_read": True, "allow_write": True, "allow_unattended": False}

R = Permissions(read=True)
W = Permissions(write=True)
RW = Permissions(read=True, write=True)
WU = Permissions(write=True, unattended=True)


@dataclass(frozen=True)
class FixtureUser:
    name: str
    sub: str
    email: str
    # What gets stored (the dicts are exactly what the Firestore client writes).
    user_doc: dict | None
    acl_doc: dict | None
    audit_docs: tuple[dict, ...] = ()
    # Expectations.
    # "ok": readable; "empty": no ACL document; "unreadable": the reader must
    # fail closed and treat the ACL as empty.
    state: str = "ok"
    # What the stored document grants, by space name (named spaces only, before
    # the admin policy).
    granted: dict[str, Permissions] = field(default_factory=dict)
    # Of those, the spaces POLICY_RESTRICTED suspends.
    suspended_by_policy: frozenset[str] = frozenset()
    # What list_spaces must return for this user, as {name: (read, write)}:
    # under POLICY_OPEN, and under POLICY_RESTRICTED.
    list_spaces_open: dict[str, tuple[bool, bool]] = field(default_factory=dict)
    list_spaces_restricted: dict[str, tuple[bool, bool]] = field(default_factory=dict)
    # The spaces the user can still see in Google Chat (spaces.list). A stored
    # entry for any other space is orphaned.
    visible_space_ids: frozenset[str] = frozenset()


def _user_doc(email: str, *, seen: datetime | None) -> dict:
    return schema.user_to_dict(
        schema.UserDoc(email=email, created_at=T0, last_mcp_seen_at=seen)
    )


def _entry(
    sid: str,
    p: Permissions,
    display_name: str,
    *,
    space_type: str = schema.SPACE,
    updated_at: datetime = T1,
) -> schema.AclEntryDoc:
    return schema.AclEntryDoc(sid, p, space_type, display_name, updated_at)


def _acl(*entries: schema.AclEntryDoc, updated_at: datetime = T1) -> dict:
    return schema.acl_to_dict(
        schema.AclDoc({e.space_id: e for e in entries}, updated_at=updated_at)
    )


def _audit(
    sid: str,
    before: Permissions | None,
    after: Permissions | None,
    changed_at: datetime,
    session: str,
) -> dict:
    return schema.audit_to_dict(
        schema.AuditDoc(
            sid, before, after, changed_at, changed_at + timedelta(days=365), session
        )
    )


# --- alice: the normal case, every valid combination -------------------------

ALICE = FixtureUser(
    name="alice",
    sub="100000000000000000001",
    email="alice@example.com",
    user_doc=_user_doc("alice@example.com", seen=T2),
    acl_doc=_acl(
        _entry("AAABBBCCC", RW, "Team General"),
        _entry("ANNOUNCE1", R, "Announcements"),
        _entry("SUPPORT01", W, "Support"),
        _entry("REPORTS01", WU, "Reports", updated_at=T2),
        updated_at=T2,
    ),
    audit_docs=(
        _audit("AAABBBCCC", None, RW, T0, "0123456789abcdef"),
        _audit("REPORTS01", W, WU, T2, "fedcba9876543210"),
    ),
    granted={
        "spaces/AAABBBCCC": RW,
        "spaces/ANNOUNCE1": R,
        "spaces/SUPPORT01": W,
        "spaces/REPORTS01": WU,
    },
    list_spaces_open={
        "spaces/AAABBBCCC": (True, True),
        "spaces/ANNOUNCE1": (True, False),
        "spaces/SUPPORT01": (False, True),
        "spaces/REPORTS01": (False, True),
    },
    list_spaces_restricted={
        "spaces/AAABBBCCC": (True, True),
        "spaces/ANNOUNCE1": (True, False),
        "spaces/SUPPORT01": (False, True),
    },
    suspended_by_policy=frozenset({"spaces/REPORTS01"}),
    visible_space_ids=frozenset({"AAABBBCCC", "ANNOUNCE1", "SUPPORT01", "REPORTS01"}),
)

# --- bob: a new user, no ACL document ------------------------------------------

BOB = FixtureUser(
    name="bob",
    sub="100000000000000000002",
    email="bob@example.com",
    user_doc=_user_doc("bob@example.com", seen=None),
    acl_doc=None,
    state="empty",
    visible_space_ids=frozenset({"AAABBBCCC", "BOBONLY01"}),
)

# --- carol: entries that must not be granted ---------------------------------

CAROL = FixtureUser(
    name="carol",
    sub="100000000000000000003",
    email="carol@example.com",
    user_doc=_user_doc("carol@example.com", seen=T1),
    acl_doc=_acl(
        _entry("CAROLTEAM", RW, "Carol's team"),
        # Hand-edited entries for a direct message and a group chat: not granted.
        _entry("DMENTRY01", R, "Direct message", space_type="DIRECT_MESSAGE"),
        _entry("GROUPCHT1", RW, "Group chat", space_type="GROUP_CHAT"),
        # Unattended: suspended under POLICY_RESTRICTED.
        _entry("UNATTEND1", WU, "Unattended sends"),
        # A space the user has left: orphaned. The name is untrusted text.
        _entry("ORPHAN001", R, 'Old <b>project</b> & "co" <script>alert(1)</script> 🚀'),
    ),
    granted={
        "spaces/CAROLTEAM": RW,
        "spaces/UNATTEND1": WU,
        "spaces/ORPHAN001": R,
    },
    suspended_by_policy=frozenset({"spaces/UNATTEND1"}),
    list_spaces_open={
        "spaces/CAROLTEAM": (True, True),
        "spaces/UNATTEND1": (False, True),
        "spaces/ORPHAN001": (True, False),
    },
    list_spaces_restricted={
        "spaces/CAROLTEAM": (True, True),
        "spaces/ORPHAN001": (True, False),
    },
    visible_space_ids=frozenset({"CAROLTEAM", "UNATTEND1", "DMENTRY01", "GROUPCHT1"}),
)

# --- dave: written by a newer schema; the reader must fail closed ------------

DAVE = FixtureUser(
    name="dave",
    sub="100000000000000000004",
    email="dave@example.com",
    user_doc=_user_doc("dave@example.com", seen=T1),
    # Not built with schema.py on purpose: a newer writer's document.
    acl_doc={
        "schema_version": schema.SCHEMA_VERSION + 1,
        "updated_at": T1,
        "entries": {
            "NEWSCHEMA": {
                "read": True,
                "write": False,
                "unattended": False,
                "space_type": "SPACE",
                "display_name": "Written by a newer version",
                "updated_at": T1,
                "new_field": {"anything": True},
            }
        },
    },
    state="unreadable",
    visible_space_ids=frozenset({"NEWSCHEMA"}),
)

USERS: tuple[FixtureUser, ...] = (ALICE, BOB, CAROL, DAVE)
BY_NAME: dict[str, FixtureUser] = {u.name: u for u in USERS}

# Documents that must never be readable, one per way of being wrong. They are
# not users and are not seeded: tests feed them to the reader directly.
MALFORMED_ACL_DOCS: dict[str, dict] = {
    "no_permission": {
        "schema_version": 1,
        "entries": {
            "GOODENTRY": {**ALICE.acl_doc["entries"]["AAABBBCCC"]},
            "NOPERMSIO": {
                "read": False, "write": False, "unattended": False,
                "space_type": "SPACE", "display_name": "x", "updated_at": T1,
            },
        },
    },
    "unattended_without_write": {
        "schema_version": 1,
        "entries": {
            "BADUNATTD": {
                "read": True, "write": False, "unattended": True,
                "space_type": "SPACE", "display_name": "x", "updated_at": T1,
            }
        },
    },
    "flag_not_boolean": {
        "schema_version": 1,
        "entries": {
            "BADFLAG01": {
                "read": "true", "write": False, "unattended": False,
                "space_type": "SPACE", "display_name": "x", "updated_at": T1,
            }
        },
    },
    "invalid_space_id": {
        "schema_version": 1,
        "entries": {"not a valid id": {**ALICE.acl_doc["entries"]["AAABBBCCC"]}},
    },
}


def documents(users: tuple[FixtureUser, ...] = USERS) -> dict[str, dict]:
    """Every document of the dataset by its Firestore path, ready to be written.

    Audit documents get fixed IDs (`audit01`, `audit02`, ...) so a second seed
    overwrites the first instead of adding to it. The documents are copies: a
    test that edits one does not change the dataset.
    """
    docs: dict[str, dict] = {}
    for u in users:
        if u.user_doc is not None:
            docs[f"users/{u.sub}"] = u.user_doc
        if u.acl_doc is not None:
            docs[f"acls/{u.sub}"] = u.acl_doc
        for i, audit in enumerate(u.audit_docs, start=1):
            docs[f"acls/{u.sub}/audit/audit{i:02d}"] = audit
    return copy.deepcopy(docs)
