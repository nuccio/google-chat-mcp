# Remote connector: research notes

Research for [issue #29](https://github.com/nuccio/google-chat-mcp/issues/29): make the server available as a remote Claude connector, so users don't need to install or run anything locally.

These documents cover the **technical requirements research** phase. They are not the design yet. The final design will be written once the open points below are decided.

## Questions addressed

1. If the server is available as a connector, there is a single instance serving many users. What does that imply?
2. With multiple users, how does each user choose their spaces and their per-space permissions?
3. How do we guarantee that no user can see another user's configuration?

## Decision so far

**Per-user configuration goes through a settings web page (`/settings`) hosted by the server.** Users sign in to it with Google, using the same identity as the connector. The model has no tool that can change the ACL.

The alternatives considered were:
- an MCP tool with elicitation;
- connector URL parameters;
- an admin-only policy.

They were rejected or kept only as a complement. The reasoning is in [01-requirements-analysis.md](01-requirements-analysis.md#2-how-each-user-chooses-spaces-and-permissions).

## Documents

| File | Content |
|------|---------|
| [01-requirements-analysis.md](01-requirements-analysis.md) | First pass on the three questions: impact on the current code, options for per-user configuration, isolation requirements |
| [02-settings-page-direction.md](02-settings-page-direction.md) | The three questions re-examined after choosing the settings page: architecture, onboarding, page behaviour, web-specific threats |

## Open points

| # | Question | Status |
|---|----------|--------|
| 1 | Who operates the server: one instance serving several organisations, or one instance per Google Workspace organisation? | Open. Recommendation: one per organisation |
| 2 | Storage backend: Postgres (Cloud SQL) or Firestore? It must hold the proxy's token store, web sessions and the ACL | Open |
| 3 | Local stdio mode: keep it alongside remote mode, or replace it? | Open. Recommendation: keep both behind an `AclProvider` interface |
| 4 | Should the settings page allow *read* access to direct messages? | Open |
| 5 | Require recent re-authentication before granting write permission? | Open. Only worth it for large deployments |
| — | Channel for per-user configuration | **Decided:** settings page (option A) |
| — | Initial configuration of a new user | **Decided:** empty ACL, tools point the user to `/settings` |
