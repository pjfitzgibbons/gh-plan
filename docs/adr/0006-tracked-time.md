# ADR 0006: Tracked time comes from an external time tracker, snapshotted into the item file

<a id="status"></a>
## 1. Status

Paused: [ADR 0011](0011-redmine-is-the-tracker.md) moves the item files this ADR snapshots time
into out of the repo. A later design will take tracked time from Redmine.

<a id="context"></a>
## 2. Context

Estimates are points ([`B1`](../requirements.md)), and planned-versus-actual variance is wanted per
item and per milestone ([`C6`](../requirements.md)). Points say how big an item is, but not what it cost.
Tracking and reporting on cost needs the time spent on each item.

Open-source and commercial time trackers all record the same thing: spans of time carrying tags
or a project.

The time tracker is not git. Its data lives outside the committed files that this tool treats as
authoritative ([ADR 0001](0001-git-is-the-database.md),
[ADR 0008](0008-file-native-items.md)). Reading it means a third kind of I/O, alongside the
`gh` and `git` subprocesses.

<a id="decision"></a>
## 3. Decision

Estimates stay in points, and hours come from a calibration. `config.yml` carries
`hours_per_point`, which defaults to 8 (one point is one working day). A project that sizes a point
as an hour sets it to 1. An item's estimated hours are `estimate × hours_per_point`. No item has an
hours estimate of its own.

Actual time is read from an external time tracker through one vendor-neutral interface. Each
vendor gets one adapter module under `src/ghplan/timekeeping/`. Core code sees only the interface,
which returns the tracker's time entries for a work item. A tracker is optional, and with none
configured the feature is off.

`gh.py`, `git.py` and the `timekeeping/` adapters are the only modules that spawn a process or
open a network connection. Everything else stays pure.

Time entries are tied to an item by a tag, the item's ID (`pl-bakor`), set by whoever starts the
timer. That is the convention for trackers that support free-form tags. An adapter for a tracker
without them correlates through another `WorkKey` field instead, such as the git branch for
WakaTime ([design 0002](../design/0002-tracked-time.md)).

The total is snapshotted into the item file by the write that sets `status: Done`, on the item's
branch before it merges. That write records the total tracked time and where it came from. Reports read the snapshot. They never query the tracker live.

How a networked adapter authenticates is decided when the first networked adapter is built.

Design detail is in [design 0002](../design/0002-tracked-time.md).

<a id="alternatives"></a>
## 4. Alternatives considered

- An hours estimate on each item, alongside points. It is a second estimate that drifts from the
  first, and nothing reconciles them.
- Building timers into gh-plan. That makes the tool a time tracker, which is a product of its own,
  and existing ones already do the job. `plan start` and `plan stop` record when work starts, not
  how long it takes.
- Calling one vendor directly with no interface. It ties item data and reports to that vendor, and
  changing trackers then means changing core code.
- Querying the tracker live at report time. Reports then depend on a system outside git, fail
  offline, and change when someone edits an old entry. The figure is not reproducible from the repo.
- Copying every time entry into the item file. It turns each file into a log and churns its
  history. The total, the entry count and the query that produced them are enough to reproduce or
  audit the number.
- Recording time on GitHub. It adds a write to GitHub, and the tool's only remote write is a
  `git push` of its own commits ([ADR 0008](0008-file-native-items.md)).
- Tying entries to an item through a task or activity entity in the tracker. It needs gh-plan to
  create those entities, which is a write to another system, and not every tracker has them.
- Tying entries to an item by git branch. Only automatic trackers (WakaTime, Wakapi) record the
  branch, so it cannot be the common key. An adapter for one of those can still match on branch.

<a id="consequences"></a>
## 5. Consequences

- `C6` gets an hours view: estimated hours against tracked hours per item and per milestone, and
  the observed hours per point against the configured `hours_per_point`.
- `actual_time` joins the item's fields. It is written by `plan set --status Done` and by
  `plan time`, and never typed by hand.
- The figure is evidence with provenance: tracker, tag, entry count and fetch time sit next to the
  total, and `git log -p` shows when it changed ([`G5`](../requirements.md)).
- A tracker that is unreachable when an item is set `Done` does not block the write. The item goes
  to `Done` without a snapshot, `validate` warns, and `plan time <id> --apply` fills it in later.
- Time logged after `Done`, such as review fixes, is not counted until someone re-snapshots the
  item.
- The process rule in [ADR 0004](0004-python-no-docker.md) and the
  [design 0001 layout](../design/0001-phase-1.md#layout) widens to include `timekeeping/`. Adapters
  are tested against recorded tracker output, the same way `gh.py` is.
