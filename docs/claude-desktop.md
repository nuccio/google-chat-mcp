# Claude Desktop configuration

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
        "--space", "spaces/GGGHHH111:w"
      ],
      "env": {
        "GOOGLE_CLIENT_SECRETS": "/Users/yourname/.config/google-chat-mcp/client_secrets.json"
      }
    }
  }
}
```

> If you placed `client_secrets.json` at the default location (`~/.config/google-chat-mcp/client_secrets.json`), the `env` block is optional.

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
      ],
      "env": {
        "GOOGLE_CLIENT_SECRETS": "/home/youruser/.config/google-chat-mcp/client_secrets.json"
      }
    }
  }
}
```

The `GOOGLE_CLIENT_SECRETS` path here is the WSL path (Linux-style) to the file you placed inside WSL during setup.

## Permission flags

- `spaces/ID:r` — read only
- `spaces/ID:w` — write only
- `spaces/ID:rw` — read and write

Repeat `--space` for each space you want to make accessible. Spaces not listed are completely inaccessible.

After saving the file, **restart Claude Desktop** for the changes to take effect.
