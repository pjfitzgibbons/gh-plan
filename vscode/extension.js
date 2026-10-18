// The Next up panel: a webview in the Plan sidebar. The webview makes no request itself; it asks
// this host for the list and for each issue, and hands every link click back to open in a tab.
const vscode = require("vscode");
const crypto = require("crypto");
const { Redmine } = require("./redmine");

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
  constructor(extensionUri) {
    this.media = vscode.Uri.joinPath(extensionUri, "media");
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
    }
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

function activate(context) {
  const view = new NextUp(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("plan.nextUp", view),
    vscode.commands.registerCommand("plan.refresh", () => view.refresh())
  );
}

module.exports = { activate };
