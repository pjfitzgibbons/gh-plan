// The Review tab: each issue's round report, .tmp/<n>/report.html, in one editor tab, Review #<n>,
// with Go and Ship on it. Claude writes the report, so its HTML is untrusted: it's shown in a
// sandboxed frame that runs no script, and only the extension's own buttons, outside the frame,
// send a command. No vscode import: plain functions, tested under node --test.
const path = require("path");

const REPORT_FILE = /^(.+)\/\.tmp\/([1-9][0-9]*)\/report\.html$/;

// The issue and its checkout from a report's path, <root>/.tmp/<n>/report.html; null for any other
// file.
function reportFile(file) {
  const match = REPORT_FILE.exec(file);
  return match ? { root: match[1], id: Number(match[2]) } : null;
}

// The round the report says it is, from <meta name="plan-round" content="<k>">; null without one.
function reportRound(html) {
  const meta = /<meta\b[^>]*\bname=["']?plan-round["']?[^>]*>/i.exec(html);
  const round = meta && /\bcontent=["']?([1-9][0-9]*)\b/i.exec(meta[0]);
  return round ? Number(round[1]) : null;
}

const escape = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// The page the tab shows: the extension's bar, with the round, when the report was written and the
// one button, Go or Ship, above the report in a frame. The frame is sandboxed without allow-scripts,
// and the policy, which the frame inherits, runs only the script carrying this render's nonce, so
// nothing in the report runs. allow-same-origin, without scripts, only lets the page's script lend
// the frame VS Code's theme and catch its link clicks. shown is the render's stamp, which a click
// sends back so it never acts on a report other than the one on screen; busy, while its Go or Ship
// runs, keeps the button off across a refresh, and doing names which one.
function reportPage({ id, html, round, written, shown, ship, busy, doing, nonce, cspSource, css, js }) {
  const label = `Review #${id}${round ? ` · round ${round}` : ""} · written ${written}`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${css}">
</head>
<body data-shown="${escape(shown)}" data-ship="${ship ? "1" : ""}" data-busy="${busy ? "1" : ""}" data-doing="${doing === "ship" ? "ship" : "go"}" data-id="${id}">
<header role="toolbar" aria-label="Review #${id}">
<span id="label">${escape(label)}</span>
<span id="hint"></span>
<button id="act" type="button"></button>
</header>
<iframe id="report" title="${escape(`#${id}'s round report`)}" sandbox="allow-same-origin" srcdoc="${escape(html)}"></iframe>
<script nonce="${nonce}" src="${js}"></script>
</body>
</html>`;
}

// What a link clicked in the report opens: an http(s) URL in a browser tab, or a file in the
// issue's .tmp/<n>/ (dir) that exists; null for anything else, a command: or file: link among them.
function linkTarget(dir, href, exists) {
  if (typeof href !== "string" || !href.trim()) return null;
  if (/^https?:\/\//i.test(href)) return { url: href };
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("/") || href.startsWith("#")) return null;
  let rel;
  try {
    rel = decodeURIComponent(href.replace(/[?#].*$/, ""));
  } catch {
    return null;
  }
  const file = path.resolve(dir, rel);
  return file.startsWith(`${dir}${path.sep}`) && exists(file) ? { file } : null;
}

// Every open Review tab in this window, one per report, by its path. host gives the VS Code side:
//   open(review)         a new panel, { reveal(), setHtml(html), post(message), onDispose(callback) }
//   render(review, html) the page for the report's text, with review.shown and review.ship set
//   stamp(file)          the report's modified time, or null once it's gone; read(file) its text
//   armed(review)        whether Go has armed Ship for review's issue; disarm() undoes it
//   execute(command, …)  runs a VS Code command; exists(file); say(level, text) shows a message
class Reports {
  constructor(host) {
    this.host = host;
    this.panels = new Map();
    // Each report's stamp when last shown, kept after its tab closes, so a report Go never saw
    // disarms Ship even when it opens a new tab.
    this.seen = new Map();
  }

  // Shows the report at file in its issue's tab: brings the tab to the front with the report as it
  // is on disk, or opens one when the issue has none here. Returns the tab's review, or null.
  show(file) {
    const issue = reportFile(file);
    if (!issue) return null;
    // Stamped before it's read: a write in between leaves a stamp older than what's shown, so a
    // click is refused and the tab shows the file again, never the other way round.
    const shown = this.host.stamp(file);
    if (shown === null) return null;
    let html;
    try {
      html = this.host.read(file);
    } catch {
      return null;
    }
    let review = this.panels.get(file);
    if (review) {
      review.panel.reveal();
    } else {
      review = { ...issue, file, busy: false, doing: null };
      review.panel = this.host.open(review);
      this.panels.set(file, review);
      review.panel.onDispose(() => this.panels.get(file) === review && this.panels.delete(file));
    }
    // A new report is a new round to review: a Ship that Go armed for the last one waits for Go.
    const disarms = this.host.armed(review) && this.seen.get(file) !== shown;
    if (disarms) {
      this.host.disarm();
      this.host.say("warning", `Plan: #${review.id}'s report changed since Go; press Go again to ship it.`);
    }
    this.seen.set(file, shown);
    review.disarmed = disarms;
    review.shown = shown;
    review.ship = this.host.armed(review);
    review.panel.setHtml(this.host.render(review, html));
    return review;
  }

  // Brings file's tab, if it has one, back in front of the round's other files.
  reveal(file) {
    this.panels.get(file)?.panel.reveal();
  }

  // Tells every tab whether its button is Go or Ship, after Go arms Ship or Ship ends.
  armedChanged() {
    for (const review of this.panels.values()) this.state(review);
  }

  state(review) {
    review.ship = this.host.armed(review);
    review.panel.post({ type: "state", ship: review.ship, busy: review.busy, doing: review.doing });
  }

  // A message from review's page. Only the button's go or ship, and a link's href, are taken from
  // it; the issue is the tab's own.
  async receive(review, message) {
    if (message?.type === "link") {
      const target = linkTarget(path.dirname(review.file), message.href, this.host.exists);
      if (target?.url) await this.host.execute("workbench.action.browser.open", target.url);
      else if (target?.file) await this.host.execute("vscode.open", target.file);
      return;
    }
    if (message?.type !== "go" && message?.type !== "ship") return;
    if (review.busy) return;
    const now = this.host.stamp(review.file);
    if (now === null) {
      this.host.say("error", `Plan: #${review.id}'s report is gone; nothing ran.`);
      this.state(review);
      return;
    }
    if (message.shown !== review.shown || now !== review.shown) {
      // show() says so itself when the change disarms Ship.
      if (!this.show(review.file)) this.state(review);
      else if (!review.disarmed) {
        this.host.say("warning", `Plan: #${review.id}'s report changed; it's shown again. Press ${review.ship ? "Ship" : "Go"} once you've read it.`);
      }
      return;
    }
    if (message.type === "ship" && !this.host.armed(review)) {
      this.host.say("error", `Plan: press Go on #${review.id}'s report first.`);
      this.state(review);
      return;
    }
    review.busy = true;
    review.doing = message.type;
    this.state(review);
    try {
      // The Next up commands themselves, so Go checks the round's files and Ship refuses any changed
      // since Go, as from the title bar. Go finds the issue from its PR body's path.
      if (message.type === "go") await this.host.execute("plan.openChanges", path.join(review.root, ".tmp", String(review.id), "pr-body.md"));
      else await this.host.execute("plan.runPr");
    } finally {
      review.busy = false;
      this.state(review);
    }
  }
}

module.exports = { reportFile, reportRound, reportPage, linkTarget, Reports };
