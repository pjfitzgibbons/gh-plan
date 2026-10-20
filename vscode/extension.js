// The Next up panel: a webview in the Plan sidebar. The webview makes no request itself; it asks
// this host for the list and for each issue, hands every link click back to open in a tab, and asks
// it to start or finish an issue.
const vscode = require("vscode");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { Redmine } = require("./redmine");
const { ACTION, checkId, start, expandHome } = require("./start");
const { reviewFile, roundFiles, shipTab, check, shipped, keepEdits } = require("./ship");
const { finish } = require("./done");

// A new window can't be handed a task directly, so Start leaves one in the extension's global
// storage, which every window shares; the worktree's window picks it up when it opens, or at once if
// it was open already, and starts Claude.
// One file per worktree, so a second Start before the first window opens doesn't replace the first's.
// A handoff is kept until Claude starts, so a reload can retry it; after a day, opening that
// worktree no longer starts Claude unasked.
const HANDOFF_FRESH_MS = 24 * 60 * 60 * 1000;

// How soon after a refresh starts a window's focus may refresh Next up again, and how long one still
// waiting holds focus refreshes off: past that, a refresh stuck on the Keychain or a slow Redmine's
// four reads no longer stops them.
const FOCUS_GAP = 5000;
const FOCUS_HOLD = 3 * 60 * 1000;

function handoffFile(storage, dir) {
  return path.join(storage, `handoff-${crypto.createHash("sha256").update(dir).digest("hex").slice(0, 16)}.json`);
}

function redmine() {
  const config = vscode.workspace.getConfiguration("plan");
  return new Redmine({
    url: config.get("redmineUrl"),
    project: config.get("project"),
    query: config.get("query"),
    service: config.get("keychainService"),
    sysService: config.get("sysKeychainService"),
  });
}

// What a Done did, and, when it removed a worktree, the window that has lost its folder.
function said(result) {
  const here = vscode.workspace.workspaceFolders?.some((f) => f.uri.fsPath === result.dir);
  const close = result.dir ? (here ? " Close this window." : ` Close the window on ${result.dir}, if one is open.`) : "";
  return `${result.notes.join(" ")}${close}`.trim();
}

class NextUp {
  constructor(context) {
    this.media = vscode.Uri.joinPath(context.extensionUri, "media");
    this.storage = context.globalStorageUri.fsPath;
    // The Start or Done under way, by issue: { action, promise }.
    this.busy = new Map();
    // The latest refresh: an older one answering after it shows nothing.
    this.refreshes = 0;
    // When the latest refresh started, and whether it's still waiting on Redmine.
    this.refreshedAt = 0;
    this.loading = false;
    // Project identifiers by id, from earlier lists (Redmine.list).
    this.known = {};
  }

  resolveWebviewView(view) {
    this.view = view;
    view.webview.options = { enableScripts: true, localResourceRoots: [this.media] };
    view.webview.html = this.html(view.webview);
    view.webview.onDidReceiveMessage((message) => this.receive(message));
  }

  async receive(message) {
    switch (message.type) {
      case "ready":
        return this.refresh();
      case "detail":
        try {
          this.post({ type: "detail", id: message.id, seq: message.seq, issue: await (this.client || redmine()).issue(message.id) });
        } catch (error) {
          this.post({ type: "detail", id: message.id, seq: message.seq, error: error.message });
        }
        return;
      case "open":
        // A new Integrated Browser tab; the webview never navigates itself.
        if (/^https?:\/\//.test(message.url)) {
          await vscode.commands.executeCommand("workbench.action.browser.open", message.url);
        }
        return;
      case "start":
        return this.start(message.id);
      case "done":
        return this.done(message.id);
    }
  }

  done(id) {
    // The issue number is the only thing taken from the webview; the PR comes from the issue.
    try {
      checkId(id, "Done");
    } catch (error) {
      vscode.window.showErrorMessage(`Plan: ${error.message}`);
      return;
    }
    return this.once(id, "Done", () => this.doneOnce(id));
  }

  // One Start or Done per issue in this window: a second click on the same button joins the first,
  // and the other button is refused by name, rather than by the lock as if from another window.
  once(id, action, run) {
    const busy = this.busy.get(id);
    if (busy && busy.action !== action) {
      vscode.window.showErrorMessage(`Plan: #${id} is being ${busy.action === "Done" ? "finished" : "started"} here; try again once that ends`);
      return busy.promise;
    }
    if (!busy) this.busy.set(id, { action, promise: run().finally(() => this.busy.delete(id)) });
    return this.busy.get(id).promise;
  }

  async doneOnce(id) {
    const config = vscode.workspace.getConfiguration("plan");
    try {
      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `Finishing #${id}` },
        () => finish({ redmine: redmine(), checkouts: config.get("checkouts"), id, disposable: config.get("disposable") })
      );
      vscode.window.showInformationMessage(`Plan: #${id} is done. ${said(result)}`);
    } catch (error) {
      // A Done that failed part way says what it changed first.
      const before = error.result && said(error.result);
      vscode.window.showErrorMessage(`Plan: #${id} isn't done. ${error.message}${before ? ` Before that: ${before}` : ""}`);
    } finally {
      this.refresh();
    }
  }

  start(id) {
    // The issue number is the only thing taken from the webview.
    try {
      checkId(id);
    } catch (error) {
      vscode.window.showErrorMessage(`Plan: ${error.message}`);
      return;
    }
    return this.once(id, "Start", () => this.startOnce(id));
  }

  async startOnce(id) {
    const config = vscode.workspace.getConfiguration("plan");
    try {
      // Claude starts only in a window opened by the Start that finished setting the work up, including
      // one that finished what a stopped Start began, or by an Open after Claude failed to start;
      // otherwise reopening started work changes nothing. Setup isn't finished until the handoff is
      // written, so a failed write is retried by the next Start.
      const handOff = (result) => {
        const file = handoffFile(this.storage, result.dir);
        fs.mkdirSync(this.storage, { recursive: true });
        // Written aside and renamed in, so a watching window never reads it half written.
        fs.writeFileSync(`${file}.tmp`, JSON.stringify({ dir: result.dir, prompt: `Work on Redmine #${id}`, at: Date.now() }));
        fs.renameSync(`${file}.tmp`, file);
      };
      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `Starting #${id}` },
        () => start({ redmine: redmine(), checkouts: config.get("checkouts"), id, confirm: confirmBlockers, handOff })
      );
      if (!result) return;
      if (result.notes.length) vscode.window.showInformationMessage(`Plan: ${result.notes.join(" ")}`);
      // An Open after Claude failed to start rewrites the kept handoff, fresh, for the watching window.
      if (!result.started && fs.existsSync(handoffFile(this.storage, result.dir))) handOff(result);
      await vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(result.dir), { forceNewWindow: true });
    } catch (error) {
      vscode.window.showErrorMessage(`Plan: #${id} didn't start. ${error.message}`);
    } finally {
      this.refresh();
    }
  }

  async refresh() {
    if (!this.view) return;
    const at = ++this.refreshes;
    this.refreshedAt = Date.now();
    this.loading = true;
    this.client = undefined;
    try {
      // Kept for the detail reads that follow the list, so they share its Keychain read.
      const client = (this.client = redmine());
      const markers = vscode.workspace.getConfiguration("plan").get("projectMarkers");
      // The view's progress bar runs while Redmine answers, as Source Control's does on a Git refresh.
      const listed = await vscode.window.withProgress({ location: { viewId: "plan.nextUp" } }, () => client.list(this.known));
      const issues = listed.map((issue) => ({
        ...issue,
        marker: markers[issue.project] || issue.project.slice(0, 2),
        action: ACTION[issue.status],
        done: issue.status === "In Progress" && Boolean(issue.pullRequests),
      }));
      if (at === this.refreshes) this.post({ type: "list", base: client.settings.url, issues });
    } catch (error) {
      if (at === this.refreshes) this.post({ type: "error", message: error.message });
    } finally {
      if (at === this.refreshes) this.loading = false;
    }
  }

  // A focus refresh, skipped while one is waiting on Redmine or started in the last few seconds, so
  // switching windows quickly, or while Redmine is slow, sends one request, not one per switch.
  refreshOnFocus() {
    const since = Date.now() - this.refreshedAt;
    if (!this.view?.visible || since < FOCUS_GAP || (this.loading && since < FOCUS_HOLD)) return;
    this.refresh();
  }

  post(message) {
    this.view && this.view.webview.postMessage(message);
  }

  html(webview) {
    const nonce = crypto.randomBytes(16).toString("base64");
    const css = webview.asWebviewUri(vscode.Uri.joinPath(this.media, "view.css"));
    const js = webview.asWebviewUri(vscode.Uri.joinPath(this.media, "view.js"));
    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${css}">
</head>
<body>
<p id="status">Loading…</p>
<div id="list"></div>
<script nonce="${nonce}" src="${js}"></script>
</body>
</html>`;
  }
}

async function confirmBlockers(blockers) {
  const list = blockers.map((b) => `#${b.id} ${b.subject} (${b.status})`).join("\n");
  const choice = await vscode.window.showWarningMessage(
    blockers.length === 1 ? "An open issue blocks this one" : `${blockers.length} open issues block this one`,
    { modal: true, detail: `${list}\n\nStart anyway?` },
    "Start anyway"
  );
  return choice === "Start anyway";
}

// In the worktree's window: start Claude beside, once, if the handoff is fresh and for this folder.
// A handoff Claude didn't take stays, so the issue's Open button can try again.
let taking = false;
async function takeHandoff(storage) {
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!folder || taking) return;
  taking = true;
  try {
    await takeHandoffFor(storage, folder);
  } finally {
    taking = false;
  }
}

async function takeHandoffFor(storage, folder) {
  const file = handoffFile(storage, folder);
  let handoff;
  try {
    handoff = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return;
  }
  if (folder !== handoff.dir) return;
  if (Date.now() - handoff.at > HANDOFF_FRESH_MS) {
    fs.rmSync(file, { force: true });
    return;
  }
  try {
    // Undocumented: Claude Code's own command, (prompt, args, location), read from its source (2.1.301).
    await vscode.commands.executeCommand("claude-vscode.terminal.open", handoff.prompt, [], "beside");
  } catch (error) {
    vscode.window.showErrorMessage(`Plan: couldn't start Claude Code: ${error.message}. To retry, reload this window, or press Open on the issue in Next up.`);
    return;
  }
  fs.rmSync(file, { force: true });
}

// When an issue's PR body for pr is written, in .tmp/<n>/, with a commit message, review replies or
// checklist ticks beside it, they open for review in the leftmost editor group, the PR body in
// front. Once all are open, a save moves nothing.
function watchHandover(context) {
  const watcher = vscode.workspace.createFileSystemWatcher("**/.tmp/*/{commit.txt,pr-body.md,issue.md,replies/*}");
  const timers = new Map();
  // The files land moments apart; wait for the writes to settle.
  const changed = (uri) => {
    const review = reviewFile(uri.fsPath);
    if (!review) return;
    const dir = path.join(review.root, ".tmp", String(review.id));
    clearTimeout(timers.get(dir));
    timers.set(dir, setTimeout(() => (timers.delete(dir), openHandover(dir)), 500));
  };
  watcher.onDidCreate(changed);
  watcher.onDidChange(changed);
  context.subscriptions.push(watcher);
}

async function openHandover(dir) {
  const files = roundFiles(dir);
  if (!files.length) return;
  const open = new Set(
    vscode.window.tabGroups.all.flatMap((g) => g.tabs).map((t) => t.input?.uri?.fsPath).filter(Boolean)
  );
  const missing = files.filter((file) => !open.has(file));
  if (!missing.length) return;
  // Each one not yet open, as a tab in the leftmost group, or the only one; then the PR body, in front.
  const body = files[files.length - 1];
  for (const file of [...missing.filter((file) => file !== body), body]) {
    await vscode.window.showTextDocument(vscode.Uri.file(file), { viewColumn: 1, preview: false });
  }
}

// openFolder only focuses a window already open on the worktree, so that window watches for its
// handoff too.
function watchHandoff(context, storage) {
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!folder) return;
  const name = path.basename(handoffFile(storage, folder));
  const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(storage), name));
  watcher.onDidCreate(() => takeHandoff(storage));
  watcher.onDidChange(() => takeHandoff(storage));
  context.subscriptions.push(watcher);
}

// Go, from the title bar of any of an issue's round files: save them, check them, and open the
// working tree's changes, whose title bar then holds Ship. With nothing to commit, Ship sits on the
// review files instead. Only Ship runs pr; once pr succeeds, the review files and diffs close and the
// issue's Pull requests field links its PR.
let armed = null;

function arm(review) {
  armed = review;
  return vscode.commands.executeCommand("setContext", "plan.shipReady", Boolean(review));
}

// Saves the issue's review files, unsaved edits included, and checks what pr will find; null, with
// the reason shown, when pr can't run.
async function prepare(uri) {
  const review = reviewFile((uri || vscode.window.activeTextEditor?.document.uri)?.fsPath || "");
  if (!review) {
    vscode.window.showErrorMessage("Plan: Go works from an issue's review files in .tmp/<n>/: commit.txt, pr-body.md, issue.md or a reply");
    return null;
  }
  const { root, id } = review;
  // Every file pr may send, replies and issue.md included, so it never sends what's on disk while
  // an edit waits unsaved in its tab. A tab whose file pr already sent and deleted isn't saved, or
  // the next pr would send it again.
  const round = path.join(root, ".tmp", String(id)) + path.sep;
  for (const doc of vscode.workspace.textDocuments) {
    if (doc.isDirty && doc.uri.fsPath.startsWith(round) && fs.existsSync(doc.uri.fsPath)) await doc.save();
  }
  let found;
  try {
    found = await check(root, id);
  } catch (error) {
    vscode.window.showErrorMessage(`Plan: #${id} can't ship. ${error.message}`);
    return null;
  }
  if (found.problems.length) {
    vscode.window.showErrorMessage(`Plan: #${id} can't ship: ${found.problems.join("; ")}. Nothing ran.`);
    return null;
  }
  return { ...review, ...found };
}

async function go(uri) {
  const ready = await prepare(uri);
  await arm(ready && { root: ready.root, id: ready.id });
  if (!ready) return;
  if (ready.dirty) {
    await vscode.commands.executeCommand("git.viewChanges");
    vscode.window.showInformationMessage(`Plan: #${ready.id}'s changes are open; the rocket runs pr ${ready.id}.`);
  } else {
    vscode.window.showInformationMessage(`Plan: nothing to commit; the rocket pushes ${ready.branch} and updates its PR.`);
  }
}

async function ship(refresh) {
  if (!armed) {
    vscode.window.showErrorMessage("Plan: press Go on an issue's review files first.");
    return;
  }
  const { root, id } = armed;
  const found = await prepare(vscode.Uri.file(`${root}/.tmp/${id}/pr-body.md`));
  await arm(null);
  if (!found) return;

  const exitCode = await runPr(root, id);
  if (exitCode !== 0) {
    vscode.window.showErrorMessage(`Plan: pr ${id} stopped (exit ${exitCode}); its terminal says why. Go and Ship again once it's fixed.`);
    // pr may have sent some of the round before it stopped.
    refresh();
    return;
  }
  const stay = new Set();
  // A reply or issue.md edited while pr ran, which pr has since sent and deleted, holds edits pr
  // never saw. They're kept beside it as <file>.unsent, then the tab is reverted: closing it would
  // offer Save, and a save writes it back for the next Ship to send again.
  for (const doc of vscode.workspace.textDocuments) {
    const review = reviewFile(doc.uri.fsPath);
    if (!doc.isDirty || review?.root !== root || review.id !== id || fs.existsSync(doc.uri.fsPath)) continue;
    let kept;
    try {
      kept = keepEdits(doc.uri.fsPath, doc.getText());
    } catch (error) {
      vscode.window.showErrorMessage(`Plan: pr sent ${path.relative(root, doc.uri.fsPath)}, but its later edits couldn't be kept: ${error.message}. Its tab stays open; copy them out before closing it.`);
      stay.add(doc.uri.fsPath);
      continue;
    }
    vscode.window.showWarningMessage(`Plan: pr sent ${path.relative(root, doc.uri.fsPath)} as saved; the edits made since are in ${path.relative(root, kept)}.`);
    await vscode.window.showTextDocument(doc, { preview: false });
    await vscode.commands.executeCommand("workbench.action.files.revert");
  }
  const tabs = vscode.window.tabGroups.all.flatMap((g) => g.tabs)
    .filter((t) => shipTab(t.input, root, id) && !stay.has(t.input?.uri?.fsPath));
  await vscode.window.tabGroups.close(tabs);
  const { failed, message } = await shipped({ root, id, branch: found.branch, redmine });
  if (failed) vscode.window.showErrorMessage(`Plan: ${message}`);
  else vscode.window.showInformationMessage(`Plan: ${message}`);
  refresh();
}

// pr <id> in its own terminal, in the worktree; resolves to its exit code. pr is run by its path: a
// login shell's PATH finds /usr/bin/pr, the paginator, first.
function runPr(root, id) {
  const pr = expandHome(vscode.workspace.getConfiguration("plan").get("pr"));
  const folder = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(root)) || vscode.TaskScope.Workspace;
  const task = new vscode.Task(
    { type: "plan", issue: id }, folder, `pr ${id}`, "plan",
    new vscode.ProcessExecution(pr, [String(id)], { cwd: root })
  );
  task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, panel: vscode.TaskPanelKind.Dedicated, clear: true };
  return new Promise((resolve, reject) => {
    const ended = vscode.tasks.onDidEndTaskProcess((event) => {
      const { definition } = event.execution.task;
      if (definition.type !== "plan" || definition.issue !== id) return;
      ended.dispose();
      resolve(event.exitCode);
    });
    vscode.tasks.executeTask(task).then(undefined, (error) => (ended.dispose(), reject(error)));
  });
}

function activate(context) {
  const view = new NextUp(context);
  fs.mkdirSync(view.storage, { recursive: true });
  watchHandoff(context, view.storage);
  takeHandoff(view.storage);
  watchHandover(context);
  let focused = vscode.window.state.focused;
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("plan.nextUp", view),
    vscode.commands.registerCommand("plan.refresh", () => view.refresh()),
    // A detail read uses the last refresh's client; a changed URL or Keychain item needs a new one.
    vscode.workspace.onDidChangeConfiguration((event) => event.affectsConfiguration("plan") && (view.client = undefined)),
    // A window coming into focus refreshes its Next up, so it shows what changed while another
    // window, Redmine's web UI or a pr in a terminal had it; closing a window focuses another. A
    // hidden panel is skipped: it reloads, and refreshes, when it's shown. The event also fires when
    // a focused window goes idle or comes back (state.active), so only a change of focus counts.
    vscode.window.onDidChangeWindowState((state) => {
      if (state.focused && !focused) view.refreshOnFocus();
      focused = state.focused;
    }),
    // Go and Ship swapped names, so their IDs are new: a keybinding on an old ID finds no command,
    // where plan.ship, the old Go, would now run pr.
    vscode.commands.registerCommand("plan.openChanges", (uri) =>
      go(uri).catch((error) => vscode.window.showErrorMessage(`Plan: Go failed. ${error.message}`))
    ),
    // Ship's window keeps its focus while pr runs, so Ship refreshes its own Next up once it links.
    vscode.commands.registerCommand("plan.runPr", () =>
      ship(() => view.refresh()).catch((error) => vscode.window.showErrorMessage(`Plan: Ship failed. ${error.message}`))
    )
  );
}

module.exports = { activate };
