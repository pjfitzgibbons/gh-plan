---
name: plan-report
description: Stand-ins for gh-plan's `plan report` commands until they ship, for any repo tt tracks. "plan report status" shows what closed on the last few active days, work in progress and the next tasks. "plan report time" compares estimates against tracked time (tt / Timewarrior) and calls out timeslices to correct. Use when Peter asks for either, for a status or standup summary, estimate accuracy, hours per point, or whether time tracking is right.
---

# plan report

Both scripts read GitHub, git and `timew export`, and write nothing. Run them from inside the repo. Issues are Redmine's now ([ADR 0011](../../../docs/adr/0011-redmine-is-the-tracker.md)), so until the scripts read Redmine they report only on the GitHub issues closed before it.

## plan report status

```
python3 ~/.claude/skills/plan-report/scripts/status.py                  # last 3 active days, next 5
python3 ~/.claude/skills/plan-report/scripts/status.py --days 5 --next 8 --json
```

- **Done:** the last N *active days*, oldest first. An active day has tracked time for the repo or an issue closed, so weekends and gaps are skipped, not shown empty. Each day lists its closed issues with points and tracked hours. Issues closed as not planned are left out.
- **Next:** work in progress first, then the ready queue, up to N in all.
  - WIP is an open issue assigned to you, with a local branch named for it (`feat/10-x`), or with a running timer tagged with it. The current branch comes first. Picking up an issue creates its branch and assigns it (gh-issues *Picking up an issue*), so a picked-up issue is WIP before any code.
  - The ready queue is open issues with a milestone (not Icebox) and no open blocker, ordered by milestone (`phase-0`, `phase-1`, …) and then backlog ID. This stands in for `plan next` (gh-plan #16): there is no priority or ordinal yet, so backlog ID is the order.

Show the output as is, then add only what the data can't say.

## plan report time

Stands in for gh-plan's `plan report time` (design 0002 §8, issue #65) until that ships. Works in any repo that tt tracks: intervals tagged `owner/repo#N` and `repo:owner/repo`, with N taken from the branch name.

```
python3 ~/.claude/skills/plan-report/scripts/time_report.py                       # closed today, local time
python3 ~/.claude/skills/plan-report/scripts/time_report.py --since 2026-10-01 --until 2026-10-12
python3 ~/.claude/skills/plan-report/scripts/time_report.py --hours-per-point 1.0 --json
```

It reads GitHub and `timew export` and writes nothing. Exit 1 means it found timeslices to check.

### The report

One row per issue closed in the period: the estimate from the `[N]` title prefix, estimated hours (`estimate × hours per point`), tracked hours from closed intervals, and the variance. Rules from design 0002:

- A running interval isn't counted; the row says "no snapshot (1 entry still running)".
- Only rows with an estimate and tracked time count toward the totals and observed hours per point; the footnote says how many.
- Issues closed as not planned are left out, and a last line says how many.
- A variance beyond ±50% is marked `← check tagging`: at that size the tagging is the likelier error.

Hours per point comes from `--hours-per-point`, or else the design default of 8.0. gh-plan uses 1.0 (a point is an hour), so pass `--hours-per-point 1.0` there.

### Timeslices to correct

**Always show these, and offer the corrections.** Bad slices make every number above misleading, so the call-outs come before any reading of the variances. The checks cover intervals overlapping the period:

| Call-out | Usual cause | Fix |
|---|---|---|
| no issue tag | Work on `main`, or before the issue's branch existed | `timew tag @ID owner/repo#N`; the script guesses the next issue worked |
| still running, tagged a closed issue | Branch still checked out after merge | `timew stop`, then switch to `main` |
| tagged #N but starts after #N closed | Same: post-merge time on the old branch | retag to the issue actually worked, or `timew untag @ID owner/repo#N` |
| flapping between #A ↔ #B | two checkouts of the repo sending signals at once, e.g. a worktree on one branch and the main checkout on another | close or leave the stray checkout; retag the slices |
| carries several issue tags | one interval counted toward each | `timew split @ID`, then retag |
| runs over 4 h | a timer left on | `timew shorten @ID …` or split |
| closed with no tagged time | worked untagged, or on another machine | retag slices, or accept the gap |

**`--assign` and `--from` are off until the scripts read Redmine:** Redmine and GitHub share issue numbers, so a slice tagged with a Redmine issue can read as one tagged after a GitHub issue closed, and retagging it would lose a correct tag. The script refuses them; show the call-outs without corrections. For the same reason, "tagged #N but starts after #N closed" compares a Redmine tag with a GitHub issue's close; don't offer to untag or retag those slices.

Present the call-outs, then the corrections, and say plainly that the guesses are guesses. When Peter says which issue the slices belong to, rerun with `--assign N`: it retags untagged slices, and slices tagged an issue after that issue closed, to #N. `--from M` (repeatable) also moves every finished slice tagged #M in the period to #N. It covers the whole day, so remove any slice of genuine #M work from the script before handing it over. `--assign` to the issue a slice already carries accepts it as it is and writes no fix.

Never hand over loose `timew` commands. Write them with `--fix-script .tmp/timew-fix-<date>.sh`: tags are single-quoted (an unquoted `#` is a glob in zsh with `extendedglob`), and the script first checks that every @id still starts where it did, because Timewarrior @ids count back from the newest interval and shift as new ones arrive. **Peter runs the script.**

### Not covered yet

The root cause of most untagged time is work before a branch exists. That's a known gap in tt with no ticket yet; mention it when untagged slices dominate.
