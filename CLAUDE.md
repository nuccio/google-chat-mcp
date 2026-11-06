# google-chat-mcp

MCP server for Google Chat with per-space access control. Python >=3.11, FastMCP, managed with `uv`.

## Language

- **Talk to the maintainer in Italian**: replies, questions, explanations, summaries in chat.
- **Everything written into the project is in English**: documentation, issues, pull requests, commit messages, log messages, code comments, docstrings, identifiers and error messages.

## Commands

```bash
uv sync --extra dev                      # create .venv, install from uv.lock
uv run pytest                            # unit tests (integration tests are excluded by default)
uv run pytest -m integration             # real Google Chat API, needs a token: see tests/README.md
uv run google-chat-mcp --help            # local CLI (auth, spaces, server over stdio)
uv run python -m google_chat_mcp.settings.mock   # settings UI with a mocked backend, http://127.0.0.1:8765/settings
```

After changing dependencies in `pyproject.toml`, run `uv lock` and commit `uv.lock`.

## Layout

- `google_chat_mcp/`: `__main__.py` (CLI), `server.py` (MCP tools), `auth.py` (OAuth), `permissions.py` (`Permissions`, `Acl`, shared by both modes), `schema.py` (Firestore document schema of the remote services, plain data), `config.py` (`--space` parsing), `chat/` (API client), `log_masking.py`, `settings/` (NiceGUI settings UI of the remote connector).
- `tests/`: pytest; `tests/README.md` explains the integration setup.
- `docs/`: user docs; `docs/remote/` holds the design of the remote connector (numbered, read in order).

## Rules that matter

- **Security first.** Per-space allowlist, no direct messages, `send_message_unattended` only for spaces marked `:unattended`, narrowest OAuth scopes. Never weaken these without an issue agreed with the maintainer. See `SECURITY.md`.
- **Never commit or print secrets**: `token.json`, `client_secrets.json`, `.env`. Only the `*.example.*` files are tracked. Don't read real credential files from `~/.config/google-chat-mcp/`.
- Message text and emails must not leak into logs: use the masking in `log_masking.py`.
- Settings UI: text from the Chat API or the user goes only into `ui.label`, input values and checkbox/radio labels. Never `ui.html`, `ui.markdown`, `ui.run_javascript` (a test enforces it).
- Update `README.md` / `docs/` when behaviour or configuration changes. Add tests with every change.

## Workflow

- One branch per issue from `main`; PR against `main` with `Fixes #N`; squash and merge. Commits must be signed. Details in `CONTRIBUTING.md`.
- Don't run integration tests or touch real Chat spaces unless asked.
- Don't push, merge or promote `stable` without an explicit request.

## Editors

VS Code settings live in `.vscode/` (shared: `settings.json`, `extensions.json`, `launch.json`). Project rules belong here and in `.claude/settings.json`, not in `.vscode/`.
