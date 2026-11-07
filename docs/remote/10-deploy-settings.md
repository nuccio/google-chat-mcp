# 10 — Deploying `gcm-settings`

Step-by-step procedure to deploy the settings web app (`/settings`, NiceGUI), check that it works, update it to a new `stable` and roll it back. It is the document for [#61](https://github.com/nuccio/google-chat-mcp/issues/61), part of #29. The shared setup is in [08](08-operator-setup.md) (do it first); the MCP service has its own document (#60, not written yet). The design is in [04](04-settings-server-design.md), the cycle in [06](06-dev-cycle.md), the exposure in [09](09-network-exposure.md).

## How to read this document

| Tag | Meaning |
|---|---|
| **[MANUAL]** | You do it by hand (console or `gcloud`). |
| **[TERRAFORM #96]** | Created by the Terraform module in `infra/` ([#96](https://github.com/nuccio/google-chat-mcp/issues/96), pull request #103). **At the time of writing (2026-11-07) that pull request is not merged**, so `infra/` is not in `main`; the Terraform facts below were read in the branch `issue-96-terraform` and are only as stable as that branch. Each Terraform step also gives the `gcloud` equivalent, for use until it is merged or if you do not use Terraform. |
| **[CHECK]** | A check to run. `python -m google_chat_mcp.settings check` ([#71](https://github.com/nuccio/google-chat-mcp/issues/71)) exists: how to run it as a Cloud Run job is in [08, Step 8](08-operator-setup.md#step-8--check-the-setup). Every [CHECK] keeps its manual line, because the command's probes at Google's endpoints have not been tried on a real project yet. |

**Sources.** Environment variable names and defaults come from the code in `main` (`google_chat_mcp/settings/config.py`, `production.py`, `remote/policy.py`, `__main__.py`), read on **2026-11-07**. Cloud Run, Terraform and Secret Manager facts come from the repository documents above (which cite the official pages, read **2026-11-06**) and from `infra/` in PR #103. Anything I could not confirm is marked **(unverified)**. Google changes flags and labels: when `gcloud run … --help` or the official page differs from this document, the tool wins, and this document should be corrected.

## What you deploy

One container image, built from the commit marked `stable`, started with the settings command. The same image runs `gcm-mcp` with a different argument (`Dockerfile`, 06 "Container image").

| Item | Value | Source |
|---|---|---|
| Cloud Run service | `gcm-settings` | `infra/` (#103) |
| Start command | entrypoint `python -m`, argument `google_chat_mcp.settings` (also the image default `CMD`) | `Dockerfile` |
| Service account | `gcm-settings@PROJECT_ID.iam.gserviceaccount.com` | `infra/` |
| Port | Cloud Run sets `PORT`; the service reads it (default 8080) | `settings/config.py` |
| Public path | `/settings` (and `/` redirects to it) | `settings/production.py` |

## Dev and production at a glance

| | Development | Production |
|---|---|---|
| Project | Personal GCP project, no organisation | Company project inside the Workspace organisation |
| Consent screen | External, Testing; sign-in limited to **test users**; weekly re-sign-in | Internal |
| `DEPLOY_MODE` | `development` (from `infra/envs/dev`): accounts without `hd` are accepted, and the pages show a "Development deployment" banner | **Not set** (or `production`). A missing or unknown value is production |
| `ALLOWED_DOMAINS` | Required by the code even in dev (any valid domain; it only matters for accounts that have `hd`) | The organisation's domain(s) |
| What is deployed | `main` with green tests (06: automatic, once the pipeline exists) | **Only the commit marked `stable`**, by a manual, approved workflow (06, 04 "Releases") |
| Firestore delete protection | Off (`terraform destroy` allowed) | On |
| Done by | The maintainer | The maintainer, in the company organisation |

Until the deploy pipeline of #29 exists, both environments are deployed by hand with the commands below; the rule "prod only from `stable`" applies to your hands too.

## Step 1 — Prerequisites

1. **Shared setup done** ([08](08-operator-setup.md)): project and billing, APIs, consent screen, Firestore database with its TTL policies, secret containers. Step 5 below tells you which of them you need and when.
2. **Tools on your machine:** `gcloud` (logged in: `gcloud auth login`), Docker (or another builder that makes a `linux/amd64` image: **(unverified)** Cloud Run runs `linux/amd64`; on an Apple-silicon Mac build with `docker buildx build --platform linux/amd64`), `git`, and, for the Terraform path, Terraform ≥ 1.6 (`infra/README.md`) with `gcloud auth application-default login`.
3. **Permissions of the operator.** I did not find a list in the repository; the following are the roles I expect the commands to need **(unverified; check against the "required roles" of each product's page)**: create service accounts and bind IAM on the project (for example `roles/owner`, or the narrower roles of Cloud Run Admin, Service Account Admin, Project IAM Admin, Artifact Registry Admin, Secret Manager Admin, Firestore/Datastore Owner), and `roles/iam.serviceAccountUser` on `gcm-settings@` to deploy a service that runs as it. In the personal dev project the owner has all of it. In production, ask the company's IT which role you get.
4. **Variables** used below (replace them in the commands; write them down privately, not in the repository):

   ```bash
   export PROJECT_ID=...        # the environment's project
   export REGION=...            # the region chosen in 08, Step 1
   export REPO="$REGION-docker.pkg.dev/$PROJECT_ID/gcm"   # Artifact Registry repository
   ```

   The repository is called `gcm` and the image `gcm-remote` (`infra/`, `outputs.tf`; 06).

## Step 2 — Get the commit marked `stable`

Production deploys the commit marked `stable`; dev may deploy `main` after green tests. `stable` is the same commit local users get with `uvx …@stable` (04 "Releases"). I did not verify how it is marked (a git tag or a branch); the commands below work for either:

```bash
git fetch --tags origin
git checkout --detach stable          # dev: git checkout --detach origin/main
git rev-parse HEAD                    # the SHA: this is the image tag
git log -1 --format='%H %G? %s'       # %G? = G means a good signature
```

Write the SHA down. It is the version you deploy and the version you roll back to.

## Step 3 — Build and push the image

The tag is the commit SHA, **never `latest`**, so every Cloud Run revision points to an exact commit and a rollback needs no rebuild (06). The Terraform module refuses an image ending in `:latest`.

```bash
gcloud auth configure-docker "$REGION-docker.pkg.dev"
IMAGE="$REPO/gcm-remote:$(git rev-parse HEAD)"
docker build --platform linux/amd64 -t "$IMAGE" .
docker push "$IMAGE"
```

The repository must exist first. **[TERRAFORM #96]** creates it (`gcm`, Docker format, with a clean-up policy that keeps the 5 most recent versions and deletes versions older than 30 days). By hand:

```bash
gcloud artifacts repositories create gcm --repository-format=docker \
  --location="$REGION" --project "$PROJECT_ID"
```

The clean-up policy is not set by that command. Without Terraform, set it by hand or watch the 0.5 GB free storage (08, Step 1). **Never delete the image of a revision that can still receive traffic**, including the previous one you may roll back to.

**Check.** `gcloud artifacts docker images list "$REPO" --project "$PROJECT_ID"` lists the image with your SHA. The CI smoke test (`scripts/docker-smoke.sh`) has already checked that the image runs as non-root, holds no credential files and starts both commands.

## Step 4 — Service account `gcm-settings@`

**[TERRAFORM #96]** creates the account and these bindings; nothing else:

| Binding | On | Why |
|---|---|---|
| `roles/datastore.user`, with the condition `resource.name == "projects/PROJECT_ID/databases/DATABASE"` | the project | Read and write Firestore (ACL, sessions, sign-in transactions, audit). IAM cannot separate collections; the code does (04 "Access to Firestore by service") |
| `roles/secretmanager.secretAccessor` | the secret `gcm-settings-client-secret` only | Its own client secret |

It has **no binding** on `gcm-mcp-client-secret`, `gcm-mcp-fernet-key` or `gcm-mcp-jwt-signing-key`. That is what keeps a flaw in the UI from reaching the key that decrypts the users' Chat tokens (04, 09).

By hand, if you do not use Terraform:

```bash
gcloud iam service-accounts create gcm-settings \
  --display-name="gcm-settings (settings UI)" --project "$PROJECT_ID"
SA="gcm-settings@$PROJECT_ID.iam.gserviceaccount.com"

# Condition: the database name is "(default)" unless you named it otherwise
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA" --role="roles/datastore.user" \
  --condition="title=only-default-database,expression=resource.name == \"projects/$PROJECT_ID/databases/(default)\""

gcloud secrets add-iam-policy-binding gcm-settings-client-secret \
  --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor" \
  --project "$PROJECT_ID"
```

The exact `--condition` syntax is `gcloud`'s; run it with `--help` if it complains **(unverified here)**.

**Check [CHECK].** The account must not read the connector's secrets:

```bash
for s in gcm-mcp-client-secret gcm-mcp-fernet-key gcm-mcp-jwt-signing-key; do
  gcloud secrets get-iam-policy "$s" --project "$PROJECT_ID" | grep -c "gcm-settings@" || true
done
```

Each line must print `0`. `gcloud projects get-iam-policy "$PROJECT_ID"` must show no other role for `gcm-settings@` than the conditional `roles/datastore.user` (and none of `roles/secretmanager.secretAccessor` at project level, which would give it every secret). The `check` command of #71 tests the same from the service's own identity: its `secret.gcm-mcp-*` lines must all say `denied to this service account, as intended`.

## Step 5 — Client secret and OAuth client

The OAuth client of the settings app is a **[MANUAL]** step, always (08, Step 4): Terraform does not manage web OAuth clients.

| Field | Value |
|---|---|
| Application type | Web application |
| Name | `gcm-settings-dev` / `gcm-settings-prod` |
| Authorised redirect URI | `{SETTINGS_BASE_URL}/settings/callback` |
| Authorised JavaScript origins | none |
| Scopes the app asks | `openid email https://www.googleapis.com/auth/chat.spaces.readonly`, with `access_type=online` (no refresh token) (`settings/google_signin.py`, 04 "OAuth client") |

1. **Create the client** in Google Auth Platform → Clients (08, Step 4). If the console will not save without a redirect URI, see "SETTINGS_BASE_URL" below to learn the address first.
2. **Store its secret.** Put the secret into the container `gcm-settings-client-secret`; type it at the prompt (end with Ctrl-D) or read it from a file you delete afterwards, never on the command line:

   ```bash
   gcloud secrets versions add gcm-settings-client-secret --data-file=- --project "$PROJECT_ID"
   ```

   The container is created by **[TERRAFORM #96]**; by hand: `gcloud secrets create gcm-settings-client-secret --replication-policy=automatic --project "$PROJECT_ID"` and bind the reader as in Step 4. A Cloud Run revision cannot start while a referenced secret has no version, so add the version **before** the first deploy.
3. **Write down the client ID** (it is not a secret). It goes in `GOOGLE_OAUTH_CLIENT_ID`.

## Step 6 — `SETTINGS_BASE_URL`

`SETTINGS_BASE_URL` is the public address of the service, only scheme, host and optional port: no path, query or credentials (a trailing slash is removed); plain `http` only for `localhost`; the service refuses to start otherwise (`config.py`). It is used for three things: the OAuth redirect URI (`{SETTINGS_BASE_URL}/settings/callback`), the websocket **Origin check** (a handshake whose `Origin` is not this exact origin is refused), and the `Secure` / HSTS behaviour. **The address in the browser's bar must be exactly this one**, or the pages load but the websocket is refused.

**Where it comes from.** `infra/` computes it before the service exists, from Cloud Run's deterministic URL form `https://gcm-settings-PROJECTNUMBER.REGION.run.app` (`infra/modules/gcm-remote/main.tf`; output `settings_url`). Verify it against the real service after the first deploy:

```bash
gcloud run services describe gcm-settings --region "$REGION" --project "$PROJECT_ID" \
  --format='value(status.url)'
```

`status.url` is the address Cloud Run reports for the service. If it differs from the one you set, **(unverified which one Cloud Run keeps serving, as Cloud Run may also answer on an older `…-HASH-xx.a.run.app` form)** set `SETTINGS_BASE_URL` to the one you will actually give to users and register that one as the redirect URI. Whichever you choose, use the same value in all three places: the variable, the OAuth client and the link you hand to users.

**Order.**

1. Decide the address (Terraform output `settings_url`, or the first deploy's `status.url`).
2. Register `{SETTINGS_BASE_URL}/settings/callback` on the OAuth client of Step 5 (Google Auth Platform → Clients → the client → Authorised redirect URIs).
3. Set `SETTINGS_BASE_URL` on `gcm-settings`.
4. Set the **same** value as `SETTINGS_BASE_URL` on `gcm-mcp` (#60). The connector uses it to point users to the settings page. A change of this address means redeploying both services.

**Custom domain (optional).** Cloud Run supports domain mappings, without a load balancer **(unverified here; read the current Cloud Run "Mapping custom domains" page, its limits and its region support, before relying on it)**. If you map one, the custom address replaces the `run.app` one in all three places above, and the old `run.app` address still answers unless you disable it, which would make the Origin check refuse pages opened there. Do not use a custom domain in dev unless you need it.

## Step 7 — Create or update the Cloud Run service

### With Terraform **[TERRAFORM #96]**

Follow `infra/README.md`. Summary of its order (it matters, because a revision cannot start without an image or without a secret version):

```bash
cd infra/envs/dev          # or prod
cp terraform.example.tfvars terraform.tfvars        # edit: project, region, client IDs, domains
terraform init -backend-config="bucket=STATE_BUCKET"
terraform apply                                     # deploy_services = false
```

1. Add the secret values and create the OAuth clients (this document, Step 5; 08, Steps 4–5).
2. Build and push the image (Step 3).
3. Set `deploy_services = true` and `image = "…/gcm-remote:SHA"`, then `terraform apply`.
4. Register the `oauth_redirect_uris` output on the OAuth clients (Step 6).

The module sets for `gcm-settings` everything in the next table. **After creation Terraform ignores the image** (`ignore_changes`): later deploys are done with `gcloud` (Step 10), not with `terraform apply`. `terraform apply` is always run by hand and reviewed.

### By hand **[MANUAL]**

```bash
gcloud run deploy gcm-settings \
  --image "$IMAGE" \
  --args=google_chat_mcp.settings \
  --service-account "gcm-settings@$PROJECT_ID.iam.gserviceaccount.com" \
  --region "$REGION" --project "$PROJECT_ID" \
  --allow-unauthenticated \
  --session-affinity \
  --timeout=900 \
  --min-instances=0 --max-instances=2 \
  --cpu=1 --memory=512Mi \
  --cpu-throttling \
  --set-env-vars "SETTINGS_BASE_URL=$SETTINGS_BASE_URL,GOOGLE_OAUTH_CLIENT_ID=$CLIENT_ID,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,FIRESTORE_DATABASE=(default),ALLOWED_DOMAINS=example.com" \
  --set-secrets "GOOGLE_OAUTH_CLIENT_SECRET=gcm-settings-client-secret:latest"
```

Flag names are from my knowledge of `gcloud run deploy`; confirm each with `gcloud run deploy --help` **(unverified)**. Points worth knowing:

- `--allow-unauthenticated` is the older way to make the service public. The module uses `invoker_iam_disabled` instead (below). If your organisation forbids `allUsers`, see the next table.
- If the value of an environment variable contains a comma (a list), `--set-env-vars` splits on it; use the `^@^` delimiter syntax of `gcloud topic escaping` for `POLICY_SPACE_ALLOWLIST` and `ALLOWED_DOMAINS` with several values **(unverified here)**, or set them in the console.
- `(default)` in a shell needs quotes, as above.

### Settings of the service

| Setting | Value | Why |
|---|---|---|
| Ingress | all (`INGRESS_TRAFFIC_ALL`) | Users' browsers reach the page directly. No load balancer (09) |
| Public access | Terraform: `invoker_iam_disabled` (default, `public_access = "disable_iam_check"`) or `roles/run.invoker` for `allUsers` (`all_users`) | Browsers do not send a Google ID token to Cloud Run. Access control is in the application (Google sign-in, `ALLOWED_DOMAINS`, the ACL). If the organisation enforces `constraints/run.managed.requireInvokerIam` or domain-restricted sharing, neither works: see "If the organisation forbids it" in [09](09-network-exposure.md) **(unverified for `gcm-settings`: 09 discusses it for `gcm-mcp`)** |
| Session affinity | **on** | A tab's websocket reconnects must reach the instance that holds its state (04 "Websocket, state and instances") |
| Request timeout | **900 s**, above the 10-minute tab idle timeout | The platform must not cut a socket the server would keep (04) |
| Min / max instances | 0 / **2** | Scale to zero, bounded cost (04 S1). Cold starts of a few seconds are accepted |
| CPU, memory | **1 vCPU**, 512 MiB | Below 1 vCPU Cloud Run allows one request per instance, so each websocket would hold a whole instance (04) |
| Billing | request-based (CPU only while a request is active) | Fits the free tier |
| Startup CPU boost | on | Faster cold start (module setting) |
| Service account | `gcm-settings@` (Step 4) | |
| Region | the region of 08, Step 1 | |

### Environment variables of `gcm-settings`

Exact names, from the code.

| Variable | Required | Meaning | Source |
|---|---|---|---|
| `SETTINGS_BASE_URL` | yes | Step 6 | `settings/config.py` |
| `GOOGLE_OAUTH_CLIENT_ID` | yes | Client ID of the settings client (Step 5) | `settings/config.py` |
| `GOOGLE_OAUTH_CLIENT_SECRET` | yes, **or** the file below | The client secret, from Secret Manager as a secret environment variable | `settings/config.py` |
| `GOOGLE_OAUTH_CLIENT_SECRET_FILE` | alternative | Path of a mounted secret file. **Set only one of the two**; both set stops the service | `settings/config.py` |
| `ALLOWED_DOMAINS` | yes | Comma-separated domains of the organisation (valid DNS names). Empty or malformed stops the service | `remote/policy.py` |
| `DEPLOY_MODE` | dev only | `development` in dev; **leave undefined in production** | `remote/policy.py` |
| `POLICY_ALLOW_WRITE` | no | `true` / `false`, default `true`. `false` disables write for everyone | `remote/policy.py` |
| `POLICY_ALLOW_UNATTENDED` | no | `true` / `false`, default `true` | `remote/policy.py` |
| `POLICY_SPACE_ALLOWLIST` | no | Comma-separated space IDs (without `spaces/`); empty means every space | `remote/policy.py` |
| `GOOGLE_CLOUD_PROJECT` | recommended | Project of the Firestore client (`GCP_PROJECT` is also read; the code falls back to the ambient project when both are missing, but set it explicitly) | `remote/firestore.py` |
| `FIRESTORE_DATABASE` | no | Database ID, `(default)` if unset. Terraform always sets it | `remote/firestore.py` |
| `PORT` | set by Cloud Run | Default 8080 | `settings/config.py` |
| `HOST` | no | Default `0.0.0.0` | `settings/config.py` |

An invalid value makes the process exit at start-up with `Configuration error: …` (messages never contain a secret). A revision that exits there is not ready, and Cloud Run keeps the traffic on the previous revision; the cause is in the logs (Step 9).

**Do not give `gcm-settings` any of** `BASE_URL`, `FERNET_KEY`, `JWT_SIGNING_KEY` or the connector's client secret. They belong to `gcm-mcp`.

## Step 8 — First checks

Run these after the **first** deploy and after **every** deploy (Step 10).

1. **The service is up and on the right revision.**

   ```bash
   gcloud run services describe gcm-settings --region "$REGION" --project "$PROJECT_ID" \
     --format='value(status.url,status.latestReadyRevisionName)'
   curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "$SETTINGS_BASE_URL/"
   ```

   The `curl` must print `303` and `…/settings` (`/` redirects there). The first request after a quiet period takes a few seconds (cold start).

2. **Security headers and CSP** on a page (04 "Security headers"):

   ```bash
   curl -sSI "$SETTINGS_BASE_URL/settings" | grep -i -E \
     'content-security-policy|x-frame-options|x-content-type-options|referrer-policy|strict-transport|cross-origin-opener|cache-control'
   ```

   Expected: a `Content-Security-Policy` with `default-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'` and `base-uri 'none'`; `X-Frame-Options: DENY`; `X-Content-Type-Options: nosniff`; `Referrer-Policy: no-referrer`; `Strict-Transport-Security: max-age=31536000` (only on `https`); `Cross-Origin-Opener-Policy: same-origin`; `Cache-Control: no-store`. If a header is missing, stop: the middleware is not active.

3. **A websocket from another origin is refused** (04 "CSRF"). The handshake path is `/_nicegui_ws/`. The Origin check refuses a foreign `Origin` and a missing one:

   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' \
     -H 'Origin: https://evil.example' \
     "$SETTINGS_BASE_URL/_nicegui_ws/socket.io/?EIO=4&transport=polling"
   curl -sS -o /dev/null -w '%{http_code}\n' \
     "$SETTINGS_BASE_URL/_nicegui_ws/socket.io/?EIO=4&transport=polling"
   ```

   Both must be refused (a 4xx). The exact status and URL are **(unverified)**: I did not run this against a deployment. What matters is the same request with `-H "Origin: $SETTINGS_BASE_URL"` is not refused. If the code or a test shows the exact answer, correct this step.

4. **Sign-in with a test account** (browser):
   - Open `$SETTINGS_BASE_URL/settings`. In dev the page carries the "Development deployment" banner; in production it must not.
   - Sign in. Dev: a test user listed on the consent screen. Production: an account of the organisation.
   - Expected: the consent page asks for exactly Chat spaces (read) and your identity, and you land on the list of spaces.
   - Production only: an account **outside** the organisation is refused by Google (`org_internal`), which proves the screen is Internal (08, Step 3). An account with a domain not in `ALLOWED_DOMAINS` is refused by the service.
   - A `redirect_uri_mismatch` error from Google means Step 6 item 2 is wrong: the address registered on the client is not the one in `SETTINGS_BASE_URL`.

5. **Enable and remove a space.** In `/settings`, enable a test space (read only), confirm, and see it in the list; then remove it. Then open **History** (`/settings/history`): the change and the removal are there, newest first. Records are in the Firestore console under `acls/{sub}/audit` (04 "Audit log").

6. **The `acl_change` log line.** 04 says each change emits one structured line (`event=acl_change`, hashed `sub`, space ID, after-state) to Cloud Logging. **I did not find `acl_change` in the code in `main` on 2026-11-07 (unverified: not implemented, or logged under another name).** Until it exists, skip this check and say so in the issue. Once it does:

   ```bash
   gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="gcm-settings" AND textPayload:"acl_change"' \
     --limit 5 --freshness 1h --project "$PROJECT_ID"
   ```

   The line must not contain message text or an email (`log_masking.py`, CLAUDE.md).

7. **`python -m google_chat_mcp.settings check` (#71).** Run it after each deploy as a Cloud Run job with this service's account, variables and secret (the commands are in [08, Step 8](08-operator-setup.md#step-8--check-the-setup), with `--args=google_chat_mcp.settings,check`) and read its output: configuration, Firestore access, the TTL policies, its own client secret and redirect URI as Google sees them, the public invoker setting, and that the service cannot read the connector's secrets. Every line must be `ok`, except `consent.screen` (always `not-checkable`: Step 8 of 08 lists what to do by hand) and any line the document says may be `not-checkable`. A `FAIL` line names the section to read. The job flags and the probes at Google's endpoints are **(unverified until the first run)**; a probe that does not recognise Google's answer says so instead of passing.

8. **Idle disconnect** (optional, 10 minutes). Leave a `/settings` tab untouched for ~11 minutes; it must navigate to "Session paused" (`/settings/paused`, 04 "Runtime and cost"). This is what keeps instance time bounded.

**If something fails:** read the logs (below), do not start debugging in production with wider access. Roll back (Step 11) first if users are affected.

## Step 9 — Reading the logs

```bash
gcloud run services logs read gcm-settings --region "$REGION" --project "$PROJECT_ID" --limit 100
```

(`gcloud logging read` with `resource.labels.service_name="gcm-settings"` works as well.) The service logs at INFO to stderr. Start-up failures show as `Configuration error: …`. Message text and e-mail addresses must not appear; if one does, that is a bug to report (do not paste the log into a public issue).

## Step 10 — Update to a new `stable`

1. Read the pull requests since the last `stable` for anything that needs a **deploy order** (below) or new variables. Compare this document's variable table with the code (`settings/config.py`, `remote/policy.py`).
2. Note the **current** revision, it is your rollback target:

   ```bash
   gcloud run revisions list --service gcm-settings --region "$REGION" --project "$PROJECT_ID"
   gcloud run services describe gcm-settings --region "$REGION" --project "$PROJECT_ID" \
     --format='value(status.traffic)'
   ```

3. Check out `stable` and build and push the new image (Steps 2–3).
4. Deploy the image only. Changing only the image keeps the other settings:

   ```bash
   gcloud run deploy gcm-settings --image "$IMAGE" \
     --region "$REGION" --project "$PROJECT_ID"
   ```

   Add `--update-env-vars` only when the release notes say a variable changed. Do not run `terraform apply` for an image change: Terraform ignores the image. Do run it, by hand and after reading `terraform plan`, when `infra/` changed.
5. Run all checks of Step 8.
6. Record what you did (SHA, revision, date) where the maintainer keeps the operating notes. The release process (#59, "releases") is not written yet.

**Safer variant for production (optional).** Deploy without sending traffic, test the new revision on its tag URL, then move traffic:

```bash
gcloud run deploy gcm-settings --image "$IMAGE" --no-traffic --tag candidate \
  --region "$REGION" --project "$PROJECT_ID"
gcloud run services update-traffic gcm-settings --to-latest \
  --region "$REGION" --project "$PROJECT_ID"
```

**(unverified)** a tag URL has a different address from `SETTINGS_BASE_URL`, so the Origin check and the OAuth redirect will not work on it: it only shows that the container starts and answers. The full checks need the real address, so they happen after the traffic moves. Use it only to catch a revision that does not start.

### Order with `gcm-mcp` when the ACL schema changes

From 04 "Schema versioning": readers accept the current schema version and the previous one; writers always write the current one; a version **higher** than a reader knows fails closed (the MCP server treats the ACL as empty, `/settings` refuses to write). So when a release changes the schema in a way the previous version cannot read:

1. Release 1 (`stable` #1): the **readers** first. `gcm-mcp` is deployed (#60). `gcm-settings` is **not** touched.
2. Release 2 (`stable` #2): the **writers**. Deploy `gcm-settings` with this document.

Never deploy `gcm-settings` first: its new documents would make `gcm-mcp` see an empty ACL (no space reachable) until it is updated. If the release does not change the schema, the order does not matter.

## Step 11 — Roll back

A rollback needs no rebuild: the previous image is still in Artifact Registry (do not delete it, Step 3).

```bash
gcloud run revisions list --service gcm-settings --region "$REGION" --project "$PROJECT_ID"
gcloud run services update-traffic gcm-settings \
  --to-revisions PREVIOUS_REVISION=100 \
  --region "$REGION" --project "$PROJECT_ID"
```

Then:

1. Run the checks of Step 8 (the sign-in and a space change are enough in an emergency).
2. **Known Cloud Run behaviour, not verified here:** once traffic is pinned to a named revision, a later `gcloud run deploy` does not automatically take traffic. Return to "latest" deliberately with `gcloud run services update-traffic gcm-settings --to-latest` when the next good release is deployed.
3. Sessions: they live in Firestore, so a rollback does not sign anybody out. Open tabs reload (04 "Websocket, state and instances").
4. **Schema:** a rollback is safe for the data only if the previous revision reads the current schema (the rule above). If the bad release had changed the schema and written documents, `gcm-settings` at the old version fails closed on newer documents (refuses to write) until you deploy the fixed version. Do not edit documents in the console to "fix" this.
5. Open an issue with what failed; fix forward in a new `stable`.

## Step 12 — Rotating the client secret

1. In the console, add a **new secret** to the settings OAuth client (Google Auth Platform → Clients → the client). Keep the old one enabled for now **(unverified: that the console allows two active secrets at once)**.
2. Add it as a new version:

   ```bash
   gcloud secrets versions add gcm-settings-client-secret --data-file=- --project "$PROJECT_ID"
   ```

3. Make the service use it. The Terraform module references `version = "latest"`. When Cloud Run resolves `latest` for an environment variable (at instance start, or at deployment) is **(unverified: read the current Cloud Run "Configure secrets" page)**. The reliable way is a new revision: re-deploy the **same image** (`gcloud run deploy gcm-settings --image "$IMAGE" …`), or `gcloud run services update gcm-settings --update-secrets GOOGLE_OAUTH_CLIENT_SECRET=gcm-settings-client-secret:VERSION_NUMBER` with the explicit version.
4. Run Step 8 (sign-in).
5. Disable the old secret in the console, and **destroy the old Secret Manager version** (`gcloud secrets versions destroy N --secret gcm-settings-client-secret`). A disabled version still counts as active for the free tier of 6 versions (08, Step 1).

If the secret leaked: do 1–4 at once, then delete the old secret from the client, and read the audit records for unexpected changes.

## Step 13 — Measuring instance time in the first month

Open tabs keep an instance billed (04 "Runtime and cost"): this is the item closest to the free tier. Cloud Run's free tier is 180,000 vCPU-seconds and 360,000 GiB-seconds per month, request-based billing, aggregated by billing account and shared with `gcm-mcp` (08, Step 1; design estimate: worst case about 144,000 vCPU-s from `gcm-settings`).

- **Where to look.** Cloud Monitoring, metric `run.googleapis.com/container/billable_instance_time` for the service `gcm-settings` (Metrics Explorer, or the service's *Metrics* tab → billable instance time) **(unverified: metric name and label, from my knowledge of Cloud Run's metrics; find it in Metrics Explorer)**. Also the *Billing → Reports* page, filtered by Cloud Run.
- **When.** Once a week during the first month, and at the end. Note the figure for `gcm-settings` and for `gcm-mcp` separately.
- **Threshold.** If the total over the month passes **about 70 % of the free allowance** (126,000 vCPU-s), act before the month ends. Keep the budget alert from 08 as the safety net; it does not stop spending.
- **What to do if it nears the limit**, in this order:
  1. Check the idle disconnect works (Step 8, item 8): a tab must pause after 10 minutes without events. If it does not, that is a bug: open an issue.
  2. Ask users to close the tab when done.
  3. Lower the session or idle time in code (a change of `settings/idle.py`, through an issue and a `stable`).
  4. Lower `max-instances` (the cost cap is `2` x time).
  5. Accept a small cost: at Cloud Run's prices (see the pricing page) a few extra hours cost very little. Do **not** go below 1 vCPU.
  6. `min-instances=1` is **not** the answer: it is billed all the time and is not proposed in 04.

## Differences between dev and production, in one list

- `DEPLOY_MODE=development` is set in dev only (by `infra/envs/dev`); never in production.
- Dev: External consent screen in Testing, **weekly re-sign-in**, test-user list, "unverified app" warning; production: Internal.
- `ALLOWED_DOMAINS` is a real domain list in production; in dev it only has to be valid.
- Dev deploys `main`; production deploys only `stable`, by a manual, approved action.
- Dev: Firestore delete protection off, a personal project, own OAuth client, secrets and database; nothing is copied to production.
- A banner on the pages shows the development deployment; production has none.
- Public access may be blocked by the company's organisation policy (Step 7); not an issue in a personal project.

## Not covered here

The deployment of `gcm-mcp` (#60), the CI/CD pipeline and Terraform automation (#29), the user guide and the migration from the local server (#59), alerts on `acl_change`, and the release process.

## Open points to verify on the first deployment

1. The `acl_change` log line (not implemented in `main` on 2026-11-07; the `check` command of #71 does not cover logs), and the first run of `check` on a real project (the probes at Google's endpoints and the TTL read are unverified).
2. The exact answer to a foreign-origin websocket handshake (Step 8, item 3).
3. What the Firestore client does with no project variable at all (Step 7 table says: set it).
4. Which address Cloud Run serves for `SETTINGS_BASE_URL` (deterministic `…-PROJECTNUMBER.REGION.run.app` versus `status.url`).
5. Whether the organisation policy allows a public `gcm-settings` (Step 7).
6. Behaviour of secret `latest` on rotation, and whether the console allows two client secrets (Step 12).
7. The operator's required roles (Step 1).
8. The flags of `gcloud run deploy` and `update-traffic` against `--help`.
9. Update this document when PR #103 (Terraform) is merged: names, defaults and the `infra/README.md` order may change.
