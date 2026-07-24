"""
Entry point CLI per google-chat-mcp.

Utilizzo:
    # Flusso OAuth una-tantum (apre il browser)
    google-chat-mcp auth

    # Lista spazi accessibili (per trovare i resource name da usare con --space)
    google-chat-mcp spaces

    # Avvia il server MCP in stdio (uso normale con Claude Desktop)
    google-chat-mcp --space spaces/AAA:rw --space spaces/BBB:r
"""

import argparse
import json
import logging
import logging.handlers
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_LOG_DIR = Path.home() / ".config" / "google-chat-mcp"
_LOG_FILE = _LOG_DIR / "server.log"


def _setup_logging() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.handlers.RotatingFileHandler(
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
            "Eccezione non gestita", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _log_unhandled


def _cmd_auth() -> None:
    from .auth import run_auth_flow
    run_auth_flow()


def _cmd_spaces() -> None:
    from .chat import ChatClient
    chat = ChatClient()
    spaces = chat.spaces.list()
    print(json.dumps(spaces, indent=2, ensure_ascii=False))


def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(
        prog="google-chat-mcp",
        description="MCP server per Google Chat",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("auth", help="Esegui il flusso OAuth (una-tantum per utente)")
    sub.add_parser(
        "spaces",
        help="Elenca tutti gli spazi accessibili (per trovare i resource name)",
    )
    parser.add_argument(
        "--space",
        action="append",
        dest="spaces",
        metavar="NAME:PERMS",
        default=[],
        help=(
            "Spazio permesso con relativi diritti. "
            "Ripeti per più spazi. "
            "Es: --space spaces/AAA:rw --space spaces/BBB:r --space spaces/CCC:w"
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
    server.init(args.spaces)
    server.mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
