# MCP Server for Google Chat with ACL

An [MCP](https://modelcontextprotocol.io) server that connects [Claude Desktop](https://claude.ai/download) to Google Chat. It is designed to run locally on each user's machine: Claude Desktop spawns the server automatically and communicates with it over stdio, so no separate process or network endpoint is needed.

## Key features

- **Per-space allowlist**: only the spaces you list are reachable, each with read access, write access or both (independent of each other).
- **No direct messages**: sending to DMs and group chats is blocked.
- **Separate tools for interactive and automated sends**: `send_message` posts only to regular spaces and is meant to require your approval in the client; `send_message_unattended` posts only to spaces marked `:unattended`, for scheduled tasks. When the client supports it, `send_message` also asks you to confirm the exact text and target space.
- **Your own identity**: each user authenticates with their own Google account, so messages come from the real person, not a shared bot.

See [docs/configuration.md](docs/configuration.md) for everything that can be configured, and its security implications.

## Available tools

| Tool | Permission | Description |
|------|------------|-------------|
| `list_spaces` | — | List configured spaces with their r/w flags |
| `get_space` | read | Get details of a space |
| `list_messages` | read | List messages in a space (optional filter) |
| `send_message` | write | Send a message to a space without `:unattended` (see [Sending messages](docs/configuration.md#sending-messages)) |
| `send_message_unattended` | write | Send a message to a `:unattended` space, without confirmation (for scheduled tasks) |
| `list_members` | read | List members of a space |
| `chat_auth_status` | — | OAuth token status and expiry warning (works without a valid token) |

---

## Setup

### 1. OAuth credentials on Google Cloud Console

1. Open [console.cloud.google.com](https://console.cloud.google.com) and select (or create) a project
2. Go to **APIs & Services → Library**, search for **Google Chat API** and enable it
3. Go to **APIs & Services → OAuth consent screen**:
   - Choose **Internal** if you use a Google Workspace organisation (no verification required), otherwise **External**
   - Add the following scopes:
     - `https://www.googleapis.com/auth/chat.spaces.readonly`
     - `https://www.googleapis.com/auth/chat.messages`
     - `https://www.googleapis.com/auth/chat.memberships.readonly`
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Give it a name (e.g. "Claude MCP")
5. Click **Download JSON** and save the file as:
   ```
   ~/.config/google-chat-mcp/client_secrets.json
   ```
   The structure of the file matches [`client_secrets.example.json`](client_secrets.example.json) in this repo.

   > On Windows with WSL, this path is inside the WSL filesystem (see below).

### 2. Installing WSL on Windows

> Skip this section on Mac or Linux.

1. Open **PowerShell as Administrator** and run:
   ```powershell
   wsl --install
   ```
   This installs WSL 2 with Ubuntu. A system restart is required.

2. After restarting, Ubuntu launches automatically. Create your user when prompted.

3. Inside the Ubuntu terminal, install `uv`:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   source ~/.bashrc
   ```

4. Create the config folder and copy the `client_secrets.json` downloaded earlier:
   ```bash
   mkdir -p ~/.config/google-chat-mcp
   # Copy the file from Windows (replace "YourUser" with your Windows username):
   cp /mnt/c/Users/YourUser/Downloads/client_secrets*.json ~/.config/google-chat-mcp/client_secrets.json
   ```

---

## First run: OAuth authentication

Run this command **once**, in a terminal on your machine, to authorise access to your Google account — it is a separate CLI step, not something Claude runs for you, and it must be done before Claude Desktop is configured to start the server:

```bash
uvx "git+https://github.com/nuccio/google-chat-mcp" auth
```

A browser window will open. Sign in with your Google account and grant access. The token is saved to `~/.config/google-chat-mcp/token.json` and refreshed automatically on subsequent runs. Its structure matches [`token.example.json`](token.example.json) in this repo.

## Finding space IDs

To configure which spaces are accessible, you need their resource name (e.g. `spaces/AAABBBCCC`). There are two ways to find it.

**Option A — CLI (after completing the auth step)**

```bash
uvx "git+https://github.com/nuccio/google-chat-mcp" spaces
```

This prints a table of all spaces your account can access:

```
spaces/AAABBBCCC  Team General    [SPACE]
spaces/DDDEEEFFF  Announcements   [SPACE]
spaces/GGGHHH111  Alice Johnson   [DIRECT_MESSAGE]
```

The first column is the value to use with `--space`.

**Option B — Google Chat URL**

Open [chat.google.com](https://chat.google.com) in your browser, navigate to the space, and look at the URL:

```
https://chat.google.com/room/AAABBBCCC/...
                              ^^^^^^^^^^^
```

The resource name is `spaces/` followed by that segment.

---

## Claude Desktop configuration

See [docs/claude-desktop.md](docs/claude-desktop.md) for where to put the configuration (macOS/Linux and Windows with WSL), and [docs/configuration.md](docs/configuration.md) for what each option means.

---

## Versions

The repository keeps two tags, both of which move over time:

| Tag | Points to | How it moves |
|---|---|---|
| `latest` | the current head of `main` | automatically, on every push to `main` |
| `stable` | the last version that has been tried and is known to work | by hand, after trying `latest` |

To install one of them, add `@<tag>` to the URL, e.g. `git+https://github.com/nuccio/google-chat-mcp@stable`. Without a tag, `uvx` installs `main`, the same as `latest`. Because the tags move, add `--refresh` before the URL in the `uvx` arguments, so that Claude Desktop picks up the new version when restarted.

To move `stable`: on GitHub, **Actions → Release tags → Run workflow**. Leave "Commit" empty to promote the commit `latest` points to, or enter the SHA of an earlier commit on `main` (e.g. to roll `stable` back). Commits that are not on `main` are refused. The workflow is in [`.github/workflows/release-tags.yml`](.github/workflows/release-tags.yml).

---

## Local development

```bash
git clone https://github.com/nuccio/google-chat-mcp
cd google-chat-mcp
uv pip install -e ".[dev]"
```

For running tests, including integration tests against real Google Chat spaces, see [tests/README.md](tests/README.md). To try an unmerged branch in Claude Desktop, see [docs/manual-testing.md](docs/manual-testing.md).
