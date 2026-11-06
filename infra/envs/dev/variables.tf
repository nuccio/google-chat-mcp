variable "project_id" {
  description = "GCP project of this environment."
  type        = string
}

variable "region" {
  description = "Region (inside the free tier)."
  type        = string
}

variable "deploy_services" {
  description = "false for the first apply, before the secret values and the image exist."
  type        = bool
  default     = true
}

variable "image" {
  description = "Image with the commit SHA as tag. Required when deploy_services is true."
  type        = string
  default     = null
}

variable "allowed_domains" {
  description = "ALLOWED_DOMAINS."
  type        = list(string)
  default     = []
}

variable "settings_client_id" {
  description = "Client ID of the settings OAuth client."
  type        = string
}

variable "mcp_client_id" {
  description = "Client ID of the connector's OAuth client."
  type        = string
}

variable "public_access" {
  description = "\"disable_iam_check\" (default) or \"all_users\". See the module."
  type        = string
  default     = "disable_iam_check"
}

variable "policy_allow_write" {
  type    = bool
  default = null
}

variable "policy_allow_unattended" {
  type    = bool
  default = null
}

variable "policy_space_allowlist" {
  type    = list(string)
  default = []
}
