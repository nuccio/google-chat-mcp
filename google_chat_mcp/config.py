"""
Parsing e validazione degli argomenti --space.

Formato: <resource_name>:<permessi>
  spaces/AAABBBCCC:r   → sola lettura
  spaces/AAABBBCCC:w   → sola scrittura
  spaces/AAABBBCCC:rw  → lettura e scrittura
"""

from dataclasses import dataclass


class PermissionDeniedError(Exception):
    pass


@dataclass
class SpaceEntry:
    name: str
    read: bool
    write: bool


def parse_space_arg(arg: str) -> SpaceEntry:
    if ":" not in arg:
        raise ValueError(
            f"Argomento --space non valido: {arg!r}. "
            "Formato atteso: spaces/ID:PERMS (es. spaces/AAA:rw)"
        )
    name, perms = arg.rsplit(":", 1)
    if not name:
        raise ValueError(f"Nome spazio vuoto in {arg!r}.")
    read = "r" in perms
    write = "w" in perms
    if not read and not write:
        raise ValueError(
            f"Permessi non validi in {arg!r}. Usa 'r', 'w', o 'rw'."
        )
    return SpaceEntry(name=name, read=read, write=write)


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

    def require_read(self, space_name: str) -> None:
        entry = self._entries.get(space_name)
        if entry is None:
            raise PermissionDeniedError(
                f"Spazio {space_name!r} non in allowlist. "
                "Aggiungilo con --space nel file di configurazione Claude."
            )
        if not entry.read:
            raise PermissionDeniedError(
                f"Permesso di lettura negato per {space_name!r} (configurato solo con :w)."
            )

    def require_write(self, space_name: str) -> None:
        entry = self._entries.get(space_name)
        if entry is None:
            raise PermissionDeniedError(
                f"Spazio {space_name!r} non in allowlist. "
                "Aggiungilo con --space nel file di configurazione Claude."
            )
        if not entry.write:
            raise PermissionDeniedError(
                f"Permesso di scrittura negato per {space_name!r} (configurato solo con :r)."
            )
