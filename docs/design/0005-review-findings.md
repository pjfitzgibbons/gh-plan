# Design 0005: Review findings in Redmine, weighted by what they lose

## Contents

- [1. Context and scope](#context)
- [2. Goals and non-goals](#goals)
- [3. Design](#design)
  - [3.1 The plugin's schema](#schema)
  - [3.2 The weighting](#weighting)
  - [3.3 Recording a round](#writer)
  - [3.4 Confirming what Claude drafted](#confirming)
  - [3.5 Rounds: the stop rule and the no-repeat rule](#rounds)
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
finding as a row. Each finding belongs to an issue, is weighted by what it loses and whether it
fails silently, and is answered with an outcome. The confirmed silent failures become an eval set,
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
- The PR carries nothing of the review that has to last: no finding is a PR comment, and its body
  is rendered from the store.
- Only a silent finding that loses something, or a crash, has to be fixed, and the store shows
  each one that's still open.
- Nothing Claude drafts is recorded as an outcome, a rule, a severity or a visibility until a person
  has accepted it.
- Rounds end on a rule, and a later round never repeats a finding.
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

The plugin adds one table, `review_findings`, and a project module of the same name. One row is
one finding in one round. #111 adds a second table to the same plugin, for the issue's Done lines.

| Field | Type | Allowed values | What it holds |
|---|---|---|---|
| `id` | integer | | Redmine's row id |
| `issue_id` | integer, not null | An issue's id | The issue whose branch was reviewed |
| `commit` | string(40) | A full SHA, or empty | The pushed commit the round reviewed (§3.3). Empty for seed rows |
| `round` | integer, not null | 1 or more, or 0 for a missed finding (§3.8) | The review round on this issue's branch |
| `fingerprint` | string(64), not null | `finding/v1:` or `missed/v1:` and 16 hex, or `seed/v1:` and a label | What makes the finding the same finding across rounds (below) |
| `file` | string | A repo-relative path, or empty | Where the finding points |
| `line` | integer | 1 or more, or empty | The line in `file` at `commit` |
| `rule_id` | string | A kebab-case rule `REVIEW.md` names, or empty | The finding's class of bug |
| `tag` | string, not null | `Important`, `Nit`, `Pre-existing`, `Security` | The reviewer's tag (#104) |
| `severity` | string, not null | `work`, `files`, `commits`, `tracker`, `nothing` | What the failure loses (§3.2) |
| `visibility` | string | `silent`, `seen`, or empty | Whether the user is shown that it failed. Empty only when `severity` is `nothing` and the finding describes no failure, such as a doc or code-quality finding |
| `summary` | string(255), not null | | The finding's first line |
| `body` | text | | The finding's failure scenario |
| `outcome` | string | `fixed`, `intentional`, `wrong`, `doesnt-matter`, or empty | What was done about it. Empty means open |
| `justification` | text | | Why, for any outcome but `fixed` |
| `verified_round` | integer | A later round, or empty | For `fixed`: the round that checked the fix and found it gone |
| `confirmed_by_id` | integer | A user's id, or empty | Who accepted the rule, outcome, severity and visibility (§3.4) |
| `confirmed_on` | datetime | | When |
| `pr_url` | string | `https://github.com/<owner>/<repo>/pull/<n>`, or empty | The PR, once one exists |
| `created_on`, `updated_on` | datetime | | Rails timestamps |

Empty is always SQL `NULL`: the model turns an empty string into `NULL` before it validates, so the queries' `IS NULL` finds every open row.

Key:

- **Unique key.** `(issue_id, fingerprint, round)`, with a unique index. A finding seen again in a
  later round is a new row, so the store keeps every round it was seen in.
- **Fingerprint.** `finding/v1:` and the first 16 hex digits of SHA-256 over the file's path, a
  newline, and the flagged line with one line either side, each stripped of leading and trailing
  space and joined by newlines, then a newline and the snippet's occurrence number among identical
  snippets in that file (1 for the first), so two findings on the same boilerplate 200 lines apart
  differ. An identical snippet added above renumbers the ones below, so a fingerprint whose
  snippet appears more than once in the file is never matched as a repeat (§3.5). A finding with no file, such as one on the design, hashes `-`, a
  newline and its summary, normalized the same way, so two file-less findings differ. The line
  number and the rule are left out, so code that moves keeps its fingerprint, and so does a finding
  whose rule is renamed. The `v1` lets a later version change the recipe without colliding with old
  rows. A `missed/v1:` fingerprint uses the same recipe on the bug's code.
- **Two findings at one place in one round.** They share a fingerprint, so they're one row. The row takes the
  weighting, tag and rule of the one that weighs more (silent before seen, any loss before
  `nothing`), and the other's tag, rule, weighting and summary are appended to the body. The open
  silent count and the eval set see the heavier one; the lighter one's rule doesn't count toward
  §3.7.
- **Outcomes.** `fixed`: the code changed and the next round checks it. `intentional`: the
  behaviour is wanted, usually an Open row in `docs/failure-modes.md`. `wrong`: the finding
  misreads the code. `doesnt-matter`: it's real, and not worth the change. `fixed` and
  `intentional` count as real findings; `wrong` and `doesnt-matter` count as noise (§3.7).

A worked example, R6.3 from #82 as a live round would record it. The commit, fingerprint, file,
line and rule are illustrative; the seed row for R6.3 (§3.2) has no commit, file, line or rule.

```json
{
  "issue_id": 82,
  "commit": "<the pushed commit round 6 reviewed>",
  "round": 6,
  "fingerprint": "finding/v1:3f9c0a7e5b21d4c8",
  "file": "vscode/done.js",
  "line": 214,
  "rule_id": "ignored-file-overwrite",
  "tag": "Important",
  "severity": "files",
  "visibility": "silent",
  "summary": "The fast-forward overwrites an ignored file in the main checkout without a word",
  "body": "An ignored file in the main checkout that the merged branch adds is replaced by the branch's copy. git reports nothing, and Done says the checkout is up to date.",
  "outcome": "fixed",
  "justification": null,
  "verified_round": null,
  "confirmed_by_id": 1,
  "pr_url": "https://github.com/pjfitzgibbons/gh-plan/pull/114"
}
```

Round 6 records it with every field from `issue_id` to `body` and no `outcome`. Its line in
`outcomes.md`, `finding/v1:3f9c0a7e5b21d4c8 · Fixed · ignored-file-overwrite · silent/files`,
records the outcome and the confirmation when Ship sends it (§3.4). If round 7 finds the overwrite gone, it sets `verified_round` to 7.

**The plugin's API.** Each action has `accept_api_auth`, so `pr` reaches it with the Keychain key.

| Route | Does | Permission |
|---|---|---|
| `GET /issues/:issue_id/review_findings.json` | The issue's findings, every round | `view_review_findings` |
| `PUT /issues/:issue_id/review_findings.json` | Upserts a list of rows by `(fingerprint, round)`, in one transaction. A field the request leaves out is left as it is. A row a person has confirmed changes only from an outcome line (`"confirm": true`); a round line leaves every field of it as it is. A change to a confirmed row adds a journal note to the issue, with the user and the old and new values | `manage_review_findings` |
| `GET /projects/:project_id/review_findings/rules.json` | Per rule: real and noise counts over confirmed findings, each fingerprint counted once by its latest round (§3.7) | `view_review_findings` |
| `GET /projects/:project_id/review_findings/rules/:rule_id.json` | How many findings have the rule, and how many of them are confirmed, over the same issues as `rules.json` | `view_review_findings` |
| `PUT /projects/:project_id/review_findings/rules/:rule_id.json` | Renames the rule to the body's `new_rule_id` on every finding in the project and its sub-projects, in one transaction, with a journal note for each confirmed one (§3.10). A finding on an issue the user can't see or can't manage findings in refuses the whole rename | `manage_review_findings` |
| `GET /projects/:project_id/review_findings` | The Findings tab (§5, L5) | `view_review_findings` |

A `PUT` that names a value outside the allowed values above is refused with 422, and nothing in
it is saved.

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

**The seed rows.** W6 imports the 38 with what the transcript holds: `issue_id` 82, `round` 3 to 6
from the label, `fingerprint` `seed/v1:82-<label>`, `tag`, `severity`, `visibility`, `summary`,
`outcome` and `pr_url`. `commit`, `file` and `line` stay empty, since rounds three to six were
never pushed. No rule existed then, so W6's import file gives each row a `rule_id` Claude drafts,
which a person confirms with the rest; a row whose rule they reject goes in with `rule_id` empty and
doesn't count toward §3.7. The eval case's place for a seed (§3.8) is kept with the case, not
written back to the row. `outcome` is `fixed` for all but six: R3.1, R3.6, R3.10,
R4.7 and R6.6 are `intentional`, since they're Open rows, and R4.9 is `doesnt-matter`, since it
was skipped. R4.8 was retired in round five, so it's `fixed`. `created_on` is the date of the
session, 2026-10-19, so the queries in §3.6 place the rows in their week. The import takes only
the rows a person has confirmed in the import file, with `confirmed_by_id` set; one they haven't is left
out, since #82's worktree is gone and no outcome line could confirm it later.
`import_findings.rb` runs once, by the maintainer, through `rails runner`, and invariant 6's added clause
names it beside `findings.py`.

<a id="writer"></a>
### 3.3 Recording a round

A round is two turns, the reviewer's and the author's, and each ends in a fixed state. The
reviewer's turn starts from a commit Ship pushed and ends with the round's findings in Redmine and
the PR body updated. The author's turn answers them and ends when Ship pushes the next commit. No
finding is posted to the PR.

Claude's one Redmine write is the reviewer's: `findings.py send` writes the round's findings as
unconfirmed rows. An outcome, rule, severity or visibility is recorded only from `outcomes.md`,
which Ship's `pr` sends after a person reviews it at Go. Both pass through an outbox in the issue's
`.tmp/<n>/` folder, which holds a file until Redmine has it:

```
.tmp/<n>/findings/
  round-<k>.jsonl     one finding per line, as the review produced it, with no outcome field;
                      then one verdict line per earlier fixed finding the round found gone
  outcomes.md         one line per finding: its fingerprint, then its outcome line (§3.4)
```

Every round reviews a commit Ship has pushed, so the round's `commit` is on GitHub, and CI's
required `pytest` check throttles the rounds. There's no review before a push: round 1 reviews the
commit the first Ship pushed when it opened the PR. The steps:

1. **The reviewer's turn.** Ship has pushed commit `C`, and the round starts only once `C`'s
   `pytest` check has passed: `findings.py add-round <n>` reads the check with `gh pr checks` and
   refuses a commit whose check is pending or failed. The review, `/code-review high` in the
   session or `claude -p` from `pr` once #104 lands, reads what §3.5 lists at `C` and returns its
   findings. `add-round` gives each one a fingerprint at `C`, applies the no-repeat rule (§3.5),
   and writes `round-<k>.jsonl` with `commit` set to `C`. For each earlier `fixed` finding the
   reviewer found gone, it adds a line with only that row's `fingerprint`, `round` and
   `verified_round`. Then `findings.py send <n>` records the round, and Claude updates the PR body
   with `gh pb`, its Done list rendered as it stands (#111). The `send` and the `gh pb` are the two
   writes outside a button that #110 adds to `CLAUDE.md`'s Boundaries, and both wait for it.
2. **The author's turn.** Claude fixes the findings to fix (§3.2), drafts an outcome line
   for every finding in `outcomes.md`, and writes the round report (#110): each finding with
   its fix or the note refuting it, the open-to-fix count, `commit.txt` and `pr-body.md`.
3. **A person reviews the report and presses Go.** The Review tab links
   `.tmp/<n>/findings/outcomes.md`, as it links `commit.txt` and `pr-body.md`, and shows the staged
   diff after Go. Ship refuses an `outcomes.md`, `commit.txt` or `pr-body.md` changed since Go.
4. **Ship runs `pr`.** `pr` commits and pushes `C+1`, the next round's commit, updates the PR, then
   calls `findings.py send <n>` for `outcomes.md`, and for any round file a reviewer's `send`
   left behind.

`findings.py send <n>` sends whatever the outbox holds:

- each `round-<k>.jsonl` with `PUT /issues/<n>/review_findings.json`. It reads the issue's
  findings back with `GET`, and deletes the file only when every fingerprint and round in it is
  there, and every verdict line's `verified_round` is set;
- `outcomes.md`, only when `pr` calls it. It parses each line, finding the row by the fingerprint
  it opens with: the fingerprint's one open row, since the no-repeat rule never records a second
  open row for it (§3.5). A line that changes an answered row names its round,
  `finding/v1:… @3 · …`. A line whose row it can't find, or that matches more than one, stops `pr`
  before anything is recorded. It records each outcome with `confirmed_by_id` set to the key's
  user, reads it back, and deletes `outcomes.md` once every line in it reads back.

`findings.py` lives with `pr` in `.claude/skills/gh-issues/scripts/`, and uses the skill's
`redmine.py`: the `Redmine` client, the Keychain's `redmine-gh-plan` key and its `Refused` error.
It's the one writer this design adds.

**Recording twice leaves the same rows.** `PUT` upserts by `(issue_id, fingerprint, round)`. A
second send of `round-<k>.jsonl` finds each row already there, with the same values, and Rails
saves nothing for a record with no change, so `updated_on` stays as it was. A round's lines carry
no `outcome`, `justification` or confirmation, and `findings.py` writes them with those keys
absent, never `null`; the plugin treats a `null` in them as a 422. Once a row is confirmed, a round
line can't change any of its fields, except that a verdict line sets `verified_round`, which isn't
one of the fields a person confirms. So a second send after the outcomes leaves the rule, severity
and visibility the person set, as well as the outcome. A second send of an outcome finds the row already holding that outcome, rule, weighting and
justification and skips it, so `confirmed_on` stays as it was and no journal note is added. A `pr` that
stops part way leaves its files in the outbox, and the next `pr` sends what's left. Every case is
the same set of rows as one send.

**Done refuses an outbox that isn't empty.** Done removes the worktree, and `.tmp/<n>/` with it.
While `.tmp/<n>/findings/` has a file in it, Done says which and stops, so a round that Redmine
never got isn't deleted with the worktree.

<a id="confirming"></a>
### 3.4 Confirming what Claude drafted

The reviewer drafts a rule, a severity and a visibility for each finding, and Claude drafts its
outcome. None of them is confirmed until a person accepts the line that carries them: the finding's
line in `outcomes.md`, its fingerprint and then #106's outcome line, with a third part:

```
finding/v1:3f9c0a7e5b21d4c8 · Fixed · ignored-file-overwrite · silent/files
finding/v1:8b02d1f4c6e9a357 · Intentional · repo-rename · seen/nothing — a repo renamed on GitHub is refused with a message; Open row "Repo renamed"
```

- The first part is the outcome: `Fixed`, `Intentional`, `Wrong` or `Doesnt-matter`.
- The second is the rule's id, as `REVIEW.md` names it. A new rule is added to `REVIEW.md` in the
  same round, so the person sees it in the diff.
- The third is `visibility/severity`, with `-` for an empty visibility.
- Any outcome but `Fixed` carries its justification after a dash, or on the lines below.

A person accepts the lot by pressing Go, after changing any line they disagree with; Ship then sends
exactly what they saw. A line they delete isn't recorded: the finding stays open, and a silent one
keeps the next round coming, up to round 3 (§3.5). `confirmed_by_id` is set only from these lines, so a row with
it empty was never accepted. Only accepted rows count in §3.6's counts and in §3.7; §3.6's list of
open findings to fix is the unaccepted ones waiting for an answer.

`Fixed` isn't taken on trust: the next round checks it (§3.5). Its rule, severity and visibility
are still a person's to accept, since those decide what counts.

<a id="rounds"></a>
### 3.5 Rounds: the stop rule and the no-repeat rule

**What the reviewer reads before each round.**

- Round 1: the branch's whole diff against `main`, the issue's description, `REVIEW.md`, and the
  docs `CLAUDE.md` says to read before the kind of change the diff makes.
- Round 2 on: only the diff from the last round's `commit` to this round's, with `REVIEW.md` and
  the issue again. It also gets the issue's findings as data, in a fenced block it's told not to
  follow: the fingerprint, file, line, summary and outcome of each. `findings.py` reads them from
  the store and the outbox together, since a round sent while Redmine was down is still there,
  and takes a drafted outcome from its `outcomes.md` line. The reviewer answers each `fixed` one that
  hasn't been verified, gone or still there.

**No repeats.** When a new finding's fingerprint matches one the issue already has, in the store
or the outbox, `add-round` looks at the earlier one:

| Earlier finding | New finding | `add-round` |
|---|---|---|
| Open | Any | Drops it: the earlier one is still waiting for an answer, and still counts toward the stop rule |
| `fixed` | Any, or the reviewer's "still there" | Records it in the new round, open: the fix didn't hold |
| `wrong`, `doesnt-matter` or `intentional` | Weighs no more (§3.1) | Drops it, so an answered finding doesn't come back |
| `wrong`, `doesnt-matter` or `intentional` | Weighs more: silent and losing something where the earlier wasn't | Records it in the new round, open, its body naming the earlier answer |

When a fingerprint has rows in several rounds, the latest one is "the earlier finding" here and
in the stop rule: a `fixed` row followed by a "still there" row is settled by the newer row, and
the older one's missing `verified_round` no longer counts.

A fingerprint whose snippet appears more than once in the file at the round's commit is never
matched: `add-round` records the new finding, and its body says which earlier one it may repeat. A
finding whose code changed has a new fingerprint, so it's new. A `fixed` finding the reviewer
says is gone gets `verified_round` set to the new round.

**The stop rule.** A finding is *open to fix* when it's a finding to fix (§3.2) and has no
outcome, recorded or drafted. That's a `silent` finding whose `severity` isn't `nothing`, or one
with the rule `crash`. `findings.py` counts it over the store and the outbox, so the count includes
a round or outcome that hasn't been sent yet. After each round it prints the count, and the PR body's review line gives it: "Claude review: round 2, 0 open to fix".

- Another round runs while the open-to-fix count is above zero, while a fingerprint's latest row
  is a `fixed` finding to fix with no `verified_round`, or while the PR's head is a commit no
  round has reviewed: an open one needs an answer, and a `fixed` one needs
  checking. The count is over every round, so an open finding the no-repeat rule dropped from a
  later round still keeps the rounds going. The last check means a Ship after the final round,
carrying its answers, gets a round of its own. When all three are clear, the round just run is the
last.
- Round 3 reviews the diff since round 2 and checks round 2's fixes like any round. Its report,
  and the PR body it renders, also ask the author to step back: the rules with findings in rounds 1 to 3, which
  findings came from an earlier round's fix, and whether the approach is wrong. A finding to fix
  still open after round 3 stays in the count `pr` prints and the PR body shows. #82 is the case for it: about 14 of its 38 findings came from an
  earlier round's fix, and rounds five and six needed forks, a tag named `main` and ssh aliases to
  reach.
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

Counts by rule and outcome, over accepted findings. A finding seen in three rounds is three rows,
so each fingerprint counts once, by its latest round:

```sql
WITH latest AS (
  SELECT f.* FROM review_findings f
  JOIN issues i ON i.id = f.issue_id
  WHERE f.confirmed_by_id IS NOT NULL
    AND f.round = (SELECT MAX(g.round) FROM review_findings g
                   WHERE g.issue_id = f.issue_id AND g.fingerprint = f.fingerprint
                     AND g.confirmed_by_id IS NOT NULL)
)
SELECT rule_id, outcome, COUNT(*) AS n
FROM latest
WHERE rule_id IS NOT NULL
GROUP BY rule_id, outcome
ORDER BY rule_id, outcome;
```

The latest round is the latest accepted one, so a finding re-opened in a round nobody has answered
yet still counts by its last accepted answer. The join on `issues` leaves out a finding whose issue
was deleted, as the plugin's routes do.

A rule's findings over time, by week, over the same `latest` rows. Each finding counts in the week it was first found, not the week of its latest round:

```sql
WITH latest AS (/* as above */),
found AS (
  SELECT issue_id, fingerprint, MIN(created_on) AS found_on FROM review_findings
  GROUP BY issue_id, fingerprint
)
SELECT strftime('%Y-%W', found.found_on) AS week, latest.outcome, COUNT(*) AS n
FROM latest JOIN found USING (issue_id, fingerprint)
WHERE latest.rule_id = 'ignored-file-overwrite'
GROUP BY week, outcome
ORDER BY week, outcome;
```

The open findings to fix, across every issue. Each finding is listed once, by its latest round, and only while that round has no answer, so one answered in a later round drops off:

```sql
SELECT i.id AS issue, i.subject, f.round, f.file, f.line, f.severity, f.summary
FROM review_findings f
JOIN issues i ON i.id = f.issue_id
WHERE f.round = (SELECT MAX(g.round) FROM review_findings g
                 WHERE g.issue_id = f.issue_id AND g.fingerprint = f.fingerprint)
  AND ((f.visibility = 'silent' AND f.severity <> 'nothing') OR f.rule_id = 'crash')
  AND f.outcome IS NULL
ORDER BY i.id, f.round, f.id;
```

<a id="codify"></a>
### 3.7 The codify rule

Over accepted findings with a rule, each fingerprint counted once by its latest round, as in the
first query in §3.6:

- **Three or more real findings** (`fixed` or `intentional`) for a rule mean it gets a checker in
  `checks/`, carrying a `# rule: <id>` line, which `assess` runs before the agent review (#105).
- **Three or more noise findings** (`wrong` or `doesnt-matter`) mean it gets a line in `REVIEW.md`
  saying what not to flag.

A rule can reach both, and gets both.

**How the store replaces the `findings` script's reading of PR threads.** #108's `findings` script
counts outcomes by parsing reply first lines from every PR's threads. With the store, it calls
`GET /projects/plan/review_findings/rules.json` instead, which counts confirmed rows in SQL and
returns `real` and `noise` for each rule. The script then greps `checks/` for each rule's
`# rule:` line and names the rules that are due a checker or a `REVIEW.md` line. #82's findings,
which never reached a thread, count too once their rules are confirmed. While Redmine is down, the script says so and prints nothing; it never
falls back to a partial count from threads.

<a id="eval"></a>
### 3.8 The eval set

**What becomes a case.**

- **A confirmed silent finding.** Accepted, `silent`, `severity` not `nothing`, and `fixed`. The
  case is the code before the fix: the pushed commit the finding was recorded on, or for the seeds, the
  merged commit with that fix reverted (`2b3d8e5` for #82). The expected answer is a silent finding
  that loses something, at the same place. A finding seen in several rounds is one case.
- **A missed finding.** A silent failure that turned up after the merge, in use or in a later
  review. The case is the PR's last reviewed commit, and the expected answer is the bug that turned
  up. Each is added as a row with `tag` `Important`, `round` 0, its `file` and `line`, and a
  fingerprint `missed/v1:…`, so the store holds it with the rest. `findings.py add-missed <n>
  <file> <line>` writes it to the outbox of the issue whose work found it, against the issue it
  escaped, and it's answered by an outcome line like any finding, `Fixed` once its fix ships. Until
  then it's in the open-to-fix query, as it should be: the bug is still in the code.
- **A clean change.** A merged PR whose code nothing has since found a silent failure in. The
  expected answer is no silent finding; one counts as a false alarm.

The seeds give eight confirmed silent cases, every row in §3.2's first bucket but R3.1, which is
still in the code. #114 was squash-merged as `2b3d8e5` and rounds three to six were never pushed,
so no commit holds any one fix on its own: W7 rebuilds each case by hand, on a branch off
`2b3d8e5`, undoing that fix's lines, and records the bug's `file` and `line` with the case.
With six clean PRs, the set starts at fourteen cases. GitHub keeps `refs/pull/<n>/head` after the
branch is deleted (PR 124's branch is gone and the ref still names its head, `4e87e5a`), but a
rebase or force-push would leave a round's commit unreachable, and the seed cases are built on
branches of their own. So every case gets a tag, `eval/<case>`, on its commit when it's added, and
the maintainer pushes the tags with the eval set's other changes; `scripts/eval` fetches a case by its tag
on any machine. A seed case's `file` and `line` are kept in the case list with its tag.

**Scoring a change.** A change is to `REVIEW.md`, the reviewer's prompt, its rules or its model.
`scripts/eval` runs the reviewer on every case three times. Each run gets the diff round 1 would
have had: from the PR's base to the case's commit, so the bug sits inside the PR's whole change.
For the seeds the base is `2b3d8e5`'s parent, and the diff is all of #114 with one fix undone; a
diff of the case branch against `2b3d8e5` alone would be the bug and nothing else. A case is caught when two of its three
runs flag a finding in the same file within three lines of the case's `line`, `silent` and with a
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
- Each round already reviews every commit Ship pushes, once its `pytest` check passes (§3.3). A CI
  run would review the same diff again.
- CI keeps the checks that need no model: `pytest` now, and `assess` with `checks/` once #105
  lands. The codify rule (§3.7) moves real findings there, so CI gains a check each time a rule
  earns one.

<a id="failure-modes"></a>
### 3.10 Failure modes

| Failure | What happens |
|---|---|
| Redmine is down when a round runs | The review runs. `add-round` drops repeats by the outbox only, says the store wasn't read, and marks the round file. The reviewer's `send` fails and leaves the file in the outbox, and the next `send` applies the no-repeat rule against the store before it records a marked round |
| The pushed commit's `pytest` check is pending or failed | `add-round` refuses and says which. A failed check is on the PR for anyone to see, and the next round waits for a commit that passes |
| Redmine is down when Ship runs | `pr` pushes and says the findings weren't sent. The outbox stays, and the next `pr` sends it |
| Redmine is down when Done runs | Done refuses while the outbox has a file (§3.3), so nothing is lost with the worktree |
| `pr` stops part way through a send | Each file stays until it reads back; the next `pr` sends it again, idempotently (§3.3) |
| A `PUT` names a value outside the schema | 422 and nothing saved; `pr` stops and names the line |
| A person deletes a line from `outcomes.md` | The finding stays open; a silent one brings another round, up to round 3 |
| An outcome line names a fingerprint the issue doesn't have | `pr` stops before recording anything and names the line |
| Two windows send the same round | One transaction takes SQLite's write lock and wins. The other waits out the busy timeout and fails with `SQLite3::BusyException`, or, if it got past the lock, `RecordNotUnique` at the unique index. The controller rolls back and runs it once more on either, and the retry finds the rows and changes nothing. A retry that fails again answers 500, the outbox keeps the file, and the next `pr` sends it |
| A rule is renamed in `REVIEW.md` | Old rows keep the old id. Fingerprints don't include the rule, so no finding repeats. `redmine-set rule OLD NEW` moves them to the new id through the plugin's rename route, all or none, with a journal note on each confirmed row, and a second run writes nothing |

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
  neither the no-repeat rule nor an outcome can reach it. #104 stops requesting it.
- **A reviewer in CI.** §3.9.

<a id="cross-cutting"></a>
## 5. Cross-cutting concerns

| Lens | Answer |
|---|---|
| L1 Security | A finding's text is written by a model from code it read, so it's untrusted. It's shown on the Findings tab, in the round report, in the PR body's render, and to the next round's reviewer. The Findings tab renders it with `<%= simple_format(h(text)) %>`: `h` escapes it first, since `simple_format` alone sanitizes and keeps a safelist of tags such as `<a href>`; the plugin's views use no `raw` or `html_safe`. `pr_url` is saved only if it matches `https://github.com/<owner>/<repo>/pull/<n>`, so a link can't carry a `javascript:` URL. The round report shows it in a webview with scripts off (#110), and the PR body's render escapes it (#111). When a later round reads findings back (§3.5), they're passed as data in a fenced block, never as instructions. `findings.py` reads the Keychain's `redmine-gh-plan` key through `redmine.py`, never one in a file |
| L2 Privacy and data | Not ticked for #84, and lightly affected: a row holds a summary and a few lines of the repo's code, all public, and the id of the user who confirmed it. A change to a confirmed row adds a journal note to its issue, with who changed it and the old and new values, so the history stays in Redmine's journal. Fixtures are invented |
| L3 Reliability and failure | §3.3: the upsert by `(issue_id, fingerprint, round)` and the read-back before each delete mean recording a round twice leaves the same rows. §3.10 for the rest |
| L4 Performance and limits | Not ticked for #84, and not affected, because a round is tens of rows: one `PUT` and one `GET` per round, and the Findings tab lists one issue's rows |
| L5 Observability | The issue page shows "Findings: 2 open to fix" through the `view_issues_show_details_bottom` hook, linking to the project's Findings tab at `/projects/plan/review_findings?issue_id=<n>`, which lists each open finding to fix first. `findings.py` prints the count after each round, the round report (#110) and the PR body's review line give it, so a PR isn't called reviewed while one is open. Done refuses while the outbox holds an unsent round |
| L6 Compatibility and migration | One `change` migration creates the table and its unique index, so `rake redmine:plugins:migrate NAME=review_findings VERSION=0` drops both. The image's entrypoint runs `rake redmine:plugins:migrate` on start only when `REDMINE_PLUGINS_MIGRATE` is set, and `compose.yml` doesn't set it today, so W1 adds `REDMINE_PLUGINS_MIGRATE: "1"` to its `environment`; a Redmine upgrade then migrates the plugin with the rest. The fingerprint is versioned (`finding/v1`, `seed/v1`, `missed/v1`). #82's findings are imported by `redmine/import_findings.rb`, through `rails runner`, from an uncommitted JSON file in `.tmp/`, with the fields §3.2 lists and the rest empty |
| L7 Usability and accessibility | Not ticked for #84, and lightly affected: the Findings tab and the issue page's count use core's list and link markup, so they keep Redmine's keyboard and screen-reader behaviour |
| L8 Testability | §6: the plugin's model and controller on Redmine's fixtures, and `findings.py` against recorded Redmine responses, without the network |
| L9 Operations and cost | §3.8: one review is about 89 s and $0.49 at list price on the subscription; an eval of fourteen cases is about 62 minutes and $21, run only when the reviewer changes. The plugin is mounted read-only like `lane_board` and adds nothing to run. W8 adds the findings to the nightly export |
| L10 Docs impact | Design 0004, since Ulysses rebuilds the issue page and W2's hook renders inside `div.attributes`, after the groups. Architecture §6.1 (the plugin and `findings.py`), §6.2 (the writer), §9 (the Findings tab) and §10 (invariant 6). `redmine/README.md` (the mount, the upgrade check, the backup). `redmine/compose.yml`. `CLAUDE.md`, for the review rule (a round on each pushed commit, replacing "Before each push"), the stop rule, and the reviewer's `send` in Boundaries (#110). `docs/failure-modes.md`, for the weighting as two fields. `REVIEW.md` (#104). The gh-issues skill's `SKILL.md`, for the outbox and the reply's first line |
| L11 Invariants | 2 holds because only the plugin's code and `findings.py` are committed, and reach `main` by PR; the rows live in Redmine, not on the default branch. 6 holds because the extension's only new code here is Go and Ship checking `outcomes.md`, the Review tab linking it, and Done checking the outbox, all file reads. `add-round` reads the commit's check through `gh`, a read like those `pr` already makes. The one new writer, `findings.py`, is named in §6.2 and in invariant 6's added clause: "Outside the extension, review findings are written only by `findings.py`, and once by `import_findings.rb` for the seeds". Its `send` runs in the session at the end of the reviewer's turn, writing unconfirmed finding rows only, and from Ship's `pr`, which alone sends outcomes. `add-round` and `add-missed` only read, and write the outbox |
| L12 Offline and concurrency | A round when Redmine is down keeps its findings in `.tmp/<n>/findings/`, and the next `pr` records them once Redmine is back; Done won't remove the worktree while they're there (§3.10). Two sends of one round meet at the unique index |

<a id="testing"></a>
## 6. Testing

The fixtures, all invented:

- **Plugin tests**, run with `rake redmine:plugins:test NAME=review_findings` in the container:
  Redmine's own `projects`, `users`, `issues`, `roles` and `members` fixtures, plus
  `test/fixtures/review_findings.yml` with one row of each outcome, one open silent row, one
  verified `fixed` row and one seed row. Tests:
  - the model: allowed values, the unique key, `pr_url`'s pattern;
  - the controller: an upsert that inserts, one that changes nothing on a second send, one that
    leaves `outcome` alone when the request omits it, a 422 that saves nothing, a 403 without
    `manage_review_findings`, `rules.json`'s counts over confirmed rows only;
  - the views: a summary holding `<script>` and a body holding `<a href>` both render escaped,
    as text;
  - the migration: up, then down to `VERSION=0`, leaves no table.
- **`findings.py` tests**, in `test_scripts.py` with the skill's other scripts. They stub
  `Redmine.send` with recorded responses saved as JSON in `scripts/fixtures/findings/`: a `GET`
  of an issue's findings, a `PUT`'s answer, a 422, a refused connection. Tests:
  - the fingerprint of a known snippet, and that moving it keeps the fingerprint;
  - `add-round` dropping a repeat from the store and, with Redmine down, from the outbox, and
    `send` dropping it against the store later;
  - `add-round` refusing a commit whose `pytest` check is pending or failed, against a recorded
    `gh pr checks` answer;
  - `send` deleting a file only after the read-back, and keeping it on a refused connection;
  - sending a round twice making the same requests and leaving the outbox empty;
  - parsing each `outcomes.md` line form in §3.4, and refusing one that names an unknown outcome.
- **`sql.rb`**, a test in `tests/` that opens a copy of a fixture database and checks that a
  `CREATE` raises.
- **Done**, in `vscode/test/done.test.js`: a temporary `.tmp/<n>/findings/` with a file makes Done
  refuse and name it.

<a id="requirements-coverage"></a>
## 7. Requirements coverage

| # | Requirement | How |
|---|---|---|
| G1, G5 | Tracking data is reviewable evidence; change history | Each row keeps who confirmed it and when, and each change to a confirmed row is a journal note on its issue with the user and the old and new values |
| G2 | Full export | W8 adds each issue's findings to the nightly export |
| G3 | No credential in a repo | `findings.py` uses the Keychain key; no CI reviewer, so no CI secret (§3.9) |
| G4 | Low operational burden | A table in the Redmine we already run |
| H1 | Scriptable from the command line | `findings.py`, `sql.rb` and `scripts/eval` |
| H6 | Validation hooks | The codify rule turns real findings into checkers `assess` runs in CI |
| I1, I3 | Free; nothing behind a paid tier | The reviewer and the eval run on the subscription; no paid API tokens |

<a id="work-breakdown"></a>
## 8. Work breakdown

Each item is a Redmine issue in project `plan`, related to #84.

| Item | Builds | After | Points |
|---|---|---|---|
| W1 | The plugin in `redmine/plugins/review_findings/`: model, migration, the API's routes and permissions, the journal note on a confirmed row's change (§3.1), the mount and `REDMINE_PLUGINS_MIGRATE` in `compose.yml`, its README, and `redmine/README.md`'s upgrade check | — | 5 |
| W2 | The Findings tab and the issue page's count (§5, L5) | W1 | 3 |
| W3 | `redmine/sql.rb`, its mount and the queries in §3.6, in `redmine/README.md`; the rename route (§3.1) and `redmine-set rule` (§3.10) | W1 | 1 |
| W4 | `findings.py` with `add-round` (the check gate, fingerprints, no-repeat) and `send`, the outbox, and `pr` calling it (§3.3, §3.4, §3.5) | W1 | 5 |
| W5 | Go and Ship check `outcomes.md` and the Review tab links it; Done's existing refusal over unsent `replies/` (`vscode/done.js`) extends to an outbox that isn't empty | W4 | 2 |
| W6 | `redmine/import_findings.rb` and the 38 seed rows, after a person confirms §3.2's draft | W1 | 2 |
| W7 | `scripts/eval`, its cases rebuilt from the seeds, and the first scored run (§3.8) | W6, #104 | 5 |
| W8 | The backup exports each issue's findings | W1 | 1 |

The issues already open change to fit:

| Issue | Change |
|---|---|
| #104 | `REVIEW.md` gives each finding a severity and a visibility as well as a tag and a rule, and the reviewer returns them; `gh pc` stops requesting Copilot |
| #106 | The outcome line gains a third part, `visibility/severity`, and is a line in `outcomes.md`, never a PR reply (§3.4) |
| #107 | The no-repeat rule reads fingerprints from the store; each round reviews a pushed commit once its `pytest` check passes; the step-back is round 3, in the report and the PR body, and rounds stop on the open-to-fix count (§3.5) |
| #108 | `findings` reads `rules.json`, not PR threads (§3.7) |
| #110 | The round report shows the round's findings and outcome lines; at the end of the reviewer's turn Claude runs `findings.py send` and updates the PR body, the two Boundaries exceptions (§3.3) |
| #111 | The Done table is the plugin's second table, after W1, and convergence adds its Done rows to the open-to-fix count (§3.5) |

`CLAUDE.md`'s review line, `docs/failure-modes.md`'s weighting, and architecture §6.1, §6.2, §9 and
§10 change with W1 and W4, when the plugin and the writer exist, not in this PR.
