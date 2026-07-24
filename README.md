# MCP Server for Google Chat with ACL

An [MCP](https://modelcontextprotocol.io) server that connects [Claude Desktop](https://claude.ai/download) to Google Chat. It is designed to run locally on each user's machine: Claude Desktop spawns the server automatically and communicates with it over stdio, so no separate process or network endpoint is needed.

The server gives Claude fine-grained, per-space access control. Each Google Chat space can be independently granted read access, write access, both, or neither — and sending direct messages to individual users is explicitly blocked. Every user authenticates with their own Google account via OAuth, so messages always come from the real person, not a shared bot.

## Available tools

| Tool | Permission | Description |
|------|------------|-------------|
| `list_spaces` | — | List configured spaces with their r/w flags |
| `get_space` | read | Get details of a space |
| `list_messages` | read | List messages in a space (optional filter) |
| `send_message` | write | Send a message to a space |
| `list_members` | read | List members of a space |

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

Run this command **once** to authorise access to your Google account:

```bash
uvx "git+https://github.com/nuccio/google-chat-mcp" auth
```

A browser window will open. Sign in with your Google account and grant access. The token is saved to `~/.config/google-chat-mcp/token.json` and refreshed automatically on subsequent runs.

## Finding space IDs

To configure which spaces are accessible, you need their resource name (e.g. `spaces/AAABBBCCC`). List all spaces your account can see with:

```bash
uvx "git+https://github.com/nuccio/google-chat-mcp" spaces
```

---

## Claude Desktop configuration

Edit your Claude Desktop config file (`claude_desktop_config.json`):

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
      ]
    }
  }
}
```

**Permission flags for `--space`:**
- `spaces/ID:r` — read only
- `spaces/ID:w` — write only
- `spaces/ID:rw` — read and write

Repeat `--space` for each space you want to make accessible. Spaces not listed are completely inaccessible.

---

## Local development

```bash
git clone https://github.com/nuccio/google-chat-mcp
cd google-chat-mcp
uv pip install -e ".[dev]"

# Run tests
pytest

# Verbose output
pytest -v

# Integration tests (requires a valid OAuth token)
pytest -m integration
```
