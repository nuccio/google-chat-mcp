terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source = "hashicorp/google"
      # invoker_iam_disabled on google_cloud_run_v2_service needs a recent 6.x.
      version = ">= 6.40, < 7.0"
    }
  }
}
