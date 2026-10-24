#!/bin/sh
# pr [done] [N]: commit, push and open or update the PR for the current branch, in one step that is
# safe to repeat. Each step runs only when it has something to do:
#
#   commit  when the tree has changes: stages everything (.tmp/ is ignored) and commits with the
#           message in .tmp/N/commit.txt, then deletes that file, so the next change needs a new one.
#           Under Ship (PR_STAGED=1) it commits what Go staged, and refuses a change made since
#   fetch   every time: fetches origin, and stops before the push when that fails. Refuses a
#           GitHub branch with commits the local one lacks, so a push never drops them
#   rebase  when the branch lacks origin's tip of the default branch: rebases onto it, so the PR isn't
#           behind when pr opens or updates it, and needs no merge from it. A failed rebase is aborted, leaving the
#           branch as it was, and stops before the push
#   push    git push -u origin HEAD; a no-op when the remote has the branch's commits. After a
#           rebase, a force push leased against the head just fetched, so a push from elsewhere
#           since is refused, never overwritten
#   PR      gh pc when the branch has no PR, else gh pb, which replaces the body with
#           .tmp/N/pr-body.md, then opens the PR in the GitHub Pull Requests extension
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
# Mid-rebase there is no branch, and a commit would land in the rebase, merge, cherry-pick or revert.
rebasing() { [ -d "$(git rev-parse --git-path rebase-merge)" ] || [ -d "$(git rev-parse --git-path rebase-apply)" ]; }
! rebasing ||
  { echo "pr: a rebase is in progress; finish it with git rebase --continue, or git rebase --abort, then run pr again" >&2; exit 2; }
for op in MERGE CHERRY_PICK REVERT; do
  ! git rev-parse -q --verify "${op}_HEAD" >/dev/null ||
    { echo "pr: a $(echo "$op" | tr A-Z_ a-z-) is in progress; finish or abort it, then run pr again" >&2; exit 2; }
done
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

# Rebase onto origin's base, on committed work only, so the PR needs no Update branch, whose merge
# the Linear history ruleset refuses. A failed fetch stops here: nothing is pushed on a stale base.
# --prune, so a branch deleted on GitHub leaves no stale origin/<branch> to lease against.
if ! why=$(GIT_TERMINAL_PROMPT=0 git fetch -q --prune origin 2>&1); then
  echo "pr: couldn't fetch origin, so nothing is pushed; check the network and run pr again:" >&2
  printf '%s\n' "$why" | sed 's/^/  /' >&2
  exit 2
fi
onto=$(git rev-parse -q --verify "refs/remotes/origin/$base") ||
  { echo "pr: the fetch brought no origin/$base, so nothing is pushed; check remote.origin.fetch" >&2; exit 2; }
pushed=$(git rev-parse -q --verify "refs/remotes/origin/$branch" || true)
# GitHub's commits that HEAD lacks, by patch, leaving out main's: a push would drop them, whether
# another clone pushed them or they were dropped or rewritten here. A merge has no patch, and may
# carry edits of its own, so one HEAD lacks is refused too. A patch-id counts context lines,
# so a copy rebased past nearby edits doesn't match its original, and is refused too: the printed
# push leases on the SHA, so a fetch since, such as VS Code's autofetch, can't widen it.
if [ -n "$pushed" ]; then
  lost=$(git log --oneline --cherry-pick --right-only "HEAD...$pushed" "^$onto")
  if [ -n "$lost" ]; then
    echo "pr: GitHub's $branch has commits $branch lacks, by patch, so nothing is pushed:" >&2
    printf '%s\n' "$lost" | sed 's/^/  /' >&2
    echo "pr: pushed from another clone: git pull --rebase origin $branch, then pr $n" >&2
    echo "pr: rewritten or dropped here: git push --force-with-lease=refs/heads/$branch:$pushed origin HEAD, then pr $n" >&2
    exit 2
  fi
fi
short=$(git rev-parse --short "$onto")
if git merge-base --is-ancestor "$onto" HEAD; then
  echo "pr: already on $base ($short)"
else
  # git overwrites an ignored file at a path the rebase brings in, so a local file there is refused.
  clash=$(git diff --name-only --diff-filter=A "HEAD...$onto" | while IFS= read -r f; do
    [ ! -e "$f" ] || printf '%s\n' "$f"
  done)
  if [ -n "$clash" ]; then
    echo "pr: origin/$base adds files $branch has as ignored files, which a rebase would overwrite, so nothing is pushed:" >&2
    printf '%s\n' "$clash" | sed 's/^/  /' >&2
    echo "pr: move them aside, then pr $n" >&2
    exit 2
  fi
  if ! why=$(git rebase -q "$onto" 2>&1); then
    conflicts=$(git diff --name-only --diff-filter=U)
    # A rebase a hook refused never started, so has nothing to abort.
    ! rebasing || git rebase --abort ||
      { echo "pr: rebasing $branch onto origin/$base failed, and so did git rebase --abort; nothing is pushed. Run git rebase --abort, then pr $n" >&2; exit 2; }
    echo "pr: rebasing $branch onto origin/$base ($short) failed, so it is aborted, $branch is as it was, and nothing is pushed:" >&2
    printf '%s\n' "${conflicts:-$why}" | sed 's/^/  /' >&2
    echo "pr: to rebase by hand: git rebase origin/$base, fix and git add each file, git rebase --continue," >&2
    echo "pr: ${pushed:+git push --force-with-lease=refs/heads/$branch:$pushed origin HEAD, }then pr $n" >&2
    exit 2
  fi
  [ -z "$why" ] || printf '%s\n' "$why"
  echo "pr: rebased onto $short (origin/$base)"
fi
# A branch rewritten without dropping GitHub's commits, by this run's rebase or one by hand, replaces
# GitHub's, leased against the head just fetched, so a push from elsewhere since is refused too.
lease=""
if [ -n "$pushed" ] && ! git merge-base --is-ancestor "$pushed" HEAD; then
  lease="--force-with-lease=refs/heads/$branch:$pushed"
fi

# No -q: a slow push shows its progress. A credential prompt fails at once rather than waiting.
echo "pr: pushing $branch${lease:+, replacing the rewritten branch on GitHub}"
# shellcheck disable=SC2086 # $lease is one word, or none
GIT_TERMINAL_PROMPT=0 git push -u $lease origin HEAD

if [ -n "$state" ]; then
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
