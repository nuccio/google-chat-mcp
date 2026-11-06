# Production: the company's GCP project, Internal consent screen.
#
# DEPLOY_MODE is deliberately not set here: a missing value means production.
# If the organisation enforces constraints/run.managed.requireInvokerIam, the
# services cannot be made public with either value of public_access; see
# docs/remote/08-network-exposure.md ("If the organisation forbids it").

module "gcm_remote" {
  source = "../../modules/gcm-remote"

  project_id      = var.project_id
  region          = var.region
  deploy_services = var.deploy_services
  image           = var.image

  allowed_domains         = var.allowed_domains
  settings_client_id      = var.settings_client_id
  mcp_client_id           = var.mcp_client_id
  public_access           = var.public_access
  policy_allow_write      = var.policy_allow_write
  policy_allow_unattended = var.policy_allow_unattended
  policy_space_allowlist  = var.policy_space_allowlist
}
