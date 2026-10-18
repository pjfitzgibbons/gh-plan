// node --test vscode/test: what Ship checks before Go, and the PR it puts on the issue, without
// Redmine or GitHub.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const { reviewFile, check, prLink, linkPullRequest } = require("../ship");

test("a review file names its issue and checkout; any other file names none", () => {
  assert.deepEqual(reviewFile("/work/gh-plan.worktrees/81/.tmp/81/commit.txt"), { root: "/work/gh-plan.worktrees/81", id: 81 });
  assert.deepEqual(reviewFile("/work/repo/.tmp/7/pr-body.md"), { root: "/work/repo", id: 7 });
  for (const file of [
    "/work/repo/.tmp/81/notes.md",
    "/work/repo/.tmp/pr-81-body.md",
    "/work/repo/.tmp/0/commit.txt",
    "/work/repo/.tmp/81x/commit.txt",
    "/work/repo/.tmp/81/sub/commit.txt",
    "/work/repo/src/.tmp/81/commit.txt.bak",
  ]) {
    assert.equal(reviewFile(file), null, file);
  }
});

// A checkout on #81's branch, with .tmp/ ignored as in the repos Ship runs in.
function sandbox(t, branch = "feat/81-ship-it") {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "plan-ship-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const git = (...args) => execFileSync("git", args, { cwd: root, encoding: "utf8" });
  git("init", "--quiet", "-b", branch);
  fs.writeFileSync(path.join(root, ".gitignore"), ".tmp\n");
  git("add", ".gitignore");
  git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--quiet", "-m", "first");
  fs.mkdirSync(path.join(root, ".tmp", "81"), { recursive: true });
  const write = (name, text = "x\n") => fs.writeFileSync(path.join(root, name), text);
  return { root, write };
}

test("a clean tree with a PR body may ship", async (t) => {
  const s = sandbox(t);
  s.write(".tmp/81/pr-body.md");
  assert.deepEqual(await check(s.root, 81), { branch: "feat/81-ship-it", dirty: false, problems: [] });
});

test("changes with both files may ship", async (t) => {
  const s = sandbox(t);
  s.write(".tmp/81/pr-body.md");
  s.write(".tmp/81/commit.txt");
  s.write("ship.js");
  assert.deepEqual(await check(s.root, 81), { branch: "feat/81-ship-it", dirty: true, problems: [] });
});

test("each missing file is named", async (t) => {
  const s = sandbox(t);
  assert.deepEqual((await check(s.root, 81)).problems, [".tmp/81/pr-body.md is missing"]);
  s.write("ship.js");
  assert.deepEqual((await check(s.root, 81)).problems, [
    ".tmp/81/pr-body.md is missing",
    "the tree has changes and .tmp/81/commit.txt is missing",
  ]);
});

test("another issue's branch is refused", async (t) => {
  const s = sandbox(t, "feat/810-other");
  s.write(".tmp/81/pr-body.md");
  assert.deepEqual((await check(s.root, 81)).problems, ["the branch feat/810-other isn't #81's"]);
});

const PR = { url: "https://github.com/someone/gh-plan/pull/111", number: 111, title: "Ship from the review" };

function redmine(value) {
  const updates = [];
  const issue = { id: 81, custom_fields: [{ id: 1, name: "Points", value: "3" },{ id: 7, name: "Pull requests", value }] };
  return {
    updates,
    issue: async () => issue,
    update: async (id, fields) => {
      updates.push({ id, ...fields });
      issue.custom_fields[1].value = fields.custom_fields[0].value;
    },
  };
}

test("a PR is linked as backfill.rb links one", () => {
  assert.equal(prLink(PR), "[gh-plan#111](https://github.com/someone/gh-plan/pull/111)");
});

test("the first Ship links the PR with a note, and Ship again changes nothing", async () => {
  const r = redmine(null);
  assert.equal(await linkPullRequest({ redmine: r, id: 81, pr: PR }), true);
  assert.deepEqual(r.updates, [{
    id: 81,
    custom_fields: [{ id: 7, value: "[gh-plan#111](https://github.com/someone/gh-plan/pull/111)" }],
    notes: "Opened [gh-plan#111](https://github.com/someone/gh-plan/pull/111): Ship from the review",
  }]);
  assert.equal(await linkPullRequest({ redmine: r, id: 81, pr: PR }), false);
  assert.equal(r.updates.length, 1);
});

test("a second PR is added after the links already there", async () => {
  const r = redmine("[gh-plan#11](https://github.com/someone/gh-plan/pull/11)");
  await linkPullRequest({ redmine: r, id: 81, pr: PR });
  assert.equal(r.updates[0].custom_fields[0].value,
    "[gh-plan#11](https://github.com/someone/gh-plan/pull/11), [gh-plan#111](https://github.com/someone/gh-plan/pull/111)");
});

test("a bare link already there counts as linked", async () => {
  const r = redmine(PR.url);
  assert.equal(await linkPullRequest({ redmine: r, id: 81, pr: PR }), false);
  assert.deepEqual(r.updates, []);
});

test("an issue with no Pull requests field is refused by name", async () => {
  const r = { issue: async () => ({ id: 81, custom_fields: [] }), update: () => assert.fail("updated") };
  await assert.rejects(linkPullRequest({ redmine: r, id: 81, pr: PR }), /no Pull requests field/);
});
