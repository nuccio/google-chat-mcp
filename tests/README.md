# Test suite

## Setup

From a repository checkout, install the development dependencies:

```bash
uv sync --extra dev
```

The commands below are run through `uv run`, which uses the `.venv` that `uv sync` created. If you activate `.venv` yourself, leave `uv run` out.

## Unit tests

Run without any configuration — no OAuth token, no real API calls:

```bash
uv run pytest
```

They also run on GitHub, on every pull request and every push to `main`, with Python 3.11, 3.12 and 3.13 (the **Tests** workflow, [`.github/workflows/tests.yml`](../.github/workflows/tests.yml)). When they pass on `main`, the workflow moves the `latest` tag to that commit.

## Fixed dataset of the remote services

[`fixtures/users.py`](fixtures/users.py) is the one set of users, ACLs and audit records for the tests of the remote services (in memory, Firestore emulator, contract tests between `gcm-settings` and `gcm-mcp`). Tests use it instead of inventing documents. The values are made up (`@example.com`, numeric `sub`s, space IDs that exist nowhere).

| User | What it is |
|---|---|
| alice | The normal case: `rw`, `r`, `w` with confirmation and `w:unattended`, with two audit records |
| bob | A new user: a `users` document and no ACL |
| carol | Entries that must not be granted: a direct message and a group chat, an entry the restricted policy suspends, and an orphan (a space she has left) with an untrusted name |
| dave | An ACL written by a newer schema version: the reader must fail closed |

- Documents are built with `google_chat_mcp.schema`, never by hand (dave's are the exception). `documents()` returns them by Firestore path, as copies.
- The expected values (what is granted, what `list_spaces` returns under the open and the restricted policy, which spaces are visible) are literals next to the data. `test_fixture_users.py` checks them against the schema, `Acl` and the policy rules, so they cannot drift.
- `MALFORMED_ACL_DOCS` holds documents that must never be readable, one per way of being wrong. They belong to no user.

## Firestore emulator tests

The tests that need Firestore (`tests/test_emulator.py`, `tests/test_firestore_backends.py` for the settings backends, `tests/test_remote_firestore_client.py`) run against the **Firestore emulator**, never against a real database. They carry the `emulator` marker and are left out of a plain `uv run pytest`.

One-time setup: a Java runtime (for example `brew install --cask temurin`) and the emulator component, `gcloud components install cloud-firestore-emulator`. The Firebase CLI is not needed.

```bash
gcloud emulators firestore start --host-port=127.0.0.1:8686
```

In another terminal:

```bash
export FIRESTORE_EMULATOR_HOST=127.0.0.1:8686
uv run pytest -m emulator
```

- Without the variable, or with no emulator behind it, the tests **fail** with a message saying so; they never skip, so a run meant to test Firestore cannot pass without doing it.
- Every test starts on an empty database (the `firestore_db` fixture resets it).
- To look at the dataset by hand, load it with `uv run python -m tests.fixtures.seed` and open the emulator's data with any Firestore client pointed at the same address. The script refuses to run without `FIRESTORE_EMULATOR_HOST`, so it cannot write to a real database. Seeding twice gives the same result.
- In CI, the **Firestore emulator** job starts the emulator from a pinned `google-cloud-cli` image and runs `pytest -m emulator`. `latest` moves only if it passes too.

## Integration tests

Integration tests call the real Google Chat API and require:

1. A valid OAuth token at `~/.config/google-chat-mcp/token.json` (run `uv run google-chat-mcp auth` once to obtain it)
2. The test environment below, with its space IDs in `.env`

A test whose space variable is not set is skipped; the others still run.

```bash
uv run pytest -m integration
```

## GCP tests

`tests/test_gcp.py` (marker `gcp`) looks at a real GCP project with your own `gcloud` login and only reads: the project's parent (production), the enabled APIs, an enabled version in each secret container, and the Cloud Run services being public. They are left out of every normal run and of CI. See [docs/remote/08-operator-setup.md](../docs/remote/08-operator-setup.md#step-8--check-the-setup).

```bash
GCM_GCP_PROJECT=my-dev-project GCM_GCP_ENV=dev uv run pytest -m gcp
```

`GCM_GCP_ENV` is `dev` or `prod` (the organisation test is skipped in `dev`). Add `GCM_GCP_DEPLOYED=1` and `GCM_GCP_REGION=…` after the services are deployed. Without the project variable the tests fail, they do not skip.

## Test environment

[`test_env.toml`](test_env.toml) describes the spaces, what each one is configured as in the tests, and the `.env` variable holding its ID. It is the only place that says so: `configured_server` in `test_integration.py` builds its configuration from it. Permissions are set in the server configuration, not on the space, so a space exists only where a role has to be kept apart from the others.

### Prerequisites

- Add the account that runs the setup as a test user on the OAuth consent screen, and add these scopes to the Data Access list: `chat.spaces.create`, `chat.spaces.readonly`, `chat.messages`. The server does not request `chat.spaces.create` or the full `chat.messages`, and needs its own scopes on the list too (see [docs/google-cloud.md](../docs/google-cloud.md#2-oauth-consent)). While the app is in "Testing" status, refresh tokens expire after 7 days.
- **Use the same account for the setup and for the integration tests**, or the tests will not reach the spaces. The two tokens are separate and the setup cannot read the server's, so nothing checks this for you.

### Running the setup

From a repository checkout:

```bash
uv run python -m tests.setup_env
```

The first run opens the browser for consent and saves its own token at `~/.config/google-chat-mcp-test-setup/token.json`. The setup is not part of the published package and the server cannot use this token.

| Found | What the setup does |
|---|---|
| Space missing | Creates it |
| One space with that display name | Reuses it |
| More than one | Stops with an error, without choosing |
| `spaces.create` answers `ALREADY_EXISTS` | Stops with an error: the name may be taken by a space the account cannot see |
| A `CHAT_*` variable points to a different space | Stops before changing anything |
| `CHAT_DIRECT_MESSAGE_SPACE` not set | Prints the instructions below and carries on |
| `CHAT_DIRECT_MESSAGE_SPACE` set, but not a direct message | Stops before changing anything |
| Seed message missing / present | Posts it / nothing |

Spaces are matched by display name only, so spaces with other names are ignored, never renamed. The setup never deletes spaces or changes members, and in `.env` it only updates the variables of the named spaces. If the account cannot create spaces through the API, create them by hand with the exact names in `test_env.toml` and run the setup again: it finds them.

### Direct message

The tests check that the send tools refuse a real direct message, including when the configuration allows the send. This conversation is opened by hand:

1. From the account that runs the tests, open a direct message with an account **dedicated to testing, never someone's main account** or a conversation with a real person. A bug in the block would send it a real message, and the setup can only check that the ID is a direct message, not who it is with.
2. Send one message, so that the conversation exists.
3. Open it in the browser. The URL ends in `/dm/<ID>`: `spaces/<ID>` is its space ID. (`google-chat-mcp spaces` does not help: it shows every direct message as `(no name)`.)
4. Put it in `CHAT_DIRECT_MESSAGE_SPACE` in `.env`.

The dedicated account's address does not belong in any file: only the space ID is needed.

### Migrating an existing `.env`

`CHAT_READ_ONLY_SPACE` and `CHAT_UNCONFIGURED_SPACE` keep their names but point to the old, hand-made spaces, so the first run stops on them. Before running the setup, remove those two and `CHAT_READ_WRITE_SPACE`, which is no longer used.

## Fixtures

`fixtures/read_only_seed_messages.json` — the messages the setup posts in `mcp-test-read-only`. Nothing else writes to that space, so ordering, `page_size`, pagination and known-content checks run against it.
