"""
Unit tests for the integration environment setup (tests/setup_env), with a
mocked API, and for what keeps it out of the published server.
"""

import json
import stat
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from google_chat_mcp import auth as server_auth
from google_chat_mcp.chat import ChatAPIError
from google_chat_mcp.config import acl_from_args
from tests.setup_env import auth as setup_auth
from tests.setup_env import setup

REPO_ROOT = Path(__file__).resolve().parent.parent

ADDRESS = "someone@example.com"
SEEDS = json.loads(setup.SEED_PATH.read_text())
CONFIG = setup.load_config()
DISPLAY_NAMES = [s.display_name for s in CONFIG.spaces]
DM = "spaces/DM"
DM_VAR = CONFIG.direct_message_env_var


class FakeChat:
    """In-memory API; it offers only what the setup may call."""

    def __init__(self, spaces=(), dm_type="DIRECT_MESSAGE", create_error=None):
        self.existing = [
            {"name": f"spaces/{name}", "displayName": name, "spaceType": "SPACE"} for name in spaces
        ]
        self.dm_type = dm_type
        self.create_error = create_error
        self.created = []
        self.posted = []
        self.stored = {}
        self.page_limit = None  # the API can return fewer messages than asked
        self.spaces = MagicMock()
        self.spaces.list.side_effect = lambda: list(self.existing)
        self.spaces.get.side_effect = lambda name: {"name": name, "spaceType": self.dm_type}
        self.messages = MagicMock()
        self.messages.list.side_effect = self._list_messages
        self.messages.send.side_effect = self._send

    def _list_messages(self, name, page_size, page_token=None):
        """Pages go from the newest back, as in the API."""
        texts = self.stored.get(name, [])
        end = int(page_token) if page_token else len(texts)
        start = max(0, end - (self.page_limit or page_size))
        return {
            "messages": [
                {"name": f"{name}/messages/{i}", "text": texts[i]} for i in range(start, end)
            ],
            "next_page_token": str(start) if start else None,
        }

    def _send(self, name, text):
        self.posted.append((name, text))
        self.stored.setdefault(name, []).append(text)

    def create_space(self, display_name):
        if self.create_error:
            raise self.create_error
        self.created.append(display_name)
        space = {"name": f"spaces/{display_name}", "displayName": display_name, "spaceType": "SPACE"}
        self.existing.append(space)
        return space


@pytest.fixture
def env_path(tmp_path):
    return tmp_path / ".env"


def _read_env(env_path):
    return dict(line.split("=", 1) for line in env_path.read_text().splitlines() if "=" in line)


def _assert_nothing_done(chat):
    assert chat.created == []
    assert chat.posted == []


# --- Spaces ---


def test_creates_what_is_missing_and_writes_env(env_path, capsys):
    chat = FakeChat()
    setup.run(chat, env_path=env_path)
    assert chat.created == DISPLAY_NAMES
    assert _read_env(env_path) == {s.env_var: f"spaces/{s.display_name}" for s in CONFIG.spaces}
    assert "Created mcp-test-read-only" in capsys.readouterr().out


def test_second_run_changes_nothing(env_path, capsys):
    chat = FakeChat()
    setup.run(chat, env_path=env_path)
    env_before = env_path.read_text()
    chat.created.clear()
    chat.posted.clear()
    capsys.readouterr()

    setup.run(chat, env_path=env_path)

    _assert_nothing_done(chat)
    assert env_path.read_text() == env_before
    out = capsys.readouterr().out
    assert "Found   mcp-test-read-only" in out
    assert "Created" not in out


def test_reuses_the_spaces_it_finds_and_creates_the_others(env_path):
    chat = FakeChat(spaces=["mcp-test-read-only", "unrelated space"])
    setup.run(chat, env_path=env_path)
    assert chat.created == DISPLAY_NAMES[1:]
    assert _read_env(env_path)["CHAT_READ_ONLY_SPACE"] == "spaces/mcp-test-read-only"
    assert "spaces/unrelated space" not in _read_env(env_path).values()


def test_duplicate_display_name_stops_without_choosing(env_path):
    chat = FakeChat(spaces=["mcp-test-write-only", "mcp-test-write-only"])
    with pytest.raises(setup.SetupError, match="mcp-test-write-only"):
        setup.run(chat, env_path=env_path)
    _assert_nothing_done(chat)
    assert not env_path.exists()


def test_a_conversation_with_the_same_name_is_not_a_space(env_path):
    chat = FakeChat()
    chat.existing.append(
        {"name": "spaces/GROUP", "displayName": "mcp-test-read-only", "spaceType": "GROUP_CHAT"}
    )
    setup.run(chat, env_path=env_path)
    assert "mcp-test-read-only" in chat.created


def test_already_exists_stops_with_an_error(env_path):
    chat = FakeChat(create_error=ChatAPIError(409, f"ALREADY_EXISTS {ADDRESS}"))
    with pytest.raises(setup.SetupError, match="ALREADY_EXISTS") as excinfo:
        setup.run(chat, env_path=env_path)
    assert ADDRESS not in str(excinfo.value)


def test_other_create_error_explains_the_manual_fallback(env_path):
    chat = FakeChat(create_error=ChatAPIError(403, f"denied {ADDRESS}"))
    with pytest.raises(setup.SetupError, match="by hand") as excinfo:
        setup.run(chat, env_path=env_path)
    assert ADDRESS not in str(excinfo.value)
    assert "403" in str(excinfo.value)


# --- .env ---


def test_env_keeps_everything_else(env_path):
    env_path.write_text(f"# a comment\nGOOGLE_CLIENT_SECRETS=/some/path\n{DM_VAR}={DM}\n")
    setup.run(FakeChat(), env_path=env_path)
    text = env_path.read_text()
    assert "# a comment\n" in text
    assert "GOOGLE_CLIENT_SECRETS=/some/path\n" in text
    assert f"{DM_VAR}={DM}\n" in text


@pytest.mark.parametrize(
    "spaces",
    [["mcp-test-read-only"], []],
    ids=["points to another space", "the space would be created"],
)
def test_env_conflict_stops_before_anything_is_done(env_path, spaces):
    original = "CHAT_READ_ONLY_SPACE=spaces/OLD\n"
    env_path.write_text(original)
    chat = FakeChat(spaces=spaces)
    with pytest.raises(setup.SetupError, match="CHAT_READ_ONLY_SPACE is spaces/OLD"):
        setup.run(chat, env_path=env_path)
    _assert_nothing_done(chat)
    assert env_path.read_text() == original


# --- Direct message ---


def test_dm_not_set_prints_instructions_and_carries_on(env_path, capsys):
    chat = FakeChat()
    setup.run(chat, env_path=env_path)
    out = capsys.readouterr().out
    assert f"{DM_VAR} is not set" in out
    assert "DEDICATED TO" in out
    chat.spaces.get.assert_not_called()
    assert chat.created == DISPLAY_NAMES


def test_dm_set_and_valid(env_path, capsys):
    env_path.write_text(f"{DM_VAR}={DM}\n")
    setup.run(FakeChat(), env_path=env_path)
    out = capsys.readouterr().out
    assert f"direct message: {DM}" in out
    assert "is not set" not in out


@pytest.mark.parametrize("dm_type", ["SPACE", "GROUP_CHAT", None])
def test_dm_of_another_type_stops_before_anything_is_done(env_path, dm_type):
    env_path.write_text(f"{DM_VAR}={DM}\n")
    chat = FakeChat(dm_type=dm_type)
    with pytest.raises(setup.SetupError, match="DIRECT_MESSAGE"):
        setup.run(chat, env_path=env_path)
    _assert_nothing_done(chat)


def test_dm_unreadable_stops_without_the_response_body(env_path):
    env_path.write_text(f"{DM_VAR}={DM}\n")
    chat = FakeChat()
    chat.spaces.get.side_effect = ChatAPIError(404, f"not found {ADDRESS}")
    with pytest.raises(setup.SetupError, match="404") as excinfo:
        setup.run(chat, env_path=env_path)
    assert ADDRESS not in str(excinfo.value)
    _assert_nothing_done(chat)


# --- Seed messages ---


def test_seeds_go_only_to_the_read_only_space_in_order(env_path):
    chat = FakeChat()
    setup.run(chat, env_path=env_path)
    assert chat.posted == [("spaces/mcp-test-read-only", text) for text in SEEDS]


def test_only_missing_seeds_are_posted(env_path):
    chat = FakeChat(spaces=["mcp-test-read-only"])
    chat.stored["spaces/mcp-test-read-only"] = [SEEDS[0]]
    setup.run(chat, env_path=env_path)
    assert [text for _, text in chat.posted] == SEEDS[1:]


def test_seeds_on_older_pages_are_not_posted_again(env_path):
    chat = FakeChat(spaces=["mcp-test-read-only"])
    chat.stored["spaces/mcp-test-read-only"] = list(SEEDS)
    chat.page_limit = 1
    setup.run(chat, env_path=env_path)
    assert chat.posted == []


def test_seed_fixture_allows_checking_order_and_page_size():
    assert len(SEEDS) >= 3
    assert len(set(SEEDS)) == len(SEEDS)


# --- Output of main() ---


@pytest.fixture
def run_main(monkeypatch):
    monkeypatch.setattr(setup, "load_credentials", MagicMock())
    monkeypatch.setattr(setup, "AuthorizedSession", MagicMock())

    def _run(error):
        monkeypatch.setattr(setup, "run", MagicMock(side_effect=error))
        return setup.main()

    return _run


def test_main_prints_only_the_status_of_an_api_error(run_main, capsys):
    assert run_main(ChatAPIError(500, f"boom {ADDRESS}")) == 1
    captured = capsys.readouterr()
    assert ADDRESS not in captured.out + captured.err
    assert "500" in captured.err


def test_main_reports_a_setup_error(run_main, capsys):
    assert run_main(setup.SetupError("something found")) == 1
    assert "something found" in capsys.readouterr().err


def test_main_succeeds(run_main):
    assert run_main(None) == 0


# --- The client ---


def test_create_space_call():
    session = MagicMock()
    session.post.return_value.ok = True
    session.post.return_value.json.return_value = {"name": "spaces/NEW"}
    assert setup.SetupClient(session).create_space("name") == {"name": "spaces/NEW"}
    args, kwargs = session.post.call_args
    assert args[0].endswith("/v1/spaces")
    assert kwargs["json"] == {"spaceType": "SPACE", "displayName": "name"}


# --- tests/test_env.toml ---


def test_config_permissions_are_valid_for_the_server():
    data = tomllib.loads(setup.CONFIG_PATH.read_text())["spaces"]
    with_permissions = {k: v["permissions"] for k, v in data.items() if "permissions" in v}
    assert with_permissions == {
        "read_only": "r",
        "write_only": "w:unattended",
        "read_write_confirm": "rw",
        "read_write_unattended": "rw:unattended",
    }
    acl_from_args([f"spaces/{k}:{p}" for k, p in with_permissions.items()])


def test_config_names_and_variables_are_unique():
    assert len(set(DISPLAY_NAMES)) == len(DISPLAY_NAMES)
    variables = [s.env_var for s in CONFIG.spaces] + [DM_VAR]
    assert len(set(variables)) == len(variables)
    assert setup._SEED_SPACE in [s.key for s in CONFIG.spaces]


def test_config_contains_no_addresses():
    assert "@" not in setup.CONFIG_PATH.read_text()


# --- Credentials of the setup ---


def test_token_is_outside_the_server_path():
    assert setup_auth.TOKEN_PATH != server_auth.TOKEN_PATH
    assert server_auth.CONFIG_DIR not in setup_auth.TOKEN_PATH.parents


def test_setup_scopes():
    assert setup_auth.SCOPES == [
        "https://www.googleapis.com/auth/chat.spaces.create",
        "https://www.googleapis.com/auth/chat.spaces.readonly",
        "https://www.googleapis.com/auth/chat.messages",
    ]


def test_token_is_written_owner_only(tmp_path):
    token_path = tmp_path / "setup" / "token.json"
    creds = MagicMock()
    creds.to_json.return_value = '{"refresh_token": "x"}'
    setup_auth._write_token(creds, token_path)
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700
    assert [p.name for p in token_path.parent.iterdir()] == ["token.json"]


def test_load_credentials_asks_for_consent_without_a_token(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    creds = MagicMock()
    creds.to_json.return_value = "{}"
    monkeypatch.setattr(setup_auth, "_run_auth_flow", lambda: creds)
    assert setup_auth.load_credentials(token_path) is creds
    assert token_path.exists()


# --- Kept out of the published server ---


def test_wheel_contains_only_the_server(tmp_path):
    subprocess.run(
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    names = zipfile.ZipFile(wheel).namelist()
    assert "google_chat_mcp/server.py" in names
    top_level = {n.split("/")[0] for n in names}
    assert {t for t in top_level if not t.endswith(".dist-info")} == {"google_chat_mcp"}
    assert not [n for n in names if "setup_env" in n or "test_env" in n]


def test_server_scopes_are_unchanged():
    assert server_auth.SCOPES == [
        "https://www.googleapis.com/auth/chat.spaces.readonly",
        "https://www.googleapis.com/auth/chat.messages.readonly",
        "https://www.googleapis.com/auth/chat.messages.create",
        "https://www.googleapis.com/auth/chat.memberships.readonly",
    ]


def test_server_package_knows_nothing_about_the_setup():
    for path in (REPO_ROOT / "google_chat_mcp").rglob("*.py"):
        text = path.read_text()
        assert "setup_env" not in text, path
        assert "chat.spaces.create" not in text, path


def test_setup_has_no_entry_point():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]
    assert project["scripts"] == {"google-chat-mcp": "google_chat_mcp.__main__:main"}
