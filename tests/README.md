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

## Integration tests

Integration tests call the real Google Chat API and require:

1. A valid OAuth token at `~/.config/google-chat-mcp/token.json` (run `uv run google-chat-mcp auth` once to obtain it)
2. The test environment below, with its space IDs in `.env`

A test whose space variable is not set is skipped; the others still run.

```bash
uv run pytest -m integration
```

## Test environment

[`test_env.toml`](test_env.toml) describes the spaces, what each one is configured as in the tests, and the `.env` variable holding its ID. It is the only place that says so: `configured_server` in `test_integration.py` builds its configuration from it. Permissions are set in the server configuration, not on the space, so a space exists only where a role has to be kept apart from the others.

### Prerequisites

- Add the account that runs the setup as a test user on the OAuth consent screen, and add these scopes to the Data Access list: `chat.spaces.create`, `chat.spaces.readonly`, `chat.messages`. The server does not request `chat.spaces.create`. While the app is in "Testing" status, refresh tokens expire after 7 days.
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
