# Design 0003: The plan board

## Contents

- [1. Context and scope](#context)
- [2. Goals and non-goals](#goals)
- [3. Design](#design)
  - [3.1 Cards](#cards)
  - [3.2 Recipes](#recipes)
  - [3.3 The runner](#runner)
  - [3.4 Keeping Claude off the runner](#hook)
  - [3.5 Results back to Claude](#results)
  - [3.6 The VS Code extension](#sidebar)
  - [3.7 Failure modes](#failure-modes)
  - [3.8 Worked example: one item's session](#worked-example)
- [4. Alternatives considered](#alternatives)
- [5. Cross-cutting concerns](#lenses)
- [6. Testing](#testing)
- [7. Requirements coverage](#requirements-coverage)
- [8. Work breakdown](#work-breakdown)

**Implements:** [ADR 0009](../adr/0009-plan-board.md) · **Requirements:**
[`D5`, `D7`, `G3`, `I1`](../requirements.md) · **Status:** draft

<a id="context"></a>
## 1. Context and scope

Claude works an item in its worktree, and Peter runs every commit, push and `gh` write. Today each
of those steps reaches him as a command to copy ([ADR 0009,
*Context*](../adr/0009-plan-board.md#context)). The plan board turns each one into a card: Claude
writes it, Peter reviews its files and presses ▶. The board is a VS Code extension,
`pjfitzgibbons.plan-board`: a sidebar view in each VS Code window (§3.6) over a Python runner,
`plan_board.py` (§3.3), which does the work and also runs from a terminal. An item's cards are its
steps in order, from the start that marks it In Progress to the done that removes its worktree, and
each card records how its step ran.

**In:** the card format · the seven recipes and their checks · the runner, `plan_board.py` · the
hooks that keep Claude off the runner and hand it results · the VS Code extension and its sidebar,
with each repo's ready list, its items in progress and its last items done · failure modes.

**Out:** gh-plan's own commands. The board runs `worktask` and the `gh` aliases as they are. It
never runs a `plan` write, and `plan:` commits still come only from `plan-start` (and later
`plan start`).

Everything here lives in `~/.claude/skills/plan-board/`, beside `worktask`, and works for any repo
Peter lists, not only gh-plan.

<a id="goals"></a>
## 2. Goals and non-goals

**Goals**

- Peter copies no command into a terminal. Every step Claude would hand over is a card.
- Peter sees what a card will do to which files before he presses ▶.
- Claude knows what ran, and how it ended, before it plants the next card.
- One view of every item in flight across repos ([`D7`](../requirements.md)).
- No paid API tokens and no new credential ([`I1`, `G3`](../requirements.md)).

**Non-goals**

- Claude running anything that writes to a remote. Only Peter's ▶ does.
- Arbitrary commands. A card lists its commands, and the runner runs them only when they are
  exactly its recipe's.
- Driving Claude. The board doesn't start, prompt or stop a Claude session, except the one prompt
  "Send back to Claude" types (§3.5).
- Replacing `plan serve` (pl-gokul), which stays a read-only web UI.

<a id="design"></a>
## 3. Design

```
  Claude (item worktree)              Peter (VS Code)                     remote
  ──────────────────────              ───────────────                     ──────
  writes .tmp/commit-<id>.txt
  writes .tmp/pr-<id>-body.md
  plants, in the main checkout,
    .tmp/plan-board/<id>/ ──────────▶ board view shows the card
      02-ship.yml
                                      reviews its files in the diff editor
                                      presses ▶
                                        └─ terminal "Plan board: pl-bakor 02-ship"
                                           plan_board.py run 02-ship.yml ─▶ git push, gh pc
                                           appends run, log, result
                                             to 02-ship.yml
  next turn: hook adds the result ◀──── status bar and toast show the result
  reads it, plants the next card
```

<a id="cards"></a>
### 3.1 Cards

A card is a YAML file, `<nn>-<recipe>.yml`, in `.tmp/plan-board/<id>/` of the repo's main checkout,
whichever checkout its recipe runs in (§3.2). `<id>` is the item, and `<nn>` is the next free
two-digit number in that directory, so an item's cards are one sequence, in the order they were
planted. Card 01 is always `worktask-start`, which Peter plants by pressing ▶ *Start* on a ready
item (§3.6). Claude plants it instead to reopen an item that is In Progress but whose worktree and
branch are gone. The runner plants a `review` card when `ship` opens a PR (§3.6). Claude plants the
rest. Cards outlive the worktree. When `worktask-done` finishes the item, they move to
`.tmp/plan-board/done/<id>/`, which keeps the last three items done (§3.3). The main checkout is the
first entry of `git worktree list`, so the worktree's Claude finds it there. `.tmp/` is gitignored
in every repo the board serves.

The worktree's Claude writes outside its own worktree to plant a card. `worktask start` adds the
main checkout to the worktree's `permissions.additionalDirectories`, so those writes don't prompt
🔍 unverified: that the setting covers writes as well as reads.

```yaml
card: 02-ship
v: 1
recipe: ship
item: pl-bakor
why: The report is done and tested; this opens its PR.
planted: 2026-10-15T21:04:11Z
planted_by: claude
runs_in: worktree
message:
  path: .tmp/commit-pl-bakor.txt
  sha256: 9f2c…
body:
  path: .tmp/pr-pl-bakor-body.md
  sha256: 41ab…
steps: |
  git add -- src/demo/report.py tests/test_report.py .tracker/archive/pl-bakor.md
  git commit -F .tmp/commit-pl-bakor.txt
  git push -u origin HEAD
  gh pc
  gh pr edit --add-reviewer @copilot
paths:
  - path: src/demo/report.py
    sha256: c07e…
  - path: tests/test_report.py
    sha256: 5d19…
  - path: .tracker/items/pl-bakor.md
    sha256: deleted
  - path: .tracker/archive/pl-bakor.md
    sha256: 88a0…
```

| Field | Meaning |
|---|---|
| `card` | The card's ID, `<nn>-<recipe>`: its file name without `.yml`. It comes first, and the runner refuses a card whose ID doesn't match its file name |
| `v` | The card format's version. The runner refuses a version it doesn't know |
| `recipe` | One of the seven in §3.2. Anything else is refused |
| `runs_in` | `main` or `worktree`. It must be the checkout the recipe runs in, and the runner must be started there |
| `item` | The item ID. It must match the directory the card is in |
| `why` | One sentence for Peter: why this step, now |
| `planted` | When the card was written, UTC |
| `planted_by` | `person` for card 01, written by ▶ *Start*; `runner` for a `review` card; `claude` for every other card |
| `person` | For a `person` card, who pressed ▶ *Start*: `git config user.name`, or the `gh` login when that's unset. No one's name is built into the runner or the sidebar; a name they show comes from git or `gh` |
| `message`, `body` | The commit message and PR body files, for the recipes that use them. After an edit to the PR's body on GitHub was caught, `body` also carries `live_sha256`, the hash of the `pr-body-live.md` Claude folded in (§3.2) |
| `steps` | Every command ▶ will run, verbatim and in order, as a literal block (`steps: \|`), one command to a line. The runner refuses a card whose steps differ from its recipe's (§3.3) |
| `paths` | The paths to commit, for `commit` and `ship`. `deleted` marks a removal: the runner checks the path is gone from the checkout, and `git add` names it only while the index still has it. After a `git mv` the removal is already staged, so `git add` leaves it out |
| `restamped` | When Peter took his own edits with *Use my edits* (§3.6), UTC. Absent otherwise |

Every file a card names carries the SHA-256 Claude computed when it planted the card. ▶ re-hashes
them all first, and a mismatch refuses the card, naming the file. Pressing ▶ on unchanged files is
the review. A file changed after planting, by Claude or by hand, needs new hashes: Claude plants a
new card, or Peter takes his own edits with *Use my edits*.

A card's `steps` are every command its ▶ will run, verbatim and in order, as Peter would type
them. Whoever plants the card writes them from its recipe and the state of the checkout then. At
▶ the runner works out the commands the recipe would run for the state as it is now, and refuses
the card if any line differs, naming the first. Otherwise it runs the card's lines exactly as they
stand. The card shows what will run, and nothing outside the recipe can run.

`steps` comes before `paths`, so the commands a card will run are read first, and the long
lists, `paths` and the runner's `log`, come after them. `steps` is a literal block, one command
to a line, so Peter can copy the whole block into a terminal and run it as it stands. A command
therefore fits on one line.

Below the fields the planter wrote, the runner appends the step's record when ▶ runs it: a `run`
block, the `log`, and a `result` block (§3.3, §3.5). Each card is the whole record of one step,
and the item's directory, read in order, is the record of its process. Nothing but the runner
writes into a card after it is planted, apart from *Use my edits* on a waiting card.

A card is spent once it has a `run` block. The sidebar shows a spent card with its outcome, and ▶
is gone from it.

<a id="recipes"></a>
### 3.2 Recipes

Each recipe runs in one directory, checks everything first, and refuses before its first write if
a check fails. *Main* is the repo's main checkout, the first entry of `git worktree list`.
*Worktree* is the item's worktree. The runner refuses a card whose `runs_in` isn't its recipe's
checkout, or that it was started outside. Paths in a card are relative to the checkout it runs in.

| Recipe | Runs in | Checks | Steps |
|---|---|---|---|
| `worktask-start` | main | the item is Todo on `origin/<default>`, or In Progress there to reopen it; it has no worktree, and no branch locally or on `origin` | `worktask start <id>`, which pushes `plan: start <id>` and opens the worktree's window. A reopen pushes nothing, because the item was started already |
| `commit` | worktree | on the item's branch; every file matches its hash; nothing outside `paths` is staged; `message` ends with an `Assisted-by:` trailer and names no AI as author | `git add -- <paths>`, `git commit -F <message>` |
| `rebase` | worktree | on the item's branch; no rebase already in progress; after `git fetch origin`, the branch's tip on `origin` is an ancestor of `HEAD` | `git fetch origin`, `git rebase --autostash origin/<default>`; when the branch is already on `origin`, `git push --force` |
| `ship` | worktree | `commit`'s checks; `body` starts with `Closes:` and carries no attribution; reads the PR's state with `gh pr view`; when the PR is open, its body on GitHub is the one the runner last posted, or the one `live_sha256` names | no PR: commit, `git push -u origin HEAD`, `gh pc`, then `gh pr edit --add-reviewer @copilot`. Open: commit, `git push`, and `gh pb` when the body differs from the one last posted. Merged or closed: refused |
| `pr-body` | worktree | the PR is open; `body`'s checks; its body on GitHub is the one the runner last posted, or the one `live_sha256` names | `gh pb` |
| `review` | main | the PR is open; Copilot's review was requested | `gh pr view <n> --json state,reviews`, once a poll, until a review by Copilot is posted or the PR closes; then `gh api --paginate repos/<owner>/<repo>/pulls/<n>/reviews/<review>/comments` for that review's comments; no ▶ (§3.6) |
| `worktask-done` | main | the PR is merged; the worktree is clean | `worktask done <id>`; when that succeeds, moves the item's cards to `.tmp/plan-board/done/` (§3.3) |

- **No `add -A`, and nothing else staged.** `commit` and `ship` stage only the paths the card names,
  and refuse before `git add` when anything outside them is already staged, because `git commit`
  takes the whole index. So a stray file never rides along.
- **What a step reads is what was reviewed.** A file can change between its hash check and the step
  that reads it, so the runner also checks what each step used. It reads the message and body into
  memory when it checks their hashes. After `git add` it hashes each staged blob (`git show
  :<path>`) against the card and confirms the index differs from `HEAD` only at the card's paths,
  before it commits. After `git commit` it compares the commit's message with the checked message,
  cleaned up the way git does it (`git stripspace`). On a mismatch it undoes its own commit with
  `git reset --soft HEAD~1`, logs that, and refuses before any push. A body goes straight to GitHub,
  so after `gh pc` or `gh pb` the runner reads it back with `gh pr view --json body`. A body that
  isn't the checked one is a `failed` outcome, and the posted body is saved as `pr-body-live.md`.
- **Resumable steps.** A step that has already happened is skipped, not repeated: `commit` with
  nothing to commit in its paths, a push when `origin` already has `HEAD`, and `gh pc` when the PR
  exists. A `ship` that failed at its push can be planted again and runs from the push.
- **An edit on GitHub stops `gh pb`.** After `gh pc` or `gh pb`, the runner saves the body it posted
  beside the item's cards, as `pr-body-posted.md`. Before the next `gh pb`, it reads the live body
  with `gh pr view --json body` and compares the two, with line endings normalised first, because
  GitHub may store a body with CRLF 🔍 unverified. A difference means someone edited the body on
  GitHub. The runner refuses before any write, saves the live body beside the cards as
  `pr-body-live.md`, and the result names it. Claude folds the edit into the body file and plants
  the card again, with the live body's hash as `live_sha256` on its `body`. The runner then accepts
  a live body that matches either one, so a further edit on GitHub still refuses. Peter reviews the
  fold-in as a diff against `pr-body-live.md` (§3.6).
- **A force-push never drops a commit.** After the fetch, `rebase` refuses before it rebases when
  the branch's tip on `origin` isn't an ancestor of `HEAD`: `origin` has a commit the worktree
  doesn't, and the push would lose it. Claude reconciles the branch first.
- **Conflicts stop.** A `rebase` that conflicts runs `git rebase --abort`, records the conflicting
  files, and stops. The branch is as it was.
- **On the default branch, nothing runs.** Every worktree recipe refuses when `HEAD` is the default
  branch, so a card can't push to `main` (invariant 2).
- **Adding a recipe** is a change to the runner, reviewed like code.

<a id="runner"></a>
### 3.3 The runner

`~/.claude/skills/plan-board/scripts/plan_board.py`, a Python script that reads cards with
ruamel.yaml as YAML 1.2, the way gh-plan reads its own files
([ADR 0007](../adr/0007-tracker-files-are-yaml.md)). The runner lives outside the project's
virtualenv, so the script declares ruamel.yaml in an inline
[PEP 723](https://peps.python.org/pep-0723/) block, and the sidebar runs it as
`uv run <path> …`, which installs ruamel.yaml on first use. It isn't put on `PATH`.

```
plan_board.py start <id>    plant card 01, worktask-start, then run it; from the main checkout
plan_board.py run <card>    check the card, run its recipe, append its record
plan_board.py check <card>  the checks only; writes nothing
plan_board.py poll <card>   one poll of a review card; logs it, and appends the result once posted
plan_board.py list [repo…]  each repo's ready items, cards and their state, and done items, as JSON
```

`run` does this in order:

1. Refuses when it is running under Claude's Bash tool (§3.4).
2. Takes the card's lock: it creates `<card>.lock` with `O_EXCL` and writes its PID into it. If the
   lock exists and its PID is alive, it refuses. If that PID is dead, it reports a stale lock and
   refuses; the sidebar offers "Clear lock".
3. Refuses a card that already has a `run` block. A `review` card is the one exception, through
   `poll`, until it has a `result`.
4. Appends the `run` block: when it started, the runner's version, the versions of Python, git and
   `gh`, the directory, the PID, the branch and `HEAD`.
5. Runs the recipe's checks, then compares the card's `steps` with the commands the recipe would run
   now and refuses on any difference (§3.1), then runs the card's steps. Each check and each
   command, with its exit code, its time and its output, goes both to the terminal and into the
   card's `log`, line by line as it happens. Output is decoded as UTF-8, replacing what isn't.
6. Appends the `result` block, then drops the lock.
7. When a `worktask-done` card's steps succeed, keeps the last three items done in that main
   checkout. Before step 6, it ranks the items in `.tmp/plan-board/done/` by the `finished` time in
   their `worktask-done` result, not by file times, which an edit or a copy changes. It deletes all
   but the newest two, and logs each deletion in the card. After step 6, it moves
   `.tmp/plan-board/<id>/` to `.tmp/plan-board/done/<id>/`, replacing an older copy of the same ID.
   A deletion that fails is logged as a warning, and the outcome stays `done`. A refused or failed
   `worktask-done` leaves the cards where they are.

The card only grows, so a run that stops partway leaves the `run` block and the log up to that
point, and the card still parses. `start` writes card 01 itself, with `planted_by: person`, the
`person` from git or `gh`, and the `why` *Started from the ready list*, then runs it as `run`
does. Its run ends when `plan: start` is on the default branch and the worktree's window is open.

The runner gets `gh`'s login from `gh` itself and holds no token ([`G3`](../requirements.md)).

<a id="hook"></a>
### 3.4 Keeping Claude off the runner

Two guards, so one mistake isn't enough:

- **A `PreToolUse` hook**, `~/.claude/skills/plan-board/hooks/deny_plan_board.py`, registered for
  Bash in Peter's user settings. It denies any command whose text names `plan_board.py` or
  `skills/plan-board/scripts`, and tells Claude to plant a card instead ([Claude Code
  hooks](https://code.claude.com/docs/en/hooks)).
- **The runner itself** refuses when `CLAUDECODE` is set in its environment, which Claude Code sets
  for the commands its Bash tool runs 🔍 unverified. The terminal ▶ opens is an ordinary VS Code
  terminal without it.

The hook reads command text, the way this repo's attribution hook does, so it stops a mistake, not
a determined workaround. The settings' existing deny rules on `git push` and `git rebase` stay.

<a id="results"></a>
### 3.5 Results back to Claude

The runner appends the step's record to the card, below the fields the planter wrote. Here is
§3.1's card after a refused ▶: the planted fields with their hashes, then the run, the log and
the result.

```yaml
card: 02-ship
v: 1
recipe: ship
item: pl-bakor
why: The report is done and tested; this opens its PR.
planted: 2026-10-15T21:04:11Z
planted_by: claude
runs_in: worktree
message:
  path: .tmp/commit-pl-bakor.txt
  sha256: 9f2c…
body:
  path: .tmp/pr-pl-bakor-body.md
  sha256: 41ab…
steps: |
  git add -- src/demo/report.py tests/test_report.py .tracker/archive/pl-bakor.md
  git commit -F .tmp/commit-pl-bakor.txt
  git push -u origin HEAD
  gh pc
  gh pr edit --add-reviewer @copilot
paths:
  - path: src/demo/report.py
    sha256: c07e…
  - path: tests/test_report.py
    sha256: 5d19…
  - path: .tracker/items/pl-bakor.md
    sha256: deleted
  - path: .tracker/archive/pl-bakor.md
    sha256: 88a0…
run:
  started: 2026-10-15T21:06:40Z
  runner: plan_board.py 1.0 · Python 3.14.0 · git 2.51.0 · gh 2.80.0
  cwd: ~/src/gh-plan.worktrees/pl-bakor
  pid: 51377
  branch: feat/pl-bakor-weekly-report-by-lane
  head: 3c9e1a2
log: |
  21:06:40.112 check  on feat/pl-bakor-weekly-report-by-lane         ok
  21:06:40.118 check  src/demo/report.py matches c07e…               ok
  21:06:40.121 check  .tmp/commit-pl-bakor.txt matches 9f2c…         changed: now 7b11…
  21:06:40.121 refused before any write
result:
  outcome: refused        # done | refused | failed
  finished: 2026-10-15T21:06:41Z
  step: check             # the last step reached
  exit: 1
  reason: ".tmp/commit-pl-bakor.txt changed after the card was planted"
  head: 3c9e1a2
  pr_url: null
```

When `ship` opens a PR, its result also carries `review_requested: copilot`, or `failed` with the
reason in the log if the request didn't go through. The PR is open either way, so the outcome is
still `done`.

- **A `UserPromptSubmit` hook**, `~/.claude/skills/plan-board/hooks/results.py`, runs at the start
  of each Claude turn. It finds the main checkout with `git worktree list` and adds, as context, the
  `result` and the end of the `log` of every card in its `.tmp/plan-board/`, outside `done/`, that
  Claude hasn't seen, then records them in `.tmp/plan-board/<id>/.seen`. In a worktree it reads only
  that item's directory, taking the ID from the branch name. In the main checkout it reads every
  item's. So Claude reads what ran before it does anything else, without being asked. For a `review`
  card, it also adds the `review` block: Copilot's summary and each comment, with the file and line
  it's on.
- **Claude reads a card's result before it plants the next card** for the same item. The CLAUDE.md
  boundary says so, and the `plan-board` skill tells it how.
- **After a changed-file refusal, Claude leaves the file alone.** The change may be Peter's. Claude
  plants the new card from the files as they stand, and rewrites one only when Peter asks. A card
  with `restamped` tells Claude the same: what shipped was Peter's text.
- **"Send back to Claude"**, on a failed or refused card, types one line into the item's Claude
  terminal: `Plan-board card 02-ship was refused: <reason>. Read
  ~/src/gh-plan/.tmp/plan-board/pl-bakor/02-ship.yml.` The path is the main checkout's, since the
  card isn't in the worktree. It uses `Terminal.sendText` on the terminal whose name starts with
  `Claude` in that worktree's window 🔍 unverified: how the Claude Code extension names its
  terminal isn't documented. If no such terminal is found, it copies the line instead.

<a id="sidebar"></a>
### 3.6 The VS Code extension

The `pjfitzgibbons.plan-board` extension grows out of `worktask`'s, which today is one URI handler
(`~/.claude/skills/worktask/vscode-ext/`). It keeps that handler and adds a view to the Explorer
sidebar ([Tree View API](https://code.visualstudio.com/api/extension-guides/tree-view)). It installs
from a local `.vsix`.

**The board view.** `planBoard.repos`, a user setting, lists repo paths, and defaults to the
window's own repo. The view has three sections, each grouped by repo:

```
READY
├─ gh-plan
│  ├─ pl-ritum  Lane filter on list         ▶ Start
│  └─ pl-sofek  Archive by month            ▶ Start
└─ demo-api
   └─ pl-mavid  Token refresh               ▶ Start
IN-PROGRESS
├─ gh-plan
│  ├─ pl-bakor  feat/pl-bakor-report        ▶ 04 rebase  waiting
│  │  ├─ 01 worktask-start  ✓ done
│  │  ├─ 02 ship            ✗ refused: commit-pl-bakor.txt changed
│  │  └─ 03 ship            ✓ done   PR #112
│  └─ pl-kozot  fix/pl-kozot-dates          Claude working
│     └─ 01 worktask-start  ✓ done
└─ demo-api
   └─ pl-tivam  feat/pl-tivam-auth          ▶ 02 commit  waiting
      └─ 01 worktask-start  ✓ done
DONE
└─ gh-plan
   └─ pl-nuvok  Export to CSV               ✓ done  PR #109
```

- **Ready** lists the items in each repo's `.tracker/ready.yml`, in its order, as the default branch
  has it on `origin` (`git show origin/<default>:.tracker/ready.yml`). That is the copy
  `worktask-start` checks against. *Refresh* runs `git fetch` first. A repo without a ready list has
  no entries here. ▶ *Start* opens a terminal named `Plan board: <id> 01-worktask-start` in the main
  checkout and runs `plan_board.py start <id>` there, so the step is card 01, kept with the rest.
- **In-progress** has one row per worktree, `git worktree list` less the main checkout. The row
  shows the item's current card: the newest card with no `run` block, with its ▶, or else a
  `review` card still watching, or else the newest card if it was refused or failed, or else
  *Claude working*. Spent cards fold under the row, oldest first. A `worktask-done` card shows here
  too, under its item, though it runs in the main checkout. Its row has a *Worktree Done* button
  in place of ▶, and pressing it is the approval. Pressed in the worktree's window, it starts the
  runner on the card from the main checkout, as a process the window doesn't own, then brings the
  main checkout's window forward and closes its own, Claude's session with it. The main window's
  sidebar sees the card's `run` block and opens a terminal that follows its log. 🔍 unverified:
  that a process the extension starts outlives the window that started it. Pressed in the main
  window, it runs there like any ▶. When the card is done, the worktree is gone, and the item
  moves to Done.
- **Done** lists the last three items finished in each repo, newest first, and starts collapsed.
  Each item's cards fold under it. Clicking one opens the card's YAML as it is, which holds its
  run, log and result. The review's diffs need the worktree, which is gone, so Done has no view of
  its own.
- **Clicking an item's row** switches to its worktree's window. It runs `code <worktree>`, which
  brings forward a window already open on that folder 🔍 unverified, and opens one if none is.
  It writes nothing, so it needs no card.
- **The review watch.** When a `ship` result records `review_requested: copilot`, the runner plants
  the item's next card, `<nn>-review.yml`, with `planted_by: runner`, `runs_in: main` and the PR's
  number. It has no ▶: it writes nothing to GitHub, so there is nothing for Peter to approve. The
  sidebar runs `plan_board.py poll <card>` every 60 seconds. The first poll appends the `run` block,
  and each poll appends one line to the `log`. When a review by Copilot is posted, the poll reads
  its comments and appends the `result` and a `review` block with its state, its summary and each
  comment with its file and line. The row shows *Copilot reviewing* until then, and *Copilot
  reviewed · 3 comments* after, and a toast offers *Open PR* and *Send to Claude*. Claude's fixes go
  out in a new `ship` card, which waits for ▶ while Peter reviews them in the worktree; nothing is
  pushed until he presses it. A GitHub webhook would need a public URL to call, which the sidebar
  doesn't have, so the card polls.

The view is read with `plan_board.py list`, refreshed by a file watcher on each repo's
`.tmp/plan-board/` and `.tracker/ready.yml`, in its main checkout. Every state has a word and an
icon, never colour alone.

**Review.** Selecting a card opens its review: `why` at the top, then the card's `steps` as the card
lists them, then each file. A spent card shows its `run` block, its `result` and its log below them.
A path to commit opens in the diff editor against `HEAD`
([`vscode.diff`](https://code.visualstudio.com/api/references/commands)), and a message opens as the
file. A body opens as the file for a new PR. For an open PR it opens in the diff editor against
what's on GitHub: `pr-body-live.md` when the card has `live_sha256`, otherwise `pr-body-posted.md`.
A body whose bytes match that copy isn't shown, and `gh pb` doesn't run for it. A file whose hash no
longer matches is marked *changed* in the tree before ▶ is pressed.

**Use my edits.** On a card with a *changed* file, waiting or refused, *Use my edits* lets Peter
keep his own edit without a trip through Claude. It opens each changed file again, a path to commit
in the diff editor against `HEAD` and a message or body as the file. The card holds hashes, not the
planted text, so Peter re-reads each changed file whole, not a diff since planting. Confirming
writes the new hashes and `restamped`. A waiting card is updated in place. A refused card has a
result and can't run again, so its copy is planted with the item's next number. Whatever those files
now hold, Peter's change or Claude's, is what ▶ ships, and reading them again is the review.

**▶ Run.** It opens a terminal named `Plan board: <id> <nn>-<recipe>`, in the recipe's directory,
and runs `plan_board.py run <card>` there, so Peter watches the output live. One terminal per card.
Pressing ▶ on a card whose lock is held does nothing but show the lock's holder.

**Status bar and toast.** The status bar shows `Plan board: 2 waiting`, or `Plan board: running 04
rebase` while a card runs. When a card finishes, a toast says its outcome, with *Open log*, *Open
PR* when there is a URL, *Send back to Claude* when it failed or was refused, and *Use my edits*
when it was refused for a changed file. A finished `worktask-start` offers *Open worktree window*,
which does what clicking the item's row does, in case the window didn't open or Peter closed it.

**Keyboard.** The view takes VS Code's own focus and arrow keys. *Plan board: Start item*, *Plan
board: Run card*, *Plan board: Review card*, *Plan board: Worktree Done*, *Plan board: Open worktree
window*, *Plan board: Use my edits* and *Plan board: Send back to Claude* are commands, so each can
be bound to a key and run from the command palette.

<a id="failure-modes"></a>
### 3.7 Failure modes

| Event | Effect | Handling |
|---|---|---|
| A recipe fails partway, such as `ship` committing then failing to push | The commit is made; nothing is pushed | The result records the step reached, and the log the command that failed. Claude plants the card again, and the resumable steps skip the commit and run from the push |
| A card's files changed after Claude planted it | ▶ would ship something Peter didn't review | The hash check refuses before any write, naming the file. If the change is Peter's, he presses *Use my edits*. Otherwise Claude plants a new card from the files as they stand, without rewriting them |
| The PR's body was edited on GitHub | `gh pb` would overwrite the edit | `ship` and `pr-body` compare the live body with the one last posted and refuse on a difference, saving the live body. Claude folds it in and plants the card again with `live_sha256`, and Peter reviews the fold-in as a diff against the live body |
| A file changes between its hash check and the step that reads it | A commit or a body Peter didn't review | The runner checks the staged blobs and the commit's message before any push and undoes its own commit on a mismatch. It reads a body back after posting it (§3.2) |
| Something outside `paths` is already staged | `git commit` would take it along | `commit` and `ship` refuse before `git add`, naming the staged paths |
| Push rejected because the branch on `origin` moved | Nothing pushed | `failed`, with git's message. Claude plants a `rebase` card, then the `ship` again |
| The branch on `origin` has a commit the worktree doesn't | A rebase and force-push would drop it | `rebase` refuses before it rebases, naming the commit. Claude reconciles the branch, then plants the `rebase` again |
| `rebase` conflicts | The rebase can't finish | The runner aborts it, so the branch is as it was, and records the conflicting files. Claude resolves them on the branch; Peter runs the next `rebase` |
| A card goes stale after its PR merges | A `ship` or `pr-body` would act on a merged PR | `ship` reads the PR's state and refuses a merged or closed PR; `pr-body` needs it open |
| ▶ pressed twice, or in two windows | Two runs of one card | The `O_EXCL` lock lets one through. The second shows the lock's holder. A card with a result never runs again |
| VS Code closes while a card runs | The terminal and the runner end | The card has its `run` block and its log up to that point, but no `result`, and the lock's PID is dead. The view shows *interrupted*; Peter clears the lock and Claude plants the card again, which the resumable steps make safe |
| `worktask-done` refused or failed | The worktree, branch or item isn't finished | The cards stay, with the result, so Claude reads why and plants the card again. They move to `.tmp/plan-board/done/` only after a `done` outcome |
| Pruning can't delete an older done item | `.tmp/plan-board/done/` holds more than three items | The card's log has the warning, and the outcome stays `done`. The next `worktask-done` tries again |
| A card's `runs_in` isn't its recipe's checkout, or ▶ runs it elsewhere | A recipe would act on the wrong checkout | Refused before any write, naming both checkouts |
| ▶ *Start* on an item someone already started | Two starts of one item | `worktask-start` checks the item is Todo on `origin`, or In Progress for a reopen, and has no worktree or branch, and refuses otherwise. A reopen's worktree check is what stops a second one. The ready list, as last fetched, may still show it until *Refresh* |
| Offline | `gh` and pushes fail | `failed`, with the error. Local-only recipes (`commit`) still run |
| Copilot can't be requested, such as on GitHub Enterprise Server or with Copilot review off | The PR is open with no review requested | `ship`'s outcome is still `done`; the result records `review_requested: failed` and the log has `gh`'s message. Peter requests a review on GitHub, or goes on without one. No `review` card is planted |
| A `review` card's poll can't reach GitHub | Copilot's review isn't noticed yet | The log has the error, the row shows *watch paused*, and the next poll tries again |
| The PR is closed or merged before Copilot reviews it | The watch would poll forever | The poll appends a `failed` result with the PR's state, and the watch stops |
| `gh` not signed in | `gh pr view` fails in the checks | Refused before any write, with `gh auth status`'s message |
| Claude tries to run `plan_board.py` | A recipe would run without Peter | The `PreToolUse` hook denies it, and the runner refuses under `CLAUDECODE` |

<a id="worked-example"></a>
### 3.8 Worked example: one item's session

The pl-zupak session, the loop this design was taken from, card by card. Claude's own steps (item
edits, `outline.py`, the test suite) need no card, because Claude runs them itself.

| # | Card | Runs in | What happened, and what the board changes |
|---|---|---|---|
| 01 | `worktask-start` | `gh-plan/` | Peter ran `worktask start pl-zupak`. Now he presses ▶ *Start* on pl-zupak in the ready list. The runner plants card 01 and runs it, and the new worktree's window opens with Claude in it |
| — | — | — | Claude rewrote the item, ran `outline.py` and `uv run pytest`. No cards |
| 02 | `ship` | `gh-plan.worktrees/pl-zupak/` | post-pr planted the message and body and handed over a line. Peter ran it while Claude was still editing the message, and Claude found out only from `git log`. Now the edit changes the message's hash, so ▶ refuses with that reason, and the result tells Claude |
| 03 | `ship` | worktree | Claude plants it again with the new hash. No PR yet, so it commits the named paths, pushes and runs `gh pc` |
| 04 | `rebase` | worktree | pl-veles (#95) merged first. Claude's `git rebase --autostash origin/main` was denied twice, and Peter ran it from a chat question. Now it's a card: fetch, rebase, and `--force` because the branch was pushed |
| 05 | `commit` | worktree | Claude fixed `ready.yml`, which #95's merge had duplicated, and commits it locally |
| 06 | `ship` | worktree | The PR is open, so `ship` commits `ready.yml`'s next fix, pushes and runs `gh pb` |
| 07 | `pr-body` | worktree | A body-only edit runs `gh pb` alone |
| 08 | `worktask-done` | `gh-plan/` | After the merge, from the main checkout, because `worktask done` refuses to run inside the worktree it removes. Once it's done, the runner moves `.tmp/plan-board/pl-zupak/` to `.tmp/plan-board/done/` |

Each card's result reaches Claude at the start of its next turn, so it never plants a card on a
guess about what the last one did. The eight cards, read in order, are the session's record, and
stay under Done until three later items have finished.

<a id="alternatives"></a>
## 4. Alternatives considered

[ADR 0009, *Alternatives considered*](../adr/0009-plan-board.md#alternatives) weighs a FastAPI
console hosting agents through the Agent SDK, Claude Desktop, a terminal-only runner and an HTML
board. Two design choices inside the board were also weighed:

- **A separate *Reviewed* press that pins the hashes.** Two presses per card, and the hashes Claude
  records at planting already catch a file changed after review.
- **Results only in the file, or only in the terminal.** A file alone needs Claude to remember to
  look; typing alone loses the record. The board does both.
- **A result file and a log file beside each card.** Three files per step, and a reader has to put
  them back together. Appending both to the card keeps each step's record in one file.
- **Claude plants card 01, as it plants the rest.** That puts a Claude turn between Peter choosing
  an item and starting it. `worktask-start` commits nothing Claude wrote, so there's nothing to
  review but its checks, and ▶ *Start* in the ready list runs them at once.

<a id="lenses"></a>
## 5. Cross-cutting concerns

| Lens | Answer |
|---|---|
| L1 Security | A card names one of seven recipes and lists the exact commands it will run; the runner refuses a card whose commands aren't its recipe's, and checks every field and refuses anything unknown. The sidebar runs the runner in a terminal and binds no network port. Claude is kept off the runner by the `PreToolUse` hook `deny_plan_board.py` and by the runner's own `CLAUDECODE` check (§3.4). No path outside the item's worktree or main checkout is staged |
| L2 Privacy and data | Cards, with their records and logs, live in the main checkout's `.tmp/plan-board/<id>/`, which is gitignored, and are never committed. After `worktask-done`, they move to `.tmp/plan-board/done/`, which keeps the last three items done and deletes older ones. Logs hold tool versions, paths, and git and `gh` output, which can include branch names and PR URLs, nothing more. Examples here are synthetic |
| L3 Reliability and failure | §3.7: a recipe failing partway, a changed file, a rejected push, a conflict, a stale card, a double press, VS Code closing mid-run |
| L4 Performance and limits | Not affected in any way that matters: `plan_board.py list` reads a few small files per item in flight, in each repo's main checkout, refreshed by a file watcher. `gh pr view` is one call per `ship` or `pr-body`, and one a minute per item waiting for Copilot's review, far inside GitHub's rate limit |
| L5 Observability | While a card runs, its named terminal shows each command and its output, and the status bar names the card. When it ends, a toast gives the outcome, the card keeps the run's versions, log and result, and a failure is one click from Claude |
| L6 Compatibility and migration | `worktask`'s URI handler is kept, so its links keep working. Cards carry `v: 1`. Until the runner ships, Claude plants cards by hand and Peter runs each card's `steps` himself, copied from the card; `post-pr` and `worktask` print their lines as they do now |
| L7 Usability and accessibility | One view across the listed repos: each repo's ready items, and each item in progress with its current card (§3.6). One click switches to an item's window. *Worktree Done* finishes an item without leaving its window. Every state has a word and an icon; the tree is keyboard-operable, and each action is a command |
| L8 Testability | §6: recipes and checks run against temporary repos with a local bare remote and a stub `gh`, without the network |
| L9 Operations and cost | No paid tokens: Claude runs under Peter's Claude Code subscription, and nothing calls the API. No credential is added: `gh` keeps its own login. The extension installs from a local `.vsix`, like `worktask`'s |
| L10 Docs impact | ADR 0009; [architecture §10](../architecture.md#invariants), invariant 5; requirement [`D7`](../requirements.md); `CLAUDE.md`'s boundaries on plan-board cards and on agent commits |
| L11 Invariants | 2 holds: worktree recipes refuse on the default branch, and only `plan-start` writes `plan:` commits. 3 holds: the board makes no tool commit. 5 gains the exception for a push, `gh pc` or `gh pb`, and the Copilot review request that follows `gh pc`, when Peter presses ▶. 6 holds, because the runner lives in `~/.claude/skills/plan-board/`, and gh-plan gains no module that spawns a process |
| L12 Offline and concurrency | Several worktrees' cards are listed and run at once, each in its own terminal. Two presses of one card can't both run (the `O_EXCL` lock), and a card with a result never runs again. Offline, recipes that need the remote fail before writing anything remote |

<a id="testing"></a>
## 6. Testing

The runner's tests live beside it, `~/.claude/skills/plan-board/scripts/test_plan_board.py`, as
`worktask`'s do, and run without the network.

- Each recipe runs against a temporary repo whose `origin` is a local bare repo, so fetch and push
  are real git with no network. `gh` is a stub script first on `PATH` that answers `gh pr view`
  from a fixture and records the `gh pc` and `gh pb` calls.
- Each check gets a refusing case: wrong directory, default branch, a changed hash, a message
  without `Assisted-by:`, a body with attribution, a merged PR, an unknown recipe or version, a
  `card` that doesn't match the file name, a `steps` line that isn't the recipe's.
- `ship` is run in all three PR states, and once after a failed push, to show the commit step is
  skipped and the push runs.
- `commit` with a `deleted` path, once after a `git mv` and once after a plain `rm`: the first
  leaves the path out of `git add`, the second names it, and both commits carry the removal.
- `rebase` gets a conflicting case and asserts the branch is unchanged afterwards.
- `ship` and `pr-body` against a stub PR whose body differs from `pr-body-posted.md`: refused, the
  live body saved, `gh pb` never called. A CRLF copy of the same body passes. The card planted again
  with `live_sha256` passes, and a second edit on GitHub after that refuses.
- `ship` on an open PR whose body matches `pr-body-posted.md`: its steps leave out `gh pb`.
- An edit between a hash check and its step: a path changed before `git add`, and the message
  changed before `git commit`. Each is caught before any push, the runner's commit is undone, and
  nothing reaches the bare remote. A stub `gh` that stores a different body from the one it was
  given makes `ship` fail, with the posted body saved.
- `commit` and `ship` with an unrelated file already staged: refused before `git add`, naming it.
- `rebase` when the bare remote's branch has a commit the worktree lacks: refused before the rebase,
  and the remote unchanged.
- A card with `restamped` runs, and the result carries it.
- `start` plants card 01 with `planted_by: person` and the `person` from `git config user.name`,
  or the `gh` login when that's unset, and runs it. It refuses an item that isn't Todo or already
  has a worktree. A `worktask-start` card for an In Progress item with no worktree or branch runs
  and pushes nothing; with either one left, it is refused.
- The card parses after every line the runner appends, and a run killed partway leaves a card with
  a `run` block and no `result`, which `run` then refuses.
- A card whose `runs_in` doesn't match its recipe, and a card run from the other checkout: both
  refused.
- `worktask-done` moves `.tmp/plan-board/<id>/` to `.tmp/plan-board/done/` after a `done` outcome,
  and leaves it after a refusal or a failure. With three items already done, the one with the oldest
  `finished` is deleted whatever the files' modified times, the deletion is in the card's log, and a
  deletion that fails leaves the outcome `done`.
- `ship` with no PR requests Copilot after `gh pc` and records it; a stubbed request that fails
  leaves the outcome `done` with `review_requested: failed`. `ship` on an open PR doesn't
  re-request.
- A `ship` that requests Copilot plants the next card, a `review` with `planted_by: runner`. `poll`
  against stub PRs: no review yet appends one log line, a review by Copilot has its comments read
  with `--paginate` and appends the `result` and the `review` block once, with each comment's file
  and line, a closed PR appends a `failed` result, and a card with a result
  refuses the next poll. `results.py` adds the `review` block once.
- The lock: two `run`s of one card at once, and a stale lock.
- `deny_plan_board.py` and `results.py` are run with sample hook input on stdin, and their JSON
  output is checked. `results.py` skips `.tmp/plan-board/done/`.
- The extension's view model, the ready, in-progress and done sections and each item's current
  card, is a plain module over `plan_board.py list`'s JSON, tested with `node --test`.
  The VS Code glue is a thin layer over it, checked by hand.

gh-plan's own suite is unaffected, because nothing in this design adds code to gh-plan.

<a id="requirements-coverage"></a>
## 7. Requirements coverage

| # | Requirement | How |
|---|---|---|
| D5 | Concurrent sessions and worktrees | Each item's cards are its own; several run at once, each in its own terminal; a lock stops a double run |
| D7 | One view of every item in progress across repos | The board view across `planBoard.repos`: each repo's ready items, and each item in progress with its branch, worktree and current card |
| G3 | No new credential | The runner shells out to `gh`, which keeps its own login |
| I1 | Free for a single user | No API tokens; Claude runs under Peter's subscription |

<a id="work-breakdown"></a>
## 8. Work breakdown

| Item | Estimate |
|---|---|
| `plan_board.py`: the card format, the lock, the appended run, log and result, `start`, `poll`, keeping the last three items done, and the seven recipes with their checks, the live-body check and tests | 3 |
| `deny_plan_board.py` and `results.py` hooks, registered in user settings, with tests | 2 |
| The sidebar: the ready, in-progress and done sections across repos, review in the diff editor, *Use my edits*, ▶ and ▶ *Start* in a named terminal, switching to an item's window, polling a `review` card every 60 s, *Worktree Done* | 3 |
| The sidebar's status bar, toast and "Send back to Claude" | 2 |
| `post-pr`, `worktask` and gh-issues' *Complete* plant cards instead of handing over lines; `worktask start` adds the main checkout to the worktree's `additionalDirectories`; the `plan-board` skill tells Claude how to plant one | 2 |
