"""The Firestore client factory (it never connects)."""

import pytest

pytest.importorskip("google.cloud.firestore")

from google_chat_mcp.remote import firestore as rf  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "FIRESTORE_DATABASE", "FIRESTORE_EMULATOR_HOST"):
        monkeypatch.delenv(var, raising=False)


def test_emulator_uses_the_test_project_and_default_database(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8686")
    assert rf.using_emulator()
    db = rf.create_client()
    assert db.project == rf.EMULATOR_PROJECT
    assert db._database == "(default)"


def test_project_and_database_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8686")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    monkeypatch.setenv("FIRESTORE_DATABASE", "gcm")
    db = rf.create_client()
    assert (db.project, db._database) == ("my-project", "gcm")


def test_arguments_override_the_environment(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8686")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "from-env")
    db = rf.create_client(project="explicit", database="other")
    assert (db.project, db._database) == ("explicit", "other")


def test_without_emulator_the_flag_is_off():
    assert not rf.using_emulator()
