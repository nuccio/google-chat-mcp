# Configuration reference

What this connector does differently from a plain Google Chat integration, and everything that can be configured. This page applies to any MCP client; for where to put the configuration in Claude Desktop, see [claude-desktop.md](claude-desktop.md).

## Key features

- **Per-space allowlist.** Only spaces listed with `--space` are reachable. Any other space is refused, even if your Google account can access it.
- **Read and write are independent.** Each space gets `r`, `w` or both; `w` does not imply `r`.
- **No direct messages.** Both send tools refuse direct messages and group chats (`DIRECT_MESSAGE`, `GROUP_CHAT`), even if they are in the allowlist.
- **Separate tools for interactive and automated sends.** `send_message` posts only to spaces without `:unattended` and is meant to require approval in the client; `send_message_unattended` posts only to `:unattended` spaces, for scheduled tasks. When the client supports it, `send_message` also asks you to confirm the exact text and target space.
- **Your own identity.** Each user authenticates with their own Google account via OAuth: messages come from the real person, not from a shared bot.
- **Local only.** The server runs on your machine over stdio, started by the MCP client. It opens no network port.

## What can be configured

### `--space NAME:PERMS[:unattended]`

The only runtime setting. Repeat it once per space.

| Value | Read | Write with | Confirmation before sending |
|---|---|---|---|
| `spaces/ID:r` | yes | — | — |
| `spaces/ID:w` | no | `send_message` | client approval, plus server confirmation if the client supports elicitation |
| `spaces/ID:rw` | yes | `send_message` | same as above |
| `spaces/ID:w:unattended` | no | `send_message_unattended` | none |
| `spaces/ID:rw:unattended` | yes | `send_message_unattended` | none (warning logged at startup) |

Rejected at startup, with an error that names the argument:

- `spaces/ID:r:unattended` — `:unattended` only applies to writes;
- any marker other than exactly `unattended` (e.g. `UNATTENDED`, empty);
- missing permissions (`spaces/ID`), unknown permissions (`spaces/ID:x`), more than three fields.

`wr` is accepted as a synonym of `rw`. Spaces not listed are completely inaccessible. To find space IDs, see [Finding space IDs](../README.md#finding-space-ids).

### Environment variables

| Variable | Used by | Default |
|---|---|---|
| `GOOGLE_CLIENT_SECRETS` | the `auth` command only | `~/.config/google-chat-mcp/client_secrets.json` |

The server itself never reads `client_secrets.json`: at runtime it only uses `token.json`, so the variable is not needed in the MCP client configuration.

### Files

All in `~/.config/google-chat-mcp/` (inside WSL on Windows). The directory is fixed, not configurable.

| File | Content |
|---|---|
| `client_secrets.json` | OAuth client downloaded from Google Cloud Console (default location) |
| `token.json` | OAuth token written by `auth`, refreshed automatically |
| `server.log` | Server log: startup warnings, every tool call with its arguments, negotiated MCP protocol version and elicitation support. Rotated at 10 MB, 5 files kept |

### Commands

| Command | Purpose |
|---|---|
| `google-chat-mcp auth` | One-time OAuth flow in the browser; writes `token.json`. Run it yourself in a terminal, before configuring the MCP client — it is not a tool the model calls |
| `google-chat-mcp spaces` | Lists every space your account can access, to find IDs for `--space` |
| `google-chat-mcp --space ...` | Starts the MCP server over stdio (what the MCP client runs) |

With `uvx`, prefix them with `uvx "git+https://github.com/nuccio/google-chat-mcp"`.

If the OAuth consent screen is in **Testing** mode, Google invalidates the refresh token after 7 days and `auth` must be run again. The `chat_auth_status` tool reports the days since consent and warns from day 6.

## Read and write permissions

`w` does not imply `r`. A space configured `:w` can be posted to, but its messages and member list cannot be read through the server. Use it for spaces that only receive output (e.g. a scheduled task announcing it finished): content the agent never reads cannot steer what it writes.

`r` protects the message content and the member list of a space. It does not protect other space metadata: the server reads it internally to enforce its rules regardless of `r` (the space type, to block DMs; the display name, shown in the confirmation prompt).

## Sending messages

### Two send tools

| Tool | Accepts | Intended client approval setting |
|---|---|---|
| `send_message` | spaces **without** `:unattended` | "Needs approval" |
| `send_message_unattended` | spaces **with** `:unattended` only | "Always allow" |

The server refuses a call made with the wrong tool for a space, so `send_message_unattended` cannot be used to skip the approval of `send_message`.

The split exists because MCP clients such as Claude Desktop only offer per-**tool** approval settings, not per-space ones: with one tool per kind of space, you can require approval for interactive sends and still let scheduled tasks post unattended. Tools also carry the standard MCP annotations (read tools are marked read-only, the send tools as non-destructive writes), which clients can use to choose approval defaults.

### Confirmation on `send_message`

What happens depends on whether the MCP client declares the `elicitation` capability, which the client decides when it connects:

- **The client supports elicitation**: before posting, the server asks you to confirm the exact text and the target space (display name and resource name). This comes on top of the client's own approval, and still applies if that approval is set to "Always allow". Anything other than an explicit yes (decline, cancel, closed prompt, unchecked box, error) means nothing is sent.
- **The client does not support elicitation**: the server posts without its own confirmation and relies on the client's approval of `send_message`. Each such send is recorded in `server.log`. The server cannot see the client's approval setting: if `send_message` is set to "Always allow", messages are posted with no confirmation at all.

In tests with Claude Desktop (September 2026), the client declared **no** elicitation support (`protocol=2025-11-25 elicitation=False`): there, the client's approval of `send_message` is the only per-message check.

#### MCP protocol versions

When the client supports elicitation, the confirmation works differently in the two generations of the protocol, both supported by the server:

- **Up to `2025-11-25`**: while `send_message` is running, the server sends the confirmation request to the client, waits for the answer, then posts or refuses.
- **`2026-07-28`**: the protocol no longer lets the server send requests to the client during a tool call. The first `send_message` call ends without posting and returns an "input required" result containing the confirmation request; the client shows it and repeats the call with your answer. The answer is valid only for the same space and the same text: if the repeated call carries different text, it is refused and nothing is sent.

#### Resulting behaviour

| Client | `send_message` | `send_message_unattended` |
|---|---|---|
| Supports elicitation, any protocol version | Client approval, then server confirmation; posts only after an explicit yes | Posts, no server prompt |
| Does not support elicitation (e.g. Claude Desktop) | Client approval only; posts once the client lets the call through | Posts, no server prompt |
| `2026-07-28`, declares elicitation but does not handle "input required" results | The call fails, nothing sent | Posts, no server prompt |

To see what your client actually uses, check `server.log`: every tool call is logged with the negotiated protocol version and whether the client supports elicitation, e.g. `tool=list_spaces protocol=2025-11-25 elicitation=True`.

### `:unattended`

Scheduled or automated tasks cannot answer a confirmation prompt. For spaces they must post to, add the `:unattended` marker and post with `send_message_unattended`:

```
--space spaces/AAABBBCCC:w
--space spaces/REPORTS01:w:unattended
```

- It only affects writes. `:unattended` without `w` is rejected at startup.
- It applies to **every caller**, including interactive chats. The server cannot tell a scheduled task from a chat: both come from the same client. A space marked `:unattended` never asks for confirmation, and `send_message_unattended` is normally set to "Always allow" in the client.
- `:rw:unattended` is allowed, but the server logs a warning at startup: content read from that space can influence posts made without confirmation.

## Security considerations

Text read from **any** space with `r` can steer posts to **every** `:unattended` space in the same configuration, without confirmation — not only to the space it was read from (prompt injection). Splitting the configuration into two servers (one for chat, one for tasks) does not help, because both are available in both contexts. What works:

1. Mark `:unattended` only spaces dedicated to automated output, where an unwanted post has low impact.
2. Prefer `w:unattended` without `r` whenever the task does not need to read that space.
3. Content rules on outbound messages ([#22](https://github.com/nuccio/google-chat-mcp/issues/22)), which apply to every call regardless of the caller.

On clients without elicitation, the protection of spaces without `:unattended` also depends on a client setting: the approval of `send_message`. Keep it on "Needs approval"; content rules (#22) are the only check that does not depend on the client at all.
