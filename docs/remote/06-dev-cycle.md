# 06 — Development cycle of the remote services

How the two remote services (`gcm-settings`, #52, and `gcm-mcp`, #53) are developed, tested, deployed and kept consistent. It builds on [04-settings-server-design.md](04-settings-server-design.md), which holds the architecture and the implementation order, and on [05-settings-ui-mock.md](05-settings-ui-mock.md) (the mock, #57).

**Principle:** one repository, one container image, one Terraform module, one pipeline, two Cloud Run services.

---

## Shared code

- **`google_chat_mcp` (core)** holds the `Permissions` type and its invariants. `/settings` uses it when it saves, the MCP server when it reads. The dependency goes one way: `settings` depends on the core, never the other way round (NFR-6).
- **`google_chat_mcp.remote`** is shared by the two remote services: the Firestore client, the Firestore `AclProvider`, the admin policy module (`ALLOWED_DOMAINS`, `POLICY_*`) and the `OAuthProxy` credentials. A configuration mismatch can only make the MCP server stricter than the page, never looser.
- **`google_chat_mcp.settings`** is the UI and its backend, with its own entry point (`python -m google_chat_mcp.settings`).
- `Permissions` in the core (step 2 of the implementation order in 04) is done together with #53, not from the UI side alone.
- **Schema first:** define the `acls` and `users` document schema (with `schema_version`) and its contract tests in the core before building either side, so the schema does not change under parallel work.

---

## Local development and tests

| Level | What runs | What it covers | Where |
|---|---|---|---|
| L0 | pytest with in-memory backends (`settings/mock.py`) | rules, UI flow, `Permissions` | local and CI |
| L1 | pytest against the **Firestore emulator** | `AclStore` (transactions, `ConcurrentChange`), sessions, login transactions | local and CI (emulator as a service container) |
| L2 | `python -m google_chat_mcp.settings.mock` with the emulated Firestore and a fake sign-in | developing the UI and flow with persistent data | local |
| L3 | the settings service locally with **real Google sign-in** (a development OAuth client with redirect `http://localhost:…/settings/callback`) and the emulated Firestore | real login, `hd`, tokens, a real `spaces.list` | local, by hand |

- The Chat API cannot be emulated. Tests use a fake `SpacesSource`; the real one is tried in L3 and on GCP.
- One emulator fixture in `tests/`, used by both services.
- **Contract tests on the schema:** what `settings` writes, the MCP server reads, and the reverse.
- **No unexpected writes:** every MCP tool runs against the emulator and no document changes except `users/{sub}.last_mcp_seen_at` and the token store (see [Access to Firestore by service](04-settings-server-design.md#access-to-firestore-by-service)).
- `tests/test_local_mode.py` keeps guarding NFR-7: no merge may break the local server.
- CI smoke test: `docker build`, and the image starts with both commands.

---

## What is done only on GCP

Cannot be emulated; checked in the dev project:

- The points in [To verify before implementation](04-settings-server-design.md#to-verify-before-implementation): websocket with session affinity and scale to zero, the `Origin` check on the real `run.app` address, cold start, instance time billed per open tab, free tier and regions.
- `spaces.get` for a space the user has left (403/404), the `hd` claim for secondary domains, consent skipped with a second OAuth client.
- IAM (service accounts, the condition on the database), Secret Manager, the Firestore TTL policies, logs and alerts on `acl_change`.
- **Checking the setup itself** (consent screen, OAuth clients, secrets, Firestore access, configuration), built in #71: a `check` command per service, run with the service's own service account; tests marked `gcp`; and an assisted sign-in script, where one person signs in once and the script checks the result. These are not UI features: a page in `/settings` could not report the failures that stop sign-in.

One-time manual bootstrap, per project:

- Create the project and link billing.
- Configure the consent screen (Internal), by hand: Google offers no supported API for it (the IAP OAuth Admin APIs were shut down on 19 March 2026). The checklist is in #59, and #71 checks the result.
- Create the **web OAuth clients** (Terraform does not manage them) and store their secrets in Secret Manager.
- Create the bucket for the Terraform state.

---

## IaC and deployment

**Terraform**, in `infra/`, with one module `gcm-remote` that instantiates both services. Shared values, defined once: `ALLOWED_DOMAINS`, `POLICY_*`, the database ID, `SETTINGS_BASE_URL`, the ACL schema version.

- **Managed by Terraform:** enabled APIs, service accounts and IAM, the Firestore database and its TTL policies, the secret containers (not the values), Artifact Registry with a cleanup policy, the two Cloud Run services, alerts. The image is ignored (`ignore_changes`), so Terraform and the deploy do not fight.
- **Service accounts** are separate, with minimal roles. IAM cannot tell Firestore collections apart, so the code enforces the separation, and the test above checks it.
- **Environments:** `infra/envs/dev` and `infra/envs/prod`, each with its own remote state in GCS.

**Pipeline** (GitHub Actions, authenticating to GCP with Workload Identity Federation, no keys):

- Pull request touching `infra/`: `terraform plan`, posted as a comment.
- `main` with green tests: build, tag with the commit SHA, push, deploy to **dev** automatically.
- **Prod:** a manual workflow on the commit marked `stable`, approved through a GitHub Environment. This is the rule in [Releases](04-settings-server-design.md#releases).
- `terraform apply` is always manual and approved.
- When the ACL schema changes: readers first (`gcm-mcp`), then writers (`gcm-settings`), each in its own `stable` ([Schema versioning](04-settings-server-design.md#schema-versioning)).
- **Rollback:** move traffic back to the previous Cloud Run revision; no rebuild.

---

## Order of work

1. `Permissions` in the core, and the document schema with contract tests (together with #53).
2. CI with the Firestore emulator, then the Firestore backends (step 3 of 04).
3. Bootstrap of the dev project and a first `infra/`, in parallel with Google sign-in and the Chat API (step 4 of 04).
4. Production entry point and security middlewares (step 5 of 04), then the deploy pipeline.
5. The prod project. The deployment instructions of #60 and #61 are written against the real pipeline.

---

## Open decisions

- **One GCP project or two (dev and prod).** Recommended: two. The Firestore free tier covers the first database of each project, so dev costs nothing and cannot touch prod data. The price is a second manual bootstrap.
- **Terraform or OpenTofu.** Recommended: Terraform.
