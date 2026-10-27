# Design 0007: Worktree frames and a live overview in VS Code

## Contents

- [1. Context and scope](#context)
- [2. Goals and non-goals](#goals)
- [3. Design](#design)
  - [3.1 What a frame is](#frame)
  - [3.2 The frame's layout](#layout)
  - [3.3 The Issue panel](#issue-panel)
  - [3.4 The Review panel](#review-panel)
  - [3.5 The PR panel](#pr-panel)
  - [3.6 Switching frames](#switching)
  - [3.7 Session state: what each Claude is doing](#status)
  - [3.8 The Worktrees view](#worktrees-view)
  - [3.9 Failure modes](#failure-modes)
- [4. Alternatives considered](#alternatives)
- [5. Cross-cutting concerns](#cross-cutting)
- [6. Testing](#testing)
- [7. Requirements coverage](#requirements-coverage)
- [8. Work breakdown](#work-breakdown)
- [9. Open questions](#open-questions)

<a id="context"></a>
## 1. Context and scope

Start opens each issue's worktree in a window of its own, with the Redmine page on the left and
Claude Code beside it ([architecture §7.1](../architecture.md#start)). The round's files open as
text tabs once written, the round report opens as the `Review #<n>` tab with Go and Ship on it,
and Go stages the changes and shows them in that tab ([`vscode/README.md`](../../vscode/README.md)).
With several issues running, each window looks different depending on how far its round has got,
and the only way to see what each Claude is doing is to visit its window.

Design 0006 (Claude Desktop, written alongside this one) moves the flow to Claude Desktop, whose
sessions sit in one list with a fixed set of panes per session. This design brings that shape to
the extension:

- **Next up** stays the first view in the Plan sidebar.
- **A frame per worktree** holds the same three panels in the same places: Issue, Review, PR, with
  Claude Code beside them.
- **Frames switch** in one click or one key.
- **A second view, Worktrees,** shows every worktree's Claude as it works: running a tool,
  waiting on a person, or finished its turn, with the worktree's git and PR state and the buttons
  for its next step.

<a id="goals"></a>
## 2. Goals and non-goals

Goals:

- Every frame has the same layout, so a person knows where to look before switching to it.
- Review is one panel: the existing `Review #<n>` tab, with the round report, the staged changes,
  Go and Ship, and links to each of the round's files.
- Switching to any frame takes one click in Worktrees or one key from the Switch command.
- Worktrees shows each session's state within two seconds of it changing, in every window of the
  repo.
- A session waiting on a person is never silent: Worktrees marks it, and the Plan icon counts it.

Non-goals:

- One window for every worktree. §4 weighs it, and §9 holds the spike that could bring it back.
- Changing what Start, Go, Ship or Done check or write.
- Reading Claude's transcript beyond what a hook hands over.
- Worktrees of other repos, which is D7's cross-repo half.
- Answering a question from Worktrees. Claude Code takes answers only in the session's own
  window, so Focus is how a person answers.

<a id="design"></a>
## 3. Design

```
  window: gh-plan (main)            window: 151-worktree-frames-and-a-live (a frame)
  ┌──────────┬───────────────┐      ┌──────────┬───────────────────────────┬─────────────┐
  │ Plan     │               │      │ Plan     │ [Issue] [Review] [PR]     │ Claude Code │
  │ Next up  │               │      │ Next up  │                           │             │
  │ Worktrees│               │      │ Worktrees│  Review: round report,    │             │
  │ ● #152   │               │      │ ● #152   │  staged changes, links    │             │
  │ ◐ #151   │◀── Focus ─────┼──────┼─◐ #151   │  [Go] [Ship]              │             │
  └──────────┴───────────────┘      └──────────┴───────────────────────────┴─────────────┘
          ▲ watches                          ▲ watches
          └──── <main checkout>/.tmp/sessions/<session id>.json ◀── session_state.py
                                              (each session's hooks, from .claude/settings.json)
```

<a id="frame"></a>
### 3.1 What a frame is

A frame is the worktree's own window, as Start opens it now. One window per worktree keeps
everything VS Code scopes to a folder correct with no work: Source Control, terminals, the file
explorer, search, and Claude Code's working directory. What changes is that each window's editor
area has a fixed layout (§3.2), and switching between windows is the extension's job (§3.6)
instead of the macOS window list.

The main checkout's window is the frame for Next up, with no issue panels.

**Names.** A worktree is named by its issue's number and subject everywhere. Its folder is
`<repo>.worktrees/<n>-<slug>`, the slug being the branch's: the subject's first five words,
lowercase, joined by hyphens (`slug` in `vscode/start.js`). #103 builds this. VS Code's window
title shows the folder's name, so a frame's window reads `151-worktree-frames-and-a-live` with no
setting. Every other place shows `#<n>` and the subject cut to 40 characters with an ellipsis:
Next up's rows, the Worktrees cards' tooltips, the Switch Frame list, and the panels' tab titles
(`#151 Issue`, with the cut subject as the tooltip). The number stays the key: `.tmp/<n>/` and the
lock files keep it alone, and a folder's issue is the number before its first hyphen, so a
worktree made before at a bare `<n>` still resolves.

<a id="layout"></a>
### 3.2 The frame's layout

Start and Open lay the window out, and a **Plan: Reset Frame** command lays it out again:

| Group | Holds |
|---|---|
| Left, two thirds | The Issue, Review and PR panels, as three tabs in that order, Review in front once a round is written |
| Right, one third | Claude Code, opened with `claude-vscode.editor.open` |

Each panel is a webview panel with a `WebviewPanelSerializer`, so a reload or a restart brings it
back in place, as the Review tab already does. The Redmine page in the Integrated Browser stops
opening on Start; the Issue panel links to it.

The round's text files stop opening as tabs by themselves. The Review panel links to each one:
`commit.txt`, `pr-body.md`, each reply, and `.tmp/<n>/findings/outcomes.md` when the round has
one. A click opens the file in the left group for editing.

<a id="issue-panel"></a>
### 3.3 The Issue panel

The subject, the points, lane and version, the description rendered (Task, Guardrails, Lenses,
Done), the blockers and children with their status, and the journal's last five notes. Links open
in the Integrated Browser. It reloads on the window's focus with Next up, through `redmine.js`.

<a id="review-panel"></a>
### 3.4 The Review panel

The Review panel is the `Review #<n>` tab #110 built (`vscode/review.js`, `vscode/extension.js`'s
`reviewHost`). It already does most of what a frame needs:

- It shows the round report, `.tmp/<n>/report.html`, in a sandboxed frame that runs no script.
- Its bar holds Go, which becomes Ship once Go has staged, with the same checks and refusals as
  the title-bar buttons.
- After Go, the staged changes show below the report: the commit message, then each file's diff.
- A `WebviewPanelSerializer` brings it back after a reload.

What's left is #137's:

- "No review yet" before the first round's report.
- The next step, `/code-review high <range>`, when the pushed commit has no report.
- A Code-Review button and a Refresh button.
- Ship's `pr` output streamed into the tab instead of a terminal.

#137 also adds the links to the round's files (§3.2), `outcomes.md` among them. The frame layout
(§3.2, breakdown row 6) then places the tab in the left group with the Issue and PR panels and
retitles it `#<n> Review`.

The title-bar Go and Ship stay on the round's files, so neither is lost if the tab fails to load.

<a id="pr-panel"></a>
### 3.5 The PR panel

From `gh pr view --json` through `start.js`: the title, state, the `assess` check, mergeability,
and each open review thread with its file, line and first comment. A thread's file link opens the
line. Once merged, it shows **Done**. It reloads on the window's focus and after Ship.

The PR panel is where a frame shows its PR, so Ship's `pr` stops opening the PR through a
`vscode://` link, which can land in another window. Pages that agents open in the Integrated
Browser through the worktask extension's `/browse` link have the same problem and no panel to
fall back on: #92 still routes those to their own worktree's window.

<a id="switching"></a>
### 3.6 Switching frames

`vscode.openFolder` on a folder already open in a window focuses that window (`extension.js`
relies on this for the handoff). Every switch goes through it:

- **A click** on a card's Focus in Worktrees, or on Open in Next up.
- **Plan: Switch Frame**, a quick pick of the worktrees by number and cut subject, the ones
  waiting on a person first, with `ctrl+alt+p` as its default key.
- **Plan: Next Waiting Frame**, `ctrl+alt+n`, goes straight to the session that has waited on a
  person longest.

A status bar item in every window shows how many sessions wait on a person, and runs the Next
Waiting command.

<a id="status"></a>
### 3.7 Session state: what each Claude is doing

Claude Code's hooks hand each event's facts to a command. One committed script,
`.claude/hooks/session_state.py`, is registered in the repo's `.claude/settings.json` for every
event below, so every worktree carries it. It keeps one file per session,
`.tmp/sessions/<session_id>.json` in the main checkout, which it finds with
`git rev-parse --git-common-dir`. `.tmp/` is ignored, so the files never reach git.

| Event | State written | Detail kept |
|---|---|---|
| `SessionStart` | `idle` | `source` |
| `UserPromptSubmit` | `working` | the prompt's first line |
| `PreToolUse` | `working` | the tool and a short form of its input: a Bash command's first 80 characters, an edited file's path |
| `PreToolUse` on `AskUserQuestion` | `waiting` | `question`, and the first question's text |
| `PermissionRequest` | `waiting` | `permission`, and the tool and its short input |
| `PostToolUse` | `working` | the tool, so an answered prompt turns back to working |
| `Notification`, matched on `permission_prompt\|elicitation_dialog\|agent_needs_input` | `waiting` | `notification`, and the message |
| `Stop` | `idle` | `last_assistant_message`'s first 200 characters, and how many background tasks are running |
| `SessionEnd` | file removed | |

The matcher leaves out `idle_prompt`, which fires when a finished session has sat at its prompt
for a while: it would turn every `idle` card `waiting`.

Each file holds `{ state, since, kind, detail, issue, branch, worktree, session_id, at }`. `issue`
is the number in the branch name, `<type>/<n>-<slug>`, and the same `git` call that finds the main
checkout reads the branch. A session on `main` or on a detached HEAD writes nothing.

Each write is the whole file, to a temporary name and then renamed, so a reader never sees half a
file. Two hooks of one session can overlap, a `PermissionRequest` and a `PostToolUse` say, and
finish in either order, so each stamps `at` when it starts and skips the rename when the file
already holds a later `at`. A write can still slip in between that check and the rename; the
session's next event puts it right. The script reads stdin, writes, and exits 0 whatever goes wrong: an error is swallowed after
a line to `.tmp/sessions/errors.log`. It starts no process beyond that one `git` and opens no
connection.

The hook input fields named here are from Claude Code 2.1.310's types (`StopHookInput`,
`NotificationHookInput`, `PermissionRequestHookInput`). Before the script is built, a logging hook
checks what each event sends, §9's questions 2 and 3, and the mapping follows what it finds.

<a id="worktrees-view"></a>
### 3.8 The Worktrees view

A second webview view, `plan.worktrees`, under Next up in the Plan sidebar. Every window of the
repo watches the main checkout's `.tmp/sessions/` and redraws on a change. It reads each
worktree's git state from `git worktree list` and the PR's from the same `gh` read Ship already
uses, on refresh and on the window's focus, not on every state change.

One card per worktree of the repo. The view is narrow, about 35 to 40 characters, so a card takes
two or three lines:

```
WORKTREES ●1                      ⟳
─────────────────────────────────────
● #105  waiting · 2m
  Which lens covers the rollback…?
  3 files · 2 ahead · no PR
─────────────────────────────────────
◐ #106  working · Edit redmine.js
  clean · 4 ahead · PR #132 ✓ checks
─────────────────────────────────────
○ #26   idle · issue closed
  "Merged; ready for Done."
```

- **Line 1:** the state's glyph, `#<n>`, the state and how long it has held. A working card adds
  the tool and its file or command, shortened. ● waiting is yellow (the theme's warning colour),
  ◐ working is blue, and ○ idle is grey.
- **Line 2:** a waiting card's question or prompt, or an idle card's last message, on one line. A
  working card has none. The tooltip holds the full text, and the cut subject.
- **Line 3:** files changed, commits ahead of `origin/main`, and the PR number with its checks.
  "N behind main" is added in yellow, and only when the branch is behind.
- **Order:** waiting first, then working, then idle, the longest in its state first within each.

Selecting or hovering over a card expands it to show the branch and the buttons:

- **Focus** switches to the worktree's frame (§3.6), or opens it when no window is.
- **Changes** opens the branch's diff against `origin/main`.
- **Review** opens the `Review #<n>` tab.
- **Go**, **Ship** and **Done** run as they do from the worktree's own window.

A card's buttons act in its worktree's window, never the one the view is in. Each one switches to
that frame and hands it the action through the handoff Start already uses (`vscode/handoff.js`).
A closed issue's card offers only Done, and Done deletes its issue's state files.

The view's title shows `●N` for the waiting count, and the Plan icon's badge shows the same
count, so a waiting session shows from any window even with the sidebar closed. When a session
starts waiting and its frame isn't the focused window, the focused window shows a notification
with a *Switch* button. Only the window VS Code says is focused shows it.

A `working` state with no hook event for ten minutes shows as *working? · 10m*, since a session
killed without `SessionEnd` leaves its file. A long tool call, such as a test suite run through
Bash, sends nothing between its `PreToolUse` and its `PostToolUse`, so while the last event is a
`PreToolUse` the card shows the tool and how long it has run, *Bash · 12m*, instead. A missing, half-written or unreadable file shows its
worktree as *no session*, never as a wrong state. A file older than a day, or one whose worktree is
gone, shows as stale, and the next window to start deletes it.

<a id="failure-modes"></a>
### 3.9 Failure modes

| Event | Effect | Handling |
|---|---|---|
| A session is killed, so no `SessionEnd` | Its card says `working` forever | *working?* after ten minutes; stale after a day or once its worktree is gone, and then deleted |
| The hook script fails | A session's state stops changing | The hook always exits 0; the error goes to `errors.log`, and the card turns *working?* |
| Two hooks of one session write at once | One write lost | Whole-file renames, skipped when the file holds a later `at`; a write that slips past the check is put right by the next event |
| Two sessions in one worktree | Two files for one card | Each session writes its own file; the card shows the newest `at` |
| A state file is corrupt | Its card can't be read | *no session* for that worktree; the other cards are unaffected |
| The hook isn't registered, as on a branch made before it | That worktree's sessions never appear | Its card shows *no session*; the hook arrives with the next rebase onto `main` |
| A webview panel fails to load | No Review, Go or Ship in the panel | The title-bar Go and Ship stay (§3.4) |
| An issue's subject changes after Start | The folder's slug no longer matches the subject | The folder keeps its slug, as the branch does; the cards and tabs show the current subject |
| The window for a frame was closed | Focus has nothing to focus | `openFolder` opens a new window, laid out as Open does |
| The sessions folder grows | Slow redraws | One file per live session; ended sessions remove theirs |

Start, Go, Ship and Done keep their own failure modes ([`failure-modes.md`](../failure-modes.md)).

<a id="alternatives"></a>
## 4. Alternatives considered

- **One window, every worktree a folder of a multi-root workspace, a frame being a set of tabs
  swapped in and out.** Closest to Desktop. It costs: Claude Code's sessions take the window's
  folder, and how one is started in a given worktree folder isn't documented
  (`claude-vscode.editor.open` takes six arguments, none named in its manifest 🔍); terminals,
  search and Source Control show every worktree at once; VS Code restores one layout per window,
  so swapping tabs is the extension's to get right, every time. Kept for §9's spike.
- **Worktrees as a section inside Next up.** One view instead of two, but each view then scrolls
  the other out of sight, and a separate view can be collapsed, moved or kept open on its own.
- **The state files in `~/.local/state/`, from a hook in `~/.claude/settings.json`.** That would
  cover every repo's sessions, but a user-scope hook isn't in the repo, so a clone or another
  machine doesn't get it, and cross-repo views are out of scope here.
- **Claude Code's own sessions list** (`claudeVSCodeSessionsList`) as the overview. It lists one
  window's sessions, not every worktree's, and shows nothing of the issue or the round.
- **Reading each session's transcript** for state. It needs no hooks, but the format is Claude
  Code's private one, and a reader polling a dozen growing files costs more than one watcher.
- **The Redmine page in the Integrated Browser as the Issue panel.** Already there, but it can't
  show the blockers' status or link into the frame, and it's slow to load on every Start.
- **Go and Ship in the panel only.** Simpler, but a panel that fails to load would leave no way to
  ship. Both stay until the panel has been used for a while; removing them is a later issue.

<a id="cross-cutting"></a>
## 5. Cross-cutting concerns

| Lens | Answer |
|---|---|
| L1 Security | The state files hold prompt and command fragments, in the main checkout's ignored `.tmp/sessions/`, mode 600. No new process spawns in the extension. Webviews keep a strict CSP and load nothing remote |
| L2 Privacy and data | The fragments are cut short (80 to 200 characters) and deleted with their session; nothing leaves the machine, and git ignores `.tmp/` |
| L3 Reliability and failure | Atomic writes; the hook always exits 0; *working?* covers a lost `SessionEnd`; a bad file shows *no session*; the title-bar Go and Ship stay as a fallback |
| L4 Performance and limits | `PreToolUse` runs the script on every tool call, so it imports little, runs one `git` and must finish in under 100 ms; a test times it. Git and PR state are read on refresh and focus, and redraws are debounced |
| L5 Observability | That is this design's purpose: every session's state in every window of the repo, with the waiting count on the Plan icon. The script's own errors go to `errors.log` |
| L6 Compatibility and migration | Nothing in Redmine, git or `.tmp/<n>/` changes. A worktree opened before the change gets its layout from Reset Frame, and keeps its bare `<n>` folder, which still resolves |
| L7 Usability and accessibility | Fixed places for each panel; keys for switching; the waiting count in the status bar. Each state has its own glyph and text as well as its colour; every card line has a tooltip with the full text. Webviews use VS Code's theme variables |
| L8 Testability | Card building and sorting, and the state reducer, are pure functions tested with `node --test`; the hook script with `uv run pytest` against recorded hook inputs |
| L9 Operations and cost | The hook registration is committed in `.claude/settings.json`, so there's nothing to install. No paid API |
| L10 Docs impact | `vscode/README.md`; architecture §6.1, §7.1, §7.2 and §9; `failure-modes.md` for the state hook; `requirements.md` D7 for the part this covers |
| L11 Invariants | 2 and 4 hold: no write changes. 6 holds: `redmine.js` and `start.js` stay the only modules that spawn or connect, and the view's git and `gh` reads go through them; the extension only reads the state files. The hook script runs in Claude, outside the extension |
| L12 Offline and concurrency | State needs no network. Every window watches one folder and only reads it; each session writes only its own file |

<a id="testing"></a>
## 6. Testing

- The Review tab: #137's tests for the empty state, the next step, the range and the output; a
  link to each round file, `outcomes.md` among them, opens that file and nothing outside
  `.tmp/<n>/`.
- The state reducer: a recorded sequence of hook inputs gives the expected cards, sort order and
  badge count, including two sessions in one worktree, a killed session going *working?*, and a
  corrupt file showing *no session*.
- `session_state.py`, under `uv run pytest`: each event writes the expected file; a session on
  `main` writes nothing; a malformed input logs and exits 0; two writes at once leave two whole
  files; it finishes under 100 ms.
- The cards: line 3's "behind main" shows only when behind; a closed issue's card offers only Done.
- The layout: Start and Reset Frame give the groups and tabs of §3.2, read from `tabGroups`.
- Names: `<n>-<slug>` folders and bare `<n>` ones both resolve to issue `<n>`; a subject over 40
  characters is cut with an ellipsis.
- Switch: the quick pick's order, and that a pick calls `openFolder` on the worktree.

<a id="requirements-coverage"></a>
## 7. Requirements coverage

| ID | How |
|---|---|
| `D5` | Each worktree's frame and session, shown side by side in Worktrees |
| `D7` | Worktrees is the one view of every item in progress in the repo, with its branch, its worktree, its round and what waits on it. Other repos' items are out of scope |

<a id="work-breakdown"></a>
## 8. Work breakdown

| # | Issue | Points |
|---|---|---|
| 1 | `session_state.py` and its registration in `.claude/settings.json`, with tests | 2 |
| 2 | #118: the Worktrees view, with its cards, buttons, badge and notification | 5 |
| 3 | Switching: Switch Frame, Next Waiting Frame, the status bar item, clicks | 2 |
| 4 | #137: the Review tab's next step, Code-Review and Refresh, Ship's output, and the links to the round's files | 5 |
| 5 | The Issue and PR panels, with serializers; Ship stops opening the PR by link | 3 |
| 6 | The frame layout on Start, Open and Reset Frame, the Review tab moved into it, and the round's files no longer opened as tabs | 2 |
| 7 | Spike: one window with every worktree (§9) | 2 |
| 8 | Docs: `vscode/README.md`, architecture, failure modes | 1 |
| 9 | #103: worktree folders named `<n>-<slug>`. Shared with design 0006 | 2 |

1 blocks 2 and 3. 9, 4 and 5 block 6. 2, 3 and 6 block 8. 7 relates to 6, since a yes would
replace it. #92 is outside this table: it still routes Integrated Browser pages to their own
window (§3.5).

<a id="open-questions"></a>
## 9. Open questions

1. Can `claude-vscode.editor.open` start a session in a chosen folder of a multi-root workspace?
   Its six arguments are unnamed in the manifest. A yes revives the one-window alternative. 🔍
2. Which `notification_type` values does Claude Code send, which mean it waits on a person, and
   which field carries the text? `NotificationHookInput` types the type as a string, and the docs'
   example shows `message`. 🔍
3. Does `PreToolUse` fire for `AskUserQuestion`, and does `PermissionRequest` fire when the
   Claude Code extension's own permission dialog shows, or only in the terminal? 🔍
