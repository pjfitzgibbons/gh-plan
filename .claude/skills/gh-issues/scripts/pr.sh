#!/bin/sh
# pr [done] [N]: commit, push and open or update the PR for the current branch, in one step that is
# safe to repeat. Each step runs only when it has something to do:
#
#   commit  when the tree has changes: stages everything (.tmp/ is ignored) and commits with the
#           message in .tmp/N/commit.txt, then deletes that file, so the next change needs a new one.
#           Under Ship (PR_STAGED=1) it commits what Go staged, and refuses a change made since
#   push    git push -u origin HEAD; a no-op when the remote has the branch's commits
#   PR      gh pc when the branch has no PR, else gh pb, which replaces the body with
#           .tmp/N/pr-body.md, then opens the PR in the GitHub Pull Requests extension
#   Copilot when the push moved the PR's branch: asks Copilot to review it again, so each round of
#           review fixes is reviewed in turn (gh pc asks for a new PR)
#   replies when .tmp/N/replies/ holds <comment id>.md files: posts each to its review thread,
#           resolves the thread and deletes the file (pr_threads.py --post)
#
# The issue's Done list is ticked in the PR body's ## Done section, never in Redmine: a
# .tmp/N/issue.md left from before is named as no longer sent, and left where it is.
#
# Once the PR has merged, pr sends only the replies and the PR body, so a Done line ticked after
# the merge reaches the PR, refusing a branch with work the PR lacks, then says to press Done in
# Next up,
# which cleans up: it checks what removing the worktree and pulling would delete or overwrite,
# which pr can't see. pr done does the same, and refuses a PR that has not merged.
#
# N defaults to the issue number in the branch name, as for gh pc (ghbody.sh).
# It refuses the default branch and a branch whose PR closed unmerged.
set -eu

# The review replies of .tmp/N/, after the push, so they describe code the PR has. The script
# checks every reply before its first post and deletes each once it is sent, so a stopped pr resumes
# where it stopped. It needs Python 3.10; /usr/bin/python3 is 3.9.
scripts=$(dirname "$(realpath "$0")")
send_round() {
  [ ! -f ".tmp/$n/issue.md" ] ||
    echo "pr: .tmp/$n/issue.md is no longer sent; tick the issue's Done list in the PR body's ## Done section, and delete the file" >&2
  [ -d ".tmp/$n/replies" ] || return 0
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' ||
    { echo "pr: .tmp/$n/ has replies to send, and python3 is older than 3.10" >&2; exit 2; }
  number=$(gh pr view "$branch" --json number --jq .number)
  python3 "$scripts/pr_threads.py" "$number" --post ".tmp/$n/replies"
}

finish=0
[ "${1:-}" != done ] || { finish=1; shift; }

root=$(git rev-parse --show-toplevel)
cd "$root"
branch=$(git branch --show-current)
echo "pr: checking $branch"
base=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
[ "$branch" != "$base" ] || { echo "pr: on $base; switch to the issue's branch" >&2; exit 2; }
n=${1:-$(printf '%s' "$branch" | sed -nE 's,^[^/]+/([0-9]+)-.*,\1,p')}
[ -n "$n" ] || { echo "pr: no issue number in branch $branch; give it: pr N" >&2; exit 2; }

state=$(gh pr view "$branch" --json state --jq .state 2>/dev/null || true)
[ "$finish" = 0 ] || [ "$state" = MERGED ] || { echo "pr: the PR for $branch has not merged" >&2; exit 2; }
case $state in
  CLOSED) echo "pr: the PR for $branch closed unmerged; start a new branch" >&2; exit 2 ;;
  MERGED)
    # Work the merged PR lacks would be left unpushed, and replies would describe it: stop, so Ship
    # shows it rather than reporting success.
    head=$(gh pr view "$branch" --json headRefOid --jq .headRefOid)
    if [ "$(git rev-parse HEAD)" != "$head" ] || [ -n "$(git status --porcelain)" ]; then
      echo "pr: the PR for $branch has merged, and $branch has work it lacks; start a new branch for it" >&2
      git status --short >&2
      exit 2
    fi
    send_round
    if [ -f ".tmp/$n/pr-body.md" ]; then
      echo "pr: updating the PR body"
      gh pb "$n"
    fi
    echo "pr: the PR for $branch has merged; press Done on #$n in Next up to clean up"
    exit 0 ;;
esac

if [ -n "$(git status --porcelain)" ]; then
  message=".tmp/$n/commit.txt"
  [ -f "$message" ] || { echo "pr: the tree has changes and $message is missing" >&2; git status --short >&2; exit 2; }
  if [ -n "${PR_STAGED:-}" ]; then
    # From Ship: commit what Go staged and showed, never a change made since.
    late=$(git diff --name-only; git ls-files --others --exclude-standard)
    [ -z "$late" ] || { echo "pr: changed since Go staged the tree, so not reviewed; press Go again:" >&2; echo "$late" >&2; exit 2; }
  else
    git add -A
  fi
  git commit -q -F "$message"
  rm "$message"
  git log --oneline --stat -1
else
  echo "pr: nothing to commit"
fi

# The PR's head on GitHub before the push, so Copilot is asked again only when the push adds
# commits. GitHub's, not the local origin/<branch>, which another clone's push leaves behind. A
# read that fails leaves it empty, so Copilot is asked rather than the push stopped.
head_before=""
[ -z "$state" ] || head_before=$(gh pr view "$branch" --json headRefOid --jq .headRefOid || true)

# No -q: a slow push shows its progress. A credential prompt fails at once rather than waiting.
echo "pr: pushing $branch"
GIT_TERMINAL_PROMPT=0 git push -u origin HEAD

if [ -n "$state" ]; then
  # Straight after the push, so a later step that fails can't leave pushed commits unreviewed. A pr
  # stopped between the push and this request still can: the next one finds nothing new to ask about.
  if [ "$head_before" != "$(git rev-parse HEAD)" ]; then
    echo "pr: asking Copilot to review the new commits"
    gh pr edit "$branch" --add-reviewer @copilot >/dev/null ||
      echo "pr: Copilot wasn't asked to review the PR; ask under Reviewers" >&2
  else
    echo "pr: no new commits; Copilot isn't asked again"
  fi
  echo "pr: updating the PR body"
  gh pb "$n"
else
  echo "pr: opening the PR"
  gh pc "$n"
fi

send_round

# The PR in the GitHub Pull Requests extension's view, not github.com: only the extension can
# offer to delete the local branch when the PR merges.
url=$(gh pr view "$branch" --json url --jq .url)
echo "$url"
# The extension offers "Delete Local Branch" only for a branch git config ties to the PR, as
# owner#repo#N. It writes that itself only for PRs it opened or checked out, so gh pc's need it here.
pr_path=${url#https://github.com/}
git config "branch.$branch.github-pr-owner-number" "$(echo "$pr_path" | sed 's|/pull/|#|; s|/|#|')"
open "vscode://github.vscode-pull-request-github/open-pull-request-webview?uri=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$url")"
