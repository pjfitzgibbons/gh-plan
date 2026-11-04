# tmux

An issue's work from the keyboard: `plan-session <n>` opens a tmux session in issue `<n>`'s
worktree, with Claude Code beside Neovim and a shell, and `plan.tmux` gives tmux vi
copy keys that reach the macOS clipboard, and keys for the issue's Redmine page and its pull
request. Start in the extension's Next up panel still makes the worktree; Go, Ship and Done stay
the extension's.

## Installing

Both come from the main checkout, so they follow `main`. From its root:

```sh
ln -s "$PWD/tmux/plan-session" ~/.local/bin/plan-session
echo "source-file '$PWD/tmux/plan.tmux'" >> ~/.tmux.conf
tmux source-file ~/.tmux.conf   # when a tmux server is already running
```

They need tmux 3.2 or newer, `python3` 3.10 or newer, and `nvim`, `claude` and `gh` on PATH.
`plan.tmux` sets only the options below; anything else in `~/.tmux.conf` stays as it is.

## plan-session

`plan-session <n>`, run from any checkout of the repo, finds the worktree on issue `<n>`'s branch,
`<type>/<n>-<slug>`, and goes to the tmux session `plan-<n>`: inside tmux it switches to it, and
outside it attaches to it. The first call makes the session in the worktree, with one window,
`plan`, of three panes, all started in the worktree:

```
┌──────────┬──────────┐
│          │ nvim     │
│ claude   ├──────────┤
│          │ shell    │
└──────────┴──────────┘
```

Claude's pane is the active one. The shell is tmux's default, the `SHELL` the tmux server started
with, zsh on macOS.

A later call only goes to the session, so a second Claude never starts. `plan-session` reads git
and drives tmux, and makes no worktree or branch: an issue with no worktree is refused, naming
Start, and one with two worktrees is refused, naming both. Either exits 1 having made nothing.

## Keys

`prefix` is tmux's prefix key, `C-b` unless `~/.tmux.conf` changes it.

tmux owns the panes: the terminal around it, Ghostty or another, shows one pane, and its own
splits stay unused. Splits made in both nest one set of panes inside the other, and neither's keys
then reach the pane they name. tmux's own split keys (`prefix %`, `prefix "`, `prefix` and an
arrow) work in a `plan-<n>` session as in any other.

| Key | Where | Does |
|---|---|---|
| `prefix [` | anywhere | Enters copy mode (tmux's own key) |
| `v` | copy mode | Starts a selection |
| `V` | copy mode | Selects whole lines |
| `y` | copy mode | Copies the selection to the macOS clipboard with `pbcopy`, and leaves copy mode |
| `prefix i` | a `plan-<n>` session | Opens issue `<n>`'s Redmine page (`REDMINE_URL`, `http://127.0.0.1:3010` unset) |
| `prefix g` | a `plan-<n>` session | Opens the worktree's pull request, with `gh pr view --web` |

Copy mode takes vi keys (`mode-keys vi`), so `h`, `j`, `k`, `l`, `w`, `/` and the rest move as in
Vim. In any session but a `plan-<n>` one, `prefix i` and `prefix g` keep tmux's own actions:
`prefix i` shows the window's details, and `prefix g`, from tmux 3.8, moves a floating pane, which
a `plan-<n>` session gives up. `REDMINE_URL` is read from the tmux server's environment, the one
it started with.

`plan.tmux` also sets `set-clipboard on` and `allow-passthrough on`, so a program's own copy,
such as Claude Code's `/copy`, reaches the clipboard through tmux. That needs a terminal that
takes OSC 52, the escape that sets the clipboard, such as iTerm2 with *Applications in terminal
may access clipboard* on; `y` in copy mode works in any terminal.

## Plugins that help

Neither is needed, and `plan.tmux` doesn't load them.

- [extrakto](https://github.com/laktak/extrakto): `prefix tab` fuzzy-finds words, paths and URLs on
  the screen and copies or inserts one.
- [tmux-fingers](https://github.com/Morantron/tmux-fingers): highlights paths, hashes and URLs on
  the screen with a letter hint each, to copy one by its letter.

## Tests

`uv run pytest tests/test_plan_session.py` runs both files against a temporary repo with
worktrees and a tmux server of its own, with stand-ins for `nvim`, `claude`, `pbcopy`, `open` and
`gh`.
