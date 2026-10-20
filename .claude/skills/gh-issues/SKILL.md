---
name: gh-issues
description: Work Redmine issues and their PRs the way Peter's repos expect. Issues are Redmine's (`#<n>` at http://127.0.0.1:3010, gh-plan ADR 0011); GitHub Issues are not used, and PRs are the one GitHub integration. Use when picking up, drafting or checking an issue, when writing a commit message or PR body, and when answering a PR review. Triggers on "work on #<n>", "pick up #<n>", "draft an issue", "create the ticket", "check the outline", "open the PR", "reply to the review".
---

# gh issues

Adapted from GitHub's `github-issues` skill ([github/awesome-copilot](https://github.com/github/awesome-copilot/tree/main/skills/github-issues), MIT), rewritten around Redmine issues and the outlines in `~/.claude/agents/technical-writer.md`.

An issue is a Redmine issue, `#<n>`, its description in the issue outline. GitHub Issues are not used. The skill lives in gh-plan at `.claude/skills/gh-issues/`, and `~/.claude/skills/gh-issues` links to the main checkout's copy, so every repo uses the one skill. The scripts are stdlib Python and shell. Claude runs only the ones that read; every commit, push, `gh` write and Redmine write happens on a button in the GH Plan extension's Next up panel, or a `pr` Peter runs.

## Rules

1. **Read the issue before you write about it.** `redmine-get /issues/<n>.json`, or `?include=journals` for its history. Never write an issue's description from memory.
2. **Peter presses the buttons.** Claude writes the files in `.tmp/<n>/` (the commit message, the PR body, review replies, the ticked description) for Peter to review; Ship sends them with `pr`. Never `git commit`, `git push`, `gh pr create` or a Redmine write on Claude's own.
3. **Branches are `<type>/<n>-<slug>`**, `feat/`, `fix/` or `chore/`, off the default branch. Start makes the branch and its worktree. Everything reaches the default branch by PR.
4. **The commit subject is the PR's title**, and ends `(fixes #<n>)` when the PR finishes issue `<n>`, `(refs #<n>)` only when it does part of it. `gh pc` titles the PR with the branch's first commit subject, the squash merge keeps it, and Redmine closes the issue when it next fetches that commit. The PR body needs no closing line.
5. **PRs get a body file, never `--fill`.** `gh pr create --fill` copies the commit message into the PR, including the `Assisted-by:` trailer, and PR descriptions carry no attribution. Start from [references/pr-body.md](references/pr-body.md).
6. **Look it up, don't guess.** If a gh flag isn't familiar, run `gh <cmd> --help` before saying it doesn't exist or flagging it unverified.
7. **Quote `[]` in zsh.** `-f 'labels[]=bug'`, never bare: zsh treats `[]` as a glob and fails with "no matches found".

## Aliases and commands

`gh pc` and `gh pb` are gh aliases over `scripts/ghbody.sh`. Each takes an optional issue number and otherwise reads it from the branch name (`fix/88-go-requests-copilot-s-review` → `88`). `pr`, `new_issue` and `redmine-get` are in `~/.local/bin/`.

| Command | Does |
|---|---|
| `gh pc [N]` | `gh pr create`, titled with the branch's first commit subject, body `.tmp/<n>/pr-body.md`, and asks Copilot to review |
| `gh pb [N]` | `gh pr edit` on this branch's PR with `.tmp/<n>/pr-body.md` |
| `pr [N]` | What Ship runs: commits with `.tmp/<n>/commit.txt`, pushes, opens or updates the PR, asks Copilot again when the push adds commits, sends the replies and ticks |
| `pr done [N]` | After the merge: removes the worktree, updates the default branch, deletes the branch, has Redmine fetch the commits. Done in Next up does this and closes the issue |

`gh pc` and `gh pb` refuse a body containing `Assisted-by` or `Co-authored-by`. `GHBODY_DRY=1` prints the command instead.

## Picking up an issue

Peter presses **Start** on the issue's row in Next up. It makes the branch and its worktree, sets the issue In Progress, and opens the worktree in a new window with Claude Code beside it. Then, before any code:

1. **Read the issue**, with `redmine-get /issues/<n>.json`.
2. **Check its description** against the issue outline, Task, Guardrails, Lenses and Done, plus whatever the repo's `docs/agents/technical-writer.md` adds. Save the description to a file and run, from inside the repo so it finds that file:
   ```
   python3 ~/.claude/skills/gh-issues/scripts/outline.py <file>
   ```
   Done must be at least two pass/fail checkboxes, and each `[x]` lens needs a Done condition that names it (`- [ ] L6: …`). If the description doesn't conform, show Peter the rewrite in chat; he makes it in Redmine. An issue in the old outline (Summary, Scope, Acceptance, Tests, Lenses) is rewritten now: Summary and Scope become Task, out-of-scope lines become Guardrails, Lenses stays, and Acceptance and Tests become Done's conditions.
3. **Code once the description conforms.**

## Shipping

When the work is done and tested, write `.tmp/<n>/commit.txt` (ending `Assisted-by: Claude:<model>`) and `.tmp/<n>/pr-body.md`, plus any replies and ticks below. The extension opens them as tabs. Peter reviews them and presses **Go**, which checks them and opens the changes, then **Ship**, which runs `pr <n>` and links the PR in the issue's Pull requests field. Go and Ship again run `pr` again, for the next round. After the merge, **Done** cleans up and closes the issue.

## Drafting a new issue

1. Copy `references/redmine-issue.md` to `.tmp/<n>/<slug>.md`, `<n>` being the issue whose work found it. Fill in the front matter by the names Redmine shows: `project`, `tracker` and `subject` are required. A blank field is left out. With no version, set `status: Icebox`. Name issues as numbers, `#79` or `79`, with commas between them in `blocked_by` and `relates`. Then write the body in the outline.
2. Check it, from inside the repo so the repo's lenses count. This only reads: it runs the outline check, looks up every name in Redmine and checks every value.
   ```
   new_issue --check .tmp/<n>/<slug>.md
   ```
3. Give Peter the same command without `--check`, as `! new_issue .tmp/<n>/<slug>.md`. It creates the issue, adds its relations and reads it back. A second run finds the issue by its subject and adds only the relations still missing.

## Answering a PR review

Fix the findings first, then write a reply to each thread as `.tmp/<n>/replies/<comment id>.md`: "Fixed: …" saying what changed, or why not. List the open threads and their comment ids with:

```
python3 ~/.claude/skills/gh-issues/scripts/pr_threads.py 73          # open threads: comment id, path:line, first line
```

`pr`, after its push, posts each reply to its thread, resolves the thread and deletes the file, so the replies describe code that's on the PR (`pr_threads.py 73 --post .tmp/<n>/replies`). It checks every file before the first post, and a reply the thread already has is not posted again. The replies open for review beside `commit.txt` and `pr-body.md`.

## Ticking the issue's Done list

When a round meets Done conditions, write `.tmp/<n>/issue.md`: the issue's description as Redmine has it now (`redmine-get /issues/<n>.json`), with those boxes ticked. Change nothing else. `pr` applies it after its push (`checklist.py <n> .tmp/<n>/issue.md`) only when checkbox state is the one difference, with a journal note listing each box, then deletes it. Any other difference, such as an edit made in Redmine since, stops `pr` naming the line, and the file is kept.

## Tests

In gh-plan, `uv run pytest` runs them with the rest. Alone:

```
cd ~/.claude/skills/gh-issues/scripts && python3 -m unittest test_scripts
```
