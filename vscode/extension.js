// The Next up panel: a webview in the Plan sidebar. The webview makes no request itself; it asks
// this host for the list and for each issue, hands every link click back to open in a tab, and asks
// it to start or finish an issue.
const vscode = require("vscode");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { Redmine } = require("./redmine");
const { ACTION, checkId, start, expandHome } = require("./start");
const { reviewFile, roundFiles, shipTab, stage, stagedChanges, openStaged, check, shipped, keepEdits } = require("./ship");
const { finish } = require("./done");
const { handoffFile, issueUrl, writeHandoff, takeHandoffFor } = require("./handoff");
const { reportFile, reportRound, reportChange, reportPage, Reports } = require("./review");

// How soon after a refresh starts a window's focus may refresh Next up again, and how long one still
// waiting holds focus refreshes off: past that, a refresh stuck on the Keychain or a slow Redmine's
// four reads no longer stops them.
const FOCUS_GAP = 5000;
const FOCUS_HOLD = 3 * 60 * 1000;

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
      const handOff = (result) =>
        writeHandoff(this.storage, { dir: result.dir, prompt: `Work on Redmine #${id}`, url: issueUrl(config.get("redmineUrl"), id) });
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
    try {
      // Kept across refreshes and for the detail reads, so they share one Keychain read, and a
      // refresh stuck on a Keychain prompt shares it rather than opening another. A change to the
      // plan settings drops it; a failed Keychain read or a refused key is read again (Redmine.key).
      const client = (this.client ??= redmine());
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

// In the worktree's window: take its handoff (handoff.js), one take at a time.
let taking = false;
async function takeHandoff(storage) {
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!folder || taking) return;
  taking = true;
  try {
    await takeHandoffFor(vscode, storage, folder);
  } finally {
    taking = false;
  }
}

// When an issue's PR body for pr is written, in .tmp/<n>/, with a commit message or review replies
// beside it, they open for review in the leftmost editor group, the PR body in
// front. Once all are open, a save moves nothing.
function watchHandover(context) {
  const watcher = vscode.workspace.createFileSystemWatcher("**/.tmp/*/{commit.txt,pr-body.md,replies/*}");
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
  // The round's report, when its tab opened first, goes back in front: it's read before the files.
  // A report older than the files is the last round's, which mustn't cover this one's.
  const report = path.join(dir, "report.html");
  const mtime = (file) => fileStat(file)?.mtimeMs ?? 0;
  if (mtime(report) >= Math.max(...files.map(mtime))) reports?.reveal(report);
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

// Go, from the title bar of any of an issue's round files: save them, check them, stage the working
// tree's changes, as pr's commit will, and open them, whose title bar then holds Ship. With nothing to commit, Ship sits on the
// review files instead. Only Ship runs pr; once pr succeeds, the review files and diffs close and the
// issue's Pull requests field links its PR.
// armed is the issue Go armed Ship for, { root, id, report, message }: its round report's stamp when
// Go began and its commit.txt's once Go saved it, so Ship ships only the report and message Go saw.
let armed = null;
// The issue Ship is running for, from its checks to pr's end, so Go doesn't stage while pr commits;
// and the one Go is running for, so Ship doesn't disarm or commit what Go is still staging.
let shipping = null;
let going = null;

function arm(review) {
  armed = review;
  reports?.armedChanged();
  // armed as it is now: a redraw in armedChanged can disarm, and its own setContext goes first.
  return vscode.commands.executeCommand("setContext", "plan.shipReady", Boolean(armed));
}

// The Review tabs (review.js): each issue's .tmp/<n>/report.html, opened or refreshed whenever it's
// saved in this window's worktree, with Go and Ship on it.
let reports = null;

// A file's fs.Stats, or undefined once it's gone.
function fileStat(file) {
  return fs.statSync(file, { throwIfNoEntry: false });
}

// A file's modified time and size, which change with each write; null once it's gone.
function stamp(file) {
  const stat = fileStat(file);
  return stat?.isFile() ? `${stat.mtimeMs}:${stat.size}` : null;
}

function reviewHost(media) {
  const say = { info: vscode.window.showInformationMessage, warning: vscode.window.showWarningMessage, error: vscode.window.showErrorMessage };
  return {
    stamp,
    read: (file) => fs.readFileSync(file, "utf8"),
    exists: (file) => stamp(file) !== null,
    armed: (review) => (armed && armed.root === review.root && armed.id === review.id ? armed : null),
    disarm: () => arm(null),
    say: (level, text) => say[level](text),
    // A URL for the browser tab; any other argument is a file's path.
    execute: (command, arg) =>
      arg === undefined ? vscode.commands.executeCommand(command)
        : vscode.commands.executeCommand(command, command === "workbench.action.browser.open" ? arg : vscode.Uri.file(arg)),
    open(review, from) {
      // Scripts are for the page's own, by nonce; the report's frame is sandboxed without them.
      const options = { enableScripts: true, localResourceRoots: [media] };
      // In the leftmost group, with the round's files, without taking the focus from the editor; or
      // the panel VS Code restored after a reload, where it was.
      const panel = from ?? vscode.window.createWebviewPanel(
        "plan.review", `Review #${review.id}`, { viewColumn: vscode.ViewColumn.One, preserveFocus: true },
        { ...options, retainContextWhenHidden: true }
      );
      if (from) panel.webview.options = options;
      // Taken once: a disposed panel's webview property throws.
      const { webview } = panel;
      webview.onDidReceiveMessage((message) =>
        reports.receive(review, message).catch((error) => vscode.window.showErrorMessage(`Plan: #${review.id}'s report: ${error.message}`))
      );
      return {
        webview,
        reveal: () => panel.reveal(undefined, true),
        setHtml: (html) => (webview.html = html),
        post: (message) => webview.postMessage(message),
        onDispose: (callback) => panel.onDidDispose(callback),
        onVisible: (callback) => panel.onDidChangeViewState(() => panel.visible && callback()),
      };
    },
    render(review, html) {
      const { webview } = review.panel;
      const written = review.shown && new Date(Number(review.shown.split(":")[0]));
      return reportPage({
        id: review.id,
        file: review.file,
        html,
        round: reportRound(html),
        written: written && written.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }),
        shown: review.shown,
        ship: review.ship,
        busy: review.busy,
        doing: review.doing,
        staged: review.staged,
        nonce: crypto.randomBytes(16).toString("base64"),
        cspSource: webview.cspSource,
        css: webview.asWebviewUri(vscode.Uri.joinPath(media, "review.css")),
        js: webview.asWebviewUri(vscode.Uri.joinPath(media, "review.js")),
      });
    },
  };
}

// Show report, for a report's path, or the report in the active editor.
function showReport(uri) {
  const file = (uri || vscode.window.activeTextEditor?.document.uri)?.fsPath || "";
  if (!reportFile(file)) {
    vscode.window.showErrorMessage("Plan: Show report works on an issue's .tmp/<n>/report.html");
    return;
  }
  if (!reports.show(file)) vscode.window.showErrorMessage(`Plan: ${file} isn't there to show`);
}

// A file watcher covers only this window's folders, so a report opens in its own worktree's window.
function watchReports(context) {
  const watcher = vscode.workspace.createFileSystemWatcher("**/.tmp/*/report.html");
  const timers = new Map();
  // A save can land as several writes; show the report once they settle.
  const changed = (uri) => {
    if (!reportFile(uri.fsPath)) return;
    clearTimeout(timers.get(uri.fsPath));
    timers.set(uri.fsPath, setTimeout(() => (timers.delete(uri.fsPath), showReport(uri)), 300));
  };
  watcher.onDidCreate(changed);
  watcher.onDidChange(changed);
  context.subscriptions.push(watcher);
}

// Saves the issue's review files, unsaved edits included, and checks what pr will find; null, with
// the reason shown, when pr can't run. Ship's check also refuses a change Go didn't stage.
async function prepare(uri, staged = false) {
  const review = reviewFile((uri || vscode.window.activeTextEditor?.document.uri)?.fsPath || "");
  if (!review) {
    vscode.window.showErrorMessage("Plan: Go works from an issue's review files in .tmp/<n>/: commit.txt, pr-body.md or a reply");
    return null;
  }
  const { root, id } = review;
  // Every file pr may send, replies included, so it never sends what's on disk while
  // an edit waits unsaved in its tab. A tab whose file pr already sent and deleted isn't saved, or
  // the next pr would send it again. The round's report isn't sent, and a save of it would be a new
  // report, which disarms the Ship this Go arms.
  const round = path.join(root, ".tmp", String(id)) + path.sep;
  for (const doc of vscode.workspace.textDocuments) {
    if (doc.isDirty && doc.uri.fsPath.startsWith(round) && !reportFile(doc.uri.fsPath) && fs.existsSync(doc.uri.fsPath)) await doc.save();
  }
  // Go stages, and pr commits, what's on disk: an edit still unsaved in a tab would be left out.
  const unsaved = vscode.workspace.textDocuments
    .filter((doc) => doc.isDirty && doc.uri.scheme === "file" && doc.uri.fsPath.startsWith(`${root}${path.sep}`) && !doc.uri.fsPath.startsWith(round))
    .map((doc) => path.relative(root, doc.uri.fsPath));
  if (unsaved.length) {
    vscode.window.showErrorMessage(`Plan: #${id} can't ship: ${unsaved.join(", ")} ${unsaved.length > 1 ? "have" : "has"} unsaved edits; save or revert, then Go. Nothing ran.`);
    return null;
  }
  let found;
  try {
    found = await check(root, id, { staged });
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

// VS Code's git extension's repository for root; null when the extension is missing or disabled, or
// hasn't found the checkout.
async function gitRepository(root) {
  try {
    const extension = vscode.extensions.getExtension("vscode.git");
    const exports = extension && (extension.isActive ? extension.exports : await extension.activate());
    return exports?.getAPI(1).getRepository(vscode.Uri.file(root)) ?? null;
  } catch {
    return null;
  }
}

async function go(uri) {
  if (shipping) {
    vscode.window.showErrorMessage(`Plan: Ship is running for #${shipping}; press Go once it ends. Nothing ran.`);
    return;
  }
  if (going) {
    vscode.window.showErrorMessage(`Plan: Go is running for #${going}; press Go once it ends. Nothing ran.`);
    return;
  }
  const issue = reviewFile((uri || vscode.window.activeTextEditor?.document.uri)?.fsPath || "");
  // Not an issue's file: prepare says so.
  if (!issue) return prepare(uri);
  // Set before the first await, so a Ship pressed while Go runs waits for it.
  going = issue.id;
  try {
    await goChecked(uri, issue);
  } finally {
    going = null;
  }
}

async function goChecked(uri, issue) {
  // The report as Go finds it, before its checks run, so one written while they run waits for Go.
  const report = stamp(path.join(issue.root, ".tmp", String(issue.id), "report.html"));
  const ready = await prepare(uri);
  // The commit message as Go saved it, stamped before the tab reads it, so Ship commits only the one
  // shown.
  const message = ready && stamp(path.join(ready.root, ".tmp", String(ready.id), "commit.txt"));
  // A Go that fails disarms its own issue's Ship, never another's.
  const own = armed?.root === issue.root && armed?.id === issue.id;
  if (!ready) {
    if (own) await arm(null);
    return;
  }
  if (reports.changedSinceGo({ ...ready, report })) {
    if (own) await arm(null);
    const now = stamp(path.join(ready.root, ".tmp", String(ready.id), "report.html"));
    vscode.window.showWarningMessage(`Plan: #${ready.id}'s report ${reportChange(now)} while Go ran; read it, then press Go again. Nothing was staged.`);
    return;
  }
  const taken = { root: ready.root, id: ready.id, report, message };
  await arm(taken);
  // A report the arm's redraw found changed has disarmed Ship again, and said so.
  if (armed !== taken) return;
  if (ready.dirty) {
    try {
      await stage(ready.root);
    } catch (error) {
      await arm(null);
      vscode.window.showErrorMessage(`Plan: #${ready.id}'s changes couldn't be staged: ${error.message}`);
      return;
    }
    // The staged diff shows in the issue's Review tab; one git can't print, as over execFile's buffer,
    // opens in VS Code's Staged Changes instead.
    let diff;
    let text;
    try {
      diff = await stagedChanges(ready.root);
      text = fs.readFileSync(path.join(ready.root, ".tmp", String(ready.id), "commit.txt"), "utf8");
    } catch (error) {
      const missed = await openStaged(await gitRepository(ready.root), (command) => vscode.commands.executeCommand(command));
      const why = /maxBuffer/i.test(error.message) ? "the diff is too large to show" : error.message;
      vscode.window.showWarningMessage(
        missed
          ? `Plan: #${ready.id}'s changes are staged, but they didn't open: ${why}; ${missed} Source Control's Staged Changes lists them; Ship commits them with pr ${ready.id}.`
          : `Plan: #${ready.id}'s changes are staged and open in Staged Changes, not the Review tab: ${why}. Ship commits them with pr ${ready.id}.`,
      );
      return;
    }
    // A report saved while git ran disarmed Ship, and said so.
    if (!reports.staged(ready, { message: text, diff })?.staged) return;
    vscode.window.showInformationMessage(`Plan: #${ready.id}'s changes are staged and show in Review #${ready.id}; Ship commits them with pr ${ready.id}.`);
  } else {
    vscode.window.showInformationMessage(`Plan: nothing to commit; the rocket pushes ${ready.branch} and updates its PR.`);
  }
}

async function ship(refresh) {
  if (going) {
    vscode.window.showErrorMessage(`Plan: Go is running for #${going}; press Ship once it ends. Nothing ran.`);
    return;
  }
  if (!armed) {
    vscode.window.showErrorMessage("Plan: press Go on an issue's review files first.");
    return;
  }
  const taken = armed;
  const { root, id } = taken;
  // Go waits from here, so it never stages while Ship's checks or pr run.
  shipping = id;
  let exitCode;
  let found;
  try {
    found = await prepare(vscode.Uri.file(`${root}/.tmp/${id}/pr-body.md`), true);
    await arm(null);
    if (!found) return;
    if (reports.changedSinceGo(taken)) {
      // A report shown while the checks ran has disarmed Ship already, and said so.
      const now = stamp(path.join(root, ".tmp", String(id), "report.html"));
      if (!taken.warned) vscode.window.showErrorMessage(`Plan: #${id}'s report ${reportChange(now)} since Go; pr didn't run. Read it, then press Go again.`);
      return;
    }
    // Ship's checks save an edit to the commit message; one Go didn't show waits for Go.
    if (stamp(path.join(root, ".tmp", String(id), "commit.txt")) !== taken.message) {
      vscode.window.showErrorMessage(`Plan: #${id}'s commit.txt changed since Go; pr didn't run. Press Go again to see it.`);
      return;
    }
    exitCode = await runPr(root, id);
  } finally {
    shipping = null;
  }
  if (exitCode !== 0) {
    vscode.window.showErrorMessage(`Plan: pr ${id} stopped (exit ${exitCode}); its terminal says why. Go and Ship again once it's fixed.`);
    // pr may have sent some of the round before it stopped.
    refresh();
    return;
  }
  const stay = new Set();
  // A reply edited while pr ran, which pr has since sent and deleted, holds edits pr
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
    // PR_STAGED: pr commits what Go staged, and refuses a change made since.
    new vscode.ProcessExecution(pr, [String(id)], { cwd: root, env: { PR_STAGED: "1" } })
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
  reports = new Reports(reviewHost(view.media));
  watchReports(context);
  // A Review tab open before a window reload comes back, showing its report as it is now and Go.
  context.subscriptions.push(
    vscode.window.registerWebviewPanelSerializer("plan.review", {
      async deserializeWebviewPanel(panel, state) {
        if (!reportFile(state?.file || "") || !reports.show(state.file, panel)) panel.dispose();
      },
    })
  );
  let focused = vscode.window.state.focused;
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("plan.nextUp", view),
    vscode.commands.registerCommand("plan.refresh", () => view.refresh()),
    // A changed URL, query or Keychain item needs a new client, and any plan.* change a new
    // refresh, which also discards one still answering with the old settings. Other settings keep
    // the client, and the key it read, so they bring no Keychain prompt.
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (!event.affectsConfiguration("plan")) return;
      const client = ["redmineUrl", "project", "query", "keychainService", "sysKeychainService"];
      if (client.some((name) => event.affectsConfiguration(`plan.${name}`))) view.client = undefined;
      view.refresh();
    }),
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
    ),
    vscode.commands.registerCommand("plan.showReport", (uri) => showReport(uri))
  );
}

module.exports = { activate };
