// node --test vscode/test: what Ship checks before Go, and the PR it puts on the issue, without
// Redmine or GitHub.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const { reviewFile, roundFiles, shipTab, check, prLink, linkPullRequest } = require("../ship");

test("a review file names its issue and checkout; any other file names none", () => {
  assert.deepEqual(reviewFile("/work/gh-plan.worktrees/81/.tmp/81/commit.txt"), { root: "/work/gh-plan.worktrees/81", id: 81 });
  assert.deepEqual(reviewFile("/work/repo/.tmp/7/pr-body.md"), { root: "/work/repo", id: 7 });
  assert.deepEqual(reviewFile("/work/repo/.tmp/7/issue.md"), { root: "/work/repo", id: 7 });
  assert.deepEqual(reviewFile("/work/repo/.tmp/7/replies/4309488236.md"), { root: "/work/repo", id: 7 });
  // pr_threads.py reads 011.md as comment 11, so Ship does too.
  assert.deepEqual(reviewFile("/work/repo/.tmp/7/replies/011.md"), { root: "/work/repo", id: 7 });
  for (const file of [
    "/work/repo/.tmp/81/notes.md",
    "/work/repo/.tmp/pr-81-body.md",
    "/work/repo/.tmp/0/commit.txt",
    "/work/repo/.tmp/81x/commit.txt",
    "/work/repo/.tmp/81/sub/commit.txt",
    "/work/repo/.tmp/81/replies/notes.md",
    "/work/repo/.tmp/81/replies/11.txt",
    "/work/repo/src/.tmp/81/commit.txt.bak",
  ]) {
    assert.equal(reviewFile(file), null, file);
  }
});

test("Ship's tabs are the issue's review files, diffs in its checkout and its Changes view", () => {
  const root = "/work/gh-plan.worktrees/81";
  const uri = (fsPath) => ({ fsPath });
  const diff = (file) => ({ original: uri(file), modified: uri(file) });
  for (const input of [
    { uri: uri(`${root}/.tmp/81/commit.txt`) },
    { uri: uri(`${root}/.tmp/81/pr-body.md`) },
    { uri: uri(`${root}/.tmp/81/issue.md`) },
    { uri: uri(`${root}/.tmp/81/replies/4309488236.md`) },
    diff(`${root}/vscode/ship.js`),
    { textDiffs: [diff(`${root}/vscode/ship.js`), diff(`${root}/README.md`)] },
    { textDiffs: [] },
  ]) {
    assert.equal(shipTab(input, root, 81), true, JSON.stringify(input));
  }
  for (const input of [
    undefined,
    {},
    { uri: uri(`${root}/vscode/ship.js`) },
    { uri: uri(`${root}/.tmp/7/pr-body.md`) },
    { uri: uri(`${root}/.tmp/81/replies/notes.md`) },
    { uri: uri(`${root}/.tmp/81/notes.md`) },
    diff("/work/gh-plan/vscode/ship.js"),
    diff(`${root}-other/README.md`),
    { textDiffs: [diff("/work/gh-plan/README.md")] },
  ]) {
    assert.equal(shipTab(input, root, 81), false, JSON.stringify(input));
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

test("a reply pr would refuse, skip or post twice is refused before Go", async (t) => {
  const s = sandbox(t);
  s.write(".tmp/81/pr-body.md");
  fs.mkdirSync(path.join(s.root, ".tmp", "81", "replies"));
  s.write(".tmp/81/issue.md");
  s.write(".tmp/81/replies/11.md");
  s.write(".tmp/81/replies/.DS_Store");
  fs.mkdirSync(path.join(s.root, ".tmp", "81", "replies", "sent"));
  s.write(".tmp/81/replies/sent/10.md");
  assert.deepEqual((await check(s.root, 81)).problems, []);
  s.write(".tmp/81/replies/13.md", " \n");
  s.write(".tmp/81/replies/011.md");
  s.write(".tmp/81/replies/notes.md");
  s.write(".tmp/81/replies/12.txt");
  assert.deepEqual((await check(s.root, 81)).problems, [
    ".tmp/81/replies/011.md and replies/11.md both reply to comment 11",
    ".tmp/81/replies/12.txt isn't a reply pr can send; name it replies/<comment id>.md",
    ".tmp/81/replies/13.md is empty",
    ".tmp/81/replies/notes.md isn't a reply pr can send; name it replies/<comment id>.md",
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

test("a round's files open once the PR body has something beside it, replies by comment id, the PR body last, nothing pr can't send", (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "plan-round-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const write = (name) => (fs.mkdirSync(path.dirname(path.join(dir, name)), { recursive: true }), fs.writeFileSync(path.join(dir, name), "x\n"));
  const names = () => roundFiles(dir).map((file) => path.relative(dir, file));
  write("pr-body.md");
  assert.deepEqual(names(), []);
  write("replies/100.md");
  write("replies/99.md");
  write("replies/draft.txt");
  write("replies/notes.md");
  assert.deepEqual(names(), ["replies/99.md", "replies/100.md", "pr-body.md"]);
  write("commit.txt");
  write("issue.md");
  assert.deepEqual(names(), ["commit.txt", "issue.md", "replies/99.md", "replies/100.md", "pr-body.md"]);
  fs.rmSync(path.join(dir, "pr-body.md"));
  assert.deepEqual(names(), []);
});
