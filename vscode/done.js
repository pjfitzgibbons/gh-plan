// Finishing an issue once its PR has merged: its worktree and branch gone, the main checkout's base
// at the merge, Redmine's commits fetched, and the issue closed with a note. No vscode import: plain
// functions, tested under node --test. Each step runs only when it has something to do, so a Done
// that stopped part way is finished by the next, and a second Done changes nothing.
const fs = require("fs");
const path = require("path");
const { checkId, issueBranch, worktrees, existingWorktree, worktreeDir, git, lock, settingUp, mainCheckout } = require("./start");
const { prLink, pullRequest } = require("./ship");
const { field } = require("./redmine");

// The statuses Done accepts: In Progress, and Closed, which a fixes commit's fetch or an earlier
// Done leaves while the cleanup or the note may still be to do.
const FINISHES = new Set(["In Progress", "Closed"]);
const FIELDS = "state,url,number,title,headRefName,headRefOid,baseRefName,mergeCommit,isCrossRepository";

// The last PR the issue's Pull requests field links, by URL; null when it links none.
function linkedPullRequest(issue) {
  const urls = field(issue, "Pull requests").match(/https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+/g);
  return urls ? urls[urls.length - 1] : null;
}

// The PR as gh reports it, by URL, so it needs no checkout of its repo.
function viewPullRequest(cwd, url) {
  return pullRequest(cwd, url, FIELDS);
}

// The PR's repo on GitHub.
function repoUrl(pr) {
  return pr.url.replace(/\/pull\/\d+$/, "");
}

function commitUrl(pr) {
  return `${repoUrl(pr)}/commit/${pr.mergeCommit.oid}`;
}

// The closing note: the PR and its merge commit, each linked.
function doneNote(pr) {
  return `Done: ${prLink(pr)} merged as [${pr.mergeCommit.oid.slice(0, 7)}](${commitUrl(pr)}).`;
}

// owner/repo, lower-cased: a URL's last two path segments, so an ssh host alias or GitHub's ssh on
// port 443 reads as the repo it names.
function repoOf(url) {
  const found = url.match(/([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?$/);
  return found ? `${found[1]}/${found[2]}`.toLowerCase() : null;
}

// Each local branch and its tip. lstrip, not short: short names a branch heads/<name> when a tag
// shares its name.
async function branches(cwd) {
  const out = await git(cwd, "for-each-ref", "--format=%(refname:lstrip=2) %(objectname)", "refs/heads");
  return new Map(out.trim().split("\n").filter(Boolean).map((line) => line.split(" ")));
}

// Does commit oid hold nothing the PR lacks: is it the PR's merged head, or behind it, as after a
// suggestion accepted on GitHub? A head this repo lacks is fetched from the PR's own ref, which
// GitHub keeps after the branch is deleted, into FETCH_HEAD only; a failed fetch stops Done, rather
// than reading as commits the PR lacks.
async function inPullRequest(cwd, oid, pr) {
  if (oid === pr.headRefOid) return true;
  if (!(await fetchHead(cwd, pr))) return false;
  return git(cwd, "merge-base", "--is-ancestor", oid, pr.headRefOid).then(() => true, () => false);
}

// Has cwd the PR's merged head, fetching it if not? Done calls it before its first change, so the
// branch sweep, after the changes, never needs origin.
async function fetchHead(cwd, pr) {
  const has = () => git(cwd, "cat-file", "-e", `${pr.headRefOid}^{commit}`).then(() => true, () => false);
  if (await has()) return true;
  await git(cwd, "fetch", "--quiet", "origin", `refs/pull/${pr.number}/head`).catch((error) => {
    throw new Error(`${prLink(pr)}'s merged head ${pr.headRefOid.slice(0, 7)} isn't in ${cwd}, and fetching it failed; Done again once origin can be reached: ${error.message}`);
  });
  return has();
}

// A tree Done may remove or switch away from: clean, and at or behind the PR's merged head. Throws
// naming why not. untracked: count untracked files, which a switch would carry over but a removal
// would lose.
async function checkMerged(dir, pr, untracked) {
  const changes = (await git(dir, "status", "--porcelain", ...(untracked ? [] : ["--untracked-files=no"]))).trim();
  if (changes) throw new Error(`${dir} has changes the merged PR lacks; commit or discard them, then Done again:\n${changes}`);
  const head = (await git(dir, "rev-parse", "HEAD")).trim();
  if (!(await inPullRequest(dir, head, pr))) {
    throw new Error(`${dir} is at ${head.slice(0, 7)}, with commits ${prLink(pr)}'s merged head ${pr.headRefOid.slice(0, 7)} lacks; open a new PR for them or drop them, then Done again`);
  }
}

// Finish issue id. view(cwd, url) reads the PR, from gh unless a test gives one. Returns
// { dir, notes }: the worktree it removed, or null, and what each step did. A Done that fails part
// way throws with that result as error.result, so Peter learns what it changed.
async function finish({ redmine, checkouts, id, view = viewPullRequest }) {
  checkId(id, "Done");
  const issue = await redmine.issue(id);
  // The panel's row can be older than the issue.
  if (!FINISHES.has(issue.status.name)) throw new Error(`#${id} is ${issue.status.name}; refresh Next up`);
  const url = linkedPullRequest(issue);
  if (!url) throw new Error(`#${id} links no pull request; Ship it first`);
  const { project, checkout, common } = await mainCheckout(redmine, issue, checkouts);
  const pr = await view(checkout, url);
  if (pr.state !== "MERGED") {
    throw new Error(`${prLink(pr)} is ${pr.state === "OPEN" ? "still open" : "closed unmerged"}; nothing changed`);
  }
  const release = lock(common, id, "Done");
  const result = { dir: null, notes: [] };
  try {
    await finishIn({ redmine, id, project, checkout, common, pr, result });
    return result;
  } catch (error) {
    error.result = result;
    throw error;
  } finally {
    release();
  }
}

async function finishIn({ redmine, id, project, checkout, common, pr, result }) {
  const { notes } = result;
  const link = prLink(pr);
  const base = pr.baseRefName;
  const upstream = `origin/${base}`;
  // In full, so a local branch named origin/<base> can't stand in for it.
  const upstreamRef = `refs/remotes/${upstream}`;

  // Every check before the first change, so a tree holding work the PR lacks, or a step bound to
  // fail, stops Done with nothing removed. The PR's own ref is fetched from origin, and the merge
  // looked for there, so they must be one repo: as configured, or as an insteadOf rewrites it.
  const origin = (await git(checkout, "remote", "get-url", "origin")).trim();
  const named = (await git(checkout, "config", "--get", "remote.origin.url")).trim();
  if (![origin, named].some((url) => repoOf(url) === repoOf(repoUrl(pr)))) {
    throw new Error(`${link} isn't in ${checkout}'s origin, ${named}; link #${id}'s PR from its own repo, then Done again`);
  }
  // The issue's worktree is the one on the PR's head branch, whatever its name, so the branch can be
  // deleted; else the one Start would find. A head name counts only from this repo, in a worktree of
  // its own: a fork's, or a long-lived branch such as develop in the main checkout, isn't the issue's.
  const porcelain = await git(checkout, "worktree", "list", "--porcelain");
  const trees = worktrees(porcelain);
  const head = pr.isCrossRepository || pr.headRefName === base ? null : pr.headRefName;
  const found = trees.find((t) => !t.main && head && t.branch === head) ||
    existingWorktree(porcelain, id, worktreeDir(checkout, id), "Done");
  if (found && fs.existsSync(found.dir)) await checkMerged(found.dir, pr, !found.main);
  if (found && !found.main && trees.find((t) => t.dir === found.dir)?.locked) {
    throw new Error(`${found.dir} is locked; run git worktree unlock ${found.dir}, then Done again`);
  }

  // The main checkout moves to the base only from the issue's branch; on any other, Peter's work
  // stays checked out, and only the base is brought up to date, unless another worktree has it.
  const current = (await git(checkout, "branch", "--show-current")).trim();
  const onBase = current === base || found?.main;
  if (current === base) {
    const changes = (await git(checkout, "status", "--porcelain", "--untracked-files=no")).trim();
    if (changes) throw new Error(`${checkout} has changes on ${base}; commit or stash them, then Done again:\n${changes}`);
  }
  const elsewhere = trees.find((t) => !t.main && t.branch === base);
  if (elsewhere && !fs.existsSync(elsewhere.dir)) {
    throw new Error(`git lists ${elsewhere.dir} on ${base}, but the folder is gone; run git worktree prune, then Done again`);
  }
  // git won't switch to a branch another worktree has checked out.
  if (elsewhere && found?.main) {
    throw new Error(`${checkout} is on ${current}, and ${base} is checked out in ${elsewhere.dir}; switch that worktree off ${base}, then Done again`);
  }
  await git(checkout, "fetch", "--quiet", "--prune", "origin");
  await git(checkout, "rev-parse", "--verify", "--quiet", upstreamRef).catch(() => {
    throw new Error(`origin has no ${base}, ${link}'s base, so Done can't bring it up; finish #${id} by hand`);
  });
  await git(checkout, "merge-base", "--is-ancestor", pr.mergeCommit.oid, upstreamRef).catch(() => {
    throw new Error(`${upstream} doesn't hold the merge commit ${pr.mergeCommit.oid.slice(0, 7)} yet; Done again shortly`);
  });
  const tips = await branches(checkout);
  const local = tips.get(base);
  if (local && !elsewhere) {
    await git(checkout, "merge-base", "--is-ancestor", local, upstreamRef).catch(() => {
      throw new Error(`${base} in ${checkout} has commits ${upstream} lacks, so it can't fast-forward; push or drop them, then Done again`);
    });
  }
  await fetchHead(checkout, pr);

  // The base first: git checks a switch or fast-forward whole before it writes a file, so an
  // untracked file in its way refuses Done before the worktree goes.
  const target = (await git(checkout, "rev-parse", upstreamRef)).trim();
  if (onBase) {
    // git overwrites an ignored file, unasked, where the merge adds a tracked one.
    const added = (await git(checkout, "diff", "--name-only", "--diff-filter=A", "HEAD", upstreamRef)).trim().split("\n").filter(Boolean);
    const inWay = added.filter((file) => fs.existsSync(path.join(checkout, file)));
    if (inWay.length) {
      throw new Error(`${checkout} has files where ${upstream} adds tracked ones; move them aside, then Done again:\n${inWay.join("\n")}`);
    }
    let from = local;
    if (current !== base) {
      await git(checkout, "switch", "--quiet", base);
      notes.push(`Switched ${checkout} to ${base}.`);
      from = (await git(checkout, "rev-parse", "HEAD")).trim();
    }
    if (from !== target) {
      await git(checkout, "merge", "--quiet", "--ff-only", upstreamRef);
      notes.push(`Brought ${base} up to the merge.`);
    }
  } else if (elsewhere) {
    // git won't move a branch another worktree has checked out.
    notes.push(`Left ${checkout} on ${current || "a detached HEAD"}; ${base} is checked out in ${elsewhere.dir}, so pull it there, and Redmine sees the merge once you do.`);
  } else if (!local) {
    notes.push(`Left ${checkout} on ${current || "a detached HEAD"}; it has no ${base} to bring up.`);
  } else if (local !== target) {
    // The old value makes git refuse if the base moved since it was checked.
    await git(checkout, "update-ref", `refs/heads/${base}`, target, local);
    notes.push(`Left ${checkout} on ${current || "a detached HEAD"}, and brought ${base} up to date.`);
  }

  if (found && !found.main) {
    // git removes a worktree whose folder was deleted by hand as well, and refuses one with
    // untracked files; ignored ones, such as .tmp/ and .venv, go with it. Start's setup marker
    // goes too, so a reopened issue's next Start sets its new worktree up.
    await git(checkout, "worktree", "remove", found.dir);
    result.dir = found.dir;
    fs.rmSync(settingUp(common, id), { force: true });
    notes.push(`Removed the worktree ${found.dir}.`);
  }

  // git can't see a squash merge, so -d always refuses: -D, once a branch holds nothing the PR
  // lacks. Every branch named for the issue goes, so a Done that stopped after removing a worktree
  // on another of them still finds it, and the found worktree's branch; but not one another
  // worktree has checked out, nor the base, which a stacked PR's or a fork's may be named like.
  const pattern = issueBranch(id);
  const busy = new Map(trees.filter((t) => t.dir !== found?.dir).map((t) => [t.branch, t.dir]));
  for (const [branch, tip] of tips) {
    if (branch === base || !(pattern.test(branch) || (head && branch === found?.branch))) continue;
    if (busy.has(branch)) {
      const dir = busy.get(branch);
      notes.push(fs.existsSync(dir)
        ? `Kept the branch ${branch}: it's checked out in ${dir}.`
        : `Kept the branch ${branch}: git lists it in ${dir}, but the folder is gone; run git worktree prune, then Done again.`);
      continue;
    }
    if (await inPullRequest(checkout, tip, pr)) {
      await git(checkout, "branch", "--quiet", "-D", branch);
      notes.push(`Deleted the branch ${branch}.`);
    } else {
      notes.push(`Kept the branch ${branch}: it's at ${tip.slice(0, 7)}, with commits ${link}'s merged head lacks.`);
    }
  }

  // Redmine reads the local branches of this .git, so once the base holds the merge, a fixes #<n>
  // commit closes the issue here. A failed fetch, or a base left to another worktree, leaves the
  // close to the API, and the commits to the next fetch.
  try {
    await redmine.fetchCommits(project);
  } catch (error) {
    notes.push(`Redmine didn't fetch the commits: ${error.message}.`);
  }

  const issue = await redmine.issue(id);
  const noted = (issue.journals || []).some((j) => (j.notes || "").includes(commitUrl(pr)));
  const fields = {};
  if (!issue.status.is_closed) fields.status_id = await redmine.statusId("Closed");
  if (!noted) fields.notes = doneNote(pr);
  if (Object.keys(fields).length) {
    await redmine.update(id, fields);
    // Redmine answers 204 and drops a status it won't allow (docs/failure-modes.md), so the close is
    // read back; the note is saved either way, and the next Done sends only the status.
    if (fields.status_id) {
      const after = await redmine.issue(id);
      if (!after.status.is_closed) {
        throw new Error(`Redmine kept #${id} ${after.status.name}: an open issue blocks it, or it has an open subtask${fields.notes ? "; the note was saved" : ""}. Close those, then Done again`);
      }
      notes.push(`#${id} is Closed.`);
    } else {
      notes.push(`Added the note to #${id}.`);
    }
  }
}

module.exports = { linkedPullRequest, doneNote, finish };
