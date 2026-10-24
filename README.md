# gh-plan

GH Plan plans work in [Redmine](https://www.redmine.org/), outside the repos it plans
([ADR 0011](docs/adr/0011-redmine-is-the-tracker.md)). Each repo is a Redmine project under one
parent project, so a milestone, a dependency or a lane can span repos. A VS Code extension takes
each issue from its branch and worktree to its pull request, and its Done button closes the issue
once the PR merges. It isn't an issue tracker of its own, it doesn't use GitHub Issues, and it isn't
a hosted service or a Python package: Redmine runs in Docker on localhost, and pull requests are
the one GitHub integration.

This repo is the setup around Redmine:

- [`redmine/`](redmine/README.md): Redmine in Docker on localhost, its projects, fields and
  workflow, the lane board plugin, and the nightly backup.
- [`vscode/`](vscode/README.md): the VS Code extension. Its Next up panel starts, ships and
  finishes each issue.
- [`.claude/`](.claude/): the attribution hook, and the gh-issues skill with its `pr`,
  `new_issue`, `issue_body` and `ghbody.sh` scripts, the last behind the `gh pc` and `gh pb` aliases.

## Setup

It needs Docker, [uv](https://docs.astral.sh/uv/), Python 3.14 as `python3` on PATH (the skill's
scripts run on it, and `/usr/bin/python3` is too old), Node, the [`gh`](https://cli.github.com/)
CLI signed in, and VS Code with Claude Code. Clone to `~/programming/gh-plan`: Redmine mounts that
checkout's `.git` and Start looks for it there (`GH_PLAN_GIT` and `plan.checkouts` point
elsewhere). On a Mac, from the root of that main checkout, never a worktree, which Done deletes:

1. **Redmine:** start it, sign in and run the setup, as
   [`redmine/README.md`](redmine/README.md) says in *Starting and stopping* and *Running the
   setup*.
2. **The API key:** store yours in the Keychain, as [`vscode/README.md`](vscode/README.md) says
   under *Settings*.
3. **The extension:** build and install it, as [`vscode/README.md`](vscode/README.md) says in
   *Build, install, test*.
4. **The commands:** link the gh-issues skill and its scripts, so every repo uses this checkout's
   copy. `~/.local/bin` must come before `/usr/bin` on PATH, which has a `pr` of its own:

   ```sh
   mkdir -p ~/.claude/skills ~/.local/bin
   ln -sfn "$PWD/.claude/skills/gh-issues" ~/.claude/skills/gh-issues
   ln -sfn ~/.claude/skills/gh-issues/scripts/pr.sh ~/.local/bin/pr
   ln -sfn ~/.claude/skills/gh-issues/scripts/new_issue.py ~/.local/bin/new_issue
   ln -sfn ~/.claude/skills/gh-issues/scripts/issue_body.py ~/.local/bin/issue_body
   gh alias set --clobber --shell pc '~/.claude/skills/gh-issues/scripts/ghbody.sh pr-create "$@"'
   gh alias set --clobber --shell pb '~/.claude/skills/gh-issues/scripts/ghbody.sh pr-edit "$@"'
   ```

## Tests

```sh
uv sync
uv run pytest
```

`uv run pytest` runs `tests/`, `.claude/hooks/` and the gh-issues scripts, and fails when coverage
of `redmine/` and `.claude/hooks/` is under 90%. The extension's tests and lint run in `vscode/`:

```sh
cd vscode && npm install && npm test && npm run lint
```

## Docs

- [`docs/architecture.md`](docs/architecture.md): the design, and the invariants every change
  holds.
- [`docs/requirements.md`](docs/requirements.md): what each button and script must do.
- [`docs/failure-modes.md`](docs/failure-modes.md): how a write to git, Redmine or GitHub can fail,
  and what guards it.
- [`docs/adr/`](docs/adr/): the decisions, one per file.
