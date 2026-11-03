# 04 — Trying the settings UI with a mocked backend

The `/settings` pages of the remote connector (issue #51) can be tried in a browser before the real server (#52) and the ACL enforcement (#53) exist. They follow the user flow in [03-settings-ui-flow.md](03-settings-ui-flow.md).

> **Development only.** The mock has a fake sign-in: whoever opens the page picks an account from a list. The data lives in memory and is lost when the process stops. Run it on your own machine only.

## Run it

```bash
uv pip install -e ".[remote]"      # or ".[dev]", which includes it
google-chat-mcp settings-mock      # http://127.0.0.1:8765/settings
```

Options: `--port` (default 8765) and `--host` (default `127.0.0.1`; any other address prints a warning, because anyone who can reach it can use the fake sign-in).

Without the CLI, the same app is `google_chat_mcp.settings.mock:create_mock_app`:

```bash
uvicorn --factory google_chat_mcp.settings.mock:create_mock_app --port 8765
```

## What to try

| Account | Shows |
|---|---|
| `alice@example.com` | The list from the mockup: Team General (read, write with approval), Announcements (read), and Old project, a space she is no longer a member of, which only offers **Remove**. "Last used 2 hours ago" in the account bar. |
| `bob@example.com` | The empty state, and the warning "Claude has not used this account yet". |
| `carol@restricted.example` | An administrator policy that forbids write without confirmation: the option is disabled, with the reason. |

Things to look at:

- **Add space** lists two spaces called "Team General" with different resource names, and a space whose name contains HTML and a `<script>`, which must show as plain text everywhere. Direct messages and group chats are not listed; pasting `spaces/DM00001` is refused.
- **Paste a link or ID**: `https://chat.google.com/room/MMM111`, `spaces/MMM111` and `MMM111` all open the Team Marketing permissions page.
- **Read only** on a new space, with no unattended spaces, saves directly. **Write** goes through the confirmation page.
- **Write without confirmation** on Reports: the confirmation page lists the readable spaces that could steer it; with Read checked as well, the strongest warning comes first. **Grant write without confirmation** leads to a "sign in again" page, standing in for Google re-authentication. Choosing another account there cancels the change.
- After that, granting Read on another space warns that its text can make Claude post in Reports.

## How it is built

| File | Role |
|---|---|
| `google_chat_mcp/settings/app.py` | Routes, server-side sessions, CSRF, security headers. No fake sign-in. |
| `google_chat_mcp/settings/rules.py` | The flow rules as pure functions: when to confirm and re-authenticate, the warnings, pasted links. |
| `google_chat_mcp/settings/backends.py` | The interfaces #52 implements: `SpacesSource` (spaces.list / spaces.get with the session's token), `AclStore` (entry and audit record in one transaction), `ConnectorUsage`, `PolicySource`, `SignIn`. |
| `google_chat_mcp/settings/mock.py` | In-memory implementations, fixture data and the development sign-in. Only imported by `settings-mock` and the tests. |
| `google_chat_mcp/settings/templates/`, `static/settings.css` | Jinja2 templates (autoescape on) and one stylesheet. No JavaScript. |

What every response carries: `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on pages. The session cookie is `HttpOnly` and `SameSite=Lax`; it only carries a random ID, and is replaced at every sign-in. In a deployment (#52) it is also `Secure` with the `__Host-` prefix; the mock leaves both out to work over plain HTTP on localhost.

What every POST checks on the server, whatever the form says: the CSRF token of the session; the account from the session only; membership and `spaceType == SPACE`; at least one permission; unattended only with write; the admin policy; for changes that need it, a confirmation made in this session for this exact change, on an ACL that has not changed since; for unattended write, a re-authentication made after the confirmation, within 300 seconds.

The tests are in `tests/test_settings_ui.py` and `tests/test_settings_rules.py`.
