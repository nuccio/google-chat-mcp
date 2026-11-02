"""
The setup procedure: finds or creates the spaces, updates .env, posts the seeds.

Safe to run again: it reuses what it finds. It never deletes or renames spaces
and never changes members. Everything that can stop it because of what it finds
is checked before the first create or post, so a conflict leaves nothing half
done.

Output never includes the text of a ChatAPIError: it holds the body of the API
response, which can include email addresses.
"""

import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, set_key
from google.auth.transport.requests import AuthorizedSession

from google_chat_mcp.chat.client import ChatAPIError, _BaseSession
from google_chat_mcp.chat.messages import MessagesClient
from google_chat_mcp.chat.spaces import SpacesClient

from .auth import load_credentials

_TESTS_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = _TESTS_DIR / "test_env.toml"
SEED_PATH = _TESTS_DIR / "fixtures" / "read_only_seed_messages.json"
ENV_PATH = _TESTS_DIR.parent / ".env"

# The space nothing ever writes to, so its content stays predictable.
_SEED_SPACE = "read_only"

_DM_INSTRUCTIONS = """\
{env_var} is not set: the direct message tests will be skipped.
To enable them, follow "Direct message" in tests/README.md. In short: from the
account that runs the tests, open a direct message with an account DEDICATED TO
TESTING, never someone's main account or a conversation with a real person, and
send one message. Then put its space ID in {env_var} in .env. This setup can
only check that the ID is a direct message, not who it is with."""


class SetupError(Exception):
    pass


@dataclass(frozen=True)
class SpaceSpec:
    key: str
    display_name: str
    env_var: str


@dataclass(frozen=True)
class EnvConfig:
    direct_message_env_var: str
    spaces: list[SpaceSpec]


class SetupClient:
    """The package's read and send code, plus the one call only the setup makes."""

    def __init__(self, session: AuthorizedSession):
        self._base = _BaseSession(session)
        self.spaces = SpacesClient(self._base)
        self.messages = MessagesClient(self._base)

    def create_space(self, display_name: str) -> dict:
        return self._base._post("spaces", {"spaceType": "SPACE", "displayName": display_name})


def load_config(path: Path = CONFIG_PATH) -> EnvConfig:
    data = tomllib.loads(path.read_text())
    return EnvConfig(
        direct_message_env_var=data["direct_message"]["env_var"],
        spaces=[
            SpaceSpec(key, spec["display_name"], spec["env_var"])
            for key, spec in data["spaces"].items()
        ],
    )


def _check_direct_message(client, space_name: str, env_var: str) -> None:
    try:
        space_type = client.spaces.get(space_name).get("spaceType")
    except ChatAPIError as e:
        raise SetupError(
            f"Could not read {env_var} ({space_name}): Google Chat API {e.status_code}."
        ) from None
    if space_type != "DIRECT_MESSAGE":
        raise SetupError(
            f"{env_var} ({space_name}) has spaceType {space_type!r}, "
            "expected 'DIRECT_MESSAGE'."
        )


def _find_spaces(client, specs: list[SpaceSpec]) -> dict[str, str]:
    """Returns {display name: space ID} for the specs that exist; duplicates stop the setup."""
    # The API has no filter on the name.
    names_by_display_name: dict[str, list[str]] = {}
    for space in client.spaces.list():
        if space.get("spaceType") == "SPACE":
            names_by_display_name.setdefault(space.get("displayName"), []).append(space["name"])
    found = {}
    for spec in specs:
        names = names_by_display_name.get(spec.display_name, [])
        if len(names) > 1:
            raise SetupError(
                f"{len(names)} spaces are named {spec.display_name!r} ({', '.join(names)}): "
                "not choosing one. Rename or delete the extra ones by hand."
            )
        if names:
            found[spec.display_name] = names[0]
    return found


def _check_env_conflicts(specs: list[SpaceSpec], found: dict[str, str], env: dict) -> None:
    """Refuses to overwrite a variable that points elsewhere, including when
    the space is about to be created: its new ID cannot match either."""
    conflicts = []
    for spec in specs:
        current = env.get(spec.env_var)
        expected = found.get(spec.display_name)
        if current and current != expected:
            target = expected or "a space that does not exist yet"
            conflicts.append(
                f"  {spec.env_var} is {current}, but {spec.display_name!r} is {target}"
            )
    if conflicts:
        raise SetupError(
            ".env points to different spaces. Nothing was changed. Fix or remove these "
            "variables (see the migration notes in tests/README.md):\n" + "\n".join(conflicts)
        )


def _create_space(client, display_name: str) -> str:
    try:
        return client.create_space(display_name)["name"]
    except ChatAPIError as e:
        if e.status_code == 409:  # ALREADY_EXISTS
            raise SetupError(
                f"Could not create {display_name!r}: the name is taken (ALREADY_EXISTS). "
                "spaces.list shows only this account's own spaces, so it may belong to "
                "a space this account cannot see."
            ) from None
        raise SetupError(
            f"Could not create {display_name!r}: Google Chat API {e.status_code}. If this "
            "account cannot create spaces through the API, create the space by hand with "
            "exactly this name and run the setup again: it will find it."
        ) from None


def _post_missing_seeds(client, space_name: str, seeds: list[str]) -> None:
    present = {m.get("text") for m in client.messages.list(space_name, page_size=1000)}
    missing = [text for text in seeds if text not in present]
    for text in missing:
        client.messages.send(space_name, text)
    print(f"Seed messages: {len(missing)} posted, {len(seeds) - len(missing)} already there.")


def run(
    client,
    env_path: Path = ENV_PATH,
    config_path: Path = CONFIG_PATH,
    seed_path: Path = SEED_PATH,
) -> None:
    config = load_config(config_path)
    seeds = json.loads(seed_path.read_text())
    env = dotenv_values(env_path, interpolate=False)

    direct_message = env.get(config.direct_message_env_var)
    if direct_message:
        _check_direct_message(client, direct_message, config.direct_message_env_var)
    found = _find_spaces(client, config.spaces)
    _check_env_conflicts(config.spaces, found, env)

    # From here on the setup changes things.
    for spec in config.spaces:
        space_name = found.get(spec.display_name)
        if space_name:
            print(f"Found   {spec.display_name}: {space_name}")
        else:
            space_name = _create_space(client, spec.display_name)
            print(f"Created {spec.display_name}: {space_name}")
        if env.get(spec.env_var) != space_name:
            set_key(env_path, spec.env_var, space_name, quote_mode="never")
        if spec.key == _SEED_SPACE:
            seed_space = space_name

    _post_missing_seeds(client, seed_space, seeds)

    if direct_message:
        print(f"Found   direct message: {direct_message}")
    else:
        print(_DM_INSTRUCTIONS.format(env_var=config.direct_message_env_var))


def main() -> int:
    try:
        run(SetupClient(AuthorizedSession(load_credentials())))
    except (SetupError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ChatAPIError as e:
        print(f"Error: Google Chat API {e.status_code}.", file=sys.stderr)
        return 1
    return 0
