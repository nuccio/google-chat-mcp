output "settings_url" {
  value = module.gcm_remote.settings_url
}

output "mcp_url" {
  value = module.gcm_remote.mcp_url
}

output "mcp_connector_url" {
  value = module.gcm_remote.mcp_connector_url
}

output "oauth_redirect_uris" {
  description = "Authorised redirect URIs to register on the two OAuth clients (08-operator-setup.md, Step 4)."
  value = {
    settings_client  = module.gcm_remote.settings_redirect_uri
    connector_client = module.gcm_remote.mcp_redirect_uri
  }
}

output "image_repository" {
  value = module.gcm_remote.image_repository
}

output "secret_ids" {
  value = module.gcm_remote.secret_ids
}

output "service_accounts" {
  value = module.gcm_remote.service_accounts
}
