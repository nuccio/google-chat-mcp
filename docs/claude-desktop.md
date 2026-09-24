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
- `spaces/ID:w:unattended` / `spaces/ID:rw:unattended` — as above, but messages are sent without asking for confirmation (see below)

Repeat `--space` for each space you want to make accessible. Spaces not listed are completely inaccessible.

After saving the file, **restart Claude Desktop** for the changes to take effect.

### `r` and `w` are independent

`w` does not imply `r`. A space configured `:w` can be posted to, but its messages and member list cannot be read through the server. Use it for spaces that only receive output (e.g. a scheduled task announcing it finished): content the agent never reads cannot steer what it writes.

`r` protects the message content and the member list of a space. It does not protect other space metadata: the server reads it internally to enforce its rules regardless of `r` (the space type, to block DMs; the display name, shown in the confirmation prompt).

## Send confirmation and `:unattended`

By default, before `send_message` posts anything, the server asks you to confirm the exact text and the target space (display name and resource name). This is an MCP *elicitation*: it is independent of the client's own tool-approval prompt, so it still applies if you set that prompt to "allow always".

- If you decline or cancel, nothing is sent.
- If the MCP client does not support elicitation, sending to a space that requires confirmation fails with an explicit error instead of posting without it.

Scheduled or automated tasks cannot answer a confirmation prompt. For spaces they must post to, add the `:unattended` marker:

```json
"--space", "spaces/AAABBBCCC:w",
"--space", "spaces/REPORTS01:w:unattended"
```

Rules for `:unattended`:

- It only affects writes. `:unattended` without `w` (e.g. `spaces/X:r:unattended`) is rejected at startup.
- It applies to **every caller**, including interactive chats. The server cannot tell a scheduled task from a chat: both come from the same client. A space marked `:unattended` never asks for confirmation.
- `:rw:unattended` is allowed, but the server logs a warning at startup: content read from that space can influence posts made without confirmation.

### Blast radius

Text read from **any** space with `r` can steer posts to **every** `:unattended` space in the same configuration, without confirmation — not only to the space it was read from (prompt injection). Splitting the configuration into two servers (one for chat, one for tasks) does not help, because both are available in both contexts. What works:

1. Mark `:unattended` only spaces dedicated to automated output, where an unwanted post has low impact.
2. Prefer `w:unattended` without `r` whenever the task does not need to read that space.
3. Content rules on outbound messages ([#22](https://github.com/nuccio/google-chat-mcp/issues/22)), which apply to every call regardless of the caller.
