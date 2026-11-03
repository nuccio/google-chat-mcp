"""Plain data types shared by the settings pages and their backends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# The only spaceType that can be enabled in v1 (decision D4).
SPACE = "SPACE"


class InvalidPermissions(ValueError):
    """A permission combination that can never be stored."""


@dataclass(frozen=True)
class Account:
    """The signed-in Google account. `sub` is the stable identifier."""

    sub: str
    email: str
    hd: str = ""


@dataclass(frozen=True)
class Space:
    """A Google Chat space as returned by spaces.list / spaces.get."""

    name: str  # resource name, e.g. "spaces/AAABBBCCC"
    display_name: str
    space_type: str  # SPACE, GROUP_CHAT or DIRECT_MESSAGE


@dataclass(frozen=True)
class Permissions:
    """Same model as `--space NAME:PERMS[:unattended]` in local mode."""

    read: bool = False
    write: bool = False
    unattended: bool = False

    def validate(self) -> None:
        # Same rules as config.parse_space_arg, and the CHECK constraint of
        # the data model: at least one permission, unattended requires write.
        if not (self.read or self.write):
            raise InvalidPermissions("at least one of read and write is required")
        if self.unattended and not self.write:
            raise InvalidPermissions("unattended requires write")

    def as_dict(self) -> dict:
        return {"read": self.read, "write": self.write, "unattended": self.unattended}


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


@dataclass(frozen=True)
class Policy:
    """What the domain administrator allows. A False flag disables the option."""

    allow_read: bool = True
    allow_write: bool = True
    allow_unattended: bool = True

    def allows(self, p: Permissions) -> bool:
        return (
            (self.allow_read or not p.read)
            and (self.allow_write or not p.write)
            and (self.allow_unattended or not p.unattended)
        )
