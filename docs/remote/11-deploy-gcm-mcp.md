# 11 — Deploying `gcm-mcp`

Issue #60, part of #29. Step-by-step instructions to deploy the MCP service of the remote connector, check that it works, update it to a new `stable` and roll it back. The settings web app has its own document (#61).

**Read first:** [08](08-operator-setup.md) (project, consent screen, OAuth clients, secrets, Firestore: this document assumes it is done), [09](09-network-exposure.md) (why `gcm-mcp` is public), [07](07-mcp-server-design.md) (design) and [06](06-dev-cycle.md) (build and release).

## How to read this document

Same tags as in 08. **[MANUAL]** is done by hand. **[TERRAFORM]** is created by the module in `infra/` ([#96](https://github.com/nuccio/google-chat-mcp/issues/96)); the command shown is the manual equivalent for an operator who does not use it. **(unverified)** marks a fact that could not be checked in the repository or in official documentation; **(not implemented)** marks something that does not exist in the code yet.

> **Status on 2026-11-07.** The service code is complete in `main` (#78, #79, #80), the image is built by the `Dockerfile` (#95) and the Terraform module is in `infra/` (#96, PR #103, merged). The follow-ups of #79 in [#108](https://github.com/nuccio/google-chat-mcp/issues/108) are **not done**: see [What is not done yet](#what-is-not-done-yet). Remaining mismatches around the module are listed in [Differences to settle before the first deploy](#differences-to-settle-before-the-first-deploy).

## What gets deployed

| | |
|---|---|
| Service | `gcm-mcp`, Cloud Run, from the **same image** as `gcm-settings`, started with `args: google_chat_mcp.remote` (the `Dockerfile` default is `gcm-settings`) |
| Listens on | `PORT` (set by Cloud Run, default 8080), Streamable HTTP at `/mcp`, stateless |
| Service account | `gcm-mcp@PROJECT_ID.iam.gserviceaccount.com` |
| Exposure | `ingress=all`, Invoker IAM check disabled, `max-instances=2`, request timeout 300 s ([09](09-network-exposure.md), decision N1) |
| Scaling and size | Scale to zero (`min-instances=0`), 1 vCPU, 512 MiB, request-based billing (values of the Terraform module, 04 "Runtime and cost") |

Everything is configured from environment variables; the code does **not** fetch from Secret Manager. Cloud Run puts the secret values into environment variables (`remote/__main__.py`). A missing or invalid value stops the process at start-up with a message that names the variable and never its value.

## Step 0 — Prerequisites

1. The shared setup of [08](08-operator-setup.md) is done for this environment: project, billing, region, APIs, consent screen, Firestore database, and the Secret Manager containers.
2. Tools on the operator's machine: `gcloud` (logged in: `gcloud auth login`), Docker (or another builder that makes a Linux image for the registry's architecture), `git`; Terraform >= 1.6 when using `infra/` (`infra/README.md`). The Cloud Run image architecture requirement is **(unverified here)**: build for `linux/amd64`, the architecture Cloud Run documents for containers, if your machine is ARM (`docker build --platform linux/amd64 …`).
3. Permissions of the operator. The exact roles are **(unverified)**; what the steps need, from the services they touch: create service accounts and IAM bindings on the project and on secrets, push to Artifact Registry, create/update Cloud Run services and act as `gcm-mcp@` when deploying. For a personal dev project, `Owner` is enough. In production, ask the organisation which predefined roles it allows and check them against the page "Cloud Run deployment permissions" before the first deploy.
4. The values from Step 7 of 08, written down privately: project ID, region, database ID, client ID of the connector, `ALLOWED_DOMAINS`, `DEPLOY_MODE` (dev only), `POLICY_*`.

## Step 1 — Build and push the image from `stable`

The image comes from the commit marked `stable` (NFR-6; **dev:** from `main` with green tests, 06).

**[TERRAFORM]** creates the Artifact Registry repository `gcm` (Docker format, in the environment's region) with a clean-up policy: keep the 5 most recent versions and delete the ones older than 30 days (`infra/modules/gcm-remote`). By hand:

```bash
gcloud artifacts repositories create gcm --repository-format=docker \
  --location=REGION --project PROJECT_ID
```

Then, from a clean checkout of the repository (the commands of 06, "Build and push"):

```bash
gcloud auth configure-docker REGION-docker.pkg.dev
```

```bash
git fetch --tags && git checkout --detach stable
```

```bash
IMAGE=REGION-docker.pkg.dev/PROJECT_ID/gcm/gcm-remote:$(git rev-parse HEAD)
docker build -t "$IMAGE" . && docker push "$IMAGE"
```

The tag is the **full commit SHA, never `latest`**: every revision points to an exact commit and a rollback needs no rebuild. Write the SHA down; Step 4 and the update procedure use it.

- **Clean-up.** The policy deletes versions after 30 days unless they are among the 5 most recent (a keep policy wins over a delete policy). **Never let the image of a revision that can still receive traffic be deleted** (06): after a rollback, the older revision's image must still exist. With the default policy, a rollback target older than 30 days *and* more than 5 versions back is gone; rebuild from its SHA if needed. Whether a Cloud Run revision pins the image so that Artifact Registry clean-up cannot remove it is **(unverified)**: do not rely on it.
- The CI job `docker.yml` only builds and smoke-tests the image; it pushes nothing. The deploy pipeline of 06 (Workload Identity Federation, manual approval for production) is **(not implemented)**: until it exists, the operator pushes by hand as above.

## Step 2 — Service account `gcm-mcp@`

**[TERRAFORM]** creates `gcm-mcp@` and exactly these bindings (`infra/modules/gcm-remote`):

| Resource | Role | Condition |
|---|---|---|
| Project (Firestore) | `roles/datastore.user` | `resource.name == "projects/PROJECT_ID/databases/DATABASE_ID"` |
| Secret: connector client secret | `roles/secretmanager.secretAccessor` | on that secret only |
| Secret: Fernet keys (token-store encryption) | `roles/secretmanager.secretAccessor` | on that secret only |
| Secret: `jwt_signing_key` | `roles/secretmanager.secretAccessor` | on that secret only |

No other role. IAM cannot tell Firestore collections apart; the code enforces what each service may touch (04, "Access to Firestore by service"). `gcm-mcp@` has **no** access to the settings client secret, and `gcm-settings@` has none to these three.

By hand:

```bash
gcloud iam service-accounts create gcm-mcp --display-name="gcm-mcp (MCP connector)" --project PROJECT_ID
```

```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:gcm-mcp@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/datastore.user" \
  --condition='title=only-gcm-database,expression=resource.name == "projects/PROJECT_ID/databases/DATABASE_ID"'
```

```bash
gcloud secrets add-iam-policy-binding SECRET_NAME --project PROJECT_ID \
  --member="serviceAccount:gcm-mcp@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Repeat the last command for the three secrets. Whether the condition expression above works for `roles/datastore.user` on a database is what the Terraform module uses; that Google evaluates it on Firestore requests is **(unverified until the first deploy)**: the check below proves the service can read, but not that a *different* database would be refused. Test that on the dev project if the second database exists (it normally does not: only one database is free).

**Check.** `gcloud projects get-iam-policy PROJECT_ID` shows the binding with its condition; `gcloud secrets get-iam-policy SECRET_NAME` shows `gcm-mcp@` on its three secrets and not on the settings secret.

## Step 3 — OAuth client of the connector and secrets

The client itself is **[MANUAL]**, always (08, Step 4). For `gcm-mcp`:

| | |
|---|---|
| Application type | Web application, name `gcm-mcp-dev` / `gcm-mcp-prod` |
| Authorised redirect URI | `{BASE_URL}/auth/callback` where `BASE_URL` is the service address, **no path, https** (`oauth.py` refuses anything else; `http://localhost` is accepted only in development mode) |
| Authorised JavaScript origins | none |
| Scopes (consent screen, Data Access) | `openid`, `https://www.googleapis.com/auth/userinfo.email`, and the four Chat scopes: `chat.spaces.readonly`, `chat.messages.readonly`, `chat.messages.create`, `chat.memberships.readonly` (`REQUIRED_SCOPES` in `remote/oauth.py`; a test keeps the Chat scopes equal to the local mode's) |

The address of a Cloud Run service is deterministic: `https://gcm-mcp-PROJECT_NUMBER.REGION.run.app` (the Terraform module builds it this way and outputs `oauth_redirect_uris`/`mcp_redirect_uri`). The project number comes from `gcloud projects describe PROJECT_ID --format='value(projectNumber)'`. That the deterministic address is the one Cloud Run assigns is **(unverified until the first deploy)**: after the deploy, compare it with `gcloud run services describe gcm-mcp --region REGION --format='value(status.url)'` and fix the client if they differ.

**Redirect URIs of Claude.** These are not registered in Google's client. They are fixed in the code: `allowed_client_redirect_uris` accepts only `https://claude.ai/api/mcp/auth_callback` (the one Anthropic's documentation names, read 2026-11-07) and `https://claude.com/api/mcp/auth_callback`. Claude Code (loopback redirect) is not supported as a client. Nothing to configure.

**Secrets.** Four containers exist for the environment (08, Step 5); `gcm-mcp` uses three. The code reads these variables (`remote/oauth.py`), whatever names the Secret Manager containers have:

| Environment variable | Content | Format |
|---|---|---|
| `GOOGLE_CLIENT_ID` | Client ID of the connector (not a secret) | plain value |
| `GOOGLE_CLIENT_SECRET` | Client secret of the connector | Secret Manager reference |
| `OAUTH_JWT_SIGNING_KEY` | Signs the tokens `gcm-mcp` issues to Claude | at least 32 characters (`token_urlsafe(48)` as in 08 gives 64) |
| `OAUTH_FERNET_KEYS` | Encrypts the token store | one or more Fernet keys, **comma-separated, newest first**; a Fernet key is 32 random bytes in URL-safe base64 (44 characters) |

Add the values as in 08, Step 5 (read from standard input; never on a command line, in a file in the repository or in a chat). Add **one enabled version** of each; the services read `latest`.

## Step 4 — The Cloud Run service

**[TERRAFORM]** (`infra/envs/dev` or `infra/envs/prod`, `infra/README.md`) creates the service in a defined order: first apply with `deploy_services = false` (APIs, registry, database, accounts, empty secrets), then the secret values (Step 3) and the image (Step 1), then `deploy_services = true` and `image = …`, `terraform apply`. The image is ignored after creation (`ignore_changes`): later updates are done with `gcloud` (below), not by Terraform.

By hand, one command with everything. The environment variable names are those of the code:

```bash
gcloud run deploy gcm-mcp \
  --project PROJECT_ID --region REGION \
  --image "$IMAGE" \
  --args=google_chat_mcp.remote \
  --service-account gcm-mcp@PROJECT_ID.iam.gserviceaccount.com \
  --ingress=all --no-invoker-iam-check \
  --min-instances=0 --max-instances=2 \
  --cpu=1 --memory=512Mi --timeout=300 --no-session-affinity \
  --set-env-vars="BASE_URL=https://gcm-mcp-PROJECT_NUMBER.REGION.run.app,SETTINGS_BASE_URL=https://gcm-settings-PROJECT_NUMBER.REGION.run.app,GOOGLE_CLIENT_ID=CLIENT_ID,GOOGLE_CLOUD_PROJECT=PROJECT_ID,FIRESTORE_DATABASE=(default),ALLOWED_DOMAINS=example.com" \
  --set-secrets="GOOGLE_CLIENT_SECRET=CONNECTOR_SECRET_NAME:latest,OAUTH_JWT_SIGNING_KEY=JWT_SECRET_NAME:latest,OAUTH_FERNET_KEYS=FERNET_SECRET_NAME:latest"
```

`--no-invoker-iam-check` and `--ingress=all` are the flags 09 cites from Cloud Run's documentation (read 2026-11-06). The other flags are standard `gcloud run deploy` flags that were **not re-read in the reference for this document (unverified)**: when a flag is refused, `gcloud run deploy --help` wins. Values containing commas or `(default)` may need the escaping syntax of `--set-env-vars` **(unverified)**; use `--env-vars-file` if the shell or `gcloud` complains.

| Variable | Value | Notes |
|---|---|---|
| `BASE_URL` | The service's own address, no path | Used as the OAuth issuer and for the redirect URI; Host/Origin protection allows only this host (09) |
| `SETTINGS_BASE_URL` | Address of `gcm-settings` | The "no space configured" answer links to `{SETTINGS_BASE_URL}/settings` |
| `GOOGLE_CLOUD_PROJECT`, `FIRESTORE_DATABASE` | Project and database ID | `(default)` unless you named the database |
| `ALLOWED_DOMAINS` | Workspace domains, comma-separated | Production: must not be empty |
| `DEPLOY_MODE` | **Dev only:** `development`. **Never set in production** | A missing or unknown value means production (08) |
| `POLICY_ALLOW_WRITE`, `POLICY_ALLOW_UNATTENDED`, `POLICY_SPACE_ALLOWLIST` | Optional admin policy | Must be the same on both services; see `remote/policy.py` and 04 "Admin policy" |
| `PORT` | Set by Cloud Run | Do not set |

Region: the one chosen in 08, Step 1 (Tier 1, with Firestore). The connector URL to give to users is `BASE_URL` + `/mcp`; a custom domain is **(not covered)**: it would change `BASE_URL`, the OAuth redirect URI and the host check together, and has not been designed.

**Public access.** With `--no-invoker-iam-check` the service accepts unauthenticated HTTPS requests; all access control is in the application (09, N1). **Production caveat:** if the organisation enforces the policy `constraints/run.managed.requireInvokerIam`, the flag is refused (09, from Cloud Run's documentation, read 2026-11-06). Then `gcm-mcp` **cannot be public** and nothing in this document works for production until the maintainer obtains an exception for this one service (09, "If the organisation forbids it"). Granting `allUsers` the Invoker role is the alternative and fails under domain-restricted sharing. Ask the organisation's administrator **before** the production deployment. Check the policy with `gcloud org-policies describe constraints/run.managed.requireInvokerIam --project PROJECT_ID` **(command unverified)**.

**Check.** `gcloud run services describe gcm-mcp --region REGION --project PROJECT_ID` shows the image tag (the SHA), the service account, the environment variables and, for the secrets, the references (never the values).

## Step 5 — Firestore TTL policies for the token store

`OAuthProxy` keeps its state in Firestore collections whose names start with `mcp-`. Each entry has an `expires_at` timestamp (`ExpiringFirestoreStore` in `remote/oauth.py`), so that a Firestore TTL policy can delete expired entries. Create **one policy per collection group**:

`mcp-oauth-proxy-clients`, `mcp-oauth-transactions`, `mcp-consent-csrf-tokens`, `mcp-authorization-codes`, `mcp-upstream-tokens`, `mcp-jti-mappings`, `mcp-refresh-tokens`.

**[TERRAFORM]** creates all seven (`ttl_token_store` in `infra/modules/gcm-remote/main.tf`). By hand (as 08, Step 6), for an operator who does not use it:

```bash
for c in mcp-oauth-proxy-clients mcp-oauth-transactions mcp-consent-csrf-tokens \
         mcp-authorization-codes mcp-upstream-tokens mcp-jti-mappings mcp-refresh-tokens; do
  gcloud firestore fields ttls update expires_at --collection-group="$c" \
    --enable-ttl --async --project PROJECT_ID
done
```

With a database that is not `(default)` add `--database=DATABASE_ID` **(unverified flag)**. The module and this list come from `remote/oauth.py` and 07. **Compare them with the collections that actually appear in the console after a first sign-in** (some appear only when first written); if one with an `expires_at` is missing, add it to `token_store_collections` in the module.

- A TTL deletes **late** (asynchronously), and its deletions are billed (08, Step 1). The code ignores expired entries on read; a late deletion is not a security gap.
- That the policy really deletes these entries, and that Claude registers again by itself when a client registration has expired after 90 days, are `[GCP]` checks still open in 07 and #108.

**Check.** `gcloud firestore fields ttls list --project PROJECT_ID` lists the seven policies in state `ACTIVE` (or `CREATING` for a while).

## Step 6 — First checks after the deploy

1. **Redirect URI.** Add `{BASE_URL}/auth/callback` to the connector's OAuth client (Step 3) if it was not there, and save. **[MANUAL]**
2. **Start-up.** `gcloud run services logs read gcm-mcp --region REGION --project PROJECT_ID --limit 50` (or Logs Explorer). Expected: JSON lines, no error. A configuration error names the variable and stops the revision (the deploy then fails and the previous revision keeps the traffic). Logs have addresses masked and contain no message text; there is no access log (`remote/logging.py`).
3. **Anonymous call.** An unauthenticated request to `/mcp` must be refused with `401` and a `WWW-Authenticate: Bearer resource_metadata=…` header, before any tool or Firestore read (09):

   ```bash
   curl -si -X POST "$BASE_URL/mcp" -H 'content-type: application/json' \
     -H 'accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | head -15
   ```

   Expected: `HTTP/2 401` and a `www-authenticate` line. Anything else (a `200`, or a `403` from Google's front end with a Google error page) is a failure: `403` before the application usually means the Invoker IAM check is still on (Step 4).
4. **Metadata.** `curl -s "$BASE_URL/.well-known/oauth-authorization-server"` returns JSON whose endpoints start with `BASE_URL`. A request with another `Host` is answered `421`, a foreign `Origin` `403` (09, tested in `tests/test_remote_oauth.py`).
5. **Add the connector in Claude with a test account.** In Claude: Settings → Connectors → Add custom connector, URL `BASE_URL/mcp` (the menu wording is **(unverified)**: Anthropic's documentation of custom connectors, cited in 09, wins). Sign in with a **test account**: development → a test user of the consent screen; production → an account of the organisation (and, to prove the Internal screen, one outside it must be refused: 08, Step 3). Expected:
   - Claude shows the connector's consent page, then Google's sign-in, then returns to Claude;
   - with an **empty ACL**, a tool call returns the message with the link to `{SETTINGS_BASE_URL}/settings`;
   - after enabling a space in `/settings` (the settings service must be running: #61), a **read** of a message list in that space works, and a space that is not enabled is refused.
6. **Logs of the test.** Look at the entries again: no message text or email address in them.
7. **`python -m google_chat_mcp.remote check` (#71): (not implemented).** It does not exist in the code at the time of writing; the checks above replace it. When #71 lands, run it after every deploy and read its output (configuration, Firestore access, the secrets, the client secret and redirect URI) and update this step.

If the sign-in fails with `redirect_uri_mismatch`, the client's redirect URI differs from `BASE_URL/auth/callback` by one character. With `org_internal`, the account is outside the organisation (expected for production tests of the refusal).

## Update to a new `stable`

1. Be sure `stable` was tried in a real client (CONTRIBUTING, "Release and branch policy"). In production only the commit marked `stable` is deployed (04, "Releases").
2. Read the release notes of the changes since the running SHA for: a new environment variable or secret, a new scope, a new Firestore collection (needs its TTL policy: Step 5), or a change of the ACL schema.
3. **If the ACL schema changes:** readers first, writers second (06). `gcm-mcp` (reader) goes first with a `stable` that reads both the old and new schema, then `gcm-settings` (writer) in its own `stable`. Never deploy the writer first. The schema version rules are in 04, "Schema versioning".
4. Build and push the new SHA (Step 1).
5. Deploy a new revision. Keep the previous one as the rollback target; first note its name: `gcloud run revisions list --service gcm-mcp --region REGION --project PROJECT_ID`.

   ```bash
   gcloud run deploy gcm-mcp --project PROJECT_ID --region REGION --image "$NEW_IMAGE"
   ```

   Only the image changes; the rest of the configuration of the previous revision is kept by Cloud Run (standard behaviour, **unverified here**). New variables or secrets go in the same command (`--update-env-vars`, `--update-secrets`).
6. Repeat Step 6, checks 2 to 5.

**Gradual rollout** (send a share of the traffic to the new revision first) is possible with Cloud Run's traffic splitting **(not part of this procedure and not tried)**; with `max-instances=2` and a handful of users, the check in Step 6 is the gate.

## Roll back

No rebuild is needed: the image of the previous revision is still in the registry (Step 1, clean-up).

```bash
gcloud run revisions list --service gcm-mcp --region REGION --project PROJECT_ID
```

```bash
gcloud run services update-traffic gcm-mcp --region REGION --project PROJECT_ID \
  --to-revisions=PREVIOUS_REVISION=100
```

After the command, `gcm-mcp` serves the old revision until you route again to `--to-latest`. Repeat Step 6, checks 3 to 5. Cases:

- **The change was a schema change.** Rolling `gcm-mcp` back is safe only while the data written by the new version is readable by the old one: that is the reason for "readers first". If `gcm-settings` already writes the new schema, roll it back first, or decide before the rollout that it cannot be rolled back and test accordingly.
- **A secret or variable changed with the deploy.** The old revision uses the values it was created with (variables) but `latest` of a secret (Step 3): if a secret changed, the old revision gets the new value too. Pin secret versions in the deploy if you need a rollback to restore them **(a consideration, not tried)**.
- **The image was deleted by the clean-up.** Rebuild from the old SHA (`git checkout --detach SHA`, Step 1) with the same tag and deploy it.

## Rotating secrets, and what users notice

| Secret | How | What users notice |
|---|---|---|
| Fernet keys (`OAUTH_FERNET_KEYS`) | Add the new key **in front** of the list (`NEW,OLD`), add it as a new secret version, deploy a new revision; later remove the old key the same way, after the longest lifetime of an entry (07, "Rotation") | Nothing while both are in the list. If a key is removed too early, entries it encrypted cannot be read and read as **missing**: those users sign in again (`raise_on_decryption_error=False`, `remote/oauth.py`). Not a data loss |
| `jwt_signing_key` | New secret version, new revision | **Expected:** tokens signed with the old key no longer validate and every user signs in again from Claude **(inferred from how FastMCP uses the key; not tried: confirm on the dev project)**. Destroy the old version afterwards: 08 counts active versions against the free tier |
| Connector client secret | Create the new secret in the OAuth client in Google's console, add it as a new version, deploy a new revision, then delete the old secret in the console. Whether the console allows two secrets at once is **(unverified)** | Sign-ins that are in progress during the switch fail once and are repeated |

Version `latest` is read at the start of an instance, so a new revision (or a new instance after scale-to-zero) is needed for a new value to be used. After a rotation **destroy** the old versions (08, Step 1).

## Development and production

| | Development | Production |
|---|---|---|
| Image from | `main` with green tests | the commit marked `stable` |
| `DEPLOY_MODE` | `development` (set only by `infra/envs/dev`) | never set |
| Consent screen | External, Testing; weekly re-sign-in of the test users | Internal; the 7-day limit is not expected **(unverified: first production deployment)** |
| `requireInvokerIam` | A personal project has no organisation policy | **Check with the organisation before deploying** (Step 4) |
| Terraform root | `infra/envs/dev` (database delete protection off) | `infra/envs/prod` |
| Secrets, OAuth client, database | Own | Own; nothing is copied between environments |
| Deploy | Automatic from `main` (pipeline **not implemented**; by hand until then) | Manual, approved (06) |

## Differences to settle before the first deploy

Checked against the merged `infra/` on 2026-11-07. The variable names of `gcm-mcp` in the module (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `OAUTH_FERNET_KEYS`, `OAUTH_JWT_SIGNING_KEY`) and the seven TTL policies agree with the code. What remains:

1. `infra/README.md` and a module comment still refer to `08-network-exposure.md`, which is now `09-network-exposure.md` (a documentation fix in `infra/`, not done here).
2. The settings service gets `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in the module, while `gcm-mcp` gets `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. That is each service's own naming and is correct if the settings code reads those names; check [10](10-deploy-settings.md) and the settings entry point, and do not assume the two services share names.

## What is not done yet

Do not rely on any of these. They are open in [#108](https://github.com/nuccio/google-chat-mcp/issues/108) and in the "to verify" lists of [07](07-mcp-server-design.md#to-verify-before-implementation) and [09](09-network-exposure.md#to-verify):

- Firestore reads per tool call (the budget of NFR-2 is unchecked);
- stateless-mode handling of `InputRequiredResult`, and refresh of the upstream Google token;
- the `[GCP]` checks on the dev project (#86, run during #97), including that the TTL policies delete the token-store entries and that strict `Origin` checking does not block Anthropic's requests;
- the `check` command (#71) and the deploy pipeline of 06.

## Sources

- Repository files, read 2026-11-07: `google_chat_mcp/remote/__main__.py`, `remote/oauth.py`, `Dockerfile`, `docs/remote/04`, `06`, `07`, `08`, `09`, `infra/` (merged in #103).
- Cloud Run, public access and ingress: <https://docs.cloud.google.com/run/docs/authenticating/public>, as cited in 09 (read 2026-11-06).
- Firestore TTL: <https://cloud.google.com/firestore/native/docs/ttl>, as cited in 08 (read 2026-11-06).
- Everything else about `gcloud` flags is **unverified** as marked.
