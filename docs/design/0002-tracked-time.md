# Design 0002: Tracked time

## Contents

- [1. Scope](#scope)
- [2. Config](#config)
- [3. Data model](#data-model)
- [4. The correlation key](#key)
- [5. The `TimeSource` interface](#interface)
- [6. When the snapshot is taken](#lifecycle)
  - [6.1 The post-merge hook](#post-merge)
- [7. Commands](#commands)
- [8. `plan report time`](#report)
- [9. Index and validation](#index)
- [10. How each tracker would implement `entries`](#adapters)
- [11. Testing](#testing)
- [12. Requirements coverage](#requirements-coverage)

**Implements:** [ADR 0006](../adr/0006-tracked-time.md) · **Requirements:**
[`C6`, `C9`](../requirements.md) · **Status:** paused with
[ADR 0006](../adr/0006-tracked-time.md)

<a id="scope"></a>
## 1. Scope

**In:** `hours_per_point` calibration · the `TimeSource` interface · the `actual_time` snapshot in
item files · the snapshot in the write that sets `Done` · the post-merge hook · `plan time` ·
`plan report time` · validation rules · index columns.

**Out:** any specific vendor adapter, and how a networked adapter authenticates. Both are settled
when the first adapter is built. §10 records how each surveyed tracker would map onto the
interface.

<a id="config"></a>
## 2. Config

Two keys join `.tracker/config.yml` ([design 0001, *Config*](0001-phase-1.md#config)), shown here without the rest of the file:

```yaml
hours_per_point: 8.0
time_source:
  kind: timewarrior
```

`hours_per_point` defaults to 8.0, one point to one working day. A project sizing a point as an hour sets 1.0. With no `time_source`, tracked time is off. `time_source.kind` selects the adapter module in `src/ghplan/timekeeping/`.

`hours_per_point` converts an estimate into hours for reporting. It is unrelated to
`points_per_week`: velocity is elapsed calendar time ([`C1`](../requirements.md)), while
`hours_per_point` is effort. A point can be eight hours of effort and still take most of a week to
land.

The value is set by hand. `plan report time` prints the observed hours per point next to it, and
whoever owns the config decides whether to change it.

Any further keys under `time_source` belong to the adapter named by `kind`.

<a id="data-model"></a>
## 3. Data model

```python
# model.py
class ActualTime(BaseModel):
    seconds: int                 # sum of closed entries; running entries excluded
    entries: int                 # how many closed entries were summed
    running: int = 0             # entries still open at fetch time, not counted
    source: str                  # time_source.kind that produced it
    key: str                     # the tag queried: the item ID, e.g. "wa-tunep"
    fetched_at: datetime

class Item(BaseModel):
    ...
    actual_time: ActualTime | None = None   # written by `set --status Done` and `plan time --apply` only
```

In an item's front matter, after every other key ([design 0001, *File format*](0001-phase-1.md#file-format)):

```yaml
---
title: Retry failed webhook deliveries
status: Done
estimate: 2
priority: P0
ordinal: 100
lane: api
milestone: beta
started: 2026-10-10T09:12:00Z
actual_time:
  seconds: 15300
  entries: 7
  running: 0
  source: timewarrior
  key: wa-tunep
  fetched_at: 2026-10-11T18:04:00Z
---
```

15300 seconds is 4.25 h.

Estimated hours are never stored. They are `estimate × hours_per_point`, computed at report time,
so changing the calibration rescales every report without touching an item file.

Nobody types `actual_time`: `set` has no flag for it, and `validate` does not check it against
anything except its own shape.

<a id="key"></a>
## 4. The correlation key

A time entry belongs to an item when it carries the item's ID as a tag
([ADR 0006](../adr/0006-tracked-time.md)). Whoever starts the timer adds the tag. With Timewarrior:

```
timew start wa-tunep
```

A time tracker's tags are global, not per repo. The prefix keeps the tags of two repos apart, so
an ID tags one item wherever the timer runs.

An entry can carry other tags too, and one entry with two item tags counts toward both items. To
split a span between items, split it in the time tracker.

<a id="interface"></a>
## 5. The `TimeSource` interface

```python
# timekeeping/__init__.py
@dataclass(frozen=True)
class WorkKey:
    tag: str                          # the item ID, "wa-tunep"
    branches: tuple[str, ...] = ()    # for adapters that match on git branch: <type>/<id>-<slug>

@dataclass(frozen=True)
class TimeEntry:
    source_id: str          # the vendor's own id, so one entry is never counted twice
    start: datetime         # UTC
    end: datetime | None    # None = still running
    tags: frozenset[str]

class TimeSource(Protocol):
    kind: str
    def entries(self, key: WorkKey) -> list[TimeEntry]: ...

class TimeSourceError(Exception): ...   # unreachable, misconfigured, or bad output

def load(config: TimeSourceConfig) -> TimeSource: ...   # picks the module by `kind`
```

`entries` is the whole contract. It reads and never writes: gh-plan does not start, stop, create
or edit anything in the time tracker. An adapter raises `TimeSourceError` for any failure and never
returns a partial list.

Summing is pure and lives outside the adapters:

```python
# timekeeping/total.py
def snapshot(key: WorkKey, entries: list[TimeEntry], kind: str, now: datetime) -> ActualTime
```

It drops duplicate `source_id`s, sums `end - start` over closed entries, and counts open ones in
`running`.

Adapters, like `gh.py` and `git.py`, are allowed to spawn a process or open a connection. Nothing
else in `timekeeping/` is.

<a id="lifecycle"></a>
## 6. When the snapshot is taken

The trigger is the write that sets `status: Done`: `plan set <id> --status Done`, on the item's
branch before it merges ([architecture §7.3](../architecture.md#finish)). The snapshot is committed
with the work and reaches the default branch in the same squash merge.

| Event | What happens |
|---|---|
| `plan set <id> --status Done` | Writes `status: Done` and `actual_time` in the same file write |
| No `time_source` configured | `set` writes `Done` and no `actual_time` |
| Time tracker fails | `set` still writes `Done`, prints the `TimeSourceError`, and leaves `actual_time` absent. `validate` warns until someone runs `plan time <id> --apply` |
| `Done` typed by hand in an editor | No snapshot. `validate` warns, and the post-merge hook reports it once the merge arrives (§6.1) |
| Item reopened | The snapshot stays: it is what was tracked up to that `Done`. The next `Done` overwrites it, and git keeps the earlier value |
| Time logged after `Done`, such as review fixes | Not counted until `plan time <id> --apply` |

The snapshot is a local file write, so nothing here touches the network except the adapter.

<a id="post-merge"></a>
### 6.1 The post-merge hook

A `Done` written without its snapshot, by hand or while the time tracker was down, reaches the
default branch with the merge. A `post-merge` git hook reports it at the moment the merged work
arrives locally, which is usually `git pull` on the default branch right after the PR merges. The
write happens later on a branch.

```python
#!/usr/bin/env python3
# .git/hooks/post-merge, written by `plan hooks install`
import os
import subprocess
import sys

PLAN = '/Users/example/My Tools/bin/plan'
PLAN_ROOT = '/Users/example/code/web-app'

try:
    subprocess.run(
        [PLAN, "time", "--missing", "--quiet"],
        env={**os.environ, "PLAN_ROOT": PLAN_ROOT},
    )
except OSError as exc:
    print(f"plan: post-merge hook could not run {PLAN}: {exc}", file=sys.stderr)
sys.exit(0)
```

- `--missing` limits `plan time` to `Done` items with no snapshot, and prints the total each would
  get.
- It writes nothing. The hook usually fires on the default branch, and item-file changes go through
  a branch like any other commit. Whoever pulled runs `plan time <id> --apply` on a branch, and the
  snapshot is taken then.
- It never fails the merge. Git ignores a `post-merge` exit status, and the hook exits 0 whatever
  `plan` returns. If the time tracker is unreachable, `plan` prints one line and the item is picked
  up by the next run. If `plan` itself is missing, the hook prints one line saying so.
- `--quiet` prints only what is pending, e.g. `wa-tunep is Done with no snapshot · 4.25 h from 7
  entries`. It prints nothing when nothing is pending.
- It is installed in every repo of the plan. A PR merged in `billing-api` arrives when someone
  pulls `billing-api`. Each hook sets `PLAN_ROOT` to the absolute path of the plan's root, so it
  reports against the whole plan from any of them. A member repo whose `path` is not a git checkout
  on this machine is skipped with a message.
- `plan hooks install` writes this hook, the `post-checkout` hook of
  [design 0001, *Commands*](0001-phase-1.md#commands) and a pre-commit hook that runs
  `plan validate` into each repo of the plan. Each calls `plan` by the absolute path of the
  executable that ran the install, because git hooks often run without the login shell's `PATH`
  (GUI git clients, virtualenvs). After `plan` moves, run the install again.
- The hooks are Python scripts. A path with a space or a shell metacharacter in it is valid,
  and a shell script would split it or run it as syntax. The installer writes `PLAN` and
  `PLAN_ROOT` with `repr()`, so they are Python string literals, and `subprocess.run` gets an
  argument list, so no shell parses them. The shebang is `/usr/bin/env python3` because a shebang
  can't hold a path with spaces either. The hook uses only the standard library, so any `python3`
  on the hook's `PATH` will do.
- The pre-commit hook has the same shape, but exits with `plan validate`'s status so an error
  blocks the commit.
- It refuses to overwrite a hook it did not write, and prints the lines to add instead.

Git runs `post-merge` after a merge or a fast-forward pull, and not after `git pull --rebase`. With
`pull.rebase` set, the hook does not fire, and `plan validate` still warns about each `Done` item
with no snapshot.

<a id="commands"></a>
## 7. Commands

| Command | What it does |
|---|---|
| `plan time <id>` | Prints the stored snapshot next to a live total from the tracker, and the difference. Writes nothing |
| `plan time <id> --apply` | Takes a fresh snapshot and writes it. Works on items not yet `Done` too, for a mid-flight check that gets committed |
| `plan time --missing` | Lists `Done` items with no snapshot and the total each would get. Writes nothing; the post-merge hook runs it (§6.1) |
| `plan report time [--milestone M]` | Estimate against actuals, per item and in total (§8) |
| `plan hooks install` | Writes the `post-checkout`, post-merge and pre-commit hooks into every repo of the plan (§6.1) |

`plan time` and `plan report time` take `--json`, like every other read.

<a id="report"></a>
## 8. `plan report time`

```
$ plan report time --milestone beta
ID         est  est h   actual h   variance
wa-ridal    3    24.0     19.5      -4.5   -19%
wa-mefut    5    40.0     52.0     +12.0   +30%
wa-tunep    2    16.0      4.3     -11.7   -73%
ba-kipes    3    24.0       —          no snapshot
───
            10   80.0     75.8      -4.2    -5%     (3 of 4 items with a snapshot)
hours per point: configured 8.0 · observed 7.6
```

- Only `Done` items with a snapshot count toward the totals and the observed hours per point. An
  item without one is listed and counted in the footnote, so a gap is visible rather than read as
  zero.
- A parent with children has no estimate ([design 0001, *Validation*](0001-phase-1.md#validation)).
  Its row sums its children's estimates and snapshots, plus its own snapshot if time was tagged to
  the parent itself. That row is for display only: totals count each child's estimate and snapshot
  once, from the child's own row, and add only the time tagged to the parent itself.
- Observed hours per point is total actual hours over total estimated points, with the same rule,
  so no estimate or snapshot is counted twice.

<a id="index"></a>
## 9. Index and validation

The `item` table gains `actual_seconds INT`, `actual_entries INT` and `actual_source TEXT`, and
`v_work` carries them through. Rebuilding the index never calls the time tracker; it reads the
snapshots in the files.

Rules added to [design 0001 §9](0001-phase-1.md#validation):

| Rule | Level | Message |
|---|---|---|
| `time_source.kind` names an installed adapter | ERROR | `time_source kind 'x' has no adapter` |
| Done item has a snapshot, when a `time_source` is configured | WARN | `wa-tunep is Done with no tracked time — run plan time wa-tunep --apply` |
| No timer was running at snapshot | WARN | `wa-tunep had 1 timer running at snapshot; that time is not counted` |
| Snapshot has entries | WARN | `wa-tunep is Done with 0 tracked entries tagged wa-tunep` |
| `started` is not later than the first tracked time, when a `time_source` is configured | WARN | `wa-tunep started 2026-10-14T09:00:00Z, after time tracked from 2026-10-13T16:20:00Z` |

A `started` later than the first tracked time means the item was started a second time, after a
lost push race or from another branch, and the first start never reached the default branch
([architecture §7.1](../architecture.md#start)).

<a id="adapters"></a>
## 10. How each tracker would implement `entries`

None of these is built yet. The table records how each surveyed tracker would map onto §5, so the
interface is known to fit more than one.

| Tracker | Query | Matched by | Notes |
|---|---|---|---|
| Timewarrior | `timew export <tag>` over a subprocess. Returns a JSON array of `id`, `start`, `end`, `tags`, `annotation` | tag | Local, no credential. Its documented sample output has a trailing comma, so parse leniently 🔍 |
| Kimai | `GET /api/timesheets?tags[]=<tag>`, paginated with `page` and `size` | tag | Bearer token. Requests fail if none of the tags exist, which the adapter treats as zero entries |
| solidtime | REST, scoped to an organisation | task or tag 🔍 | API token. Endpoint details unverified 🔍 |
| Traggo | GraphQL | key:value tag, e.g. `item:wa-tunep` | Its model is only tagged time spans, with no tasks |
| WakaTime / Wakapi | summaries filtered by `project` and `branches` | `WorkKey.branches` | Automatic: no timer to start. WakaTime records `branch` per heartbeat; Wakapi's branch support is unverified 🔍. A summary has no per-entry ids, so the adapter synthesises one entry per day |
| ActivityWatch | `GET /api/0/buckets/<id>/events` on `localhost:5600` | none | Records applications and windows, not tasks. Not a fit |
| Watson | reads its frames file | tag | No release since 2.1.0 (May 2022) |

<a id="testing"></a>
## 11. Testing

- `snapshot()` is pure. It gets cases for duplicate ids, running entries, an empty list, and one
  entry carrying two item tags.
- `set --status Done` is tested with a fake `TimeSource` covering three cases: it returns entries,
  it returns none, and it raises. The raise case asserts the item still reaches `Done`.
- `plan time --missing` gets a case showing it writes nothing.
- `plan hooks install` is tested in temporary git repos: it writes every hook with an absolute
  path to `plan`, writes the hooks into a sibling member repo with `PLAN_ROOT` set, skips a member
  path that is not a git checkout, and refuses to overwrite a hook it did not write.
- The installed hooks are run against a stub `plan` placed at a path with a space and shell
  metacharacters in it, such as `my tools/$(touch pwned);/plan`. The test asserts that the stub
  ran with the expected arguments and `PLAN_ROOT`, that `pwned` was not created, and that the
  post-merge hook exits 0 when the stub fails or is missing.
- Each adapter, when built, is tested against recorded tracker output committed as a fixture, with
  synthetic tags and times. No network in the suite.

<a id="requirements-coverage"></a>
## 12. Requirements coverage

| # | Requirement | How |
|---|---|---|
| C6 | Planned against actual, per item and per milestone | `plan report time`, in hours, per item and per milestone |
| C9 | Tracked effort per item, calibrated against points | `actual_time` snapshot, `hours_per_point`, observed hours per point in the report |
| G5 | History of tracking data | the snapshot is committed in the item file; `git log -p` shows every re-snapshot |
