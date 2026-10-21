Fixes #<n>.

<One short paragraph: what the PR makes true, and the problem it fixes. Plain present tense.>

## Done

- [x] <A line of the issue's Done list, copied as written, that this PR meets.>
- [ ] <A line it doesn't meet, copied as written>: <why, or which PR will.>

## <A change, named as the reader would look for it>

<What it does now, and any limit or choice that isn't obvious. Name code in backticks: `shipped()`, `pr <n>`, file paths.>

## Failure modes

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| <A step that writes to git, Redmine, GitHub or a file another window reads> | | | | | |

## Tests

- <Each suite run, and its result: `npm test` in `vscode/`, `uv run pytest`.>
- <What has no test, and why.>
- `/code-review high` ran on the branch's whole diff, review fixes included.

<!--
Rules (delete this comment):
- The body opens with Fixes #<n>. when the PR finishes the issue, Refs #<n>. when it does part of it,
  matching the title, which (from the commit subject) ends (fixes #<n>) or (refs #<n>).
- Done copies every line of the issue's ### Done, lens conditions included, as Redmine has it now
  (redmine-get /issues/<n>.json). Tick each the PR meets, and say after a colon why any other
  isn't. The issue's description is never ticked: this section is the record, and Done's closing
  note names the PR.
- One section per change a reviewer would look for. Drop Failure modes when nothing writes
  (docs/failure-modes.md has the columns).
- No attribution lines.
-->
