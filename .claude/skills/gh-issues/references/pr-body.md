<One short paragraph: what the PR makes true, and the problem it fixes. Plain present tense.>

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
- The title, from the commit subject, ends (fixes #<n>) or (refs #<n>); the body needs no closing line.
- One section per change a reviewer would look for. Drop Failure modes when nothing writes
  (docs/failure-modes.md has the columns).
- No attribution lines.
-->
