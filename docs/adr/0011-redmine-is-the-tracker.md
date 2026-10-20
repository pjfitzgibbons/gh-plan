# ADR 0011: Redmine is the tracker, outside the repos it plans

## Contents

- [1. Status](#status)
- [2. Context](#context)
- [3. Decision](#decision)
- [4. Alternatives considered](#alternatives)
- [5. Consequences](#consequences)
- [6. Amendment: the flow](#flow)

<a id="status"></a>
## 1. Status

Proposed. Supersedes [ADR 0001](0001-git-is-the-database.md),
[ADR 0002](0002-tracker-per-repo.md), [ADR 0007](0007-tracker-files-are-yaml.md) and
[ADR 0008](0008-file-native-items.md). Pauses [ADR 0010](0010-plan-state-on-a-branch.md), and
amends [ADR 0004](0004-python-no-docker.md).

<a id="context"></a>
## 2. Context

Under [ADR 0008](0008-file-native-items.md) the tracker is Markdown files committed in the repo
each item belongs to. Keeping planning state in the same history as the code costs something on
every item:

- Each `plan: start` is a direct commit to the default branch, the one exception to "everything
  reaches it by pull request". On gh-plan it puts every open pull request one commit behind, and
  each then needs a rebase unrelated to its own change.
- Every branch regenerates `ready.yml`, so two branches in flight conflict on it.
- The tool has to own everything a tracker gives for free: titles, bodies, parents, dependencies,
  cycle checks, a web page per item, a board and a Gantt.

[ADR 0010](0010-plan-state-on-a-branch.md) moves the files onto a `gh-plan` branch to remove the
first two costs. It keeps the third, and adds a worktree, a symlink in every item worktree, and a
history migration.

All three costs come from keeping the tracker inside the repo it tracks. The
[alternatives survey](../alternatives-survey.md) was re-run on 2026-10-16 for trackers outside the
repo, asking for each one how easily the missing requirements could be added. Redmine came first,
and a hands-on test of Redmine 7.0.2 on the same day met every requirement it covered, with core
features and a plugin of 65 lines
([survey, *Redmine 7*](../alternatives-survey.md#redmine-7)).

<a id="decision"></a>
## 3. Decision

Redmine 7 is the tracker. It runs outside the repos it plans, and nothing tracker-related is
committed in them.

- One Redmine, with one project per repo under a parent project for the plan. Milestones are
  versions in the parent project, shared with its tree, so one version spans every repo
  ([`A1`, `A2`](../requirements.md)).
- Items are Redmine issues, with sub-tasks for children and the `blocks` relation for
  dependencies. Redmine refuses a relation that would close a cycle, across projects
  ([`A4`, `E1`, `E2`](../requirements.md)).
- Fields Redmine lacks are custom fields: `Points` (a list of 1, 2, 3, 5, 8, 13), `Lane` (a
  single-valued list), and `Baseline start` and `Baseline finish` (dates). Workflow makes the
  baseline fields read-only for every role but the administrator, and the journal keeps each
  re-baseline with its note ([`B1`, `C3`, `C4`, `D1`, `D4`](../requirements.md)).
- Each repo is registered as a Redmine repository of its project, with `refs` and `fixes`
  as commit keywords. A commit naming `#<n>` is linked to that issue, and `fixes #<n>` closes it,
  so actual start and finish come from commits ([`C5`](../requirements.md)).
- Redmine runs in Docker on the Mac, on SQLite in a named volume that is backed up nightly.
- What Redmine does not do is a plugin of ours, in Ruby on Redmine's plugin API: the lane
  board, the ready flag and the reality checks. Scripts outside Redmine use its REST API. The
  plugin, the scripts and the export live in gh-plan, which becomes the Redmine setup.
- Remote writes are a `git push` of an item branch, and Redmine's REST API with the key from the
  Keychain. The VS Code extension's Start is the first writer: it sets an issue In Progress with its start date
  and a journal note naming the branch.
- The estimate rule is a practice ([`B3`](../requirements.md)). An item
  above 5 points is split before it is scheduled. A reality check lists any that are not, and
  nothing refuses the save.

<a id="alternatives"></a>
## 4. Alternatives considered

- Keep the files, on a `gh-plan` branch ([ADR 0010](0010-plan-state-on-a-branch.md)). It
  removes the commits to the default branch and the `ready.yml` conflicts, and leaves the tool
  owning a tracker's worth of features.
- YouTrack, free for up to 10 users. Its Gantt spans projects, its swimlanes follow a custom
  field, and JavaScript workflows can fill the gaps. It is closed source, and no JetBrains page
  confirms that the free tier has every feature 🔍.
- OpenProject Community Edition. Gantt, shared versions, story points and cycle checks are in
  core, but baselines are in the paid Enterprise Basic tier, and no board groups by a custom field.
- Frappe with a task app of our own. The Gantt and kanban are generic, and everything else is
  Python. It is a tracker to write, on MariaDB and Redis.
- Taskwarrior with TaskJuggler for the Gantt and baselines. Both are local, and the pair needs
  glue code and a board of its own. TaskJuggler's last release was in July 2025.
- Write our own tracker outside the repo. Redmine already does most of it.

<a id="consequences"></a>
## 5. Consequences

- The default branch takes only pull requests. `plan:` commits, `ready.yml` and the conflicts on
  it go away, and so does architecture invariant 3, which governs the tool's commits. Invariant 5,
  which allowed no remote write but a `git push`, is retired. Invariant 6 names the modules that
  may write, the extension's Redmine client among them.
- An item is `#<n>`, numbered across every project. Branches become `<type>/<n>-<slug>`. GitHub
  also links `#<n>` in a commit message to the repo's own issue or pull request `<n>`, so a
  reference can point at both 🔍.
- Tracking data lives in Redmine's database, not in git. [`G1`](../requirements.md) is met by a
  scheduled export of every issue and its journal to a file committed in gh-plan.
- [`G6`](../requirements.md), hand-editable files, is lost. The web UI and the REST API replace it.
- Redmine is one more system to run, back up and upgrade ([`G4`](../requirements.md)), and it is
  down while the Mac is off. Running it in Docker amends [ADR 0004](0004-python-no-docker.md) for
  Redmine only; scripts of ours stay Python. The plugin is Ruby.
- The API key is a credential on the machine, kept out of every repo, which still meets
  [`G3`](../requirements.md).
- A commit older than its repository's registration in Redmine is linked to its issue but never
  changes the issue's status (`app/models/changeset.rb`, line 153, in 7.0.2). Importing history
  closes nothing.
- The existing items in each repo's `.tracker/` are imported once into Redmine, keeping each
  body, with the old ID in a field.
- The skills that read items or make branches (`worktask`, `gh-issues`, `post-pr`, the plan-board
  recipes) and tt's branch parsing change to Redmine issues.
- ADR 0010 and the items that carry it out are paused, in the icebox.

<a id="flow"></a>
## 6. Amendment: the flow

The buttons are in the extension's Next up panel, so the Redmine issue page needs no view hook or
`vscode://` link. A New issue's row shows Start. An In Progress one shows Open, and Done as well
once the issue links a PR. Go and Ship are in the title bar of the issue's review files in
`.tmp/<n>/`. Ship runs `pr`, which commits, pushes, and opens or updates the PR. It then posts each
review reply in `.tmp/<n>/replies/` to its thread, resolves the thread, and applies the box ticks
in `.tmp/<n>/issue.md` to the issue's description. After the merge, Done removes the worktree and
the branch, brings the base up, has Redmine fetch the commits, and closes the issue. Every write to
GitHub or Redmine happens when Peter presses a button or runs `pr`.
