# 05 — Trying the settings UI with a mocked backend

The `/settings` pages of the remote connector (issue #51) can be tried in a browser before the real server (#52) and the ACL enforcement (#53) exist. They follow the user flow in [03-settings-ui-flow.md](03-settings-ui-flow.md) and are built with NiceGUI, as decided in [04-settings-server-design.md](04-settings-server-design.md).

> **Development only.** The mock has a fake sign-in: whoever opens the page picks an account from a list. The data lives in memory and is lost when the process stops. Run it on your own machine only.

## Run it

```bash
uv pip install -e ".[remote]"      # or ".[dev]", which includes it
python -m google_chat_mcp.settings.mock      # http://127.0.0.1:8765/settings
```

It is not a subcommand of `google-chat-mcp`: the local CLI does not change while remote mode is built (NFR-7 on #29). Without the `remote` extra the command stops with a message saying so.

Options: `--port` (default 8765) and `--host` (default `127.0.0.1`; any other address prints a warning, because anyone who can reach it can use the fake sign-in).

The mock is served by NiceGUI's own server (`ui.run`). Open it at the address it prints: the websocket only accepts that origin (see below), so the page does not work from another host name.

## What to try

| Account | Shows |
|---|---|
| `alice@example.com` | The list from the mockup: Team General (read, write with approval), Announcements (read), and Old project, a space she is no longer a member of, which only offers **Remove**. "Last used 2 hours ago" in the account bar. |
| `bob@example.com` | The empty state, and the warning "Claude has not used this account yet". |
| `carol@restricted.example` | An administrator policy that forbids write without confirmation: the option is not offered, with the reason. |

Things to look at:

- **Add space** lists two spaces called "Team General" with different resource names, and a space whose name contains HTML and a `<script>`, which must show as plain text everywhere. The search box filters as you type. Direct messages and group chats are not listed; pasting `spaces/DM00001` is refused.
- **Paste a link or ID**: `https://chat.google.com/room/MMM111`, `spaces/MMM111` and `MMM111` all open the Team Marketing permissions page.
- **Read only** on a new space, with no unattended spaces, saves directly. **Write** goes through the confirmation page.
- **Write without confirmation** on Reports: the confirmation page lists the readable spaces that could steer it; with Read checked as well, the strongest warning comes first. **Grant write without confirmation** saves it: there is no re-authentication (decision on #29).
- After that, granting Read on another space warns that its text can make Claude post in Reports.
- **Direct messages** tab: only a "not available yet" notice (decision on #29).
- **History of changes**: the audit records of the signed-in account.

## How it is built

| File | Role |
|---|---|
| `google_chat_mcp/settings/app.py` | `SettingsApp` (flow logic and server-side sessions, no UI), and `install()`: the NiceGUI pages, the plain routes that set or clear the session cookie, and the two middlewares. No fake sign-in. |
| `google_chat_mcp/settings/rules.py` | The flow rules as pure functions: when to confirm, the warnings, pasted links. |
| `google_chat_mcp/settings/backends.py` | The interfaces #52 implements: `SpacesSource` (spaces.list / spaces.get with the session's token), `AclStore` (entry and audit record in one transaction), `ConnectorUsage`, `PolicySource`, `SignIn`. |
| `google_chat_mcp/settings/mock.py` | In-memory implementations, fixture data and the development sign-in. Only imported by `python -m google_chat_mcp.settings.mock` and the tests. |

**Cookies.** NiceGUI events travel over a websocket and cannot set cookies, so only plain HTTP routes do: `/settings/sign-in` and `/settings/switch-account` (start a sign-in), the sign-in callback (the development sign-in exchanges a one-time ticket bound to the browser's session at `/settings/dev/complete`), and `/settings/signed-out` (clears the cookie after **Sign out** has deleted the session). The cookie is `HttpOnly` and `SameSite=Lax`, only carries a random ID, and is replaced at every sign-in. In a deployment (#52) it is also `Secure` with the `__Host-` prefix; the mock leaves both out to work over plain HTTP on localhost.

**Every response** carries the CSP of the design document (`'unsafe-inline'` and `'unsafe-eval'` are required by NiceGUI; `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'none'`, `connect-src 'self'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, and `Cache-Control: no-store` except on NiceGUI's versioned static files.

**Websocket Origin check.** NiceGUI's Socket.IO server accepts any origin. A middleware rejects every request to `/_nicegui_ws/` whose `Origin` is not the settings service's own, and every request without `Origin`. This replaces the CSRF tokens of a form-based app.

**Every change is checked on the server**, whatever the page sent: the session is re-read on every event; the account comes from the session only; membership and `spaceType == SPACE`; at least one permission; unattended only with write; the admin policy; for changes that need it, a confirmation made in this session for this exact change, within 10 minutes, on an ACL that has not changed since.

**Rendering rules.** Text from the Chat API, the ACL or the user only goes into `ui.label`, input values and checkbox or radio labels. The settings code never uses `ui.html`, `ui.markdown`, `ui.run_javascript`, `ui.add_*_html` or `:`-prefixed props; a test checks it.

**Not in the mock:** closing a tab's websocket after 10 minutes without events (#52, to bound Cloud Run instance time).

The tests are in `tests/test_settings_ui.py` (NiceGUI's user simulation, no browser) and `tests/test_settings_rules.py`. `tests/test_local_mode.py` checks the NFR-7 criteria: local mode starts over stdio, its CLI is unchanged, and it imports and loads nothing from the settings package.
