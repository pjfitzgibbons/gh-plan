// node --test vscode/test: what Start hands the worktree's window, and what that window opens, with
// a stubbed vscode.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { HANDOFF_FRESH_MS, handoffFile, issueUrl, writeHandoff, takeHandoffFor } = require("../handoff");

const dir = "/work/gh-plan.worktrees/102";
const url = "http://127.0.0.1:3010/issues/102";

// A storage folder of the test's own, removed after it.
function storage(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "handoff-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// The commands run and errors shown; a command named in fail rejects.
function stubVscode(fail = []) {
  const ran = [];
  const errors = [];
  return {
    ran,
    errors,
    commands: {
      executeCommand: async (...args) => {
        ran.push(args);
        if (fail.includes(args[0])) throw new Error(`${args[0]} failed`);
      },
    },
    window: { showErrorMessage: (message) => errors.push(message) },
  };
}

test("the issue's URL is plan.redmineUrl's /issues/<n>", () => {
  assert.equal(issueUrl("http://127.0.0.1:3010", 102), url);
  assert.equal(issueUrl("http://127.0.0.1:3010//", 102), url);
});

test("the handoff holds the folder, the prompt, the issue's URL and when it was written", (t) => {
  const store = storage(t);
  writeHandoff(store, { dir, prompt: "Work on Redmine #102", url });
  const handoff = JSON.parse(fs.readFileSync(handoffFile(store, dir), "utf8"));
  assert.deepEqual({ ...handoff, at: typeof handoff.at }, { dir, prompt: "Work on Redmine #102", url, at: "number" });
  assert.deepEqual(fs.readdirSync(store), [path.basename(handoffFile(store, dir))]);
});

test("the worktree's window opens the page in the first group, then Claude beside it", async (t) => {
  const store = storage(t);
  writeHandoff(store, { dir, prompt: "Work on Redmine #102", url });
  const vscode = stubVscode();
  await takeHandoffFor(vscode, store, dir);
  assert.deepEqual(vscode.ran, [
    ["workbench.action.focusFirstEditorGroup"],
    ["workbench.action.browser.open", url],
    ["claude-vscode.terminal.open", "Work on Redmine #102", [], "beside"],
  ]);
  assert.deepEqual(vscode.errors, []);
  assert.equal(fs.existsSync(handoffFile(store, dir)), false);
});

test("another folder's window opens nothing", async (t) => {
  const store = storage(t);
  writeHandoff(store, { dir, prompt: "Work on Redmine #102", url });
  // A file named for another folder holding this handoff, as a hash collision would.
  fs.copyFileSync(handoffFile(store, dir), handoffFile(store, "/work/gh-plan"));
  const vscode = stubVscode();
  await takeHandoffFor(vscode, store, "/work/gh-plan");
  await takeHandoffFor(vscode, store, "/work/other");
  assert.deepEqual(vscode.ran, []);
  assert.equal(fs.existsSync(handoffFile(store, dir)), true);
});

test("a page that fails to open names its URL, and Claude still starts", async (t) => {
  const store = storage(t);
  writeHandoff(store, { dir, prompt: "Work on Redmine #102", url });
  const vscode = stubVscode(["workbench.action.browser.open"]);
  await takeHandoffFor(vscode, store, dir);
  assert.equal(vscode.errors.length, 1);
  assert.match(vscode.errors[0], /127\.0\.0\.1:3010\/issues\/102/);
  assert.deepEqual(vscode.ran.at(-1), ["claude-vscode.terminal.open", "Work on Redmine #102", [], "beside"]);
  assert.equal(fs.existsSync(handoffFile(store, dir)), false);
});

test("only an http(s) URL opens; Claude starts either way", async (t) => {
  const store = storage(t);
  writeHandoff(store, { dir, prompt: "Work on Redmine #102", url: "file:///etc/passwd" });
  const vscode = stubVscode();
  await takeHandoffFor(vscode, store, dir);
  assert.deepEqual(vscode.ran, [["claude-vscode.terminal.open", "Work on Redmine #102", [], "beside"]]);
  assert.match(vscode.errors[0], /file:\/\/\/etc\/passwd/);
});

test("a handoff written before the page, with no url, starts Claude alone, with no error", async (t) => {
  const store = storage(t);
  writeHandoff(store, { dir, prompt: "Work on Redmine #102" });
  const vscode = stubVscode();
  await takeHandoffFor(vscode, store, dir);
  assert.deepEqual(vscode.ran, [["claude-vscode.terminal.open", "Work on Redmine #102", [], "beside"]]);
  assert.deepEqual(vscode.errors, []);
});

test("a handoff Claude didn't take stays, and the retry opens the page and Claude again", async (t) => {
  const store = storage(t);
  writeHandoff(store, { dir, prompt: "Work on Redmine #102", url });
  const failing = stubVscode(["claude-vscode.terminal.open"]);
  await takeHandoffFor(failing, store, dir);
  assert.match(failing.errors[0], /couldn't start Claude Code/);
  assert.equal(fs.existsSync(handoffFile(store, dir)), true);
  const retry = stubVscode();
  await takeHandoffFor(retry, store, dir);
  assert.deepEqual(retry.ran.map(([command]) => command), [
    "workbench.action.focusFirstEditorGroup",
    "workbench.action.browser.open",
    "claude-vscode.terminal.open",
  ]);
  assert.equal(fs.existsSync(handoffFile(store, dir)), false);
});

test("a handoff over a day old is dropped, and opens nothing", async (t) => {
  const store = storage(t);
  const file = handoffFile(store, dir);
  fs.writeFileSync(file, JSON.stringify({ dir, prompt: "Work on Redmine #102", url, at: Date.now() - HANDOFF_FRESH_MS - 1 }));
  const vscode = stubVscode();
  await takeHandoffFor(vscode, store, dir);
  assert.deepEqual(vscode.ran, []);
  assert.equal(fs.existsSync(file), false);
});
