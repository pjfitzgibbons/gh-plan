// node --test vscode/test: what Go checks before Ship, and what Ship does on GitHub and the issue,
// without Redmine or GitHub.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const { reviewFile, roundFiles, shipTab, stage, stagedChanges, openStaged, check, prLink, linkPullRequest, shipped, unsent, keepEdits } = require("../ship");

test("a review file names its issue and checkout; any other file names none", () => {
  assert.deepEqual(reviewFile("/work/gh-plan.worktrees/81/.tmp/81/commit.txt"), { root: "/work/gh-plan.worktrees/81", id: 81 });
  assert.deepEqual(reviewFile("/work/repo/.tmp/7/pr-body.md"), { root: "/work/repo", id: 7 });
  assert.deepEqual(reviewFile("/work/repo/.tmp/7/replies/4309488236.md"), { root: "/work/repo", id: 7 });
  // pr_threads.py reads 011.md as comment 11, so Go does too.
  assert.deepEqual(reviewFile("/work/repo/.tmp/7/replies/011.md"), { root: "/work/repo", id: 7 });
  for (const file of [
    "/work/repo/.tmp/81/notes.md",
    // The Done ticks are the PR body's now; an issue.md left from before is never sent.
    "/work/repo/.tmp/81/issue.md",
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

test("Go's tabs are the issue's review files, diffs in its checkout and its Changes view", () => {
  const root = "/work/gh-plan.worktrees/81";
  const uri = (fsPath) => ({ fsPath });
  const diff = (file) => ({ original: uri(file), modified: uri(file) });
  for (const input of [
    { uri: uri(`${root}/.tmp/81/commit.txt`) },
    { uri: uri(`${root}/.tmp/81/pr-body.md`) },
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
    { uri: uri(`${root}/.tmp/81/issue.md`) },
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

test("a reply pr would refuse, skip or post twice is refused before Ship", async (t) => {
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

test("Go stages every change, and Ship refuses a file changed after it", async (t) => {
  const s = sandbox(t);
  s.write(".tmp/81/pr-body.md");
  s.write(".tmp/81/commit.txt");
  s.write("ship.js");
  s.write(".gitignore", ".tmp\nlocal.log\n");
  s.write("local.log");
  // Before Go, Ship would commit all of it unreviewed.
  assert.deepEqual((await check(s.root, 81, { staged: true })).problems, [
    ".gitignore, ship.js changed since Go; press Go to stage and review them",
  ]);
  await stage(s.root);
  // pr commits the index Go staged, so the Changes view's staged list is the commit; ignored files stay out.
  const index = execFileSync("git", ["diff", "--cached", "--name-status"], { cwd: s.root, encoding: "utf8" });
  assert.equal(index, "M\t.gitignore\nA\tship.js\n");
  assert.deepEqual(await check(s.root, 81, { staged: true }), { branch: "feat/81-ship-it", dirty: true, problems: [] });
  s.write("ship.js", "changed after Go\n");
  fs.rmSync(path.join(s.root, ".gitignore"));
  s.write("README.md");
  // An edit, a deletion and a new file, each by name; .tmp/ shows up only because .gitignore went.
  assert.deepEqual((await check(s.root, 81, { staged: true })).problems, [
    ".gitignore, .tmp/81/commit.txt, .tmp/81/pr-body.md, README.md, local.log, ship.js changed since Go; press Go to stage and review them",
  ]);
  s.write(".gitignore", ".tmp\nlocal.log\n");
  fs.rmSync(path.join(s.root, "README.md"));
  assert.deepEqual((await check(s.root, 81, { staged: true })).problems, [
    "ship.js changed since Go; press Go to stage and review it",
  ]);
  // Go's own check doesn't ask, since Go stages what it finds.
  assert.deepEqual((await check(s.root, 81)).problems, []);
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

// gh on PATH standing in for GitHub: it answers pr view with PR, or fails it when asked to.
function fakeGh(t, { view = true } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "plan-gh-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.writeFileSync(path.join(dir, "pr.json"), JSON.stringify(PR));
  fs.writeFileSync(path.join(dir, "gh"), `#!/bin/sh
${view ? `cat "${dir}/pr.json"` : "echo 'HTTP 502: Bad Gateway' >&2; exit 1"}
`, { mode: 0o755 });
  const PATH = process.env.PATH;
  process.env.PATH = `${dir}:${PATH}`;
  t.after(() => (process.env.PATH = PATH));
}

// Ship's step once pr succeeds, for #81 on its branch.
const ship = (r = redmine(null)) => shipped({ root: os.tmpdir(), id: 81, branch: "feat/81-ship-it", redmine: () => r });

test("Ship links the branch's PR on the issue, and Ship again says it's linked", async (t) => {
  fakeGh(t);
  const r = redmine(null);
  assert.deepEqual(await ship(r), { failed: false, message: `#81 now links ${PR.url}` });
  assert.deepEqual(await ship(r), { failed: false, message: `#81 already links ${PR.url}` });
  assert.equal(r.updates.length, 1);
});

test("a PR that can't be read says Redmine wasn't told", async (t) => {
  fakeGh(t, { view: false });
  assert.deepEqual(await ship(), {
    failed: true,
    message: "#81 shipped, but Redmine wasn't told. gh pr view feat/81-ship-it --json url,number,title: HTTP 502: Bad Gateway. Go and Ship again to retry.",
  });
});

test("Redmine refusing the link says so by name", async (t) => {
  fakeGh(t);
  const r = { issue: async () => ({ id: 81, custom_fields: [] }), update: () => assert.fail("updated") };
  assert.deepEqual(await ship(r), {
    failed: true,
    message: "#81 shipped, but Redmine wasn't told. #81 has no Pull requests field; run setup.rb. Go and Ship again to retry.",
  });
});

test("a Redmine client that can't be made says Redmine wasn't told", async (t) => {
  fakeGh(t);
  const made = shipped({ root: os.tmpdir(), id: 81, branch: "feat/81-ship-it", redmine: () => { throw new Error("no Redmine URL"); } });
  assert.deepEqual(await made, {
    failed: true,
    message: "#81 shipped, but Redmine wasn't told. no Redmine URL. Go and Ship again to retry.",
  });
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
  write("issue.md.unsent");
  assert.deepEqual(names(), ["commit.txt", "replies/99.md", "replies/100.md", "pr-body.md"]);
  fs.rmSync(path.join(dir, "pr-body.md"));
  assert.deepEqual(names(), []);
});

test("edits pr never saw are kept beside the file, never over one, and count as unsent", (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "plan-keep-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.mkdirSync(path.join(dir, "replies"));
  const reply = path.join(dir, "replies", "11.md");
  assert.equal(keepEdits(reply, "later\n"), `${reply}.unsent`);
  assert.equal(keepEdits(reply, "later still\n"), `${reply}.unsent2`);
  assert.equal(fs.readFileSync(`${reply}.unsent`, "utf8"), "later\n");
  // An issue.md and its kept edits, from before the PR held the Done ticks, are never sent.
  fs.writeFileSync(path.join(dir, "issue.md"), "- [x] ticked\n");
  fs.writeFileSync(path.join(dir, "issue.md.unsent"), "- [x] ticked\n");
  assert.deepEqual(unsent(dir).map((file) => path.relative(dir, file)), [
    "replies/11.md.unsent",
    "replies/11.md.unsent2",
  ]);
});

test("Go reads the index into VS Code's git extension before it opens the staged changes", async () => {
  const calls = [];
  const repository = { status: async () => { calls.push("status"); } };
  assert.equal(await openStaged(repository, async (command) => { calls.push(command); }), null);
  assert.deepEqual(calls, ["status", "git.viewStagedChanges"]);
});

test("without the git extension's repository, or when it can't read the index, Go opens nothing and says why", async () => {
  const opened = [];
  const execute = async (command) => { opened.push(command); };
  assert.match(await openStaged(null, execute), /no repository/);
  const failing = { status: async () => { throw new Error("index.lock exists"); } };
  assert.equal(await openStaged(failing, execute), "VS Code's git extension couldn't read it: index.lock exists.");
  assert.deepEqual(opened, []);
});

test("the staged changes are git's diff of what Go staged, renames as renames and paths unquoted", async (t) => {
  const s = sandbox(t);
  s.write("é name.txt", "one\ntwo\nthree\n");
  await stage(s.root);
  execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--quiet", "-m", "second"], { cwd: s.root });
  fs.renameSync(path.join(s.root, "é name.txt"), path.join(s.root, "moved.txt"));
  s.write("added.txt");
  await stage(s.root);
  const diff = await stagedChanges(s.root);
  assert.match(diff, /^rename from é name\.txt$/m);
  assert.match(diff, /^rename to moved\.txt$/m);
  assert.match(diff, /^\+\+\+ b\/added\.txt$/m);
  assert.ok(!diff.includes("\u001b["), "no colour codes");
});

test("the staged changes read the same whatever the diff prefixes, context, blank-line and textconv config", async (t) => {
  const s = sandbox(t);
  s.write("a.txt", "one\n\ntwo\n");
  await stage(s.root);
  execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--quiet", "-m", "second"], { cwd: s.root });
  s.write("a.txt", "one\n\nthree\n");
  await stage(s.root);
  execFileSync("git", ["config", "diff.noprefix", "true"], { cwd: s.root });
  execFileSync("git", ["config", "diff.suppressBlankEmpty", "true"], { cwd: s.root });
  // A textconv filter would show its own text, not the file's, which pr commits.
  fs.mkdirSync(path.join(s.root, ".git", "info"), { recursive: true });
  fs.writeFileSync(path.join(s.root, ".git", "info", "attributes"), "*.txt diff=upper\n");
  execFileSync("git", ["config", "diff.context", "0"], { cwd: s.root });
  execFileSync("git", ["config", "diff.upper.textconv", "tr a-z A-Z <"], { cwd: s.root });
  const diff = await stagedChanges(s.root);
  assert.match(diff, /^diff --git a\/a\.txt b\/a\.txt$/m);
  assert.match(diff, /^\+\+\+ b\/a\.txt$/m);
  // The blank line between keeps its space, so line numbers count it.
  assert.match(diff, /^ one\n \n-two$/m);
});

test("a submodule bump shows as a diff of its commit line, whatever diff.submodule says", async (t) => {
  const s = sandbox(t);
  const sub = path.join(s.root, "..", `${path.basename(s.root)}-sub`);
  t.after(() => fs.rmSync(sub, { recursive: true, force: true }));
  const git = (cwd, ...args) => execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "protocol.file.allow=always", ...args], { cwd });
  fs.mkdirSync(sub);
  git(sub, "init", "--quiet");
  git(sub, "commit", "--quiet", "--allow-empty", "-m", "one");
  git(s.root, "submodule", "add", "--quiet", sub, "sub");
  git(s.root, "commit", "--quiet", "-m", "add sub");
  git(path.join(s.root, "sub"), "commit", "--quiet", "--allow-empty", "-m", "two");
  await stage(s.root);
  git(s.root, "config", "diff.submodule", "log");
  const diff = await stagedChanges(s.root);
  assert.match(diff, /^diff --git a\/sub b\/sub$/m);
  assert.match(diff, /^\+Subproject commit [0-9a-f]{40}$/m);
  assert.doesNotMatch(diff, /^Submodule /m);
});

test("a staged diff over execFile's buffer is refused by name", async (t) => {
  const s = sandbox(t);
  s.write("big.txt", ("y".repeat(99) + "\n").repeat(15000));
  await stage(s.root);
  await assert.rejects(stagedChanges(s.root), /maxBuffer/);
});
