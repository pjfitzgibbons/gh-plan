# Failure modes

A command that writes outside its own process, to git, Redmine, GitHub or a file another window reads, gets a failure-mode table in its PR body before its code is written. The table lists each step that writes or reads outside state against five ways it can go wrong:

| Column | The question |
|---|---|
| **Fails** | The step fails part way. Does the next run finish the job, from whatever it left behind? |
| **Twice** | The command runs again after success. Does it change nothing? |
| **At once** | Two runs overlap, from two clicks or two windows. Does one join the other, or fail before it writes? |
| **Stale** | What it read has changed since: a row, a status, a folder deleted by hand. Does it check again before writing? |
| **Guards** | Each limit or existence check it adds. Which recovery does it block? |

A cell names the line or test that answers it, or says "open" for Peter to decide. Every *Fails* cell gets a row in the command's fault-injection test, which fails that step once and asserts the next run ends where a clean one does (`STEPS` in `vscode/test/start.test.js`).

A review fix re-reads the whole table. Five of PR #110's findings came from fixes that broke another row. Prefer a refusal that names its fix to automatic recovery: recovery code is new surface, and three findings came from it.

Before each push, `/code-review high` runs on the branch's whole diff, review fixes included, and the PR body says it ran.

## Redmine drops a status it won't allow

Redmine answers an issue update with 204 even when it has refused the status in it. It drops `status_id` and keeps the rest of the update, the journal note included (`Issue#safe_attributes=`, `app/models/issue.rb` lines 619–624 in 7.0.2). It drops the status when:

- no workflow transition from the issue's status to the new one exists for any of the user's roles. The administrator takes every role, so only a non-admin key meets this;
- the issue would close while an open issue blocks it or it has an open subtask (`closable?`, lines 1126–1128). On 2026-10-18 this kept #80 open: #50 blocked it;
- the issue would reopen while it is a subtask of a closed parent (`reopenable?`, line 1130).

An issue created with a status its workflow doesn't allow from the start gets the first status it does allow, or the default status (lines 625–626). #84, created as Icebox, came back New.

A 204 doesn't mean the status changed. A write that sets a status reads the issue back, and if the status isn't the one it sent, it refuses and names the likely rule (#85). The note is saved by then, so the refusal says so. A commit's `fixes #<n>` doesn't go through this filter (`Changeset#fix_issue` uses `assign_attributes`, `app/models/changeset.rb` line 253), so a commit can close a blocked issue that the API can't.

## Start (PR #110)

Within a window, a second click on an issue joins the Start under way. Across windows, a lock file in the repo's git dir, holding the extension host's pid, refuses the second Start before it changes anything. A lock left by a Start that stopped is never taken over, because two windows taking it at once could both win. The refusal names the file for Peter to delete.

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| Read the issue | Nothing written; rerun | — | — | Any status but New or In Progress is refused | — |
| Read the blockers | A blocker Redmine won't show is listed as unknown | — | — | — | — |
| Lock | — | Released after every Start | The second window is refused | A dead holder's lock is refused by name, never taken over | Blocks recovery after a crash until the file is deleted |
| Fetch origin | Stops before any branch | No-op | git locks the repo | `--prune` drops a deleted `develop`; the base is `develop`, else origin's default, else `main` | — |
| Setup marker | Written before the worktree, removed if `git worktree add` fails and leaves no worktree (a failed hook leaves one), and after the handoff, so a failure in between reruns setup | Absent once set up, so Open changes nothing | — | A worktree made by hand or before the marker counts as set up | — |
| Branch and worktree | Rerun reuses the branch left behind, on its own base | Found by number, reused | Behind the lock | A listed worktree deleted by hand is refused, naming `git worktree prune`; the issue's branch in the main checkout is refused; a worktree mid-rebase is found by its folder | Only `feat/`, `fix/`, `chore/` or no prefix names an issue's branch |
| Pushed branch | — | — | — | An issue branch on `origin` is checked out, tracking it, before a new one is made | — |
| `uv sync` | Reruns while the marker exists | Skipped once set up | Behind the lock | — | — |
| Status to In Progress | Rerun sends it | Skipped when In Progress | Behind the lock | Refused statuses never reach it. A subtask of a closed parent, or a key whose roles lack New → In Progress, gets the journal note without the status, and the next Start sends both again: open (#85) | — |
| Handoff to Claude | The last setup step: a failed write leaves the marker, so the next Start writes it. Kept until Claude starts; reload or Open retries | Written only by the Start that set up, or to retry one kept | One file per worktree, written aside and renamed in; an open window watches for its own | — | Expires after a day, so a forgotten one can't start Claude unasked |

### Open

Found by the last local review and left unfixed: each is unlikely in use, and its worst case is one an agent can read and undo.

| Case | Worst case | Recovery |
|---|---|---|
| Two windows press Start on a New issue at once; the second read the issue before the first took the lock | A second "Started" journal note | Delete the note in Redmine |
| Two windows press Open to retry a kept handoff at once | One shows "didn't start" with ENOENT, and doesn't open the window | Press Open again |
| An Open from another window rewrites the handoff while this window is starting Claude | That retry is lost silently | Press Open again |
| `pr-body.md` is open in a group to the right and `commit.txt` isn't open | Each save adds another group below with a second copy of the PR body | Close the extra groups |
| `plan.checkouts` names a submodule or a repo with a separate git dir | The worktree is made in the wrong place | Not a setup in use; `git worktree remove` it |
| A subject with no ASCII letters or digits | The branch is named `feat/<n>-` | Rename the branch |
| Each Redmine request reads the API key from the Keychain again, and blockers are fetched with their journals | A Start with several blockers is slower | None needed |
