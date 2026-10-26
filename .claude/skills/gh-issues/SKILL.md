---
name: gh-issues
description: Work Redmine issues and their PRs the way gh-plan expects. Issues are Redmine's (`#<n>` at http://127.0.0.1:3010, gh-plan ADR 0011); GitHub Issues are not used, and PRs are the one GitHub integration. Use when picking up, drafting or checking an issue, when writing a commit message or PR body, and when answering a PR review. Triggers on "work on #<n>", "pick up #<n>", "draft an issue", "create the ticket", "check the outline", "open the PR", "reply to the review".
---

# gh issues

## Contents

- [Rules](#rules)
- [Aliases and commands](#aliases-and-commands)
- [Picking up an issue](#picking-up-an-issue)
- [Shipping](#shipping)
- [Reverting a PR](#reverting-a-pr)
- [Drafting a new issue](#drafting-a-new-issue)
- [Answering a PR review](#answering-a-pr-review)
- [Ticking the issue's Done list](#ticking-the-issues-done-list)
- [Tests](#tests)

Adapted from GitHub's `github-issues` skill ([github/awesome-copilot](https://github.com/github/awesome-copilot/tree/main/skills/github-issues), MIT), rewritten around Redmine issues and the outlines in `~/.claude/agents/technical-writer.md`.

An issue is a Redmine issue, `#<n>`, its description in the issue outline. GitHub Issues are not used. The skill lives in gh-plan at `.claude/skills/gh-issues/`, and `~/.claude/skills/gh-issues` links to the main checkout's copy, so every repo uses the one skill. The scripts are stdlib Python and shell. Claude runs only the ones that read; every commit, push, `gh` write and Redmine write happens on a button in the GH Plan extension's Next up panel, or a `pr`, `new_issue`, `issue_body`, `redmine-set` or `issue_relations` a person runs.

## Rules

1. **Read the issue before you write about it.** `redmine-get /issues/<n>.json`, or `?include=journals` for its history. Never write an issue's description from memory.
2. **A person presses the buttons.** Claude writes the files in `.tmp/<n>/` (the commit message, the PR body with its Done list ticked, review replies) for a person to review; Ship sends them with `pr`. Never `git commit`, `git push`, `gh pr create` or a Redmine write on Claude's own.
3. **Branches are `<type>/<n>-<slug>`**, `feat/`, `fix/` or `chore/`, off the default branch. Start makes the branch and its worktree. Everything reaches the default branch by PR.
4. **The first commit's subject is the PR's title**, and ends `(refs #<n>)`, never `fixes`: Redmine scans every branch of the shared `.git`, and would close the issue at its next fetch, before the PR merges. `pr` refuses a `commit.txt` that says `fixes #<n>`, in any case, naming the line. The PR body opens `Fixes #<n>.` when the PR finishes issue `<n>`, `Refs #<n>.` only when it does part of it, on its own line; `gh pc` and `gh pb` refuse any other opening. They title the PR with the branch's first commit subject, its `(refs #<n>)` made `(fixes #<n>)` when the body opens `Fixes`; the squash merge keeps the title, and Redmine closes the issue when it next fetches that commit. The first commit's message says what the issue asks for and how this commit does it. When the work is split across PRs on purpose, it says only this PR's part. Every commit message, this one and a later round's, is terse: about ten lines, subject and trailer included, as guidance rather than a limit; a line or two over is fine, and more when the commit's detail needs it. The PR body holds the detail.
5. **A later round's commit describes only what it changes.** Its subject and body name what the round fixes, such as the review findings it answers or a check that failed, and never restate the PR's whole change or tell how earlier rounds led to it: what was found, and how it's fixed. Write it from the round's diff, `git diff` before Go, not from the issue.
6. **PRs get a body file, never `--fill`.** `gh pr create --fill` copies the commit message into the PR, including the `Assisted-by:` trailer, and PR descriptions carry no attribution. Start from [references/pr-body.md](references/pr-body.md).
7. **Look it up, don't guess.** If a gh flag isn't familiar, run `gh <cmd> --help` before saying it doesn't exist or flagging it unverified.
8. **Quote `[]` in zsh.** `-f 'labels[]=bug'`, never bare: zsh treats `[]` as a glob and fails with "no matches found".
9. **Every Redmine write is a committed command.** `new_issue` creates an issue and its relations, `issue_relations` adds or removes relations between issues that exist, `issue_body` replaces a description once its diff is reviewed, and `redmine-set` makes every other change: an issue's status, version, subject, points or journal note, a version's status or description, or a new version, and a review rule's rename across its findings. Never generate Python for a Redmine write, neither `python3 -c` nor a `.tmp/*/update.py`; a change none of them makes is an issue for `redmine-set`. Run `redmine-set --check …` or `issue_relations --check …`, which only read, then hand over the same command without `--check`, adding the `--expect` it prints for a description. A change over many issues, such as a milestone move, is a shell script of `redmine-set` lines, reviewed before it runs.

## Aliases and commands

`gh pc` and `gh pb` are gh aliases over `scripts/ghbody.sh`. Each takes an optional issue number and otherwise reads it from the branch name (`fix/88-go-requests-copilot-s-review` → `88`). `pr`, `new_issue`, `issue_body`, `redmine-set`, `issue_relations` and `redmine-get` are in `~/.local/bin/`.

| Command | Does |
|---|---|
| `gh pc [N]` | `gh pr create`, titled with the branch's first commit subject, its keyword the body's (rule 4), body `.tmp/<n>/pr-body.md`, and asks Copilot to review |
| `gh pb [N]` | `gh pr edit` on this branch's PR with `.tmp/<n>/pr-body.md`, and the title as `gh pc` makes it |
| `pr [N]` | What Ship runs: commits with `.tmp/<n>/commit.txt`, rebases onto `origin/main` when behind it, pushes, opens or updates the PR, sends the replies |
| `pr done [N]` | After the merge: sends any replies left and the PR body, so a Done line ticked after the merge reaches the PR, then says to press Done in Next up, which cleans up and closes the issue. Bare `pr` does the same once the PR has merged |
| `issue_body --check N FILE` | Checks the file against the outline, shows the diff against issue `N`'s description, and prints the `issue_body --expect …` command that writes it. Only reads |
| `issue_body --expect D N FILE` | The printed command: replaces the description, refusing if it changed since `--check`, and reads it back. A file that matches writes nothing |
| `redmine-set issue N …` | Changes issue `N`: `--status NAME`, `--version NAME` or `--no-version`, `--subject`, `--points`, `--description FILE` with the `--expect` that `--check` prints, and `--note TEXT` for the journal. Checks every name against Redmine first, and refuses to reopen an issue in a closed version, naming `--no-version` and the version's reopen command |
| `redmine-set version NAME …` | Changes a version in `plan` (`--project` picks another): `--status open\|locked\|closed`, `--description`; `--create` makes it, shared with the tree |
| `redmine-set rule OLD NEW` | Renames review rule `OLD` to `NEW` on every finding in `plan` and its subprojects (`--project` picks another), in one transaction, with a journal note on each confirmed one |
| `redmine-set --check …` | Prints each change any of these would make. Only reads |
| `issue_relations [--check] [--remove] A TYPE B …` | Adds each relation that's missing, or with `--remove` removes each, and reads them back. `124 blocked_by 104` finds `104 blocks 124`; another relation between the same two issues is refused. `--check` only reads |

`gh pc` and `gh pb` refuse a body containing `Assisted-by` or `Co-authored-by`. `GHBODY_DRY=1` prints the command instead.

## Picking up an issue

A person presses **Start** on the issue's row in Next up. It makes the branch and its worktree, sets the issue In Progress, and opens the worktree in a new window with Claude Code beside it. Then, before any code:

1. **Read the issue**, with `redmine-get /issues/<n>.json`.
2. **Check its description** against the issue outline, Task, Guardrails, Lenses and Done, plus whatever the repo's `docs/agents/technical-writer.md` adds. Save the description to a file and run, from inside the repo so it finds that file:
   ```
   python3 ~/.claude/skills/gh-issues/scripts/outline.py <file>
   ```
   Done must be at least two pass/fail checkboxes, each `[x]` lens needs a Done condition that names it (`- [ ] L6: …`), and no Done line is ticked. If the description doesn't conform, write the rewrite to `.tmp/<n>/issue-<n>-body.md`, the description alone, and run `issue_body --check <n> .tmp/<n>/issue-<n>-body.md`, which only reads: the outline check and the diff. Then hand over the `issue_body --expect …` command it prints; it writes the description and nothing else, refuses if the description changed since the check, and reads it back. The same goes for an issue the work changes. An issue in the old outline (Summary, Scope, Acceptance, Tests, Lenses) is rewritten now: Summary and Scope become Task, out-of-scope lines become Guardrails, Lenses stays, and Acceptance and Tests become Done's conditions.
3. **Code once the description conforms.**

## Shipping

When the work is done and tested, and `/code-review low` has run on the uncommitted changes, write `.tmp/<n>/commit.txt` (rules 4 and 5, ending `Assisted-by: Claude:<model>`) and `.tmp/<n>/pr-body.md`, its `## Done` section ticked as below and followed by Testing, Risk and How to revert as [references/pr-body.md](references/pr-body.md) lays them out, plus any replies. The extension opens them as tabs. A person reviews them and presses **Go**, which checks them, then stages every change and shows the staged diff and commit message in the issue's `Review #<n>` tab (VS Code's Staged Changes only for a diff over 1 MB). **Ship** refuses any file changed since Go, `commit.txt` included, then runs `pr <n>` and links the PR in the issue's Pull requests field. Go and Ship again run `pr` again, for the next round. After the merge, **Done** cleans up and closes the issue.

**The end of the reviewer's turn.** A round reviews the commit Ship pushed, once its `assess` check passes. After the review, record its findings with `findings.py send <n>`, once #124 builds it, then update the PR body with `gh pb`, its Done list as it stands (unticked in round 1). These are Claude's only writes outside a button.

**The round report.** After answering a round, write `.tmp/<n>/report.html` last, after `commit.txt` and `pr-body.md`. The extension shows it in the `Review #<n>` tab, where Go and Ship are. It's one self-contained HTML file. Its head holds `<meta name="plan-round" content="<k>">`. Its body opens with two tables. The first gives the review: the command, the commit it reviewed and the base, where the round's fixes are, the `/code-review low` before Go, as run (with its date and time, and its result) or waiting, and the `assess` and test counts. The second counts the findings: all, New, Fixed and Left (or whatever other outcome a finding has), and the open silent failures. Next comes the findings list, each link labelled with its outcome: New blue, Fixed green, any other amber. Each finding then gives its title with the same label, the fix or the note refuting it, and its drafted outcome line from `outcomes.md`. Under it, each diff hunk the fix touched, test included, sits in its own `<details>`, labelled `file:line +adds −dels`, with old and new line numbers and added and removed lines shaded. A finding left as it is shows the code it names, by `file:line`, the same way. Links to `commit.txt` and `pr-body.md` by name close it. It runs no script and loads nothing: an image is inline SVG or a `data:` URI.

## Reverting a PR

A revert is an issue of its own, started and shipped like any other. On its branch, `git revert --no-commit <the squash commit>` stages the revert and `git revert --quit` ends the revert git started, which `pr` would otherwise refuse as in progress; then `.tmp/<m>/commit.txt` gives it a subject that ends `(refs #<m>)`, the revert's own issue, and `pr-body.md` opens `Fixes #<m>.`, so the PR title ends `(fixes #<m>)`. Never commit `git revert`'s own subject or use GitHub's Revert button: both copy the original's `(fixes #<n>)`, and Redmine closes #<n> again when it fetches the commit. When the reverted PR fixed #<n>, #<n> stays closed with that PR in its Pull requests field, so a person reopens it. The reverted PR's How to revert section lists anything else to undo.

## Drafting a new issue

1. Copy `references/redmine-issue.md` to `.tmp/<n>/<slug>.md`, `<n>` being the issue whose work found it. Fill in the front matter by the names Redmine shows: `tracker` and `subject` are required. There is no `project` field: `new_issue` files in the project whose homepage is the origin of the checkout it runs in, and refuses outside a checkout, for an origin no project has, and for a `project:` line. A blank field is left out. With no version, set `status: Icebox`. Name issues as numbers, `#79` or `79`, with commas between them in `blocked_by` and `relates`. Then write the body in the outline.
2. Check it from the checkout of the repo the issue is for, so its lenses count and its project is found, giving the draft's full path when it is in another repo's worktree. This only reads: it runs the outline check, looks up every name in Redmine and checks every value.
   ```
   cd <checkout> && new_issue --check <draft>
   ```
   The result names the project the issue goes to.
3. Hand over the same command without `--check`, as `cd <checkout> && new_issue <draft>`. It creates the issue in that project, adds its relations and reads it back. A second run finds the issue by its subject and adds only the relations still missing.

## Answering a PR review

Fix the findings first. Then write each open thread's context into `.tmp/<n>/replies/`, one `<comment id>.md` each, and write the reply below it: "Fixed: …" saying what changed, or why not. The context, the path and line, the review comment and the last lines of the diff hunk it's on, is an HTML comment at the top, so a person reads each reply against what it answers; `pr` strips it before posting. Run `--drafts` again after writing replies without it, and it adds the context above them.

```
python3 ~/.claude/skills/gh-issues/scripts/pr_threads.py 73          # open threads: comment id, path:line, first line
python3 ~/.claude/skills/gh-issues/scripts/pr_threads.py 73 --drafts .tmp/<n>/replies
```

A file with no reply below its context stops `pr` before the first post; delete the file for a thread that gets no reply.

`pr`, after its push, posts each reply to its thread, resolves the thread and deletes the file, so the replies describe code that's on the PR (`pr_threads.py 73 --post .tmp/<n>/replies`). It checks every file before the first post, and a reply the thread already has is not posted again. The replies open for review beside `commit.txt` and `pr-body.md`.

## Ticking the issue's Done list

The PR records what the issue's work met; the issue keeps the spec, and its description is never ticked. The PR body's `## Done` section, after its opening paragraph, copies each line of the issue's `### Done` as Redmine has it now (`redmine-get /issues/<n>.json`), ticks the ones the PR meets, and says after any unticked line why it isn't met, or which PR will meet it. Testing and the change sections below it hold the proof. Update it each round, and `pr` sends it with the body. Done's closing note names the PR: "Done: verified in gh-plan#117".

A `.tmp/<n>/issue.md` from before is never sent: `pr` names it and leaves it, and Done removes it with the worktree.

## Tests

In gh-plan, `uv run pytest` runs them with the rest. Alone:

```
cd ~/.claude/skills/gh-issues/scripts && python3 -m unittest test_scripts
```
