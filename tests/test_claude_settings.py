"""Contract test on the permission entries of .claude/settings.json.

The entries are part of the rules that limit Claude sessions (see
docs/maintainer-agent-workflow.md), so losing one must fail a test.
"""

import json
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

SETTINGS = Path(__file__).resolve().parent.parent / ".claude" / "settings.json"

NEVER_LIST = [
    "Bash(gh pr merge *--admin*)",
    "Bash(gh * --admin*)",
    "Bash(git push * main)",
    "Bash(git push * main:*)",
    "Bash(git push * HEAD:main)",
    "Bash(git push * stable)",
    "Bash(git push * stable:*)",
    "Bash(git push --force*)",
    "Bash(git push -f*)",
    "Bash(gh workflow run *)",
    "Bash(gh repo edit *)",
    "Bash(gh secret *)",
    "Bash(gh auth token*)",
    "Bash(uv run pytest -m integration*)",
    "mcp__github",
    "Read(~/.config/google-chat-mcp/**)",
    "Read(~/.config/claudio4nuccio/**)",
    "Read(**/token.json)",
    "Read(**/client_secrets.json)",
    "Read(**/client_secret*.json)",
    "Read(**/.env)",
]


def _load() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def _matches_any(rules: list[str], command: str) -> bool:
    """Whether a Bash command matches one of the rules, as a glob on the command."""
    patterns = [r[len("Bash(") : -1] for r in rules if r.startswith("Bash(")]
    return any(fnmatchcase(command, pattern) for pattern in patterns)


@pytest.fixture(scope="module")
def permissions() -> dict:
    return _load()["permissions"]


def test_declares_the_schema():
    assert _load()["$schema"] == "https://json.schemastore.org/claude-code-settings.json"


@pytest.mark.parametrize("entry", NEVER_LIST)
def test_never_list_is_denied(permissions, entry):
    assert entry in permissions["deny"]


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 1 --squash",
        "gh pr review 1 --approve",
        "gh issue create --parent 1 --title x --body y",
    ],
)
def test_coordinator_is_not_asked_inside_its_scope(permissions, command):
    assert not _matches_any(permissions["ask"], command)
    assert not _matches_any(permissions["deny"], command)


@pytest.mark.parametrize(
    "command",
    [
        "gh api repos/o/r/rulesets",
        "gh api repos/o/r/branches/main/protection",
        "gh api --paginate 'repos/{owner}/{repo}/issues/1/sub_issues' --jq '.[].number'",
    ],
)
def test_read_only_api_calls_are_allowed(permissions, command):
    assert not _matches_any(permissions["deny"], command)
    assert not _matches_any(permissions["ask"], command)


@pytest.mark.parametrize(
    "command",
    [
        "gh api -X PUT repos/o/r/rulesets/1",
        "gh api -XPUT repos/o/r/rulesets/1",
        "gh api repos/o/r/rulesets/1 -X DELETE",
        "gh api --method POST repos/o/r/rulesets",
        "gh api repos/o/r/rulesets --method=POST",
        "gh api repos/o/r/labels -f name=x",
        "gh api repos/o/r/labels -F name=x",
        "gh api repos/o/r/labels --field name=x",
        "gh api repos/o/r/labels --raw-field name=x",
        "gh api repos/o/r/rulesets --input body.json",
        "gh api graphql -f query=x",
        "gh api --paginate graphql -f query=x",
    ],
)
def test_write_forms_of_gh_api_ask(permissions, command):
    assert _matches_any(permissions["ask"], command)
