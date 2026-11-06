# Infrastructure (Terraform)

Creates the GCP side of the remote connector described in [`docs/remote/`](../docs/remote/04-settings-server-design.md): two Cloud Run services from one image, Firestore, Secret Manager, Artifact Registry. Issue #96.

```
infra/
  modules/gcm-remote/   the shared module
  envs/dev/             personal project, DEPLOY_MODE=development (#86)
  envs/prod/            company project, production
```

One root per environment, each with its own state and its own variables. `DEPLOY_MODE=development` is set only in `envs/dev`; `envs/prod` never defines it, and a missing or unknown value means production.

## What it creates

| Resource | Notes |
|---|---|
| APIs | Cloud Run, Firestore, Secret Manager, Artifact Registry, Logging, Chat, IAM, Resource Manager |
| Cloud Run `gcm-settings` | `python -m google_chat_mcp.settings` (args `google_chat_mcp.settings`), session affinity, timeout 900 s |
| Cloud Run `gcm-mcp` | args `google_chat_mcp.remote`, no session affinity, timeout 300 s |
| Both services | scale to zero, `max-instances=2`, 1 vCPU, 512 MiB, request-based billing, `ingress=all`, Invoker IAM check disabled ([08-network-exposure.md](../docs/remote/08-network-exposure.md), N1) |
| Service accounts | `gcm-settings@`, `gcm-mcp@` |
| Firestore | the `(default)` database (the only one inside the free quota), delete protection on (off in dev), TTL on `expire_at` for `sessions`, `login_transactions` and `audit`, and on `expires_at` for the seven `mcp-*` token-store collections |
| Secret Manager | four containers, **no values**: `gcm-settings-client-secret`, `gcm-mcp-client-secret`, `gcm-mcp-fernet-key`, `gcm-mcp-jwt-signing-key` |
| Artifact Registry | repository `gcm`, clean-up policy: keep the 5 most recent versions, delete versions older than 30 days |

IAM: both accounts get `roles/datastore.user` with a condition on the database name (IAM cannot separate collections; the code does, see 04, "Access to Firestore by service"). `gcm-settings@` reads only its client secret. `gcm-mcp@` reads only its client secret, the Fernet key and the `jwt_signing_key`. No other binding exists, so `gcm-settings` cannot reach the token-store key or the signing key.

The image is ignored after creation (`ignore_changes`), so deploys of new revisions do not fight with Terraform.

## What it does not do

The consent screen, the two OAuth clients and the secret values are manual ([08-operator-setup.md](../docs/remote/08-operator-setup.md)); so are the project, billing, the state bucket and the budget alert. Terraform never reads or stores a secret value.

## Public access and the organisation policy

`public_access = "disable_iam_check"` (default) sets `invoker_iam_disabled` on both services. If the organisation enforces `constraints/run.managed.requireInvokerIam`, this is refused, and `gcm-mcp` cannot be public until the maintainer obtains an exception for that service. The alternative `public_access = "all_users"` grants `roles/run.invoker` to `allUsers`, which fails under domain-restricted sharing. See "If the organisation forbids it" in 08-network-exposure.md.

## Usage

Needs Terraform >= 1.6 and credentials of the operator (`gcloud auth application-default login`). The order matters because a Cloud Run revision cannot start with a secret that has no version, nor without an image:

```bash
cd infra/envs/dev          # or prod
cp terraform.example.tfvars terraform.tfvars   # edit
terraform init -backend-config="bucket=STATE_BUCKET"
terraform apply            # deploy_services = false: APIs, registry, database, accounts, empty secrets
```

1. Add the secret values (08-operator-setup.md, Step 5) and create the OAuth clients (Step 4).
2. Build and push the image to `image_repository` (output) tagged with the commit SHA.
3. Set `deploy_services = true` and `image`, then `terraform apply`.
4. Register the `oauth_redirect_uris` (output) on the two OAuth clients. The addresses are deterministic (`https://SERVICE-PROJECTNUMBER.REGION.run.app`), so they are known before the services exist.

Validate without a backend or credentials:

```bash
terraform fmt -check -recursive infra
cd infra/envs/dev && terraform init -backend=false && terraform validate
```

Commit `.terraform.lock.hcl` of each root after the first `init`.

## Token store expiry

The TTL policies on `expires_at` of the `mcp-*` collections (`mcp-oauth-proxy-clients`, `mcp-oauth-transactions`, `mcp-consent-csrf-tokens`, `mcp-authorization-codes`, `mcp-upstream-tokens`, `mcp-jti-mappings`, `mcp-refresh-tokens`) **must exist for the token store to expire**: without them its entries (including anonymous client registrations) stay in Firestore for ever, even though the code reads an expired entry as missing. If the list of collections in `remote/oauth.py` or 07 changes, update `token_store_collections` in the module. Whether TTL deletions count against the free delete quota is still to check on the dev project (04, "To verify", item 3). Tracked in #108.

## Names the code must agree with

`gcm-mcp` receives `BASE_URL`, `SETTINGS_BASE_URL`, `GOOGLE_CLIENT_ID`, `ALLOWED_DOMAINS`, `POLICY_*`, `GOOGLE_CLOUD_PROJECT`, `FIRESTORE_DATABASE` and the secrets as `GOOGLE_CLIENT_SECRET`, `OAUTH_FERNET_KEYS` and `OAUTH_JWT_SIGNING_KEY`, as read by `remote/oauth.py`. `gcm-settings` receives `SETTINGS_BASE_URL`, `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`.
