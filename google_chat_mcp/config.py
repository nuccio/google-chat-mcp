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

from .permissions import Acl, PermissionDeniedError, Permissions

__all__ = ["Acl", "LocalAclProvider", "PermissionDeniedError", "acl_from_args", "parse_space_arg"]

NOT_CONFIGURED_HINT = "Add it with --space in the Claude configuration file."

UNATTENDED = "unattended"
VALID_PERMS = ("r", "w", "rw", "wr")


def parse_space_arg(arg: str) -> tuple[str, Permissions]:
    parts = arg.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(
            f"Invalid --space argument: {arg!r}. "
            "Expected format: spaces/ID:PERMS[:unattended] (e.g. spaces/AAA:rw)"
        )
    name, perms = parts[0], parts[1]
    if not name:
        raise ValueError(f"Empty space name in {arg!r}.")
    if perms not in VALID_PERMS:
        raise ValueError(
            f"Invalid permissions in {arg!r}. Use 'r', 'w', or 'rw'."
        )
    read = "r" in perms
    write = "w" in perms
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
    return name, Permissions(read=read, write=write, unattended=unattended)


def acl_from_args(space_args: list[str]) -> Acl:
    """The Acl described by the --space arguments."""
    return Acl(
        dict(parse_space_arg(a) for a in space_args),
        not_configured_hint=NOT_CONFIGURED_HINT,
    )


class LocalAclProvider:
    """AclProvider backed by --space flags: returns the same Acl on every call."""

    def __init__(self, acl: Acl) -> None:
        self._acl = acl

    async def acl(self) -> Acl:
        return self._acl
