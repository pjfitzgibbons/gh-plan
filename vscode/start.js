// Starting an issue: its branch and worktree off the latest base, then In Progress in Redmine. No
// vscode import: the names and the blocker filter are plain functions, tested under node --test,
// and start() takes its questions to Peter as a callback.
const { execFile } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PREFIX = { Feature: "feat", Bug: "fix", Support: "chore" };

// The statuses Start accepts, with the label of their row's button.
const ACTION = { New: "Start", "In Progress": "Open" };

function checkId(id) {
  if (!Number.isInteger(id) || id <= 0) throw new Error(`Start needs an issue number, not ${JSON.stringify(id)}`);
}

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

// A branch name for issue id, whatever its slug became: <n>-… with a tracker's prefix or none, so
// release/2026-10 is no branch of #2026.
function issueBranch(id) {
  return new RegExp(`^(?:(?:${Object.values(PREFIX).join("|")})/)?${id}-`);
}

// The worktree already holding a branch for issue id, or at dir, where Start puts it, whatever its
// HEAD: a rebase or bisect detaches it, and branch is then null. main: it's the main checkout,
// listed first.
function existingWorktree(porcelain, id, dir) {
  const pattern = issueBranch(id);
  for (const [i, block] of porcelain.split("\n\n").entries()) {
    const lines = block.split("\n");
    const at = lines[0].replace(/^worktree /, "");
    const headBranch = lines.find((l) => l.startsWith("branch refs/heads/"))?.slice("branch refs/heads/".length);
    const branch = headBranch && pattern.test(headBranch) ? headBranch : null;
    if (!branch && i > 0 && at === dir && headBranch) {
      throw new Error(`${at} is checked out on ${headBranch}, not a branch for #${id}; switch it back, then Start again`);
    }
    if (branch || (i > 0 && at === dir)) return { dir: at, branch: branch || null, main: i === 0 };
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

// One Start per issue across windows: an exclusive file in the repo's git dir, holding the pid of
// the extension host that took it. One left by a crash is never taken over, since two windows
// taking it at once could both win; the refusal names it for Peter to delete. Returns its release.
function lock(common, id) {
  const file = path.join(common, `plan-starting-${id}`);
  try {
    fs.writeFileSync(file, `${process.pid}\n`, { flag: "wx" });
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    // An empty file is one being written, so its holder is alive.
    const pid = Number(fs.readFileSync(file, "utf8").trim() || NaN);
    if (!Number.isInteger(pid) || alive(pid)) throw new Error(`#${id} is being started in another window; if none is, delete ${file}`);
    throw new Error(`a Start of #${id} that stopped without finishing left ${file}; delete it, then Start again`);
  }
  return () => fs.rmSync(file, { force: true });
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

// In the repo's git dir, outside every tree: written just before Start makes an issue's worktree,
// removed if that fails, and removed once its setup has all succeeded. A worktree without it is set up, including one made
// before this marker or by another tool, so Open leaves it alone.
const settingUp = (common, id) => path.join(common, `plan-setting-up-${id}`);

// Start issue id, or reopen its worktree. Safe to repeat: each step runs only when it has something
// to do. confirm(blockers) is asked only before a new worktree; false stops with nothing changed.
// handOff(result), when given, is the last step of setup, so a handoff that fails runs setup again.
// Returns { dir, branch, created, started, notes }, or null when Peter said no. started: this Start
// finished the worktree's setup, here or after an earlier one stopped part way.
async function start({ redmine, checkouts, id, confirm, handOff = () => {} }) {
  checkId(id);
  const issue = await redmine.issue(id);
  // The panel's row can be older than the issue: one closed, iceboxed or moved on since keeps its status.
  if (!ACTION[issue.status.name]) throw new Error(`#${id} is ${issue.status.name}; refresh Next up`);
  const project = await redmine.projectIdentifier(issue.project.id);
  if (!checkouts[project]) throw new Error(`no checkout for Redmine project "${project}"; add it to plan.checkouts`);
  const named = expandHome(checkouts[project]);
  if (!fs.existsSync(named)) throw new Error(`the checkout for "${project}" isn't there: ${named}`);
  // The main checkout's root, whatever folder of it (or of a worktree) the map names: worktrees go
  // beside it, never in its tree. git names worktrees by their real path, and the new window is
  // matched to its handoff by it.
  const common = (await git(named, "rev-parse", "--path-format=absolute", "--git-common-dir")).trim();
  const checkout = fs.realpathSync(path.dirname(common));

  const release = lock(common, id);
  try {
    return await startIn({ redmine, id, confirm, handOff, issue, checkout, common });
  } finally {
    release();
  }
}

async function startIn({ redmine, id, confirm, handOff, issue, checkout, common }) {
  const notes = [];
  const dir = worktreeDir(checkout, id);
  let found = existingWorktree(await git(checkout, "worktree", "list", "--porcelain"), id, dir);
  let created = false;
  if (found?.main) {
    throw new Error(`${found.branch} is checked out in the main checkout, ${checkout}; switch that off it, then Start again`);
  }
  if (found && !fs.existsSync(found.dir)) {
    throw new Error(`git still lists ${found.dir} as a worktree, but it's gone; run git worktree prune in ${checkout}, then Start again`);
  }
  if (!found) {
    // A blocker Peter can't read, deleted or in a project hidden from him, is listed as unknown.
    const blockers = (await Promise.all(blockerIds(issue).map((blocker) => redmine.issue(blocker).then(
      (other) => !other.status.is_closed && { id: other.id, subject: other.subject, status: other.status.name },
      () => ({ id: blocker, subject: "(couldn't read it)", status: "unknown" })
    )))).filter(Boolean);
    if (blockers.length && !(await confirm(blockers))) return null;

    // A new branch starts from the latest base or not at all; a failed fetch stops before anything changes.
    // --prune drops a branch origin has deleted, so a gone develop is no base.
    await git(checkout, "fetch", "--quiet", "--prune", "origin");
    // develop, else origin's default branch, else main.
    let base = "main";
    await git(checkout, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").then((ref) => (base = ref.trim().replace(/^origin\//, "")), () => {});
    await git(checkout, "show-ref", "--verify", "--quiet", "refs/remotes/origin/develop").then(() => (base = "develop"), () => {});
    if (fs.existsSync(dir)) throw new Error(`${dir} exists but isn't a worktree of ${checkout}`);
    fs.mkdirSync(path.dirname(dir), { recursive: true });
    // The issue's branch, if it has one, here or pushed from elsewhere, is reused as it is: it may
    // hold work, so it keeps its own base.
    const refs = async (prefix) => (await git(checkout, "for-each-ref", "--format=%(refname:short)", prefix))
      .split("\n").map((name) => name.replace(/^origin\//, "")).find((name) => issueBranch(id).test(name));
    const local = await refs("refs/heads");
    const pushed = !local && (await refs("refs/remotes/origin"));
    const branch = local || pushed || branchName(issue);
    fs.writeFileSync(settingUp(common, id), `${new Date().toISOString()}\n`);
    try {
      if (local) {
        await git(checkout, "worktree", "add", "--quiet", dir, branch);
        notes.push(`Reused the branch ${branch}, on its own base, in ${dir}.`);
      } else if (pushed) {
        await git(checkout, "worktree", "add", "--quiet", "--track", "-b", branch, dir, `origin/${branch}`);
        notes.push(`Checked out ${branch} from origin in ${dir}.`);
      } else {
        await git(checkout, "worktree", "add", "--quiet", "--no-track", "-b", branch, dir, `origin/${base}`);
        notes.push(`Made ${branch} from origin/${base} in ${dir}.`);
      }
    } catch (error) {
      // A failed hook leaves the worktree made, and its setup still to finish. With no worktree,
      // there's no setup to finish, and one made later by hand is set up.
      const left = await git(checkout, "worktree", "list", "--porcelain").then((list) => existingWorktree(list, id, dir), () => true);
      if (!left) fs.rmSync(settingUp(common, id), { force: true });
      throw error;
    }
    found = { dir, branch };
    created = true;
  }

  // A sync or update that failed part way leaves the marker, so the next Start runs setup again.
  const marker = settingUp(common, id);
  const started = fs.existsSync(marker);
  if (started && fs.existsSync(path.join(found.dir, "uv.lock"))) {
    await run("uv", ["sync", "--quiet"], found.dir);
    notes.push("Synced the uv environment.");
  }

  if (issue.status.name === "New") {
    await redmine.update(id, {
      status_id: await redmine.statusId("In Progress"),
      ...(issue.start_date ? {} : { start_date: new Date().toLocaleDateString("sv") }),
      notes: `Started${found.branch ? ` on \`${found.branch}\`` : ""}, in ${found.dir}.`,
    });
    notes.push(`#${id} is In Progress.`);
  }
  const result = { ...found, created, started, notes };
  if (started) {
    await handOff(result);
    fs.rmSync(marker, { force: true });
  }
  return result;
}

module.exports = { ACTION, checkId, slug, branchName, worktreeDir, blockerIds, issueBranch, existingWorktree, expandHome, run, git, start };
