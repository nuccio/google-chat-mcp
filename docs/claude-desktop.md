# Claude Desktop configuration

How to add the server to Claude Desktop. For the meaning of the `--space` values, the send confirmation and everything else that can be configured, see [configuration.md](configuration.md).

## Before you start: authenticate from a terminal

Claude Desktop cannot sign you in to Google: the server only reads the token that the `auth` command writes. Do this once, **before** editing the Claude Desktop configuration:

1. Check that everything `uvx` needs is in place:
   - `uv` is installed (`uvx --version` works in a terminal; on Windows, inside WSL);
   - `client_secrets.json` is at `~/.config/google-chat-mcp/client_secrets.json`, or `GOOGLE_CLIENT_SECRETS` is set to its path in the same terminal (see [OAuth credentials on Google Cloud Console](../README.md#1-oauth-credentials-on-google-cloud-console)).
2. In a terminal (on Windows, the WSL terminal), run:
   ```bash
   uvx "git+https://github.com/nuccio/google-chat-mcp@stable" auth
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

> **Back up the file before editing it.** If the JSON has a syntax error (even a single missing comma), Claude Desktop silently discards it and resets it to an empty default configuration on the next launch — wiping out any other MCP servers you had configured too. Keep a copy you can restore from, e.g.:
> ```bash
> cp ~/Library/Application\ Support/Claude/claude_desktop_config.json ~/Library/Application\ Support/Claude/claude_desktop_config.json.bak
> ```
> If Claude Desktop ever starts with no MCP servers after an edit, check this backup and validate your JSON (e.g. `python3 -m json.tool claude_desktop_config.json`) before reapplying it.

## macOS / Linux

```json
{
  "mcpServers": {
    "google-chat": {
      "command": "uvx",
      "args": [
        "git+https://github.com/nuccio/google-chat-mcp@stable",
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
        "git+https://github.com/nuccio/google-chat-mcp@stable",
        "--space", "spaces/AAABBBCCC:rw",
        "--space", "spaces/DDDEEEFFF:r"
      ]
    }
  }
}
```

The server uses the OAuth token in `~/.config/google-chat-mcp/token.json` (inside WSL on Windows), written by the `auth` command during setup. No `env` block is needed: `GOOGLE_CLIENT_SECRETS` is only read by `auth`.

## Choosing the version

The examples above install `stable`, the version to use unless you are developing the server or testing a change. `latest` and branches are for developers and testers only. To install one of them, change the repository URL in `args`:

| URL | Installs | When to use it |
|---|---|---|
| `git+https://github.com/nuccio/google-chat-mcp@stable` | the last version tried and known to work | everyday use |
| `git+https://github.com/nuccio/google-chat-mcp@latest` | the most recent `main` that passed the automated tests, not yet tried in a real client | developers and testers only: to try a merged change before it becomes `stable`, see [manual-testing.md](manual-testing.md) |
| `git+https://github.com/nuccio/google-chat-mcp@<branch>` | an unmerged branch | developers and testers only: to look at a change before it is merged |

Write `@` followed by the tag or branch name, not the `/tree/...` address shown by the GitHub web page: `uvx` cannot resolve that and the server does not start.

Tags and branches move, while `uvx` keeps the version it downloaded. Put `"--refresh"` before the URL, so that Claude Desktop installs the current version of the tag or branch every time it is restarted:

```json
"args": [
  "--refresh",
  "git+https://github.com/nuccio/google-chat-mcp@stable",
  "--space", "spaces/AAABBBCCC:rw"
]
```

This is convenient, but read the trade-off in [Pinning a version](#pinning-a-version) first.

The `--space` values must match the version: for example, `:unattended` is not accepted by versions older than the one that introduced it, and the server refuses to start.

What `stable` and `latest` mean, and how they are moved, is described in [Release and branch policy](../README.md#release-and-branch-policy).

## Pinning a version

The server can read your OAuth token, so whatever `uvx` installs can too. With `--refresh` on a tag, every restart of Claude Desktop runs whatever the tag points to at that moment, with dependencies resolved again from PyPI. You get fixes without doing anything, and you also get a bad release without doing anything.

`uvx` does **not** read the repository's `uv.lock`. It resolves the dependencies itself, to the newest versions that `pyproject.toml` allows. Pinning the commit therefore fixes this project's code, not its dependencies.

From the least to the most fixed:

| Install | Project code | Dependencies | Updates |
|---|---|---|---|
| `--refresh` and `@stable` | changes when the tag moves | newest allowed, at every start | automatic |
| `--refresh`, `--exclude-newer "7 days"` and `@stable` | changes when the tag moves | newest allowed that are at least 7 days old, at every start | automatic |
| `@<commit>` and `--exclude-newer <date>` | fixed | newest allowed on that date | by hand |
| Clone and `uv sync --frozen` | fixed | exactly `uv.lock`, with hashes checked | by hand |

**A commit and a date.** Use the full 40-character SHA of the commit that `stable` points to, printed by `git ls-remote https://github.com/nuccio/google-chat-mcp 'refs/tags/stable^{}'`. Do not use `--refresh`. The date also limits the package that builds the project (`hatchling`):

```json
"args": [
  "--exclude-newer", "2026-11-01",
  "git+https://github.com/nuccio/google-chat-mcp@<full-commit-sha>",
  "--space", "spaces/AAABBBCCC:rw"
]
```

Without the date, the dependencies are chosen when `uvx` first builds its environment, and chosen again if its cache is cleared. To update, change both values, after reading what changed.

**A waiting period on a tag.** `--exclude-newer "7 days"` keeps automatic updates but skips dependency releases less than 7 days old, which is often long enough for a malicious release to be found and removed. It does not delay this project's own code. If a new version of this project requires a dependency released in the last 7 days, the server does not start until that release is old enough. A relative value needs a recent `uv` (tried with 0.11.17).

**A clone with the lock file.** This is the only install that uses `uv.lock`, which exists only in commits from the one that added it onwards:

```bash
git clone https://github.com/nuccio/google-chat-mcp ~/google-chat-mcp
```

```bash
git -C ~/google-chat-mcp checkout <full-commit-sha>
```

```bash
uv sync --directory ~/google-chat-mcp --frozen --no-dev
```

Then point `command` at the installed program, with its full path, and leave `uvx` out. Nothing is downloaded when Claude Desktop starts it:

```json
"command": "/Users/you/google-chat-mcp/.venv/bin/google-chat-mcp",
"args": [
  "--space", "spaces/AAABBBCCC:rw"
]
```

Run `auth` and `spaces` with the same program. To update, repeat the `checkout` and `uv sync` with a newer commit, and restart Claude Desktop. On Windows, do this inside WSL and use `"command": "wsl"` with `"args": ["--", "/home/you/google-chat-mcp/.venv/bin/google-chat-mcp", ...]`.

A pinned install does not receive security fixes by itself. Check the [`stable` tag](https://github.com/nuccio/google-chat-mcp/tags) from time to time, and update on purpose.

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

To try `latest` before it becomes `stable`, see [manual-testing.md](manual-testing.md).
