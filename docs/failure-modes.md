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

A review fix re-reads the whole table. Two of PR #110's findings came from fixes that broke another row.

Before each push, `/code-review high` runs on the branch's whole diff, review fixes included, and the PR body says it ran.

## Start (PR #110)

Within a window, a second click on an issue joins the Start under way. Across windows, a lock file in the repo's git dir, holding the extension host's pid, refuses the second Start before it changes anything. A lock whose process is gone is taken over, and the refusal names the file, so one left behind can be deleted by hand.

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| Read the issue | Nothing written; rerun | — | — | Any status but New or In Progress is refused | — |
| Read the blockers | A blocker Redmine won't show is listed as unknown | — | — | — | — |
| Lock | — | Released after every Start | The second window is refused | A dead holder's lock is taken over | — |
| Fetch origin | Stops before any branch | No-op | git locks the repo | `--prune` drops a deleted `develop`; the base is `develop`, else origin's default, else `main` | — |
| Setup marker | Written before the worktree, removed if `git worktree add` fails and after the last setup step, so a failure in between reruns setup | Absent once set up, so Open changes nothing | — | A worktree made by hand or before the marker counts as set up | — |
| Branch and worktree | Rerun reuses the branch left behind, on its own base | Found by number, reused | Behind the lock | A listed worktree deleted by hand is refused, naming `git worktree prune`; the issue's branch in the main checkout is refused; a worktree mid-rebase is found by its folder | Only `feat/`, `fix/`, `chore/` or no prefix names an issue's branch |
| Pushed branch | — | — | — | An issue branch on `origin` is checked out, tracking it, before a new one is made | — |
| `uv sync` | Reruns while the marker exists | Skipped once set up | Behind the lock | — | — |
| Status to In Progress | Rerun sends it | Skipped when In Progress | Behind the lock | Refused statuses never reach it | — |
| Handoff to Claude | Kept until Claude starts; reload or Open retries | Written only by the Start that set up, or to retry one kept | One file per worktree, written aside and renamed in; an open window watches for its own | — | Expires after a day, so a forgotten one can't start Claude unasked |
