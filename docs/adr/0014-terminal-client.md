# ADR 0014: The terminal is GH Plan's client, over one Python core

## Contents

- [1. Status](#status)
- [2. Context](#context)
- [3. Decision](#decision)
- [4. Alternatives considered](#alternatives)
- [5. Consequences](#consequences)

<a id="status"></a>
## 1. Status

Proposed. Changes [architecture §6.3](../architecture.md#platform) and, through #185, invariant 6
in [architecture §10](../architecture.md#invariants). Replaces part of
[design 0007](../design/0007-vscode-frames.md). The report server is designed in design 0008 (#187).

<a id="context"></a>
## 2. Context

The VS Code extension is GH Plan's one client ([architecture §6.1](../architecture.md#front-ends)):
Next up, Start, Open, Go, Ship and Done run only inside it, and design 0007's frames build on it.
An issue's work has moved to tmux, Neovim and a browser. `plan-session` opens each worktree's tmux
session, and from there nothing can reach those commands.

The logic is split across two languages. `vscode/` holds about 2,100 lines of JavaScript. The
gh-issues scripts beside it, `pr`, `findings.py`, `review.py` and the rest, hold about 4,200 lines
of Python and shell, and `plan-session` is Python too. Lookups are written in both: an issue's
worktree is found by `existingWorktree` in `vscode/start.js` and again by `issue_worktrees` in
`tmux/plan-session`, and Redmine has a client in `vscode/redmine.js` and another in the skill's
`redmine.py`.

Each round's report, `.tmp/<n>/report.html`, is a one-off: Claude writes the page, or a script for
that round, and nothing renders it from the round's data. It carries no buttons. Go and Ship are in
the extension's `Review #<n>` tab, so a report opened in a browser can be read and not acted on.

A person presses every button ([ADR 0011, *Amendment: the flow*](0011-redmine-is-the-tracker.md#flow)),
and requirement [`G3`](../requirements.md) keeps the keys in the Keychain and `gh` the only way to
GitHub. Neither changes here.

<a id="decision"></a>
## 3. Decision

- **`plan` is the core every client calls.** Its subcommands hold the steps of Next up, Start,
  Open, Go, Ship and Done, and their checks and refusals. A client shows the results and
  offers the buttons.
- **The core is one Python package, run with `uv`.** It shares nothing with the package
  [ADR 0004](0004-python-no-docker.md) described: Redmine stays the tracker, and the package holds
  no tracker files.
- **One Flask server per machine serves the round report from the core,** rendering it with Jinja,
  and the report's buttons post to it. Each post is a person's press, and the server writes to git,
  GitHub or Redmine on nothing else.
- **The terminal is the client the tool is built for:** tmux with `plan-session`, `pending` (#178)
  and `plan`'s views, `plan switch` (#141) and `plan worktrees` (#167).
- **The extension stays a client of the same core,** running `plan … --json`, until a later issue
  retires it.

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
- **A static report with the extension's buttons.** No listening process, which is what design
  0006 prefers. Go and Ship then exist only in VS Code, and the report stays a page written by
  hand each round.
- **Stdlib `http.server` in place of Flask.** No dependency. Routing, form parsing and the checks
  in §5 would be written by hand, it has no test client, and its
  [documentation](https://docs.python.org/3/library/http.server.html) warns that it implements only
  basic security checks. The report would still need a template engine.
- **FastAPI, which #25 named.** Its strengths are async handlers and typed JSON APIs. This server
  renders HTML for one person pressing one button at a time, and Ship's `pr` is a blocking
  subprocess. It would add Starlette, Pydantic and an ASGI server that this work has no use for.

<a id="consequences"></a>
## 5. Consequences

- The server binds 127.0.0.1 only. It is a listening process that can write to GitHub and Redmine,
  and any page open in the browser can send it requests. So it needs a Host check against DNS
  rebinding, and an Origin check and a per-launch token before it takes a POST. How each works is
  design 0008's.
- The extension keeps working, running `plan … --json`, until an issue retires it. #185 and #186
  port its logic a command at a time, and its commands, settings and keybindings stay as they are.
  Until each port lands, the JavaScript stays the running code.
- Flask and Jinja are new dependencies, installed through `uv`. They are the project's first
  runtime dependencies: `pyproject.toml` holds only a test environment until #185 adds the package.
  The core needs the Python 3.14 the project already requires, so `plan-session`, which runs on 3.10
  or newer, moves up once it imports the core (#186).
- Invariant 6 changes: the named modules become the Python core's, plus the extension's one module
  that runs `plan`, `vscode/plan.js`. The rewording is #185's. Invariant 2 holds, because the
  server's writes are presses and code still reaches the default branch only by PR.
- Design 0007's frames, window switching (§3.6) and sidebar Worktrees view (§3.8) give way to the
  terminal forms in #141 and #167. Its session state (§3.7) stands, and those views read it.
- Design 0006 needs changing on its own branch: its §3.2 builds `plan` in Node, where this ADR makes
  it Python, and its §4 rejects Review.html with live buttons, which this ADR accepts. Its mod, if
  built, calls this core.
- Architecture §6.3's *Editor integration* row names the terminal client and the report server.
  §6.1 and §6.2 follow as #185 and #186 move the code.
- The `node --test` suites of the ported logic become the core's tests under `uv run pytest`, and
  the extension's are left testing `vscode/plan.js` and VS Code's calls, against a stubbed `plan`.
