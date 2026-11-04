"""
The rules of the settings flow, as pure functions (flow steps 3, 5, 6 and 7).

Kept free of HTTP and of the UI framework, so they can be tested on their
own and reused by #52.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .models import AclEntry, Permissions

# Resource IDs as Google returns them. Anything else is refused before any
# lookup, so it never reaches a template, a log line or an API URL.
SPACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_REF_PATTERNS = [
    re.compile(r"(?:^|/)spaces/([A-Za-z0-9_-]+)"),
    re.compile(r"/room/([A-Za-z0-9_-]+)"),
    re.compile(r"/space/([A-Za-z0-9_-]+)"),
]


def parse_space_ref(ref: str) -> str | None:
    """
    Extract `spaces/ID` from a pasted Google Chat link, resource name or bare ID.

    Returns None when nothing that looks like a space ID is found.
    """
    ref = ref.strip()
    if not ref:
        return None
    for pattern in _REF_PATTERNS:
        m = pattern.search(ref)
        if m and SPACE_ID_RE.match(m.group(1)):
            return f"spaces/{m.group(1)}"
    if SPACE_ID_RE.match(ref):
        return f"spaces/{ref}"
    return None


def permission_labels(p: Permissions) -> list[str]:
    """Labels of the list page. Unattended write is a permission of its own."""
    labels = []
    if p.read:
        labels.append("Read")
    if p.write:
        labels.append("Write without confirmation" if p.unattended else "Write with approval")
    return labels


@dataclass(frozen=True)
class Requirements:
    confirmation: bool


def requirements(
    before: Permissions | None, after: Permissions, acl_after: list[AclEntry]
) -> Requirements:
    """
    The table in flow step 6.

    Only what the change *adds* is considered: removing permissions, or
    turning unattended write back into write with approval, needs nothing.
    There is no re-authentication (decision on #29): the confirmation page
    is the only extra step.
    """
    b = before or Permissions()
    new_read = after.read and not b.read
    new_write = after.write and not b.write
    new_unattended = after.unattended and not b.unattended
    any_unattended = any(e.permissions.unattended for e in acl_after)
    return Requirements(
        confirmation=new_unattended or new_write or (new_read and any_unattended),
    )


@dataclass(frozen=True)
class RiskWarning:
    """
    One warning of the confirmation page.

    kind:
      - "same_space": read and unattended write on the same space (strongest);
      - "unattended_readers": granting unattended write while other entries
        are readable; `spaces` lists the readable ones;
      - "read_reaches_unattended": granting read while unattended spaces
        exist; `spaces` lists the unattended ones.
    """

    kind: str
    spaces: list[str] = field(default_factory=list)


def acl_with_change(
    acl: list[AclEntry], space_name: str, after: Permissions | None, display_name: str = ""
) -> list[AclEntry]:
    """The ACL as it would be after the change (`after=None` removes the entry)."""
    result = [e for e in acl if e.space_name != space_name]
    if after is not None:
        result.append(AclEntry(space_name, after, display_name))
    return result


def warnings(
    space_name: str,
    before: Permissions | None,
    after: Permissions,
    acl_after: list[AclEntry],
    names: dict[str, str],
) -> list[RiskWarning]:
    """
    Warnings computed on the whole ACL after the change, strongest first.

    `names` maps resource names to display names; unknown ones are shown
    by resource name.
    """
    b = before or Permissions()
    new_read = after.read and not b.read
    new_unattended = after.unattended and not b.unattended
    others = [e for e in acl_after if e.space_name != space_name]

    def label(e: AclEntry) -> str:
        return names.get(e.space_name) or e.display_name or e.space_name

    result: list[RiskWarning] = []
    if after.read and after.unattended and (new_read or new_unattended):
        result.append(RiskWarning("same_space"))
    if new_unattended:
        readers = sorted(label(e) for e in others if e.permissions.read)
        if readers:
            result.append(RiskWarning("unattended_readers", readers))
    if new_read:
        unattended = sorted(label(e) for e in others if e.permissions.unattended)
        if unattended:
            result.append(RiskWarning("read_reaches_unattended", unattended))
    return result


def acl_fingerprint(acl: list[AclEntry]) -> str:
    """
    A digest of the whole ACL. A confirmation is only valid for the ACL it
    was shown on, because the warnings depend on every entry.
    """
    rows = sorted(
        f"{e.space_name}:{int(e.permissions.read)}{int(e.permissions.write)}"
        f"{int(e.permissions.unattended)}"
        for e in acl
    )
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()
