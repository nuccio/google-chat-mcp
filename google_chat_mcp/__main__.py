"""
CLI entry point for google-chat-mcp.

Usage:
    # One-time OAuth flow (opens the browser)
    google-chat-mcp auth

    # List accessible spaces (to find the resource names to use with --space)
    google-chat-mcp spaces

    # Start the MCP server over stdio (normal use with Claude Desktop)
    google-chat-mcp --space spaces/AAA:rw --space spaces/BBB:r --space spaces/CCC:w:unattended
"""

import argparse
import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .log_masking import EmailMaskingFormatter, mask_fastmcp_logging

load_dotenv()

_LOG_DIR = Path.home() / ".config" / "google-chat-mcp"
_LOG_FILE = _LOG_DIR / "server.log"


def _open_owner_only(path: str, flags: int) -> int:
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)  # also fixes files created by earlier versions
    return fd


class _OwnerOnlyRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Writes log files readable by the owner only: they hold message text."""

    def _open(self):
        return open(
            self.baseFilename,
            self.mode,
            encoding=self.encoding,
            errors=self.errors,
            opener=_open_owner_only,
        )


def _setup_logging() -> None:
    _LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(_LOG_DIR, 0o700)
    formatter = EmailMaskingFormatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = _OwnerOnlyRotatingFileHandler(
        _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    def _log_unhandled(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("google-chat-mcp").critical(
            "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _log_unhandled


def _cmd_auth() -> None:
    from .auth import run_auth_flow
    run_auth_flow()


def _cmd_spaces() -> None:
    from .auth import load_credentials
    from .chat import ChatClient
    chat = ChatClient(load_credentials())
    spaces = chat.spaces.list()
    if not spaces:
        print("No spaces found.")
        return
    col = max(len(s.get("name", "")) for s in spaces)
    for s in spaces:
        name = s.get("name", "")
        display = s.get("displayName") or s.get("singleUserBotDm", {}).get("displayName") or "(no name)"
        space_type = s.get("spaceType", s.get("type", ""))
        print(f"{name:<{col}}  {display}  [{space_type}]")


def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(
        prog="google-chat-mcp",
        description="MCP server for Google Chat",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("auth", help="Run the OAuth flow in the browser (once per user)")
    sub.add_parser(
        "spaces",
        help="List every space your account can access (to find resource names)",
    )
    parser.add_argument(
        "--space",
        action="append",
        dest="spaces",
        metavar="NAME:PERMS[:unattended]",
        default=[],
        help=(
            "Space to allow, with its permissions (r, w or rw) and the optional "
            ":unattended marker for sends from scheduled tasks. "
            "Repeat for each space. "
            "E.g. --space spaces/AAA:rw --space spaces/BBB:r --space spaces/CCC:w:unattended"
        ),
    )

    args = parser.parse_args()

    if args.command == "auth":
        _cmd_auth()
        return

    if args.command == "spaces":
        _cmd_spaces()
        return

    from . import server
    mask_fastmcp_logging()
    server.init(args.spaces)
    server.mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
