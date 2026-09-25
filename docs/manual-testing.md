# Manual testing from Claude Desktop

How to try an unmerged branch in Claude Desktop before merging it, and the checklist for the send confirmation (#21).

## 1. Point Claude Desktop at the branch

`uvx` can install directly from a branch: add `@<branch>` to the repository URL. `--refresh` makes `uvx` fetch the latest commit on the branch at every start instead of reusing its cache, so a new push is picked up by restarting Claude Desktop.

Add a **second** server entry next to your normal one, so you can switch back by disabling it:

```json
{
  "mcpServers": {
    "google-chat-test": {
      "command": "uvx",
      "args": [
        "--refresh",
        "git+https://github.com/nuccio/google-chat-mcp@claude/loving-clarke-a2tocy",
        "--space", "spaces/TEST_CONFIRM:rw",
        "--space", "spaces/TEST_AUTO:rw:unattended"
      ]
    }
  }
}
```

On Windows with WSL, put `"wsl", "--"` in front as in [claude-desktop.md](claude-desktop.md): `"command": "wsl"`, `"args": ["--", "uvx", "--refresh", "git+...@claude/loving-clarke-a2tocy", ...]`.

- Replace `TEST_CONFIRM` and `TEST_AUTO` with two spaces **used only for testing**: these tests post real messages. Find the IDs with `uvx "git+https://github.com/nuccio/google-chat-mcp" spaces`.
- The OAuth token is the one you already have (`~/.config/google-chat-mcp/token.json`): the branch does not change scopes, no new `auth` needed.
- Disable the normal `google-chat` server during the test, so Claude cannot pick the wrong one.
- Restart Claude Desktop after every change to the config file.

The server log is `~/.config/google-chat-mcp/server.log` (inside WSL on Windows). Keep it open with `tail -f ~/.config/google-chat-mcp/server.log`.

## 2. Checklist — sending messages (#21)

Before starting, in Claude Desktop set `send_message` to **require approval** and `send_message_unattended` to **allow always** (see [Tool approval settings](claude-desktop.md#tool-approval-settings)).

| # | What to do | Expected result |
|---|---|---|
| 0 | Restart Claude Desktop | `server.log` has a `WARNING` for `spaces/TEST_AUTO` (`:rw:unattended`) |
| 1 | Ask Claude: "list my google chat spaces" | In the log, `tool=list_spaces protocol=... elicitation=...`. **Write down both values** |
| 2 | Ask Claude to send "test 1" to `TEST_CONFIRM`, and approve | Claude Desktop asks approval for `send_message`. If `elicitation=True`, the server then asks its own confirmation (display name, `spaces/TEST_CONFIRM`, exact text). The message is posted |
| 3 | Ask to send "test 2" to `TEST_CONFIRM`, and deny the approval | Nothing is posted |
| 4 | Ask to send "test 3" to `TEST_AUTO` | Claude uses `send_message_unattended`, no approval or confirmation is asked, the message is posted |
| 5 | Ask Claude to send "test 4" to `TEST_AUTO` **using `send_message`** | Refused: the server answers that the space is `:unattended` and `send_message_unattended` must be used |
| 6 | Ask Claude to send "test 5" to `TEST_CONFIRM` **using `send_message_unattended`** | Refused: the space is not `:unattended`. Nothing is posted |
| 7 | Change `TEST_AUTO` to `:r:unattended`, restart | The server does not start; `server.log` has `ValueError: ... ':unattended' only applies to writes` |

Only if step 1 logged `elicitation=True`: repeat step 2 declining the **server's** confirmation (after approving the tool in the client). Nothing must be posted.

## 3. What to report

- The `protocol=` and `elicitation=` values from step 1.
- For each step: passed / failed, and for failures the Claude reply and the matching `server.log` lines.

## 4. Afterwards

Remove the `google-chat-test` entry, re-enable the normal server and restart Claude Desktop.
