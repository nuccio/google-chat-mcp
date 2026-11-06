"""
Permissions of a user on Google Chat spaces: the model shared by both modes.

`Permissions` says what is allowed on one space. `Acl` maps space names to
`Permissions` and answers the questions the tools ask. Neither knows where the
permissions come from: `--space` flags (local mode) or the settings page
(remote mode) both produce them.

`r` and `w` are independent: `w` does not imply `r`. `unattended` disables the
per-message confirmation for the space and requires `w`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


class PermissionDeniedError(Exception):
    pass


class InvalidPermissions(ValueError):
    """A permission combination that can never be stored or configured."""


@dataclass(frozen=True)
class Permissions:
    read: bool = False
    write: bool = False
    unattended: bool = False

    def validate(self) -> None:
        """Raises InvalidPermissions unless the combination is allowed:
        at least one of read and write, and unattended only with write."""
        if not (self.read or self.write):
            raise InvalidPermissions("at least one of read and write is required")
        if self.unattended and not self.write:
            raise InvalidPermissions("unattended requires write")

    def as_dict(self) -> dict:
        return {"read": self.read, "write": self.write, "unattended": self.unattended}


class Acl:
    """Immutable mapping from space name to Permissions, with the checks the
    tools apply.

    `not_configured_hint` completes the error for a space that is not in the
    mapping; it differs between modes (where permissions are configured).
    """

    def __init__(
        self, entries: Mapping[str, Permissions], not_configured_hint: str = ""
    ):
        self._entries: Mapping[str, Permissions] = MappingProxyType(dict(entries))
        self._hint = not_configured_hint

    def list_spaces(self) -> list[dict]:
        return [
            {"name": name, "read": p.read, "write": p.write}
            for name, p in self._entries.items()
        ]

    def requires_confirmation(self, space_name: str) -> bool:
        """True unless the space is configured unattended.

        Unknown spaces require confirmation: the safe default.
        """
        p = self._entries.get(space_name)
        return p is None or not p.unattended

    def readable_unattended_spaces(self) -> list[str]:
        """Spaces configured read, write and unattended: content read from them
        can influence posts made without confirmation."""
        return [
            name
            for name, p in self._entries.items()
            if p.read and p.write and p.unattended
        ]

    def _get(self, space_name: str) -> Permissions:
        p = self._entries.get(space_name)
        if p is None:
            message = f"Space {space_name!r} not in allowlist."
            if self._hint:
                message = f"{message} {self._hint}"
            raise PermissionDeniedError(message)
        return p

    def require_read(self, space_name: str) -> None:
        if not self._get(space_name).read:
            raise PermissionDeniedError(
                f"Read permission denied for {space_name!r} (configured with :w only)."
            )

    def require_write(self, space_name: str) -> None:
        if not self._get(space_name).write:
            raise PermissionDeniedError(
                f"Write permission denied for {space_name!r} (configured with :r only)."
            )
