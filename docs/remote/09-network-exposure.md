# 09 — Network exposure of `gcm-mcp` and trying the real connector

Issue #82, part of #29. It answers three questions that [07](07-mcp-server-design.md) left open: how Claude reaches `gcm-mcp` on Cloud Run, what is exposed to the internet and what restricts it, and how the real Claude connector is tried during development. It gives #79 (OAuth) and #80 (HTTP entry point) the exposure model they implement, and feeds #60 (deployment) and #71 (setup checks).

> **Status: design only.** Nothing here is implemented. Facts checked against official documentation on **2026-11-06** are marked with the source; what could not be checked is listed in [To verify](#to-verify), each as `[local]` or `[GCP]` (same meaning as in 07).

---

## What was checked (2026-11-06)

| Fact | Source | Consequence |
|---|---|---|
| Anthropic's outbound ranges, "for example, when making MCP tool calls to external servers", are `160.79.104.0/21` and `153.61.208.0/20`. No IPv6 outbound range is listed. The page says the addresses "will not change without notice" and has no "last updated" date. Five `34.162.x.x/32` addresses are listed as phased out. | [platform.claude.com/docs/en/api/ip-addresses](https://platform.claude.com/docs/en/api/ip-addresses) (read directly) | An allowlist by source address is possible but depends on a list Anthropic may change. |
| All remote MCP connections originate from Anthropic's cloud infrastructure. A server must be reachable over the public internet; a local server needs a public URL, and the testing page suggests a tunnel such as Cloudflare Tunnel or ngrok. Anthropic's outbound traffic, including discovery requests to the authorisation server, comes from `160.79.104.0/21`. Availability: Claude, Cowork and Claude Desktop on Free (one custom connector), Pro, Max, Ultra, Team and Enterprise. | [Get started with custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp), [connectors/building](https://claude.com/docs/connectors/building), [testing](https://claude.com/docs/connectors/building/testing.md), [authentication](https://claude.com/docs/connectors/building/authentication.md) (read 2026-11-07) | The real connector can only be tried against a public HTTPS address ([Trying the real connector](#trying-the-real-connector-during-development)). |
| Claude's hosted apps (claude.ai, Desktop, mobile, Cowork) use exactly `https://claude.ai/api/mcp/auth_callback`. Registration: DCR (RFC 7591, a new client on each fresh connection, so the docs prefer CIMD), CIMD when the metadata advertises it (`client_id_metadata_document_supported` and `none` among the token endpoint auth methods), otherwise DCR; or a client ID and secret the user types in the add-connector dialog. PKCE `S256` is required. Claude needs a `401` with `WWW-Authenticate: Bearer resource_metadata=…` (it ignores that header on a `200`), uses only the first `authorization_servers` entry, refreshes on `401` and up to five minutes before expiry, and expects `invalid_grant` for a dead refresh token. `/token` must accept form-encoded bodies. Discovery, registration and token endpoints have a **10 s** limit, refresh 30 s; a tool call 240 s. Transports: Streamable HTTP recommended, SSE being deprecated. Auth specs 2025-03-26, 2025-06-18, 2025-11-25. | [authentication](https://claude.com/docs/connectors/building/authentication.md), [building](https://claude.com/docs/connectors/building) (read 2026-11-07) | Allow only the `claude.ai` callback. The `claude.com` callback seen in an older search snippet is **not** in the current docs. The 10 s limit applies to a cold start of `gcm-mcp`: see [To verify](#to-verify). |
| The MCP spec revision "latest" is **2026-07-28**. Servers **MUST** implement OAuth Protected Resource Metadata (RFC 9728) and answer an invalid or expired token with **401** and a `WWW-Authenticate: Bearer resource_metadata=…` header. Clients **MUST** send the `resource` parameter (RFC 8707). Servers **MUST** reject tokens not issued for them (audience). Client registration order: pre-registered, then Client ID Metadata Documents (CIMD), then DCR (marked deprecated). | [modelcontextprotocol.io/specification/latest/basic/authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization) and its `client-registration` page | The 401 an unauthenticated caller gets is what starts Claude's sign-in, so it is also the right, minimal answer to anyone else. |
| Streamable HTTP: servers **MUST** validate `Origin` on all incoming connections and answer an invalid one with **403**. The revision removes the GET stream and protocol-level sessions. No Host-header rule was found. HTTPS is required explicitly for authorization-server endpoints and redirect URIs, not stated as a MUST for the MCP endpoint itself. | [modelcontextprotocol.io/specification/latest/basic/transports/streamable-http](https://modelcontextprotocol.io/specification/latest/basic/transports/streamable-http) | Origin validation must be switched on in FastMCP (below). Cloud Run serves only HTTPS on `run.app`, so TLS is given. |
| FastMCP 4.1.5 (the locked version) has `host_origin_protection` on `http_app()` and `FASTMCP_HTTP_HOST_ORIGIN_PROTECTION`, `FASTMCP_HTTP_ALLOWED_HOSTS`, `FASTMCP_HTTP_ALLOWED_ORIGINS`. **The default is `False`: off.** The installed code answers a bad Host with 421 and a bad Origin with 403. CORS is not enabled unless middleware is added. | Installed code in `fastmcp/server/http.py` and `fastmcp/settings.py`; [gofastmcp.com/deployment/http](https://gofastmcp.com/deployment/http) | `gcm-mcp` must turn it on explicitly. Add no CORS middleware. |
| Cloud Run ingress has three values: `all`, `internal`, `internal-and-cloud-load-balancing`. `internal-and-cloud-load-balancing` refuses requests that go straight to the `run.app` URL. The ingress setting applies to the `run.app` URL, domain mappings and load balancers alike. | [docs.cloud.google.com/run/docs/securing/ingress](https://docs.cloud.google.com/run/docs/securing/ingress) (no date on the page) | There is **no source-IP allowlist** in Cloud Run itself. Filtering by Anthropic's ranges means a load balancer with Cloud Armor in front. |
| Public access: the documented recommendation is to **disable the Invoker IAM check** (`--no-invoker-iam-check`), not to grant `allUsers`. `allUsers` fails under a domain-restricted-sharing organisation policy. The org policy `constraints/run.managed.requireInvokerIam` can forbid disabling the check (not enforced by default). | [docs.cloud.google.com/run/docs/authenticating/public](https://docs.cloud.google.com/run/docs/authenticating/public) | The production organisation may forbid it: see [Option A](#option-a-public-cloud-run-security-in-the-application-chosen). |
| The default `run.app` URL can be disabled (`--no-default-url`); "your custom URL mapping remains fully active". Disabling it breaks Cloud Scheduler, Tasks, Pub/Sub, Eventarc, Workflows, uptime checks and Firebase Hosting that target it. | Same ingress page | Not useful as a filter by itself. |
| Request timeout: default 300 s, up to 3600 s. `max-instances`: default 100 per revision, settable from 1. | [request-timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout), [max-instances](https://docs.cloud.google.com/run/docs/configuring/max-instances) | The caps in 07 (`max-instances=2`, 300 s) are valid settings. |

Not read: the Cloud Run, load balancer and Cloud Armor **price pages** (they did not load in the fetch tool). The monthly figures quoted on #82 (forwarding rule about $18, policy $5, $1 per rule) come from third-party summaries and are **not verified**.

---

## Decisions

| # | Decision | Reason |
|---|---|---|
| N1 | **`gcm-mcp` is public on Cloud Run: `ingress=all`, Invoker IAM check disabled, `max-instances=2`, in dev and in the first production deployment.** All access control is in the application (07). Option B is revisited under the [conditions below](#when-option-b-is-revisited). | Claude does not send a Google ID token, so Cloud Run IAM cannot authenticate it. The alternative that filters by source needs a load balancer, which costs money (NFR-2) and covers only part of the service ([Option B](#option-b-load-balancer-and-cloud-armor-not-adopted-for-now)). The first tests use an account connected to nothing else. |
| N2 | **Two Cloud Run services, `gcm-mcp` and `gcm-settings`** (confirms the decision of 2026-11-04 on #29). | One service has one service account and one ingress. Merging would give the browser-facing NiceGUI UI the identity that reads the token-store Fernet key and the `jwt_signing_key`. Reasons in [04](04-settings-server-design.md#contribution-to-one-service-or-two-29) and [Two services](#two-services-one-image). |
| N3 | **One container image**, started with a different command by each service (confirms 06 and 07). | Service account, secrets, ingress, scaling and timeout are per service, not per image. The image holds no secret. A two-target Dockerfile stays an option ([Two services](#two-services-one-image)). |
| N4 | **The standard way to try the real connector is the GCP dev deployment.** A tunnel to a local server is not part of the standard cycle. L2 (fake verifier) is never exposed. | Same OAuth client, same consent screen, same Cloud Run behaviour as what will run; no public URL pointing at a developer's machine. [Details below](#trying-the-real-connector-during-development). |
| N5 | **`host_origin_protection=True` with `allowed_hosts` set to the host of `BASE_URL`; no CORS middleware; access logs off.** | FastMCP's default is off, and the spec makes Origin validation a MUST. |
| N6 | **The caller's address is not used for any decision.** | Not verified to be reliable behind Cloud Run, and Anthropic's ranges can change. Option C is not adopted. |

---

## How Claude reaches `gcm-mcp`

```
Claude client (web, Desktop, mobile)
        │  the user adds https://gcm-mcp-<hash>.<region>.run.app/mcp as a custom connector
        ▼
Anthropic's cloud ──HTTPS──► Cloud Run front end ──► gcm-mcp (FastMCP, OAuthProxy)
                                                         │
User's browser ──HTTPS──► /authorize, /consent, /auth/callback
                                                         │
                                                         ├──► Google sign-in (browser redirect)
                                                         └──► Firestore, Secret Manager, Chat API
```

1. The user adds the connector in Claude (as a custom connector; the exact menu was not verified) with the `gcm-mcp` URL. The request that follows does **not** come from the user's machine but from Anthropic's cloud, so the address must be public HTTPS.
2. Claude calls `/mcp` with no token and gets `401` with `WWW-Authenticate` pointing at the protected-resource metadata. From there it discovers the authorisation server (which is `gcm-mcp` itself, through `OAuthProxy`), registers (CIMD, DCR, or the custom client ID the user types), and opens the user's browser at `/authorize`.
3. The browser shows `gcm-mcp`'s consent page, then Google's sign-in, then returns to `{BASE_URL}/auth/callback`. `gcm-mcp` then redirects the browser to Claude's callback with an authorisation code.
4. Claude (Anthropic's cloud) exchanges the code at the token endpoint and calls `/mcp` with the access token. Each call is validated as in 07 ("One user per request").

### Endpoints and who calls them

Paths are those `OAuthProxy` is expected to serve; the exact list is `[local]` item 3 below. The point of the table is the last two columns.

| Endpoint | Called by | Credential required | Answer to an anonymous caller |
|---|---|---|---|
| `POST /mcp` | Anthropic's cloud | Bearer token from `OAuthProxy` | `401` + `WWW-Authenticate`, before any tool or Firestore read |
| `GET /.well-known/oauth-protected-resource…` | Anthropic's cloud | none (public by spec) | Static metadata: the resource URL and the authorisation server |
| `GET /.well-known/oauth-authorization-server` | Anthropic's cloud | none (public by spec) | Static metadata: endpoint URLs, supported methods |
| `POST /register` (DCR, if enabled) | Anthropic's cloud | none | Registers a client. Redirect URIs restricted to Claude's callbacks (07); the registration stores a small entry in Firestore, see [Flood](#flood-and-cost-without-a-load-balancer) |
| Fetch of a client's CIMD URL | **`gcm-mcp` calls out** to a URL chosen by the client | none | Outbound request to a client-supplied URL: see [To verify](#to-verify) (SSRF) |
| `GET /authorize` | User's browser | none; the user must then pass the consent page and Google | Redirect to the consent page; rejected unless the redirect URI is allowed |
| consent page (`/consent`) | User's browser | browser session cookie set by `/authorize` | Refused without it |
| `GET /auth/callback` | User's browser (redirected by Google) | `state` bound to a transaction in Firestore | Refused unless the transaction exists and the browser cookie matches |
| `POST /token` | Anthropic's cloud | authorisation code + PKCE verifier, or refresh token | `400`/`401` |
| `/health` or similar | none, **not added** | | There is no unauthenticated health route, because FastMCP custom routes "are never protected by the server's authentication middleware". |

Everything not in this table does not exist. In particular: no settings page, no ACL read or write, and no tool is reachable without a token.

---

## What is exposed and what restricts it

### Option A: public Cloud Run, security in the application (chosen)

- **Setting:** `--ingress=all`, `--no-invoker-iam-check`, `--max-instances=2`, request timeout 300 s (as in 07).
- **Exposed to anyone on the internet:** the endpoints in the table whose "credential" column is "none". The attacker learns that the service is an MCP server and sees its OAuth metadata. They cannot call a tool, read an ACL or reach Chat.
- **What restricts it, all from 07:** `OAuthProxy` with the verifier's `audience` set to this service's client ID; consent page on; `allowed_client_redirect_uris` restricted to Claude's callbacks; `hd` check on every request in `DEPLOY_MODE=production`; `401` before any tool; fail closed (M9).
- **Added by this document (N5):**
  - `host_origin_protection=True`, `allowed_hosts` = the host part of `BASE_URL`. A request with another `Host` or an invalid `Origin` is refused (421/403 in the installed code). Anthropic's server-to-server requests are expected to carry no `Origin`; whether strict mode accepts that is `[local]` item 1.
  - No `CORSMiddleware`. Claude's connector is not browser JavaScript; there is no reason to allow any origin.
  - Access logs off in `uvicorn` (already in 07: the callback URL carries `code` and `state`).
  - No route outside the table, no unauthenticated health endpoint.
- **If the organisation forbids it.** The production organisation may enforce `constraints/run.managed.requireInvokerIam`, which blocks `--no-invoker-iam-check`. Then `gcm-mcp` cannot be public at all and the maintainer must get an exception for this one service (or use B with a different authentication story, which does not exist yet). To ask before #60.
- **Residual risk:** a flaw in the OAuth proxy or in FastMCP's HTTP stack is reachable from the internet without an account. The defences are the locked and audited dependencies (#50) and the fact that the service holds no data of its own beyond the encrypted token store. A flood of anonymous requests costs money: see below.
- **Cost:** none beyond the free tier.

### Option B: load balancer and Cloud Armor (not adopted for now)

`ingress=internal-and-cloud-load-balancing`, default URL disabled, an external Application Load Balancer with a managed certificate on a custom domain, and a Cloud Armor policy allowing only Anthropic's ranges.

- **For:** the service is no longer reachable by anyone else; floods are dropped before Cloud Run; one more layer.
- **Against:**
  - A fixed monthly cost for the forwarding rule and the policy, and a domain name. The amount is **not verified** (see above). It breaks NFR-2 and needs the owner's explicit acceptance.
  - **It cannot cover the whole service.** `/authorize`, the consent page and `/auth/callback` are called by the **user's browser**, not by Anthropic, so the allowlist can apply only to `/mcp`, `/token`, `/register` and the metadata paths, with path rules. The browser-facing part stays open to the internet in any case.
  - The ranges are published "without notice", and a public report (anthropics/claude-ai-mcp#422, not read here) says a connector received no traffic although the `/21` was allowed. A silent failure mode that would have to be tested.
  - The Invoker IAM check still has to be disabled, so the application controls are needed anyway.

### Option C: A plus an address check on `/mcp` (not adopted)

Rejected for now (N6). Cloud Run does not document what `X-Forwarded-For` holds for requests that go straight to `run.app`, and a check on a list that Anthropic can change adds a failure mode without a barrier that holds on its own.

### When Option B is revisited

Revisit before any of these happens, and record the answer in this document:

1. The connector serves accounts that are connected to real company data, rather than a test account.
2. The first production deployment (#60) in the company's organisation. The company may already run a load balancer and Cloud Armor, which removes the cost objection, or may forbid an unauthenticated service, which removes Option A.
3. Traffic logs show scanning that the application controls handle only by spending free-tier quota.

### Flood and cost without a load balancer

- **Cap:** `max-instances=2` bounds concurrency and the instance cost. A request that waits for capacity fails after up to 30 s (Cloud Run docs), so a flood degrades the service for real users rather than raising the bill without limit.
- **Cheap rejection:** an unauthenticated request is answered with `401` by the token check before any tool, Firestore read or Secret Manager call (07, "One user per request"). The only anonymous paths that touch Firestore are `/register` and `/authorize`; their cost per request is a write or read each, to be counted `[local]`.
- **Free tier (as used in 04, not re-verified today):** 2 M requests, 180 000 vCPU-s and 360 000 GiB-s a month. Whether requests the application rejects with 401 are billed as ordinary requests was **not found** in the documentation read; assume they are.
- **Controls:** a billing budget alert on the project (#60), and a known emergency step: `gcloud run services update gcm-mcp --ingress=internal` closes the service to the internet at once, and `--ingress=all` reopens it.
- **Registration spam:** if DCR is enabled, every anonymous `POST /register` can create a Firestore entry. The store's TTL (07, "Token store") bounds it; the number of entries a flood creates and whether FastMCP limits registrations are `[local]` item 4.

---

## Two services, one image

Confirms the decisions of 2026-11-04 (two services) and 2026-11-06 (one image) on #29/#82, with the network argument added.

- **Two services.** A Cloud Run service has one service account and one ingress setting. The connector must be public to Anthropic's cloud (this document); the settings page is public to users' browsers but speaks a different protocol and needs session affinity and websockets (04). Merging them would also give the UI the identity that can read the token-store Fernet key and the `jwt_signing_key`, so a flaw in the UI (the larger surface, it renders names chosen by others) would expose the key that decrypts every user's Chat tokens. Both scale to zero within the free tier, so cost is not a reason to merge. The reasons in full are in 04 ("Contribution to one service or two"); this section only adds the exposure argument.
- **One image.** Everything that matters for configuration is set per service. The image holds no secret; the service account does. A second image would change the dependency set (the connector would not contain NiceGUI), which narrows supply-chain exposure (an installed package is not inert, for example `.pth` files run at interpreter start) but not the reachable attack surface. The cost is splitting the `remote` extra, two build targets and smoke tests, and a rule that both images share one commit SHA to keep NFR-6 and the readers-first schema rollout checkable.
- **Revisit** only for a concrete reason: a supply-chain concern about the connector's dependencies, or a measured cold-start problem. The middle path is one Dockerfile with two build targets, one pipeline, one SHA.

---

## Trying the real connector during development

Three ways, in order of preference.

| | What | Reaches the real Claude connector? | Verdict |
|---|---|---|---|
| **GCP dev deployment** | `gcm-mcp` on Cloud Run in the personal dev project (#86), real Firestore in that project, External consent screen in Testing, `DEPLOY_MODE=development`. | **Yes**, and it is the only place all of Cloud Run's behaviour is real. | **Standard.** |
| **L3 with a tunnel** | Local server, real Google sign-in, emulator, exposed through a tunnel (Cloudflare Tunnel, ngrok, …). | Yes, but through a public URL that points at a developer's machine. | **Not part of the cycle.** Allowed only as a maintainer's decision, with the conditions below. |
| **L2/L3 local** | FastMCP client or MCP Inspector against `localhost`. | **No.** Anthropic's cloud cannot reach `localhost`. | Stays the daily loop, as in 07. |

### Why the GCP dev deployment is the standard

- It is the same path production will use: same Cloud Run ingress and TLS, same `run.app` hostname rules, same Secret Manager and Firestore.
- It does not depend on a tunnel product, a stable hostname, or a laptop being online.
- Every item that needs a public URL is tried in one place (the `[GCP]` items in 07), so there is a single OAuth client with a single redirect URI per environment.
- The dev project is already a decision (#86), costs nothing, and holds only test data.

Smallest dev deployment that makes it possible (follows 06, "Order of work", step 3):

1. The dev project, Firestore database, Secret Manager secrets and Artifact Registry from the manual bootstrap in 06.
2. The External consent screen in Testing with the developer's account as a test user, and the connector's OAuth client with redirect URI `{BASE_URL}/auth/callback`. `BASE_URL` is the `run.app` address, known only after the first deploy: deploy once, read the address, create or edit the client, redeploy with `BASE_URL` set.
3. `gcm-mcp` deployed with `DEPLOY_MODE=development`, `ingress=all`, `--no-invoker-iam-check`.
4. In Claude: Customize → Connectors → Add → Add custom connector, enter the `/mcp` URL; choose "Use your own OAuth client" if the server does not take DCR/CIMD, or the automatic option otherwise. On Team and Enterprise an Owner (Team) or a user with the right custom role (Enterprise) adds it under Organization settings → Connectors → Add → Custom → Web, then members click Connect. Sign in with the test user.
5. Run the `[GCP]` checks and record the results here.

The 7-day refresh token expiry of Testing apps (#86) means signing in again once a week; this is expected.

### The tunnel, if the maintainer wants it anyway

Not a supported path; if used, it is bound by these conditions:

- **What it exposes:** the developer's machine serves a public HTTPS URL for as long as the tunnel runs. Anyone can reach the OAuth endpoints and, with a valid token, the tools. Real Google sign-in must be on: **the L2 fake verifier is never served through a tunnel**, and the entry point keeps refusing it unless asked explicitly.
- **OAuth client:** a development client in the dev project whose redirect is the tunnel's HTTPS URL, with the same External/Testing screen. A tunnel hostname that changes on every start (common on free plans) means editing the redirect URI each time; use a reserved hostname.
- **Data:** the emulator holds only test data. The Chat token that sign-in produces is a real token for the test user's real spaces, so the allowlist is restricted to a test space, as for the integration tests.
- **Lifetime:** hours, not days. Stop it when the session ends.
- **What it cannot test:** Cloud Run behaviour (cold start, ingress, the real `Host`), so it does not replace the GCP deployment.

---

## Transport hygiene

| Item | Rule | Status |
|---|---|---|
| TLS | Only the HTTPS `run.app` address. No custom HTTP listener, no `http://` in any redirect URI except `localhost` in local runs. | Cloud Run terminates TLS; `[GCP]` check that `http://` is redirected or refused. |
| `Origin` / `Host` | `host_origin_protection=True`, `allowed_hosts=[BASE_URL host]` (N5). A test sends a bad `Host` and a bad `Origin` and expects refusal. | FastMCP option exists in 4.1.5; behaviour of strict mode with no `Origin`: `[local]`. |
| CORS | None. | Nothing to add. |
| Logs | Access logs off; masking from 07. | Already in 07. |
| Protocol version | The server answers with the 2026-07-28 behaviour where it can, and the older sessionless behaviour otherwise (07, M6). | Which version Claude speaks: `[GCP]` item 5. |

---

## To verify

`[local]` can be checked on a developer's machine; `[GCP]` only against the dev deployment with the real connector. These are also added to the list in 07.

1. `[local]` **Strict Host/Origin mode:** that `host_origin_protection=True` accepts a request with no `Origin` (a server-to-server call) and refuses a wrong one with 403 and a wrong `Host`.
2. `[local]` **No path outside the table:** the real list of routes `OAuthProxy` and `http_app()` register, compared with the endpoint table (names included).
3. `[local]` **CIMD fetch:** that FastMCP's fetch of a client's metadata URL rejects private and link-local addresses (SSRF) and limits size and time; if not, set `enable_cimd=False` and rely on DCR or a pre-registered client.
4. `[local]` **Registration:** whether FastMCP limits `POST /register`, how many Firestore entries a flood creates, and whether they expire.
5. `[GCP]` **Which source addresses call `/mcp`, `/token` and `/register`** from claude.ai and from Claude Desktop, compared with the published ranges (the logs hold them if access logging of the Cloud Run request log is on; the app's own access logs stay off).
6. `[GCP]` **Callback and registration:** that Claude still calls back to `https://claude.ai/api/mcp/auth_callback` only, and which of CIMD, DCR or a typed client ID it ends up using with `OAuthProxy`.
7. `[GCP]` **MCP protocol version** Claude speaks (decides the confirmation of sends, see #63 and 07).
8. `[GCP]` **No IAM-authenticated calls:** that nothing in the flow needs the service to accept IAM-authenticated requests, with the Invoker IAM check disabled.
9. `[GCP]` **Client address:** what the request seen by the app contains for the caller (`X-Forwarded-For`) behind `run.app`. Only needed if Option C is reconsidered.
10. `[GCP]` **Billing of rejected requests:** that a `401` costs only a request; the price pages did not load for this document.
11. `[GCP]` **Option B costs** (forwarding rule, Cloud Armor policy and rules) from Google's price pages, before B is discussed with the owner.
12. `[GCP]` **Organisation policy** `constraints/run.managed.requireInvokerIam` in the company's organisation: whether it is enforced, and whether an exception for `gcm-mcp` is possible.
13. `[GCP]` **Cold start against the 10 s limit:** Claude gives discovery, registration and token endpoints 10 seconds. A cold `gcm-mcp` (Python, FastMCP, Firestore client, secrets) must answer within that, including on the first request after idle; measure it. If it does not fit, the remedies are lazy loading in the lifespan or `min-instances=1` (a cost, NFR-2).
14. `[GCP]` **Strict Origin and the connector:** the testing page lists strict `Origin` checks among the causes of an `initialize` timeout. Confirm that `host_origin_protection=True` does not block Anthropic's requests (same item as 1, from the real side).
15. `[GCP]` **`hd`/consent behaviour with the typed-client-ID path** is not part of this document; see 07.

---

## Out of scope

- The OAuth proxy (#79) and the HTTP entry point (#80): this document gives them the exposure model.
- Terraform and deployment instructions (#60, #61), and the `check` command (#71), which should add: reach the public URL as an unauthenticated caller and expect `401` with `WWW-Authenticate`, and a request with a wrong `Host` or `Origin` expecting refusal.
- Any change that weakens the per-space allowlist, the `hd` check or the consent page (`SECURITY.md`).
