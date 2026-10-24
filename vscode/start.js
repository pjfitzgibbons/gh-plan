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

function checkId(id, action = "Start") {
  if (!Number.isInteger(id) || id <= 0) throw new Error(`${action} needs an issue number, not ${JSON.stringify(id)}`);
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

// Each worktree git lists: { dir, branch, main, locked }, the main checkout first; branch is null on
// a detached HEAD.
function worktrees(porcelain) {
  return porcelain.trim().split("\n\n").map((block, i) => {
    const lines = block.split("\n");
    const branch = lines.find((l) => l.startsWith("branch refs/heads/"))?.slice("branch refs/heads/".length);
    const locked = lines.some((l) => l === "locked" || l.startsWith("locked "));
    return { dir: lines[0].replace(/^worktree /, ""), branch: branch || null, main: i === 0, locked };
  });
}

// The worktree already holding a branch for issue id, or at dir, where Start puts it, whatever its
// HEAD: a rebase or bisect detaches it, and branch is then null. main: it's the main checkout,
// listed first. action names the command a refusal tells Peter to run again.
function existingWorktree(porcelain, id, dir, action = "Start") {
  const pattern = issueBranch(id);
  for (const tree of worktrees(porcelain)) {
    const branch = tree.branch && pattern.test(tree.branch) ? tree.branch : null;
    if (!branch && !tree.main && tree.dir === dir && tree.branch) {
      throw new Error(`${tree.dir} is checked out on ${tree.branch}, not a branch for #${id}; switch it back, then ${action} again`);
    }
    if (branch || (!tree.main && tree.dir === dir)) return { dir: tree.dir, branch, main: tree.main };
  }
  return null;
}

function expandHome(dir) {
  return dir.replace(/^~(?=$|\/)/, os.homedir());
}

// Runs command in cwd; resolves to its output. With a timeout, in ms, a command still running then
// is killed and refused as timed out.
function run(command, args, cwd, timeout = 0) {
  return new Promise((resolve, reject) => {
    const refuse = (why) => reject(new Error(`${command} ${args.join(" ")}: ${why}`));
    // execFile kills the command at its timeout but answers only once its output closes, which a
    // process the command started can hold open, so the timeout refuses it either way.
    const timer = timeout && setTimeout(() => refuse(`timed out after ${timeout / 1000}s`), timeout);
    execFile(command, args, { cwd, timeout }, (error, stdout, stderr) => {
      clearTimeout(timer);
      const late = timeout && error?.killed && error.code !== "ERR_CHILD_PROCESS_STDIO_MAXBUFFER";
      if (error) refuse(late ? `timed out after ${timeout / 1000}s` : (stderr || error.message || "").trim());
      else resolve(stdout);
    });
  });
}
const git = (cwd, ...args) => run("git", args, cwd);

// One Start or Done per issue across windows, so Open and Done on the same row exclude each
// other: an exclusive file in the repo's git dir, holding the pid of the extension host that took
// it and its action. One left by a crash is never taken over, since two windows taking it at once
// could both win; the refusal names it for Peter to delete. Returns its release.
const DOING = { Start: "started", Done: "finished" };

function lock(common, id, action = "Start") {
  const file = path.join(common, `plan-busy-${id}`);
  try {
    fs.writeFileSync(file, `${process.pid} ${action}\n`, { flag: "wx" });
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    // An empty file is one being written, so its holder is alive.
    const [pid, held = action] = fs.readFileSync(file, "utf8").trim().split(" ");
    if (!Number.isInteger(Number(pid || NaN)) || alive(Number(pid))) {
      throw new Error(`#${id} is being ${DOING[held] || DOING[action]} in another window; if none is, delete ${file}`);
    }
    throw new Error(`a ${DOING[held] ? held : action} of #${id} that stopped without finishing left ${file}; delete it, then ${action} again`);
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

// cocoindex-code keeps a project's code index in <root>/.cocoindex_code/, and finds the project by
// walking up to its settings.yml. A worktree beside the main checkout is its own project: with the
// main checkout's settings, its first search indexes the branch's own files.
const INDEX = ".cocoindex_code";
const INDEX_SETTINGS = path.join(INDEX, "settings.yml");
const onPath = (command) => (process.env.PATH || "").split(path.delimiter).filter(Boolean).some((dir) => {
  try {
    fs.accessSync(path.join(dir, command), fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
});

// Gives dir the checkout's code index settings, never its databases, which the one ccc daemon may be
// writing. Returns the note saying what it did.
async function enableIndex(checkout, dir) {
  if (!fs.existsSync(path.join(checkout, INDEX_SETTINGS))) return `No code index in ${checkout} to set up from.`;
  if (!onPath("ccc")) return "No code index: ccc isn't on PATH.";
  // Not ignored, the index would be untracked files that Go stages and Ship commits.
  if (!(await git(dir, "check-ignore", "--quiet", `${INDEX}/`).then(() => true, () => false))) {
    return `No code index: ${INDEX}/ isn't ignored in ${dir}.`;
  }
  try {
    fs.mkdirSync(path.join(dir, INDEX), { recursive: true });
    fs.copyFileSync(path.join(checkout, INDEX_SETTINGS), path.join(dir, INDEX_SETTINGS));
  } catch (error) {
    return `No code index: ${error.message}.`;
  }
  return "The worktree's code index builds on its first ccc search, or with ccc index.";
}

// The daemon keeps a project's databases open once it has indexed it, and goes on writing to them
// once they're deleted, so a worktree made again at the same path would search a stale index. ccc
// reset has it let go before Done removes the worktree. Only a worktree that has built its index:
// on one that hasn't, reset would start a daemon to do nothing. null, or the note saying it didn't.
async function releaseIndex(dir) {
  if (!fs.existsSync(path.join(dir, INDEX, "target_sqlite.db"))) return null;
  if (!onPath("ccc")) return `ccc isn't on PATH, so its daemon may still hold ${dir}'s code index; run ccc daemon restart.`;
  return run("ccc", ["reset", "-f"], dir, 60 * 1000)
    .then(() => null, (error) => `The ccc daemon may still hold ${dir}'s code index: ${error.message}; run ccc daemon restart.`);
}

// The issue's project, and the root and git dir of its main checkout, whatever folder of it (or of
// a worktree) the map names: worktrees go beside it, never in its tree. git names worktrees by
// their real path, and the new window is matched to its handoff by it.
async function mainCheckout(redmine, issue, checkouts) {
  const project = await redmine.projectIdentifier(issue.project.id);
  if (!checkouts[project]) throw new Error(`no checkout for Redmine project "${project}"; add it to plan.checkouts`);
  const named = expandHome(checkouts[project]);
  if (!fs.existsSync(named)) throw new Error(`the checkout for "${project}" isn't there: ${named}`);
  const common = (await git(named, "rev-parse", "--path-format=absolute", "--git-common-dir")).trim();
  return { project, checkout: fs.realpathSync(path.dirname(common)), common };
}

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
  const { checkout, common } = await mainCheckout(redmine, issue, checkouts);

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
  if (started) notes.push(await enableIndex(checkout, found.dir));

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

module.exports = { ACTION, checkId, slug, branchName, worktreeDir, blockerIds, issueBranch, worktrees, existingWorktree, expandHome, run, git, lock, settingUp, enableIndex, releaseIndex, mainCheckout, start };
