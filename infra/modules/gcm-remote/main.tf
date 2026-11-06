# The remote connector on GCP: two Cloud Run services (gcm-settings, gcm-mcp)
# from one image, Firestore, Secret Manager, Artifact Registry.
#
# Design: docs/remote/04-settings-server-design.md, 06-dev-cycle.md,
# 07-mcp-server-design.md, 08-network-exposure.md. What Terraform does not do
# (consent screen, OAuth clients, secret values): 08-operator-setup.md.

data "google_project" "this" {
  project_id = var.project_id
}

locals {
  settings_name = "gcm-settings"
  mcp_name      = "gcm-mcp"

  # Cloud Run's deterministic URL. Known before the services exist, so each
  # service can be given its own address (SETTINGS_BASE_URL, BASE_URL) without
  # a dependency cycle.
  settings_url = "https://${local.settings_name}-${data.google_project.this.number}.${var.region}.run.app"
  mcp_url      = "https://${local.mcp_name}-${data.google_project.this.number}.${var.region}.run.app"

  # Environment variables shared by both services. DEPLOY_MODE is defined only
  # in development; absent means production.
  shared_env = merge(
    {
      GOOGLE_CLOUD_PROJECT = var.project_id
      FIRESTORE_DATABASE   = var.firestore_database
      ALLOWED_DOMAINS      = join(",", var.allowed_domains)
    },
    var.development_mode ? { DEPLOY_MODE = "development" } : {},
    var.policy_allow_write == null ? {} : { POLICY_ALLOW_WRITE = tostring(var.policy_allow_write) },
    var.policy_allow_unattended == null ? {} : { POLICY_ALLOW_UNATTENDED = tostring(var.policy_allow_unattended) },
    length(var.policy_space_allowlist) == 0 ? {} : { POLICY_SPACE_ALLOWLIST = join(",", var.policy_space_allowlist) },
  )
}

# ---------------------------------------------------------------- APIs

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "chat.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------- Artifact Registry

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "gcm"
  format        = "DOCKER"
  description   = "Image of the remote connector (one image, two services)"

  # Clean-up (06, "Container image"): keep the most recent versions, delete
  # what is older. A keep policy wins over a delete policy, so the image of a
  # revision that can still receive traffic should be among the recent ones.
  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = var.registry_keep_count
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "${var.registry_delete_after_days * 86400}s"
    }
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------- Firestore

resource "google_firestore_database" "this" {
  project                     = var.project_id
  name                        = var.firestore_database
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "OPTIMISTIC"
  delete_protection_state     = var.firestore_delete_protection ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"
  deletion_policy             = var.firestore_delete_protection ? "ABANDON" : "DELETE"
  app_engine_integration_mode = "DISABLED"

  depends_on = [google_project_service.apis]
}

# TTL policies (04, "Data model"). Deletion is asynchronous; the code also
# checks expire_at on every read.
resource "google_firestore_field" "ttl_sessions" {
  project    = var.project_id
  database   = google_firestore_database.this.name
  collection = "sessions"
  field      = "expire_at"
  ttl_config {}
  # Leave the default indexes alone.
  index_config {}
}

resource "google_firestore_field" "ttl_login_transactions" {
  project    = var.project_id
  database   = google_firestore_database.this.name
  collection = "login_transactions"
  field      = "expire_at"
  ttl_config {}
  index_config {}
}

# The audit records live in acls/{sub}/audit: one policy for that collection group.
resource "google_firestore_field" "ttl_audit" {
  project    = var.project_id
  database   = google_firestore_database.this.name
  collection = "audit"
  field      = "expire_at"
  ttl_config {}
  index_config {}
}

# ---------------------------------------------------------------- Service accounts

resource "google_service_account" "settings" {
  project      = var.project_id
  account_id   = local.settings_name
  display_name = "gcm-settings (settings UI)"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "mcp" {
  project      = var.project_id
  account_id   = local.mcp_name
  display_name = "gcm-mcp (MCP connector)"
  depends_on   = [google_project_service.apis]
}

# Both services get datastore.user on the one database and nothing else on
# Firestore. IAM cannot tell collections apart (04, "Access to Firestore by
# service"); the code enforces what each service may touch. The condition
# narrows the role to this database.
resource "google_project_iam_member" "firestore" {
  for_each = {
    settings = google_service_account.settings.member
    mcp      = google_service_account.mcp.member
  }

  project = var.project_id
  role    = "roles/datastore.user"
  member  = each.value

  condition {
    title       = "only-${var.firestore_database == "(default)" ? "default" : var.firestore_database}-database"
    description = "Limits datastore.user to the database of the remote connector"
    expression  = "resource.name == \"projects/${var.project_id}/databases/${var.firestore_database}\""
  }

  depends_on = [google_firestore_database.this]
}

# ---------------------------------------------------------------- Secret Manager (containers only: values are set by the operator)

locals {
  # secret ID => who may read it. Nobody else gets secretAccessor on it.
  secrets = {
    "gcm-settings-client-secret" = google_service_account.settings.member
    "gcm-mcp-client-secret"      = google_service_account.mcp.member
    "gcm-mcp-fernet-key"         = google_service_account.mcp.member
    "gcm-mcp-jwt-signing-key"    = google_service_account.mcp.member
  }
}

resource "google_secret_manager_secret" "this" {
  for_each = local.secrets

  project   = var.project_id
  secret_id = each.key

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "reader" {
  for_each = local.secrets

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}

# ---------------------------------------------------------------- gcm-settings

resource "google_cloud_run_v2_service" "settings" {
  count = var.deploy_services ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = local.settings_name

  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = var.public_access == "disable_iam_check"
  deletion_protection  = false

  template {
    service_account  = google_service_account.settings.email
    timeout          = "${var.settings_timeout_seconds}s"
    session_affinity = true

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image
      # The image starts gcm-settings by default (CMD); stated here as well.
      args = ["google_chat_mcp.settings"]

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = true # request-based billing
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = merge(local.shared_env, {
          SETTINGS_BASE_URL      = local.settings_url
          GOOGLE_OAUTH_CLIENT_ID = var.settings_client_id
        })
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "GOOGLE_OAUTH_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.this["gcm-settings-client-secret"].secret_id
            version = "latest"
          }
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.image != null
      error_message = "image is required when deploy_services is true."
    }
    # The deploy workflow changes the image; Terraform must not revert it.
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_project_iam_member.firestore,
    google_secret_manager_secret_iam_member.reader,
  ]
}

# ---------------------------------------------------------------- gcm-mcp

resource "google_cloud_run_v2_service" "mcp" {
  count = var.deploy_services ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = local.mcp_name

  # N1: public, no Invoker IAM check, max-instances 2. All access control is in the application.
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = var.public_access == "disable_iam_check"
  deletion_protection  = false

  template {
    service_account  = google_service_account.mcp.email
    timeout          = "${var.mcp_timeout_seconds}s"
    session_affinity = false # stateless (07, M6)

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image
      args  = ["google_chat_mcp.remote"]

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = merge(local.shared_env, {
          BASE_URL               = local.mcp_url
          SETTINGS_BASE_URL      = local.settings_url
          GOOGLE_OAUTH_CLIENT_ID = var.mcp_client_id
        })
        content {
          name  = env.key
          value = env.value
        }
      }

      # The variable names of the three secrets are defined by the code of
      # google_chat_mcp.remote (#53, #79); see infra/README.md.
      dynamic "env" {
        for_each = {
          GOOGLE_OAUTH_CLIENT_SECRET = "gcm-mcp-client-secret"
          FERNET_KEY                 = "gcm-mcp-fernet-key"
          JWT_SIGNING_KEY            = "gcm-mcp-jwt-signing-key"
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.image != null
      error_message = "image is required when deploy_services is true."
    }
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_project_iam_member.firestore,
    google_secret_manager_secret_iam_member.reader,
  ]
}

# ---------------------------------------------------------------- Public access via allUsers (only when chosen)

resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = var.deploy_services && var.public_access == "all_users" ? {
    settings = google_cloud_run_v2_service.settings[0]
    mcp      = google_cloud_run_v2_service.mcp[0]
  } : {}

  project  = var.project_id
  location = var.region
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------- Guard

# In production an empty ALLOWED_DOMAINS would refuse every account.
resource "terraform_data" "production_needs_domains" {
  lifecycle {
    precondition {
      condition     = var.development_mode || length(var.allowed_domains) > 0
      error_message = "allowed_domains must not be empty outside development."
    }
  }
}
