# Copy to terraform.tfvars (ignored by git) and fill in.
project_id         = "gcm-prod-123456"
region             = "us-central1"
allowed_domains    = ["example.com"]
settings_client_id = "1234-settings.apps.googleusercontent.com"
mcp_client_id      = "1234-connector.apps.googleusercontent.com"

# First apply: deploy_services = false. Then add the secret values, push the
# image, and apply again with the image:
# deploy_services = true
# image           = "us-central1-docker.pkg.dev/gcm-prod-123456/gcm/gcm-remote:<commit SHA>"
deploy_services = false
