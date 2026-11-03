# 04 — Settings UI: server side

The backend behind `/settings` (issue #52): Google sign-in, listing the user's spaces, and storing the per-user ACL that the MCP server (#53) enforces. It serves the user flow in [03-settings-ui-flow.md](03-settings-ui-flow.md) (#51) and builds on [02-settings-page-direction.md](02-settings-page-direction.md).

Constraints from #29: GCP only (NFR-1), zero running cost for at most 40 users with very low concurrency (NFR-2), a database sized for a handful of records (NFR-3), Google Sign-In with the connector's identity (NFR-4), a minimal stack (NFR-5).

---

## Decisions

| # | Decision | Reason |
|---|---|---|
| S1 | **Cloud Run, scale to zero**, `max-instances` capped at 2. Cold starts are accepted. | Only serverless runtime with a free tier that fits NFR-2. The cap bounds the cost of a runaway client. |
| S2 | **Starlette + Jinja2**, server-rendered forms, no JavaScript. Starlette and Jinja2 already come with FastMCP. | NFR-5, and a strict CSP with `script-src 'none'`. No new framework to learn or audit. |
| S3 | **Firestore (Native mode), one database.** Server access only, through IAM; Security Rules deny every client SDK request. | Free tier covers the volume (NFR-3). Nothing in the browser talks to Firestore directly. |
| S4 | **One ACL document per user** (`acls/{sub}`), with all entries in a map and a `version` counter. | The warnings and the re-validation of 03 step 6–7 depend on the whole ACL. One document gives one read per MCP call and an atomic, versioned write. |
| S5 | **Server-side sessions in Firestore.** The cookie holds only a random ID; the database stores its SHA-256. | Real logout and revocation. No signing key to manage. A database leak yields no usable cookie. |
| S6 | **The online access token lives in the session document only**, and the session never outlives it. No refresh token is ever requested. | The picker and the save-time membership check need a Chat token; nothing outlives the session. |
| S7 | **`sub` comes from the session only.** No route, form field or query parameter carries a user identifier. | IDOR is impossible by construction, not by a check that could be forgotten. |
| S8 | **Effective permissions = stored ACL ∩ admin policy**, computed at save time *and* by the MCP server on every call. | A policy tightened after a save must apply at once, without rewriting users' ACLs. |
| S9 | **Audit record written in the same transaction as the ACL change**, in a per-user subcollection with a one-year TTL. | No change without a record. The user can see their own history. |
| S10 | **Built as a separate ASGI app** (`google_chat_mcp.settings`) with its own entry point. | Keeps the "one service or two" decision open until #29 records it; see the last section. |

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
  csrf_token,
  created_at, last_seen_at, expire_at,     # expire_at drives the TTL policy
  auth_time,
  pending_change,                          # see "Confirmation and re-authentication"
  reauth: { change_hash, at }
}
```

| Property | Value | Why |
|---|---|---|
| Cookie | `__Host-gcm_session`, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain` | `__Host-` pins it to this origin. `Lax`, not `Strict`: the user arrives from a link in Claude and returns from Google, both cross-site navigations. CSRF is handled by the token below. |
| Idle timeout | 30 minutes | Per 02. `last_seen_at` is rewritten at most once a minute to save writes. |
| Absolute lifetime | `min(token_expires_at − 2 min, created_at + 60 min)` | The session never outlives the Chat token it carries (S6), so a save never fails on an expired token halfway. |
| Logout (`POST /settings/logout`, CSRF-protected) | Deletes the session document, clears the cookie, redirects to a signed-out page | Real revocation (S5). The Google token is not revoked: it is the user's own short-lived token and expires within the hour. |
| Expiry during the flow | Redirect to sign-in, then to `/settings`; the interrupted step is dropped | As 03 step 8: a write grant is never left half done. |

**Stated precisely:** `/settings` stores no refresh token and no Google credential that outlives the session. The access token sits in the session document for at most an hour, encrypted at rest by Firestore, and is deleted at logout or by the TTL policy.

---

## Listing spaces

All Chat calls use the session's access token, never the connector's tokens.

| Page | Call | Notes |
|---|---|---|
| Enabled list (`GET /settings`) | `spaces.list` with no filter, all pages (`pageSize=1000`) | Used to name the ACL entries and to detect **orphaned entries**: ACL keys absent from the result. DMs are fetched too, so an orphan check also works when the DM view arrives. |
| Picker (`GET /settings/spaces/add?q=…`) | `spaces.list` with `filter=spaceType = "SPACE"` | `q` filters on `displayName` in the server, case-insensitive substring. Results sorted by name, then resource name. |
| Pasted link or ID (`GET /settings/spaces/add?ref=…`) | `spaces.get` | The server extracts the space ID from a `chat.google.com` URL or `spaces/ID` string with a strict pattern. Anything else: "Not a Google Chat space link". |
| Permissions page, and every save | `spaces.get` | Membership check: with user authentication Google returns the space only if the user can see it. `spaceType` must be `SPACE` (03, step 7). |

- **No cache** across requests: one `spaces.list` per page view is cheap at this scale, and a cache would show spaces the user has just left.
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

## Endpoints

All routes are under `/settings`. Every route except `callback`, `login` and the signed-out page requires a valid session; without one, it redirects to `login`.

| Method & path | Purpose | State change |
|---|---|---|
| `GET /settings/login` | Starts sign-in (also used by **[Switch account]** with `?select_account=1`) | Creates a login transaction |
| `GET /settings/callback` | OAuth callback | Creates the session |
| `POST /settings/logout` | Sign out | Deletes the session |
| `GET /settings` | Enabled list, account bar, connector status | — |
| `GET /settings/spaces/add` | Picker (`q`) and pasted link (`ref`) | — |
| `GET /settings/spaces/{id}` | Permissions form (add and edit) | — |
| `POST /settings/spaces/{id}` | Submits permissions. Saves directly when no confirmation is needed; otherwise stores `pending_change` and redirects to `confirm` | ACL, or session |
| `GET /settings/spaces/{id}/confirm` | Confirmation page, rendered from `pending_change` | — |
| `POST /settings/spaces/{id}/confirm` | Commits the pending change, or starts re-authentication | ACL |
| `POST /settings/spaces/{id}/remove` | Removes an entry (also for orphaned entries) | ACL |
| `GET /settings/history` | The user's own audit records, newest first, 50 per page | — |

`{id}` is a space ID, never a user ID (S7). Every handler starts with `sub = session.sub` and touches only `acls/{sub}` and `users/{sub}`. A test asserts that no route declares a parameter named `sub`, `user`, `email` or `uid`.

### Authorization checks on every write

In this order, before the transaction:

1. Valid session; CSRF token matches (see below).
2. `{id}` matches the ID pattern.
3. Form values parse to booleans; `unattended` is ignored unless `write` is set, and a request carrying `unattended` without `write` is rejected (03 step 5).
4. `spaces.get` succeeds with the session's token and `spaceType == "SPACE"`. Skipped only for **remove**, so the user can always clean up an orphaned entry.
5. The admin policy allows every requested permission for this space.
6. If the change needs confirmation or re-authentication (table in 03 step 6), the session holds a matching `pending_change` or `reauth` for this exact change and ACL version.

Then, in one Firestore transaction: read `acls/{sub}`, check `version` equals the version the confirmation was computed on, write the entry and `version + 1`, write the audit record. A version mismatch means the ACL changed in another tab; the user gets the confirmation page again, recomputed.

### Confirmation and re-authentication

- `POST /settings/spaces/{id}` stores in the session:
  `pending_change = { space, before, after, acl_version, warnings_hash, created_at }`.
  The confirmation page is rendered from it, not from form fields, so it cannot be tampered with between the two steps.
- The pending change expires after 10 minutes and is cleared after use, after any other save, and at logout.
- **Unattended write** (03 step 6): the confirm button is a `POST` that stores `reauth_for = hash(pending_change)` in a new login transaction and redirects (303) to Google with `max_age=300`. The callback checks the same `sub` and `auth_time`, sets `session.reauth = { change_hash, at }`, and returns the user to the confirmation page, now showing **[Grant]**. The final `POST` checks the hash and that `at` is less than 5 minutes old.
- **If Google does not honour `max_age` or omits `auth_time`** (to verify, as in 03): the callback cannot prove a recent login, so this step is dropped by configuration (`REAUTH_FOR_UNATTENDED=false`) and the confirmation page alone is used. The code must not silently accept a token without `auth_time`.

### CSRF

- Synchronizer token: 32 random bytes per session, in a hidden field of every form, compared in constant time.
- Every `POST` also checks `Origin` (or, if absent, `Sec-Fetch-Site`) equals `base_url`'s origin. Requests with neither header are rejected.
- `SameSite=Lax` on the session cookie is a third layer, not the main one.
- `GET` routes never change state. The pasted link uses `GET` because it only reads.

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

Set by one middleware on every `/settings` response:

```
Content-Security-Policy: default-src 'none'; style-src 'self'; img-src 'self';
                         form-action 'self' https://accounts.google.com;
                         frame-ancestors 'none'; base-uri 'none'
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Strict-Transport-Security: max-age=31536000
Cross-Origin-Opener-Policy: same-origin
Cache-Control: no-store
```

- `form-action` includes Google because the re-authentication `POST` answers with a redirect there, and Chrome applies `form-action` to redirects.
- `no-store` keeps pages with space names and the CSRF token out of shared caches and the back-button cache.
- Escaping is #51's scope; the server's part is Jinja's `autoescape=True` for every template, enforced by a test that renders each template with a space named `<script>`.

---

## Runtime and cost

| Item | Setting | Free tier (to confirm against current pricing) | Expected use |
|---|---|---|---|
| Cloud Run | request-based billing, `min-instances=0`, `max-instances=2`, 512 MiB, 1 vCPU, region in the free-tier list | 2 M requests, 180 000 vCPU-s, 360 000 GiB-s per month | Far below; a few hundred page views a month |
| Firestore | one database, same region | 1 GiB, 50 000 reads, 20 000 writes, 20 000 deletes per day | MCP: 1 ACL read per tool call (≈ 8 000/day at 40 users × 200 calls). `/settings`: tens a day |
| Secret Manager | OAuth client secret | 6 active versions, 10 000 accesses per month | 1 secret, read at start-up |
| Cloud Logging | default bucket | 50 GiB per month | Negligible |
| Artifact Registry | container image | 0.5 GB | One image, old versions cleaned up by policy |

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

**Lean:** two services from one image. The isolation of the token decryption key is worth the small extra configuration. This is input to the decision in #29, not the decision itself; S10 keeps both options open.

---

## To verify before implementation

1. Whether Google honours `max_age` and returns `auth_time` (shared with 03; decides re-authentication).
2. That consent is skipped for a user who already granted `chat.spaces.readonly` to the connector's client, when `/settings` uses a second client of the same project (02 assumed one client).
3. That `spaces.get` with user authentication returns `403`/`404` for a space the user has left, and not stale data.
4. Firestore free-tier conditions: which databases it covers, and whether TTL deletions count against the delete quota.
5. Cloud Run free-tier regions, and whether Firestore's free tier is available in the same region.
6. Google's ID token: that `hd` is present for every Workspace account of the domain, and absent for consumer accounts.

## Out of scope

- UI screens and flow (#51, [03](03-settings-ui-flow.md)).
- The MCP server, its token store and its use of `users` and `acls` (#53).
- Implementation, CI/CD and Terraform (later phases of #29).
- Deleting a user's data on request: to define with #53, since `users/{sub}` belongs to the MCP side.
