"""Plain data types shared by the settings pages and their backends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..permissions import InvalidPermissions, Permissions
from ..remote.policy import Policy

__all__ = ["InvalidPermissions", "Permissions", "Policy"]

# The only spaceType that can be enabled in v1 (decision D4).
SPACE = "SPACE"


@dataclass(frozen=True)
class Account:
    """The signed-in Google account. `sub` is the stable identifier."""

    sub: str
    email: str
    hd: str = ""
    access_token: str = ""


@dataclass(frozen=True)
class Space:
    """A Google Chat space as returned by spaces.list / spaces.get."""

    name: str  # resource name, e.g. "spaces/AAABBBCCC"
    display_name: str
    space_type: str  # SPACE, GROUP_CHAT or DIRECT_MESSAGE


@dataclass(frozen=True)
class AclEntry:
    space_name: str
    permissions: Permissions
    # Snapshot of the display name at save time, only used as a label for
    # entries Google no longer returns (orphans). Never used for decisions.
    display_name: str = ""
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AuditRecord:
    sub: str
    space_name: str
    before: Permissions | None  # None: the entry did not exist
    after: Permissions | None  # None: the entry was removed
    changed_at: datetime
