# Architecture

## Contents

- [1. Purpose](#purpose)
- [2. Definitions](#definitions)
- [3. Storage](#storage)
- [4. Plans](#plans)
  - [4.1 Member repos](#members)
  - [4.2 Dependencies and parents](#dependencies)
- [5. Identity](#identity)
- [6. Components](#components)
  - [6.1 Core and front ends](#front-ends)
  - [6.2 Process boundary](#process-boundary)
  - [6.3 Platform](#platform)
- [7. Operations](#operations)
  - [7.1 Start](#start)
  - [7.2 Stop](#stop)
  - [7.3 Finish](#finish)
  - [7.4 Tool commits](#tool-commits)
  - [7.5 Freeze](#freeze)
  - [7.6 `ready.yml`](#ready-file)
- [8. Failure modes](#failure-modes)
- [9. Views](#views)
- [10. Invariants](#invariants)

**Requirements:** [`requirements.md`](requirements.md) · **Decisions:** [`adr/`](adr/) ·
**Design:** [design 0001](design/0001-phase-1.md), [design 0002](design/0002-tracked-time.md),
[design 0003](design/0003-plan-board.md)

<a id="purpose"></a>
## 1. Purpose

GH Plan is a file-native tracker. Each item is a Markdown file with YAML front matter, committed in
the repo it belongs to, beside the code and docs it plans. The file holds the whole item: its
title, its description, its planning fields and its dependencies. Git records its history and its
progress. GitHub Issues is not used, and pull requests are the one GitHub integration
([ADR 0008](adr/0008-file-native-items.md)).

GitHub Projects v2 was the planning layer this replaces. Its fields live on the project item instead
of the issue, it has no dependency field and no readable rank, some fields are display-only, a board
cannot group by labels, and writing project fields trips a secondary rate limit at about twenty
mutations. Keeping Issues underneath and planning in files beside them meant syncing two halves of
every item, with only one half in the repo's history. Forking an existing tool would mean owning
someone else's data model; the [alternatives survey](alternatives-survey.md) records where each
candidate fell short.

The tool (`gh-plan`, product GH Plan, CLI `plan`) lives in its own repo. Its data lives in
`.tracker/` in each repo being planned ([ADR 0002](adr/0002-tracker-per-repo.md)). It has no
comments, notifications, auth, multi-user support, permissions or email.

<a id="definitions"></a>
## 2. Definitions

| Term | Definition |
|---|---|
| Tracker | A local CLI plus an optional localhost web UI over the files in `.tracker/` |
| Item | One Markdown file, `.tracker/items/<id>.md`, or `.tracker/archive/<id>.md` once Done (a reopened item moves back to `items/`), in the repo the item belongs to. YAML front matter holds its fields and the body holds its description. Committed. The source of truth |
| ID | `<prefix>-<word>`, such as `pl-bakor`: the repo's prefix and a random pronounceable five-letter word (§5) |
| Plan | A `.tracker/` and the items it reads. Every repo's `.tracker/` is a plan of its own items; one that lists member repos spans theirs too (§4) |
| Member repo | A repo a plan lists by path in its config, whose items the plan reads in place |
| Default branch | The branch GitHub has configured for the repo: `main`, or `develop` under git-flow. It carries the tracker's current state |
| Tool commit | A commit the tool makes straight to the default branch, `plan: start <id>` or `plan: stop <id>`. The only commits that do not arrive through a pull request (§7.4) |
| Start | The tool commit that sets an item In Progress and records `started`. An item's actual start ([`C5`](requirements.md)) |
| Finish | The first-parent commit on the default branch that makes an item Done. Its committer date is the item's actual finish |
| Index | `.tracker/.cache/index.db`, SQLite, built from the item files and local git history. Derived, gitignored, rebuildable |
| Time source | An external time tracker, read through one adapter per vendor. Optional ([ADR 0006](adr/0006-tracked-time.md)) |
| Snapshot | The total tracked time for an item, written into its file by the write that sets it Done, with the tracker and tag it came from |
| Lane | A contention domain. Items in a lane touch the same files, so they run one at a time. Single-valued |
| Milestone | A `.tracker/milestones/<id>.md` file with an exit criterion. One in a plan that lists member repos can span them |
| Baseline | A frozen start/finish pair, written once per milestone at its checkpoint and changed only by a deliberate rebaseline |
| needs-triage | An item with no estimate or lane. A warning, not an error |
| Freeze | Compute and write baselines for one milestone. Explicit, dry by default (§7.5) |
| `ready.yml` | `.tracker/ready.yml`, the committed answer to `plan ready`: the next ready items, and the branches in flight ([`D6`](requirements.md)) |

<a id="storage"></a>
## 3. Storage

Committed item files are the source of truth and SQLite is a disposable index
([ADR 0001](adr/0001-git-is-the-database.md)). `.tracker/` lives in the repo being planned
([ADR 0002](adr/0002-tracker-per-repo.md)) and is found by walking up from the working directory
([design 0001, *Discovery*](design/0001-phase-1.md#discovery)).

```
repo/.tracker/
  items/pl-bakor.md     ← one file per item: YAML front matter, Markdown body. The source of truth. Committed
  milestones/beta.md    ← one file per milestone: front matter, then the exit criterion. Committed
  config.yml            ← prefix, points_per_week, hours_per_point, lanes, repos, time_source
  focus.yml             ← the one fast-tracked feature, present only while a focus is set
  ready.yml             ← the committed answer to plan ready, regenerated by every tool write
  .cache/index.db       ← SQLite. Derived, gitignored, deletable, rebuilt in seconds
  out/                  ← generated HTML. Gitignored
  .gitignore            ← ignores .cache/ and out/. Written by plan init. Committed, so every tracker carries the rule
```

Nothing authoritative lives in the database. SQLite lets commands and the web UI sort, filter and
join without re-parsing every item file. Drop it at any time and `plan reindex` rebuilds it from
the committed files and the local git history, without the network.

One file per item and one per milestone means concurrent edits to different items merge cleanly,
and `git log -p` on one file is that item's history. Every committed tracker file is YAML, or
Markdown with YAML front matter, in one canonical form that every tool write produces
([ADR 0007](adr/0007-tracker-files-are-yaml.md)). The schemas are in
[design 0001, *Data model*](design/0001-phase-1.md#data-model) and
[design 0002, *Data model*](design/0002-tracked-time.md#data-model).

<a id="plans"></a>
## 4. Plans

<a id="members"></a>
### 4.1 Member repos

Every repo's `.tracker/` is a plan, and a repo is planned on its own with no other repo present.
A `.tracker/` whose config lists member repos by path also spans them: it reads their item files in
place, keeps its own items like any repo, and holds the milestones that span repos
([`A1`, `A2`](requirements.md), [ADR 0008](adr/0008-file-native-items.md)). Sibling repos find
the plan through the root pointer (#77).

A milestone named on an item is looked up in the item's own repo first, then in the plan that lists
the repo as a member. A repo can keep milestones of its own beside that plan's. One with the same ID
as a plan milestone hides the plan's for that repo's items, and `plan validate` reports it.

A member missing on disk is reported, and the plan is answered without it. Prefixes are unique
within a plan, and a plan whose members share one fails `plan validate`. A change that touches
several repos, such as moving items into a shared milestone, is one commit per repo; nothing makes
them atomic.

<a id="dependencies"></a>
### 4.2 Dependencies and parents

Dependencies are `blocked_by` in the item file, a list of `"<id> - <title>"` strings, so the data
behind `plan why` reads without a tool ([`E1`, `G7`](requirements.md)). The ID is authoritative,
and the tool rewrites the title part on every write. A dependency can name an item in another repo
of the same plan. The tool checks every write for a cycle across the whole plan
([`E2`](requirements.md)). A repo validated on its own reports a reference to another repo as
unresolved, not as an error.

A parent is `parent: <id>` on the child ([`A4`](requirements.md)). A child is an item like any
other, with its own estimate, lane, baseline and dependencies, and a parent with children carries
no estimate, so the roll-up the index computes does not double-count ([`A5`](requirements.md)).
An item is worked only in its own repo, because its branch and its start commit live there. When
an item also needs a change in another repo, that change is a child item in the other repo, with
`parent` naming the first item, and it is started and finished there.

<a id="identity"></a>
## 5. Identity

An ID is the repo's prefix and a random word, such as `pl-bakor`. The word alternates consonant and
vowel, five letters long, from 16 consonants and 5 vowels: about 102,000 words per repo, less a
blocklist of words that read badly. The prefix is set in the repo's config and names the repo, so
an ID means the same item in any plan that includes it. The ID is the file's name, and nothing
inside the file repeats it ([design 0001, *Identity*](design/0001-phase-1.md#identity)).

The tool picks a word no existing file uses, so two worktrees creating items at once are unlikely to
collide ([`D5`](requirements.md)) and no counter needs merging. When two branches do pick the same
word, the second merge conflicts on the new file and `plan validate` reports a duplicate ID.

An ID is rarely typed in full. Inside its own repo the prefix may be left off. Any unique prefix of
the word is enough (`bak`), and so is a unique word from the title. Commands run on a branch named
for an item take its ID from the branch, and shell completion offers IDs with their titles.

<a id="components"></a>
## 6. Components

<a id="front-ends"></a>
### 6.1 Core and front ends

One core library has two front ends: the CLI and a web UI. Neither shells out to the other. The web
UI is a server bound to localhost that renders the same templates as `plan render`
([ADR 0005](adr/0005-generated-html-charts.md)); its stack is not chosen.

Reads are implicit and writes are deliberate. Every query reads the files and local git, so it works
offline. Freeze is dry by default, and a start or stop names its item
([design 0001, *Reads and writes*](design/0001-phase-1.md#reads-and-writes),
[*Commands*](design/0001-phase-1.md#commands)).

<a id="process-boundary"></a>
### 6.2 Process boundary

`gh.py`, `git.py` and the adapters in `timekeeping/` are the only Python modules, and
`vscode/redmine.js` and `vscode/start.js` the only extension modules, that spawn a process or open
a network connection. Everything else is pure and testable without a network or a repo
([ADR 0004](adr/0004-python-no-docker.md), [ADR 0006](adr/0006-tracked-time.md),
[design 0001, *Layout*](design/0001-phase-1.md#layout)).

- `git.py` makes every call to git: reading history for finish dates, building and pushing tool
  commits, and listing the branches on `origin` for `ready.yml`.
- `gh.py` reads a repo's pull requests through the `gh` CLI, only to add each PR's number and link
  to reports ([`I5`](requirements.md)). It never writes. `gh` is already authenticated, so the tool
  holds no token, as [`G3`](requirements.md) requires. A library such as PyGithub, Octokit or httpx
  would need a token.
- Each `timekeeping/` adapter reads one vendor's time tracker behind the `TimeSource` interface.
  It reads and never writes
  ([design 0002, *The TimeSource interface*](design/0002-tracked-time.md#interface)).
- `vscode/redmine.js` makes every call to Redmine's REST API: it reads the panel's issues and
  writes Start's status change and journal note. It reads the API key from the macOS Keychain on
  each call, so no repo holds it ([`G3`](requirements.md)).
- `vscode/start.js` runs git and `uv` for Start: it fetches, adds the worktree and syncs it.

The tool finds the default branch from `refs/remotes/origin/HEAD`, so it needs no GitHub read for
that either.

<a id="platform"></a>
### 6.3 Platform

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.14+ (the floor is the one version CI tests) | [ADR 0004](adr/0004-python-no-docker.md) |
| Item state and history | subprocess to `git` | [ADR 0008](adr/0008-file-native-items.md) |
| Pull request links | subprocess to `gh`, read-only | requirement [`G3`](requirements.md) |
| Time tracker access | one adapter per vendor behind `TimeSource` | [ADR 0006](adr/0006-tracked-time.md) |
| Tracker files | YAML 1.2 via ruamel.yaml, and Markdown with YAML front matter | [ADR 0007](adr/0007-tracker-files-are-yaml.md) |
| Index | SQLite via stdlib `sqlite3` | [ADR 0001](adr/0001-git-is-the-database.md) |
| Validation | Pydantic v2 | [ADR 0004](adr/0004-python-no-docker.md) |
| CLI | Typer | [ADR 0004](adr/0004-python-no-docker.md) |
| Charts | generated HTML: CSS Grid for bars and lanes, inline SVG for dependency arrows | [ADR 0005](adr/0005-generated-html-charts.md) |
| Web UI | a localhost server rendering the chart templates; stack not chosen | [ADR 0005](adr/0005-generated-html-charts.md) |
| Packaging | `uv run`, no Docker on the run path; an optional `Dockerfile` for CI | [ADR 0004](adr/0004-python-no-docker.md) |

<a id="operations"></a>
## 7. Operations

```
   item branch  feat/pl-bakor-slug                        default branch (main)
   ───────────────────────────────                        ─────────────────────
                                    (1) START, tool commit
   plan start / post-checkout ───────────────────────────▶ plan: start pl-bakor
                                                            status: In Progress · started
   branch is created from,  ◀──────────────────────────────┘
   or fast-forwarded onto, the start

   work · status: Done · actual_time   (2) FINISH, the squash merge of the PR
   ───────────────────────────────────────────────────────▶ the first-parent commit
                                                            that makes it Done

                                    (3) STOP, tool commit, abandons a start
   plan stop ─────────────────────────────────────────────▶ plan: stop pl-bakor
                                                            status: Todo · started kept
```

The default branch carries the tracker's current state: whatever it says about an item is the
item's status. The tool commits to it directly only to start or stop an item. Every other change to
the tracker, including the finish, reaches it through a pull request, like code
([ADR 0008](adr/0008-file-native-items.md)).

<a id="start"></a>
### 7.1 Start

Two signals start an item: a skill calls `plan start <id>`, or a `post-checkout` hook sees a branch
name carrying an ID. The branch is named for the item, `<type>/<id>-<slug>`, in the item's own repo, and `<type>` follows the item's `type`: `feat`, `fix` for a bug, or `chore`.
The tool sets `status: In Progress` and `started` (UTC, to the second), regenerates `ready.yml`,
commits both to the default branch as `plan: start <id>`, and pushes. Offline, the commit is kept
locally and pushed by the next `plan sync` or `git push`.

The item's branch contains its start. `plan start` commits the start first and creates the branch
from it. The hook finds the branch already made, so it fast-forwards the branch onto the start if
the branch has no commits of its own yet. Otherwise it writes the same change into the branch's
working tree, and `plan validate` reports the branch as not containing its start. Because the
branch contains its start, its own change from In Progress to Done merges without a conflict.

`started` is written once. A second start, from another branch or after a stop, finds it set on the
default branch and leaves it, so an item's actual start is its first start.

<a id="stop"></a>
### 7.2 Stop

`plan stop <id>` abandons a start. It commits the item back to Todo on the default branch as
`plan: stop <id>` and leaves `started` in the file as the record of the false start. A false start
is shared as soon as it is pushed, so stopping it is a commit too.

<a id="finish"></a>
### 7.3 Finish

The finish is the merge. The branch sets `status: Done` before it merges, and the change reaches the
default branch in the same squash commit as the work. The item's actual finish is the committer date
of the first-parent commit on the default branch that made it Done, read from local history. An item
worked across several branches is set Done by the last of them.

The write that sets `status: Done` also writes the `actual_time` snapshot when a time source is
configured. A time source that fails does not block it: the item still reaches Done, without a
snapshot, and `validate` warns
([design 0002, *When the snapshot is taken*](design/0002-tracked-time.md#lifecycle)).

<a id="tool-commits"></a>
### 7.4 Tool commits

A tool commit touches only one item's `status` and `started`, plus `ready.yml`. The tool builds it
on the tip of the default branch through a temporary index, without checking that branch out, so
nothing else in the working tree can get into it and the working tree does not change. Its message
is fixed, so `git log --grep '^plan:'` finds every one.

Two starts can race. The tool moves the local default branch only by compare-and-swap
(`git update-ref <ref> <new> <old>`), so two worktrees on one machine cannot overwrite each other's
start. When a push is rejected because the default branch moved, the tool fetches and re-reads the
item on the new tip. If the item is already started there, it does nothing. Otherwise it rebuilds
its commit on that tip, regenerating `ready.yml`, and pushes again.

Branch protection on the default branch must let the owner push. A pull request that has to be up
to date with the default branch before merging falls one commit behind with every tool commit.

<a id="freeze"></a>
### 7.5 Freeze

Freeze computes start and finish dates for one milestone and writes them into its items as
baselines. It is dry by default. A baseline is written once and never silently recomputed;
`--rebaseline` replaces it deliberately, with a reason, and git records the change
([`C3`, `C4`](requirements.md)). `plan validate` runs before freeze, and an unestimated item fails
it, because it would corrupt the baseline. Freeze writes on a branch, and its baselines reach the
default branch through a pull request.

<a id="ready-file"></a>
### 7.6 `ready.yml`

`ready.yml` lets a viewer read what is ready on GitHub or in an editor with no tool installed
([`D6`](requirements.md)). It lists at most 20 items `plan ready` reports, with what `ready`
reports for each, and when it was generated. A second section lists the items in flight: each
branch on `origin` that names an item, with the item's ID, title, `started` and the date of the
branch's last commit. Only pushed branches are listed, so the file is the same whichever machine
generates it.

Every tool write regenerates it, tool commits included. Its as-of commit is the last commit that
wrote it; the file cannot name the commit that contains it. Two branches that both regenerate it
conflict on merge, and the second regenerates it again.

<a id="failure-modes"></a>
## 8. Failure modes

| Event | Effect | Handling |
|---|---|---|
| Offline at start or stop | The tool commit cannot be pushed | It is kept on the local default branch and pushed by the next `plan sync` or `git push`. Every query still works |
| Push rejected because the default branch moved | The start is not shared | Fetch, re-read the item on the new tip, and rebuild and push, or do nothing if it is already started there (§7.4) |
| Two worktrees start at once on one machine | Both try to move the local default branch | Compare-and-swap lets one through; the other re-reads and rebuilds |
| Branch made with commits before its start | The branch does not contain its start | The hook writes the start into the working tree; `plan validate` reports it, and the branch's Done conflicts on the `status` line at merge |
| False start pushed | The default branch shows the item In Progress | `plan stop` commits it back to Todo; `started` stays |
| Second start for an item already started | `started` is already set | Left as it is. `plan validate` reports a `started` later than the first tracked time for the item |
| Two branches create the same ID | The second merge conflicts on the new file | `plan validate` reports the duplicate; one file is renamed by hand |
| Member repo missing on disk | Its items are not read | Reported; the plan is answered without it |
| Cross-repo `blocked_by` validated outside its plan | The other item cannot be read | Reported as unresolved, not as an error |
| Time source unreachable when an item is set Done | No snapshot | The item still reaches Done; `validate` warns until `plan time <id> --apply` |
| Two sessions edit different items | Two different files | Git merges cleanly, because there is one file per item |
| Two sessions edit the same item | Ordinary git conflict | A short YAML conflict a human can read, because each key is on its own line |
| Two branches regenerate `ready.yml` | Merge conflict on `ready.yml` | The second branch regenerates it |
| A change across several repos half-lands | Some repos have the commit and some do not | Nothing makes it atomic; `plan validate` from the plan reports what no longer resolves |

<a id="views"></a>
## 9. Views

The terminal views are `plan next`, `ready`, `why` and `board`, each with `--json`
([design 0001, *next, ready, why*](design/0001-phase-1.md#next-ready-why)).

Every chart, graph and table is also generated HTML, with no Mermaid, no JS charting
library and no build step ([ADR 0005](adr/0005-generated-html-charts.md)):

| Artifact | Technique |
|---|---|
| Lane board | CSS Grid: lanes are rows, statuses are columns |
| Timeline bars | CSS Grid: a bar is a column span; duration is `grid-column: start / span n` |
| Dependency arrows | Inline SVG over the grid |
| Tables, roll-ups, ready queue | plain `<table>`, server-rendered |
| Colour and status | CSS custom properties, with a shape or label as well as colour so nothing depends on hue alone |

`plan render` writes the HTML to `.tracker/out/`, which is gitignored, because GitHub shows a
committed HTML file as source. `plan serve` renders the same templates from a localhost server.
Until the web UI exists there is no web page per item; GitHub renders the Markdown file, front
matter as a table above the body, which covers reading.

<a id="invariants"></a>
## 10. Invariants

1. Git is the database. SQLite is a disposable index that `plan reindex` rebuilds from the committed
   files and local git history ([ADR 0001](adr/0001-git-is-the-database.md)).
2. The default branch carries the tracker's current state. What it says about an item is the item's
   status. Every change reaches it through a pull request, except the tool's `plan:` commits
   (§7, [ADR 0008](adr/0008-file-native-items.md)).
3. A tool commit touches only one item's `status` and `started`, plus `ready.yml`, and its message
   is `plan: start <id>` or `plan: stop <id>` (§7.4).
4. `started` is written once and never changed or removed. `plan stop` sets the item back to Todo
   and leaves `started` as the record of the false start, and a later start keeps it (§7.1, §7.2).
5. Retired, so the invariants after it keep their numbers. Invariant 6 bounds remote writes,
   which include Redmine's REST API ([ADR 0011](adr/0011-redmine-is-the-tracker.md)).
6. `gh.py`, `git.py` and the `timekeeping/` adapters are the only Python modules, and
   `vscode/redmine.js` and `vscode/start.js` the only extension modules, that spawn a process or
   open a network connection. Every call to Redmine goes through `redmine.js` (§6.2).
7. Estimates are `Literal[1, 2, 3, 5]`. 8 and 13 are refusal codes meaning "split this" and cannot
   be represented ([`B3`](requirements.md)).
8. An item has at most one lane ([`D4`](requirements.md)).
9. A parent with children has no estimate ([`A5`](requirements.md)).
10. A baseline is written only by freeze and never silently recomputed ([`C3`](requirements.md)).
11. Tracked time is read from the time tracker and never written to it. The write that sets an item
    Done writes the `actual_time` snapshot (§7.3, [ADR 0006](adr/0006-tracked-time.md)).
