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

Only a silent failure needs a fix or an *Open* row. That means one that leaves no trace and loses work, files, commits or the tracker's state. A refusal or an error the user sees is already handled.

Before each push, `/code-review high` runs on the branch's whole diff, review fixes included, and the PR body says it ran.

## Redmine drops a status it won't allow

Redmine answers an issue update with 204 even when it has refused the status in it. It drops `status_id` and keeps the rest of the update, the journal note included (`Issue#safe_attributes=`, `app/models/issue.rb` lines 619–624 in 7.0.2). It drops the status when:

- no workflow transition from the issue's status to the new one exists for any of the user's roles. The administrator takes every role, so only a non-admin key meets this;
- the issue would close while an open issue blocks it or it has an open subtask (`closable?`, lines 1126–1128). On 2026-10-18 this kept #80 open: #50 blocked it;
- the issue is a subtask of a closed parent and the new status is open. Such a subtask takes only a closed status (`reopenable?`, line 1130), so even New → In Progress is dropped.

An issue created with a status its workflow doesn't allow from the start gets the first status it does allow, or the default status (lines 625–626). #84, created as Icebox, came back New.

A 204 doesn't mean the status changed. Every write that sets a status goes through `redmine.update`, which reads the issue back. If the status isn't the one it sent, the write is refused, naming the issue, both statuses and the three rules above. The note is saved by then, so the refusal says so. The read-back writes nothing, never sends the status again, and doesn't run for a write that sets no status. A commit's `fixes #<n>` doesn't go through this filter (`Changeset#fix_issue` uses `assign_attributes`, `app/models/changeset.rb` line 253), so a commit can close a blocked issue that the API can't.

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
| Status to In Progress | Rerun sends it | Skipped when In Progress | Behind the lock | Refused statuses never reach it. A subtask of a closed parent, or a key whose roles lack New → In Progress, gets the journal note without the status, and Start is refused before the handoff. The next Start sends both again and is refused the same way (`start.test.js`, *Start on a subtask of a closed parent*) | — |
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

## Done (PR #114)

Done finishes an issue once its PR has merged. It checks everything before it changes anything: the PR is merged, the worktree holds nothing the PR lacks, `origin` holds the merge, and the base can fast-forward to it. Each later step runs only when it has something left to do, so a Done that stopped part way is finished by the next one, and a Done after success changes nothing. A Done that fails part way says what it changed before the failure. Start and Done take one lock per issue, so Open and Done on the same row exclude each other. In one window, a second click on the same button joins the first, and the other button is refused by name.

| Step | Fails | Twice | At once | Stale | Guards |
|---|---|---|---|---|---|
| Read the issue | Nothing written; rerun | Closed is accepted, though Next up shows no Done on a closed row | — | Any status but In Progress or Closed is refused; an issue with no linked PR is refused, naming Ship | — |
| Read the PR | Nothing written; rerun | — | — | An open PR, or one closed unmerged, is refused with nothing changed | Only the last github.com PR the *Pull requests* field links counts |
| Lock | — | Released after every Done | A second click joins the Done under way; a Start or Open of the same issue, here or in another window, is refused | A dead holder's lock is refused by name, never taken over | Blocks recovery after a crash until the file is deleted |
| Fetch origin | Stops before any change | No-op | git locks the repo | `origin` must be the PR's repo, must still have the base, and `origin/<base>` must hold the merge commit. A local base must be behind it, unless another worktree has it checked out. Otherwise Done stops before any change | A PR head that isn't local is fetched from `refs/pull/<n>/head` before any change; if that fetch fails, Done stops, rather than reading the head as missing |
| Bring the base up | The next Done fast-forwards it again | A no-op | git locks the repo | The main checkout switches to the base only from the issue's branch, and changes on the base refuse Done before the worktree goes, as does the issue's branch in the main checkout while another worktree has the base. On any other branch only the base ref moves, and the note says so. A base checked out in another worktree is left for Peter to pull there, and Redmine sees the merge once he does. A listed base worktree whose folder is gone is refused, naming `git worktree prune`. The base moves only from the tip that was checked | Fast-forward only, checked before any change. A file where the merge adds a tracked one or a folder on its path, a rename's new path included, refuses Done before the switch, since git would overwrite an ignored one unasked. From the issue's branch the base ref moves first, so the switch goes straight to the merge and never writes the old base's files |
| Remove the worktree | The next Done removes it, or drops git's entry for a folder already gone. Start's setup marker goes with it | Skipped once gone | Behind the lock | The worktree on the PR's head branch, whatever its name, when the PR is from this repo and the worktree isn't the main checkout; else the one Start would find. A locked worktree is refused, naming `git worktree unlock`. Checked before any change: changes, commits the PR's merged head lacks, or an ignored file outside `plan.disposable` refuse it; a HEAD behind that head, after a commit made on GitHub, is accepted | git refuses untracked files, but removes ignored ones unasked. Only those `plan.disposable` names go with the worktree: `.tmp/` and what a tool makes again, such as `node_modules/` or `__pycache__/`. Done lists any other, such as a `.env`, and stops. The base is brought up first, so git refuses an untracked file in its way before the worktree goes |
| Delete the branch | The next Done deletes it | Skipped once gone | Behind the lock | Every branch named for the issue, and the found worktree's branch, so a Done that stopped after the worktree went still finds the worktree's own when it is named for the issue; a branch another worktree has checked out is kept and named, with `git worktree prune` when its folder is gone, and the base is never deleted. Deleted only when the tip holds nothing the PR's merged head lacks, and only from that tip, so a branch that moved while Done ran stops Done and is kept; any other tip is kept and named | — |
| Fetch the commits | Noted, not thrown; the close goes through the API instead | Fetches nothing new | Redmine handles each fetch | — | Needs the web service's key in the Keychain; without it, Done closes the issue through the API, and the commits wait for the Repository tab |
| Close and note | The next Done sends what's missing | Skipped when the issue is closed and a note holds the merge commit's URL | Behind the lock | Re-read after the fetch, which may have closed it. `redmine.update` reads the close back: a close Redmine dropped is refused, naming blockers and subtasks, and the refusal says the note was saved | — |

### Open

Found by the last local review and left unfixed: each fails silently, and its worst case is one an agent can read and undo.

| Case | Worst case | Recovery |
|---|---|---|
| Another issue's `fixes #<n>` commit is fetched by Done | That issue closes, and its row leaves Next up before its own Done | Run `pr done` in that issue's worktree |
| GitHub keeps the PR's branch after the merge (gh-plan deletes it: `delete_branch_on_merge` is on) | `origin/<branch>` survives, and a Start of the reopened issue checks out the merged branch | `git push origin --delete <branch>` |
| Go runs `pr` in the worktree while Done removes it; Ship and Go take neither the lock nor the in-window guard | `pr` fails part way, or pushes a branch Done just deleted | Don't press Done until Go's terminal has finished; delete a re-pushed branch on GitHub |
