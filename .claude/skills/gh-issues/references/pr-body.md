Fixes #<n>.

<One short paragraph: what the PR makes true, and the problem it fixes. Plain present tense.>

## Done

- [x] <A line of the issue's Done list, copied as written, that this PR meets.>
- [ ] <A line it doesn't meet, copied as written>: <why, or which PR will.>

## Testing

- <Each command run, and its result: `npm test` in `vscode/`, `uv run pytest`, a manual run.>
- <What has no test, and why.>
- <Claude's review, round <k> of `<commit>`: <N> findings, <M> to fix; assess <passed or failed>. Or, before the first push: none yet.>

## Risk

<What could break, and who would notice: a failing check, an error on a button, or nothing until a file is missing. A summary; Failure modes, when the body has it, holds the per-step table.>

## How to revert

<What a revert of this PR leaves behind: a Redmine field it set, a file it wrote, a setting it changed; or that it leaves nothing. SKILL.md's *Reverting a PR* has the steps.>

## <A change, named as the reader would look for it>

<What it does now, and any limit or choice that isn't obvious. Name code in backticks: `shipped()`, `pr <n>`, file paths.>

## Failure modes

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| <A step that writes to git, Redmine, GitHub or a file another window reads> | | | | | |

<!--
Rules (delete this comment):
- The body opens with Fixes #<n>. when the PR finishes the issue, Refs #<n>. when it does part of it.
  gh pc and gh pb end the title the same way; the branch's commits say refs.
- Done copies every line of the issue's ### Done, lens conditions included, as Redmine has it now
  (redmine-get /issues/<n>.json). Tick each the PR meets, and say after a colon why any other
  isn't. The issue's description is never ticked: this section is the record, and Done's closing
  note names the PR.
- Testing, Risk and How to revert are never dropped: when there's nothing to say, say so and why.
  Risk summarises; Failure modes, when present, holds the per-step detail.
- One section per change a reviewer would look for. Drop Failure modes when nothing writes
  (docs/failure-modes.md has the columns).
- No attribution lines.
-->
