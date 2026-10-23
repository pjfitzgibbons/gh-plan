#!/bin/sh
# Apply a drafted body from .tmp/ to the current branch's PR. Used by gh aliases:
#
#   gh pc [N]   pr create, title = the branch's first commit subject, body .tmp/N/pr-body.md;
#               then requests Copilot's review
#   gh pb [N]   pr edit (this branch's PR) --body-file .tmp/N/pr-body.md
#
# N is a Redmine issue number. It defaults to the one in the branch name: fix/88-go-requests -> 88.
# GHBODY_DRY=1 prints the gh command instead of running it.
set -eu

kind=${1:?usage: ghbody.sh pr-create|pr-edit [N]}
root=$(git rev-parse --show-toplevel)
n=${2:-$(git branch --show-current | sed -nE 's,^[^/]+/([0-9]+)-.*,\1,p')}
[ -n "$n" ] || { echo "ghbody: no issue number given and none in the branch name" >&2; exit 2; }
printf '%s' "$n" | grep -qE '^[0-9]+$' ||
  { echo "ghbody: '$n' is not an issue number; the body is .tmp/N/pr-body.md" >&2; exit 2; }

case $kind in
  pr-create | pr-edit) file="$root/.tmp/$n/pr-body.md" ;;
  *) echo "ghbody: unknown kind $kind" >&2; exit 2 ;;
esac
[ -f "$file" ] || { echo "ghbody: $file not found" >&2; exit 2; }

run() { if [ "${GHBODY_DRY:-}" ]; then printf '%s\n' "$*"; else "$@"; fi; }

case $kind in
  pr-create | pr-edit)
    # PR descriptions carry no attribution.
    if grep -qi 'assisted-by\|co-authored-by' "$file"; then
      echo "ghbody: $file contains an attribution line; remove it" >&2; exit 1
    fi
    if [ "$kind" = pr-create ]; then
      base=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
      title=$(git log --reverse --format=%s "origin/$base..HEAD" | head -n1)
      [ -n "$title" ] || { echo "ghbody: no commits ahead of origin/$base" >&2; exit 2; }
      run gh pr create --title "$title" --body-file "$file"
      # gh pc asks Copilot to review each new PR; pr.sh asks again each time it updates one,
      # and gh documents @copilot for pr edit, not pr create. A failed request doesn't fail gh pc.
      run gh pr edit --add-reviewer @copilot ||
        echo "ghbody: Copilot's review was not requested; request it under Reviewers on the PR" >&2
    else
      run gh pr edit --body-file "$file"
    fi ;;
esac
