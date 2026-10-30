# Design 0005: Review findings in Redmine, weighted by what they lose

## Contents

- [1. Context and scope](#context)
- [2. Goals and non-goals](#goals)
- [3. Design](#design)
  - [3.1 The plugin's schema](#schema)
  - [3.2 The weighting](#weighting)
  - [3.3 Recording a round](#writer)
  - [3.4 Confirming what Claude drafted](#confirming)
  - [3.5 Rounds: convergence and the stop rule](#rounds)
  - [3.6 Queries](#queries)
  - [3.7 The codify rule](#codify)
  - [3.8 The eval set](#eval)
  - [3.9 A reviewer in CI](#ci)
  - [3.10 Failure modes](#failure-modes)
- [4. Alternatives considered](#alternatives)
- [5. Cross-cutting concerns](#cross-cutting)
- [6. Testing](#testing)
- [7. Requirements coverage](#requirements-coverage)
- [8. Work breakdown](#work-breakdown)

<a id="context"></a>
## 1. Context and scope

Every branch gets `/code-review high` rounds, and each round leaves findings.
Today a finding lives in two places, a PR thread and the session that fixed it, and neither can be
counted. #82 had 38 findings over rounds three to six. Only 3 reached PR #114's threads, because
rounds three to six ran locally, so the class of each one and what was done about it are recorded
only in that session's transcript. Nothing can answer how often a class of bug turns up, whether a
rule is worth a checker, or whether a change to the reviewer finds more real bugs or fewer.

This design is for `review_findings`, a Redmine plugin in `redmine/plugins/` that keeps every
review as a ledger: each round, each finding it raises, each answer and each check of a fix is a
row, added and never changed. Each finding belongs to an issue, is keyed by its file, lines and
rule, is weighted by what it loses and whether it fails silently, and is answered with an outcome. The confirmed silent failures become an eval set,
and every change to the reviewer is scored against it.

It covers the reviewer as gh-plan runs it: locally, through Claude Code on the maintainer's subscription,
with Ship's `pr` pushing every round. The review's record lives in Redmine, and the PR holds only
the commits, the squash merge and a body rendered from the store. #104 sets that reviewer up in
place of Copilot, #105 adds `assess` and `checks/`, #106 the outcome line, #107 the rounds, #108 the
`findings` script, #110 the round report and #111 the Done ticks; §8 says what each of them changes
to fit this design.

<a id="goals"></a>
## 2. Goals and non-goals

Goals:

- Every finding, its rule and its outcome are rows that SQL can query across every review.
- The rows are a ledger: nothing is changed or deleted, and the read API adds the rows up into
  each finding's latest status.
- The PR carries nothing of the review that has to last: no finding is a PR comment, and its body
  is rendered from the store.
- Only a silent finding that loses something, or a crash, has to be fixed, and the store shows
  each one that's still open.
- Nothing Claude drafts is recorded as an outcome, a rule, a severity or a visibility until a person
  has accepted it.
- Rounds converge: round 1 reviews the whole change, and each later round checks the earlier
  fixes, one finding at a time, reviews only the code added since, and ends on a rule.
- A rule found a third time in one PR becomes a scripted check, so the rest of its matches are
  searched for rather than sampled.
- A rule that keeps finding real bugs becomes a checker, and one that keeps raising noise becomes a
  line in `REVIEW.md`.
- A change to the reviewer is kept only if it scores better on the eval set.

Non-goals:

- Copilot code review. GitHub's docs say replies to it are "visible to other people but not to
  Copilot", and that it "may repeat previous comments, even if you resolved or downvoted them"
  ([Using Copilot code review](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/copilot-code-review)).
  Neither can be fixed from our side.
- ms-security. Its threat model calls for a weighting of its own.
- Building any of it. That's the work items in §8.

<a id="design"></a>
## 3. Design

<a id="schema"></a>
### 3.1 The plugin's schema

The plugin adds two tables, `review_findings` and `review_rounds`, and a project module named `review_findings`. Both are a ledger: rows are only added, never changed or deleted. One `review_rounds` row is one reviewed commit. One `review_findings` row is one event on one finding: the reviewer raising it, a person answering it, or a later round checking its fix. #111 adds another table to the same plugin, for the issue's Done lines.

**A finding's key** is `file:lines:rule`, for example `vscode/done.js:214-219:ignored-file-overwrite`, or `vscode/done.js:214:ignored-file-overwrite` for one line. Every finding names a file and lines in it, in code or in a committed doc; a design finding points at the design's lines. Something the reviewer can't place is a note in the round's `summary`, not a finding. The key is set when the finding is first raised, from the lines at that round's commit, and later rows for the finding carry the same key even after the code moves. Two findings with one key in one round should be very rare, since the rule is part of the key; when it happens they're one finding, weighted as the heavier (silent before seen, any loss before `nothing`), with the other's summary appended to the body.

`review_findings`:

| Field | Type | Allowed values | What it holds |
|---|---|---|---|
| `id` | integer | | Redmine's row id |
| `issue_id` | integer, not null | An issue's id | The issue whose branch was reviewed |
| `round` | integer, not null | 1 or more, or 0 for a missed finding | The round the event belongs to. A missed finding is a silent failure no round caught, found after the merge in use or in a later issue's review; it's recorded against the issue it escaped as a `review` row in round 0, answered like any finding, and becomes an eval case (§3.8) |
| `source` | string, not null | `review`, `outcomes` | `review`: the reviewer wrote it. `outcomes`: a person accepted it at Go, from `<k>-outcomes.md` (§3.4) |
| `file` | string, not null | A repo-relative path | The key's file |
| `line_start`, `line_end` | integer, not null | 1 or more, `line_end` ≥ `line_start` | The key's lines |
| `rule_id` | string, not null | A kebab-case rule `REVIEW.md` names | The key's rule, the finding's class of bug |
| `outcome` | string, not null | `new`, `fixed`, `intentional`, `wrong`, `doesnt-matter` | The finding's state after this event (below) |
| `tag` | string | `Important`, `Nit`, `Pre-existing`, `Security` | The reviewer's tag (#104) |
| `severity` | string, not null | `work`, `files`, `commits`, `tracker`, `nothing` | What the failure loses (§3.2) |
| `visibility` | string | `silent`, `seen`, or empty | Whether the user is shown that it failed. Empty only when `severity` is `nothing` and the finding describes no failure, such as a doc or code-quality finding |
| `summary` | string(255) | | The finding's first line, on the row that raised it |
| `body` | text | | The failure scenario on a raising row; on a checking row, what the fix was checked against |
| `justification` | text | | Why, for an `outcomes` row whose outcome isn't `fixed` |
| `confirmed_by_id` | integer | A user's id; set on every `outcomes` row, empty on every `review` row | Who accepted the outcome, severity and visibility (§3.4) |
| `created_on` | datetime | | When the row was added. There's no `updated_on`, since a row never changes |

Empty is always SQL `NULL`: the model turns an empty string into `NULL` before it validates.

**Outcomes.** The ledger reuses the outcome words, with `new` for open:

- `new`: open. The reviewer raised it, or a later round found its fix didn't hold.
- `fixed`: from `outcomes`, the code changed and the next round checks it; from `review`, a later round checked the fix and it held.
- `intentional`: the behaviour is wanted, usually an Open row in `docs/failure-modes.md`.
- `wrong`: the finding misreads the code.
- `doesnt-matter`: it's real, and not worth the change.

`fixed` and `intentional` count as real findings; `wrong` and `doesnt-matter` count as noise (§3.7).

**A finding's status is its latest row**, by `round`, then `review` before `outcomes` within a round. Nothing stores the status; the read API works it out from the rows (below). R6.3 from #82, as a live run would record it, with an illustrative key:

| Event | `round` | `source` | `outcome` | Status after |
|---|---|---|---|---|
| Round 6 raises it, `vscode/done.js:214-219:ignored-file-overwrite`, `silent/files` | 6 | `review` | `new` | Open to fix |
| A person accepts `6-outcomes.md` at Go | 6 | `outcomes` | `fixed` | Fixed, waiting for a check |
| Round 7 checks the fix: it held | 7 | `review` | `fixed` | Fixed and checked |
| …or it didn't hold | 7 | `review` | `new` | Open to fix again |

**Unique key.** `(issue_id, file, line_start, line_end, rule_id, round, source)`, with a unique index: one event per finding per round from each source.

**Rounds.** A `review_rounds` row records one review of one commit, so a round with no findings, or one whose `ci` didn't pass, still leaves a trace. Its fields are `<k>-findings.md`'s: `issue_id`, `round`, `commit` (a full SHA), `ci` (`success`, `failure` or `none`), `ci_summary`, `repo` (`owner/repo`) and `pr_number`, the PR the commit was pushed to, `base`, `effort` (`medium` or `high`), `review_md` (`base` or `commit`), `summary` (with the reviewer's notes it couldn't place), and the lists `controls` and `new_rules`. A finding's commit and PR are its round's; a missed finding's PR is the last round's of the issue it escaped. Every review is numbered, whatever its `ci`. Two unique indexes, `(issue_id, round)` and `(issue_id, commit)`, mean a round number is used once and a commit is reviewed once. The issue's last round is its highest `review_rounds` row, and the API returns it as `last_round`, so nothing outside Redmine numbers a round.

**The plugin's API.** Each action has `accept_api_auth`, so `pr` reaches it with the Keychain key.

| Route | Does | Permission |
|---|---|---|
| `GET /issues/:issue_id/review_findings.json` | The issue's ledger and what it adds up to: `last_round`; `review_rounds`, in order; `review_findings`, every row; `findings`, one entry per key with its latest row and its status; and `rules`, the number of distinct keys per rule, over every round, for the third-repeat check (§3.7) | `view_review_findings` |
| `POST /issues/:issue_id/review_findings.json` | Adds the body's `review_round`, if any, and its `review_findings` rows, in one transaction. A row or round identical to one already there is skipped, so a second send adds nothing. One whose unique key is already there with different values, or a round whose number isn't `last_round` + 1, is refused with 422, and nothing in the request is saved | `manage_review_findings` |
| `GET /projects/:project_id/review_findings/rules.json` | Per rule: real and noise counts over the latest confirmed row of each key (§3.7) | `view_review_findings` |
| `GET /projects/:project_id/review_findings/rules/:rule_id.json` | How many keys have the rule, and how many have a confirmed row, over the same issues as `rules.json` | `view_review_findings` |
| `PUT /projects/:project_id/review_findings/rules/:rule_id.json` | Renames the rule to the body's `new_rule_id` on every row in the project and its sub-projects, in one transaction, with a journal note on each issue it touches (§3.10). It's the one change to existing rows, since a rule's name is a label and not an event. A row on an issue the user can't see or can't manage findings in refuses the whole rename | `manage_review_findings` |
| `GET /projects/:project_id/review_findings` | The Findings tab (§5, L5) | `view_review_findings` |

A `POST` or `PUT` that names a value outside the allowed values above is refused with 422, and
nothing in it is saved.

<a id="weighting"></a>
### 3.2 The weighting

A finding gets two values. `severity` is what the failure loses: the user's work, files, commits,
the tracker's state, or nothing. `visibility` is whether the user is shown that it failed: `silent`
if nothing says so, `seen` if an error, a refusal or a wrong result is in front of them.

**A finding has to be fixed when it's `silent` and loses something, or when it's a crash.** A seen
failure is already mitigated, since the user knows to look. A crash isn't. It's loud, but an
unhandled error that stops the command with a traceback, not a message, doesn't tell the user what
it left behind or whether running it again is safe. Every crash gets the rule `crash`, however rare,
and is weighted by what it leaves: a crash part way through a write by what it loses, and one before
any write as `nothing` and `seen`. This is the rule `CLAUDE.md` gives, written as two fields and a
rule so the store can count it. A finding that has to be fixed is a *finding to fix*.

#82's 38 findings, from rounds three to six, sorted this way. The labels are the session's, R and
the round and the finding's number. This is Claude's draft; a person confirms each silent row before
W6 imports it (§3.4).

| Bucket | Count | Findings |
|---|---|---|
| Silent, loses something | 9 | R4.1 `-D` on the base branch (commits) · R4.5 a tag named `main` let `update-ref` overwrite unpushed commits (commits) · R5.1 a lookup by head name removed the review worktree (files) · R5.7 a local branch `origin/main` stood in for the remote (commits) · R5.8 `update-ref` made a base that wasn't there (commits) · R6.1 `-D develop` on a release or fork PR (commits) · R6.3 the fast-forward overwrote an ignored file (files) · R6.4 a false "Brought" note (tracker) · R3.1 a fetch closes another issue (tracker) |
| Seen | 16 | Loses something: R3.5 an untracked file blocked the fast-forward after the worktree was gone (work) · R5.2 the same for case and folder clashes (work). Nothing lost: R3.2, R3.3, R3.4, R3.7, R3.10, R4.4, R4.8, R4.10, R5.3, R5.4, R5.6, R5.9, R6.5, R6.6 |
| Nothing lost | 13 | Silent: R3.6 GitHub keeps the merged branch · R4.2 a part-way failure hid what had changed · R4.6 a kept branch wasn't named · R4.7 Go and Done at once · R5.5 the fast-forward went unreported · R6.2 a locked worktree. Not a failure (`visibility` empty): R4.3 and R5.10 docs · R3.8 the README put the key in argv (`Security`) · R3.9, R4.9, R6.7 and R6.8 code quality (`Nit`) |

Every one of the nine in the first bucket was fixed, except R3.1, which is an Open row in
`docs/failure-modes.md`.

**The seed rows.** W6 imports the 38 once W7 has rebuilt the eval cases (§3.8), so each can
carry a key. Each seed is two rows, a `review` row of `new` and an `outcomes` row with its outcome,
both with `issue_id` 82, `round` 3 to 6 from the label, `tag`, `severity`, `visibility` and
`summary` from the transcript. Rounds three to six were never pushed, so the seeds have no
`review_rounds` rows. The key's file and lines are the case's for the eight W7 rebuilds; for the
other 30, the import file gives the lines in `2b3d8e5` where the finding's code is. No rule existed
then, so the import file gives each a `rule_id` Claude drafts. A person confirms the lines and rule
with the rest, and a seed whose code can't be placed, or whose rule they reject, is left out.
`outcome` is `fixed` for all but six: R3.1, R3.6, R3.10,
R4.7 and R6.6 are `intentional`, since they're Open rows, and R4.9 is `doesnt-matter`, since it
was skipped. R4.8 was retired in round five, so it's `fixed`. `created_on` is the date of the
session, 2026-10-19, so the queries in §3.6 place the rows in their week. The import takes only
the rows a person has confirmed in the import file, with `confirmed_by_id` set on each `outcomes`
row, since #82's worktree is gone and no outcome line could confirm one later.
`import_findings.rb` runs once, by the maintainer, through `rails runner`, and invariant 6's added clause
names it beside `findings.py`.

<a id="writer"></a>
### 3.3 Recording a round

A round is two turns, the reviewer's and the author's, and each ends in a fixed state. The
reviewer's turn starts from a commit Ship pushed and ends with the round in Redmine and the PR body
updated. The author's turn answers the round and ends when Ship pushes the next commit. No finding
is posted to the PR.

Redmine is required. The ledger is the record, and nothing stands in for it while Redmine is down:
a send that can't reach it stops and says so, the team brings Redmine back, and the next send
carries on from the round's files.

Claude's one Redmine write is the reviewer's: `findings.py send` records the round and its findings. An outcome, severity or visibility is confirmed only from `<k>-outcomes.md`, which Ship's `pr` sends after a person reviews it at Go. Each round's files sit in the issue's `.tmp/<n>/` folder, named by the round's number `k`, and stay until the PR is closed:

```
.tmp/<n>/<k>-findings.md        round k's review: its findings, its checks of earlier fixes, and ci
.tmp/<n>/<k>-outcomes.md        a person's answers to round k's findings (§3.4)
.tmp/<n>/<k>-check-<rule>.py    a check round k wrote for a rule's third finding, until Go commits it (§3.7)
```

`<k>-findings.md` and `<k>-outcomes.md` share one format: a line per finding that a person reads,
then one fenced `json` block with the same rows, and in `<k>-findings.md` the round's fields.
`findings.py` writes the lines from the block. `send` posts only the block, after checking that the
lines still match it, so a file edited on one side only stops `send`, naming the line. A finding's
line has the outcome line's shape (§3.4), with the summary after the dash:

```
vscode/done.js:214-219:ignored-file-overwrite · New · silent/files — The fast-forward overwrites an ignored file in the main checkout without a word
```

Every round reviews a commit Ship has pushed, so the round's commit is on GitHub, and
`scripts/assess`, which `review.py` runs on that commit, throttles the rounds. There's no review
before a push: round 1 reviews the commit the first Ship pushed when it opened the PR. The steps:

1. **The reviewer's turn.** Ship has pushed commit `C`, and `pr` starts `review.py`, unless a round file or a Redmine round already names `C`. It reads `last_round` from Redmine, so the round is `k` = `last_round` + 1, and stops, saying so, when Redmine can't answer. It runs the round (§3.5) and `scripts/assess` in a worktree of `C`, and writes `.tmp/<n>/<k>-findings.md`, with assess's result under `ci`. Then it runs `findings.py send <n>` to record the round, and the PR body is updated with `gh pb`, its Done list rendered as it stands (#111). The `send` and the `gh pb` are the two writes outside a button in `CLAUDE.md`'s Boundaries.
2. **The author's turn.** Claude fixes the findings to fix (§3.2), drafts `<k>-outcomes.md`
   with a line for every finding round `k` raised, and writes the round report (#110): each
   finding with its fix or the note refuting it, the open-to-fix count, `commit.txt` and
   `pr-body.md`.
3. **A person reviews the report and presses Go.** The Review tab links `<k>-outcomes.md`, as it
   links `commit.txt` and `pr-body.md`, and shows the staged diff after Go. Ship refuses a
   `<k>-outcomes.md`, `commit.txt` or `pr-body.md` changed since Go.
4. **Ship runs `pr`.** `pr` commits and pushes `C+1`, the next round's commit, and updates the PR. Then it runs `findings.py send <n>`, and starts the review of `C+1`.

`findings.py send <n>` posts, in round order and each round's findings before its outcomes, every
round file whose rows Redmine doesn't have yet, with `POST /issues/<n>/review_findings.json`:

- **`<k>-findings.md`**: the round and its rows. A findings file whose round Redmine lacks is a
  review that ran and wasn't recorded, so `send` posts it as it is, and `review.py` doesn't review
  that commit again.
- **`<k>-outcomes.md`**: one `outcomes` row per line, with `confirmed_by_id` set to the key's user.
  A line whose key round `k` or an earlier round didn't raise stops `send` before anything in the
  file is recorded.

After each post `send` reads the issue back, and prints the open-to-fix count from the GET (§3.5).
It deletes nothing.

`findings.py` lives with `pr` in `.claude/skills/gh-issues/scripts/`, and uses the skill's
`redmine.py`: the `Redmine` client, the Keychain's `redmine-gh-plan` key and its `Refused` error.
It's the one writer this design adds. When a connection drops part way through a write, `redmine.py` raises `Refused` and says the write may have been applied; the next `send` finds what landed and adds the rest.

**Recording twice adds nothing.** The `POST` skips a row or round identical to one already
there, so a second send of a file adds no row, and a `pr` that stops part way is finished by the
next one. A row whose unique key is there with other values is refused with 422, nothing in the
request is saved, and `send` names the line: a file changed after it was sent isn't a new answer.
A later answer is a line in a later round's `<k>-outcomes.md` (§3.4).

**Done refuses until Redmine has every round.** Done removes the worktree, and `.tmp/<n>/` with it. It reads the issue's ledger first, and while a `<k>-findings.md` round or a `<k>-outcomes.md` row isn't in it, Done names the file and `findings.py send <n>`, and stops, so a round Redmine never got isn't deleted with the worktree.

<a id="confirming"></a>
### 3.4 Confirming what Claude drafted

The reviewer drafts a rule, a severity and a visibility for each finding, and Claude drafts its
outcome. None of them is confirmed until a person accepts the line that carries them: the finding's
line in `<k>-outcomes.md`, its key and then #106's outcome line, with a third part:

```
vscode/done.js:214-219:ignored-file-overwrite · Fixed · silent/files
vscode/github.js:88:repo-rename · Intentional · seen/nothing — a repo renamed on GitHub is refused with a message; Open row "Repo renamed"
```

- The key is the finding's, as round `k` raised it. Its rule is the rule's id as `REVIEW.md` names
  it; a new rule is added to `REVIEW.md` in the same round, so the person sees it in the diff.
- The first part is the outcome: `Fixed`, `Intentional`, `Wrong` or `Doesnt-matter`.
- The second is `visibility/severity`, with `-` for an empty visibility.
- Any outcome but `Fixed` carries its justification after a dash, or on the lines below.

The rule is part of the key, so a line can't change it. A person who thinks the rule is wrong
answers `Wrong` and names the rule that fits, which counts the finding as that rule's noise
(§3.7).

A person accepts the lot by pressing Go, after changing any line they disagree with; Ship then sends
exactly what they saw. A line they delete isn't recorded: the finding stays `new`, and a silent one
keeps the next round coming, up to round 3 (§3.5). A line in a later round's file may answer an
earlier round's finding, or change an earlier answer, and is recorded in that later round. Only
`outcomes` rows carry `confirmed_by_id`, so only they count in §3.6's counts and in §3.7; §3.6's
list of open findings to fix is the ones with no answer since they were raised.

`Fixed` isn't taken on trust: the next round checks it (§3.5). Its severity and visibility are
still a person's to accept, since those decide what counts.

<a id="rounds"></a>
### 3.5 Rounds: convergence and the stop rule

Rounds converge. A reviewer can't cover every point of a change, so a second full review samples a
different part of it, and its findings are new rather than fewer. Round 1 is the one full review;
every later round checks the fixes, finding by finding, in their context, and reviews only the code
added since the round before it.

**Round 1** reads the branch's whole diff against `main`, the issue's description, `REVIEW.md`,
and the docs `CLAUDE.md` says to read before the kind of change the diff makes, and raises its
findings.

**Round 2 on** checks each finding whose latest row is an `outcomes` row of `fixed`. For each, the
reviewer gets, as data in a fenced block it's told not to follow:

- the key, and the summary and body of the row that raised it;
- the fix: the diff from the round that answered it to this round's commit, within the key's file
  and the code the fix touched;
- that code's first and second degree relations: the symbols the fix changed, their callers and
  callees, the docs that name them, and code with the same syntax.

It answers each with a `review` row in this round: `fixed` when the fix held, `new` when it didn't.
A finding answered `intentional`, `wrong` or `doesnt-matter` was a person's call, and isn't checked
again. A finding still `new` is waiting for an answer, not another review. A later round raises a
new finding only when the fixed code shows a finding to fix (§3.2), with its key at this round's
commit.

**New code in a later round.** When the diff since the last round, Skipped paths aside, changes a
file that no fix being checked names, the author has widened the change. Round 1's review and
security passes then run beside the check, over that diff alone, so code added after round 1 is
reviewed and round 1's findings aren't raised again. Only the branch's own paths count, so a
rebase that brings in `main`'s changes doesn't start them.

**A rule's third finding becomes a check.** The issue's GET returns, per rule, how many keys the
ledger has over every round. When the reviewer would raise a third finding under one rule, it
writes `<k>-check-<rule>.py` instead (§3.7), runs it on the round's commit, and raises a finding for
each match; the round's summary names the check. Go commits it to `checks/` with the fixes, so
`assess` runs it on every later commit, and a fix holds when it prints nothing. The check shows the rule was searched for across the change, not sampled
a third time.

**The stop rule.** A finding is *open to fix* when it's a finding to fix (§3.2) and its latest row
is `new`. That's a `silent` finding whose `severity` isn't `nothing`, or one with the rule `crash`.
The issue's GET returns the count, and `findings.py` prints it after each send; the PR body's review
line gives it: "Claude review: round 2, 0 open to fix".

- Another round runs while the open-to-fix count is above zero, while a finding to fix has an
  `outcomes` row of `fixed` that no later round has checked, while the last round's `ci` isn't
  `success`, or while the PR's head is a commit no round has reviewed. An open finding needs an
  answer, a `fixed` one needs checking, and a failed `ci` needs a passing commit. The last check
  means a Ship after the final round, carrying its answers, gets a round of its own. When all four
  are clear, the round just run is the last.
- Round 3 checks round 2's fixes like any round. Its report, and the PR body it renders, also ask
  the author to step back: the rules with findings in rounds 1 to 3, which findings came from an
  earlier round's fix, and whether the approach is wrong. A finding to fix still open after round 3
  stays in the count `pr` prints and the PR body shows. #82 is the case for it: about 14 of its 38
  findings came from an earlier round's fix, and rounds five and six needed forks, a tag named
  `main` and ssh aliases to reach.
- After round 3, a round runs only on `pr --review`, so a fix made in answer to round 3 is
  checked only if a person asks for one.

<a id="queries"></a>
### 3.6 Queries

The store is Redmine's SQLite database, so any question about findings is a `SELECT`.
`redmine/sql.rb`, mounted at `/setup/sql.rb`, runs one, on a connection opened read-only, and
prints the rows as tab-separated text:

```
docker compose -f redmine/compose.yml exec -T -u redmine redmine ruby /setup/sql.rb "<SELECT …>"
```

It opens `sqlite/redmine.db` with `SQLite3::Database.new(path, readonly: true)`. In the running
image (sqlite3 gem 2.9.6, SQLite 3.53.2) a write on that connection raises
`SQLite3::ReadOnlyException`, so a mistyped query can't change the tracker. `VACUUM INTO` is the exception: it writes a copy of the database even on a read-only connection, so `sql.rb` refuses any statement that starts with `VACUUM` before it opens the database. It also refuses a second statement after the first, and runs only as the database's owner, since a read-only connection still creates the `-wal` and `-shm` files and root's would stop Redmine writing. `NULL` prints as an empty field. A tab, newline, carriage return or backslash prints as `\t`, `\n`, `\r` or `\\`, and any other control character as `\x` and two hex digits, so each row stays on one line.

The ledger holds events, so every query starts from one row per finding: its latest, by round and
then `outcomes` after `review`. The plugin's GET works out each finding's status with the same
`latest` rows, for one issue:

```sql
WITH ranked AS (
  SELECT f.*, ROW_NUMBER() OVER (
    PARTITION BY f.issue_id, f.file, f.line_start, f.line_end, f.rule_id
    ORDER BY f.round DESC, f.source = 'outcomes' DESC) AS n
  FROM review_findings f
  JOIN issues i ON i.id = f.issue_id
),
latest AS (SELECT * FROM ranked WHERE n = 1)
```

The join on `issues` leaves out a finding whose issue was deleted, as the plugin's routes do.

Counts by rule and outcome, over accepted answers, each finding counted once by its latest answer:

```sql
WITH answered AS (
  SELECT f.*, ROW_NUMBER() OVER (
    PARTITION BY f.issue_id, f.file, f.line_start, f.line_end, f.rule_id
    ORDER BY f.round DESC) AS n
  FROM review_findings f
  JOIN issues i ON i.id = f.issue_id
  WHERE f.source = 'outcomes'
)
SELECT rule_id, outcome, COUNT(*) AS n
FROM answered
WHERE n = 1
GROUP BY rule_id, outcome
ORDER BY rule_id, outcome;
```

A finding whose fix a later round found didn't hold still counts by its last answer until a person
answers it again. A finding no one has answered has no `outcomes` row, so it isn't in these counts.
The open-to-fix query below lists the unanswered ones that are findings to fix, and this one lists
every unanswered finding by rule:

```sql
WITH ranked AS (/* as above */),
latest AS (SELECT * FROM ranked WHERE n = 1)
SELECT rule_id, COUNT(*) AS n
FROM latest l
WHERE NOT EXISTS (
  SELECT 1 FROM review_findings o
  WHERE o.source = 'outcomes' AND o.issue_id = l.issue_id AND o.file = l.file
    AND o.line_start = l.line_start AND o.line_end = l.line_end AND o.rule_id = l.rule_id)
GROUP BY rule_id
ORDER BY rule_id;
```

A rule's findings over time, by week, over the same `answered` rows. Each finding counts in the
week it was first raised:

```sql
WITH answered AS (/* as above */),
found AS (
  SELECT issue_id, file, line_start, line_end, rule_id, MIN(created_on) AS found_on
  FROM review_findings
  GROUP BY issue_id, file, line_start, line_end, rule_id
)
SELECT strftime('%Y-%W', found.found_on) AS week, answered.outcome, COUNT(*) AS n
FROM answered JOIN found USING (issue_id, file, line_start, line_end, rule_id)
WHERE answered.n = 1 AND answered.rule_id = 'ignored-file-overwrite'
GROUP BY week, answered.outcome
ORDER BY week, answered.outcome;
```

The open findings to fix, across every issue, over the `latest` rows:

```sql
WITH ranked AS (/* as above */),
latest AS (SELECT * FROM ranked WHERE n = 1)
SELECT i.id AS issue, i.subject, l.round,
       l.file || ':' || l.line_start
         || CASE WHEN l.line_end <> l.line_start THEN '-' || l.line_end ELSE '' END
         || ':' || l.rule_id AS key,
       l.severity, l.summary
FROM latest l
JOIN issues i ON i.id = l.issue_id
WHERE l.outcome = 'new'
  AND ((l.visibility = 'silent' AND l.severity <> 'nothing') OR l.rule_id = 'crash')
ORDER BY i.id, l.round, l.id;
```

<a id="codify"></a>
### 3.7 The codify rule

**A check** is a Python file. Run from the repo root with `python3`, it prints one line per match,
`file:lines: message`, with the path relative to the root, and exits 0. It passes when it prints
nothing; a non-zero exit means the check itself failed. Every check, whether written for one PR or
committed to `checks/`, has this one interface, so a check written for a PR can be committed as it
is, checks can be compared over time, and code they share can move into a module.

**Within a PR**, a rule's third finding over the issue's rounds becomes `<k>-check-<rule>.py`
(§3.5). Its matches are the rule's findings in that round. A model wrote it from a diff an author
wrote, so it runs under macOS's `sandbox-exec`: no network, no Keychain, no reads under the home
directory but its own file and the commit's tree, no writes outside a scratch directory, and only
`PATH`, `HOME` and `LANG` in its environment. Without `sandbox-exec` it isn't run, and the round's
summary says so. Go commits it with the fixes as
`checks/<rule>.py`, with a `# rule: <id>` line, so the check is kept and `assess` runs it on every
later commit. When `checks/<rule>.py` already exists, the round's check is merged into it rather
than written beside it, so a rule has one checker that grows with each PR that adds a case. A
match answered `Intentional` or `Wrong` goes in the checker's own list of lines it skips, with the
outcome's justification as the comment, so the committed checker prints nothing on the fixed
commit. A check that can't be made to print nothing without skipping most of its matches is too
broad: it isn't committed, and the round's summary says so.

The checks are kept over time by git. `git log -- checks/<rule>.py` shows each PR that added to a
rule's checker, and the round's summary in Redmine names the check it wrote, so the ledger and the
checker can be read side by side. The periodic aggregation is the `findings` script below: per
rule, the real and noise counts, whether `checks/` has a checker, and how many PRs have changed
it.

**Across the project**, over accepted findings with a rule, each counted once by its latest
answer, as in the first query in §3.6:

- **Three or more real findings** (`fixed` or `intentional`) for a rule with no checker mean it
  gets one in `checks/`, `<rule>.py`, carrying a `# rule: <id>` line, which `assess` runs before
  the agent review (#105). This catches a rule whose findings came one or two to a PR, so no PR
  reached its third.
- **Three or more noise findings** (`wrong` or `doesnt-matter`) mean it gets a line in `REVIEW.md`
  saying what not to flag.

A rule can reach both, and gets both.

**How the store replaces the `findings` script's reading of PR threads.** #108's `findings` script
counts outcomes by parsing reply first lines from every PR's threads. With the store, it calls
`GET /projects/plan/review_findings/rules.json` instead, which counts each finding's latest
`outcomes` row in SQL and returns `real` and `noise` for each rule. The script then greps `checks/`
for each rule's `# rule:` line, counts the PRs that changed each checker with `git log`, and names
the rules that are due a checker or a `REVIEW.md` line.
#82's findings, which never reached a thread, count too once W6 imports them. While Redmine is
down, the script says so and prints nothing; it never falls back to a partial count from threads.

<a id="eval"></a>
### 3.8 The eval set

**What becomes a case.**

- **A confirmed silent finding.** Accepted, `silent`, `severity` not `nothing`, and `fixed`. The
  case is the code before the fix: the pushed commit the finding was recorded on, or for the seeds, the
  merged commit with that fix reverted (`2b3d8e5` for #82). The expected answer is a silent finding
  that loses something, at the same place. A finding seen in several rounds is one case.
- **A missed finding.** A silent failure that turned up after the merge, in use or in a later
  review. The case is the PR's last reviewed commit, and the expected answer is the bug that turned
  up. Each is added as a `review` row of `new`, with `tag` `Important`, `round` 0 and its key, so
  the store holds it with the rest. `findings.py add-missed <n> <file> <lines> <rule>` writes it
  to `.tmp/<n>/0-findings-<m>.md` of the issue `<n>` whose work found it, against the issue `<m>`
  it escaped, and it's answered by an outcome line like any finding, `Fixed` once its fix ships. Until
  then it's in the open-to-fix query, as it should be: the bug is still in the code.
- **A clean change.** A merged PR whose code nothing has since found a silent failure in. The
  expected answer is no silent finding; one counts as a false alarm.

The seeds give eight confirmed silent cases, every row in §3.2's first bucket but R3.1, which is
still in the code. #114 was squash-merged as `2b3d8e5` and rounds three to six were never pushed,
so no commit holds any one fix on its own. GitHub keeps #114's two commits, `296ce28` and its head
`5fdd355` (`refs/pull/114/head`), and this clone has no branch, reflog entry or dangling commit
from rounds three to six. Before W7 rebuilds a case, it asks a person to look for those rounds'
commits in any other clone or archive, and builds the case on the round's own commit where one
turns up. Otherwise W7 rebuilds it by hand, on a branch off `2b3d8e5`, undoing that fix's lines,
and records the bug's key with the case.
With six clean PRs, the set starts at fourteen cases. GitHub keeps `refs/pull/<n>/head` after the
branch is deleted (PR 124's branch is gone and the ref still names its head, `4e87e5a`), but a
rebase or force-push would leave a round's commit unreachable, and the seed cases are built on
branches of their own. So every case gets a tag, `eval/<case>`, on its commit when it's added, and
the maintainer pushes the tags with the eval set's other changes; `scripts/eval` fetches a case by its tag
on any machine. A seed case's key is kept in the case list with its tag, and W6 imports the seed
with that key.

**Scoring a change.** A change is to `REVIEW.md`, the reviewer's prompt, its rules or its model.
`scripts/eval` runs the reviewer on every case three times. Each run gets the diff round 1 would
have had: from the PR's base to the case's commit, so the bug sits inside the PR's whole change.
For the seeds the base is `2b3d8e5`'s parent, and the diff is all of #114 with one fix undone; a
diff of the case branch against `2b3d8e5` alone would be the bug and nothing else. A case is caught when two of its three
runs flag a finding in the same file whose lines are within three lines of the case's, `silent` and with a
severity other than `nothing`. The score is the silent cases caught, minus the clean cases with a silent
false alarm. The score without the change is the last kept run's, saved with the set of cases it
covered; when a case has been added since, the eval runs the kept reviewer too, so both scores
cover the same cases.

**Keep or drop.** A change is kept only when its score is strictly higher than without it. An equal
score drops it, since the change adds text to every review and buys nothing.

**Cost and how often.** One `claude -p --output-format json "/code-review high"` on commit
`7920e16` (#102, five files, 222 lines added and 48 removed) took 89 s and used 7,950 output
tokens and about 395,000 cached input tokens, $0.49 at list price. It draws on the subscription,
not paid API tokens. With no case added since the last kept run, an eval runs only the change's
side: fourteen cases three times is 42 runs, about 62 minutes and about $21 at list price, each
run's cost scaling with its diff. An eval after a case is added runs both sides, twice that. How much of a
subscription's limit that is, is 🔍 unverified. The eval runs only when `REVIEW.md`, the prompt,
the rules or the model change, never on a schedule.

<a id="ci"></a>
### 3.9 A reviewer in CI

**gh-plan doesn't get a reviewer in CI.**

- CI can't reach Redmine on `127.0.0.1`, so it would post to PR threads and need an import to bring
  its findings home: a second writer and a second path for every finding.
- It would need a token in the repo's secrets, `CLAUDE_CODE_OAUTH_TOKEN` or an API key. Under G3
  that token would have to be listed in architecture §6.2 with what it reads and writes, and an API
  key costs paid tokens, against I1.
- Each round already reviews every commit Ship pushes and runs `scripts/assess` on it (§3.3). A CI
  run would review the same diff again.
- CI keeps the checks that need no model, `assess` with `checks/`, on every PR and push to `main`,
  which is what Dependabot's PRs rely on. A round runs the same assess locally and doesn't read
  CI's. The codify rule (§3.7) moves real findings there, so assess gains a check each time a rule
  earns one.

<a id="failure-modes"></a>
### 3.10 Failure modes

Recording a round's failure modes are rows in the register,
[`docs/failure-modes.md`](../failure-modes.md#ship): Ship's *Send the findings* and *Start the
review*, which a fix to `findings.py` or `review.py` is read against. Two cases sit outside a
command:

- **Redmine is down.** Nothing stands in for it (§3.3). `review.py` doesn't start a round it can't
  number, `send` stops with the files kept, and Done refuses until every round is in. The team
  brings Redmine back, and the next `pr` or `findings.py send <n>` carries on.
- **A rule is renamed in `REVIEW.md`.** Old rows keep the old id, so a key under the old rule and
  one under the new are two findings. `redmine-set rule OLD NEW` moves every row to the new id
  through the plugin's rename route, all or none, with a journal note on each issue it touches,
  and a second run writes nothing.

<a id="alternatives"></a>
## 4. Alternatives considered

- **GitHub code scanning with SARIF.** Uploaded findings become alerts, with fingerprints, closing
  when the code is fixed, and dismissal reasons
  ([Resolving code scanning alerts](https://docs.github.com/en/code-security/how-tos/manage-security-alerts/manage-code-scanning-alerts/resolving-code-scanning-alerts)).
  An alert has no thread to answer in, `intentional` has no clean dismissal reason, and it's free
  on public repos only. The counts would come from the API, not SQL.
- **SonarQube.** A real findings database, with states, comments and a Web API. It's one more server
  to patch and back up (G4), `pr` would need a stored token for it, and its SARIF import files
  every finding as a security issue and keeps only the severity, so visibility and the rule are
  lost.
- **DefectDojo.** Built to collect findings from many scanners, with deduplication, risk acceptance
  and a reimport that closes what disappeared. It's self-hosted only, one more server to run beside
  Redmine (G4).
- **Committed JSONL with a SQLite index.** One file per finding in the repo, indexed locally. It
  breaks invariant 1, since findings are tracker data, and invariant 2, since a finding's outcome
  would change on the default branch only through a PR. Two branches recording findings conflict
  in the same files.
- **A row per finding, keyed by a fingerprint of its code and updated in place.** A hash of the
  flagged lines, with an occurrence number for repeated snippets, follows code that moves. But an
  identical snippet added above renumbers the ones below, a finding on repeated code can't be
  matched as the same finding, and each update overwrites what the row said before. A key a person
  can read, `file:lines:rule`, fixed when the finding is raised, and a row per event keep the whole
  history, and round 2 on checks the finding by its key rather than finding it again.
- **Issues as findings, under a Finding tracker.** No plugin, but every finding takes an issue
  number, shows in Next up and the Lanes tab unless every query excludes it, and its fields are
  custom fields, so each query joins `custom_values` once per field.
- **Findings as PR review comments.** Each finding posted to the PR, answered in its thread. The
  PR would then hold a lasting record the store already has, a comment written at a round's line
  lands on the wrong code once a later fix moves it, and every thread needs an answer and a
  resolve. The PR body rendered from the store at convergence is the public record instead (#111).
- **PR-thread trailers only.** The reply's first line already holds the outcome. Threads miss every
  finding fixed before the push, as with #82's 38, and the counts need a pass over every PR.
- **Copilot code review.** Replies are "visible to other people but not to Copilot", and it "may
  repeat previous comments, even if you resolved or downvoted them"
  ([Using Copilot code review](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/copilot-code-review)), so
  neither a check of a fix nor an outcome can reach it. #104 stops requesting it.
- **A reviewer in CI.** §3.9.

<a id="cross-cutting"></a>
## 5. Cross-cutting concerns

| Lens | Answer |
|---|---|
| L1 Security | A finding's text is written by a model from code it read, so it's untrusted. It's shown on the Findings tab, in the round report, in the PR body's render, and to the next round's reviewer. The Findings tab renders it with `<%= simple_format(h(text)) %>`: `h` escapes it first, since `simple_format` alone sanitizes and keeps a safelist of tags such as `<a href>`; the plugin's views use no `raw` or `html_safe`. A round's `repo` is saved only if it matches `<owner>/<repo>` in GitHub's name characters and `pr_number` is a positive integer, and the views build the PR's link from them, so a link can't carry a `javascript:` URL. The round report shows it in a webview with scripts off (#110), and the PR body's render escapes it (#111). When a later round reads findings back (§3.5), they're passed as data in a fenced block, never as instructions. `findings.py` reads the Keychain's `redmine-gh-plan` key through `redmine.py`, never one in a file |
| L2 Privacy and data | Not ticked for #84, and lightly affected: a row holds a summary and a few lines of the repo's code, all public, and the id of the user who confirmed it. Rows are never changed, so the ledger is its own history; a rule rename, the one change, adds a journal note to each issue it touches. Fixtures are invented |
| L3 Reliability and failure | §3.3: a `POST` skips a row identical to one already there and refuses one that differs, so sending a round twice adds nothing, and a part-way send is finished by the next. Round files stay until Done has checked Redmine holds every round. §3.10 and the register for the rest |
| L4 Performance and limits | Not ticked for #84, and not affected, because a round is tens of rows: one `POST` and one `GET` per round file, and the Findings tab lists one issue's rows |
| L5 Observability | The issue page shows "Findings: 2 open to fix" through the `view_issues_show_details_bottom` hook, linking to the project's Findings tab at `/projects/plan/review_findings?issue_id=<n>`, which lists each open finding to fix first. `findings.py` prints the count after each round, the round report (#110) and the PR body's review line give it, so a PR isn't called reviewed while one is open. Done refuses while a round file's rows aren't in Redmine, naming the file |
| L6 Compatibility and migration | Migration 001 created a `review_findings` table keyed by fingerprint, which live Redmine has, with no rows. Migration 002, a `change` migration, drops it and creates the ledger's two tables and their unique indexes, so `rake redmine:plugins:migrate NAME=review_findings VERSION=0` drops both. The image's entrypoint runs `rake redmine:plugins:migrate` on start only when `REDMINE_PLUGINS_MIGRATE` is set, and `compose.yml` doesn't set it today, so W1 adds `REDMINE_PLUGINS_MIGRATE: "1"` to its `environment`; a Redmine upgrade then migrates the plugin with the rest. #82's findings are imported by `redmine/import_findings.rb`, through `rails runner`, from an uncommitted JSON file in `.tmp/`, with the fields §3.2 lists and the rest empty |
| L7 Usability and accessibility | Not ticked for #84, and lightly affected: the Findings tab and the issue page's count use core's list and link markup, so they keep Redmine's keyboard and screen-reader behaviour |
| L8 Testability | §6: the plugin's model and controller on Redmine's fixtures, and `findings.py` against recorded Redmine responses, without the network |
| L9 Operations and cost | §3.8: one review is about 89 s and $0.49 at list price on the subscription; an eval of fourteen cases is about 62 minutes and $21, run only when the reviewer changes. The plugin is mounted read-only like `lane_board` and adds nothing to run. W8 adds the findings to the nightly export |
| L10 Docs impact | Design 0004, since Ulysses rebuilds the issue page and W2's hook renders inside `div.attributes`, after the groups. Architecture §6.1 (the plugin and `findings.py`), §6.2 (the writer), §9 (the Findings tab) and §10 (invariant 6). `redmine/README.md` (the mount, the upgrade check, the backup). `redmine/compose.yml`. `CLAUDE.md`, for the review rule (a round on each pushed commit, replacing "Before each push"), the stop rule, and the reviewer's `send` in Boundaries (#110). `docs/failure-modes.md`, for the weighting as two fields and Ship's two review rows. `checks/README.md`, for a checker in Python printing `file:lines: message` (§3.7). `REVIEW.md` (#104). The gh-issues skill's `SKILL.md`, for the outbox and the reply's first line |
| L11 Invariants | 2 holds because only the plugin's code and `findings.py` are committed, and reach `main` by PR; the rows live in Redmine, not on the default branch. 6 holds because the extension's only new code here is Go and Ship checking `<k>-outcomes.md`, the Review tab linking it, and Done reading the issue's ledger through `redmine.js`. `findings.py` reads assess's result from the `<k>-findings.md` `review.py` wrote. The one new writer, `findings.py`, is named in §6.2 and in invariant 6's added clause: "Outside the extension, review findings are written only by `findings.py`, and once by `import_findings.rb` for the seeds". Its `send` runs from the detached review at the end of the reviewer's turn, writing the round and its `review` rows only, and from Ship's `pr`, which alone sends `outcomes` rows. `add-missed` only reads, and writes a file under `.tmp/<n>/` |
| L12 Offline and concurrency | Redmine is required: while it's down no round starts, a send stops with its files kept, and Done refuses (§3.10). A review that ran before Redmine went down waits as `<k>-findings.md` and is sent, not reviewed again, once it's back. Two sends of one round meet at the unique indexes, and a round whose number isn't `last_round` + 1 is refused |

<a id="testing"></a>
## 6. Testing

The fixtures, all invented:

- **Plugin tests**, run with `rake redmine:plugins:test NAME=review_findings` in the container:
  Redmine's own `projects`, `users`, `issues`, `roles` and `members` fixtures, plus
  `test/fixtures/review_findings.yml` with one finding for each outcome, one open silent finding,
  and one raised, answered `fixed` and checked in a later round. Tests:
  - the model: allowed values, the unique keys, `line_end` not before `line_start`, `repo`'s pattern;
  - the controller: a `POST` that adds rows, a second identical one that adds nothing, one that
    differs on an existing key refused with 422 and nothing saved, a round that isn't
    `last_round` + 1 refused, a 403 without `manage_review_findings`; the GET's `last_round`,
    each key's latest status and per-rule key counts; `rules.json`'s counts over `outcomes` rows
    only;
  - the views: a summary holding `<script>` and a body holding `<a href>` both render escaped,
    as text;
  - the migration: up, then down to `VERSION=0`, leaves no table.
- **`findings.py` tests**, in `test_scripts.py` with the skill's other scripts. They stub
  `Redmine.send` with recorded responses saved as JSON in `scripts/fixtures/findings/`: a `GET`
  of an issue's ledger, a `POST`'s answer, a 422, a refused connection. Tests:
  - writing a round file's lines from its JSON, and `send` refusing a file whose lines no longer
    match it;
  - `send` posting files in round order, findings before outcomes, and skipping what Redmine has;
  - `send` posting a `<k>-findings.md` Redmine lacks without starting a review, and `review.py`
    not reviewing a commit a round file names;
  - a round whose `ci` is `failure` recorded with its number and rows;
  - a refused connection keeping every file and the next `send` finishing the job;
  - parsing each outcome line form in §3.4, and refusing a line with an unknown outcome or a key
    no round raised;
  - round 2's prompt holding only `fixed` findings, with their keys, claims and the fix's context,
    a later round's new code getting the review and security passes over the diff since the last
    round, and a rule's third finding turning into a check.
- **`sql.rb`**, a test in `tests/` that opens a copy of a fixture database and checks that a
  `CREATE` raises.
- **Done**, in `vscode/test/done.test.js`: a `.tmp/<n>/<k>-outcomes.md` whose rows a stubbed
  ledger lacks makes Done refuse and name it.

<a id="requirements-coverage"></a>
## 7. Requirements coverage

| # | Requirement | How |
|---|---|---|
| G1, G5 | Tracking data is reviewable evidence; change history | The ledger only adds rows: each answer is a row with who confirmed it and when, and a changed answer is a later row beside the earlier one. A rule rename is a journal note on each issue it touches |
| G2 | Full export | W8 adds each issue's findings to the nightly export |
| G3 | No credential in a repo | `findings.py` uses the Keychain key; no CI reviewer, so no CI secret (§3.9) |
| G4 | Low operational burden | A table in the Redmine we already run |
| H1 | Scriptable from the command line | `findings.py`, `sql.rb` and `scripts/eval` |
| H6 | Validation hooks | The codify rule turns real findings into checkers `assess` runs in each round |
| I1, I3 | Free; nothing behind a paid tier | The reviewer and the eval run on the subscription; no paid API tokens |

<a id="work-breakdown"></a>
## 8. Work breakdown

Each item is a Redmine issue in project `plan`, related to #84.

| Item | Builds | After | Points |
|---|---|---|---|
| W1 | The plugin in `redmine/plugins/review_findings/`: model, migration, the API's routes and permissions, the rule rename's journal note (§3.1), the mount and `REDMINE_PLUGINS_MIGRATE` in `compose.yml`, its README, and `redmine/README.md`'s upgrade check | — | 5 |
| W2 | The Findings tab and the issue page's count (§5, L5) | W1 | 3 |
| W3 | `redmine/sql.rb`, its mount and the queries in §3.6, in `redmine/README.md`; the rename route (§3.1) and `redmine-set rule` (§3.10) | W1 | 1 |
| W4 | The ledger: migration 002's two tables and the `POST` and GET (§3.1); `findings.py send` and the round files (§3.3, §3.4); `review.py`'s round 1 and its fix checks in context, and a rule's third finding as a check (§3.5, §3.7) | W1 | 5 |
| W5 | Go and Ship check `<k>-outcomes.md` and the Review tab links it; Go commits a round's `<k>-check-<rule>.py` to `checks/`, merged into an existing checker (§3.7); Done refuses while a round file's rows aren't in Redmine (§3.3) | W4 | 2 |
| W6 | `redmine/import_findings.rb` and the 38 seeds, keyed from W7's cases and `2b3d8e5`, after a person confirms §3.2's draft | W1, W7 | 2 |
| W7 | `scripts/eval`, its cases rebuilt from the seeds, and the first scored run (§3.8) | #104 | 5 |
| W8 | The backup exports each issue's findings | W1 | 1 |

The issues already open change to fit:

| Issue | Change |
|---|---|
| #104 | `REVIEW.md` gives each finding a severity and a visibility as well as a tag and a rule, and the reviewer returns them |
| #106 | The outcome line opens with the finding's key, gains `visibility/severity`, and is a line in `<k>-outcomes.md`, never a PR reply (§3.4) |
| #107 | Round 1 is the full review and later rounds check fixes in context; each round reviews a pushed commit and is numbered whatever its assess; the step-back is round 3, in the report and the PR body, and rounds stop on the open-to-fix count (§3.5) |
| #108 | `findings` reads `rules.json`, not PR threads, and counts each checker's PRs from git (§3.7) |
| #110 | The round report shows the round's findings and outcome lines; at the end of the reviewer's turn Claude runs `findings.py send` and updates the PR body, the two Boundaries exceptions (§3.3) |
| #111 | The Done table is the plugin's second table, after W1, and convergence adds its Done rows to the open-to-fix count (§3.5) |

#104 changes `CLAUDE.md`'s review line, `docs/failure-modes.md`'s weighting and review line, and
architecture §6.1, §6.2 and §7.2, for the reviewer and its round files. Architecture §9 and §10
change with W1 and W4, when the plugin and the writer exist.
