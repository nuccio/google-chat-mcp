"""In-memory doubles of the remote readers, for tests."""

from __future__ import annotations

import copy


class InMemoryAclReader:
    """An `AclReader` over a dict of `acls/{sub}` documents. Counts the reads."""

    def __init__(self, documents: dict[str, dict] | None = None) -> None:
        self.documents: dict[str, dict] = documents or {}
        self.reads: list[str] = []

    @classmethod
    def from_dataset(cls, documents: dict[str, dict]) -> "InMemoryAclReader":
        """From `users.documents()`: the `acls/{sub}` documents, by `sub`."""
        prefix = "acls/"
        return cls(
            {
                path[len(prefix):]: doc
                for path, doc in documents.items()
                if path.startswith(prefix) and "/" not in path[len(prefix):]
            }
        )

    async def read(self, sub: str) -> dict | None:
        self.reads.append(sub)
        doc = self.documents.get(sub)
        return None if doc is None else copy.deepcopy(doc)
