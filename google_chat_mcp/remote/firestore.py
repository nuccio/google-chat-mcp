"""
The Firestore client of the remote services.

One database holds the collections of both services (docs/remote/04, "Access
to Firestore by service"). The client is configured from the environment:

- `GOOGLE_CLOUD_PROJECT` (or `GCP_PROJECT`): the project. Optional when
  Application Default Credentials know it.
- `FIRESTORE_DATABASE`: the database ID, `(default)` if unset.
- `FIRESTORE_EMULATOR_HOST`: when set, the client talks to the emulator with
  anonymous credentials (the Firestore library does this by itself).
"""

from __future__ import annotations

import os

DEFAULT_DATABASE = "(default)"
# Project used with the emulator when none is configured; the emulator accepts any.
EMULATOR_PROJECT = "gcm-test"


def using_emulator() -> bool:
    return bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


def create_client(project: str | None = None, database: str | None = None):
    """A `google.cloud.firestore.Client` for the configured project and database.

    Arguments override the environment. Creating the client does not connect.
    """
    from google.cloud import firestore

    project = (
        project
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or (EMULATOR_PROJECT if using_emulator() else None)
    )
    database = database or os.environ.get("FIRESTORE_DATABASE") or DEFAULT_DATABASE
    return firestore.Client(project=project, database=database)
