# gh-plan — repo instructions

**GH Plan** is a file-native tracker: a local CLI (later a localhost web UI) over Markdown item
files committed in the repo each item belongs to. It does not use GitHub Issues; pull requests are
its one GitHub integration. Nothing is inherited from a parent directory's `CLAUDE.md` or
settings: what this repo needs is here or in `.claude/`.

## Read before

- Read `docs/design/0001-phase-1.md` before writing code.
- Read `docs/architecture.md` before moving a responsibility between modules, and §10 before any
  change to how the tool writes to git, GitHub or the item files.
- Read the ADR in `docs/adr/` that decided a behaviour before changing it: ADR 0008 before touching
  items, branches or `plan:` commits.
- Read `docs/requirements.md` before adding a command or changing what one does.
- Read `docs/failure-modes.md` before writing a command that writes to git, Redmine, GitHub or a
  file another window reads, and before fixing a review finding in one.
- Read `docs/agents/technical-writer.md` before writing an item, ADR or design doc.
- Read `.tracker/ready.yml` and the item files before answering "what's next".

## Commands

- **Tests:** `uv run pytest`. It also runs `.claude/hooks/`, and fails under 90% coverage.
- **The VS Code extension:** `npm test` and `npm run lint`, in `vscode/`, after `npm install`.
- **Review findings:** fix only the silent ones, failures that leave no trace and lose work, files,
  commits or the tracker's state. A failure that shows the user something is wrong is already
  mitigated.
- **Before each push:** `/code-review high` on the branch's whole diff, review fixes included. The
  PR body says it ran.
- **Python:** `python3` on PATH. `/usr/bin/python3` is 3.9 and can't run the skill scripts.
- **An item's work:** `worktask start|next|done <id>` (the worktask skill). `start` pushes the
  `plan: start <id>` commit and makes the worktree and branch.
- **An item's body:** `python3 ~/.claude/skills/gh-issues/scripts/outline.py .tracker/items/<id>.md`,
  from inside the repo.
- **A PR:** `gh pc` creates it, `gh pb` updates its body, from `.tmp/<id>/pr-body.md`.
- **`plan next` and `plan ready`:** the `plan` skill is broken; read `.tracker/ready.yml` instead.

## Done differently

- **Items are files**, `.tracker/items/<id>.md`, IDs `pl-<CVCVC>`, never GitHub Issues.
- **Branches** are `<type>/<id>-<slug>`: `feat/`, `fix/` or `chore/`. Short-lived, off `main`,
  squash-merged.
- **`plan:` commits go straight to `main`**, and nothing else does.
- **An item's edits ride on its branch**, and reach `main` with the PR, like code.
- **A PR title ends `(fixes #<n>)`** when the PR finishes Redmine issue `<n>`, and `(refs #<n>)`
  only when it does part of it. The title becomes the squash commit's subject, and Redmine closes
  the issue when it next fetches that commit.
- **An AI is never an author** (the Linux kernel's
  [coding-assistants](https://docs.kernel.org/process/coding-assistants.html) rule). No
  `Co-Authored-By`, `Signed-off-by`, `Claude-Session` or "Generated with" line names an AI, in a
  commit, a PR description or a document. Every commit Claude makes ends with
  `Assisted-by: Claude:<model>`; PR descriptions carry no attribution.

## Boundaries

- **Peter runs the commands, from plan-board cards.** Hand over each commit, push or `gh` write as
  a card in the main checkout's `.tmp/plan-board/<id>/`
  ([design 0003](docs/design/0003-plan-board.md)), never as a line in chat, and say why in the
  card. The card's `steps` list every command that will run, verbatim and in order; the runner
  runs exactly those, and refuses a card whose steps differ from its recipe's. Until the runner
  ships, Peter runs the card's steps himself, copied from the card. Read a card's result before
  planting the next. Reads, inspections, the test suite and cleaning up your own mess you run
  yourself.
- **An agent commits only on its own item branch,** and only when asked.
- **Never commit secrets.** The tool authenticates by shelling out to `gh` (requirement `G3`); do
  not add a library that needs a PAT.
- **Never use real personal data** in fixtures, tests or examples.
- **No speculative files.** No directory or file until something goes in it.
- **Agree a doc change in chat first**, then edit in place on the branch.
- **Invariants** ([architecture §10](docs/architecture.md#invariants)); these three break most
  easily:
  - **2.** The default branch carries the current state. Everything reaches it by PR, except
    `plan:` commits.
  - **3.** A tool commit touches one item's `status` and `started`, plus `ready.yml`.
  - **6.** Only the named modules spawn a process or open a connection; every Redmine call goes
    through `vscode/redmine.js`.

## Gotchas

- **The attribution hook** (`.claude/hooks/no_ai_attribution.py`) reads the command text: the
  `Assisted-by:` trailer must be literal in the `git commit` command, not in a variable. It
  applies to throwaway test repos too.
- **`gh pr create --fill`** copies the commit's `Assisted-by:` trailer into the PR body. Use `gh pc`.
- **zsh globs `[]`:** quote it, `-f 'labels[]=bug'`.
- **GitHub's docs and GitHub's behaviour differ.** Verify against the API, and give a claim about
  it a source or mark it 🔍 unverified.
