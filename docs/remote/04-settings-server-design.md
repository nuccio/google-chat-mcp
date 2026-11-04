# 04 — Settings UI: server side

The backend behind `/settings` (issue #52): Google sign-in, listing the user's spaces, and storing the per-user ACL that the MCP server (#53) enforces. It serves the user flow in [03-settings-ui-flow.md](03-settings-ui-flow.md) (#51) and builds on [02-settings-page-direction.md](02-settings-page-direction.md).

Constraints from #29: GCP only (NFR-1), zero running cost for at most 40 users with very low concurrency (NFR-2), a database sized for a handful of records (NFR-3), Google Sign-In with the connector's identity (NFR-4), a minimal stack (NFR-5).

---

## Architecture

Drawn with two services, the lean in the [last section](#contribution-to-one-service-or-two-29). With one service, `gcm-settings` and `gcm-mcp` become two route groups of the same process; the rest is unchanged.

![Architecture: Browser and Claude, the two Cloud Run services, Firestore, Secret Manager, Cloud Logging, Google Sign-In and the Chat API](images/04-architecture.svg)

Icons: [Google Cloud product icons](https://cloud.google.com/icons). The diagram is a plain SVG file and can be edited directly.

Who reads and writes what in Firestore:

| Collection | `gcm-settings` | `gcm-mcp` |
|---|---|---|
| `acls/{sub}` + `audit` | read / write, one transaction | read on every tool call |
| `users/{sub}` | read `last_mcp_seen_at` | write `last_mcp_seen_at` |
| `sessions`, `login_transactions` | read / write | — |
| `OAuthProxy` token store (encrypted) | — | read / write |

The two services share only the Google `sub` and the configuration in Secret Manager and environment variables. IAM does not enforce the empty cells: both service accounts can reach the whole database (see the last section).

---

## Decisions

| # | Decision | Reason |
|---|---|---|
| S1 | **Cloud Run, scale to zero**, `max-instances` capped at 2. Cold starts are accepted. | Only serverless runtime with a free tier that fits NFR-2. The cap bounds the cost of a runaway client. |
| S2 | **NiceGUI** for the pages (`@ui.page`), on its own FastAPI app. Sign-in, callback and sign-out stay plain FastAPI routes. Accepted costs, all detailed in [NiceGUI: consequences and rules](#nicegui-consequences-and-rules): JavaScript and a websocket are required, the CSP must allow `'unsafe-inline'` and `'unsafe-eval'`, and an open tab keeps a Cloud Run instance billed. | NFR-5 read as "simple for the team that maintains it": the team knows NiceGUI. The pages stay in Python, with no templates or front-end code to write. |
| S3 | **Firestore (Native mode), one database.** Server access only, through IAM; Security Rules deny every client SDK request. | Free tier covers the volume (NFR-3). Nothing in the browser talks to Firestore directly. |
| S4 | **One ACL document per user** (`acls/{sub}`), with all entries in a map and a `version` counter. | The warnings and the re-validation of 03 step 6–7 depend on the whole ACL. One document gives one read per MCP call and an atomic, versioned write. |
| S5 | **Server-side sessions in Firestore.** The cookie holds only a random ID; the database stores its SHA-256. | Real logout and revocation. No signing key to manage. A database leak yields no usable cookie. |
| S6 | **The online access token lives in the session document only**, and the session never outlives it. No refresh token is ever requested. | The picker and the save-time membership check need a Chat token; nothing outlives the session. |
| S7 | **`sub` comes from the session only.** No route, form field or query parameter carries a user identifier. | IDOR is impossible by construction, not by a check that could be forgotten. |
| S8 | **Effective permissions = stored ACL ∩ admin policy**, computed at save time *and* by the MCP server on every call. | A policy tightened after a save must apply at once, without rewriting users' ACLs. |
| S9 | **Audit record written in the same transaction as the ACL change**, in a per-user subcollection with a one-year TTL. | No change without a record. The user can see their own history. |
| S10 | **Built as a separate ASGI app** (`google_chat_mcp.settings`, a FastAPI app with NiceGUI attached by `ui.run_with`) with its own entry point. | Keeps the "one service or two" decision open until #29 records it; see the last section. |

---

## Sign-in flow

### OAuth client

- One "Web application" OAuth client for `/settings`, in the same GCP project and under the same consent screen (Internal) as the connector's client. A second client keeps the two client secrets apart; both clients see the same `sub`, because Google's `sub` is per account, not per client.
- Redirect URI: `{base_url}/settings/callback`.
- Authorization request:
  - `scope=openid email https://www.googleapis.com/auth/chat.spaces.readonly`
  - `access_type=online` (no refresh token)
  - `response_type=code`, PKCE `S256`, a random `state` and `nonce`
  - `hd={allowed_domain}` as a hint only; the claim is checked on return
  - `prompt=select_account` only for **[Switch account]**
  - `max_age=300` only for re-authentication (step 6 of 03)

### Login transaction

Before the redirect to Google, the server stores a transaction in `login_transactions/{sha256(tx_id)}`:

```
{ state, nonce, code_verifier, purpose: "login" | "reauth", reauth_for, created_at, expire_at }
```

- `tx_id` is 32 random bytes, sent in a cookie `__Host-gcm_login` (`Secure`, `HttpOnly`, `SameSite=Lax`, `Max-Age=600`).
- TTL 10 minutes, enforced both by the code and by a Firestore TTL policy on `expire_at`. The 10 minutes also absorb a cold start on the way back.
- The transaction is deleted when the callback consumes it, so a `code` or `state` can be used only once.

### Callback (`GET /settings/callback`)

1. Load the transaction from the cookie; reject if missing, expired, or `state` differs. The cookie binds the flow to this browser, which stops login CSRF.
2. Exchange the code with the PKCE verifier.
3. Verify the ID token with `google-auth`: signature, `iss`, `aud` = this client, `exp`, `nonce`, `email_verified == true`, `hd == allowed_domain`. On failure: generic error page, nothing stored.
4. Check that the granted scopes include `chat.spaces.readonly`. If the user unticked it on the consent screen, show a page explaining that the list of spaces cannot be read without it.
5. **Login:** create a new session (never reuse an ID from before the login, against session fixation) and delete the transaction. **Re-auth:** require the same `sub` as the current session and `auth_time ≥ now − 300 s`, then mark the session; see "Re-authentication".
6. Redirect (303) to `/settings`. The target is fixed: there is no `next` parameter, so no open redirect.

### Session

Document `sessions/{sha256(session_id)}`:

```
{
  sub, email,
  access_token, token_expires_at,
  created_at, last_seen_at, expire_at,     # expire_at drives the TTL policy
  auth_time,
  pending_change,                          # see "Confirmation and re-authentication"
  reauth: { change_hash, at }
}
```

| Property | Value | Why |
|---|---|---|
| Cookie | `__Host-gcm_session`, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain` | `__Host-` pins it to this origin. `Lax`, not `Strict`: the user arrives from a link in Claude and returns from Google, both cross-site navigations. CSRF is handled as described in [CSRF](#csrf). |
| Idle timeout | 30 minutes for the session; **10 minutes without user events for an open tab**, after which the server closes the tab's websocket (see [Runtime and cost](#runtime-and-cost)) | Per 02. `last_seen_at` is rewritten at most once a minute to save writes. |
| Absolute lifetime | `min(token_expires_at − 2 min, created_at + 60 min)` | The session never outlives the Chat token it carries (S6), so a save never fails on an expired token halfway. |
| Logout (**[Sign out]** button) | The event handler deletes the session document, then navigates to `GET /settings/signed-out`, which clears the cookie | Real revocation (S5): the session is gone before the navigation. The Google token is not revoked: it is the user's own short-lived token and expires within the hour. |
| Expiry during the flow | The next event finds no valid session and navigates to sign-in, then to `/settings`; the interrupted step is dropped | As 03 step 8: a write grant is never left half done. |

**Stated precisely:** `/settings` stores no refresh token and no Google credential that outlives the session. The access token sits in the session document for at most an hour, encrypted at rest by Firestore, and is deleted at logout or by the TTL policy.

---

## Listing spaces

All Chat calls use the session's access token, never the connector's tokens.

| Page | Call | Notes |
|---|---|---|
| Enabled list (page `/settings`) | `spaces.list` with no filter, all pages (`pageSize=1000`) | Used to name the ACL entries and to detect **orphaned entries**: ACL keys absent from the result. DMs are fetched too, so an orphan check also works when the DM view arrives. |
| Picker (page `/settings/spaces/add`) | `spaces.list` with `filter=spaceType = "SPACE"`, once when the page is built | The search box filters the list in the page's server-side state as the user types (debounced), case-insensitive substring on `displayName`. Results sorted by name, then resource name. |
| Pasted link or ID (picker page, **[Use]**) | `spaces.get` | The server extracts the space ID from a `chat.google.com` URL or `spaces/ID` string with a strict pattern. Anything else: "Not a Google Chat space link". |
| Permissions page, and every save | `spaces.get` | Membership check: with user authentication Google returns the space only if the user can see it. `spaceType` must be `SPACE` (03, step 7). |

- **No cache** across pages: one `spaces.list` per page build is cheap at this scale, and a cache would show spaces the user has just left. Within one open picker the list is kept in memory, so typing in the search box costs no Chat call.
- **Chat API errors:** `401` ends the session (token revoked or expired); `403`/`404` on `spaces.get` means "You are no longer a member of this space" (03 step 8); `429`/`5xx` shows a retry page and changes nothing.
- **Space names never go to logs**, nor does `q`.

---

## Data model

Firestore, Native mode, one database. Every document carries `schema_version`.

```
users/{sub}                         written by the MCP server; read by /settings
  schema_version: 1
  email
  created_at
  last_mcp_seen_at                  # rewritten at most once an hour

acls/{sub}                          written by /settings; read by the MCP server
  schema_version: 1
  version: 17                       # +1 on every change; optimistic concurrency
  updated_at
  entries: {
    "AAABBBCCC": { read: true,  write: true,  unattended: false, updated_at },
    "REPORTS01": { read: false, write: true,  unattended: true,  updated_at }
  }

acls/{sub}/audit/{auto_id}          written by /settings in the same transaction
  schema_version: 1
  changed_at, expire_at              # expire_at = changed_at + 365 days (TTL policy)
  space: "AAABBBCCC"
  before: { read, write, unattended } | null
  after:  { read, write, unattended } | null
  acl_version: 17
  reauthenticated: true | false
  session: sha256(session_id)[:16]  # correlates the changes of one session

sessions/{sha256(session_id)}       see "Session"
login_transactions/{sha256(tx_id)}  see "Login transaction"
```

- **Keys** are the space ID without the `spaces/` prefix, so map keys need no quoting in field paths. The ID is validated against `^[A-Za-z0-9_-]{1,128}$` before use.
- **Invariants**, enforced in one function shared by `/settings` and the MCP server (Firestore has no `CHECK`):
  - `unattended ⇒ write` (same rule as `parse_space_arg`);
  - `read or write` (an entry with no permission is deleted, not stored);
  - at most 200 entries per user, far above real use, to bound the document.
- **Why `users` and `acls` are separate:** the MCP server writes `last_mcp_seen_at`; keeping it out of the ACL document means the MCP server never needs to write `acls`, which matters if IAM ever allows splitting the two.
- **A new user** has no `acls/{sub}` document; it reads as an empty ACL (the empty state of 03).

### Schema versioning

- Readers accept `schema_version` equal to the current version or the previous one, and upgrade the previous one in memory.
- Writers always write the current version, so documents migrate on their next change. No batch migration job.
- **A version higher than the reader knows fails closed:** the MCP server treats the ACL as empty and `/settings` refuses to write. This protects a rollback of one service after the other has written a newer schema.
- A change that cannot be read by the previous version is deployed in two steps: readers first, writers second.

---

## Routes, pages and actions

With NiceGUI, state changes are **event handlers** running on the server (a button click arrives over the page's websocket), not `POST` endpoints. The table lists the HTTP routes, the pages, and the actions on each page.

All paths are under `/settings`. Every page requires a valid session; without one, it redirects to `login`.

**Plain FastAPI routes** (no NiceGUI):

| Route | Purpose | State change |
|---|---|---|
| `GET /settings/login` | Starts sign-in (also used by **[Switch account]** with `?select_account=1`) | Creates a login transaction |
| `GET /settings/callback` | OAuth callback | Creates the session |
| `GET /settings/signed-out` | Clears the session cookie and shows the signed-out page. The session document is already deleted by the **[Sign out]** handler | — |

**NiceGUI pages** (`@ui.page`):

| Page | Shows | Actions (event handlers) |
|---|---|---|
| `/settings` | Enabled list, account bar, connector status | **[Remove]** (also for orphaned entries) → ACL; **[Sign out]** → deletes the session |
| `/settings/spaces/add` | Picker with search box and pasted link | **[Use]** for a pasted link; **[Choose]** navigates to the permissions page |
| `/settings/spaces/{id}` | Permissions (add and edit) | **[Continue]**: saves directly when no confirmation is needed; otherwise stores `pending_change` and navigates to `confirm` → ACL, or session |
| `/settings/spaces/{id}/confirm` | Confirmation, built from `pending_change` | **[Verify it's you]** starts re-authentication; **[Grant]** commits the pending change → ACL |
| `/settings/history` | The user's own audit records, newest first, 50 per page | — |

`{id}` is a space ID, never a user ID (S7). Every page reads the session cookie from the request that loaded it (`@ui.page` handler with a `request: Request` argument) and keeps only the session ID in the page's server-side state. Every handler starts by **re-reading the session from Firestore** (it may have expired or been signed out from another tab), takes `sub` from it, and touches only `acls/{sub}` and `users/{sub}`. A test asserts that no route or page declares a parameter named `sub`, `user`, `email` or `uid`.

### Authorization checks on every write

In this order, before the transaction:

1. The session, re-read from Firestore, is still valid. The event arrived over the websocket of a page loaded with that session (see [CSRF](#csrf)).
2. `{id}` matches the ID pattern.
3. The values come from the page's server-side element state (checkboxes, radio), not from data sent by the browser alongside the event. `unattended` is ignored unless `write` is set, and a change carrying `unattended` without `write` is rejected (03 step 5). The page can now disable the write mode when Write is unchecked, but the server keeps the rule.
4. `spaces.get` succeeds with the session's token and `spaceType == "SPACE"`. Skipped only for **remove**, so the user can always clean up an orphaned entry.
5. The admin policy allows every requested permission for this space.
6. If the change needs confirmation or re-authentication (table in 03 step 6), the session holds a matching `pending_change` or `reauth` for this exact change and ACL version.

Then, in one Firestore transaction: read `acls/{sub}`, check `version` equals the version the confirmation was computed on, write the entry and `version + 1`, write the audit record. A version mismatch means the ACL changed in another tab; the user gets the confirmation page again, recomputed.

### Confirmation and re-authentication

- **[Continue]** on the permissions page stores in the session:
  `pending_change = { space, before, after, acl_version, warnings_hash, created_at }`.
  The confirmation page is built from it, so it cannot be tampered with between the two steps. It stays a separate page, not a `ui.dialog`: a dialog would hold the pending change only in the tab's memory, which is lost if the instance changes.
- The pending change expires after 10 minutes and is cleared after use, after any other save, and at logout.
- **Unattended write** (03 step 6): **[Verify it's you]** stores `reauth_for = hash(pending_change)` in a new login transaction, sets the login cookie, and navigates the browser to Google with `max_age=300`. The cookie cannot be set over the websocket, so the handler navigates to `GET /settings/login?reauth=1`, which sets it and redirects; that route starts re-authentication only if the session holds a pending change. The callback checks the same `sub` and `auth_time`, sets `session.reauth = { change_hash, at }`, and returns the user to the confirmation page, now showing **[Grant]**. The **[Grant]** handler checks the hash and that `at` is less than 5 minutes old.
- **If Google does not honour `max_age` or omits `auth_time`** (to verify, as in 03): the callback cannot prove a recent login, so this step is dropped by configuration (`REAUTH_FOR_UNATTENDED=false`) and the confirmation page alone is used. The code must not silently accept a token without `auth_time`.

### CSRF

There are no forms posting to the server, so a synchronizer token in a hidden field no longer applies. The risk moves to the websocket: a page on another site opening a socket to `/settings` and sending events (cross-site WebSocket hijacking).

- **Origin check at the websocket handshake.** NiceGUI creates its Socket.IO server with `cors_allowed_origins='*'` (checked in NiceGUI 3.19), so it accepts any origin. An ASGI middleware in front of NiceGUI rejects every request to `/_nicegui_ws/` whose `Origin` is not `base_url`'s origin, and every request with no `Origin` at all.
- **Events only reach the page that created them.** A socket is bound to the page's client ID, which exists only in the page loaded by the user. An attacker without it cannot drive another user's page.
- **The session is re-validated on every event** (check 1), so a socket that survives a sign-out or an expired session changes nothing.
- `SameSite=Lax` on the session cookie is an extra layer: current browsers do not send it on cross-site websocket handshakes. The socket itself is not authenticated by the cookie, so this layer matters little.
- `GET` routes change no ACL. `GET /settings/login?reauth=1` only starts a re-authentication the user has already asked for.

---

## Admin policy

Deployment configuration (environment variables set by Terraform), read at start-up:

| Variable | Example | Effect |
|---|---|---|
| `ALLOWED_DOMAIN` | `example.com` | Required. Checked against `hd` in both flows. |
| `POLICY_ALLOW_WRITE` | `true` | `false` disables Write for everyone. |
| `POLICY_ALLOW_UNATTENDED` | `false` | `false` disables "Write without confirmation" for everyone. |
| `POLICY_SPACE_ALLOWLIST` | `AAABBBCCC,DDDEEEFFF` | Optional. If set, only these spaces can be added. |

- `/settings` uses the policy to **disable options** with the reason (03 step 5), to **refuse saves** (step 7, check 5), and to mark stored entries that the current policy no longer allows: "Suspended by your administrator". Those entries keep their stored value, so loosening the policy restores them, and they can always be removed.
- The MCP server applies the same intersection on every call (S8), using the same module and the same environment variables. A mismatch between the two services' configuration can only make the MCP server stricter than the page, never looser.
- An unknown or unparsable policy value stops the service at start-up.

---

## Audit log

- **What:** one record per ACL change (add, edit, remove): before → after, ACL version, whether re-authentication happened, a short session correlation ID. No IP address, no user agent, no space names (the ID is enough and names can change).
- **Where:** `acls/{sub}/audit`, written in the same transaction as the change (S9). The user sees it on `/settings/history`; the operator reads it in the Firestore console.
- **Retention:** one year, through a Firestore TTL policy on `expire_at`. TTL deletion is asynchronous, so records may survive a little longer.
- **Operator signal:** each change also emits one structured log line to Cloud Logging (`event=acl_change`, hashed `sub`, space ID, after-state), so alerts can be set on, for example, every new unattended grant. Cloud Logging's default retention (30 days) applies to that copy. The log masking work recovered from #43 applies here.

---

## Security headers

Set by one middleware on every `/settings` response, NiceGUI's static files included:

```
Content-Security-Policy: default-src 'self';
                         script-src 'self' 'unsafe-inline' 'unsafe-eval';
                         style-src 'self' 'unsafe-inline';
                         img-src 'self' data:; font-src 'self'; connect-src 'self';
                         object-src 'none'; form-action 'self';
                         frame-ancestors 'none'; base-uri 'none'
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Strict-Transport-Security: max-age=31536000
Cross-Origin-Opener-Policy: same-origin
Cache-Control: no-store
```

- **The CSP no longer stops injected scripts.** NiceGUI's page template carries inline scripts (the element tree is embedded in one), and `nicegui.js` evaluates code with `eval` and `new Function` (both checked in NiceGUI 3.19). NiceGUI has no nonce support, so `'unsafe-inline'` and `'unsafe-eval'` are required. What the CSP still does: blocks loading scripts and connections from other origins, framing, `<object>`, `<base>` rewriting and form posts elsewhere. Protection against stored XSS through space names now rests entirely on the rendering rules in the next section.
- `connect-src 'self'` covers the websocket to the same origin.
- `no-store` applies to pages; NiceGUI's versioned static files (`/_nicegui/{version}/…`) keep their own long cache headers, which is fine because they hold no user data.
- Escaping of what the user sees is #51's scope; the rules on which NiceGUI elements may carry data are below.

---

## NiceGUI: consequences and rules

Checked against NiceGUI 3.19.0, the current version on PyPI. To check again at the version pinned in `uv.lock`.

### Rendering rules (stored XSS)

Anyone who creates a space chooses its name, so space names, member names and anything returned by the Chat API are untrusted.

| Allowed for untrusted text | Forbidden for untrusted text |
|---|---|
| `ui.label`, `ui.table` / `ui.aggrid` cells as plain text, `ui.select` / `ui.input` values, `ui.notify` | `ui.html` (even with `sanitize=True`), `ui.markdown`, `ui.add_head_html` / `ui.add_body_html`, `ui.run_javascript`, `ui.navigate.to` with a URL built from data, table cell slots that render HTML |
| Props with a literal value | **Props whose name starts with `:`**: NiceGUI evaluates their value as JavaScript in the browser |

Enforced by:
- a CI check that fails if the settings package imports or calls any of the forbidden functions, or sets a `:`-prefixed prop;
- a test that builds every page with a space named `` <img src=x onerror=alert(1)>`${alert(1)}` `` (covers HTML and the template literal that carries the element tree) and asserts that the name appears only as text.

### Websocket, state and instances

- Each open tab has its state in the memory of one instance, and talks to it over a Socket.IO websocket. Cloud Run needs **session affinity** on, so a tab's reconnects reach the same instance while it lives.
- If the instance stops or the tab reconnects to another one, the browser reloads the page (NiceGUI does this when the handshake fails). Nothing is lost: the ACL, the session and `pending_change` are in Firestore. This is why `pending_change` stays in the session document and the confirmation is a page, not a dialog.
- Cloud Run request timeout: 15 minutes, above the tab idle timeout, so the platform does not cut a socket the server would keep.
- **NiceGUI storage is not used** (`app.storage.user`, `app.storage.browser`). It writes to the local disk, which Cloud Run does not keep, and it needs its own signed cookie. With no `storage_secret`, NiceGUI adds no session middleware. Sessions stay those of [Session](#session).
- `binding_refresh_interval=None`: the pages use no automatic bindings, so the server does not poll them.

### Dependencies and image

NiceGUI brings FastAPI, `uvicorn[standard]`, `python-socketio`, `aiohttp`, `httpx`, `Pygments`, `docutils`, `markdown2` and several MB of front-end files (Vue, Quasar, Tailwind, Socket.IO). Consequences:
- **Cold start:** more to import than Starlette + Jinja2. To measure; the target is still "a few seconds".
- **Image:** if both services use one image, the MCP service carries NiceGUI without importing it. A `settings` extra in `pyproject.toml` keeps it out of the PyPI package for local users.
- **Supply chain:** every new dependency goes under the lock file and the audit job from #50.
- Tailwind is turned off (`tailwind=False`); Quasar props are enough for the layout, and one script less runs in the page.

### Impact on the user flow (03, #51)

[03](03-settings-ui-flow.md) was written for pages without JavaScript. With NiceGUI, these parts change and should be updated in #51:
- "Search is a GET form … needs no JavaScript, which keeps a strict CSP": the search box filters as the user types, and the CSP is the one above.
- "The page has no JavaScript to disable [the write mode]": the page can now disable it; the server rule stays.
- Saving is an event handler, not a `POST`; the POST/redirect/GET pattern becomes a navigation to the list with a `ui.notify` message.
- The rest of the flow (separate list and picker, confirmation table, re-authentication, errors) is unchanged.

---

## Runtime and cost

| Item | Setting | Free tier (to confirm against current pricing) | Expected use |
|---|---|---|---|
| Cloud Run | request-based billing, `min-instances=0`, `max-instances=2`, 512 MiB, 1 vCPU, session affinity, 15-minute request timeout, region in the free-tier list | 2 M requests, 180 000 vCPU-s, 360 000 GiB-s per month | Requests: far below. Instance time: see the websocket note below |
| Firestore | one database, same region | 1 GiB, 50 000 reads, 20 000 writes, 20 000 deletes per day | MCP: 1 ACL read per tool call (≈ 8 000/day at 40 users × 200 calls). `/settings`: tens a day |
| Secret Manager | OAuth client secret | 6 active versions, 10 000 accesses per month | 1 secret, read at start-up |
| Cloud Logging | default bucket | 50 GiB per month | Negligible |
| Artifact Registry | container image | 0.5 GB | One image, old versions cleaned up by policy |

**Open tabs cost instance time.** With request-based billing, Cloud Run bills an instance while it has at least one active request, and an open websocket is an active request. A settings tab left open would keep `gcm-settings` billed for as long as it stays open. Rules that bound it:
- the server closes a tab's websocket after **10 minutes without user events**, and navigates it to a static "Session paused, reload to continue" page;
- every tab is closed at the session's absolute lifetime (60 minutes at most);
- no `ui.timer` or other server-side activity keeps a tab alive.

Rough worst case: 40 users × 4 visits a month × 15 minutes = 40 hours = 144 000 vCPU-s at 1 vCPU, against 180 000 free, shared with the MCP service. Tabs open at the same time share one instance, so real use is lower, and after the initial setup users rarely come back. Still, this is the item closest to the free-tier limit; measure it in the first month. Going below 1 vCPU is not an option: Cloud Run then allows only one concurrent request per instance, and each websocket would hold a whole instance.

**Cold start, accepted:** after an idle period the first request starts a container (Python import, Firestore and Google clients). Expect a few seconds. Where it shows:
- the first page view after a while;
- the OAuth callback, if the instance stopped while the user was on Google's page. The 10-minute transaction TTL and the Firestore-backed transaction make this correct, only slower.

Mitigations that cost nothing: lazy imports of the Chat client, no work at import time. `min-instances=1` would remove cold starts but is not free and is not proposed. A maximum response time, if wanted, is a separate NFR (#29).

---

## Contribution to "one service or two" (#29)

What the settings component needs if it runs on its own:

| Need | Detail |
|---|---|
| Cloud Run service | `gcm-settings`, same container image as the MCP server, different command (S10). One build pipeline, two deploys. |
| Service account | `gcm-settings@`: `roles/datastore.user` with an IAM condition on the database, `roles/secretmanager.secretAccessor` on its own client secret only. No access to the connector's secrets or token-store encryption key. |
| OAuth client | Its own client, same project and consent screen. Redirect URI `{settings_base_url}/settings/callback`. |
| Configuration shared with the MCP server | `ALLOWED_DOMAIN`, the `POLICY_*` variables, the Firestore database ID, the settings base URL (the MCP server returns it in its link), the ACL schema version. One Terraform module sets them on both services. |

What the split buys, given what Firestore allows:

- **Gained:** a flaw in the web UI does not reach the MCP process, its memory, or the key that decrypts the users' Chat refresh tokens in `OAuthProxy`'s store. The UI is the larger attack surface (it renders names chosen by others), so this is the main benefit.
- **Not gained:** Firestore IAM has no per-collection permissions. IAM conditions can scope a role to a database, but not to `acls` versus the token store. Both service accounts end up with `datastore.user` on the same database. A UI compromise could therefore read the token store's ciphertext (not decrypt it) or delete documents. Real separation needs a second database or project; whether the free quota covers a second database is to verify, and if not it breaks NFR-2.
- **Cost:** both services scale to zero and share the billing account's free tier, so the split costs nothing at this load.
- **Operational cost:** two deploys and two OAuth clients, mitigated by one image and one Terraform module.
- **NiceGUI adds to the case for two services.** One service is possible: `ui.run_with(app, mount_path="/settings")` mounts NiceGUI on a FastAPI app that also mounts FastMCP's HTTP app. But the MCP process would then run code that evaluates JavaScript-bearing props and serves a weaker CSP; it would also carry NiceGUI's module-level singletons, its Socket.IO server, and its websocket-driven instance time. Two services keep all of that away from the token store.

**Lean:** two services from one image. The isolation of the token decryption key is worth the small extra configuration. This is input to the decision in #29, not the decision itself; S10 keeps both options open.

---

## To verify before implementation

1. Whether Google honours `max_age` and returns `auth_time` (shared with 03; decides re-authentication).
2. That consent is skipped for a user who already granted `chat.spaces.readonly` to the connector's client, when `/settings` uses a second client of the same project (02 assumed one client).
3. That `spaces.get` with user authentication returns `403`/`404` for a space the user has left, and not stale data.
4. Firestore free-tier conditions: which databases it covers, and whether TTL deletions count against the delete quota.
5. Cloud Run free-tier regions, and whether Firestore's free tier is available in the same region.
6. Google's ID token: that `hd` is present for every Workspace account of the domain, and absent for consumer accounts.
7. NiceGUI on Cloud Run: websocket behaviour with session affinity and scale to zero, cold-start time with NiceGUI imported, and instance time billed per open tab (the estimate in [Runtime and cost](#runtime-and-cost)).
8. NiceGUI: that an ASGI middleware placed in front of NiceGUI sees the `/_nicegui_ws/` handshake and can reject it by `Origin`. Also whether a later version adds CSP nonces, which would allow removing `'unsafe-inline'`.
9. Cloud Run: that a service with less than 1 vCPU is limited to one concurrent request, as assumed in [Runtime and cost](#runtime-and-cost).

## Out of scope

- UI screens and flow (#51, [03](03-settings-ui-flow.md)).
- The MCP server, its token store and its use of `users` and `acls` (#53).
- Implementation, CI/CD and Terraform (later phases of #29).
- Deleting a user's data on request: to define with #53, since `users/{sub}` belongs to the MCP side.
