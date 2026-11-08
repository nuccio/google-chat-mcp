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

- `google_chat_mcp/`: `__main__.py` (CLI), `tools.py` (the MCP tools, built by `create_server(provider, chat)`), `interfaces.py` (`AclProvider`, `ChatSource`), `server.py` (local builder: `--space` and `token.json` into `create_server`), `auth.py` (OAuth, `LocalChatSource`), `permissions.py` (`Permissions`, `Acl`, shared by both modes), `schema.py` (Firestore document schema of the remote services, plain data), `config.py` (`--space` parsing, `LocalAclProvider`), `chat/` (API client), `log_masking.py`, `settings/` (NiceGUI settings UI of the remote connector).
- `tests/`: pytest; `tests/README.md` explains the integration setup.
- `docs/`: user docs; `docs/remote/` holds the design of the remote connector (numbered, read in order); `docs/maintainer-agent-workflow.md` holds the rules of the maintainer's Claude sessions.

## Rules that matter

- **Security first.** Per-space allowlist, no direct messages, `send_message_unattended` only for spaces marked `:unattended`, narrowest OAuth scopes. Never weaken these without an issue agreed with the maintainer. See `SECURITY.md`.
- **Never commit or print secrets**: `token.json`, `client_secrets.json`, `.env`. Only the `*.example.*` files are tracked. Don't read real credential files from `~/.config/google-chat-mcp/`.
- Message text and emails must not leak into logs: use the masking in `log_masking.py`.
- Settings UI: text from the Chat API or the user goes only into `ui.label`, input values and checkbox/radio labels. Never `ui.html`, `ui.markdown`, `ui.run_javascript` (a test enforces it).
- Update `README.md` / `docs/` when behaviour or configuration changes. Add tests with every change.

## Workflow

- One branch per issue from `main`; PR against `main` with `Fixes #N`; squash and merge. Commits must be signed. Details in `CONTRIBUTING.md`. Sessions whose preset sets `AGENT_ROLE` use the branch model of `docs/maintainer-agent-workflow.md` instead.
- Before pushing, run `gh api user --jq .login`: it must print `$AGENT_GH_LOGIN`. Without that variable, or on a mismatch, the session has no role and does not push, approve or merge.
- Don't run integration tests or touch real Chat spaces unless asked.
- Don't push, merge or promote `stable` without an explicit request. A coordinator's scope, given by the maintainer, is the explicit request for merging inside that scope, and only for that.

## Agent workflow

A session follows `docs/maintainer-agent-workflow.md` when its preset sets `AGENT_ROLE`. The roles, scope, merge conditions and stop conditions are there.

- A coordinator works alone inside its scope, one approved issue plus its sub-issues: `gh pr merge` and `gh pr review` are not under `ask`. Permission patterns cannot tell a PR's base branch apart, so "a coordinator never approves or merges a PR to `main`" is a written rule.
- Protected areas: a coordinator may merge into the issue branch a PR touching the protected paths listed in that file only when the approved design declares the area, the security check passed and the diff stays inside the declared areas. Changes to `SECURITY.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `docs/maintainer-agent-workflow.md`, `.claude/` and `.github/` reach `main` only through the final PR.
- Never: promote `stable`, push to `main`, use `--admin`, change branch protection or rulesets or these rules, create issues outside the scope, touch real Chat spaces or run integration tests, read or print credentials.

## Editors

VS Code settings live in `.vscode/` (shared: `settings.json`, `extensions.json`, `launch.json`). Project rules belong here and in `.claude/settings.json`, not in `.vscode/`.
