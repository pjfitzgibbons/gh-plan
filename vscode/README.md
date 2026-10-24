# GH Plan for VS Code

A **Plan** icon in the Activity Bar opens **Next up**: Redmine's saved query of that name, one row per
issue, in the query's order. It refreshes whenever its window comes into focus, so closing a
worktree's window, or coming back from Redmine or a terminal, shows the issues as they are now. A focus within five seconds of the last refresh, or while one has waited on Redmine for under three minutes, is skipped. Open rows stay open, their detail reloads in place, and the scroll stays where it was. While Redmine answers, a progress bar runs across the top of the panel, as
Source Control's does on a Git refresh.

- **A row** shows the repo marker, the number, the points, the subject, and the status when it isn't
  New. A click anywhere on it opens the issue in place; the issue loads when it first opens.
- **Start** shows when the pointer is over a New issue's row. It warns when an open issue blocks this
  one, then makes the branch `<type>/<n>-<slug>` and its worktree in `<repo>.worktrees/<n>` beside the
  checkout, off the latest `origin/develop` (or `origin/main`), sets the issue In Progress with a
  journal note, and opens the worktree in a new window, with the issue's Redmine page in an Integrated
  Browser tab in the leftmost editor group and Claude Code beside it on the right. If the page fails to
  open, an error names its URL and Claude starts anyway. An Open after Claude failed to start opens both
  again. The issue's branch,
  if it already has one here or on `origin`, is reused on its own base. On an In Progress row the same
  button is **Open**, which reopens the worktree, making it the same way if this machine has none, and
  changes nothing in one already set up. Start refuses an issue whose status has moved on from New or
  In Progress since the panel loaded.
- **The code index:** when the main checkout has a [cocoindex-code](https://github.com/cocoindex-io/cocoindex-code)
  index (`.cocoindex_code/settings.yml`), `ccc` is on PATH and the repo ignores `.cocoindex_code/`,
  Start's setup copies `settings.yml` into the new worktree, and nothing else: the worktree's first
  `ccc` search, or `ccc index`, builds its own index from the branch's files. Done runs
  `ccc reset -f` in the worktree before removing it, once a search has built its index, so the `ccc`
  daemon lets go of its databases.
  A missing index or `ccc` is a note, never a failed Start. Run `ccc init` once in the main checkout to turn
  it on.
- **A link** opens in an Integrated Browser tab.
- **Review before `pr`:** once an issue's `.tmp/<n>/pr-body.md` is written with something beside
  it for `pr` to send, they open as tabs in the leftmost editor group (or the only one), with no
  split, the PR body in front; groups to its right stay as they are. Beside it can be the commit
  message, `commit.txt`, and a reply to each review finding, `replies/<comment id>.md`, which `pr`
  posts to the finding's thread before resolving it. The PR body's `## Done` section ticks the
  issue's Done list; the issue's description in Redmine is never ticked.
  When all are already open, a later write or save changes no layout.
- **Go** is the diff icon in the title bar of any of them. It saves them all, refuses by name a
  missing PR body, a missing commit message while the tree has changes, a reply `pr` can't send, an empty
  reply or two replies to one thread, or a branch that isn't the issue's. It then stages every change
  and opens them (Git: View Staged Changes), so what it shows is what Ship commits. With nothing to
  commit, it opens nothing.
- **Ship** is the rocket that Go then puts in the changes' title bar, and on the review files. Only
  Ship runs `pr`: it saves and checks again, refuses by name a file changed since Go staged the
  tree, so nothing goes in unreviewed, then runs `pr <n>` in a terminal in the worktree. That `pr`
  commits what Go staged, and refuses a change made since. Go and Ship both refuse a worktree file
  with unsaved edits.
  Once `pr` succeeds, the review files, the Changes view and any diffs of the worktree's files
  close; `pr` has deleted each reply once sent, and asked Copilot to review the PR
  if the push added commits (`gh pc` asks when it opens one). `gh pr edit --help` says `--add-reviewer` re-requests a reviewer. The issue's Pull requests field links the PR, with a journal note, and Next up
  refreshes. Edits made to a reply while `pr` ran, which it
  never saw, are kept beside it as `<file>.unsent` before its tab closes. Go and Ship
  again run `pr` again, which updates the PR body, and leave Redmine as it is once the PR is linked.
- **Done** shows beside Open on an In Progress row that links a PR. Once that PR has merged, it
  removes the issue's worktree, brings the main checkout's base up to the merge, deletes the
  branch, has Redmine fetch the commits, and closes the issue with a note naming the PR that holds
  its Done record and linking its merge commit. It refuses an unmerged PR, a worktree with changes or commits the PR lacks, and
  one holding any file but a dotfile in a `.tmp/<n>/replies/`, that `pr` hasn't sent, for any issue, before it changes anything. It also refuses a `.tmp/<n>/pr-body.md`, for any issue, that differs from its issue's PR body or whose issue links no PR, so a Done line ticked after the merge isn't lost: `pr done <n>` sends it. An `issue.md` left from before is never sent, and goes with the worktree. The main checkout leaves its branch only when that branch is the
  issue's; a branch whose tip moved past the PR's head is kept and named. Done again finishes what
  a stopped one left, and otherwise changes nothing. Then close the worktree's window.

## Settings

| Setting | Default | |
|---|---|---|
| `plan.redmineUrl` | `http://127.0.0.1:3010` | The Redmine it reads. |
| `plan.project`, `plan.query` | `plan`, `Next up` | The project and saved query shown. |
| `plan.projectMarkers` | `Pl`, `tt`, `ms` | The marker per Redmine project identifier. |
| `plan.checkouts` | gh-plan, tempotag under `~/programming` | The main checkout per Redmine project identifier, for Start. |
| `plan.disposable` | `.tmp/`, `.cocoindex_code/`, `node_modules/`, `.venv/`, caches, `dist/`, `build/` | The ignored files Done may delete with an issue's worktree, by name at any depth; `name/` is a folder. Done refuses a worktree holding any other. |
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
break it. The page lands in the leftmost group under `workbench.browser.newTabPlacement`'s default,
`activeGroup`; `sideGroup` or `window` puts it in a group of its own.
