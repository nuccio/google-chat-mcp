# MCP server for Google Chat

An [MCP](https://modelcontextprotocol.io) server that connects [Claude Desktop](https://claude.ai/download) to Google Chat, with per-space access control. It runs locally on each user's machine: Claude Desktop starts it automatically and talks to it over stdio, so there is no separate process to run and no network port to open.

## Key features

- **Per-space allowlist**: only the spaces you list are reachable, each with read access, write access or both.
- **No direct messages**: the server never posts to direct messages or group chats.
- **Separate tools for interactive and automated sends**: `send_message` for messages you approve, `send_message_unattended` for scheduled tasks, only to spaces you mark for it.
- **Your own identity**: you sign in with your own Google account, so messages come from you, not from a shared bot.

Everything that can be configured, and what it means for security, is in [docs/configuration.md](docs/configuration.md).

## Available tools

| Tool | Permission | Description |
|------|------------|-------------|
| `list_spaces` | — | List configured spaces with their r/w flags |
| `get_space` | read | Get details of a space |
| `list_messages` | read | List the most recent messages in a space (up to 1000 per call), oldest first. An optional filter narrows the results; `page_token` reaches older messages |
| `send_message` | write | Send a message to a space without `:unattended` (see [Sending messages](docs/configuration.md#sending-messages)) |
| `send_message_unattended` | write | Send a message to a `:unattended` space, without confirmation (for scheduled tasks) |
| `list_members` | read | List members of a space |
| `chat_auth_status` | — | OAuth token status and expiry warning (works without a valid token) |

---

## Setup

### 1. Prerequisites

**On Windows**, the server runs inside WSL. Skip this part on macOS or Linux.

1. Open **PowerShell as Administrator** and run:
   ```powershell
   wsl --install
   ```
   This installs WSL 2 with Ubuntu. A restart is required.
2. After the restart, Ubuntu opens by itself. Create your user when asked.

From here on, on Windows, run every command in the Ubuntu (WSL) terminal.

**On every system**, install [`uv`](https://docs.astral.sh/uv/), which downloads and runs the server:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal afterwards, and check that `uvx --version` works.

### 2. OAuth credentials on Google Cloud Console

1. Open [console.cloud.google.com](https://console.cloud.google.com) and select (or create) a project.
2. Go to **APIs & Services → Library**, search for **Google Chat API** and enable it.
3. Go to **Google Auth Platform** (in older versions of the console, **APIs & Services → OAuth consent screen**):
   - **Branding**: fill in the app name and the support email.
   - **Audience**: choose **Internal** if you use a Google Workspace organisation (no verification required), otherwise **External**. With **External**, the app stays in **Testing** status: add your own Google account under **Test users**, and expect to repeat the sign-in in step 3 every 7 days (Google invalidates the token after that; `chat_auth_status` warns you from day 6).
   - **Data Access**: add these scopes:
     - `https://www.googleapis.com/auth/chat.spaces.readonly`
     - `https://www.googleapis.com/auth/chat.messages`
     - `https://www.googleapis.com/auth/chat.memberships.readonly`
4. Go to **Clients → Create client** (in older consoles, **APIs & Services → Credentials → Create Credentials → OAuth client ID**):
   - Application type: **Desktop app**
   - Give it a name (e.g. "Claude MCP")
5. Download the JSON file and save it as `~/.config/google-chat-mcp/client_secrets.json`. Its structure matches [`client_secrets.example.json`](client_secrets.example.json).

   ```bash
   mkdir -p ~/.config/google-chat-mcp
   ```

   On Windows the file ends up in the Windows `Downloads` folder: copy it into WSL (replace `YourUser` with your Windows username):

   ```bash
   cp /mnt/c/Users/YourUser/Downloads/client_secret*.json ~/.config/google-chat-mcp/client_secrets.json
   ```

### 3. Sign in to Google

Run this **once**, in a terminal on your machine. It is a separate step that you run yourself, not something Claude does for you, and it must be done before Claude Desktop is configured:

```bash
uvx "git+https://github.com/nuccio/google-chat-mcp@stable" auth
```

A browser window opens. Sign in with your Google account and grant access. The token is saved to `~/.config/google-chat-mcp/token.json` and refreshed automatically afterwards. Its structure matches [`token.example.json`](token.example.json).

### 4. Find space IDs

The server needs the resource name of each space you want to allow (e.g. `spaces/AAABBBCCC`). There are two ways to find it.

**From the command line** (after step 3):

```bash
uvx "git+https://github.com/nuccio/google-chat-mcp@stable" spaces
```

This prints every space your account can access:

```
spaces/AAABBBCCC  Team General  [SPACE]
spaces/DDDEEEFFF  Announcements  [SPACE]
spaces/GGGHHH111  (no name)  [DIRECT_MESSAGE]
```

The first column is the resource name. Direct messages are listed too, without a name, but the server never posts to them.

**From the Google Chat address**: open [chat.google.com](https://chat.google.com), go to the space, and look at the URL:

```
https://chat.google.com/room/AAABBBCCC/...
                              ^^^^^^^^^^^
```

The resource name is `spaces/` followed by that segment.

### 5. Add the server to Claude Desktop

Open the Claude Desktop configuration file, on macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, and add the server, with one `--space` for each space you allow. **Back up the file first**: if the JSON is invalid, Claude Desktop replaces it with an empty configuration.

```json
{
  "mcpServers": {
    "google-chat": {
      "command": "uvx",
      "args": [
        "git+https://github.com/nuccio/google-chat-mcp@stable",
        "--space", "spaces/AAABBBCCC:rw",
        "--space", "spaces/DDDEEEFFF:r"
      ]
    }
  }
}
```

After `--space`, write the resource name followed by `:r` (read), `:w` (write) or `:rw` (both). Then restart Claude Desktop, and in the connector's settings set `send_message` to require approval.

- Windows with WSL, updating the server, and the approval settings for each tool: [docs/claude-desktop.md](docs/claude-desktop.md).
- What every `--space` value means, including `:unattended` for scheduled tasks: [docs/configuration.md](docs/configuration.md).

---

## Versions

Install the `stable` tag, as the examples do.

| Tag | Points to | For |
|---|---|---|
| `stable` | the last version tried in a real client and known to work | everyone |
| `latest` | the most recent commit on `main` whose automated tests passed, not yet tried in a real client | developers and testers only |

Both tags move, and `uvx` resolves the dependencies itself instead of using the repository's `uv.lock`. To fix what you run, see [Pinning a version](docs/claude-desktop.md#pinning-a-version) and [SECURITY.md](SECURITY.md). How the tags are moved is described in [Release and branch policy](CONTRIBUTING.md#release-and-branch-policy).

## Contributing

How to set up a checkout, run the tests and propose a change: [CONTRIBUTING.md](CONTRIBUTING.md). Tests against real Google Chat spaces: [tests/README.md](tests/README.md). Trying `latest` in Claude Desktop: [docs/manual-testing.md](docs/manual-testing.md).
