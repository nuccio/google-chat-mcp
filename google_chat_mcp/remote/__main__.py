"""Entry point of `gcm-mcp`, the remote MCP server. Not implemented yet.

Exists from step 3 of docs/remote/07 so CI can check that the command stops
with a clear message (NFR-7). The HTTP entry point arrives in step 5.
"""

import sys


def main() -> int:
    print(
        "google_chat_mcp.remote: not implemented yet (see docs/remote/07-mcp-server-design.md).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
