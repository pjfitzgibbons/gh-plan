# ADR 0014: `plan` runs the flow, on any tracker, with the terminal as its client

## Contents

- [1. Status](#status)
- [2. Context](#context)
- [3. Decision](#decision)
- [4. Alternatives considered](#alternatives)
- [5. Consequences](#consequences)

<a id="status"></a>
## 1. Status

Proposed. Changes [architecture §6.3](../architecture.md#platform) and, through #185, invariants 2
and 6 in [architecture §10](../architecture.md#invariants). Replaces part of
[design 0007](../design/0007-vscode-frames.md). Once built, it also replaces what
[design 0005](../design/0005-review-findings.md) says about keeping findings in Redmine, and what
[ADR 0011](0011-redmine-is-the-tracker.md) says about bare issue numbers and Go. The report server
is designed in design 0008 (#187).

<a id="context"></a>
## 2. Context

The VS Code extension is GH Plan's one client ([architecture §6.1](../architecture.md#front-ends)):
Next up, Start, Open, Go, Ship and Done run only inside it, and design 0007's frames build on it.
An issue's work has moved to tmux, Neovim and a browser, and VS Code is no longer in use, so each
time the tool launches it the work is interrupted. `plan-session` opens each worktree's tmux session,
and from there nothing can reach those commands.

The logic is split across two languages. `vscode/` holds about 2,100 lines of JavaScript. The
gh-issues scripts beside it, `pr`, `findings.py`, `review.py` and the rest, hold about 4,200 lines
of Python and shell, and `plan-session` is Python too. Lookups are written in both: an issue's
worktree is found by `existingWorktree` in `vscode/start.js` and again by `issue_worktrees` in
`tmux/plan-session`, and Redmine has a client in `vscode/redmine.js` and another in the skill's
`redmine.py`.

Each round's report, `.tmp/<n>/report.html`, is a one-off: Claude writes the page, or a script for
that round, and nothing renders it from the round's data. It carries no buttons. Go and Ship are in
the extension's `Review #<n>` tab, so a report opened in a browser can be read and not acted on.

The flow has to run on two Macs that share one chezmoi install. At home the tracker is Redmine. At
work it is Jira Cloud, with the team on Jira and GitHub, the same PR review process, plain git, and
existing integrations in shell and some Python. Jira and GitHub automation doesn't change an issue's
status there. GitHub and Jira plugins are largely frozen at work, and their APIs and CLIs are open.

Nothing in the code treats Redmine as replaceable. Issues are bare integers, review findings live in
Redmine's tables where a Jira team can't see them, and the extension calls Redmine directly.

The scripts run on whatever `python3` is on PATH, which on this Mac is 3.15, while the project pins
3.14 and `pyproject.toml` lints them for 3.10. That gap has already caused churn and broken work in
progress.

Claude Code reports its sessions in two forms: `claude agents --json` gives background sessions a
`state` field and interactive ones a `status` field.

A person presses every button ([ADR 0011, *Amendment: the flow*](0011-redmine-is-the-tracker.md#flow)),
and requirement [`G3`](../requirements.md) keeps the keys in the Keychain and `gh` the only way to
GitHub. Neither changes here.

<a id="decision"></a>
## 3. Decision

- **`plan` and its helpers are the runtime.** The flow runs from the `plan` command: `next`, `status`,
  `worktrees`, `start`, `open`, `ship` and `close`, with their checks and refusals. A client shows
  the results.
- **Claude Code is a helper agent.** `plan` calls it for reviews and code, and every `plan` command
  that only reads works with no Claude session running. Claude Code hosts no part of the flow, so
  there is no plugin to install at work.
- **The core is one Python package, `plan/`, run with `uv`.** `plan` is linked into `~/.local/bin` as
  the skill scripts are, and any tool or library the core needs comes through `uv`. The package
  shares nothing with the one [ADR 0004](0004-python-no-docker.md) described.
- **Python 3.14 is the one target.** The pin comes first: `plan` and its helpers run on it, and no
  code keeps compatibility with an older version or works toward 3.14 from one. Code that would do
  either stops, and the target is checked first.
- **Policy owns its ports.** `plan/model.py` and `plan/usecases.py` name no tool. Adapters meet the
  ports, and the command line wires them together.
- **The tracker is a port.** `Tracker` covers next up, read an issue, transition, link a PR and add a
  note. Redmine and Jira are equal adapters, and Redmine's is the reference example. The Jira adapter
  wraps the existing integrations and reuses the CLI's web login, as `gh` does, storing no token.
  The machine's chezmoi `machine` value picks the adapter. Automation moves an issue in one place
  only: Redmine closes it when it fetches a merged `fixes` keyword. Every other move is a `plan`
  transition a person presses.
- **Review findings are a separate port, out of Redmine, over a store that can be shared.**
  `ReviewFindings` names no storage, so the store can be a resource others reach, and needn't be
  GitHub or local files. This machine, with one developer for now, uses a local SQLite database. The
  work machine's store is still to choose. Redmine stores no findings.
- **Every issue key is `PREFIX-number`.** `PROJ-185` in Jira. Redmine issues take the same form, from
  a prefix per project: `GH-185`, `PL-7`, `TT-75`, `MS-12`, the extension's project markers in
  capitals. Redmine's number stays unique across its projects, so a key's number finds the issue, and
  a prefix that doesn't match the issue's project is refused. A Redmine plugin teaches Redmine the
  form, so it reads `GH-185` itself and renders it as a link to the issue. New branches are
  `<type>/<PREFIX-number>-<slug>`, and worktree folders and `.tmp/` folders take the key. A bare
  number is read only as a reference, for older branches and folders.
- **A helper's state is one field.** The adapter over `claude agents --json` maps background sessions'
  `state` and interactive sessions' `status` to one `helper_state`, one of `idle`, `working`,
  `needs_input`, `done` and `failed`. Nothing past the adapter sees either raw field.
- **One Flask server per machine serves the reports,** read-only, rendered with Jinja from a round's
  data. launchd starts it at login, on the host. It writes nothing and takes no POST. Next steps are
  `plan` and `pr` lines to copy, and a person runs them in a terminal.
- **Go and Ship are one step.** The step that renders `.tmp/<n>/report.html` also stages the files,
  so `plan` has no `go` and `pr <n>` is the only step left. Staging goes through a manifest of files
  and directories, never `git add -A`: #189's hand-run `pr` put two tmux logs on `main`. Commands are
  printed to copy, and commit and push stay in `pr`.
- **The terminal is the client the tool is built for:** Ghostty with tmux on both Macs, `plan-session`,
  `pending` (#178) and `plan`'s views, `plan switch` (#141) and `plan worktrees` (#167).
- **The extension is off the code path.** It stays in the repo unchanged and unused, and retiring
  it can come later. Nothing in the flow launches VS Code (#188). New client work is frozen: #143,
  #92, #135 and #114 wait.

<a id="alternatives"></a>
## 4. Alternatives considered

- **The extension as it is.** It works, and needs no port. Its commands stay out of reach of a
  terminal, so every tmux feature would need its own copy of them, and the duplication in §2 would
  grow.
- **Claude Desktop's mod (design 0006, not yet merged).** Panes and buttons sit beside the session,
  and no server is needed. It still needs a `plan` command, because a mod has no Node. It ties the
  tool to one app, whose answers design 0006's §9 still lacks, the first being whether anything
  outside Desktop can open a Code-tab session on a given folder. A mod could still be another
  client of this core.
- **A Node core built from `vscode/redmine.js` and `vscode/start.js`,** as design 0006's §3.2 has
  it. That moves the smaller side and keeps the extension's tests, but `pr`'s Python scripts and
  `plan-session` would still have their own lookups, or would be ported in turn. Python is where
  most of the code already is ([ADR 0011](0011-redmine-is-the-tracker.md#consequences) keeps it as
  the language of our scripts).
- **Claude Code as the base, with the flow packaged as a plugin.** Sessions, worktrees and an overview
  come free, and slash commands could front the flow. It makes a helper the runtime, ties the flow to
  one tool's session model, and adds a plugin to install at work, where the CLIs are already open.
- **POST buttons for Go and Ship.** One press instead of a copy and a paste, at the cost of a
  listening process that can write to GitHub and the tracker. A later design could add them.
- **The server in Docker, beside Redmine.** Docker restarts it by itself, but a container can't read
  the Keychain, so `gh`'s login and Redmine's key would have to leave it, and worktrees would have to
  be mounted at their host paths.
- **Findings kept only in local files.** Simple on both machines, and the team can't see them.
- **The GitHub PR as the findings record.** Team-visible with no new store, and it ties findings to
  one forge's comments.
- **Findings behind the tracker port.** One port fewer, and every tracker adapter has to invent
  storage for findings. Jira has no place for them that matches Redmine's tables.
- **Keeping bare integers for Redmine and keys for Jira.** No change to Redmine's side, and every
  branch, folder, message and test then handles two shapes of key.
- **Stdlib `http.server` in place of Flask.** No Flask dependency, and Jinja works without it.
  Routing and the `Host` check in §5 would be written by hand, and it has no test client.
- **FastAPI, which #25 named.** Its strengths are async handlers and typed JSON APIs. This server
  renders HTML for one person. It would add Starlette, Pydantic and an ASGI server that this work has
  no use for.

<a id="consequences"></a>
## 5. Consequences

- The report server binds 127.0.0.1 only and checks `Host` against DNS rebinding. It takes no POST,
  so it needs no Origin check or token. How it works is design 0008's.
- #185 and #186 port the extension's logic to `plan` a command at a time, and the extension isn't
  rewired to call it. Until a command is ported, a person runs the script behind it, such as `pr`.
- The core's dependencies come through `uv`, and Flask and Jinja join them with the report server.
  `pyproject.toml` holds only a test environment until #185 adds the package.
- `plan-session`, the skill scripts and hooks move onto the pin. Their `python3` shebangs, `pr.sh`'s
  3.10 check and ruff's `py310` target go, with no version support left behind.
- `redmine/backup.py` moves onto the pin too, and its `py39` target goes only once it has. launchd
  runs it on `/usr/bin/python3`, 3.9, so the LaunchAgent has to run the pinned Python first, or
  newer syntax stops the nightly backup.
- Once the flow works, the findings in Redmine migrate to the store, kept for analysis only, and
  Redmine's findings tables, its plugin code for them and `findings.py send` retire. Until then `pr`
  keeps sending rounds to Redmine.
- Each issue key changes on the Redmine side: Start's `branchName`, `plan-session`'s lookups, `pr` and
  the `.tmp/<n>/` paths in the skill all move to keys, and `plan` reads bare numbers meanwhile.
  Commit keywords use the key on both trackers, `refs GH-185`, so the plugin reads keys in commits
  as well as rendering them. It changes how Redmine behaves, so it is rechecked on each upgrade.
- The Jira adapter is built on the work machine, from the `Tracker` port and the Redmine adapter.
- `helper_state`'s mapping still needs one answer: what `claude agents --json` means by `blocked`.
- Invariant 6 changes: the named modules become the Python core's. Invariant 2 changes in where
  status lives and what sets it, the tracker and `plan`, and code still reaches the default branch
  only by PR. Both rewordings are #185's.
- #186 has no `plan go`, so its guardrail and L3 are rewritten, and `PR_STAGED` goes. #189's scope is
  settled against this. Architecture §7.2 and its flow diagram, `docs/failure-modes.md`, ADR 0011
  and ADR 0009 still describe Go and change with #192.
- Design 0007's frames, window switching (§3.6) and sidebar Worktrees view (§3.8) give way to the
  terminal forms in #141 and #167. Its session state (§3.7) stands, and those views read it.
- Design 0006 needs changing on its own branch: its §3.2 builds `plan` in Node, where this ADR makes
  it Python. Its mod, if built, calls this core.
- Architecture §6.3's *Editor integration* row names the terminal client, and invariant 2's "Status
  lives in Redmine" becomes the tracker. §6.1 and §6.2 follow as #185 and #186 move the code.
- The ported logic's tests are written for the core under `uv run pytest`. The `node --test` suites
  stay with the extension.
