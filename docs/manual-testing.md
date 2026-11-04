# Manual testing from Claude Desktop

How to try `latest` in Claude Desktop before promoting it to `stable` (step 4 of the [flow of a change](../CONTRIBUTING.md#flow-of-a-change)), and the checklist for sending messages (#21).

## 1. Point Claude Desktop at `latest`

Add `@latest` to the repository URL. `--refresh` makes `uvx` fetch the commit the tag points to at every start instead of reusing its cache, so a new merge is picked up by restarting Claude Desktop. The same setup works with `@<branch>` to look at a change before it is merged.

Add a **second** server entry next to your normal one (which stays on `@stable`), so you can switch back by disabling it:

```json
{
  "mcpServers": {
    "google-chat-test": {
      "command": "uvx",
      "args": [
        "--refresh",
        "git+https://github.com/nuccio/google-chat-mcp@latest",
        "--space", "spaces/CONFIRM_ID:rw",
        "--space", "spaces/UNATTENDED_ID:rw:unattended"
      ]
    }
  }
}
```

On Windows with WSL, put `"wsl", "--"` in front as in [claude-desktop.md](claude-desktop.md): `"command": "wsl"`, `"args": ["--", "uvx", "--refresh", "git+...@latest", ...]`.

- These tests post real messages, so they use two spaces **used only for testing**, created by the [test environment setup](../tests/README.md#test-environment): `mcp-test-read-write-confirm` and `mcp-test-read-write-unattended`. Replace `spaces/CONFIRM_ID` and `spaces/UNATTENDED_ID` with the values of `CHAT_READ_WRITE_CONFIRM_SPACE` and `CHAT_READ_WRITE_UNATTENDED_SPACE` from `.env`.
- The OAuth token is the one you already have (`~/.config/google-chat-mcp/token.json`): no new `auth` is needed unless a change adds OAuth scopes.
- Disable the normal `google-chat` server during the test, so Claude cannot pick the wrong one.
- If everything passes, promote `latest` to `stable` (see [Promoting to `stable`](../CONTRIBUTING.md#promoting-to-stable)).
- Restart Claude Desktop after every change to the config file.

The server log is `~/.config/google-chat-mcp/server.log` (inside WSL on Windows). Keep it open with `tail -f ~/.config/google-chat-mcp/server.log`.

## 2. Checklist — sending messages (#21)

Before starting, in Claude Desktop set `send_message` to **require approval** and `send_message_unattended` to **allow always** (see [Tool approval settings](claude-desktop.md#tool-approval-settings)).

| # | What to do | Expected result |
|---|---|---|
| 0 | Restart Claude Desktop | `server.log` has a `WARNING` for the ID of `mcp-test-read-write-unattended` (`:rw:unattended`) |
| 1 | Ask Claude: "list my google chat spaces" | In the log, `tool=list_spaces protocol=... elicitation=...`. **Write down both values** |
| 2 | Ask Claude to send "test 1" to `mcp-test-read-write-confirm`, and approve | Claude Desktop asks approval for `send_message`. If `elicitation=True`, the server then asks its own confirmation (display name, space ID, exact text). The message is posted |
| 3 | Ask to send "test 2" to `mcp-test-read-write-confirm`, and deny the approval | Nothing is posted |
| 4 | Ask to send "test 3" to `mcp-test-read-write-unattended` | Claude uses `send_message_unattended`, no approval or confirmation is asked, the message is posted |
| 5 | Ask Claude to send "test 4" to `mcp-test-read-write-unattended` **using `send_message`** | Refused: the server answers that the space is `:unattended` and `send_message_unattended` must be used |
| 6 | Ask Claude to send "test 5" to `mcp-test-read-write-confirm` **using `send_message_unattended`** | Refused: the space is not `:unattended`. Nothing is posted |
| 7 | Change `mcp-test-read-write-unattended` to `:r:unattended`, restart | The server does not start; `server.log` has `ValueError: ... ':unattended' only applies to writes` |

Only if step 1 logged `elicitation=True`: repeat step 2 declining the **server's** confirmation (after approving the tool in the client). Nothing must be posted.

## 3. What to report

- The `protocol=` and `elicitation=` values from step 1.
- For each step: passed / failed, and for failures the Claude reply and the matching `server.log` lines.

## 4. Afterwards

Remove the `google-chat-test` entry, re-enable the normal server and restart Claude Desktop.
