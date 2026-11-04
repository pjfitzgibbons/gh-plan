# GH Plan's tmux settings: source this file from ~/.tmux.conf (tmux/README.md).
#
# Copy mode takes vi keys and copies to the macOS clipboard, and a plan-<n> session, the one
# plan-session makes, gets keys for its issue's Redmine page and its pull request.

# Copy mode: prefix [ enters it; v starts a selection, V selects whole lines, y copies and leaves.
set-option -g mode-keys vi
bind-key -T copy-mode-vi v send-keys -X begin-selection
bind-key -T copy-mode-vi V send-keys -X select-line
bind-key -T copy-mode-vi y send-keys -X copy-pipe-and-cancel pbcopy

# A program's own copy, such as Claude Code's /copy, writes the clipboard with an OSC 52 escape,
# and set-clipboard passes it out to the terminal: any pane's output can then set the clipboard.
# plan-session lets escapes through tmux (allow-passthrough) in Claude's pane alone.
set-option -g set-clipboard on

# prefix i: the session's issue in Redmine. prefix g: its pull request, from its worktree.
# plan-session sets @plan_issue on the session. In any other session both keep tmux's own action:
# prefix i shows the window's details, and prefix g, from tmux 3.8, moves a floating pane.
bind-key i {
  if-shell -F '#{@plan_issue}' {
    run-shell -b 'open "${REDMINE_URL:-http://127.0.0.1:3010}/issues/#{@plan_issue}"'
  } {
    display-message
  }
}
bind-key g {
  if-shell -F '#{@plan_issue}' {
    run-shell -b -c '#{session_path}' 'gh pr view --web'
  } {
    switch-client -T move
  }
}
