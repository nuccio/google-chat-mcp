"""
NFR-7 on #29: what may be merged into `main` while remote mode is built.

A merge may bring a partial implementation of the remote connector or of the
settings web app; it may never bring a local MCP server that does not work.
Each test below names the criterion of NFR-7 it enforces.

CI runs this file twice: with the `dev` extra (as every other test), and in a
job that installs the package as a local user does, without any extra
(`GCM_LOCAL_ONLY_INSTALL=1`). Criteria that cannot be automated (trying a
core change in a real client before moving `stable`, describing what a
partial merge does not do yet) are review rules, listed in NFR-7.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from tests.test_server import EXPECTED_TOOLS

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "google_chat_mcp"

# Packages that belong to remote mode only. Local mode must not import them.
REMOTE_PACKAGES = ("google_chat_mcp.settings", "google_chat_mcp.remote", "nicegui", "google.cloud")

# Local mode: every module of the package outside the remote-only packages.
LOCAL_MODULES = sorted(
    p for p in PKG.rglob("*.py")
    if p.relative_to(PKG).parts[0] not in ("settings", "remote")
)

LOCAL_ONLY_INSTALL = os.environ.get("GCM_LOCAL_ONLY_INSTALL") == "1"

# The local CLI as documented for local users. Changing it is a change for
# local users: update this list on purpose, and the local docs with it (#58).
LOCAL_SUBCOMMANDS = ["auth", "spaces"]


def _local_env(tmp_path: Path) -> dict[str, str]:
    """A clean HOME: no token, logs in a temporary folder."""
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["PYTHONPATH"] = str(ROOT)
    return env


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(path: Path) -> set[str]:
    """Absolute names of the modules a file imports, relative imports resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_name(path) if path.name == "__init__.py" else _module_name(path).rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            found.add(prefix)
            found.update(f"{prefix}.{alias.name}" for alias in node.names)
    return found


def _is_remote(module: str) -> bool:
    return any(module == p or module.startswith(p + ".") for p in REMOTE_PACKAGES)


# --- Criterion 1: local mode works as before ---------------------------------


def _stdio_commands() -> list[tuple[str, list[str]]]:
    commands = [("module", [sys.executable, "-m", "google_chat_mcp"])]
    script = shutil.which("google-chat-mcp")
    if script:
        commands.append(("installed script", [script]))
    return commands


@pytest.mark.anyio
@pytest.mark.parametrize("label, command", _stdio_commands(), ids=lambda v: v if isinstance(v, str) else "")
async def test_local_server_starts_over_stdio_and_lists_its_tools(tmp_path, label, command):
    """NFR-7, criterion 1: the local server starts over stdio, as Claude Desktop starts it."""
    transport = StdioTransport(
        command=command[0],
        args=[*command[1:], "--space", "spaces/AAA:rw", "--space", "spaces/BBB:r"],
        env=_local_env(tmp_path),
        cwd=str(tmp_path),
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS
        result = await client.call_tool("list_spaces", {})
    text = str(result.structured_content or result.content)
    assert "spaces/AAA" in text and "spaces/BBB" in text


# --- Criterion 2: nothing local depends on the partial work --------------------


def test_local_cli_is_unchanged(tmp_path):
    """NFR-7, criterion 2: the local CLI shows only the documented subcommands."""
    out = subprocess.run(
        [sys.executable, "-m", "google_chat_mcp", "--help"],
        capture_output=True, text=True, env=_local_env(tmp_path), cwd=tmp_path, check=True,
    ).stdout
    choices = re.search(r"\{([a-z0-9_,-]+)\}", out)
    assert choices is not None, out
    assert choices.group(1).split(",") == LOCAL_SUBCOMMANDS
    assert "--space" in out


@pytest.mark.parametrize("path", LOCAL_MODULES, ids=lambda p: str(p.relative_to(ROOT)))
def test_local_modules_do_not_import_remote_code(path):
    """NFR-7, criteria 2 and 3: local modules import nothing that belongs to remote mode."""
    remote = sorted(m for m in _imported_modules(path) if _is_remote(m))
    assert not remote, f"{path.relative_to(ROOT)} imports remote-only modules: {remote}"


def test_starting_local_mode_loads_no_remote_module(tmp_path):
    """NFR-7, criterion 2: what the stdio entry point loads at run time, not only what it imports."""
    code = (
        "import sys\n"
        "import google_chat_mcp.__main__, google_chat_mcp.server\n"
        "google_chat_mcp.server.init(['spaces/AAA:rw'])\n"
        f"prefixes = {REMOTE_PACKAGES!r}\n"
        "print('\\n'.join(sorted(m for m in sys.modules "
        "if any(m == p or m.startswith(p + '.') for p in prefixes))))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=_local_env(tmp_path), cwd=tmp_path, check=True,
    ).stdout.strip()
    assert out == "", f"Local mode loaded remote-only modules:\n{out}"


@pytest.mark.skipif(not LOCAL_ONLY_INSTALL, reason="only in the CI job that installs without extras")
def test_local_install_has_no_remote_dependency():
    """NFR-7, criterion 2: this job really runs without the `remote` extra."""
    assert importlib.util.find_spec("nicegui") is None


# --- Criterion 3: partial remote work is isolated and fails clearly -----------------


@pytest.mark.skipif(not LOCAL_ONLY_INSTALL, reason="only in the CI job that installs without extras")
def test_remote_entry_point_without_the_extra_fails_clearly(tmp_path):
    """NFR-7, criterion 3: a remote entry point without its extra says what is missing."""
    result = subprocess.run(
        [sys.executable, "-m", "google_chat_mcp.settings.mock", "--help"],
        capture_output=True, text=True, env=_local_env(tmp_path), cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "needs the 'remote' extra" in result.stderr
    assert "Traceback" not in result.stderr


# --- Criterion 4: development entry points are never part of a deployable app --------


def test_settings_app_does_not_import_the_mock():
    """NFR-7, criterion 4: the fake sign-in exists only in the development mock."""
    for path in (PKG / "settings").glob("*.py"):
        if path.name == "mock.py":
            continue
        imported = _imported_modules(path)
        assert not any(m.startswith("google_chat_mcp.settings.mock") for m in imported), path.name


def test_mock_declares_itself_as_development_only():
    """NFR-7, criterion 4: the mock says what it is wherever it can be seen."""
    source = (PKG / "settings" / "mock.py").read_text(encoding="utf-8")
    assert source.lstrip('"\n').startswith("DEVELOPMENT ONLY")
    assert "fake sign-in" in source
