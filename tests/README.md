# Test suite

## Setup

Install development dependencies:

```bash
uv pip install -e ".[dev]"
```

## Unit tests

Run without any configuration — no OAuth token, no real API calls:

```bash
pytest
```

## Integration tests

Integration tests call the real Google Chat API and require:

1. A valid OAuth token at `~/.config/google-chat-mcp/token.json` (run `google-chat-mcp auth` once to obtain it)
2. Three Google Chat spaces configured via environment variables

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| `CHAT_READ_WRITE_SPACE` | Space configured with read+write access (e.g. `spaces/AAABBBCCC`) |
| `CHAT_READ_ONLY_SPACE` | Space configured with read-only access (e.g. `spaces/DDDEEEFFF`) |
| `CHAT_UNCONFIGURED_SPACE` | Space that exists on Google Chat but is **not** in the server config — all operations on it must be blocked (e.g. `spaces/GGGHHH111`) |

Run integration tests:

```bash
pytest -m integration
```

## Fixtures

`fixtures/rw_space_known_messages.json` — list of message texts expected to be present in `CHAT_READ_WRITE_SPACE`. The set is stable across runs because tests only add messages, never delete them. Update this file when you manually add known messages to the space.
