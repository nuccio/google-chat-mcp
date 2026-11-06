terraform {
  required_version = ">= 1.6"

  # State in a GCS bucket created by hand (docs/remote/08-operator-setup.md).
  # Partial configuration: pass the bucket at init time,
  #   terraform init -backend-config="bucket=BUCKET"
  # or use `terraform init -backend=false` to only validate.
  backend "gcs" {
    prefix = "gcm-remote"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.40, < 7.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
