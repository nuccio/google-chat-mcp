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

## 2. Checklist — send confirmation (#21)

| # | What to do | Expected result |
|---|---|---|
| 0 | Restart Claude Desktop | `server.log` has a `WARNING` for `spaces/TEST_AUTO` (`:rw:unattended`) |
| 1 | Ask Claude: "list my google chat spaces" | In the log, `tool=list_spaces protocol=... elicitation=...`. **Write down both values**: they tell which protocol Claude Desktop uses and whether it supports confirmation |
| 2 | Ask Claude to send "test 1" to `TEST_CONFIRM` and **confirm** | A confirmation prompt shows the space display name, `spaces/TEST_CONFIRM` and the exact text. The message is posted after you confirm |
| 3 | Ask to send "test 2" to `TEST_CONFIRM` and **decline** | Nothing is posted. Claude reports the send was not confirmed and does not retry on its own |
| 4 | Ask to send "test 3" to `TEST_CONFIRM` and **close/cancel** the prompt | Nothing is posted |
| 5 | If the prompt shows a checkbox: leave it **unchecked** and submit | Nothing is posted |
| 6 | Ask to send "test 4" to `TEST_AUTO` | No confirmation prompt; the message is posted |
| 7 | In Claude Desktop set the `send_message` tool to "always allow", then repeat test 2 | The server's confirmation prompt still appears (it does not depend on the client's tool approval) |
| 8 | Change `TEST_AUTO` to `:r:unattended`, restart | The server does not start; `server.log` has `ValueError: ... ':unattended' only applies to writes` |

If step 1 logs `elicitation=False`, Claude Desktop does not support the confirmation: test 2 must fail with "this MCP client does not support elicitation" and nothing must be posted; test 6 must still work. In that case steps 3–5 and 7 cannot be run.

## 3. What to report

- The `protocol=` and `elicitation=` values from step 1.
- For each step: passed / failed, and for failures the Claude reply and the matching `server.log` lines.
- A screenshot of the confirmation prompt (step 2), to check that it is readable.

## 4. Afterwards

Remove the `google-chat-test` entry, re-enable the normal server and restart Claude Desktop.
