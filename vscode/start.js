// Starting an issue: its branch and worktree off the latest base, then In Progress in Redmine. No
// vscode import: the names and the blocker filter are plain functions, tested under node --test,
// and start() takes its questions to Peter as a callback.
const { execFile } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PREFIX = { Feature: "feat", Bug: "fix", Support: "chore" };

// The subject's first five words, lowercase, joined by hyphens, as worktask made them.
function slug(subject) {
  return subject.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean).slice(0, 5).join("-");
}

// <type>/<n>-<slug>; a tracker with no prefix gets none.
function branchName(issue) {
  const prefix = PREFIX[issue.tracker.name];
  return `${prefix ? `${prefix}/` : ""}${issue.id}-${slug(issue.subject)}`;
}

// Beside the checkout: <parent>/<repo>.worktrees/<n>.
function worktreeDir(checkout, id) {
  return path.join(path.dirname(checkout), `${path.basename(checkout)}.worktrees`, String(id));
}

// The issues that block this one. Redmine keeps only "blocks", from the blocker to the blocked.
function blockerIds(issue) {
  return (issue.relations || [])
    .filter((r) => r.relation_type === "blocks" && r.issue_to_id === issue.id)
    .map((r) => r.issue_id);
}

// The worktree already holding a branch for issue id, whatever its slug became.
function existingWorktree(porcelain, id) {
  const pattern = new RegExp(`^branch refs/heads/(?:[\\w-]+/)?${id}-`);
  for (const block of porcelain.split("\n\n")) {
    const lines = block.split("\n");
    if (lines.some((line) => pattern.test(line))) {
      return { dir: lines[0].replace(/^worktree /, ""), branch: lines.find((l) => pattern.test(l)).slice(18) };
    }
  }
  return null;
}

function expandHome(dir) {
  return dir.replace(/^~(?=$|\/)/, os.homedir());
}

function run(command, args, cwd) {
  return new Promise((resolve, reject) => {
    execFile(command, args, { cwd }, (error, stdout, stderr) => {
      if (error) reject(new Error(`${command} ${args.join(" ")}: ${(stderr || error.message).trim()}`));
      else resolve(stdout);
    });
  });
}
const git = (cwd, ...args) => run("git", args, cwd);

// Written in the worktree's own git dir, outside its tree, once its setup has all succeeded.
const SET_UP = "plan-set-up";

// Start issue id, or reopen its worktree. Safe to repeat: each step runs only when it has something
// to do. confirm(blockers) is asked only before a new worktree; false stops with nothing changed.
// Returns { dir, branch, created, started, notes }, or null when Peter said no. started: this Start
// finished the worktree's setup, here or after an earlier one stopped part way.
async function start({ redmine, checkouts, id, confirm }) {
  if (!Number.isInteger(id) || id <= 0) throw new Error(`Start needs an issue number, not ${JSON.stringify(id)}`);
  const issue = await redmine.issue(id);
  const project = await redmine.projectIdentifier(issue.project.id);
  if (!checkouts[project]) throw new Error(`no checkout for Redmine project "${project}"; add it to plan.checkouts`);
  const named = expandHome(checkouts[project]);
  if (!fs.existsSync(named)) throw new Error(`the checkout for "${project}" isn't there: ${named}`);
  // The main checkout's root, whatever folder of it (or of a worktree) the map names: worktrees go
  // beside it, never in its tree. git names worktrees by their real path, and the new window is
  // matched to its handoff by it.
  const common = (await git(named, "rev-parse", "--path-format=absolute", "--git-common-dir")).trim();
  const checkout = fs.realpathSync(path.dirname(common));

  const notes = [];
  let found = existingWorktree(await git(checkout, "worktree", "list", "--porcelain"), id);
  let created = false;
  if (found && !fs.existsSync(found.dir)) {
    throw new Error(`git still lists ${found.dir} as a worktree, but it's gone; run git worktree prune in ${checkout}, then Start again`);
  }
  if (!found) {
    const blockers = [];
    for (const blocker of blockerIds(issue)) {
      const other = await redmine.issue(blocker);
      if (!other.status.is_closed) blockers.push({ id: other.id, subject: other.subject, status: other.status.name });
    }
    if (blockers.length && !(await confirm(blockers))) return null;

    // A branch starts from the latest base or not at all; a failed fetch stops before anything changes.
    await git(checkout, "fetch", "--quiet", "origin");
    let base = "main";
    await git(checkout, "show-ref", "--verify", "--quiet", "refs/remotes/origin/develop").then(() => (base = "develop"), () => {});
    const dir = worktreeDir(checkout, id);
    if (!path.relative(checkout, dir).startsWith("..")) throw new Error(`${dir} is inside ${checkout}; worktrees go beside it`);
    if (fs.existsSync(dir)) throw new Error(`${dir} exists but isn't a worktree of ${checkout}`);
    fs.mkdirSync(path.dirname(dir), { recursive: true });
    // A branch left without its worktree (say, by a removed worktree) is reused, not made again.
    const local = (await git(checkout, "for-each-ref", "--format=%(refname:short)", "refs/heads"))
      .split("\n").find((name) => new RegExp(`^(?:[\\w-]+/)?${id}-`).test(name));
    const branch = local || branchName(issue);
    if (local) await git(checkout, "worktree", "add", "--quiet", dir, branch);
    else await git(checkout, "worktree", "add", "--quiet", "--no-track", "-b", branch, dir, `origin/${base}`);
    found = { dir, branch };
    created = true;
    notes.push(`Made ${branch} from origin/${base} in ${dir}.`);
  }

  // A sync or update that failed part way can leave .venv behind or the status unchanged, so setup
  // is done only once the marker, written last, is there.
  const marker = path.join((await git(found.dir, "rev-parse", "--path-format=absolute", "--git-dir")).trim(), SET_UP);
  const started = !fs.existsSync(marker);
  if (started && fs.existsSync(path.join(found.dir, "uv.lock"))) {
    await run("uv", ["sync", "--quiet"], found.dir);
    notes.push("Synced the uv environment.");
  }

  if (issue.status.name !== "In Progress") {
    await redmine.update(id, {
      status_id: await redmine.statusId("In Progress"),
      ...(issue.start_date ? {} : { start_date: new Date().toLocaleDateString("sv") }),
      notes: `Started on \`${found.branch}\`, in ${found.dir}.`,
    });
    notes.push(`#${id} is In Progress.`);
  }
  if (started) fs.writeFileSync(marker, `${new Date().toISOString()}\n`);
  return { ...found, created, started, notes };
}

module.exports = { slug, branchName, worktreeDir, blockerIds, existingWorktree, expandHome, start };
