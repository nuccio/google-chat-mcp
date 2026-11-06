"""
Loads the fixed dataset (tests/fixtures/users.py) into the Firestore emulator.

    FIRESTORE_EMULATOR_HOST=127.0.0.1:8686 uv run python -m tests.fixtures.seed

Meant for the emulator only: it refuses to run unless FIRESTORE_EMULATOR_HOST is
set, so it can never write to a real Firestore database. Seeding twice gives the
same result (fixed document IDs, `set` overwrites).
"""

from __future__ import annotations

import os
import sys
import urllib.request

from tests.fixtures import users

PROJECT = "gcm-test"


class NotAnEmulator(RuntimeError):
    pass


def emulator_host() -> str:
    host = os.environ.get("FIRESTORE_EMULATOR_HOST", "")
    if not host:
        raise NotAnEmulator(
            "FIRESTORE_EMULATOR_HOST is not set. Start the emulator "
            "(`gcloud emulators firestore start --host-port=127.0.0.1:8686`) and "
            "export FIRESTORE_EMULATOR_HOST=127.0.0.1:8686. This script never "
            "writes to a real Firestore database."
        )
    return host


def client():
    """A Firestore client bound to the emulator (refuses anything else)."""
    emulator_host()
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import firestore

    return firestore.Client(project=PROJECT, credentials=AnonymousCredentials())


def reset(host: str | None = None) -> None:
    """Deletes every document of the emulator's default database."""
    host = host or emulator_host()
    url = f"http://{host}/emulator/v1/projects/{PROJECT}/databases/(default)/documents"
    with urllib.request.urlopen(urllib.request.Request(url, method="DELETE"), timeout=10):
        pass


def seed(db=None, selected=users.USERS) -> list[str]:
    """Writes the documents of the selected users; returns their paths."""
    db = db or client()
    docs = users.documents(selected)
    for path, data in docs.items():
        db.document(path).set(data)
    return sorted(docs)


def main() -> int:
    try:
        paths = seed()
    except NotAnEmulator as e:
        print(e, file=sys.stderr)
        return 2
    for p in paths:
        print(p)
    print(f"{len(paths)} documents written to the emulator at {emulator_host()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
