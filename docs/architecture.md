# Architecture

## Contents

- [1. Purpose](#purpose)
- [2. Definitions](#definitions)
- [3. Storage](#storage)
- [4. Plans](#plans)
  - [4.1 Projects and versions](#members)
  - [4.2 Dependencies and parents](#dependencies)
- [5. Identity](#identity)
- [6. Components](#components)
  - [6.1 Redmine and its clients](#front-ends)
  - [6.2 Process boundary](#process-boundary)
  - [6.3 Platform](#platform)
- [7. Operations](#operations)
  - [7.1 Start](#start)
  - [7.2 Go and Ship](#ship)
  - [7.3 Finish](#finish)
  - [7.4 Tool commits](#tool-commits)
  - [7.5 Baselines](#freeze)
  - [7.6 Next up](#ready-file)
- [8. Failure modes](#failure-modes)
- [9. Views](#views)
- [10. Invariants](#invariants)

**Requirements:** [`requirements.md`](requirements.md) · **Decisions:** [`adr/`](adr/) ·
**Failure modes by command:** [`failure-modes.md`](failure-modes.md)

<a id="purpose"></a>
## 1. Purpose

GH Plan plans work across several repos in Redmine, which runs outside them
([ADR 0011](adr/0011-redmine-is-the-tracker.md)). The gh-plan repo is the setup around it:
Redmine 7.0 in Docker with its projects, fields and workflow (`redmine/`), the lane board plugin,
and a VS Code extension (`vscode/`) that starts, ships and finishes an issue from a panel. `pr`,
the gh-issues skill's script, commits, pushes and opens or updates the pull request. GitHub Issues
is not used, and pull requests are the one GitHub integration.

<a id="definitions"></a>
## 2. Definitions

| Term | Definition |
|---|---|
| Issue | A Redmine issue, `#<n>`, numbered across every project. The unit of planned work |
| Project | One Redmine project per repo, under the parent project `plan` (§4.1) |
| Version | A milestone: a Redmine version in `plan`, shared with its tree, so it can span repos |
| Default branch | The branch GitHub has configured for the repo: `main`, or `develop` under git-flow. It carries the code |
| Tool commit | None: the tool makes no commits of its own, and every commit reaches the default branch through a pull request (§7.4) |
| Start | Setting an issue In Progress from Next up, which makes its branch and worktree and records its start date (§7.1) |
| Round | One pass of review: the commit message, the PR body with its ticked Done list, and the review replies in `.tmp/<n>/` that `pr` sends (§7.2) |
| Finish | The squash merge of the issue's PR, then Done, which closes the issue (§7.3) |
| Lane | A contention domain, the `Lane` custom field. Issues in a lane touch the same files, so they run one at a time. Single-valued |
| Points | The estimate, the `Points` custom field: 1, 2, 3, 5, 8 or 13 |
| Baseline | `Baseline start` and `Baseline finish`, a frozen pair of dates on an issue, changed only by the administrator (§7.5) |
| Icebox | An open status for issues not planned yet. Next up and the lane board leave it out |
| Next up | Redmine's public saved query in `plan`: open issues, Icebox left out, in `Ordinal` order (§7.6) |

<a id="storage"></a>
## 3. Storage

Redmine's database is the source of truth. It is SQLite, `redmine.db` in the named volume
`gh-plan-redmine_db`, with attachments in `gh-plan-redmine_files`
([`redmine/README.md`](../redmine/README.md), *Where the data lives*). It holds every issue with its
fields, relations and journal. The repos it plans hold no tracker files.

Each repo's `.git` is mounted read-only into the container, so Redmine reads its commits and links
each one that names `#<n>` to that issue. The `Pull requests` field holds each PR of an issue as a
Markdown link.

A checkout's `.tmp/` is ignored by git. `.tmp/<n>/` holds a round's files for issue `<n>` until
`pr` sends and deletes them, and the extension's Done refuses to remove a worktree that still holds
any it hasn't sent.

The two keys live only in the macOS Keychain: the REST API key in `redmine-gh-plan`, and the
repository web service's key in `redmine-sys-api` ([`G3`](requirements.md)). A nightly launchd job
backs up the database and attachments, and exports every issue as JSON, to a dated folder on the
USB drive ([`G2`](requirements.md); [`redmine/README.md`](../redmine/README.md), *Backup and export*).

<a id="plans"></a>
## 4. Plans

<a id="members"></a>
### 4.1 Projects and versions

One Redmine holds the plan. The private parent project `plan` has one sub-project per repo,
`gh-plan` and `tempotag`, each with the issue tracking, repository, Gantt, calendar and lane board
modules, and each repo is registered as its project's Git repository. Milestones are versions in
`plan`, shared with its tree, so one version spans every repo ([`A1`, `A2`](requirements.md)).
`redmine/setup.rb` makes all of this, and running it again changes nothing that already matches.

<a id="dependencies"></a>
### 4.2 Dependencies and parents

A dependency is Redmine's `blocks` relation. Cross-project relations are on, so an issue can be
blocked by one in another repo's project, and Redmine refuses a relation that would close a cycle
([`E1`, `E2`](requirements.md)). Start warns when an open issue blocks the one being started.

A child is a sub-task ([`A4`](requirements.md)). The lane board totals the points of leaf issues
only, so a parent never counts its sub-tasks twice ([`A5`](requirements.md)). An issue is worked
in its own project's repo, so a change it needs in another repo is a sub-task in that repo's
project.

<a id="identity"></a>
## 5. Identity

An issue is `#<n>`, numbered by Redmine across every project. Its branch is `<type>/<n>-<slug>`,
with `feat/`, `fix/` or `chore/`, and its worktree is `<repo>.worktrees/<n>` beside the main
checkout. A commit message names the issue with `refs #<n>`, which links the commit, or
`fixes #<n>`, which also closes the issue when Redmine fetches the commit. GitHub also links `#<n>`
in a commit message to the repo's own issue or pull request `<n>`, so a reference can point at
both 🔍.

An issue imported from a repo's former `.tracker/` keeps its old ID in the searchable `Old ID`
field.

<a id="components"></a>
## 6. Components

<a id="front-ends"></a>
### 6.1 Redmine and its clients

Redmine serves the web UI, the REST API and the repository web service. Everything else is a
client of one of them:

- **The extension** shows Next up in a panel, and holds the Start, Open, Go, Ship and Done
  buttons ([`vscode/README.md`](../vscode/README.md)).
- **`pr`** commits, pushes, opens or updates the PR, and posts review replies. After a merge it
  sends only the replies and the PR body, and leaves the cleanup to Done. It and its Python scripts live in
  `.claude/skills/gh-issues/scripts/`, with their tests. `~/.claude/skills/gh-issues` links to the
  main checkout's copy, so every repo uses the one skill and `~/.local/bin/pr` reaches it.
- **The setup scripts**, `setup.rb`, `import.rb` and `backfill.rb`, run inside the container with
  `rails runner`, on the database directly, so they need no API key.
- **The lane board plugin** adds the Lanes tab. It only reads.
- **`redmine-get`**, in `~/.local/bin/`, sends one GET with the Keychain key, so an agent can read
  issues without seeing the key.

<a id="process-boundary"></a>
### 6.2 Process boundary

`vscode/redmine.js` and `vscode/start.js` are the only extension modules that spawn a process or
open a network connection, apart from Ship's `pr`, which `vscode/extension.js` runs as a VS Code
task in a terminal of its own. The rest of the extension is plain functions, testable without a
network or a repo.

- `vscode/redmine.js` makes every call to Redmine. Through the REST API it reads the panel's
  issues and writes Start's and Done's status changes, Ship's PR link and their journal notes.
  Through the repository web service it has Redmine fetch a project's commits after a merge. It
  reads the API key from the Keychain once per client, and the web service's key on each call, so
  no repo holds either ([`G3`](requirements.md)). Next up keeps one client across refreshes until
  the `plan.*` settings change; each command makes its own.
- `vscode/start.js` runs git, `gh` and `uv` for Start, Go, Ship and Done: it fetches, adds and removes
  worktrees, syncs them, reads PRs, and pulls and deletes merged branches.

`gh` is already authenticated, so nothing holds a GitHub token ([`G3`](requirements.md)); a library
such as Octokit would need one. Outside the repo, `pr` writes to GitHub through `gh` and to Redmine
through the REST API, with the same Keychain keys.

<a id="platform"></a>
### 6.3 Platform

| Layer | Choice | Why |
|---|---|---|
| Tracker | Redmine 7.0, pinned to an exact tag, in Docker on localhost | [ADR 0011](adr/0011-redmine-is-the-tracker.md) |
| Database | SQLite, in a named volume | [ADR 0011](adr/0011-redmine-is-the-tracker.md) |
| Theme | Opale, pinned by URL | [`redmine/README.md`](../redmine/README.md) |
| Plugin and setup | Ruby, on Redmine's plugin API and `rails runner` | [ADR 0011](adr/0011-redmine-is-the-tracker.md) |
| Editor integration | A VS Code extension in JavaScript, tested with `node --test` | [`vscode/README.md`](../vscode/README.md) |
| Pull requests | Subprocess to `gh` | Requirement [`G3`](requirements.md) |
| Keys | The macOS Keychain | Requirement [`G3`](requirements.md) |

<a id="operations"></a>
## 7. Operations

```
   Next up                       worktree <repo>.worktrees/<n>             Redmine #<n>
   ───────                       ─────────────────────────────             ────────────
   (1) START ──────────────────▶ branch <type>/<n>-<slug>, off the base ─▶ In Progress,
                                                                            start date, note
                                 work · .tmp/<n>/commit.txt, pr-body.md
   (2) GO, then SHIP ──────────▶ pr <n>: commit, push, PR ───────────────▶ Pull requests
                                 PR body: the Done list, ticked
                                 review rounds: replies/
   (3) squash merge, title ends (fixes #<n>)
   (4) DONE ───────────────────▶ worktree and branch removed, base up ───▶ commits fetched,
                                                                            Closed, note
```

The default branch carries the code, and every change reaches it through a pull request. An
issue's status lives in Redmine, and only Start, Done and a fetched `fixes #<n>` commit change it.
The issue keeps the spec, its Lenses and Done list; the PR's body records which of those its work
met, beside the diff, tests and review that prove them
([ADR 0011, *Amendment: the verification record*](adr/0011-redmine-is-the-tracker.md#record)).
Every write to GitHub or Redmine happens when Peter presses a button or runs `pr`
([ADR 0011, *Amendment: the flow*](adr/0011-redmine-is-the-tracker.md#flow)).

<a id="start"></a>
### 7.1 Start

Start shows on a New issue's row in Next up. It warns when an open issue blocks this one, then
makes the branch `<type>/<n>-<slug>` and its worktree off the latest `origin/develop`, or
`origin/main`, and sets the issue In Progress with a journal note. It writes the start date only
when the issue has none, so an issue's actual start is its first ([`C5`](requirements.md)). It
opens the worktree in a new window with Claude Code beside it.

If the issue's branch already exists here or on `origin`, Start reuses it on its own base. On an
In Progress row the button is Open, which reopens the worktree, making it the same way if this
machine has none. Start refuses an issue whose status has moved on from New or In Progress since
the panel loaded. A lock file per issue keeps two windows from starting it at once
([failure modes, *Start*](failure-modes.md)).

<a id="ship"></a>
### 7.2 Go and Ship

A round is written into `.tmp/<n>/`: `commit.txt` when the tree has changes, `pr-body.md`, whose
`## Done` section copies the issue's Done list and ticks the lines the PR meets, and a reply to
each review thread in `replies/<comment id>.md`. Once the PR body and something beside it are
written, the extension opens them all for review.

Go, in the title bar of any of them, saves them and refuses by name a missing PR body, a missing
commit message while the tree has changes, or a branch that isn't the issue's. It then stages every
change, as `pr`'s commit will, and opens them, so the staged list is what Ship commits. Only Ship
runs `pr`: it saves and checks again, refuses by name any file changed since Go staged the tree,
then runs `pr <n>` in a terminal in the worktree. Under Ship, `pr` commits the index Go staged and
refuses a change made since, rather than staging again. Go and Ship refuse a worktree file with
unsaved edits, which neither the stage nor the commit would hold.

`pr` commits with `commit.txt`, pushes, and opens the PR or replaces its body. It then posts each
reply to its thread and resolves the thread. Each reply is deleted once sent, so a second `pr`
sends nothing twice. After the merge, `pr` sends only the replies and the PR body, so a Done line
ticked after the merge reaches the PR. The issue's description is never written: an `issue.md` left from before the
PR held the Done ticks is named as no longer sent, and left where it is. After the first `pr`, the
issue's `Pull requests` field links the PR, with a journal note. A `pr` whose push adds commits
to an open PR also asks Copilot to review it again, for the commits that answer a review; `gh pc` asks for a new PR.
`gh pr edit --help` (2.102.0) says `--add-reviewer` adds or re-requests a reviewer.

<a id="finish"></a>
### 7.3 Finish

The finish is the squash merge. GitHub makes the PR's title the squash commit's subject, so a PR
that finishes issue `<n>` ends its title `(fixes #<n>)`, and one that does part of it
`(refs #<n>)`. Redmine closes the issue when it fetches that commit.

Done shows beside Open on an In Progress row that links a PR. Once the PR has merged, it removes
the issue's worktree, brings the main checkout's base up to the merge, deletes the branch, has
Redmine fetch the commits, and closes the issue with a note naming the PR that holds its Done
record, `Done: verified in gh-plan#117`, and linking its merge commit.
It checks everything before it changes anything, and Done again finishes what a stopped one left
([failure modes, *Done*](failure-modes.md)).

<a id="tool-commits"></a>
### 7.4 Tool commits

The tool makes no commits of its own. An issue's status lives in Redmine
([ADR 0011](adr/0011-redmine-is-the-tracker.md)): the extension's Start sets it In Progress, Done
closes it after the merge, and a `fixes #<n>` commit closes it when Redmine fetches the commit.
Nothing reaches the default branch except through a pull request.

<a id="freeze"></a>
### 7.5 Baselines

`Baseline start` and `Baseline finish` are date fields on each issue. `setup.rb` makes them
read-only in workflow for every role but `Baseline editor`, a role with no members, so only the
administrator, who takes every role in workflow checks, can change them. The journal keeps each
change with its note ([`C3`, `C4`](requirements.md)). The administrator sets them by hand; a
script that computes a milestone's baselines over the REST API is not built yet.

<a id="ready-file"></a>
### 7.6 Next up

Next up is a public saved query in `plan`: open issues, Icebox left out, in `Ordinal` order. It
answers "what's next" in Redmine's web UI, in the extension's panel and over the REST API
([`D6`](requirements.md)). The panel reloads it whenever its window comes into focus, so it shows
what another window, the web UI or `pr` changed. A focus within five seconds of the last refresh, or while one has waited on Redmine for under three minutes, doesn't reload it again. The lane board marks an issue ready when it is open and every issue
that blocks it is closed.

<a id="failure-modes"></a>
## 8. Failure modes

Each command that writes has its own table in [`failure-modes.md`](failure-modes.md). The events
they share:

| Event | Effect | Handling |
|---|---|---|
| Redmine is down, or the Mac is off | Next up can't load, and nothing can start or finish | Start and Done refuse before they change anything |
| No API key in the Keychain | No call to Redmine can be made | Refused, naming the Keychain item to store the key in |
| Redmine answers 204 but drops a status it won't allow | The status is unchanged, though the note is saved | The write reads the issue back, refuses, and names the likely rule ([failure modes](failure-modes.md#redmine-drops-a-status-it-wont-allow)) |
| A `fixes #<n>` commit is merged | Redmine sees it only at its next fetch | Done has the web service fetch; without its key, the Repository tab does |
| Two windows start or finish one issue | Both would change the same worktree | A lock file per issue refuses the second |
| A review reply is still unsent at Done | Removing the worktree would delete it | Done refuses before it changes anything, and names it |
| The worktree's `pr-body.md` differs from the PR's body at Done | Removing the worktree would delete a Done tick the PR lacks | Done refuses before it changes anything, naming `pr done <n>` |
| `#<n>` in a commit message | GitHub links it to the repo's own issue or PR `<n>` | None; Redmine's link is the one that counts 🔍 |

<a id="views"></a>
## 9. Views

Redmine's own pages are the views:

| View | Shows |
|---|---|
| Issue list | Issues with their fields, the `Pull requests` column among the defaults |
| Next up | The open issues in order, in Redmine and in the extension's panel (§7.6) |
| Lanes tab | The lane board: lanes as rows and statuses as columns, each lane's open points, and a ready mark on each open issue nothing open blocks |
| Gantt | Each project's issues and versions over time |
| An issue's page | Its description, relations, sub-tasks, linked commits and journal |

The Lanes tab is at `/projects/<identifier>/lanes`, and has no Icebox column
([lane board](../redmine/plugins/lane_board/README.md)).

<a id="invariants"></a>
## 10. Invariants

1. Redmine is the tracker. Its database holds every issue, with its status and history, outside
   the repos it plans, and nothing tracker-related is committed in them
   ([ADR 0011](adr/0011-redmine-is-the-tracker.md)).
2. The default branch carries the code, and every change reaches it through a pull request. An
   issue's status lives in Redmine: the extension's Start and Done set it, and a `fixes #<n>`
   commit closes the issue when Redmine fetches it (§7.4). A Done tick is a verification record,
   not status: it is recorded in the PR's body, and status in Redmine.
3. Retired with the tool's `plan:` commits ([ADR 0011](adr/0011-redmine-is-the-tracker.md)), so the
   invariants after it keep their numbers.
4. An issue's start date is written once: Start sets it only when the issue has none (§7.1).
5. Retired, so the invariants after it keep their numbers. Invariant 6 bounds remote writes,
   which include Redmine's REST API ([ADR 0011](adr/0011-redmine-is-the-tracker.md)).
6. `vscode/redmine.js` and `vscode/start.js` are the only extension modules that spawn a process
   or open a network connection, apart from the VS Code task in `vscode/extension.js` that runs
   Ship's `pr`. Every call the extension makes to Redmine goes through `redmine.js` (§6.2).
7. Points are 1, 2, 3, 5, 8 or 13. An issue above 5 points is split before it is scheduled, as a
   practice: nothing refuses the save ([`B3`](requirements.md)).
8. An issue has at most one lane: `Lane` is a single-valued list ([`D4`](requirements.md)).
9. A parent's points never count twice: the lane board totals leaf issues only
   ([`A5`](requirements.md)).
10. Only the administrator can change a baseline, and the journal keeps each change (§7.5,
    [`C3`](requirements.md)).
11. Retired while tracked time is paused ([ADR 0006](adr/0006-tracked-time.md)), so the invariants
    keep their numbers.
