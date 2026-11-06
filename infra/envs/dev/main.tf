# Development environment: a personal GCP project (#86). The consent screen is
# External in Testing, so accounts have no `hd` claim.

module "gcm_remote" {
  source = "../../modules/gcm-remote"

  project_id      = var.project_id
  region          = var.region
  deploy_services = var.deploy_services
  image           = var.image

  # The only place DEPLOY_MODE=development is set (docs/remote/06-dev-cycle.md).
  development_mode = true

  allowed_domains         = var.allowed_domains
  settings_client_id      = var.settings_client_id
  mcp_client_id           = var.mcp_client_id
  public_access           = var.public_access
  policy_allow_write      = var.policy_allow_write
  policy_allow_unattended = var.policy_allow_unattended
  policy_space_allowlist  = var.policy_space_allowlist

  # Dev is disposable: allow `terraform destroy` to remove the database.
  firestore_delete_protection = false
}
