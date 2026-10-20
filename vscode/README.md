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
- **Review before `pr`:** once an issue's `.tmp/<n>/pr-body.md` is written with something beside
  it for `pr` to send, they open as tabs in the leftmost editor group (or the only one), with no
  split, the PR body in front; groups to its right stay as they are. Beside it can be the commit
  message, `commit.txt`; a reply to each review finding, `replies/<comment id>.md`, which `pr`
  posts to the finding's thread before resolving it; and `issue.md`, the issue's description with
  the Done boxes ticked, which `pr` applies to Redmine only when the boxes are all that changed.
  When all are already open, a later write or save changes no layout.
- **Ship** is the diff icon in the title bar of any of them. It saves them all, refuses by name a
  missing PR body, a missing commit message while the tree has changes, a reply `pr` can't send, an empty
  reply or two replies to one thread, or a branch that isn't the issue's, and opens the working tree's changes (Git: View Changes). With nothing to commit, it
  opens nothing.
- **Go** is the rocket that Ship then puts in the changes' title bar, and on the review files. Only
  Go runs anything: it saves and checks again, then runs `pr <n>` in a terminal in the worktree.
  Once `pr` succeeds, the review files, the Changes view and any diffs of the worktree's files
  close; `pr` has deleted each reply and `issue.md` once sent, and the issue's Pull requests field
  links the PR, with a journal note. Edits made to a reply or `issue.md` while `pr` ran, which it
  never saw, are kept beside it as `<file>.unsent` before its tab closes. Ship and Go
  again run `pr` again, which updates the PR body, and leave Redmine as it is once the PR is linked.
- **Done** shows beside Open on an In Progress row that links a PR. Once that PR has merged, it
  removes the issue's worktree, brings the main checkout's base up to the merge, deletes the
  branch, has Redmine fetch the commits, and closes the issue with a note linking the PR and its
  merge commit. It refuses an unmerged PR, a worktree with changes or commits the PR lacks, and
  one holding any file but a dotfile in a `.tmp/<n>/replies/`, or an `.tmp/<n>/issue.md` or `issue.md.unsent`, that `pr` hasn't sent, for any issue, before it changes anything. The main checkout leaves its branch only when that branch is the
  issue's; a branch whose tip moved past the PR's head is kept and named. Done again finishes what
  a stopped one left, and otherwise changes nothing. Then close the worktree's window.

## Settings

| Setting | Default | |
|---|---|---|
| `plan.redmineUrl` | `http://127.0.0.1:3010` | The Redmine it reads. |
| `plan.project`, `plan.query` | `plan`, `Next up` | The project and saved query shown. |
| `plan.projectMarkers` | `Pl`, `tt`, `ms` | The marker per Redmine project identifier. |
| `plan.checkouts` | gh-plan, tempotag under `~/programming` | The main checkout per Redmine project identifier, for Start. |
| `plan.disposable` | `.tmp/`, `node_modules/`, `.venv/`, caches, `dist/`, `build/` | The ignored files Done may delete with an issue's worktree, by name at any depth; `name/` is a folder. Done refuses a worktree holding any other. |
| `plan.pr` | `~/.local/bin/pr` | The `pr` script Ship runs, by path: a login shell finds `/usr/bin/pr` first. |
| `plan.keychainService` | `redmine-gh-plan` | The Keychain item holding your Redmine API key. |
| `plan.sysKeychainService` | `redmine-sys-api` | The Keychain item holding the key of Redmine's repository web service, for Done's fetch (`redmine/README.md`, *Running the setup*). |

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
