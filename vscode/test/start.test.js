// node --test vscode/test: the names Start makes and the blockers it warns on, without Redmine or git.
const test = require("node:test");
const assert = require("node:assert/strict");
const { slug, branchName, worktreeDir, blockerIds, existingWorktree, start } = require("../start");
const { Redmine } = require("../redmine");

test("the slug is the subject's first five words", () => {
  assert.equal(slug("Start an issue from the Next up panel"), "start-an-issue-from-the");
  assert.equal(slug("Redmine: backfill  history, PR links!"), "redmine-backfill-history-pr-links");
});

test("each tracker names its branch prefix", () => {
  const subject = "Next up view";
  assert.equal(branchName({ id: 83, subject, tracker: { name: "Feature" } }), "feat/83-next-up-view");
  assert.equal(branchName({ id: 83, subject, tracker: { name: "Bug" } }), "fix/83-next-up-view");
  assert.equal(branchName({ id: 83, subject, tracker: { name: "Support" } }), "chore/83-next-up-view");
  assert.equal(branchName({ id: 83, subject, tracker: { name: "Epic" } }), "83-next-up-view");
});

test("the worktree sits beside the checkout", () => {
  assert.equal(worktreeDir("/work/gh-plan", 80), "/work/gh-plan.worktrees/80");
  assert.equal(worktreeDir("/work/tempotag", 7), "/work/tempotag.worktrees/7");
});

test("only issues that block this one are blockers", () => {
  const issue = {
    id: 80,
    relations: [
      { issue_id: 50, issue_to_id: 80, relation_type: "blocks" },
      { issue_id: 80, issue_to_id: 90, relation_type: "blocks" },
      { issue_id: 60, issue_to_id: 80, relation_type: "precedes" },
      { issue_id: 70, issue_to_id: 80, relation_type: "relates" },
    ],
  };
  assert.deepEqual(blockerIds(issue), [50]);
  assert.deepEqual(blockerIds({ id: 80 }), []);
});

test("an issue's worktree is found by number, whatever its slug", () => {
  const porcelain = [
    "worktree /work/gh-plan\nHEAD 1111\nbranch refs/heads/main",
    "worktree /work/gh-plan.worktrees/80\nHEAD 2222\nbranch refs/heads/feat/80-old-slug",
    "worktree /work/gh-plan.worktrees/800\nHEAD 3333\nbranch refs/heads/feat/800-other",
  ].join("\n\n");
  assert.deepEqual(existingWorktree(porcelain, 80), { dir: "/work/gh-plan.worktrees/80", branch: "feat/80-old-slug", main: false });
  assert.equal(existingWorktree(porcelain, 8), null);
  assert.equal(existingWorktree(porcelain, 81), null);
});

test("an issue's branch checked out in the main checkout is marked main; another prefix isn't an issue's", () => {
  const porcelain = [
    "worktree /work/gh-plan\nHEAD 1111\nbranch refs/heads/fix/80-in-main",
    "worktree /work/gh-plan.worktrees/x\nHEAD 2222\nbranch refs/heads/release/2026-10",
  ].join("\n\n");
  assert.deepEqual(existingWorktree(porcelain, 80), { dir: "/work/gh-plan", branch: "fix/80-in-main", main: true });
  assert.equal(existingWorktree(porcelain, 2026), null);
});

test("a message that isn't an issue number is refused before anything runs", async () => {
  const redmine = { issue: () => assert.fail("Redmine was asked") };
  for (const id of ["80", 0, -1, 1.5, null, "80; rm -rf /"]) {
    await assert.rejects(start({ redmine, checkouts: {}, id, confirm: () => true }), /needs an issue number/);
  }
});

// A throwaway origin and checkout, and a Redmine that records what it's told.
function sandbox(t) {
  const { execFileSync } = require("child_process");
  const fs = require("fs");
  const os = require("os");
  const path = require("path");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "plan-start-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const git = (cwd, ...args) => execFileSync("git", args, { cwd, encoding: "utf8" });
  git(root, "init", "--quiet", "--bare", "-b", "main", "origin.git");
  git(root, "clone", "--quiet", "origin.git", "repo");
  const checkout = fs.realpathSync(path.join(root, "repo"));
  git(checkout, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--quiet", "--allow-empty", "-m", "first");
  git(checkout, "push", "--quiet", "origin", "main");
  const issues = {
    80: { id: 80, subject: "Start from the panel", tracker: { name: "Feature" }, project: { id: 2 },
          status: { name: "New", is_closed: false }, relations: [{ issue_id: 50, issue_to_id: 80, relation_type: "blocks" }] },
    50: { id: 50, subject: "Port the skills", status: { name: "New", is_closed: false } },
  };
  const updates = [];
  const redmine = {
    issue: async (id) => issues[id],
    projectIdentifier: async () => "demo",
    statusId: async () => 2,
    update: async (id, fields) => {
      updates.push({ id, ...fields });
      issues[id].status = { name: "In Progress", is_closed: false };
    },
  };
  // The map names the checkout through the temp folder's symlink; git and Start use its real path.
  return { checkout, git, redmine, updates, issues, checkouts: { demo: path.join(root, "repo") } };
}

test("Start warns on an open blocker, and No changes nothing", async (t) => {
  const s = sandbox(t);
  const asked = [];
  const result = await start({ ...s, id: 80, confirm: async (b) => (asked.push(b), false) });
  assert.equal(result, null);
  assert.deepEqual(asked, [[{ id: 50, subject: "Port the skills", status: "New" }]]);
  assert.equal(s.git(s.checkout, "branch", "--list", "feat/80-*"), "");
  assert.deepEqual(s.updates, []);
});

test("Start makes the branch and worktree once, and a second Start changes nothing", async (t) => {
  const s = sandbox(t);
  s.issues[50].status = { name: "Closed", is_closed: true };
  const confirm = () => assert.fail("asked with no open blocker");
  const first = await start({ ...s, id: 80, confirm });
  assert.equal(first.branch, "feat/80-start-from-the-panel");
  assert.equal(first.dir, worktreeDir(s.checkout, 80));
  assert.equal(first.created, true);
  assert.equal(first.started, true);
  assert.equal(s.git(first.dir, "branch", "--show-current").trim(), first.branch);
  assert.equal(s.updates.length, 1);
  assert.equal(s.updates[0].status_id, 2);
  assert.match(s.updates[0].notes, /feat\/80-start-from-the-panel/);

  const second = await start({ ...s, id: 80, confirm });
  assert.deepEqual({ dir: second.dir, branch: second.branch, created: second.created, started: second.started }, { dir: first.dir, branch: first.branch, created: false, started: false });
  assert.deepEqual(second.notes, []);
  assert.equal(s.updates.length, 1);
});

test("Start on a subtask of a closed parent stops with Redmine's refusal and no handoff, every time", async (t) => {
  const s = sandbox(t);
  s.issues[50].status = { name: "Closed", is_closed: true };
  s.issues[80].status = { id: 1, name: "New", is_closed: false };
  // Redmine's update, against a Redmine that keeps the note and drops the status, as 7.0.2 does
  // for a subtask whose parent is closed.
  Object.assign(s.redmine, {
    update: Redmine.prototype.update,
    statusNames: new Map([[2, "In Progress"]]),
    send: async (method, path, { issue: fields }) => s.updates.push({ path, ...fields }),
    get: async (path) => ({ issue: s.issues[Number(path.match(/\d+/)[0])] }),
  });
  const handOff = () => assert.fail("handed off to Claude");
  for (const attempt of [1, 2]) {
    await assert.rejects(start({ ...s, id: 80, confirm: () => true, handOff }), /^Error: Redmine kept #80 New, not In Progress\. .*the note was saved\./);
    assert.equal(s.updates.length, attempt, "each Start sends the status and note once");
  }
  assert.equal(s.issues[80].status.name, "New");
});

test("a Start stopped after the worktree finishes on the next Start", async (t) => {
  const s = sandbox(t);
  s.issues[50].status = { name: "Closed", is_closed: true };
  const update = s.redmine.update;
  s.redmine.update = async () => { throw new Error("Redmine can't be reached"); };
  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), /can't be reached/);
  s.redmine.update = update;
  const again = await start({ ...s, id: 80, confirm: () => true });
  assert.equal(again.created, false);
  assert.equal(again.started, true, "the resumed Start hands over to Claude");
  assert.deepEqual(again.notes, ["#80 is In Progress."]);
});

test("a project with no checkout is refused by name", async (t) => {
  const s = sandbox(t);
  await assert.rejects(start({ ...s, checkouts: {}, id: 80, confirm: () => true }), /no checkout for Redmine project "demo"/);
});

test("the worktree goes beside the repo root, even when the map names a folder inside it", async (t) => {
  const s = sandbox(t);
  const fs = require("fs");
  const path = require("path");
  s.issues[50].status = { name: "Closed", is_closed: true };
  const inside = path.join(s.checkout, "vscode");
  fs.mkdirSync(inside);
  const result = await start({ ...s, checkouts: { demo: inside }, id: 80, confirm: () => true });
  assert.equal(result.dir, path.join(path.dirname(s.checkout), "repo.worktrees", "80"));
  assert.ok(path.relative(s.checkout, result.dir).startsWith(".."), `${result.dir} is inside ${s.checkout}`);
});

// A uv that records each run, makes .venv, and fails the first time, as a broken install would.
function failingUv(t, s) {
  const fs = require("fs");
  const path = require("path");
  const bin = fs.mkdtempSync(path.join(path.dirname(s.checkout), "bin-"));
  const log = path.join(bin, "runs");
  fs.writeFileSync(path.join(bin, "uv"), `#!/bin/sh\necho run >> "${log}"\nmkdir -p .venv\n[ -f "${log}.ok" ] || { touch "${log}.ok"; exit 1; }\n`, { mode: 0o755 });
  const PATH = process.env.PATH;
  process.env.PATH = `${bin}:${PATH}`;
  t.after(() => (process.env.PATH = PATH));
  fs.writeFileSync(path.join(s.checkout, "uv.lock"), "");
  s.git(s.checkout, "add", "uv.lock");
  s.git(s.checkout, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--quiet", "-m", "uv.lock");
  s.git(s.checkout, "push", "--quiet", "origin", "main");
  return () => fs.readFileSync(log, "utf8").split("\n").filter(Boolean).length;
}

for (const status of ["New", "In Progress"]) {
  test(`a sync that left .venv behind runs again on the next Start, for a ${status} issue`, async (t) => {
    const s = sandbox(t);
    s.issues[50].status = { name: "Closed", is_closed: true };
    s.issues[80].status = { name: status, is_closed: false };
    const runs = failingUv(t, s);
    const confirm = () => true;

    await assert.rejects(start({ ...s, id: 80, confirm }), /uv sync/);
    assert.deepEqual(s.updates, []);
    const again = await start({ ...s, id: 80, confirm });
    assert.equal(runs(), 2);
    assert.equal(again.created, false);
    assert.equal(again.started, true, "the resumed Start hands over to Claude");
    assert.ok(again.notes.includes("Synced the uv environment."));
    assert.equal(s.updates.length, status === "New" ? 1 : 0);
    // Set up now: reopening syncs no more and starts nothing.
    const reopened = await start({ ...s, id: 80, confirm });
    assert.equal(runs(), 2);
    assert.equal(reopened.started, false);
  });
}

test("a worktree deleted by hand is refused before Redmine changes", async (t) => {
  const s = sandbox(t);
  const fs = require("fs");
  s.issues[50].status = { name: "Closed", is_closed: true };
  const first = await start({ ...s, id: 80, confirm: () => true });
  fs.rmSync(first.dir, { recursive: true });
  s.issues[80].status = { name: "New", is_closed: false };
  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), /git worktree prune/);
  assert.equal(s.updates.length, 1);
});

test("a failed fetch makes no branch", async (t) => {
  const s = sandbox(t);
  s.issues[50].status = { name: "Closed", is_closed: true };
  s.git(s.checkout, "remote", "set-url", "origin", "/nonexistent/origin.git");
  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), /git fetch/);
  assert.equal(s.git(s.checkout, "branch", "--list", "feat/80-*"), "");
  assert.deepEqual(s.updates, []);
});

test("a Start on an issue closed since the panel loaded changes nothing", async (t) => {
  const s = sandbox(t);
  s.issues[80].status = { name: "Closed", is_closed: true };
  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), /#80 is Closed; refresh Next up/);
  assert.equal(s.git(s.checkout, "branch", "--list", "feat/80-*"), "");
  assert.deepEqual(s.updates, []);
});

test("a Start on an issue iceboxed since the panel loaded changes nothing", async (t) => {
  const s = sandbox(t);
  s.issues[80].status = { name: "Icebox", is_closed: false };
  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), /#80 is Icebox; refresh Next up/);
  assert.equal(s.git(s.checkout, "branch", "--list", "feat/80-*"), "");
  assert.deepEqual(s.updates, []);
});

test("Open on a worktree Start didn't make syncs nothing and starts nothing", async (t) => {
  const s = sandbox(t);
  const path = require("path");
  s.issues[80].status = { name: "In Progress", is_closed: false };
  const dir = worktreeDir(s.checkout, 80);
  s.git(s.checkout, "worktree", "add", "--quiet", "-b", "feat/80-by-hand", dir, "main");
  const runs = failingUv(t, s);
  const result = await start({ ...s, id: 80, confirm: () => assert.fail("asked about blockers") });
  assert.deepEqual({ dir: result.dir, branch: result.branch, created: result.created, started: result.started }, { dir: path.resolve(dir), branch: "feat/80-by-hand", created: false, started: false });
  assert.deepEqual(result.notes, []);
  assert.throws(runs, /ENOENT/, "uv never ran");
  assert.deepEqual(s.updates, []);
});

test("an issue branch pushed from elsewhere is checked out, tracking origin", async (t) => {
  const s = sandbox(t);
  s.issues[50].status = { name: "Closed", is_closed: true };
  s.git(s.checkout, "push", "--quiet", "origin", "main:fix/80-pushed-elsewhere");
  const result = await start({ ...s, id: 80, confirm: () => true });
  assert.equal(result.branch, "fix/80-pushed-elsewhere");
  assert.equal(s.git(result.dir, "rev-parse", "--abbrev-ref", "@{upstream}").trim(), "origin/fix/80-pushed-elsewhere");
  assert.ok(result.notes.includes(`Checked out fix/80-pushed-elsewhere from origin in ${result.dir}.`));
  assert.equal(result.started, true);
});

test("an issue branch checked out in the main checkout is refused by name", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "switch", "--quiet", "-c", "feat/80-in-main");
  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), /feat\/80-in-main is checked out in the main checkout/);
  assert.deepEqual(s.updates, []);
});

test("a Start under way in another window is refused before anything changes", async (t) => {
  const s = sandbox(t);
  const fs = require("fs");
  const path = require("path");
  s.issues[50].status = { name: "Closed", is_closed: true };
  const held = path.join(s.checkout, ".git", "plan-busy-80");
  fs.writeFileSync(held, `${process.pid} Start\n`);
  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), (e) => e.message.startsWith("#80 is being started in another window; if none is, delete " + held));
  assert.equal(s.git(s.checkout, "branch", "--list", "feat/80-*"), "");
  assert.deepEqual(s.updates, []);
  assert.ok(fs.existsSync(held), "another window's lock is left alone");
});

test("a lock left by a Start that stopped is refused by name, never taken over", async (t) => {
  const s = sandbox(t);
  const fs = require("fs");
  const path = require("path");
  const { spawnSync } = require("child_process");
  s.issues[50].status = { name: "Closed", is_closed: true };
  const held = path.join(s.checkout, ".git", "plan-busy-80");
  fs.writeFileSync(held, `${spawnSync("true").pid} Start\n`);
  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), (e) => e.message.includes(`left ${held}; delete it`));
  assert.ok(fs.existsSync(held));
  assert.deepEqual(s.updates, []);
  fs.rmSync(held);
  assert.equal((await start({ ...s, id: 80, confirm: () => true })).created, true);
  assert.ok(!fs.existsSync(held), "released after");
});

test("a git worktree add whose hook fails keeps the marker, so the next Start finishes setup", async (t) => {
  const s = sandbox(t);
  const fs = require("fs");
  const path = require("path");
  s.issues[50].status = { name: "Closed", is_closed: true };
  const hook = path.join(s.checkout, ".git", "hooks", "post-checkout");
  fs.writeFileSync(hook, "#!/bin/sh\nexit 1\n", { mode: 0o755 });
  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), /worktree add/);
  fs.rmSync(hook);
  const again = await start({ ...s, id: 80, confirm: () => true });
  assert.equal(again.created, false);
  assert.equal(again.started, true);
});

test("a failed handoff leaves setup unfinished, and the next Start hands off again", async (t) => {
  const s = sandbox(t);
  s.issues[50].status = { name: "Closed", is_closed: true };
  const handed = [];
  await assert.rejects(start({ ...s, id: 80, confirm: () => true, handOff: () => { throw new Error("disk full"); } }), /disk full/);
  const again = await start({ ...s, id: 80, confirm: () => true, handOff: (r) => handed.push(r.dir) });
  assert.equal(again.started, true);
  assert.deepEqual(handed, [again.dir]);
  const reopened = await start({ ...s, id: 80, confirm: () => true, handOff: (r) => handed.push(r.dir) });
  assert.equal(reopened.started, false);
  assert.equal(handed.length, 1);
});

test("Open finds a worktree mid-rebase by its folder, and changes nothing", async (t) => {
  const s = sandbox(t);
  s.issues[50].status = { name: "Closed", is_closed: true };
  const first = await start({ ...s, id: 80, confirm: () => true });
  s.git(first.dir, "switch", "--quiet", "--detach");
  const again = await start({ ...s, id: 80, confirm: () => assert.fail("asked about blockers") });
  assert.deepEqual({ dir: again.dir, branch: again.branch, created: again.created, started: again.started }, { dir: first.dir, branch: null, created: false, started: false });
  assert.equal(s.updates.length, 1);
});

test("a blocker Redmine won't show is listed as unknown, not a failed Start", async (t) => {
  const s = sandbox(t);
  const issue = s.redmine.issue;
  s.redmine.issue = async (id) => (id === 50 ? Promise.reject(new Error("HTTP 404")) : issue(id));
  const asked = [];
  assert.equal(await start({ ...s, id: 80, confirm: async (b) => (asked.push(b), false) }), null);
  assert.deepEqual(asked, [[{ id: 50, subject: "(couldn't read it)", status: "unknown" }]]);
});

test("a failed git worktree add leaves no marker, so a worktree made by hand after is set up", async (t) => {
  const s = sandbox(t);
  s.issues[50].status = { name: "Closed", is_closed: true };
  s.issues[80].status = { name: "In Progress", is_closed: false };
  const inject = stubs(t, s);
  inject.gitOnce("worktree add");
  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), /injected/);
  const dir = worktreeDir(s.checkout, 80);
  s.git(s.checkout, "worktree", "add", "--quiet", "-b", "feat/80-by-hand", dir, "main");
  const opened = await start({ ...s, id: 80, confirm: () => assert.fail("asked about blockers") });
  assert.equal(opened.started, false);
  assert.deepEqual(opened.notes, []);
});

test("a develop that origin deleted is no base", async (t) => {
  const s = sandbox(t);
  s.issues[50].status = { name: "Closed", is_closed: true };
  s.git(s.checkout, "push", "--quiet", "origin", "main:develop");
  s.git(s.checkout, "fetch", "--quiet", "origin");
  s.git(s.checkout, "push", "--quiet", "origin", "--delete", "develop");
  s.git(s.checkout, "update-ref", "refs/remotes/origin/develop", "main");
  const result = await start({ ...s, id: 80, confirm: () => true });
  assert.ok(result.notes.includes(`Made feat/80-start-from-the-panel from origin/main in ${result.dir}.`), result.notes.join(" "));
});

// Fault injection: each step of Start fails once, and the next Start must end where a clean one
// does. A new step that writes gets a row here, with its row in the PR's failure-mode table.
function stubs(t, s) {
  const fs = require("fs");
  const path = require("path");
  const { execFileSync } = require("child_process");
  const bin = fs.mkdtempSync(path.join(path.dirname(s.checkout), "bin-"));
  const PATH = process.env.PATH;
  const realGit = execFileSync("sh", ["-c", "command -v git"], { encoding: "utf8" }).trim();
  process.env.PATH = `${bin}:${PATH}`;
  t.after(() => (process.env.PATH = PATH));
  // A uv that makes .venv; a git that fails "git <args>" once when told to, else is git.
  fs.writeFileSync(path.join(bin, "uv"), "#!/bin/sh\nmkdir -p .venv\n", { mode: 0o755 });
  fs.writeFileSync(path.join(bin, "git"), `#!/bin/sh\nif [ -f "${bin}/fail" ] && [ "$1 $2" = "$(cat "${bin}/fail")" ]; then rm "${bin}/fail"; echo injected >&2; exit 1; fi\nexec "${realGit}" "$@"\n`, { mode: 0o755 });
  fs.writeFileSync(path.join(s.checkout, "uv.lock"), "");
  s.git(s.checkout, "add", "uv.lock");
  s.git(s.checkout, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--quiet", "-m", "uv.lock");
  s.git(s.checkout, "push", "--quiet", "origin", "main");
  return {
    gitOnce: (args) => fs.writeFileSync(path.join(bin, "fail"), args),
    uvOnce: () => fs.writeFileSync(path.join(bin, "uv"), `#!/bin/sh\nmkdir -p .venv\n[ -f "${bin}/uv-ok" ] || { touch "${bin}/uv-ok"; echo injected >&2; exit 1; }\n`, { mode: 0o755 }),
  };
}

function failOnce(object, method) {
  const real = object[method];
  object[method] = async () => {
    object[method] = real;
    throw new Error(`injected: ${method}`);
  };
}

const STEPS = {
  "reading the issue": (s) => failOnce(s.redmine, "issue"),
  "reading the project": (s) => failOnce(s.redmine, "projectIdentifier"),
  "the fetch": (s, inject) => inject.gitOnce("fetch --quiet"),
  "making the worktree": (s, inject) => inject.gitOnce("worktree add"),
  "uv sync": (s, inject) => inject.uvOnce(),
  "reading the status id": (s) => failOnce(s.redmine, "statusId"),
  "the Redmine update": (s) => failOnce(s.redmine, "update"),
  "the handoff": (s) => failOnce(s, "handOff"),
};

for (const [step, fail] of Object.entries(STEPS)) {
  test(`a Start that fails at ${step} ends, on the next Start, where a clean one does`, async (t) => {
    const s = sandbox(t);
    s.issues[50].status = { name: "Closed", is_closed: true };
    const inject = stubs(t, s);
    const confirm = () => true;
    s.handOff = () => {};
    fail(s, inject);
    await assert.rejects(start({ ...s, id: 80, confirm }), /injected/);

    const again = await start({ ...s, id: 80, confirm });
    assert.equal(again.branch, "feat/80-start-from-the-panel");
    assert.equal(again.dir, worktreeDir(s.checkout, 80));
    assert.equal(s.git(again.dir, "branch", "--show-current").trim(), again.branch);
    assert.equal(again.started, true, "the Start that finishes setup hands over to Claude");
    assert.equal(s.updates.length, 1);
    assert.equal(s.issues[80].status.name, "In Progress");

    const reopened = await start({ ...s, id: 80, confirm });
    assert.equal(reopened.started, false);
    assert.deepEqual(reopened.notes, []);
    assert.equal(s.updates.length, 1);
  });
}
