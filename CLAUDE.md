# gh-plan — repo instructions

**GH Plan** plans work in Redmine ([ADR 0011](docs/adr/0011-redmine-is-the-tracker.md)), run in
Docker on localhost (`redmine/`), with a VS Code extension (`vscode/`) that starts, ships and
finishes each issue. It does not use GitHub Issues; pull requests are its one GitHub integration. Nothing is inherited from a parent directory's `CLAUDE.md` or
settings: what this repo needs is here or in `.claude/`.

## Read before

- Read `docs/architecture.md` before moving a responsibility between modules, and §10 before any
  change to how the tool writes to git, GitHub or Redmine.
- Read the ADR in `docs/adr/` that decided a behaviour before changing it: ADR 0011 before touching
  issues, branches or anything that writes to Redmine.
- Read `docs/requirements.md` before adding a button or script, or changing what one does.
- Read `docs/failure-modes.md` before writing a command that writes to git, Redmine, GitHub or a
  file another window reads, and before fixing a review finding in one.
- Read `docs/agents/technical-writer.md` before writing an issue, ADR or design doc.
- Read Redmine's `Next up` query before answering "what's next":
  `redmine-get '/projects/plan/issues.json?query_id=8'` (`/queries.json` lists the IDs), or the
  Next up panel.

## Commands

- **Assess:** `scripts/assess` runs every check below and each checker in `checks/`, one line
  each, and exits 1 if any failed; it is CI's one required check. Run it before writing
  `commit.txt`, and whenever asked. `--rules` lists the rules `checks/` covers.
- **Tests:** `uv run pytest` runs `tests/` (the backup and contents-list suites), `.claude/hooks/`
  and the gh-issues scripts, and fails under 90% coverage of `redmine/` and `.claude/hooks/`.
- **Python lint:** `uv run ruff check .` and `uv run ruff format --check .`; `uv run ruff format .`
  fixes the format.
- **The VS Code extension:** `npm test` and `npm run lint`, in `vscode/`, after `npm install`.
- **Review findings:** weigh severity, visibility and frequency. Fix silent failures that lose
  work, files, commits or the tracker's state, and hard crashes, even rare ones. A rare warning or
  a clean error-stop can stay.
- **Before each push:** `/code-review high`, once `scripts/assess` passes, on the branch's whole
  diff, review fixes included. The PR body says it ran.
- **Python:** `python3` on PATH. `/usr/bin/python3` is 3.9 and can't run the skill scripts.
- **An issue's work:** Start, Go, Ship and Done in the extension's Next up panel
  ([`vscode/README.md`](vscode/README.md)).
- **An issue's body:** `redmine-get /issues/<n>.json`, or `?include=journals` for its history.
- **A PR:** Ship, or `pr <n>`, from `.tmp/<n>/commit.txt` and `.tmp/<n>/pr-body.md`.

## Done differently

- **Issues are Redmine's**, `#<n>` at http://127.0.0.1:3010, never GitHub Issues or files in the repo.
- **Branches** are `<type>/<n>-<slug>`: `feat/`, `fix/` or `chore/`. Short-lived, off `main`,
  squash-merged. Nothing reaches `main` except by PR.
- **Review replies are files** that `pr` sends, one per review thread in
  `.tmp/<n>/replies/<comment id>.md` (the gh-issues skill).
- **Done ticks are the PR's.** The PR body's `## Done` section copies the issue's Done list and
  ticks what the PR meets; the issue's description is never ticked.
- **A PR title ends `(fixes #<n>)`** when the PR finishes Redmine issue `<n>`, and `(refs #<n>)`
  only when it does part of it. The title becomes the squash commit's subject, and Redmine closes
  the issue when it next fetches that commit.
- **Dependabot's PRs are the exception** to the branch and title rules (`.github/dependabot.yml`):
  `dependabot/` branches, `chore:` titles, no Redmine issue and no Done. A person, never an agent,
  merges one once CI passes, and a Redmine one by `redmine/README.md`'s *Upgrading*; CI doesn't
  build Redmine.
- **An AI is never an author** (the Linux kernel's
  [coding-assistants](https://docs.kernel.org/process/coding-assistants.html) rule). No
  `Co-Authored-By`, `Signed-off-by`, `Claude-Session` or "Generated with" line names an AI, in a
  commit, a PR description or a document. Every commit Claude makes ends with
  `Assisted-by: Claude:<model>`; PR descriptions carry no attribution.

## Boundaries

- **Peter presses the buttons.** Every commit, push, `gh` write and Redmine write happens on Start,
  Ship or Done, or a `pr`, `new_issue` or `issue_body` he runs. Write `.tmp/<n>/commit.txt`,
  `pr-body.md`, `replies/` and issue drafts for him to review; reads, inspections, the test
  suite and cleaning up your own mess you run yourself.
- **An agent commits only on its own issue branch,** and only when asked.
- **Never commit secrets.** The tool authenticates by shelling out to `gh` (requirement `G3`); do
  not add a library that needs a PAT.
- **Never use real personal data** in fixtures, tests or examples.
- **No speculative files.** No directory or file until something goes in it.
- **Agree a doc change in chat first**, then edit in place on the branch.
- **Invariants** ([architecture §10](docs/architecture.md#invariants)); these two break most
  easily:
  - **2.** The default branch carries the code, and everything reaches it by PR. Status lives in
    Redmine.
  - **6.** Only the named modules, and Ship's `pr` task, spawn a process or open a connection; every
    Redmine call goes through `vscode/redmine.js`.

## Gotchas

- **The attribution hook** (`.claude/hooks/no_ai_attribution.py`) reads the command text: the
  `Assisted-by:` trailer must be literal in the `git commit` command, not in a variable. It
  applies to throwaway test repos too.
- **`gh pr create --fill`** copies the commit's `Assisted-by:` trailer into the PR body. Use `gh pc`.
- **zsh globs `[]`:** quote it, `-f 'labels[]=bug'`.
- **GitHub's docs and GitHub's behaviour differ.** Verify against the API, and give a claim about
  it a source or mark it 🔍 unverified.
