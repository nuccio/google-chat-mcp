"""Tests against a real GCP project, with `gcloud` (marker `gcp`; issue #71).

They look at the project from outside, with the operator's own gcloud login:
what the `check` commands cannot see from inside a service account (the
project's parent, the enabled APIs, the secret containers, the Cloud Run
settings). They only read. They are left out of every normal run and of CI;
see tests/README.md ("GCP tests").

    GCM_GCP_PROJECT=my-dev-project GCM_GCP_ENV=dev uv run pytest -m gcp

- `GCM_GCP_PROJECT`: the project ID. Required.
- `GCM_GCP_ENV`: `dev` (personal project, External consent screen, no
  organisation) or `prod` (Internal screen, the project is under the Workspace
  organisation). Required: the organisation test only applies to `prod`.
- `GCM_GCP_REGION`: region of the Cloud Run services (needed with the next one).
- `GCM_GCP_DEPLOYED=1`: also test the Cloud Run services (after a deploy).

Like the emulator tests, a run meant to test the project fails with a message
when it is not set up; it never passes by skipping everything.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.gcp

ROOT = Path(__file__).resolve().parent.parent
TF = (ROOT / "infra/modules/gcm-remote/main.tf").read_text()
SECRETS = sorted(re.findall(r'"(gcm-[a-z-]+)"\s+=\s+google_service_account', TF))
APIS = sorted(re.findall(r'"([a-z]+\.googleapis\.com)"', TF.split("google_project_service")[1].split("]")[0]))


@pytest.fixture(scope="module")
def project():
    value = os.environ.get("GCM_GCP_PROJECT", "").strip()
    if not value:
        pytest.fail("GCM_GCP_PROJECT is not set: see the docstring of tests/test_gcp.py")
    if os.environ.get("GCM_GCP_ENV") not in ("dev", "prod"):
        pytest.fail("GCM_GCP_ENV must be `dev` or `prod`")
    if not shutil.which("gcloud"):
        pytest.fail("gcloud is not installed or not on PATH")
    return value


def gcloud(*args: str):
    done = subprocess.run(
        ["gcloud", *args, "--format=json"], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, f"gcloud {' '.join(args[:3])} failed: {done.stderr.strip()[:300]}"
    return json.loads(done.stdout or "null")


def test_the_lists_were_read_from_terraform():
    assert len(SECRETS) == 4 and len(APIS) >= 8, (SECRETS, APIS)


def test_project_exists_and_is_active(project):
    info = gcloud("projects", "describe", project)
    assert info["lifecycleState"] == "ACTIVE"


def test_prod_project_is_under_the_workspace_organisation(project):
    """An Internal consent screen needs the project to belong to the organisation."""
    if os.environ["GCM_GCP_ENV"] != "prod":
        pytest.skip("dev is a personal project without an organisation (08, 'Dev and production')")
    parent = gcloud("projects", "describe", project).get("parent") or {}
    assert parent.get("type") == "organization", (
        "the project has no organisation as parent: docs/remote/08-operator-setup.md#step-1--gcp-project-billing-and-region"
    )


def test_required_apis_are_enabled(project):
    enabled = {s["config"]["name"] for s in gcloud("services", "list", "--enabled", "--project", project)}
    missing = sorted(set(APIS) - enabled)
    assert not missing, f"not enabled: {missing} (docs/remote/08-operator-setup.md#step-2--enable-the-apis)"


@pytest.mark.parametrize("secret", SECRETS)
def test_each_secret_container_has_an_enabled_version(project, secret):
    versions = gcloud(
        "secrets", "versions", "list", secret, "--project", project, "--filter=state=ENABLED", "--limit=1"
    )
    assert versions, f"{secret}: no enabled version (docs/remote/08-operator-setup.md#step-5--secrets-secret-manager)"


@pytest.fixture(scope="module")
def deployed():
    if not os.environ.get("GCM_GCP_DEPLOYED"):
        pytest.skip("set GCM_GCP_DEPLOYED=1 after the services are deployed")
    if not os.environ.get("GCM_GCP_REGION"):
        pytest.fail("GCM_GCP_REGION is not set")
    return os.environ["GCM_GCP_REGION"]


@pytest.mark.parametrize("service", ["gcm-mcp", "gcm-settings"])
def test_cloud_run_service_is_public(project, deployed, service):
    """The Invoker IAM check is disabled, or `allUsers` is an invoker (09, N1).

    Unverified: the annotation name below is the one `gcloud run services
    describe` printed in Google's documentation, not one seen on a project.
    """
    described = gcloud("run", "services", "describe", service, "--region", deployed, "--project", project)
    annotations = described.get("metadata", {}).get("annotations", {})
    disabled = annotations.get("run.googleapis.com/invoker-iam-disabled") == "true"
    if not disabled:
        policy = gcloud("run", "services", "get-iam-policy", service, "--region", deployed, "--project", project)
        members = {m for b in policy.get("bindings", []) if b["role"] == "roles/run.invoker" for m in b["members"]}
        assert "allUsers" in members, (
            f"{service} is not public: docs/remote/11-deploy-gcm-mcp.md#step-4--the-cloud-run-service"
        )
