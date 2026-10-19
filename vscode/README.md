# GH Plan for VS Code

A **Plan** icon in the Activity Bar opens **Next up**: Redmine's saved query of that name, one row per
issue, in the query's order.

- **A row** shows the repo marker, the number, the points, the subject, and the status when it isn't
  New. A click anywhere on it opens the issue in place; the issue loads on the first open only.
- **Start** shows when the pointer is over a New issue's row. It warns when an open issue blocks this
  one, then makes the branch `<type>/<n>-<slug>` and its worktree in `<repo>.worktrees/<n>` beside the
  checkout, off the latest `origin/develop` (or `origin/main`), sets the issue In Progress with a
  journal note, and opens the worktree in a new window with Claude Code beside it. The issue's branch,
  if it already has one here or on `origin`, is reused on its own base. On an In Progress row the same
  button is **Open**, which reopens the worktree, making it the same way if this machine has none, and
  changes nothing in one already set up. Start refuses an issue whose status has moved on from New or
  In Progress since the panel loaded.
- **A link** opens in an Integrated Browser tab.
- **Review before `pr`:** once an issue's `.tmp/<n>/commit.txt` and `.tmp/<n>/pr-body.md` are both
  written, they open stacked in the leftmost editor group (or the only one), the commit message
  above the PR body; groups to its right stay as they are. When both are already open, a later
  write or save changes no layout.

## Settings

| Setting | Default | |
|---|---|---|
| `plan.redmineUrl` | `http://127.0.0.1:3010` | The Redmine it reads. |
| `plan.project`, `plan.query` | `plan`, `Next up` | The project and saved query shown. |
| `plan.projectMarkers` | `Pl`, `tt`, `ms` | The marker per Redmine project identifier. |
| `plan.checkouts` | gh-plan, tempotag under `~/programming` | The main checkout per Redmine project identifier, for Start. |
| `plan.keychainService` | `redmine-gh-plan` | The Keychain item holding your Redmine API key. |

The API key lives only in the Keychain. Store it once, from Redmine's *My account* page:

```sh
security add-generic-password -U -s redmine-gh-plan -a "$USER" -w
```

## Build, install, test

From this folder:

```sh
npm install
npm test
npm run lint
npx -y @vscode/vsce@3 package --allow-missing-repository --skip-license -o /tmp/gh-plan.vsix
code --install-extension /tmp/gh-plan.vsix
```

Then run **Developer: Reload Window**. Start's Claude step calls Claude Code's undocumented
`claude-vscode.terminal.open` command, read from its source (2.1.301), so a Claude Code update may
break it.
