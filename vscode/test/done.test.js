// node --test vscode/test: Done, against a throwaway origin whose PR squash-merged, with a Redmine
// and a gh that record what they're asked.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const { worktreeDir } = require("../start");
const { linkedPullRequest, doneNote, finish, DISPOSABLE } = require("../done");

const ID = 82;
const BRANCH = "feat/82-finish-it";
const PR_URL = "https://github.com/example/demo/pull/9";

test("the linked PR is the last one the field names", () => {
  const issue = (value) => ({ custom_fields: [{ name: "Pull requests", value }] });
  assert.equal(linkedPullRequest(issue("[demo#8](https://github.com/example/demo/pull/8), [demo#9](https://github.com/example/demo/pull/9)")), PR_URL);
  assert.equal(linkedPullRequest(issue("")), null);
  assert.equal(linkedPullRequest({}), null);
});

test("the note links the PR and its merge commit", () => {
  const pr = { url: PR_URL, number: 9, mergeCommit: { oid: "782fead542ba2591c5dd44838f691bed5bae6e62" } };
  assert.equal(doneNote(pr), "Done: [demo#9](https://github.com/example/demo/pull/9) merged as [782fead](https://github.com/example/demo/commit/782fead542ba2591c5dd44838f691bed5bae6e62).");
});

// An origin and checkout; #82's branch in its worktree, pushed, and squash-merged into main as a
// PR would be. view() is gh, answering for that PR.
function sandbox(t, { merged = true } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "plan-done-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const git = (cwd, ...args) => execFileSync("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", ...args], { cwd, encoding: "utf8" }).trim();
  git(root, "init", "--quiet", "--bare", "-b", "main", "origin.git");
  git(root, "clone", "--quiet", "origin.git", "repo");
  const checkout = fs.realpathSync(path.join(root, "repo"));
  // origin is named as GitHub names it, and fetched from the bare repo.
  git(checkout, "remote", "set-url", "origin", "git@github.com:example/demo.git");
  git(checkout, "config", `url.${path.join(root, "origin.git")}.insteadOf`, "git@github.com:example/demo.git");
  fs.writeFileSync(path.join(checkout, ".gitignore"), ".tmp\n");
  git(checkout, "add", ".gitignore");
  git(checkout, "commit", "--quiet", "-m", "first");
  git(checkout, "push", "--quiet", "-u", "origin", "main");
  const dir = worktreeDir(checkout, ID);
  git(checkout, "worktree", "add", "--quiet", "-b", BRANCH, dir, "main");
  fs.writeFileSync(path.join(dir, "done.js"), "// Done\n");
  fs.mkdirSync(path.join(dir, ".tmp", String(ID)), { recursive: true });
  fs.writeFileSync(path.join(dir, ".tmp", String(ID), "pr-body.md"), "The body\n");
  git(dir, "add", "done.js");
  git(dir, "commit", "--quiet", "-m", "Done");
  git(dir, "push", "--quiet", "-u", "origin", BRANCH);
  const pr = { state: "OPEN", url: PR_URL, number: 9, title: "Finish it (fixes #82)", headRefName: BRANCH, headRefOid: git(dir, "rev-parse", "HEAD"), baseRefName: "main", mergeCommit: null };
  // GitHub's squash merge, made in a clone of origin so the checkout's main is behind it.
  const merge = () => {
    git(root, "clone", "--quiet", "origin.git", "github");
    const github = path.join(root, "github");
    git(github, "merge", "--quiet", "--squash", `origin/${pr.headRefName}`);
    git(github, "commit", "--quiet", "-m", "Finish it (fixes #82) (#9)");
    git(github, "push", "--quiet", "origin", "main");
    Object.assign(pr, { state: "MERGED", mergeCommit: { oid: git(github, "rev-parse", "HEAD") } });
  };
  if (merged) merge();

  const issue = {
    id: ID, project: { id: 2 }, status: { name: "In Progress", is_closed: false }, journals: [],
    custom_fields: [{ id: 7, name: "Pull requests", value: `[demo#9](${PR_URL})` }],
  };
  const updates = [];
  const fetched = [];
  const views = [];
  const redmine = {
    issue: async () => structuredClone(issue),
    projectIdentifier: async () => "demo",
    statusId: async () => 5,
    fetchCommits: async (project) => fetched.push(project),
    update: async (id, fields) => {
      updates.push({ id, ...fields });
      if (fields.status_id) issue.status = { name: "Closed", is_closed: true };
      if (fields.notes) issue.journals.push({ notes: fields.notes });
    },
  };
  const view = async (cwd, url) => (views.push(url), structuredClone(pr));
  return { root, checkout, dir, git, pr, merge, issue, updates, fetched, views, redmine, view, checkouts: { demo: path.join(root, "repo") } };
}

const done = (s) => finish({ redmine: s.redmine, checkouts: s.checkouts, id: ID, view: s.view });
const branches = (s) => s.git(s.checkout, "branch", "--list", "--format=%(refname:short)").split("\n").filter(Boolean);

// Where a clean Done ends: no worktree, no branch, main at the merge, the issue Closed with one note.
// on: the branch the main checkout was on, when not main or the issue's.
function assertDone(s, on = "main") {
  assert.ok(!fs.existsSync(s.dir), "the worktree is gone");
  assert.equal(s.git(s.checkout, "worktree", "list", "--porcelain").split("\n\n").length, 1);
  assert.deepEqual(branches(s), [...new Set(["main", on])].sort());
  assert.equal(s.git(s.checkout, "branch", "--show-current"), on);
  assert.equal(s.git(s.checkout, "rev-parse", "main"), s.pr.mergeCommit.oid);
  assert.equal(s.issue.status.name, "Closed");
  assert.deepEqual(s.issue.journals.map((j) => j.notes), [doneNote(s.pr)]);
}

test("Done after the merge removes the worktree and branch, pulls the merge, and closes the issue with its note", async (t) => {
  const s = sandbox(t);
  const result = await done(s);
  assert.equal(result.dir, s.dir);
  assertDone(s);
  assert.deepEqual(s.views, [PR_URL]);
  assert.deepEqual(s.fetched, ["demo"]);
  assert.deepEqual(s.updates, [{ id: ID, status_id: 5, notes: doneNote(s.pr) }]);
  assert.deepEqual(result.notes, ["Brought main up to the merge.", `Removed the worktree ${s.dir}.`, `Deleted the branch ${BRANCH}.`, `#${ID} is Closed.`]);

  const again = await done(s);
  assertDone(s);
  assert.equal(s.updates.length, 1, "Done again changes nothing in Redmine");
  assert.deepEqual(again, { dir: null, notes: [] });
});

test("Done before the merge changes nothing and says the PR is still open", async (t) => {
  const s = sandbox(t, { merged: false });
  await assert.rejects(done(s), /demo#9\]\(.+\) is still open; nothing changed/);
  assert.ok(fs.existsSync(s.dir));
  assert.deepEqual(branches(s), [BRANCH, "main"]);
  assert.deepEqual(s.updates, []);
  assert.deepEqual(s.fetched, []);
  s.merge();
  await done(s);
  assertDone(s);
});

test("a worktree with work the PR lacks is kept, and Done names it", async (t) => {
  const s = sandbox(t);
  fs.writeFileSync(path.join(s.dir, "more.js"), "// more\n");
  await assert.rejects(done(s), (e) => e.message.startsWith(`${s.dir} has changes the merged PR lacks`) && e.message.includes("?? more.js"));
  assert.ok(fs.existsSync(path.join(s.dir, "more.js")));
  s.git(s.dir, "add", "more.js");
  s.git(s.dir, "commit", "--quiet", "-m", "more");
  await assert.rejects(done(s), new RegExp(`is at [0-9a-f]{7}, with commits \\[demo#9\\].+'s merged head [0-9a-f]{7} lacks; open a new PR`));
  assert.deepEqual(branches(s), [BRANCH, "main"]);
  assert.deepEqual(s.updates, []);
});

test("a worktree behind the PR's head, after a commit made on GitHub, is removed with its branch", async (t) => {
  const s = sandbox(t, { merged: false });
  s.git(s.root, "clone", "--quiet", "origin.git", "suggest");
  const suggest = path.join(s.root, "suggest");
  s.git(suggest, "switch", "--quiet", BRANCH);
  s.git(suggest, "commit", "--quiet", "--allow-empty", "-m", "Accept a suggestion");
  s.git(suggest, "push", "--quiet", "origin", `HEAD:${BRANCH}`, "HEAD:refs/pull/9/head");
  s.pr.headRefOid = s.git(suggest, "rev-parse", "HEAD");
  s.merge();
  await done(s);
  assertDone(s);
});

test("a main checkout with changes on the base is refused before the worktree goes", async (t) => {
  const s = sandbox(t);
  fs.appendFileSync(path.join(s.checkout, ".gitignore"), "more\n");
  await assert.rejects(done(s), (e) => e.message.startsWith(`${s.checkout} has changes on main; commit or stash them`));
  assert.ok(fs.existsSync(s.dir));
  assert.deepEqual(branches(s), [BRANCH, "main"]);
  assert.deepEqual(s.updates, []);
});

test("a PR head branch that names no issue is found by its worktree, which goes with it", async (t) => {
  const s = sandbox(t, { merged: false });
  const spike = path.join(s.root, "spike");
  s.git(s.checkout, "worktree", "add", "--quiet", "-b", "spike-login", spike, "main");
  fs.writeFileSync(path.join(spike, "spike.js"), "// Spike\n");
  s.git(spike, "add", "spike.js");
  s.git(spike, "commit", "--quiet", "-m", "Spike");
  s.git(spike, "push", "--quiet", "origin", "spike-login");
  Object.assign(s.pr, { headRefName: "spike-login", headRefOid: s.git(spike, "rev-parse", "HEAD") });
  s.merge();
  const result = await done(s);
  assert.equal(result.dir, path.join(fs.realpathSync(s.root), "spike"));
  assert.ok(!fs.existsSync(spike));
  assert.deepEqual(branches(s), [BRANCH, "main"]);
  assert.equal(s.issue.status.name, "Closed");
});

test("a base checked out in another worktree is left to it, and the merge is checked on origin", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "switch", "--quiet", "-c", "spike");
  const review = path.join(s.root, "review");
  s.git(s.checkout, "worktree", "add", "--quiet", review, "main");
  const result = await done(s);
  assert.ok(result.notes.includes(`Left ${s.checkout} on spike; main is checked out in ${fs.realpathSync(review)}, so pull it there, and Redmine sees the merge once you do.`), result.notes.join(" "));
  assert.equal(s.git(s.checkout, "rev-parse", "origin/main"), s.pr.mergeCommit.oid);
  assert.deepEqual(branches(s), ["main", "spike"]);
  assert.equal(s.issue.status.name, "Closed");
});

test("a PR head that can't be fetched stops Done, rather than reading as commits the PR lacks", async (t) => {
  const s = sandbox(t);
  s.pr.headRefOid = "0123456789abcdef0123456789abcdef01234567";
  await assert.rejects(done(s), (e) => e.message.includes("isn't in") && e.message.includes("fetching it failed; Done again once origin can be reached"));
  assert.ok(fs.existsSync(s.dir));
  assert.deepEqual(s.updates, []);
});

test("a base with commits origin lacks is refused before the worktree goes", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "commit", "--quiet", "--allow-empty", "-m", "local only");
  await assert.rejects(done(s), new RegExp(`main in .+ has commits origin/main lacks, so it can't fast-forward`));
  assert.ok(fs.existsSync(s.dir));
  assert.deepEqual(s.updates, []);
});

test("a PR in another repo than origin is refused before anything changes", async (t) => {
  const s = sandbox(t);
  s.pr.url = "https://github.com/example/other/pull/9";
  await assert.rejects(done(s), (e) => e.message.includes("isn't in") && e.message.includes("'s origin, git@github.com:example/demo.git"));
  assert.ok(fs.existsSync(s.dir));
  assert.deepEqual(s.updates, []);
});

test("an origin named by an ssh host alias is the PR's repo", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "remote", "set-url", "origin", "git@github-work:example/demo.git");
  s.git(s.checkout, "config", `url.${path.join(s.root, "origin.git")}.insteadOf`, "git@github-work:example/demo.git");
  await done(s);
  assertDone(s);
});

test("a PR base origin no longer has is refused, rather than waited for", async (t) => {
  const s = sandbox(t);
  s.pr.baseRefName = "feat/80-gone";
  await assert.rejects(done(s), /origin has no feat\/80-gone, .+'s base, so Done can't bring it up; finish #82 by hand/);
  assert.ok(fs.existsSync(s.dir));
  assert.deepEqual(s.updates, []);
});

test("the issue's branch in the main checkout, with the base in another worktree, is refused before anything changes", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "worktree", "remove", s.dir);
  s.git(s.checkout, "switch", "--quiet", BRANCH);
  const review = path.join(s.root, "review");
  s.git(s.checkout, "worktree", "add", "--quiet", review, "main");
  await assert.rejects(done(s), new RegExp(`is on ${BRANCH}, and main is checked out in .+review; switch that worktree off main`));
  assert.deepEqual(branches(s), [BRANCH, "main"]);
  assert.deepEqual(s.updates, []);
});

test("an untracked file the fast-forward would write is refused before the worktree goes", async (t) => {
  const s = sandbox(t);
  fs.writeFileSync(path.join(s.checkout, "done.js"), "// a scratch copy\n");
  fs.writeFileSync(path.join(s.checkout, "notes.txt"), "scratch\n");
  await assert.rejects(done(s), (e) => e.message.includes("has files where origin/main adds tracked ones") && e.message.endsWith("\ndone.js"));
  assert.ok(fs.existsSync(s.dir));
  assert.deepEqual(s.updates, []);
  fs.rmSync(path.join(s.checkout, "done.js"));
  await done(s);
  assertDone(s);
});

test("a Done that stopped after removing a worktree on another of the issue's branches deletes that branch next time", async (t) => {
  const s = sandbox(t);
  s.git(s.dir, "switch", "--quiet", "-c", "fix/82-renamed");
  s.git(s.checkout, "worktree", "remove", s.dir);
  const result = await done(s);
  assert.ok(result.notes.includes("Deleted the branch fix/82-renamed."), result.notes.join(" "));
  assertDone(s);
});

test("a PR whose head is named like the base finds the issue's worktree, and never deletes the base", async (t) => {
  const s = sandbox(t);
  s.pr.headRefName = "main";
  const result = await done(s);
  assert.equal(result.dir, s.dir);
  assertDone(s);
});

test("an issue's branch another worktree has is kept and named, with prune when its folder is gone", async (t) => {
  const s = sandbox(t);
  const alt = path.join(s.root, "alt");
  const gone = path.join(s.root, "gone");
  s.git(s.checkout, "worktree", "add", "--quiet", "-b", "fix/82-alt", alt, BRANCH);
  s.git(s.checkout, "worktree", "add", "--quiet", "-b", "fix/82-gone", gone, BRANCH);
  fs.rmSync(gone, { recursive: true });
  const result = await done(s);
  assert.ok(result.notes.includes(`Kept the branch fix/82-alt: it's checked out in ${fs.realpathSync(alt)}.`), result.notes.join(" "));
  assert.ok(result.notes.some((n) => n.startsWith("Kept the branch fix/82-gone: git lists it in ") && n.includes("run git worktree prune")), result.notes.join(" "));
  assert.deepEqual(branches(s), ["fix/82-alt", "fix/82-gone", "main"]);
});

test("a base worktree whose folder was deleted by hand is refused, naming git worktree prune", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "switch", "--quiet", "-c", "spike");
  const review = path.join(s.root, "review");
  s.git(s.checkout, "worktree", "add", "--quiet", review, "main");
  fs.rmSync(review, { recursive: true });
  await assert.rejects(done(s), /but the folder is gone; run git worktree prune, then Done again/);
  assert.ok(fs.existsSync(s.dir));
  s.git(s.checkout, "worktree", "prune");
  await done(s);
  assertDone(s, "spike");
});

test("a tag named like the base doesn't hide the base's local commits", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "tag", "main");
  s.git(s.checkout, "switch", "--quiet", "-c", "spike");
  s.git(s.checkout, "commit", "--quiet", "--allow-empty", "-m", "on spike");
  s.git(s.checkout, "update-ref", "refs/heads/main", "HEAD");
  await assert.rejects(done(s), /main in .+ has commits origin\/main lacks/);
  assert.ok(fs.existsSync(s.dir));
});

test("a locked worktree is refused before anything changes, naming git worktree unlock", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "worktree", "lock", s.dir);
  await assert.rejects(done(s), new RegExp(`is locked; run git worktree unlock .+, then Done again`));
  assert.notEqual(s.git(s.checkout, "rev-parse", "main"), s.pr.mergeCommit.oid);
  s.git(s.checkout, "worktree", "unlock", s.dir);
  await done(s);
  assertDone(s);
});

test("an ignored file outside plan.disposable is refused before anything changes, not deleted with the worktree", async (t) => {
  const s = sandbox(t);
  // Ignored through the repo's exclude file, so the merged tree stays as the PR left it.
  fs.appendFileSync(path.join(s.checkout, ".git", "info", "exclude"), ".env\nnode_modules\n");
  fs.writeFileSync(path.join(s.dir, ".env"), "TOKEN=example\n");
  fs.mkdirSync(path.join(s.dir, "lib", "node_modules", "x"), { recursive: true });
  fs.writeFileSync(path.join(s.dir, "lib", "node_modules", "x", "index.js"), "\n");
  await assert.rejects(done(s), (e) => e.message.includes("has ignored files its removal would delete") && e.message.endsWith("\n.env"));
  assert.ok(fs.existsSync(path.join(s.dir, ".env")));
  assert.notEqual(s.git(s.checkout, "rev-parse", "main"), s.pr.mergeCommit.oid);
  // Named in plan.disposable, it goes with the worktree, as .tmp/ and node_modules/ do.
  await finish({ redmine: s.redmine, checkouts: s.checkouts, id: ID, view: s.view, disposable: [...DISPOSABLE, ".env"] });
  assertDone(s);
});

test("an ignored file where the merge adds a tracked one is refused, not overwritten", async (t) => {
  const s = sandbox(t);
  // The PR's file is ignored in this checkout, as when a PR starts tracking a path it ignored.
  fs.appendFileSync(path.join(s.checkout, ".git", "info", "exclude"), "done.js\n");
  fs.writeFileSync(path.join(s.checkout, "done.js"), "// Peter's own copy\n");
  await assert.rejects(done(s), (e) => e.message.includes("has files where origin/main adds tracked ones") && e.message.endsWith("\ndone.js"));
  assert.equal(fs.readFileSync(path.join(s.checkout, "done.js"), "utf8"), "// Peter's own copy\n");
  assert.ok(fs.existsSync(s.dir));
});

test("an ignored file at a rename's new path, or with a name git would quote, is refused, not overwritten", async (t) => {
  for (const name of ["moved.js", "café.js"]) {
    const s = sandbox(t, { merged: false });
    // The PR renames a file main already tracks, so git sees a rename, not an add.
    fs.writeFileSync(path.join(s.checkout, "old.js"), "// Long enough that git pairs the rename\n".repeat(5));
    s.git(s.checkout, "add", "old.js");
    s.git(s.checkout, "commit", "--quiet", "-m", "old");
    s.git(s.checkout, "push", "--quiet");
    s.git(s.dir, "pull", "--quiet", "--rebase", "origin", "main");
    s.git(s.dir, "mv", "old.js", name);
    s.git(s.dir, "commit", "--quiet", "-m", "rename");
    s.git(s.dir, "push", "--quiet", "--force");
    s.pr.headRefOid = s.git(s.dir, "rev-parse", "HEAD");
    s.merge();
    fs.appendFileSync(path.join(s.checkout, ".git", "info", "exclude"), `${name}\n`);
    fs.writeFileSync(path.join(s.checkout, name), "// Peter's own copy\n");
    await assert.rejects(done(s), (e) => e.message.includes("has files where origin/main adds tracked ones") && e.message.includes(`\n${name}`), name);
    assert.equal(fs.readFileSync(path.join(s.checkout, name), "utf8"), "// Peter's own copy\n");
  }
});

test("an ignored file where the merge adds a folder is refused, not deleted", async (t) => {
  const s = sandbox(t, { merged: false });
  fs.mkdirSync(path.join(s.dir, "notes"));
  fs.writeFileSync(path.join(s.dir, "notes", "a.md"), "tracked\n");
  s.git(s.dir, "add", "notes");
  s.git(s.dir, "commit", "--quiet", "-m", "notes");
  s.git(s.dir, "push", "--quiet");
  s.pr.headRefOid = s.git(s.dir, "rev-parse", "HEAD");
  s.merge();
  fs.appendFileSync(path.join(s.checkout, ".git", "info", "exclude"), "notes\n");
  fs.writeFileSync(path.join(s.checkout, "notes"), "Peter's own\n");
  await assert.rejects(done(s), (e) => e.message.includes("has files where origin/main adds tracked ones") && e.message.endsWith("\nnotes"));
  assert.equal(fs.readFileSync(path.join(s.checkout, "notes"), "utf8"), "Peter's own\n");
  assert.ok(fs.existsSync(s.dir));
});

test("a branch that moves while Done runs stops Done, and is kept", async (t) => {
  const s = sandbox(t);
  // A commit lands on the issue's branch just before the worktree goes, after Done read the tips.
  const bin = fs.mkdtempSync(path.join(s.root, "bin-"));
  const realGit = execFileSync("sh", ["-c", "command -v git"], { encoding: "utf8" }).trim();
  const PATH = process.env.PATH;
  process.env.PATH = `${bin}:${PATH}`;
  t.after(() => (process.env.PATH = PATH));
  fs.writeFileSync(path.join(bin, "git"), `#!/bin/sh\nif [ "$1 $2" = "worktree remove" ]; then "${realGit}" -C "${s.dir}" -c user.name=Test -c user.email=test@example.com commit --quiet --allow-empty -m late; fi\nexec "${realGit}" "$@"\n`, { mode: 0o755 });
  await assert.rejects(done(s), new RegExp(`the branch ${BRANCH} moved from `));
  assert.deepEqual(branches(s), [BRANCH, "main"]);
  assert.equal(s.git(s.checkout, "log", "-1", "--format=%s", BRANCH), "late");
});

test("the issue's branch in the main checkout switches straight to the merge, never through the old base", async (t) => {
  const s = sandbox(t, { merged: false });
  // The old base tracks notes.txt and the PR deletes it, so neither HEAD nor the merge has it, and
  // this checkout now keeps an ignored one there.
  fs.writeFileSync(path.join(s.checkout, "notes.txt"), "tracked\n");
  s.git(s.checkout, "add", "notes.txt");
  s.git(s.checkout, "commit", "--quiet", "-m", "notes");
  s.git(s.checkout, "push", "--quiet");
  s.git(s.dir, "pull", "--quiet", "--rebase", "origin", "main");
  s.git(s.dir, "rm", "--quiet", "notes.txt");
  s.git(s.dir, "commit", "--quiet", "-m", "drop notes");
  s.git(s.dir, "push", "--quiet", "--force");
  s.pr.headRefOid = s.git(s.dir, "rev-parse", "HEAD");
  s.merge();
  onIssueBranch(s);
  fs.appendFileSync(path.join(s.checkout, ".git", "info", "exclude"), "notes.txt\n");
  fs.writeFileSync(path.join(s.checkout, "notes.txt"), "Peter's own\n");
  const result = await done(s);
  assert.equal(fs.readFileSync(path.join(s.checkout, "notes.txt"), "utf8"), "Peter's own\n");
  assert.deepEqual(result.notes.slice(0, 2), ["Brought main up to the merge.", `Switched ${s.checkout} to main.`]);
  assertDone(s);
});

test("a long-lived head branch in the main checkout isn't the issue's, and is never deleted", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "switch", "--quiet", "-c", "develop");
  s.pr.headRefName = "develop";
  const result = await done(s);
  assert.equal(result.dir, s.dir);
  assert.equal(s.git(s.checkout, "branch", "--show-current"), "develop");
  assert.deepEqual(branches(s), ["develop", "main"]);
});

test("the issue's branch in a main checkout with no local base switches to origin's, with no second note", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "worktree", "remove", s.dir);
  s.git(s.checkout, "switch", "--quiet", BRANCH);
  s.git(s.checkout, "branch", "--quiet", "-D", "main");
  const result = await done(s);
  assert.ok(result.notes.includes(`Switched ${s.checkout} to main.`));
  assert.ok(!result.notes.includes("Brought main up to the merge."), result.notes.join(" "));
  assertDone(s);
});

test("a worktree on another of the issue's branches goes with that branch as well as the PR's", async (t) => {
  const s = sandbox(t);
  s.git(s.dir, "switch", "--quiet", "-c", "fix/82-renamed");
  await done(s);
  assertDone(s);
});

test("Start's setup marker goes with the worktree", async (t) => {
  const s = sandbox(t);
  const marker = path.join(s.checkout, ".git", `plan-setting-up-${ID}`);
  fs.writeFileSync(marker, "");
  await done(s);
  assert.ok(!fs.existsSync(marker));
});

test("a branch with no worktree that moved on since the merge is kept, and the issue still closes", async (t) => {
  const s = sandbox(t);
  s.git(s.dir, "commit", "--quiet", "--allow-empty", "-m", "after the merge");
  s.git(s.checkout, "worktree", "remove", s.dir);
  const result = await done(s);
  assert.deepEqual(branches(s), [BRANCH, "main"]);
  assert.ok(result.notes.some((n) => n.startsWith(`Kept the branch ${BRANCH}: it's at `)), result.notes.join(" "));
  assert.equal(s.issue.status.name, "Closed");
});

test("a main checkout on another branch stays there, and its main is brought up to the merge", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "switch", "--quiet", "-c", "spike");
  const result = await done(s);
  assert.equal(s.git(s.checkout, "branch", "--show-current"), "spike");
  assert.equal(s.git(s.checkout, "rev-parse", "main"), s.pr.mergeCommit.oid);
  assert.ok(result.notes.includes(`Left ${s.checkout} on spike, and brought main up to date.`));
  assert.deepEqual(branches(s), ["main", "spike"]);
});

test("the issue's branch in the main checkout is switched off, then deleted; untracked files go along", async (t) => {
  const s = sandbox(t);
  s.git(s.checkout, "worktree", "remove", s.dir);
  s.git(s.checkout, "switch", "--quiet", BRANCH);
  fs.writeFileSync(path.join(s.checkout, "notes.txt"), "scratch\n");
  const result = await done(s);
  assert.equal(result.dir, null);
  assert.ok(result.notes.includes(`Switched ${s.checkout} to main.`));
  assert.ok(fs.existsSync(path.join(s.checkout, "notes.txt")));
  assertDone(s);
});

test("a failed fetch of Redmine's commits doesn't stop Done", async (t) => {
  const s = sandbox(t);
  s.redmine.fetchCommits = async () => { throw new Error("Redmine refused the key (403)"); };
  const result = await done(s);
  assert.ok(result.notes.includes("Redmine didn't fetch the commits: Redmine refused the key (403)."));
  assertDone(s);
});

test("an issue the fetch closed gets the note, and no second status", async (t) => {
  const s = sandbox(t);
  s.redmine.fetchCommits = async () => (s.issue.status = { name: "Closed", is_closed: true });
  await done(s);
  assert.deepEqual(s.updates, [{ id: ID, notes: doneNote(s.pr) }]);
  assertDone(s);
});

test("a close Redmine drops is refused, the note is kept, and the next Done sends only the status", async (t) => {
  const s = sandbox(t);
  const update = s.redmine.update;
  s.redmine.update = async (id, fields) => update(id, { ...fields, status_id: undefined });
  await assert.rejects(done(s), (e) => {
    assert.match(e.message, new RegExp(`Redmine kept #${ID} In Progress: an open issue blocks it, or it has an open subtask; the note was saved`));
    // What it changed before it failed comes with the error.
    assert.deepEqual(e.result, { dir: s.dir, notes: ["Brought main up to the merge.", `Removed the worktree ${s.dir}.`, `Deleted the branch ${BRANCH}.`] });
    return true;
  });
  s.redmine.update = update;
  await done(s);
  assert.deepEqual(s.updates.map((u) => Object.keys(u).filter((k) => u[k] !== undefined)), [["id", "notes"], ["id", "status_id"]]);
  assertDone(s);
});

test("Done refuses a message that isn't an issue number, an issue not In Progress, and one with no PR", async (t) => {
  const s = sandbox(t);
  for (const id of ["82", 0, 1.5, null]) {
    await assert.rejects(finish({ redmine: s.redmine, checkouts: s.checkouts, id, view: s.view }), /Done needs an issue number/);
  }
  s.issue.status = { name: "New", is_closed: false };
  await assert.rejects(done(s), /#82 is New; refresh Next up/);
  s.issue.status = { name: "In Progress", is_closed: false };
  s.issue.custom_fields[0].value = "";
  await assert.rejects(done(s), /#82 links no pull request; Ship it first/);
  assert.deepEqual(s.views, []);
  assert.ok(fs.existsSync(s.dir));
});

test("a Done under way in another window is refused before anything changes", async (t) => {
  const s = sandbox(t);
  const held = path.join(s.checkout, ".git", `plan-busy-${ID}`);
  fs.writeFileSync(held, `${process.pid} Done\n`);
  await assert.rejects(done(s), (e) => e.message.startsWith(`#${ID} is being finished in another window; if none is, delete ${held}`));
  // Open on the same row takes the same lock, so neither runs under the other.
  fs.writeFileSync(held, `${process.pid} Start\n`);
  await assert.rejects(done(s), (e) => e.message.startsWith(`#${ID} is being started in another window`));
  assert.ok(fs.existsSync(s.dir));
  assert.deepEqual(s.updates, []);
});

// Fault injection: each step of Done fails once, and the next Done must end where a clean one
// does. A new step that writes gets a row here, with its row in docs/failure-modes.md.
function stubGit(t, s) {
  const bin = fs.mkdtempSync(path.join(s.root, "bin-"));
  const realGit = execFileSync("sh", ["-c", "command -v git"], { encoding: "utf8" }).trim();
  const PATH = process.env.PATH;
  process.env.PATH = `${bin}:${PATH}`;
  t.after(() => (process.env.PATH = PATH));
  fs.writeFileSync(path.join(bin, "git"), `#!/bin/sh\nif [ -f "${bin}/fail" ] && [ "$1 $2" = "$(cat "${bin}/fail")" ]; then rm "${bin}/fail"; echo injected >&2; exit 1; fi\nexec "${realGit}" "$@"\n`, { mode: 0o755 });
  return (args) => fs.writeFileSync(path.join(bin, "fail"), args);
}

function failOnce(object, method) {
  const real = object[method];
  object[method] = async () => {
    object[method] = real;
    throw new Error(`injected: ${method}`);
  };
}

// Each row fails one step once; prepare puts the main checkout where that step runs.
const onIssueBranch = (s) => {
  s.git(s.checkout, "worktree", "remove", s.dir);
  s.git(s.checkout, "switch", "--quiet", BRANCH);
};
const onSpike = (s) => s.git(s.checkout, "switch", "--quiet", "-c", "spike");
const STEPS = {
  "reading the issue": { fail: (s) => failOnce(s.redmine, "issue") },
  "reading the project": { fail: (s) => failOnce(s.redmine, "projectIdentifier") },
  "reading the PR": { fail: (s) => failOnce(s, "view") },
  "fetching origin": { fail: (s, gitOnce) => gitOnce("fetch --quiet") },
  "removing the worktree": { fail: (s, gitOnce) => gitOnce("worktree remove") },
  "moving the base before the switch": { prepare: onIssueBranch, fail: (s, gitOnce) => gitOnce("update-ref refs/heads/main") },
  "switching to the base": { prepare: onIssueBranch, fail: (s, gitOnce) => gitOnce("switch --quiet") },
  "fast-forwarding the base": { fail: (s, gitOnce) => gitOnce("merge --quiet") },
  "moving the base under another branch": { prepare: onSpike, on: "spike", fail: (s, gitOnce) => gitOnce("update-ref refs/heads/main") },
  "deleting the branch": { fail: (s, gitOnce) => gitOnce("update-ref -d") },
  "reading the status id": { fail: (s) => failOnce(s.redmine, "statusId") },
  "the Redmine update": { fail: (s) => failOnce(s.redmine, "update") },
};

for (const [step, { prepare, fail, on }] of Object.entries(STEPS)) {
  test(`a Done that fails at ${step} ends, on the next Done, where a clean one does`, async (t) => {
    const s = sandbox(t);
    prepare?.(s);
    fail(s, stubGit(t, s));
    await assert.rejects(done(s), /injected/);
    await done(s);
    assertDone(s, on);
    const updates = s.updates.length;
    assert.deepEqual((await done(s)).dir, null);
    assert.equal(s.updates.length, updates);
  });
}

test("the commit fetch posts the web service's key in the body, and names a refused key", async (t) => {
  const http = require("http");
  const { Redmine } = require("../redmine");
  // A security that answers for the sys key only, as the Keychain would.
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), "plan-keychain-"));
  t.after(() => fs.rmSync(bin, { recursive: true, force: true }));
  fs.writeFileSync(path.join(bin, "security"), '#!/bin/sh\n[ "$3" = test-sys ] && echo sys-key\n', { mode: 0o755 });
  const PATH = process.env.PATH;
  process.env.PATH = `${bin}:${PATH}`;
  t.after(() => (process.env.PATH = PATH));
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, body });
      res.statusCode = new URLSearchParams(body).get("key") === "sys-key" ? 200 : 403;
      res.end();
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  const url = `http://127.0.0.1:${server.address().port}`;

  await new Redmine({ url, sysService: "test-sys" }).fetchCommits("gh-plan");
  assert.deepEqual(seen, [{ method: "POST", url: "/sys/fetch_changesets", body: "key=sys-key&id=gh-plan" }]);
  await assert.rejects(new Redmine({ url, sysService: "other" }).fetchCommits("gh-plan"), /no Keychain item "other"; store the key of Redmine's repository web service in it/);
  fs.writeFileSync(path.join(bin, "security"), "#!/bin/sh\necho wrong\n", { mode: 0o755 });
  await assert.rejects(new Redmine({ url, sysService: "test-sys" }).fetchCommits("gh-plan"), /refused the key in Keychain item "test-sys" \(403\); run setup.rb with SYS_API_KEY/);
});

test("replies or checklist ticks pr hasn't sent are refused before anything changes, not deleted with the worktree", async (t) => {
  const s = sandbox(t);
  const round = path.join(s.dir, ".tmp", String(ID));
  fs.writeFileSync(path.join(round, "issue.md"), "- [x] Done\n");
  fs.mkdirSync(path.join(round, "replies"));
  fs.writeFileSync(path.join(round, "replies", "11.md"), "Fixed.\n");
  fs.writeFileSync(path.join(round, "replies", "notes.md"), "Misnamed: pr refuses it, so it's still unsent.\n");
  fs.writeFileSync(path.join(round, "replies", "12.txt"), "Misnamed: pr skips it, so it's still unsent.\n");
  // A dotfile, such as Finder's, goes with the worktree.
  fs.writeFileSync(path.join(round, "replies", ".DS_Store"), "");
  // Another issue's round in the same worktree counts too.
  const other = path.join(s.dir, ".tmp", "85");
  fs.mkdirSync(other);
  fs.writeFileSync(path.join(other, "issue.md"), "- [x] Done\n");
  await assert.rejects(done(s), (e) => e.message.includes("replies or checklist ticks pr hasn't sent") &&
    e.message.includes("python3 ~/.claude/skills/gh-issues/scripts/checklist.py") &&
    e.message.endsWith(`\n.tmp/${ID}/issue.md\n.tmp/${ID}/replies/11.md\n.tmp/${ID}/replies/12.txt\n.tmp/${ID}/replies/notes.md\n.tmp/85/issue.md`));
  assert.ok(fs.existsSync(path.join(round, "issue.md")));
  assert.notEqual(s.git(s.checkout, "rev-parse", "main"), s.pr.mergeCommit.oid);
  // Once pr has sent them, it deletes them, and Done goes ahead.
  fs.rmSync(path.join(round, "issue.md"));
  fs.rmSync(path.join(round, "replies", "11.md"));
  fs.rmSync(path.join(round, "replies", "notes.md"));
  fs.rmSync(path.join(round, "replies", "12.txt"));
  fs.rmSync(path.join(other, "issue.md"));
  await done(s);
  assertDone(s);
});
