# 01 — Requirements analysis: multi-user remote connector

This is the first pass on the three questions from [issue #29](https://github.com/nuccio/google-chat-mcp/issues/29). It was written before choosing how users configure their spaces. The follow-up based on that choice is [02-settings-page-direction.md](02-settings-page-direction.md).

The code references are to the current local version (`google_chat_mcp/`). The library references are to FastMCP 4.0.9.

---

## 1. A single instance serving many users

A remote connector is a single URL (e.g. `https://chat-mcp.example.com/mcp`), so one service answers every user. On Claude Team/Enterprise plans an admin can add the connector for the whole organisation, but each user still goes through their own OAuth login. There is one instance and many identities.

The current code assumes one user per process:

| Where | Today | Remote |
|-------|-------|--------|
| `server.py`: module globals `_cfg`, `_chat` | one ACL and one client per process | resolved **per request** from the authenticated user |
| `auth.py`: `TOKEN_PATH` under `~/.config` | one token on the local filesystem | one token per user, encrypted, in shared storage |
| `InstalledAppFlow` ("Desktop app" OAuth client) | each user brings their own `client_secrets.json` | a single "Web application" OAuth client, owned by whoever runs the server |
| `--space` on the command line | ACL set by whoever launches the process | per-user ACL stored in a database |
| `_LoggingMiddleware` logs tool `args` | a user's own messages in their own log file | would log everyone's message text, so it must be reduced |

### Tenancy granularity

A decision follows from this: one instance for several organisations, or **one instance per Google Workspace organisation** (multi-user within the organisation)? The recommendation is one per organisation:

- it can use an **Internal** OAuth consent screen. That means no Google app verification, and none of the 7-day refresh token expiry of Testing mode (already handled in `chat_auth_status`);
- a compromise of the service stays within one organisation;
- the Terraform code is the same, applied once per organisation.

### Authentication towards Claude

There is no need to write an OAuth authorisation server from scratch. FastMCP ships `GoogleProvider`, built on `OAuthProxy`, which:

- exposes an authorisation server that follows the MCP specification to the client (Claude), with dynamic client registration and CIMD support (`enable_cimd`);
- delegates the actual login to Google;
- issues **its own** tokens to Claude instead of forwarding Google's (the MCP specification forbids token passthrough);
- stores the upstream Google tokens through `client_storage` (an `AsyncKeyValue`), encrypted with Fernet. A persistent backend can be plugged in there.

Inside a tool, `get_access_token()` gives the identity of the user making the current request.

---

## 2. How each user chooses spaces and permissions

One constraint drives the design: **the model must not be able to change the configuration.** A tool such as `configure_space` would let a chat message like "grant yourself write access to X" (prompt injection) widen the ACL. Today this cannot happen, because `--space` lives in a file the model never touches. Configuration has to stay outside the conversation.

| Option | Assessment |
|--------|------------|
| **A. Settings web page hosted by the server** (`/settings`), with Google login and the same identity as the connector | **Recommended, and chosen.** It lists the user's spaces through the Chat API with their own token, with read/write checkboxes per space. |
| B. MCP tool with user confirmation (elicitation) | Rejected as the main channel. Elicitation support in Claude clients is not guaranteed, and the model is still one step away from changing the ACL. At most it could be used to *narrow* permissions. |
| C. Configuration in the connector URL (`/mcp?space=...`) | Rejected. An organisation-wide connector has one shared URL, and the URL is not tied to an identity. |
| D. Admin policy | A complement to A, not a replacement. The admin sets a ceiling (e.g. the DM block already in the code, or a list of allowed spaces) and users choose within it. Effective permission = policy ∩ user preference. |

One property keeps the risk low: Google already restricts each user to the spaces they belong to. The ACL is always an *extra restriction*. A user who configures themselves badly cannot reach anyone else's data. At worst they give Claude more freedom on their own spaces.

### Minimal data model

The key is Google's `sub` (stable), not the email address:

```
users        (sub PK, email, created_at)
google_creds (sub PK/FK, encrypted_refresh_token, scopes, authorized_at)
space_acl    (sub FK, space_name, read, write, updated_at)   PK(sub, space_name)
```

> [02-settings-page-direction.md](02-settings-page-direction.md) drops `google_creds`: the tokens stay in the `OAuthProxy` store.

`SpaceConfig` in `config.py` can be kept as it is. Only its source changes: `SpaceConfig.from_db(sub)` instead of `from_args`.

---

## 3. No user can see another user's configuration

**Identity**
- The user is derived **only** from the token validated by the server (`get_access_token()` → `sub`). Never from tool arguments, client-set headers or query parameters.
- The server validates the token's audience/resource (RFC 8707), so a token issued for another service is rejected.

**Data access**
- Every function in the data access layer takes `sub` as a mandatory parameter. There is no "list all ACLs".
- Defence in depth: Row Level Security on Postgres, or per-user documents (`users/{sub}/...`) with matching rules on Firestore.
- Settings endpoints never accept a user id. They always act on the user of the session, which prevents IDOR (reaching someone else's data by changing an id in the request).
- Session cookies are `HttpOnly`/`Secure`/`SameSite`, and every POST is protected against CSRF.

**In-memory state**
- The `_cfg`/`_chat` globals go away. Any cache is keyed by `sub` and expires.
- This is a real risk: under concurrent requests, a shared global would serve one user's credentials to another.
- A dedicated test is needed: two users with different tokens make parallel calls, and neither sees the other's spaces or messages.

**Output and logs**
- `list_spaces` and `chat_auth_status` return only the current user's data. `token_path` no longer makes sense and is removed.
- Logs contain the tool name, the space and a hash of `sub`. They never contain message text.

### Limit: isolation from the operator

**Isolating users from each other does not isolate them from whoever runs the server.** The operator holds everyone's refresh tokens and can, in principle, read their chats. The risk can be reduced but not removed:
- token encryption with a KMS;
- controlled, audited database access;
- minimal scopes.

This is another reason to prefer one instance run by each organisation.

For the same reason, **domain-wide delegation** (a service account that can impersonate any user) is not recommended. It simplifies authentication, but a server compromise then gives access to the whole domain.
