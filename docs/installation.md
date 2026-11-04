# Installation

How to prepare your computer, sign in to Google and find the spaces to allow. Follow the section for your system, then the common steps below it. You need the `client_secrets.json` file from [google-cloud.md](google-cloud.md).

- [macOS](#macos)
- [Linux](#linux)
- [Windows (with WSL)](#windows-with-wsl)
- Common steps: [Sign in to Google](#sign-in-to-google), [Find space IDs](#find-space-ids)

All the server's files live in `~/.config/google-chat-mcp/`: the credentials file, the token written when you sign in, and `server.log`. The folder is fixed; see [Files](configuration.md#files).

## macOS

1. Install [`uv`](https://docs.astral.sh/uv/), which downloads and runs the server. In Terminal:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   (or `brew install uv` if you use Homebrew). Open a new Terminal window and check that `uvx --version` works.
2. Create the server's folder:
   ```bash
   mkdir -p ~/.config/google-chat-mcp
   ```
3. Move the credentials file there, with the name the server expects:
   ```bash
   mv ~/Downloads/client_secret_*.json ~/.config/google-chat-mcp/client_secrets.json
   ```

Then [sign in to Google](#sign-in-to-google).

## Linux

1. Install [`uv`](https://docs.astral.sh/uv/):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   Open a new terminal and check that `uvx --version` works.
2. Create the server's folder:
   ```bash
   mkdir -p ~/.config/google-chat-mcp
   ```
3. Move the credentials file there, with the name the server expects (adjust the path if your browser saves elsewhere):
   ```bash
   mv ~/Downloads/client_secret_*.json ~/.config/google-chat-mcp/client_secrets.json
   ```

Then [sign in to Google](#sign-in-to-google).

## Windows (with WSL)

The server runs inside WSL (Windows Subsystem for Linux), and Claude Desktop starts it there. Every command below, except the first, runs in the **Ubuntu (WSL) terminal**, not in PowerShell or the Command Prompt.

1. Open **PowerShell as Administrator** and run:
   ```powershell
   wsl --install
   ```
   This installs WSL 2 with Ubuntu. Restart Windows when asked. After the restart Ubuntu opens by itself: create your Linux user when prompted.
2. In the Ubuntu terminal, install [`uv`](https://docs.astral.sh/uv/):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   Close the Ubuntu terminal, open it again, and check that `uvx --version` works.
3. Create the server's folder (inside WSL, not on the Windows drive):
   ```bash
   mkdir -p ~/.config/google-chat-mcp
   ```
4. The browser saved the credentials file in the Windows `Downloads` folder. Copy it into WSL, replacing `YourUser` with your Windows user name:
   ```bash
   cp /mnt/c/Users/YourUser/Downloads/client_secret_*.json ~/.config/google-chat-mcp/client_secrets.json
   ```

Then [sign in to Google](#sign-in-to-google).

## Sign in to Google

Run this **once**, in the terminal (on Windows, the Ubuntu terminal). It is a separate step that you run yourself, not something Claude does for you, and it must be done before Claude Desktop is configured:

```bash
uvx "git+https://github.com/nuccio/google-chat-mcp@stable" auth
```

A browser window opens: sign in with your Google account and grant access. If no browser opens, which is common under WSL, open the address printed in the terminal in your browser; the sign-in completes in the terminal.

The token is saved to `~/.config/google-chat-mcp/token.json`, readable only by your user, and refreshed automatically afterwards. Its structure matches [`token.example.json`](../token.example.json).

If the credentials file is somewhere else, set `GOOGLE_CLIENT_SECRETS` to its path in the same terminal before running `auth`. Only `auth` reads that file: the server itself uses `token.json`.

Run the same command again if a tool later reports a missing or expired token (`invalid_grant`), then restart Claude Desktop. With an **External** app in Testing status this happens every 7 days (see [google-cloud.md](google-cloud.md#2-oauth-consent)).

### Upgrading from the `chat.messages` scope

The server now requests `chat.messages.readonly` and `chat.messages.create` instead of `chat.messages`, which also allowed updating and deleting messages and managing reactions. A `token.json` issued for the old scope is refused with an error, so you must sign in again once:

1. On the OAuth consent screen, add the two new scopes and remove `chat.messages` (see [google-cloud.md](google-cloud.md#2-oauth-consent)).
2. In your [Google account permissions](https://myaccount.google.com/permissions), remove access for this app. Running `auth` alone does not revoke the old refresh token.
3. Run the `auth` command above.

All versions on a machine share the same `token.json`. A new token does not work with older versions, which request `chat.messages` when they refresh it, so update every server entry and do not roll back without running `auth` again on that version.

## Find space IDs

The server reaches only the spaces you list, by their resource name (e.g. `spaces/AAABBBCCC`). There are two ways to find it.

**From the command line** (after signing in):

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

Next: [add the server to Claude Desktop](claude-desktop.md).
