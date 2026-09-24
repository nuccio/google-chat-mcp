"""
Parsing and validation of the --space arguments.

Format: <resource_name>:<permissions>[:unattended]
  spaces/AAABBBCCC:r             → read only
  spaces/AAABBBCCC:w             → write only, each send confirmed by the user
  spaces/AAABBBCCC:rw            → read and write, each send confirmed by the user
  spaces/AAABBBCCC:w:unattended  → write only, sends without confirmation

`r` and `w` are independent: `w` does not imply `r`.
The optional `unattended` marker disables the per-message confirmation for
that space (for scheduled/automated tasks) and requires `w`.
"""

from dataclasses import dataclass


class PermissionDeniedError(Exception):
    pass


@dataclass
class SpaceEntry:
    name: str
    read: bool
    write: bool
    require_confirmation: bool = True


UNATTENDED = "unattended"


def parse_space_arg(arg: str) -> SpaceEntry:
    parts = arg.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(
            f"Invalid --space argument: {arg!r}. "
            "Expected format: spaces/ID:PERMS[:unattended] (e.g. spaces/AAA:rw)"
        )
    name, perms = parts[0], parts[1]
    if not name:
        raise ValueError(f"Empty space name in {arg!r}.")
    read = "r" in perms
    write = "w" in perms
    if not read and not write:
        raise ValueError(
            f"Invalid permissions in {arg!r}. Use 'r', 'w', or 'rw'."
        )
    unattended = False
    if len(parts) == 3:
        if parts[2] != UNATTENDED:
            raise ValueError(
                f"Invalid marker {parts[2]!r} in {arg!r}. "
                f"The only accepted marker is {UNATTENDED!r}."
            )
        if not write:
            raise ValueError(
                f"{arg!r}: ':{UNATTENDED}' only applies to writes, "
                "but the space has no 'w' permission."
            )
        unattended = True
    return SpaceEntry(
        name=name, read=read, write=write, require_confirmation=not unattended
    )


class SpaceConfig:
    def __init__(self, entries: list[SpaceEntry]):
        self._entries: dict[str, SpaceEntry] = {e.name: e for e in entries}

    @classmethod
    def from_args(cls, space_args: list[str]) -> "SpaceConfig":
        return cls([parse_space_arg(a) for a in space_args])

    def list_spaces(self) -> list[dict]:
        return [
            {"name": e.name, "read": e.read, "write": e.write}
            for e in self._entries.values()
        ]

    def requires_confirmation(self, space_name: str) -> bool:
        """True unless the space is configured with ':unattended'.

        Unknown spaces require confirmation: the safe default.
        """
        entry = self._entries.get(space_name)
        return entry is None or entry.require_confirmation

    def readable_unattended_spaces(self) -> list[str]:
        """Spaces configured ':rw:unattended': content read from them can
        influence posts made without confirmation."""
        return [
            e.name
            for e in self._entries.values()
            if e.read and e.write and not e.require_confirmation
        ]

    def require_read(self, space_name: str) -> None:
        entry = self._entries.get(space_name)
        if entry is None:
            raise PermissionDeniedError(
                f"Space {space_name!r} not in allowlist. "
                "Add it with --space in the Claude configuration file."
            )
        if not entry.read:
            raise PermissionDeniedError(
                f"Read permission denied for {space_name!r} (configured with :w only)."
            )

    def require_write(self, space_name: str) -> None:
        entry = self._entries.get(space_name)
        if entry is None:
            raise PermissionDeniedError(
                f"Space {space_name!r} not in allowlist. "
                "Add it with --space in the Claude configuration file."
            )
        if not entry.write:
            raise PermissionDeniedError(
                f"Write permission denied for {space_name!r} (configured with :r only)."
            )
