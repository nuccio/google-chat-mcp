# 02 — Direction: per-user configuration via a settings page

**Decision:** each user configures their spaces and permissions on a settings web page (`/settings`) hosted by the server. They sign in with Google, using the same identity as the connector. This is option A of [01-requirements-analysis.md](01-requirements-analysis.md#2-how-each-user-chooses-spaces-and-permissions).

This document re-examines the three questions from issue #29 in the light of that decision. The library references are to FastMCP 4.0.9.

---

## Architecture

One service, two entry points, two separate authentication mechanisms:

```
                 ┌────────────────────── single service ─────────────────────┐
Claude ──OAuth──▶│ /mcp       FastMCP token (JWT)  → sub → ACL → Chat API    │
                 │            (GoogleProvider / OAuthProxy)                  │
Browser ─OIDC───▶│ /settings  session cookie       → sub → ACL (read/write)  │
                 └───────────────┬───────────────────────────────┬──────────┘
                                 ▼                               ▼
                      Google token store (encrypted)     space_acl + audit log,
                      managed by OAuthProxy              keyed by sub
```

The two sides share only the Google `sub`.

### Consequence: the settings page stores no Google credentials

In FastMCP, `OAuthProxy` stores upstream Google tokens per issued token (`upstream_token_id`), not per user. So `/settings` cannot look up "this user's refresh token" in the proxy's store. That leads to a simplification:

- **`/settings` keeps no Google credentials.** It does a standard OIDC login:
  - scopes: `openid email` + `chat.spaces.readonly`, with `access_type=online`;
  - the access token is used to list the user's spaces during the session, then discarded.
- **The only per-user data the server owns is the ACL**, plus its audit log. The Chat tokens stay in the encrypted `OAuthProxy` store. That store needs a persistent backend (`client_storage`) instead of the default local file store.
- The user has already granted `chat.spaces.readonly` in the connector flow, so Google should usually skip the consent screen. *To be verified during implementation.*

Compared with the data model in 01, the `google_creds` table is no longer needed.

---

## 1. Single instance, re-examined

- **Google OAuth client:** one "Web application" client is enough, with two redirect URIs: the proxy callback and `/settings/callback`. There is still one consent screen, so the Internal/External decision is unchanged.
- **Onboarding:**
  1. The user adds the connector in Claude and signs in.
  2. The first tool call finds an empty ACL:
     - `list_spaces` returns an empty list plus a `settings_url` field;
     - the other tools fail with an error that includes the same link.
  - The URL is static (`{base_url}/settings`) and has no parameters. It is harmless for the model to show it, because the page requires a Google login anyway.
  - Putting the configuration step inside the connector's OAuth flow is not worth it: it would mean changing `OAuthProxy` internals for little gain.
- **Changes take effect immediately:** tools read the ACL from the database on every call, with no cache or a cache of a few seconds at most. Revoking write permission must apply from the next call. The cost is one query by `sub` on a small table.
- **Horizontal scaling:** all replicas must share the proxy's token store, the `/settings` sessions and the proxy's OAuth transactions. A single replica still needs external storage, because on Cloud Run and similar platforms the filesystem is not persistent.
- **Web framework:**
  - server-rendered HTML with Jinja;
  - served through `@mcp.custom_route` (available in FastMCP 4), or a Starlette app mounted next to the MCP app;
  - no SPA: server-side forms make CSRF protection and escaping simpler.

---

## 2. Per-user configuration: the page

- **Content:** the spaces returned by `spaces.list` with the user's token, each with read/write checkboxes.
  - `DIRECT_MESSAGE`/`GROUP_CHAT` spaces are shown with write disabled, consistent with the block already in `send_message`.
  - Whether *reading* DMs is allowed is an open point.
- **Orphaned entries:** spaces that are in the ACL but no longer returned by Google, because the user left or the space was deleted.
  - They are shown as such, with a button to remove them.
  - They are not a risk: Google blocks access anyway.
- **Admin policy:** in v1 it is deployment configuration (environment variables / Terraform), not a second UI.
  - Examples: allowed domain, DM block, optional allowlist of spaces.
  - The page only offers what the policy allows.
- **Connector status:**
  - Every MCP call updates `users.last_mcp_seen_at`, so the page can show "Connector linked as alice@…, last used: …".
  - This covers the **mismatched identity** case: a user with several Google accounts could configure account B on `/settings` while Claude is linked to account A.
  - The page must show clearly which account is signed in, and warn when that `sub` has never used the connector.
- **Audit log:** every ACL change is recorded (`sub`, space, before → after, timestamp) and is visible to the user.

Resulting data model:

```
users       (sub PK, email, created_at, last_mcp_seen_at)
space_acl   (sub FK, space_name, read, write, updated_at)        PK(sub, space_name)
acl_audit   (id PK, sub FK, space_name, before, after, changed_at)
```

In code, an `AclProvider` interface with two implementations:
- `CliAclProvider`: built from `--space`, for local mode;
- `DbAclProvider`: looked up by `sub`, for remote mode.

The tools stay the same in both modes.

---

## 3. Isolation, re-examined

Everything in [01 §3](01-requirements-analysis.md#3-no-user-can-see-another-users-configuration) still applies:
- identity taken only from the token;
- queries scoped by `sub`;
- no global state;
- no message text in logs.

The web page adds new threats, some of them specific to this project:

| Threat | Why it matters here | Mitigation |
|--------|---------------------|------------|
| **Stored XSS via space names** | Anyone who creates a space chooses its `displayName`. Rendered without escaping, a malicious name could change the ACL of *another* user who belongs to the same space. | Jinja autoescape always on, no `|safe`, strict CSP (`default-src 'self'`, no inline scripts) |
| **Clickjacking** | The page has buttons that grant write access, an obvious target. | `frame-ancestors 'none'` / `X-Frame-Options: DENY` |
| **CSRF** | POST requests change permissions. | Per-session CSRF token, `SameSite=Lax` or `Strict` cookies |
| **IDOR** | — | No user id in URLs or forms: `sub` comes only from the session |
| **Tokens used on the wrong entry point** | An MCP bearer token sent to `/settings`, or a session cookie sent to `/mcp`. | Separate mechanisms: `/settings` accepts only the cookie, `/mcp` only the bearer token with a verified audience |
| **Accounts outside the domain** | The Internal consent screen already excludes them. | Explicit check of the `hd` claim in both flows, as a second defence |
| **Stolen or forgotten session** | — | Short session (e.g. 30 min), logout. Optional: recent re-authentication (`max_age`) before granting write |

What option A solves by design: the model has no tool that changes the ACL, so prompt injection cannot widen permissions. This must hold in the future too: no "configuration" tools, not even read-only ones for the settings page beyond returning its link.

---

## Open points

See the table in [README.md](README.md#open-points).
