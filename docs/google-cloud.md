# Google Cloud setup

The server signs in to Google Chat with your own Google account, through an OAuth client that you create once in a Google Cloud project. This page creates it and downloads its credentials file, `client_secrets.json`. It is the same on every operating system; where to save the file is in [installation.md](installation.md).

You need a Google account that uses Google Chat.

## 1. Project and API

1. Open [console.cloud.google.com](https://console.cloud.google.com) and select a project, or create one (e.g. "google-chat-mcp").
2. Go to **APIs & Services → Library**, search for **Google Chat API** and enable it.

## 2. OAuth consent

Go to **Google Auth Platform**. In older versions of the console the same settings are under **APIs & Services → OAuth consent screen**.

- **Branding**: fill in the app name (e.g. "Claude MCP") and the support email.
- **Audience**:
  - **Internal** if your account belongs to a Google Workspace organisation. Only accounts in the organisation can use the app, and no verification is required.
  - **External** otherwise. The app stays in **Testing** status: add your own Google account under **Test users**, or the sign-in is refused. In Testing status Google invalidates the token 7 days after you sign in, so the sign-in has to be repeated every week; `chat_auth_status` warns you from day 6.
- **Data Access**: add these scopes:
  - `https://www.googleapis.com/auth/chat.spaces.readonly`
  - `https://www.googleapis.com/auth/chat.messages`
  - `https://www.googleapis.com/auth/chat.memberships.readonly`

  These are the only scopes the server asks for. The integration test setup needs one more, see [tests/README.md](../tests/README.md#prerequisites).

## 3. OAuth client

1. Go to **Clients → Create client** (in older consoles, **APIs & Services → Credentials → Create Credentials → OAuth client ID**).
2. Application type: **Desktop app**. Give it a name (e.g. "Claude MCP").
3. Download the JSON file of the client. Google names it `client_secret_<id>.apps.googleusercontent.com.json`; its structure matches [`client_secrets.example.json`](../client_secrets.example.json).

Do not share the file or commit it to a repository. Next, save it where the server looks for it: see your system in [installation.md](installation.md).
