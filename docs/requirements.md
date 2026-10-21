# Requirements

## Contents

- [1. Priorities](#priorities)
- [2. Structure (A)](#structure)
- [3. Estimation (B)](#estimation)
- [4. Scheduling (C)](#scheduling)
- [5. Parallelism (D)](#parallelism)
- [6. Dependencies and ordering (E)](#dependencies)
- [7. States (F)](#states)
- [8. Evidence and provenance (G)](#evidence)
- [9. Automation (H)](#automation)
- [10. Cost and licence (I)](#cost)

These are the requirements gh-plan meets. The [alternatives survey](alternatives-survey.md) scored
existing tools against the same list.

They are sized for one person planning work across a few repos, a few hours at a time and not
every day. They are not sized for a team.

<a id="priorities"></a>
## 1. Priorities

`MUST`: a tool failing this is disqualified · `SHOULD`: failing this is a real cost, recorded and
traded · `COULD`: nice, and no tool gets credit for it over a MUST.

<a id="structure"></a>
## 2. Structure (A)

| # | Priority | Requirement | Why |
|---|---|---|---|
| **A1** | MUST | **One tracker spanning several repositories**, with a single portfolio view across all of them | A project's work often crosses repos, for example an app, an API and its docs |
| **A2** | MUST | **Milestones that span repositories** | Milestones are the organising unit, and most of them touch several repos. GitHub milestones are per-repo, which forces either a central-issues compromise or hand-synced duplicates |
| **A3** | MUST | **Milestone carries an exit criterion**, not just a due date | A milestone closes on its checkpoint, which is sometimes stricter than "all issues closed" |
| **A4** | MUST | **Sub-tasks are first-class and schedulable**: own estimate, own dates, appear in every view | The >5-points split rule produces them constantly. A checkbox list is not sufficient: it cannot be scheduled or counted |
| **A5** | SHOULD | **Parent roll-up** that does not double-count a parent carrying children | |
| **A6** | COULD | Nesting deeper than two levels | Two levels is usually enough, so deeper nesting is a COULD |

<a id="estimation"></a>
## 3. Estimation (B)

| # | Priority | Requirement | Why |
|---|---|---|---|
| **B1** | MUST | **Numeric estimate per item**, Fibonacci 1 / 2 / 3 / 5 / 8 / 13 | |
| **B2** | MUST | **Estimate visible in list views without opening the item**, in one canonical place | |
| **B3** | SHOULD | **Items above 5 points are split before they are scheduled**, as a practice, and a reality check lists any scheduled item above 5 | 8 and 13 mean "split this", not a size. Nothing refuses the save: the check makes a lapse visible |
| **B4** | SHOULD | Points queryable in bulk for roll-ups and totals | |

<a id="scheduling"></a>
## 4. Scheduling (C)

| # | Priority | Requirement | Why |
|---|---|---|---|
| **C1** | MUST | **Velocity expressed per week, not per day** | Elapsed calendar time, absorbing the weeks the project is untouched. A daily rate needs a fictional working calendar to correct it |
| **C2** | MUST | **Duration derived from estimate × velocity**, not typed per item | |
| **C3** | MUST | **Baseline start and finish, frozen** and never silently recomputed | A baseline that moves when the estimate changes is not a baseline |
| **C4** | MUST | **Re-baselining is deliberate and leaves a record** of when and why | |
| **C5** | MUST | **Actual start and finish derived from evidence**, not typed: the tool's start commit on the default branch, and the commit on the default branch that makes the item Done | A commit timestamp is evidence in git history; a typed date is an assertion |
| **C6** | SHOULD | Planned-versus-actual variance visible per item and per milestone | |
| **C7** | COULD | **Gantt rendering** | Deferred: it answers "when will this be done", which for work done a few hours at a time is a number nobody should trust. Wanted eventually |
| **C8** | SHOULD | No working-calendar/weekend logic that would double-count idleness already inside the weekly velocity | |
| **C9** | SHOULD | **Tracked time per item**, read from a time tracker and compared with the estimate through a configured hours-per-point | Points size an item; tracked time shows what it cost, and calibrates what a point is worth in each project |

<a id="parallelism"></a>
## 5. Parallelism (D)

| # | Priority | Requirement | Why |
|---|---|---|---|
| **D1** | MUST | **A "lane" dimension: a contention domain.** Items in a lane touch the same files and run one at a time; different lanes run in parallel | This, not the dependency graph, is what limits concurrency. Two sessions editing `.claude/settings.json` collide regardless of what any graph says |
| **D2** | MUST | **Board/kanban grouping by that dimension** | |
| **D3** | MUST | **"What can I start right now" answerable directly**: the head of each lane with dependencies met | The daily question, and the one a Gantt never answers |
| **D4** | MUST | **Single-valued lane membership** | An item in two contention domains breaks the invariant. A multi-value label cannot enforce this |
| **D5** | SHOULD | Multiple concurrent sessions / worktrees supported without the tracker fighting it | |
| **D6** | SHOULD | **`.tracker/ready.yml` is the committed answer to `plan ready`.** It lists the items `plan ready` reports (`D3`), at most 20, with what `ready` reports for each (ID, title, estimate, duration, lane, milestone, priority, focus, and what it unblocks), and when it was generated. Its as-of commit is the last commit that wrote it, which git and GitHub's file view both show; the file cannot name the commit that contains it. A second section lists the items in flight: each branch on `origin` that names an item, with the item's ID and title, the branch, its `started`, and the date of the branch's last commit | A viewer reads it on GitHub or in an editor with no tool installed. Only pushed branches are listed, so the file is the same whichever machine generates it |
| **D7** | SHOULD | **One view of every item in progress across repos**: its branch, its worktree, and the actions waiting on it | Several items run at once in several repos, and the next step is lost switching windows |

<a id="dependencies"></a>
## 6. Dependencies and ordering (E)

| # | Priority | Requirement | Why |
|---|---|---|---|
| **E1** | MUST | **First-class blocks / blocked-by relationships** | Dependencies drive ordering, readiness and baseline dates. They live in the item file as `blocked_by`, and can name an item in another repo of the same plan (see [ADR 0008](adr/0008-file-native-items.md)) |
| **E2** | MUST | **Cycle detection**, or data good enough to detect cycles reliably in a script | A cycle makes the topological sort undefined, and the sort drives both ordering and baseline dates |
| **E3** | MUST | **Priority that lets newly filed urgent work outrank everything older** | Issue-number ordering sorts a new P0 last, which is backwards |
| **E4** | SHOULD | Dependencies are *intra-milestone only* by convention; cross-milestone sequence comes from milestone order | Recording what milestone order already implies produces a dense, cycle-prone, useless edge list |
| **E5** | COULD | Readable total ordering (rank) | A bare rank was rejected: an opaque integer carries no intent where a priority band does. `ordinal`, a tiebreak inside a priority band, is adopted because ordering tasks across repos is still needed |

<a id="states"></a>
## 7. States (F)

| # | Priority | Requirement | Why |
|---|---|---|---|
| **F1** | MUST | **A deferred state that is visible and counted but excluded from the schedule**: the icebox | Deferred work and accidentally-dropped work must not look the same, or you cannot tell a decision from a mistake |
| **F2** | MUST | **Workflow states orderable**, because order reads as progression in any board UI | `Icebox → Todo → In Progress → Blocked → Done` |
| **F4** | MUST | **An orthogonal taxonomy alongside state**, such as `repo:*`, `kind:*` and `by-design` labels | A label can be load-bearing. `by-design` marks a weakness recorded deliberately, closed by documenting it and never by fixing it |

<a id="evidence"></a>
## 8. Evidence and provenance (G)

| # | Priority | Requirement | Why |
|---|---|---|---|
| **G1** | MUST | **Tracking data is reviewable evidence**: every change is recorded with who made it and when, diffable, and readable by someone with no account | Planning data is a record of what was planned and when, and others need to review it. A row in someone's hosted database is not evidence |
| **G2** | MUST | **Full export in a documented, stable format.** No lock-in | |
| **G3** | MUST | **No credential in a repo.** No PAT, API token or app secret in a committed file. A CI secret is allowed only for a token listed in [architecture §6.2](architecture.md#process-boundary), with what it reads, what it writes and where it lives | A hard rule forbids secrets in any repo. A listed token's scope bounds what a leak exposes |
| **G4** | SHOULD | Low operational burden: no other system that itself needs patching, backing up and validating | A self-hosted tracker is one more system to secure and maintain |
| **G5** | SHOULD | Change history on tracking data: who changed an estimate or a baseline, and when | |
| **G6** | SHOULD | **Tracking files are editable by hand**, in a text editor, with no tool installed | A person fixes or reviews a value where it lives. The tool is a convenience over the files, not the only way to change them |
| **G7** | SHOULD | **An item's front matter shows the data behind `plan why`** without a tool. `blocked_by` is a list of `"<id> - <title>"` strings | The ID is authoritative. The tool rewrites the title part on every write |

<a id="automation"></a>
## 9. Automation (H)

| # | Priority | Requirement | Why |
|---|---|---|---|
| **H1** | MUST | **Scriptable from the command line** for lint and generated views | |
| **H5** | SHOULD | Automatic membership: new items join the tracked set without a manual step | An item file in a repo's `.tracker/items/` or `.tracker/archive/` is in the plan. A Done item lives in `archive/`; a reopened one moves back to `items/`. There is no second list to add it to |
| **H6** | SHOULD | Validation hooks, so a rule is enforced by a pre-commit hook or a CI check before a change lands, rather than by a lint that runs later | A lint detects; it does not prevent |

<a id="cost"></a>
## 10. Cost and licence (I)

| # | Priority | Requirement | Why |
|---|---|---|---|
| **I1** | MUST | **Free, or very cheap** for a single user | |
| **I2** | SHOULD | **Open source**, preferably with a copyleft or permissive licence | Anyone should be able to reproduce the setup |
| **I3** | MUST | **No feature we depend on sits behind a paid tier** | OpenProject's baselines and Plane's GitHub integration both failed here |
| **I4** | SHOULD | Actively maintained: commits within the last quarter, a release within the year | |
| **I5** | COULD | Integrates with GitHub for PR/commit linking | Useful, not essential; **C5** needs commit data from *somewhere*, but a local git read satisfies it |
