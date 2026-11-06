output "settings_url" {
  description = "Address of gcm-settings (SETTINGS_BASE_URL)."
  value       = local.settings_url
}

output "mcp_url" {
  description = "Address of gcm-mcp (BASE_URL). The connector URL to give to Claude is this plus /mcp."
  value       = local.mcp_url
}

output "settings_redirect_uri" {
  description = "Authorised redirect URI for the settings OAuth client (08-operator-setup.md, Step 4)."
  value       = "${local.settings_url}/settings/callback"
}

output "mcp_redirect_uri" {
  description = "Authorised redirect URI for the connector's OAuth client (08-operator-setup.md, Step 4)."
  value       = "${local.mcp_url}/auth/callback"
}

output "mcp_connector_url" {
  description = "URL to add as a custom connector in Claude."
  value       = "${local.mcp_url}/mcp"
}

output "image_repository" {
  description = "Artifact Registry path to push the image to: <this>/gcm-remote:<commit SHA>."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "secret_ids" {
  description = "Secret Manager containers whose values the operator sets."
  value       = sort(keys(google_secret_manager_secret.this))
}

output "service_accounts" {
  description = "Service accounts of the two services."
  value = {
    settings = google_service_account.settings.email
    mcp      = google_service_account.mcp.email
  }
}
