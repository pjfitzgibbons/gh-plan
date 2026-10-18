// node --test vscode/test: the names Start makes and the blockers it warns on, without Redmine or git.
const test = require("node:test");
const assert = require("node:assert/strict");
const { slug, branchName, worktreeDir, blockerIds, existingWorktree, start } = require("../start");

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
  assert.deepEqual(existingWorktree(porcelain, 80), { dir: "/work/gh-plan.worktrees/80", branch: "feat/80-old-slug" });
  assert.equal(existingWorktree(porcelain, 8), null);
  assert.equal(existingWorktree(porcelain, 81), null);
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

test("a sync that left .venv behind runs again on the next Start", async (t) => {
  const s = sandbox(t);
  const fs = require("fs");
  const path = require("path");
  s.issues[50].status = { name: "Closed", is_closed: true };
  // A uv that records each run, makes .venv, and fails the first time, as a broken install would.
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

  await assert.rejects(start({ ...s, id: 80, confirm: () => true }), /uv sync/);
  assert.deepEqual(s.updates, []);
  const again = await start({ ...s, id: 80, confirm: () => true });
  assert.equal(fs.readFileSync(log, "utf8"), "run\nrun\n");
  assert.ok(again.notes.includes("Synced the uv environment."));
  assert.equal(s.updates.length, 1);
  // Started now, with .venv there: reopening syncs no more.
  await start({ ...s, id: 80, confirm: () => true });
  assert.equal(fs.readFileSync(log, "utf8"), "run\nrun\n");
});
