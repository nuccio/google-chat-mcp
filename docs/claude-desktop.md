# Claude Desktop configuration

How to add the server to Claude Desktop. For the meaning of the `--space` values, the send confirmation and everything else that can be configured, see [configuration.md](configuration.md).

## Locate the config file

| Platform | Path |
|----------|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Open the file in any text editor (create it if it does not exist yet).

## macOS / Linux

```json
{
  "mcpServers": {
    "google-chat": {
      "command": "uvx",
      "args": [
        "git+https://github.com/nuccio/google-chat-mcp",
        "--space", "spaces/AAABBBCCC:rw",
        "--space", "spaces/DDDEEEFFF:r",
        "--space", "spaces/GGGHHH111:w:unattended"
      ]
    }
  }
}
```

## Windows (with WSL)

The Claude Desktop config file lives on the **Windows** filesystem, but the server runs inside WSL. Use `wsl` as the command:

```json
{
  "mcpServers": {
    "google-chat": {
      "command": "wsl",
      "args": [
        "--",
        "uvx",
        "git+https://github.com/nuccio/google-chat-mcp",
        "--space", "spaces/AAABBBCCC:rw",
        "--space", "spaces/DDDEEEFFF:r"
      ]
    }
  }
}
```

The server uses the OAuth token in `~/.config/google-chat-mcp/token.json` (inside WSL on Windows), written by the `auth` command during setup. No `env` block is needed: `GOOGLE_CLIENT_SECRETS` is only read by `auth`.

## `--space` values at a glance

| Value | Meaning |
|---|---|
| `spaces/ID:r` | read only |
| `spaces/ID:w` | write only, each message confirmed by you |
| `spaces/ID:rw` | read and write, each message confirmed by you |
| `spaces/ID:w:unattended` | write only, no confirmation (for scheduled tasks) |

Repeat `--space` for each space. Spaces not listed are completely inaccessible. Before using `:unattended`, read [Security considerations](configuration.md#security-considerations).

After saving the file, **restart Claude Desktop** for the changes to take effect.

## Checking that it works

`~/.config/google-chat-mcp/server.log` records every tool call with the MCP protocol version Claude Desktop negotiated and whether it supports the send confirmation (`elicitation=True|False`). See [Client requirements and MCP protocol versions](configuration.md#client-requirements-and-mcp-protocol-versions).

To try an unmerged branch, see [manual-testing.md](manual-testing.md).
