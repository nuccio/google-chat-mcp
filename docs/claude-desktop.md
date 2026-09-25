# Claude Desktop configuration

How to add the server to Claude Desktop. For the meaning of the `--space` values, the send confirmation and everything else that can be configured, see [configuration.md](configuration.md).

## Before you start: authenticate from a terminal

Claude Desktop cannot sign you in to Google: the server only reads the token that the `auth` command writes. Do this once, **before** editing the Claude Desktop configuration:

1. Check that everything `uvx` needs is in place:
   - `uv` is installed (`uvx --version` works in a terminal; on Windows, inside WSL);
   - `client_secrets.json` is at `~/.config/google-chat-mcp/client_secrets.json`, or `GOOGLE_CLIENT_SECRETS` is set to its path in the same terminal (see [OAuth credentials on Google Cloud Console](../README.md#1-oauth-credentials-on-google-cloud-console)).
2. In a terminal (on Windows, the WSL terminal), run:
   ```bash
   uvx "git+https://github.com/nuccio/google-chat-mcp" auth
   ```
   A browser window opens: sign in with your Google account and grant access.
3. Check that `~/.config/google-chat-mcp/token.json` now exists. Only then continue with the steps below.

If a tool later fails with an authentication error (missing token, `invalid_grant`), run the same command again from the terminal and restart Claude Desktop.

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

To use the tested version instead of the latest one, replace the URL with `"--refresh", "git+https://github.com/nuccio/google-chat-mcp@stable"` (see [Versions](../README.md#versions)).

The server uses the OAuth token in `~/.config/google-chat-mcp/token.json` (inside WSL on Windows), written by the `auth` command during setup. No `env` block is needed: `GOOGLE_CLIENT_SECRETS` is only read by `auth`.

## `--space` values at a glance

| Value | Meaning |
|---|---|
| `spaces/ID:r` | read only |
| `spaces/ID:w` | write only, with `send_message` (approved by you, see below) |
| `spaces/ID:rw` | read and write, with `send_message` (approved by you, see below) |
| `spaces/ID:w:unattended` | write only, with `send_message_unattended`, no confirmation (for scheduled tasks) |

Repeat `--space` for each space. Spaces not listed are completely inaccessible. Before using `:unattended`, read [Security considerations](configuration.md#security-considerations).

After saving the file, **restart Claude Desktop** for the changes to take effect.

## Tool approval settings

Claude Desktop does not support the server's own send confirmation (MCP elicitation): in tests it declared `elicitation=False`. The only check before a message is posted is Claude Desktop's **per-tool approval**, which you set in the connector's settings (each tool can be allowed always, require approval, or be blocked; the exact labels depend on the Claude Desktop version). Set:

| Tool | Setting | Why |
|---|---|---|
| `send_message` | require approval | you see and approve each message to a regular space |
| `send_message_unattended` | allow always | scheduled tasks can post to `:unattended` spaces with nobody present |
| read tools (`list_spaces`, `get_space`, `list_messages`, `list_members`, `chat_auth_status`) | your choice | they do not change anything |

If you set `send_message` to "allow always", messages to regular spaces are posted with no confirmation at all: the server cannot detect it. See [Sending messages](configuration.md#sending-messages).

## Checking that it works

`~/.config/google-chat-mcp/server.log` records every tool call with the MCP protocol version Claude Desktop negotiated and whether it supports the server's send confirmation (`elicitation=True|False`), and each `send_message` posted without it. See [Sending messages](configuration.md#sending-messages).

To try an unmerged branch, see [manual-testing.md](manual-testing.md).
