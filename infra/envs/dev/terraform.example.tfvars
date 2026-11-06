# Copy to terraform.tfvars (ignored by git) and fill in.
project_id         = "gcm-dev-123456"
region             = "us-central1"
settings_client_id = "1234-settings.apps.googleusercontent.com"
mcp_client_id      = "1234-connector.apps.googleusercontent.com"

# First apply: deploy_services = false. Then add the secret values, push the
# image, and apply again with the image:
# deploy_services = true
# image           = "us-central1-docker.pkg.dev/gcm-dev-123456/gcm/gcm-remote:<commit SHA>"
deploy_services = false
