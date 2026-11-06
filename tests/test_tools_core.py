"""tools.py is the shared core: it must not know which mode it runs in (07, "No tool checks the mode")."""

import ast
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "google_chat_mcp" / "tools.py"
PACKAGE = "google_chat_mcp"

# Modules of the package that tools.py may import. Everything else of the package
# (auth, config, server, remote, settings, ...) belongs to a mode.
CORE = {"chat", "interfaces", "log_masking", "permissions", "schema"}


def _tree() -> ast.Module:
    return ast.parse(TOOLS.read_text(encoding="utf-8"), filename=str(TOOLS))


def _imports(tree: ast.Module) -> list[tuple[str, str]]:
    """(kind, name) for each import: 'pkg' for the package's own modules, 'ext' for the rest."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                found.append(("pkg" if top == PACKAGE else "ext", alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # `from .x import y` -> module x; `from . import x` -> module x
                module = (node.module or "").split(".")[0] or None
                names = [module] if module else [a.name for a in node.names]
                found.extend(("pkg", n) for n in names)
            else:
                top = (node.module or "").split(".")[0]
                if top == PACKAGE:
                    parts = (node.module or "").split(".")
                    names = [parts[1]] if len(parts) > 1 else [a.name for a in node.names]
                    found.extend(("pkg", n) for n in names)
                else:
                    found.append(("ext", node.module or ""))
    return found


def test_tools_import_only_the_core_modules():
    own = {name for kind, name in _imports(_tree()) if kind == "pkg"}
    assert own <= CORE, f"tools.py imports modules that belong to a mode: {sorted(own - CORE)}"


def test_tools_import_only_standard_and_declared_third_party_libraries():
    stdlib = set(sys.stdlib_module_names)
    allowed_third_party = {"fastmcp", "mcp_types"}
    external = {name.split(".")[0] for kind, name in _imports(_tree()) if kind == "ext"}
    unexpected = {m for m in external if m not in stdlib | allowed_third_party | {"__future__"}}
    assert not unexpected, f"tools.py imports unexpected libraries: {sorted(unexpected)}"


def test_tools_read_neither_environment_nor_argv():
    tree = _tree()
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id, node.attr) in {("os", "environ"), ("os", "getenv"), ("sys", "argv")}:
                offenders.append(f"{node.value.id}.{node.attr} (line {node.lineno})")
    assert not offenders, offenders
    names = {kind_name for _, kind_name in _imports(tree)}
    assert not {"os", "sys", "argparse"} & {n.split(".")[0] for n in names}, "tools.py has no reason to import os, sys or argparse"
