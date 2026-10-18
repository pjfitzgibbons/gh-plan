// The Next up panel: a webview in the Plan sidebar. The webview makes no request itself; it asks
// this host for the list and for each issue, hands every link click back to open in a tab, and asks
// it to start an issue.
const vscode = require("vscode");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { Redmine } = require("./redmine");
const { start } = require("./start");
const { stackedBelowFirst } = require("./layout");

// A new window can't be handed a task directly, so Start leaves one in the extension's global
// storage, which every window shares; the new window's activation picks it up and starts Claude.
// One file per worktree, so a second Start before the first window opens doesn't replace the first's.
const HANDOFF_FRESH_MS = 2 * 60 * 1000;

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
  });
}

class NextUp {
  constructor(context) {
    this.media = vscode.Uri.joinPath(context.extensionUri, "media");
    this.storage = context.globalStorageUri.fsPath;
    // Starts under way, by issue: a second click joins the first rather than racing it.
    this.starting = new Map();
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
          this.post({ type: "detail", id: message.id, issue: await redmine().issue(message.id) });
        } catch (error) {
          this.post({ type: "detail", id: message.id, error: error.message });
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
    }
  }

  start(id) {
    // The issue number is the only thing taken from the webview.
    if (!Number.isInteger(id) || id <= 0) {
      vscode.window.showErrorMessage(`Plan: Start needs an issue number, not ${JSON.stringify(id)}`);
      return;
    }
    if (!this.starting.has(id)) {
      this.starting.set(id, this.startOnce(id).finally(() => this.starting.delete(id)));
    }
    return this.starting.get(id);
  }

  async startOnce(id) {
    const config = vscode.workspace.getConfiguration("plan");
    let result;
    try {
      result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `Starting #${id}` },
        () => start({ redmine: redmine(), checkouts: config.get("checkouts"), id, confirm: confirmBlockers })
      );
    } catch (error) {
      vscode.window.showErrorMessage(`Plan: #${id} didn't start. ${error.message}`);
      return;
    }
    if (!result) return;
    if (result.notes.length) vscode.window.showInformationMessage(`Plan: ${result.notes.join(" ")}`);
    // Claude starts only in a window opened by the Start that finished setting the work up, including
    // one that finished what a stopped Start began, or by an Open after Claude failed to start;
    // otherwise reopening started work changes nothing.
    const file = handoffFile(this.storage, result.dir);
    if (result.started || fs.existsSync(file)) {
      fs.mkdirSync(this.storage, { recursive: true });
      fs.writeFileSync(file, JSON.stringify({ dir: result.dir, prompt: `Work on Redmine #${id}`, at: Date.now() }));
    }
    await vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(result.dir), { forceNewWindow: true });
    this.refresh();
  }

  async refresh() {
    if (!this.view) return;
    const client = redmine();
    const markers = vscode.workspace.getConfiguration("plan").get("projectMarkers");
    try {
      const issues = (await client.list()).map((issue) => ({
        ...issue,
        marker: markers[issue.project] || issue.project.slice(0, 2),
      }));
      this.post({ type: "list", base: client.settings.url, issues });
    } catch (error) {
      this.post({ type: "error", message: error.message });
    }
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

// In the window Start opened: start Claude beside, once, if the handoff is fresh and for this folder.
// A handoff Claude didn't take stays, so the issue's Open button can try again.
async function takeHandoff(storage) {
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!folder) return;
  const file = handoffFile(storage, folder);
  let handoff;
  try {
    handoff = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return;
  }
  if (folder !== handoff.dir || Date.now() - handoff.at > HANDOFF_FRESH_MS) return;
  try {
    // Undocumented: Claude Code's own command, (prompt, args, location), read from its source (2.1.301).
    await vscode.commands.executeCommand("claude-vscode.terminal.open", handoff.prompt, [], "beside");
  } catch (error) {
    vscode.window.showErrorMessage(`Plan: couldn't start Claude Code: ${error.message}. To retry, reload this window, or press Open on the issue in Next up.`);
    return;
  }
  fs.rmSync(file, { force: true });
}

// When an issue's commit message and PR body for pr are both written, in .tmp/<n>/, they open for
// review in the leftmost editor group: the commit message above, the PR body below. Once both are
// open, a save moves nothing.
function watchHandover(context) {
  const watcher = vscode.workspace.createFileSystemWatcher("**/.tmp/*/{commit.txt,pr-body.md}");
  const timers = new Map();
  // The two files land moments apart; wait for the writes to settle.
  const changed = (uri) => {
    const dir = path.dirname(uri.fsPath);
    clearTimeout(timers.get(dir));
    timers.set(dir, setTimeout(() => (timers.delete(dir), openHandover(dir)), 500));
  };
  watcher.onDidCreate(changed);
  watcher.onDidChange(changed);
  context.subscriptions.push(watcher);
}

async function openHandover(dir) {
  const files = ["commit.txt", "pr-body.md"].map((name) => path.join(dir, name));
  if (!files.every((file) => fs.existsSync(file))) return;
  const open = new Set(
    vscode.window.tabGroups.all.flatMap((g) => g.tabs).map((t) => t.input?.uri?.fsPath).filter(Boolean)
  );
  if (files.every((file) => open.has(file))) return;
  // The pair stacks inside the leftmost group, or the only one; groups to its right stay as they are.
  // A group already directly below the leftmost is column 2, and is reused.
  const stacked = stackedBelowFirst(await vscode.commands.executeCommand("vscode.getEditorLayout"));
  const [commit, body] = files.map((file) => vscode.Uri.file(file));
  await vscode.window.showTextDocument(commit, { viewColumn: 1, preview: false });
  // A new group goes below the active one, which is now the leftmost, and takes focus.
  if (!stacked) await vscode.commands.executeCommand("workbench.action.newGroupBelow");
  await vscode.window.showTextDocument(body, { viewColumn: stacked ? 2 : vscode.ViewColumn.Active, preview: false });
}

function activate(context) {
  const view = new NextUp(context);
  takeHandoff(view.storage);
  watchHandover(context);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("plan.nextUp", view),
    vscode.commands.registerCommand("plan.refresh", () => view.refresh())
  );
}

module.exports = { activate };
