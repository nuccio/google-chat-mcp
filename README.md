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
| `list_messages` | read | List the most recent messages in a space (up to 1000 per call), oldest first. An optional filter narrows the results; `page_token` reaches older messages |
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
uvx "git+https://github.com/nuccio/google-chat-mcp@stable" auth
```

A browser window will open. Sign in with your Google account and grant access. The token is saved to `~/.config/google-chat-mcp/token.json` and refreshed automatically on subsequent runs. Its structure matches [`token.example.json`](token.example.json) in this repo.

## Finding space IDs

To configure which spaces are accessible, you need their resource name (e.g. `spaces/AAABBBCCC`). There are two ways to find it.

**Option A — CLI (after completing the auth step)**

```bash
uvx "git+https://github.com/nuccio/google-chat-mcp@stable" spaces
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

Install the `stable` tag, as the examples do. The `latest` tag is for developers and testers only (see [Release and branch policy](#release-and-branch-policy)).

---

## Local development

```bash
git clone https://github.com/nuccio/google-chat-mcp
cd google-chat-mcp
uv pip install -e ".[dev]"
```

For running tests, including integration tests against real Google Chat spaces, see [tests/README.md](tests/README.md). To try the settings page of the remote connector with a mocked backend, see [docs/remote/04-settings-ui-mock.md](docs/remote/04-settings-ui-mock.md). To try `latest` in Claude Desktop before promoting it to `stable`, see [docs/manual-testing.md](docs/manual-testing.md).

---

## Release and branch policy

Two tags separate the version people use every day from the one being tried:

| Tag | Points to | How it moves |
|---|---|---|
| `latest` | the most recent commit on `main` whose automated tests passed | automatically, when the tests on `main` pass |
| `stable` | the last version tried in a real client and known to work | by hand, after trying `latest` |

Users should install `stable`. `latest` is for developers and testers only, because it has not been tried in a real client yet.

Clients configured with `stable` are not affected by bugs that the automated tests did not catch: a change reaches them only after someone has tried it in a real client. Some problems only show up there — for example, whether the client supports a given MCP feature.

Both tags move, and `uvx` resolves the dependencies itself instead of using `uv.lock`. To fix what you run, see [Pinning a version](docs/claude-desktop.md#pinning-a-version) and [SECURITY.md](SECURITY.md).

**Flow of a change:**

1. The change is developed on its own branch, usually one per issue, and proposed with a pull request.
2. The automated tests run on every pull request (the **Tests** workflow, [`.github/workflows/tests.yml`](.github/workflows/tests.yml), on Python 3.11–3.13) and must pass before merging. Integration tests need a real OAuth token and are run by hand (see [tests/README.md](tests/README.md)).
3. The pull request is merged into `main` with squash and merge: `main` only accepts verified signatures, and GitHub signs the squash commit, while a rebase merge rewrites the commits without signing them. The tests run again on `main`, and if they pass `latest` moves to the new commit. The branch is kept until step 5.
4. `latest` is tried in Claude Desktop, following [docs/manual-testing.md](docs/manual-testing.md).
5. If it works, `latest` is promoted to `stable`, and the branches of the pull requests released by the promotion are deleted. If a bug turns up, `stable` stays where it is, and the fix goes through the same flow.

**Promoting to `stable`:** on GitHub, **Actions → Release tags → Run workflow**. Leave "Commit" empty to promote the commit `latest` points to, or enter the SHA of an earlier commit on `main` (e.g. to roll `stable` back). Commits that are not on `main` are refused. The workflow then deletes the branch of every pull request merged into `main` whose merge is now part of `stable`, unless something was pushed to the branch after the merge or an open pull request still uses it. The workflow is in [`.github/workflows/release-tags.yml`](.github/workflows/release-tags.yml).

The branch protection of `main` (Settings → Branches) requires the three `pytest` jobs of the **Tests** workflow, one approving review and verified signatures. How to contribute a change is described in [CONTRIBUTING.md](CONTRIBUTING.md).

For how to install a given version in Claude Desktop, see [Choosing the version](docs/claude-desktop.md#choosing-the-version).
