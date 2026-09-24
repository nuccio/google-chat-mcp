# Configuration reference

What this connector does differently from a plain Google Chat integration, and everything that can be configured. This page applies to any MCP client; for where to put the configuration in Claude Desktop, see [claude-desktop.md](claude-desktop.md).

## Key features

- **Per-space allowlist.** Only spaces listed with `--space` are reachable. Any other space is refused, even if your Google account can access it.
- **Read and write are independent.** Each space gets `r`, `w` or both; `w` does not imply `r`.
- **No direct messages.** `send_message` refuses direct messages and group chats (`DIRECT_MESSAGE`, `GROUP_CHAT`), even if they are in the allowlist.
- **Confirmation for every message.** Before posting, the server asks you to confirm the exact text and target space, independently of the client's own tool approval. Spaces used by automated tasks can opt out with `:unattended`.
- **Your own identity.** Each user authenticates with their own Google account via OAuth: messages come from the real person, not from a shared bot.
- **Local only.** The server runs on your machine over stdio, started by the MCP client. It opens no network port.

## What can be configured

### `--space NAME:PERMS[:unattended]`

The only runtime setting. Repeat it once per space.

| Value | Read | Write | Confirmation before sending |
|---|---|---|---|
| `spaces/ID:r` | yes | no | — |
| `spaces/ID:w` | no | yes | yes |
| `spaces/ID:rw` | yes | yes | yes |
| `spaces/ID:w:unattended` | no | yes | no |
| `spaces/ID:rw:unattended` | yes | yes | no (warning logged at startup) |

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
| `google-chat-mcp auth` | One-time OAuth flow in the browser; writes `token.json` |
| `google-chat-mcp spaces` | Lists every space your account can access, to find IDs for `--space` |
| `google-chat-mcp --space ...` | Starts the MCP server over stdio (what the MCP client runs) |

With `uvx`, prefix them with `uvx "git+https://github.com/nuccio/google-chat-mcp"`.

If the OAuth consent screen is in **Testing** mode, Google invalidates the refresh token after 7 days and `auth` must be run again. The `chat_auth_status` tool reports the days since consent and warns from day 6.

## Read and write permissions

`w` does not imply `r`. A space configured `:w` can be posted to, but its messages and member list cannot be read through the server. Use it for spaces that only receive output (e.g. a scheduled task announcing it finished): content the agent never reads cannot steer what it writes.

`r` protects the message content and the member list of a space. It does not protect other space metadata: the server reads it internally to enforce its rules regardless of `r` (the space type, to block DMs; the display name, shown in the confirmation prompt).

## Send confirmation

By default, before `send_message` posts anything, the server asks you to confirm the exact text and the target space (display name and resource name). This is an MCP *elicitation*: it is independent of the client's own tool-approval prompt, so it still applies if you set that prompt to "allow always".

- If you decline or cancel, nothing is sent.
- If the MCP client does not support elicitation, sending to a space that requires confirmation fails with an explicit error instead of posting without it.

### Client requirements and MCP protocol versions

The confirmation only works if the MCP client declares the `elicitation` capability. Whether it does, and which MCP protocol version it uses, is decided by the client when it connects: the server cannot change it.

The server supports both generations of the protocol, and the confirmation works differently in each:

- **Up to `2025-11-25`**: while `send_message` is running, the server sends the confirmation request to the client, waits for the answer, then posts or refuses.
- **`2026-07-28`**: the protocol no longer lets the server send requests to the client during a tool call. The first `send_message` call ends without posting and returns an "input required" result containing the confirmation request; the client shows it and repeats the call with your answer. The answer is valid only for the same space and the same text: if the repeated call carries different text, it is refused and nothing is sent.

Resulting behaviour:

| Client | Space requiring confirmation | `:unattended` space |
|---|---|---|
| Supports elicitation, any protocol version | Asks, posts only after an explicit yes | Posts, no prompt |
| Does not support elicitation | Refused with an error, nothing sent | Posts, no prompt |
| `2026-07-28`, declares elicitation but does not handle "input required" results | The call fails, nothing sent | Posts, no prompt |

In every case, anything other than an explicit yes (decline, cancel, closed prompt, unchecked box, error) means nothing is sent.

To see what your client actually uses, check `server.log`: every tool call is logged with the negotiated protocol version and whether the client supports elicitation, e.g. `tool=list_spaces protocol=2025-11-25 elicitation=True`. If it shows `elicitation=False`, only `:unattended` spaces can be posted to with that client.

### `:unattended`

Scheduled or automated tasks cannot answer a confirmation prompt. For spaces they must post to, add the `:unattended` marker:

```
--space spaces/AAABBBCCC:w
--space spaces/REPORTS01:w:unattended
```

- It only affects writes. `:unattended` without `w` is rejected at startup.
- It applies to **every caller**, including interactive chats. The server cannot tell a scheduled task from a chat: both come from the same client. A space marked `:unattended` never asks for confirmation.
- `:rw:unattended` is allowed, but the server logs a warning at startup: content read from that space can influence posts made without confirmation.

## Security considerations

Text read from **any** space with `r` can steer posts to **every** `:unattended` space in the same configuration, without confirmation — not only to the space it was read from (prompt injection). Splitting the configuration into two servers (one for chat, one for tasks) does not help, because both are available in both contexts. What works:

1. Mark `:unattended` only spaces dedicated to automated output, where an unwanted post has low impact.
2. Prefer `w:unattended` without `r` whenever the task does not need to read that space.
3. Content rules on outbound messages ([#22](https://github.com/nuccio/google-chat-mcp/issues/22)), which apply to every call regardless of the caller.
