# 08 — Operator setup: what both remote services share

Step-by-step guide for the person who sets up **one instance** of the remote connector (one GCP project, one Workspace organisation in production). It covers what `gcm-settings` and `gcm-mcp` share: the project, the consent screen, the OAuth clients, the secrets and Firestore. Deploying each service is in its own document ([10](10-deploy-settings.md) for `gcm-settings`, #61; [11](11-deploy-gcm-mcp.md) for `gcm-mcp`, #60).

It is part of #59 and follows the design in [04](04-settings-server-design.md), [06](06-dev-cycle.md) and [07](07-mcp-server-design.md). Parts of #59 that are not here yet: the configuration reference, Terraform, releases, operating it, the user guide and the migration from the local server.

**Order:** this guide → #61 and #60 (the two deployments, in the order their documents give) → run the `check` commands, the `gcp` tests and the assisted sign-in ([Step 8](#step-8--check-the-setup)).

## How to read this guide

Every step carries a tag:

| Tag | Meaning |
|---|---|
| **[MANUAL]** | You do it by hand in the console or with `gcloud`. Google offers no supported way to script it, or the project has not automated it. |
| **[TERRAFORM #96]** | Will be created by Terraform ([#96](https://github.com/nuccio/google-chat-mcp/issues/96), not implemented yet). Until it is, do it by hand with the command shown, and skip the step once Terraform exists. |
| **[CHECK #71]** | A check built in [#71](https://github.com/nuccio/google-chat-mcp/issues/71) tells you whether the step was right: see [Step 8](#step-8--check-the-setup). It complements the "Check" line of the step; it does not replace the manual checklist where it says `not-checkable`. |

Where dev and production differ, the step says so, and the table in the next section lists everything in one place.

**About the sources.** Facts about Google products were read in the official pages cited below on **2026-11-06**. What I could not confirm is marked **(unverified)**. Google changes console labels and limits: when a page differs from this guide, the page wins, and this guide should be corrected.

## Dev and production at a glance

| | Development | Production |
|---|---|---|
| GCP project | On the maintainer's personal Google account, no organisation (#86) | Inside the company's Workspace organisation |
| Consent screen audience | **External**, publishing status **Testing**, people who may sign in listed as **test users** | **Internal** |
| Who can sign in | Only the listed test users (at most 100); Google enforces the list, the service does not repeat it | Only accounts of the organisation (Google), plus the `hd` check in the service |
| Refresh tokens and sign-in | Expire **7 days** after consent: sign in again once a week | Not expected to expire after 7 days **(unverified: item 8 of the "to verify" list in [07](07-mcp-server-design.md#to-verify-before-implementation), tried on the first production deployment)** |
| "Unverified app" warning | Shown to test users before consent | Not expected for an Internal app **(unverified, same item)** |
| `DEPLOY_MODE` | `development`: an account without `hd` is accepted; a present `hd` must be in `ALLOWED_DOMAINS` | `production` (also the default when the variable is missing or unknown): `hd` must be present and in `ALLOWED_DOMAINS` |
| Where `DEPLOY_MODE` is set | `infra/envs/dev` (#96) | Never set, or set to `production` |
| `ALLOWED_DOMAINS` | Any value; accounts without `hd` ignore it in this mode | The organisation's primary domain, and secondary domains if wanted |
| What cannot be tried | `hd` of a Workspace account and the Internal screen: tried on the first production deployment | — |
| Bootstrap done by | The maintainer, by hand | The maintainer, by hand, in the company organisation |

Each environment has its **own** OAuth clients, secrets and Firestore database; nothing is copied between them (07, "Environments and deploy"). Whether dev and production are two projects is an open decision in [06](06-dev-cycle.md#open-decisions) (recommended: two); this guide assumes two.

## Step 1 — GCP project, billing and region

1. **[MANUAL]** Create the project.
   - Production: create it **inside the Workspace organisation** (the folder or organisation the company's IT assigns). An Internal consent screen needs the project's parent to be the organisation.
   - Development: create it on the personal account; it has no parent.
   - Name the project so that the environment is obvious (for example `gcm-dev` / `gcm-prod`). The project ID cannot be changed later.
2. **[MANUAL]** Link a billing account. The free tiers below are granted per billing account, and Firestore's free quota and Secret Manager's free versions need billing enabled on the project, though you stay at zero cost within the limits.
3. **[MANUAL]** Set a **budget alert** on the billing account (Billing → Budgets & alerts), for example a low amount, so a mistake is noticed. A budget alert does not stop spending **(by Google's design; unverified here)**.
4. **[MANUAL]** Choose one **region** and use it for Cloud Run, Firestore and Artifact Registry. Keep them together.
   - Cloud Run's free tier is not tied to a region in the free-features page, but the pricing page applies the free tier "as a spending based discount using Tier 1 pricing". Pick a Tier 1 region. In Europe the Tier 1 list includes `europe-west1` (Belgium), `europe-west4` (Netherlands), `europe-west8` (Milan), `europe-west9` (Paris), `europe-north1` (Finland), `europe-southwest1` (Madrid).
   - **(unverified)** whether Firestore's free quota is available in every region, and whether the chosen Firestore location can be one of those. Open item 4 of the "to verify" list in [04](04-settings-server-design.md#to-verify-before-implementation). Check on the Firestore pricing page for the location you pick before creating the database; the location cannot be changed afterwards.
   - Latency to Google Chat and to users is not a concern at this volume; the region decision is about cost and data location policy of the company.

**Check.** `gcloud projects describe PROJECT_ID` shows the project. For production, its `parent` is the organisation: `gcloud projects describe PROJECT_ID --format="value(parent.type,parent.id)"` prints `organization` and the number. **[CHECK #71]** runs the same test (the `gcp` tests; production only).

### Free-tier limits that the design relies on (NFR-2)

Read from the official pages on 2026-11-06. The design values are those of [04, "Runtime and cost"](04-settings-server-design.md#runtime-and-cost).

| Service | Free allowance | Source |
|---|---|---|
| Cloud Run, request-based billing | 2 million requests, 180,000 vCPU-seconds and 360,000 GiB-seconds per month (aggregated by billing account); 1 GB outbound from North America | [Cloud Run pricing](https://cloud.google.com/run/pricing), [free features](https://cloud.google.com/free/docs/free-cloud-features) |
| Firestore | **One** free database per project (named databases have no free quota): 1 GiB stored, 50,000 reads, 20,000 writes and 20,000 deletes **per day**, quota reset around midnight Pacific time | [Firestore pricing](https://cloud.google.com/firestore/pricing) |
| Firestore TTL deletes | **Not part of the free quota: billed** at $0.01 per 100,000 deletions (price on the page on 2026-11-06). At this volume the cost is negligible. This answers item 3 of the "to verify" list in 04 | [Firestore pricing](https://cloud.google.com/firestore/pricing) |
| Secret Manager | 6 active secret versions, 10,000 access operations and 3 rotation notifications per month. A version is active when Enabled or Disabled | [Secret Manager pricing](https://cloud.google.com/secret-manager/pricing) |
| Cloud Logging | First 50 GiB of log data per project per month (default retention) | [free features](https://cloud.google.com/free/docs/free-cloud-features) |
| Artifact Registry | 0.5 GB of storage per month | [free features](https://cloud.google.com/free/docs/free-cloud-features) |

What this means for the setup:

- Keep the **first** database of the project and use only it (the `(default)` database unless you have a reason to choose another ID; `FIRESTORE_DATABASE` selects it). Do not create a second database: it is billed.
- The **secrets** are five active versions at most: the settings client secret, the connector client secret, the Fernet key and the `jwt_signing_key`, plus one spare for a rotation. A sixth active version is billed; **destroy** old versions after a rotation, do not only disable them.
- The item closest to the limit is Cloud Run instance time of `gcm-settings` (open tabs); [04](04-settings-server-design.md#runtime-and-cost) explains it, #61 says how to measure it.

## Step 2 — Enable the APIs

**[TERRAFORM #96]** (enabled APIs are in its scope). By hand until then, with the project selected:

```bash
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  logging.googleapis.com \
  chat.googleapis.com \
  --project PROJECT_ID
```

`chat.googleapis.com` is the Google Chat API; the local setup enables it the same way ([google-cloud.md](../google-cloud.md)). The list is derived from the services in the design. Terraform's final list is authoritative; it may add `iam.googleapis.com` and others it needs **(unverified until #96)**.

**Check.** `gcloud services list --enabled --project PROJECT_ID` lists them. **[CHECK #71]** the `gcp` tests check the required APIs.

## Step 3 — Consent screen (Google Auth Platform)

**[MANUAL], always.** Google offers no supported API for it: the IAP OAuth Admin API (`gcloud iap oauth-brands`, Terraform `google_iap_brand`) was shut down on 19 March 2026 ([IAP page](https://docs.cloud.google.com/iap/docs/programmatic-oauth-clients)). No replacement was found **(unverified: re-check the Google Auth Platform documentation before the bootstrap)**. The two environments differ here.

Open **Google Auth Platform** in the console for the project (in older consoles: APIs & Services → OAuth consent screen). If it says "Google Auth Platform not configured yet", click **Get started**. The wizard asks for the same things as the tabs below.

### 3.1 Branding

| Field | Value |
|---|---|
| App name | Name users will recognise on the consent page, for example `Google Chat connector` (what they see when they sign in to `/settings` and when Claude connects) |
| User support email | A mailbox that is read. Production: a company address |
| Developer contact | A mailbox that is read |
| Logo, authorised domains | Optional. A logo makes Google ask for verification of an External app in production; an Internal app is not verified **(unverified: confirm on the production deployment)**. Do not set a logo in dev |

**Authorised domains** are needed only for the links to a privacy policy or terms, which this project does not require. `run.app` is a public suffix and cannot be added as an authorised domain **(unverified; if the console asks, leave the field empty and say so in the PR/issue)**.

### 3.2 Audience

- **Production: Internal.** Only accounts of the organisation that owns the project can authorise the app. Google answers other accounts with an `org_internal` error ([Manage App Audience](https://support.google.com/cloud/answer/15549945)). This is why the project must be inside the organisation. **No publishing status step**: an Internal app has no Testing / Production split **(unverified for the 7-day limit, see the table above)**.
- **Development: External, publishing status Testing.** The app stays in Testing; **do not click Publish app**. Under **Test users** add every Google account that will sign in (the maintainer, any developer). At most 100 test users; each one counts once added. Sign-in is refused for any other account ([Manage App Audience](https://support.google.com/cloud/answer/15549945)).
  - Test users see an "unverified app" warning before consent, and **their authorisation, and so their refresh token, expires seven days after consent**. The exception for apps that only ask `openid`, `email` and `profile` does not apply: this app asks for Chat scopes. Plan for a weekly sign-in.
  - Add test users when needed; a new developer who cannot sign in is almost always missing from this list.

### 3.3 Data Access (scopes)

Add the scopes the services request. Add them on the consent screen **even though Google appears not to enforce this list for an Internal app** (so no check can detect a missing scope: #71, "Not automated"). It is documentation of what the app will ask, and it matters in External.

| Scope | Asked by | Why |
|---|---|---|
| `openid`, `email` | both services | The `sub` that identifies the user, the verified email, and the `hd` claim |
| `https://www.googleapis.com/auth/chat.spaces.readonly` | `gcm-settings` and `gcm-mcp` | List and check the user's spaces |
| `https://www.googleapis.com/auth/chat.messages.readonly` | `gcm-mcp` | Read messages |
| `https://www.googleapis.com/auth/chat.messages.create` | `gcm-mcp` | Send messages |
| `https://www.googleapis.com/auth/chat.memberships.readonly` | `gcm-mcp` | List members |

The four Chat scopes are those of local mode ([google-cloud.md](../google-cloud.md)), the connector adds `openid` and `email` ([07](07-mcp-server-design.md)). `gcm-settings` asks only `openid email chat.spaces.readonly` ([04](04-settings-server-design.md#oauth-client)). **(unverified)** the final scope list of the connector depends on #49; if the code asks for another scope, update this table in the same PR.

### 3.4 What differs, in one place

| | Dev (External, Testing) | Prod (Internal) |
|---|---|---|
| Audience | External | Internal |
| Test users | Required | Not used |
| Publishing | Stay in Testing | Not applicable |
| Weekly re-sign-in | Yes | Not expected **(unverified)** |
| Second barrier (`hd`) | Off (`DEPLOY_MODE=development`) | On |

**Check (partly manual: no API reads the consent screen).**

1. Audience tab shows the right type (and, in dev, "Testing" and the right test users).
2. Data Access shows the scopes of the table.
3. **[CHECK #71]** the assisted sign-in script: one sign-in with an account that must succeed, and (production only) one with an account **outside** the organisation that must be refused. The refusal is what proves the screen is Internal. In dev only the first part applies.

## Step 4 — OAuth clients (two, "Web application")

**[MANUAL]**, always: Terraform does not manage web OAuth clients ([06](06-dev-cycle.md#what-is-done-only-on-gcp)). Create both under the one consent screen, in **Google Auth Platform → Clients → Create client** (older consoles: APIs & Services → Credentials → Create credentials → OAuth client ID).

| | `gcm-settings` client | `gcm-mcp` (connector) client |
|---|---|---|
| Application type | Web application | Web application |
| Name | `gcm-settings` (add `-dev` / `-prod`) | `gcm-mcp` (add `-dev` / `-prod`) |
| Authorised redirect URI | `{SETTINGS_BASE_URL}/settings/callback` | `{BASE_URL}/auth/callback`, with `BASE_URL` the address of `gcm-mcp` |
| Authorised JavaScript origins | none | none |
| Secret stored in | Secret Manager, readable by `gcm-settings@` only | Secret Manager, readable by `gcm-mcp@` only |

Why two clients: they keep two secrets apart, and `gcm-settings` has no access to the connector's secrets (04). Both see the same `sub`.

**The redirect URIs depend on the service addresses**, which Cloud Run assigns when the service is first deployed. Order, as in #61 and #60:

1. Create the clients now (or after the first deploy) with the redirect URIs unknown. Google needs at least the field in order to save a Web client; if the console refuses to save without one, use the first deploy's address and come back **(unverified: the console's behaviour)**.
2. After the first deployment of each service, read its address and add the redirect URI to the client. #61 and #60 give the exact steps.
3. For local development (level L3 of [06](06-dev-cycle.md)) a separate **development client** with `http://localhost:PORT/settings/callback` / `http://localhost:PORT/auth/callback` is used. It is a third client, never reused in the deployed environment.

**Storing the secrets.** When the console shows the client secret, put it straight into Secret Manager (Step 5); do not paste it into a file in the repository, a chat or a shell history.

**Check.** The console lists both clients as "Web application". **[CHECK #71]**: `python -m google_chat_mcp.settings check` and `python -m google_chat_mcp.remote check` ask Google whether the client secret is accepted and the redirect URI is registered. The probes are not verified on a real project yet (Step 8).

## Step 5 — Secrets (Secret Manager)

Four secrets in total.

| Secret | Value | Readable by | Created by |
|---|---|---|---|
| settings client secret | From Step 4 | `gcm-settings@` only | Container: **[TERRAFORM #96]**. Value: **[MANUAL]** |
| connector client secret | From Step 4 | `gcm-mcp@` only | Container: **[TERRAFORM #96]**. Value: **[MANUAL]** |
| Fernet key (token store) | Generated by you | `gcm-mcp@` only | Container: **[TERRAFORM #96]**. Value: **[MANUAL]** |
| `jwt_signing_key` | Generated by you | `gcm-mcp@` only | Container: **[TERRAFORM #96]**. Value: **[MANUAL]** |

Terraform creates the containers, **not the values** ([06](06-dev-cycle.md), [07](07-mcp-server-design.md)). Until #96 exists, create the container by hand:

```bash
gcloud secrets create SECRET_NAME --replication-policy=automatic --project PROJECT_ID
```

Add a version. The value is read from the standard input, so it never appears in the command line or the shell history. Generate the two keys without printing them. A Fernet key is 32 random bytes in URL-safe base64, which is what the first command produces:

```bash
openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n' \
  | gcloud secrets versions add FERNET_SECRET_NAME --data-file=- --project PROJECT_ID
```

```bash
python3 -c "import secrets, sys; sys.stdout.write(secrets.token_urlsafe(48))" \
  | gcloud secrets versions add JWT_SECRET_NAME --data-file=- --project PROJECT_ID
```

The exact secret names, the format that the code expects for `jwt_signing_key` and how the services read these secrets (mounted or fetched at start-up) are defined by #96 and #53 **(unverified until then: use the names and formats of those issues, not the placeholders above)**. For the client secrets, paste the value into `gcloud secrets versions add NAME --data-file=-` from a terminal prompt (type it, end with Ctrl-D), or from a file you delete afterwards. Never commit it.

Per-environment: the values are different in dev and production, and are never copied.

**Check.** `gcloud secrets versions list NAME --project PROJECT_ID` shows one enabled version for each of the four. **[CHECK #71]**: the `gcp` tests check that each container has an enabled version; the `check` commands check that the Fernet key and the `jwt_signing_key` are readable and valid, and that `gcm-settings` has **no** access to the connector's secrets.

The IAM bindings (who may read which secret) are **[TERRAFORM #96]**. Until then, grant `roles/secretmanager.secretAccessor` on each secret to its service account only; see 04 and 07 for the table.

## Step 6 — Firestore

1. **[TERRAFORM #96]** / **[MANUAL]** Create the database: Native mode, **Standard edition** **(unverified: the free quota is documented for the "free database"; confirm on the pricing page that Standard edition is the one it applies to)**, in the region of Step 1, and use the **first** database of the project (the `(default)` database unless you have a reason to name it; one free database per project).

   ```bash
   gcloud firestore databases create --database='(default)' --location=REGION --edition=standard --project PROJECT_ID
   ```

   The location cannot be changed after creation.

2. **Security Rules: deny all client access.** When the console asks for the initial rules, choose **Restrictive** (the console's wording; the Firebase mobile and web SDKs are not used) ([Manage databases](https://cloud.google.com/firestore/native/docs/manage-databases)). This is **defence in depth only**: the server client libraries bypass Security Rules and are controlled by IAM ([Security rules](https://cloud.google.com/firestore/native/docs/security/get-started)). Nothing in this project talks to Firestore from a browser. Who can read what is decided by IAM and by the code (04, "Access to Firestore by service"). If Terraform or the console leaves the database with other rules, set them to:

   ```
   rules_version = '2';
   service cloud.firestore {
     match /databases/{database}/documents {
       match /{document=**} {
         allow read, write: if false;
       }
     }
   }
   ```

3. **TTL policies** ([Firestore TTL](https://cloud.google.com/firestore/native/docs/ttl)). **[TERRAFORM #96]**. By hand, one policy per collection group and field:

   | Collection group | Field | Used by |
   |---|---|---|
   | `sessions` | `expire_at` | `gcm-settings`, sessions |
   | `login_transactions` | `expire_at` | `gcm-settings`, sign-in attempts (10 minutes) |
   | `audit` | `expire_at` | Audit records, one year (`acls/{sub}/audit`) |
   | Token store of `OAuthProxy` | **(unverified)**: the field and layout depend on the Firestore store of `py-key-value-aio` (item 4 of the "to verify" list in 07) | `gcm-mcp` |

   ```bash
   gcloud firestore fields ttls update expire_at \
     --collection-group=sessions --enable-ttl --async --project PROJECT_ID
   ```

   Repeat for `login_transactions` and `audit`. The TTL policy deletes late (asynchronously) and its deletions are billed (see the table in Step 1); the services also check expiry in code, so a late deletion is not a security gap.

   A TTL policy on a collection group named `audit` applies to every `audit` subcollection, which is intended.

4. **IAM.** Both services get `roles/datastore.user` with an IAM condition on this database. IAM cannot separate collections; the code does (04). **[TERRAFORM #96]**.

**Check.** `gcloud firestore databases list --project PROJECT_ID` shows one database in the right location. `gcloud firestore fields ttls list --project PROJECT_ID` lists the policies. **[CHECK #71]**: both `check` commands test that Firestore is reachable with the service's own account and that the TTL policies exist (they report `not-checkable` when the account may not read them).

## Step 7 — What to give to #61 and #60

At this point the shared setup is done. The next documents need these values (a table for the operator to fill in privately; do not put it in the repository):

- project ID and region; the Firestore database ID;
- the two client IDs, and the two secrets in Secret Manager;
- the Fernet key and `jwt_signing_key` secrets;
- `ALLOWED_DOMAINS` (production: the organisation's domains; dev: only matters for accounts with `hd`); `DEPLOY_MODE` (dev only); `POLICY_*` ([04](04-settings-server-design.md#admin-policy)).

`SETTINGS_BASE_URL` and `BASE_URL` are known after the first deploy; #61 and #60 say how to read them and complete the redirect URIs.

## Step 8 — Check the setup

**[CHECK #71]** Three tools tell you whether Steps 1–7 above were done right. None of them changes anything in the project, and none prints a secret value.

| Tool | Where it runs | What it checks |
|---|---|---|
| `python -m google_chat_mcp.remote check` (`gcm-mcp`) and `python -m google_chat_mcp.settings check` (`gcm-settings`) | As a Cloud Run job with **the service's own service account and environment**, so each service checks only what it can see | Configuration variables, Firestore access, the TTL policies, the secrets (and that `gcm-settings` cannot read the connector's), the OAuth client ID, secret and redirect URI as Google sees them, the public invoker setting |
| `uv run pytest -m gcp` ([tests/README.md](../../tests/README.md#gcp-tests)) | Your workstation, with your own `gcloud` login | The project's parent is the organisation (production), the required APIs, each secret container has an enabled version, the Cloud Run services are public |
| `python -m google_chat_mcp.remote.signin` | Your workstation, with a **development** OAuth client | One real sign-in: granted scopes, audience, `email_verified`, `hd`; and, in production, that an account outside the organisation is refused |

**Reading the output.** One line per check: `ok`, `FAIL` or `not-checkable`, the check's name, what was found and, unless it is `ok`, the document section that tells you what to do. The exit code is 1 when any check is `FAIL` (so a pipeline can use it) and 0 otherwise. `not-checkable` is **not** a pass: it means the check could not be done from there (for example, the consent screen has no API) and the line says what to do by hand. A project that is not configured yet fails with a readable list; nothing needs to be set up before running it, which is how you see what is missing.

**Running `check` as a job.** The image is the one of the services (`ENTRYPOINT python -m`), so only the arguments differ. Use the same service account, variables and secrets as the service, and read the output from the log (the commands are the ones in [11](11-deploy-gcm-mcp.md#step-4--the-cloud-run-service) and [10](10-deploy-settings.md#step-7--create-or-update-the-cloud-run-service) with `gcloud run jobs` instead of `gcloud run deploy`; the flags of `gcloud run jobs` were **not re-read (unverified)**, `gcloud run jobs deploy --help` wins):

```bash
gcloud run jobs deploy gcm-mcp-check \
  --project PROJECT_ID --region REGION --image "$IMAGE" \
  --args=google_chat_mcp.remote,check \
  --service-account gcm-mcp@PROJECT_ID.iam.gserviceaccount.com \
  --max-retries=0 \
  --set-env-vars="…the same as the service…" \
  --set-secrets="…the same as the service…"
```

```bash
gcloud run jobs execute gcm-mcp-check --project PROJECT_ID --region REGION --wait
```

```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="gcm-mcp-check"' \
  --project PROJECT_ID --freshness 15m --order asc --format='value(textPayload)'
```

For `gcm-settings` use `gcm-settings-check`, `--args=google_chat_mcp.settings,check` and the settings service account, variables and secret. A failed check makes the execution fail, which is the intended signal.

**What `check` cannot see.**

- **The consent screen** (type, test users, scopes, texts): Google offers no API for it, so the line is always `not-checkable`. The checklist of Step 3 and the assisted sign-in are the check.
- **The organisation parent and the enabled APIs**: a service account of the service does not have the roles to read them. They are in the `gcp` tests.
- **TTL policies**: reading them needs an index-settings permission that the service's Firestore role may not include **(unverified)**. If so the line is `not-checkable` with the `gcloud firestore fields ttls list` command, and the `gcp` tests are not a substitute: use the command.
- **The probes at Google's endpoints** (redirect URI, client secret) rely on how Google answers an unauthenticated request. **They have not been tried on a real project (unverified).** A probe that gets an answer it does not recognise reports `not-checkable`, never `ok`; the redirect URI probe also sends a URI nobody registered, and reports `not-checkable` when Google accepts that too.
- **Logs and alerts**: the `acl_change` log line of 04 is not emitted by the code (it was not found in `main` on 2026-11-07), and `check` does not read logs.
- **A wrong `Host` or `Origin`** (09) is covered by the unit tests, not by `check`.

**The assisted sign-in.** Create a third, **development** client (Step 4, point 3) with the redirect URI `http://localhost:8765/callback`, download its JSON to a place outside the repository, and run:

```bash
uv run python -m google_chat_mcp.remote.signin --client-secrets PATH/TO/dev-client.json \
  --service mcp --domain example.com
```

It prints an address; open it, sign in with an account of the organisation, and come back to the terminal. It must end with every line `ok`. In production run it once more with `--expect refused` and an account **outside** the organisation: Google shows `Error 403: org_internal` and sends nothing back, so the script waits for `--timeout` seconds and reports `not-checkable`: read the page, and the refusal is what proves the consent screen is Internal. A code arriving instead is a `FAIL` (the screen is not Internal). In development (External, Testing) only the first run applies: add `--development`, because a personal test user has no `hd`. Use `--service settings` for the settings scopes.

## Not covered yet

Remaining parts of #59, to write when their subject exists: the configuration reference (every variable and secret, which service reads it), why the MCP side's read-only access to the ACL is enforced in code and not by IAM (the short answer is in 04, "Access to Firestore by service"), what Terraform creates and how to apply it (#96), releases, operating it (logs, the `acl_change` alert, cold starts), the user guide and the migration from the local server.
