# 03 — Settings UI: user flow

The user flow that the design of the `/settings` page (issue #51) must follow. It covers what the user sees and does when giving the remote connector access to a space or a direct message. How the server serves the page is in #52. How the MCP server enforces the ACL is in #53.

It builds on [02-settings-page-direction.md](https://github.com/nuccio/google-chat-mcp/blob/7676cc8fa1131acb9605333fa1b17be5baf5109e/docs/remote/02-settings-page-direction.md): Google sign-in with the connector's identity (`sub`), no Google credentials stored by `/settings` beyond the session. The pages are built with NiceGUI ([04-settings-server-design.md](04-settings-server-design.md), #52), not with server-side templates as 02 assumed.

Decisions taken on #29 on 2026-11-04 and applied here: no re-authentication before granting write (step 6), and the Direct messages tab shows only a "not available yet" notice (D4).

---

## Decisions

| # | Decision | Reason |
|---|---|---|
| D1 | **Two separate parts:** the list of enabled entries, and a picker to add one. There is no single page listing every space with checkboxes. | The ACL is an allowlist: each entry is an explicit choice. With 100+ rows of checkboxes it is easy to grant write access to the wrong row. The list shows at a glance what Claude can reach. |
| D2 | **Read and write are independent**, as in local mode. Write has two modes: with approval (`send_message`) and unattended (`send_message_unattended`). | Same model as `--space NAME:PERMS[:unattended]` ([configuration.md](../configuration.md)). Write-only spaces are useful for output-only spaces. |
| D3 | **Two views: Spaces and Direct messages.** Each has its own enabled list and its own picker. | The two have different permissions (DMs: read only) and are identified differently (DMs have no display name). |
| D4 | **Direct messages (and group chats) are designed but not implemented in v1.** Read only; write is never offered. | The MCP server blocks sends to anything other than `spaceType == SPACE`. Reading DMs is left for a later version. |
| D5 | **The link the MCP tools return is static** (`{SETTINGS_BASE_URL}/settings`, the address of the settings service), with no parameters. | A link carrying a space ID would let the model, or text injected into it, choose which space the user is about to grant. |
| D6 | **The server re-validates every save.** The form is never trusted. | A hand-edited form must not get past the rules shown in the UI. |

---

## Navigation

```
[ Spaces ]  [ Direct messages ]                 alice@example.com · [Sign out]
```

- `/settings` opens the **Spaces** view.
- `/settings/dms` is the **Direct messages** view, covering `DIRECT_MESSAGE` and `GROUP_CHAT`. **In v1 it shows only a notice** (D4), and lists nothing:
  > Direct messages and group chats are not available yet. Claude cannot read or post in them.
- The account bar is on every page.

---

## Flow: adding a space

### 1. Entry

The user reaches `/settings` in one of two ways:
- from the link returned by the MCP tools (`list_spaces` with an empty ACL, or the error for a space that is not configured);
- by opening the address directly.

### 2. Sign-in

- OIDC with `openid email chat.spaces.readonly`, `access_type=online`. The `hd` claim is checked against the allowed domain.
- The account bar shows which account is being configured and whether the connector uses it:
  > Configuring **alice@example.com** · Connector linked to this account, last used: 2 hours ago · [Switch account]
- If this `sub` has never used the connector, a warning is shown. It does not block the user, because configuring before first use is normal:
  > Claude has not used this account yet. If you linked a different Google account in Claude, these settings will have no effect.
- **[Switch account]** restarts the sign-in with `prompt=select_account`.

### 3. Enabled spaces (`GET /settings`)

```
Enabled spaces                                          [+ Add space]
────────────────────────────────────────────────────────────────────
Team General   spaces/AAABBBCCC   Read · Write                 [Edit]
Announcements  spaces/DDDEEEFFF   Read                         [Edit]
Reports        spaces/REPORTS01   Write without confirmation   [Edit]
Old project    spaces/ZZZ999      ⚠ You are no longer a member [Remove]
```

- **Unattended write** is shown as a permission of its own, "Write without confirmation", not as plain "Write".
- When at least one space accepts writes without confirmation, a notice stays at the top of the list:
  > N spaces accept messages without confirmation. Set `send_message_unattended` to "Always allow" in Claude only if you need it for scheduled tasks.
- **Orphaned entries** are spaces in the ACL that Google no longer returns, because the user left or the space was deleted. They only offer **[Remove]**.
- **Empty state:**
  > Claude cannot access any space yet. Add the spaces you want to make available: for each one, choose whether Claude can read, write, or both.
  >
  > [Add your first space]

### 4. Picking the space (`GET /settings/spaces/add?q=…`)

```
Add space
[ search by name…        ] [Search]
Or paste a Google Chat link or space ID: [                    ] [Use]

Team Marketing   spaces/MMM111                      [Choose]
Team General     spaces/AAABBBCCC   already enabled → [Edit]
Team General     spaces/QQQ222                      [Choose]
```

- **Listed spaces** come from `spaces.list` with the session's token, filtered to `spaceType == SPACE`.
- **Search** filters the list as the user types, on the server, by `displayName`. The list is fetched once when the page is built.
- **The resource name is always shown.** Display names are not unique, and anyone who creates a space chooses its name.
- **Spaces already enabled** cannot be chosen again; their row links to **[Edit]**.
- **Pasting a link or ID** is for when there are too many spaces with the same name. The server extracts `spaces/ID` and checks it with `spaces.get` using the session's token.

### 5. Permissions (`GET /settings/spaces/{id}`)

The same page is used to add and to edit.

```
Team Marketing (spaces/MMM111)

☐ Read     Claude can read messages and the member list
☐ Write    Claude can post messages as you
     ◉ with your approval in Claude          (send_message)
     ○ without confirmation, for scheduled tasks   (send_message_unattended)

[Cancel]  [Continue]
```

- **No permission is preselected.** Saving with none selected is refused with "Choose at least one permission". On an existing entry, removing every permission is done with **[Remove]**.
- **The write mode defaults to "with your approval".** It is disabled while Write is unchecked, and the server ignores it in that case anyway. A change carrying unattended without write is rejected, the same rule as `parse_space_arg`.
- **Options forbidden by the admin policy** are disabled, with the reason next to them, for example "Write not allowed by your administrator".

### 6. Confirmation

The confirmation page lists the grant in plain words, for example:
> You are allowing Claude to post in «Team Marketing» as alice@example.com.

**The risk depends on the whole ACL, not on one space.** Text read from any readable entry (spaces and, later, DMs) can steer posts to every unattended space ([Security considerations](../configuration.md#security-considerations)). The page therefore shows these warnings when they apply:

- **Granting unattended write while other entries are readable:**
  > Messages read from these N spaces (Team General, Announcements, …) can lead Claude to post in «Reports» without asking you.
- **Granting read while unattended spaces exist:**
  > Text in «Team Marketing» will be able to make Claude post, without confirmation, in: Reports.
- **Read and unattended write on the same space:** the strongest warning on the page. It replaces the startup warning of local mode.

When the confirmation page is shown:

| Change | Confirmation page |
|---|---|
| Read, no unattended spaces in the ACL | no |
| Read, with unattended spaces in the ACL | yes |
| Write with approval | yes |
| Write without confirmation | yes |
| Removing permissions, or removing an entry | no |

**No re-authentication** (decision on #29). The confirmation keeps the LLM under control: the model cannot change the ACL, and a person decides after reading the warnings. It is not a security barrier against someone who already controls the user's browser session.

### 7. Saving

Before writing, the server checks again:
1. the user is a member of the space (`spaces.get` with the session's token);
2. `spaceType == SPACE`. In v1 this rejects every DM and group chat, so a hand-edited form cannot add one;
3. the admin policy allows each requested permission;
4. if the change requires confirmation, it happened in this session for this exact change, on an ACL that has not changed since.

Then:
- the ACL entry and its audit record (before → after, including the unattended flag) are written in the same transaction;
- the page goes back to the list with a message:
  > «Team Marketing» added: Read. Claude sees it from its next request; nothing needs restarting.

### 8. Errors

| Case | What the user sees |
|---|---|
| Session expired during the flow | The user signs in again and returns to the list. The interrupted step is not resumed, so a write grant is never left half done. |
| No longer a member, or space deleted between picking and saving | "You are no longer a member of this space", then back to the picker |
| Permission forbidden by the policy (form edited by hand) | Generic rejection; the permissions page is shown again |

---

## Direct messages view (not implemented in v1)

Kept here so that the design leaves room for it.

| | Spaces | Direct messages |
|---|---|---|
| Name shown | `displayName` + resource name | member names (from `memberships.list`) + resource name |
| Permissions | Read, Write (with approval / without confirmation) | Read only |
| Confirmation | per the table in step 6 | always |
| Search | by space name | by member name or email |

- The DM permissions page has only **Read**. There are no disabled write options to explain.
- It carries a warning:
  > Claude will read private conversations. Text read here can also influence messages to spaces without confirmation.
- Adding a DM for reading counts as a readable source in the step 6 warnings.
- **Cost:** naming DMs needs one `memberships.list` call per DM. To be measured when the view is implemented.

### Consequence for the MCP server (#53)

- In remote mode the MCP server also refuses **reads** of anything other than `spaceType == SPACE` until this view is implemented. This applies even when the entry is in the ACL, for example after a direct edit of the database.
- Local mode keeps accepting DMs for reading with `--space spaces/DM:r`, as it does today.
- The difference is intended: in local mode the configuration is written by whoever starts the process.
- **Where the rule lives (NFR-6 on #29):** the tools do not know which mode they run in, so the refusal is not a check in the tools. It belongs to the remote `AclProvider`, which does not grant read on an entry whose space is not a named space. The local `AclProvider` (from `--space`) keeps granting it.
- The two modes are alternatives for a Claude client, not used together. The difference is documented for users moving from local to remote, in the migration guide of #59.

---

## Data model impact

On top of the model in [02](https://github.com/nuccio/google-chat-mcp/blob/7676cc8fa1131acb9605333fa1b17be5baf5109e/docs/remote/02-settings-page-direction.md#2-per-user-configuration-the-page):

```
space_acl   (sub FK, space_name, read, write, unattended, display_name, updated_at)   PK(sub, space_name)
            CHECK (NOT unattended OR write)
acl_audit   (id PK, sub FK, space_name, before, after, changed_at)      -- before/after include unattended
```

- `display_name` is a snapshot taken at save time, used only to label orphaned entries. It is untrusted text and never used for a decision.
- The storage is Firestore ([04](04-settings-server-design.md#data-model)): there is no `CHECK` constraint, and the same rule is enforced by the code that writes the ACL.

## To verify

- Whether Claude's remote connectors offer per-tool approval settings, as Claude Desktop does. The split between `send_message` and `send_message_unattended`, and the label "with your approval in Claude", depend on it.
