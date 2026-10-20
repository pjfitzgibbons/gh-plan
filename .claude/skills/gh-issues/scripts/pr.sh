#!/bin/sh
# pr [done] [N]: commit, push and open or update the PR for the current branch, then clean up once
# it merges, in one step that is safe to repeat. Each step runs only when it has something to do:
#
#   commit  when the tree has changes: stages everything (.tmp/ is ignored) and commits with the
#           message in .tmp/N/commit.txt, then deletes that file, so the next change needs a new one
#   push    git push -u origin HEAD; a no-op when the remote has the branch's commits
#   PR      gh pc when the branch has no PR, else gh pb, which replaces the body with
#           .tmp/N/pr-body.md, then opens the PR in the GitHub Pull Requests extension
#   Copilot when the push moved the PR's branch: asks Copilot to review it again, so each round of
#           review fixes is reviewed in turn (gh pc asks for a new PR)
#   replies when .tmp/N/replies/ holds <comment id>.md files: posts each to its review thread,
#           resolves the thread and deletes the file (pr_threads.py --post)
#   ticks   when .tmp/N/issue.md exists: applies its checkbox changes to issue N's description in
#           Redmine, refusing any other difference, then deletes it (checklist.py)
#   cleanup once the PR has merged: removes the branch's worktree, switches the main checkout to
#           the default branch, pulls, deletes the local branch, and has Redmine fetch the new
#           commits, so a fixes #N commit closes issue N. pr done does only this, and refuses a PR
#           that has not merged.
#
# N defaults to the issue number (or old item ID) in the branch name, as for gh pc (ghbody.sh).
# It refuses the default branch, a branch whose PR closed unmerged, and the cleanup when the branch
# holds work the merged PR lacks.
set -eu

# Redmine reads new commits only when its Repository tab opens; its repository web service fetches
# them now (gh-plan #62). The key is in the Keychain, and goes in the request body, out of ps and
# Redmine's log. Without it, or without Redmine, pr says so and carries on.
fetch_redmine() {
  key=$(security find-generic-password -s redmine-sys-api -w 2>/dev/null) ||
    { echo "pr: no Keychain item redmine-sys-api; Redmine fetches the commits when its Repository tab opens" >&2; return 0; }
  url=${REDMINE_URL:-http://127.0.0.1:3010}
  # /sys never reloads Redmine's settings, so a key setup.rb just stored is refused until a page
  # view reloads them: one first.
  curl -s -o /dev/null "$url/login" || true
  code=$(printf 'key=%s' "$key" | curl -s -o /dev/null -w '%{http_code}' --data-binary @- \
    "$url/sys/fetch_changesets") || true
  if [ "$code" = 200 ]; then echo "pr: Redmine fetched the commits"
  else echo "pr: Redmine didn't fetch the commits (HTTP ${code:-000}); open its Repository tab" >&2; fi
}

# The review replies and checklist ticks of .tmp/N/, after the push, so the replies describe code
# the PR has. Each script checks everything before its first write and deletes each file once it
# is sent, so a stopped pr resumes where it stopped. They need Python 3.10; /usr/bin/python3 is 3.9.
scripts=$(dirname "$(realpath "$0")")
send_round() {
  [ -d ".tmp/$n/replies" ] || [ -f ".tmp/$n/issue.md" ] || return 0
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' ||
    { echo "pr: .tmp/$n/ has replies or ticks to send, and python3 is older than 3.10" >&2; exit 2; }
  if [ -d ".tmp/$n/replies" ]; then
    number=$(gh pr view "$branch" --json number --jq .number)
    python3 "$scripts/pr_threads.py" "$number" --post ".tmp/$n/replies"
  fi
  [ ! -f ".tmp/$n/issue.md" ] || python3 "$scripts/checklist.py" "$n" ".tmp/$n/issue.md"
}

finish=0
[ "${1:-}" != done ] || { finish=1; shift; }

root=$(git rev-parse --show-toplevel)
cd "$root"
branch=$(git branch --show-current)
echo "pr: checking $branch"
base=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
[ "$branch" != "$base" ] || { echo "pr: on $base; switch to the item's branch" >&2; exit 2; }
n=${1:-$(printf '%s' "$branch" | sed -nE 's,^[^/]+/([0-9]+|[a-z][a-z0-9]*-[a-z]+)-.*,\1,p')}
[ -n "$n" ] || { echo "pr: no issue number in branch $branch; give it: pr N" >&2; exit 2; }

state=$(gh pr view "$branch" --json state --jq .state 2>/dev/null || true)
[ "$finish" = 0 ] || [ "$state" = MERGED ] || { echo "pr: the PR for $branch has not merged" >&2; exit 2; }
case $state in
  CLOSED) echo "pr: the PR for $branch closed unmerged; start a new branch" >&2; exit 2 ;;
  MERGED)
    # Clean up, but only when the PR merged exactly this branch's commits.
    head=$(gh pr view "$branch" --json headRefOid --jq .headRefOid)
    if [ "$(git rev-parse HEAD)" != "$head" ] || [ -n "$(git status --porcelain)" ]; then
      echo "pr: $branch has work the merged PR lacks; nothing deleted" >&2
      git status --short >&2
      exit 2
    fi
    # Before the worktree goes, and its .tmp/ with it.
    send_round
    main=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
    if [ "$root" != "$main" ]; then
      echo "pr: removing the worktree $root"
      cd "$main"
      git worktree remove "$root"
    fi
    echo "pr: merged; switching $main to $base"
    git -C "$main" switch -q "$base"
    git -C "$main" pull -q --prune
    # -D: git can't see a squash merge, so -d would refuse.
    git -C "$main" branch -D "$branch"
    fetch_redmine
    exit 0 ;;
esac

if [ -n "$(git status --porcelain)" ]; then
  message=".tmp/$n/commit.txt"
  [ -f "$message" ] || { echo "pr: the tree has changes and $message is missing" >&2; git status --short >&2; exit 2; }
  git add -A
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
  # Straight after the push, so a later step that fails can't leave pushed commits unreviewed.
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
