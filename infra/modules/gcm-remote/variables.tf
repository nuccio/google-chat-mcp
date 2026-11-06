# ---------------------------------------------------------------- Where

variable "project_id" {
  description = "GCP project of this environment. One project per environment (docs/remote/06-dev-cycle.md)."
  type        = string
}

variable "region" {
  description = "Region of Cloud Run, Artifact Registry and Firestore. Pick one inside the free tier (docs/remote/08-operator-setup.md, Step 1)."
  type        = string
}

variable "firestore_database" {
  description = "ID of the Firestore database. The first database of a project is the only one inside the free quota, so keep \"(default)\"."
  type        = string
  default     = "(default)"
}

variable "firestore_delete_protection" {
  description = "Refuse deletion of the Firestore database (it holds the ACLs and the token store)."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------- Image

variable "deploy_services" {
  description = <<-EOT
    Create the two Cloud Run services. Set to false for the first apply: it
    creates the APIs, the registry, the database, the service accounts and the
    empty secret containers, so that the operator can add the secret values and
    push the image. The services cannot start before both exist.
  EOT
  type        = bool
  default     = true
}

variable "image" {
  description = "Full image reference for both services, with the commit SHA as tag (never latest). Used when a service is created; later deploys change the image outside Terraform (ignore_changes)."
  type        = string
  default     = null

  validation {
    condition     = var.image == null || !endswith(var.image, ":latest")
    error_message = "Do not use the latest tag: a revision must point to an exact commit."
  }
}

variable "registry_keep_count" {
  description = "Number of most recent image versions the Artifact Registry clean-up policy always keeps."
  type        = number
  default     = 5
}

variable "registry_delete_after_days" {
  description = "Image versions older than this, and not among the most recent ones, are deleted."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------- Configuration shared by the services

variable "development_mode" {
  description = <<-EOT
    true sets DEPLOY_MODE=development on both services: accounts without an hd
    claim are accepted. Only infra/envs/dev sets it. When false the variable is
    not defined at all, which the code treats as production (any missing or
    unknown value means production).
  EOT
  type        = bool
  default     = false
}

variable "allowed_domains" {
  description = "ALLOWED_DOMAINS: the Workspace domains whose accounts may sign in (comma-separated in the service)."
  type        = list(string)
  default     = []
}

variable "policy_allow_write" {
  description = "POLICY_ALLOW_WRITE. null leaves the variable unset (the code default applies)."
  type        = bool
  default     = null
}

variable "policy_allow_unattended" {
  description = "POLICY_ALLOW_UNATTENDED. null leaves the variable unset."
  type        = bool
  default     = null
}

variable "policy_space_allowlist" {
  description = "POLICY_SPACE_ALLOWLIST: space IDs (without the spaces/ prefix). Empty means every space is allowed."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------- OAuth clients (created by hand, docs/remote/08-operator-setup.md Step 4)

variable "settings_client_id" {
  description = "Client ID of the settings OAuth client (not a secret). Its secret goes in Secret Manager."
  type        = string
}

variable "mcp_client_id" {
  description = "Client ID of the connector's OAuth client (not a secret). Its secret goes in Secret Manager."
  type        = string
}

# ---------------------------------------------------------------- Network (docs/remote/08-network-exposure.md)

variable "public_access" {
  description = <<-EOT
    How the services are made reachable from the internet:
    - "disable_iam_check" (default, N1): the Invoker IAM check is disabled
      (invoker_iam_disabled). Needs constraints/run.managed.requireInvokerIam
      not to be enforced.
    - "all_users": grants roles/run.invoker to allUsers. Fails under a
      domain-restricted-sharing organisation policy.
    If the organisation enforces requireInvokerIam, neither works for gcm-mcp
    and an exception is needed (08-network-exposure.md, "If the organisation
    forbids it").
  EOT
  type        = string
  default     = "disable_iam_check"

  validation {
    condition     = contains(["disable_iam_check", "all_users"], var.public_access)
    error_message = "public_access must be \"disable_iam_check\" or \"all_users\"."
  }
}

# ---------------------------------------------------------------- Runtime (docs/remote/04, "Runtime and cost"; 07)

variable "max_instances" {
  description = "max-instances of both services (04 and N1: 2)."
  type        = number
  default     = 2
}

variable "settings_timeout_seconds" {
  description = "Request timeout of gcm-settings (04: 15 minutes, for the websocket)."
  type        = number
  default     = 900
}

variable "mcp_timeout_seconds" {
  description = "Request timeout of gcm-mcp (07 and N1: 300 s)."
  type        = number
  default     = 300
}

variable "cpu" {
  description = "vCPU of each instance."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory of each instance."
  type        = string
  default     = "512Mi"
}
