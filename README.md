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

Setting up takes five steps. Each links to the page with the details.

1. **Create the OAuth credentials on Google Cloud.** Enable the Google Chat API in a Google Cloud project, configure the consent screen and create a *Desktop app* OAuth client, then download its `client_secrets.json`. → [docs/google-cloud.md](docs/google-cloud.md)
2. **Prepare your computer.** Install `uv` (on Windows, inside WSL) and save `client_secrets.json` in `~/.config/google-chat-mcp/`. → [macOS](docs/installation.md#macos) · [Linux](docs/installation.md#linux) · [Windows (with WSL)](docs/installation.md#windows-with-wsl)
3. **Sign in to Google**, once, from a terminal: `uvx "git+https://github.com/nuccio/google-chat-mcp@stable" auth`. → [Sign in to Google](docs/installation.md#sign-in-to-google)
4. **Find the IDs of the spaces to allow** (e.g. `spaces/AAABBBCCC`). → [Find space IDs](docs/installation.md#find-space-ids)
5. **Add the server to Claude Desktop**, with one `--space` for each space, then set which tools need your approval. → [macOS](docs/claude-desktop.md#macos) · [Linux](docs/claude-desktop.md#linux) · [Windows (with WSL)](docs/claude-desktop.md#windows-with-wsl) · [Tool approval settings](docs/claude-desktop.md#tool-approval-settings)

What every `--space` value means, including `:unattended` for scheduled tasks, and what it implies for security: [docs/configuration.md](docs/configuration.md).

---

## Versions

Install the `stable` tag, as the examples do.

| Tag | Points to | For |
|---|---|---|
| `stable` | the last version tried in a real client and known to work | everyone |
| `latest` | the most recent commit on `main` whose automated tests passed, not yet tried in a real client | developers and testers only |

Both tags move, and `uvx` resolves the dependencies itself instead of using the repository's `uv.lock`. To fix what you run, see [Pinning a version](docs/claude-desktop.md#pinning-a-version) and [SECURITY.md](SECURITY.md). How the tags are moved is described in [Release and branch policy](CONTRIBUTING.md#release-and-branch-policy).

## Contributing

How to set up a checkout, run the tests and propose a change: [CONTRIBUTING.md](CONTRIBUTING.md). Tests against real Google Chat spaces: [tests/README.md](tests/README.md). Trying `latest` in Claude Desktop: [docs/manual-testing.md](docs/manual-testing.md). Trying the settings page of the remote connector with a mocked backend: [docs/remote/05-settings-ui-mock.md](docs/remote/05-settings-ui-mock.md). Setting up the GCP project, consent screen, OAuth clients, secrets and Firestore of the remote connector (operator guide, in progress): [docs/remote/08-operator-setup.md](docs/remote/08-operator-setup.md). Deploying, checking, updating and rolling back the MCP service `gcm-mcp`: [docs/remote/11-deploy-gcm-mcp.md](docs/remote/11-deploy-gcm-mcp.md).
