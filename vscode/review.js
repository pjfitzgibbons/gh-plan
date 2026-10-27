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
  const meta = /<meta\s(?:[^>]*\s)?name=(["']?)plan-round\1(?=[\s/>])[^>]*>/i.exec(html);
  const round = meta && /\scontent=(["']?)([1-9][0-9]*)\1(?=[\s/>])/i.exec(meta[0]);
  return round ? Number(round[2]) : null;
}

// How a report differs from the one Go saw, by its stamp now: deleted, or written again.
const reportChange = (now) => (now === null ? "was deleted" : "changed");

const escape = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// git diff's text as files: each one's path, the path it came from when renamed, how it changed
// (added, deleted, renamed or modified), whether it's binary, its counts and its hunks, each hunk's
// first old and new line and its lines as git prints them.
function stagedDiff(text) {
  const files = [];
  let file = null;
  let hunk = null;
  for (const line of String(text).split("\n")) {
    if (line.startsWith("diff --git ")) {
      // "a/<path> b/<path>": the path until --- and +++ name it, which they don't for a binary file
      // or a mode change.
      const both = line.slice("diff --git ".length);
      const name = both.slice(2, 2 + (both.length - 5) / 2);
      file = { path: name, from: name, status: "modified", binary: false, adds: 0, dels: 0, hunks: [] };
      files.push(file);
      hunk = null;
      continue;
    }
    if (!file) continue;
    const at = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (at) {
      hunk = { old: Number(at[1]), new: Number(at[2]), lines: [] };
      file.hunks.push(hunk);
    } else if (hunk) {
      if (!/^[ +\\-]/.test(line)) continue;
      hunk.lines.push(line);
      if (line[0] === "+") file.adds++;
      else if (line[0] === "-") file.dels++;
    } else if (line.startsWith("new file mode")) file.status = "added";
    else if (line.startsWith("deleted file mode")) file.status = "deleted";
    else if (line.startsWith("rename from ")) [file.from, file.status] = [line.slice("rename from ".length), "renamed"];
    else if (line.startsWith("rename to ")) file.path = line.slice("rename to ".length);
    // git ends a path holding a space with a tab here.
    else if (line.startsWith("--- a/")) file.from = line.slice("--- a/".length).replace(/\t$/, "");
    else if (line.startsWith("+++ b/")) file.path = line.slice("+++ b/".length).replace(/\t$/, "");
    else if (line.startsWith("Binary files ")) file.binary = true;
  }
  for (const each of files) if (each.status === "deleted") each.path = each.from;
  return files;
}

// The staged changes' section, all of it the extension's markup with the code escaped: the commit
// message, then each file's diff in a <details> of its own, with old and new line numbers.
function stagedSection({ message, files }) {
  const adds = files.reduce((sum, file) => sum + file.adds, 0);
  const dels = files.reduce((sum, file) => sum + file.dels, 0);
  const row = (old, now, cls, text) =>
    `<div><span class="ln">${old}</span><span class="ln">${now}</span><span class="${cls}">${escape(text)}</span></div>`;
  const diff = (file) => {
    if (file.binary) return '<p class="note">A binary file: its change doesn\'t show as text.</p>';
    if (!file.hunks.length) return '<p class="note">Only its mode changed.</p>';
    const rows = [];
    for (const hunk of file.hunks) {
      let [old, now] = [hunk.old, hunk.new];
      rows.push(`<div class="at"><span class="ln"></span><span class="ln"></span><span>@@ ${old} → ${now}</span></div>`);
      for (const line of hunk.lines) {
        if (line[0] === "+") rows.push(row("", now++, "add", line));
        else if (line[0] === "-") rows.push(row(old++, "", "del", line));
        else if (line[0] === " ") rows.push(row(old++, now++, "", line));
        else rows.push(row("", "", "note", line));
      }
    }
    return `<pre class="diff">${rows.join("")}</pre>`;
  };
  const name = (file) => (file.status === "renamed" ? `${file.from} → ${file.path}` : file.path);
  const items = files.map((file) =>
    `<details><summary><code>${escape(name(file))}</code> <span class="count">+${file.adds} −${file.dels}</span>` +
    `${file.status === "modified" ? "" : ` <span class="status">${file.status}</span>`}</summary>${diff(file)}</details>`
  );
  return `<section id="staged" aria-label="Staged changes">
<h2>Staged changes · ${files.length} file${files.length === 1 ? "" : "s"} +${adds} −${dels}</h2>
<h3>Commit message</h3>
<pre class="message">${escape(message ?? "")}</pre>
<h3>Files</h3>
${items.join("\n")}
</section>`;
}

// The page the tab shows: the extension's bar, with the round, when the report was written and the
// one button, Go or Ship, above the report in a frame. The frame is sandboxed without allow-scripts,
// and the policy, which the frame inherits, runs only the script carrying this render's nonce, so
// nothing in the report runs; an image shows only from a data: URI, so the report loads nothing.
// allow-same-origin, without scripts, only lets the page's script lend the frame VS Code's theme
// and catch its link clicks. shown is the render's stamp, which a click
// sends back so it never acts on a report other than the one on screen; busy, while its Go or Ship
// runs, keeps the button off across a refresh, and doing names which one. file is the report's
// path, which the page keeps so the tab comes back after a window reload. html is null with no
// report yet. staged is what Go staged, below the report, which the page shows while Ship is armed.
function reportPage({ id, file, html, round, written, shown, ship, busy, doing, staged, nonce, cspSource, css, js }) {
  const label = html === null ? `Review #${id} · no report yet` : `Review #${id}${round ? ` · round ${round}` : ""} · written ${written}`;
  const report = html === null
    ? '<p id="empty">No round report yet: Claude writes one after a review round.</p>'
    : `<iframe id="report" title="${escape(`#${id}'s round report`)}" sandbox="allow-same-origin" srcdoc="${escape(html)}"></iframe>`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${cspSource} 'unsafe-inline'; img-src data:; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${css}">
</head>
<body data-shown="${escape(shown ?? "")}" data-ship="${ship ? "1" : ""}" data-busy="${busy ? "1" : ""}" data-doing="${doing === "ship" ? "ship" : "go"}" data-id="${id}" data-file="${escape(file)}">
<header role="toolbar" aria-label="Review #${id}">
<span id="label">${escape(label)}</span>
<span id="hint"></span>
<button id="act" type="button"></button>
</header>
${report}
${staged ? stagedSection(staged) : ""}
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
//   open(review, from)   a panel, { reveal(), setHtml(html), post(message), onDispose(callback),
//                        onVisible(callback) }:
//                        a new one, or VS Code's panel from, restored after a window reload
//   render(review, html) the page for the report's text, or null with none, with review.shown,
//                        review.ship and review.staged set
//   stamp(file)          the report's modified time, or null once it's gone; read(file) its text
//   armed(review)        what Go armed Ship with for review's issue, { report }, the report's stamp
//                        when Go ran (null with no report), or null; disarm() undoes it
//   execute(command, …)  runs a VS Code command; exists(file); say(level, text) shows a message
class Reports {
  constructor(host) {
    this.host = host;
    this.panels = new Map();
  }

  // Shows the report at file in its issue's tab: brings the tab to the front with the report as it
  // is on disk, or opens one when the issue has none here; from is a panel VS Code restored, which
  // becomes the tab. Returns the tab's review, or null, when from is then for the caller to close.
  show(file, from) {
    return this.#open(file, from);
  }

  // Shows what Go staged for the issue, { message, diff } as git diff prints it, below its report,
  // opening its tab, with or without a report, when it has none here. Returns the tab's review, whose
  // staged is null when a new report has disarmed Ship since Go began.
  staged({ root, id }, { message, diff }) {
    return this.#open(path.join(root, ".tmp", String(id), "report.html"), null, { message, files: stagedDiff(diff) });
  }

  #open(file, from, staged) {
    const issue = reportFile(file);
    if (!issue) return null;
    const { shown, html } = this.#read(file);
    // Only Go's staged changes open a tab with no report.
    if (shown === null && !staged) return null;
    let review = this.panels.get(file);
    if (review && from) return null;
    if (review) {
      review.panel.reveal();
      // The same report, as from Show report or a second watcher event: the page stays, and keeps
      // its place in the report.
      if (!staged && review.shown === shown) {
        this.state(review);
        return review;
      }
    } else {
      review = { ...issue, file, busy: false, doing: null, staged: null };
      review.panel = this.host.open(review, from);
      this.panels.set(file, review);
      review.panel.onDispose(() => this.panels.get(file) === review && this.panels.delete(file));
      // A hidden tab may miss a post, so a tab shown again is told the state.
      review.panel.onVisible(() => this.state(review));
    }
    this.#draw(review, shown, html, staged);
    return review;
  }

  // The report's stamp and text, stamped before it's read: a write in between leaves a stamp older
  // than what's shown, so a click is refused and the tab shows the file again, never the other way
  // round. Both are null once it's gone.
  #read(file) {
    const shown = this.host.stamp(file);
    try {
      return { shown, html: shown === null ? null : this.host.read(file) };
    } catch {
      return { shown: null, html: null };
    }
  }

  // Draws the tab's page for the report as read, with Go's staged changes when it's given them.
  #draw(review, shown, html, staged) {
    // A report other than the one Go saw is a new round to review: Ship waits for Go again. The
    // mark tells a Ship whose checks are running that this has been said.
    const go = this.host.armed(review);
    if (go && go.report !== shown) {
      go.warned = true;
      this.host.disarm();
      this.host.say("warning", `Plan: #${review.id}'s report ${reportChange(shown)} since Go; press Go again to ship it.`);
    }
    // Staged changes belong to the Go that armed Ship on this report.
    if (staged) review.staged = this.host.armed(review) ? staged : null;
    else if (!this.host.armed(review)) review.staged = null;
    review.shown = shown;
    review.ship = this.ship(review);
    review.panel.setHtml(this.host.render(review, html));
  }

  // Whether the tab's button is Ship: Go armed it for this issue, on the report the tab shows.
  ship(review) {
    const go = this.host.armed(review);
    return Boolean(go && go.report === review.shown);
  }

  // Whether the issue's report isn't the one Go saw when it armed Ship, armed: Ship checks it once
  // its own checks have run, just before pr, so a report written while they ran is never shipped.
  changedSinceGo({ root, id, report }) {
    return this.host.stamp(path.join(root, ".tmp", String(id), "report.html")) !== report;
  }

  // Brings file's tab, if it has one, back in front of the round's other files.
  reveal(file) {
    this.panels.get(file)?.panel.reveal();
  }

  // Tells every tab whether its button is Go or Ship, after Go arms Ship or Ship ends.
  // A Go arming Ship drops the staged changes an earlier Go showed, before it stages, so Ship is
  // never shown over a diff it won't commit; its own arrive with staged().
  armedChanged() {
    for (const review of this.panels.values()) {
      if (review.staged && this.host.armed(review)) {
        review.staged = null;
        this.redraw(review);
      } else this.state(review);
    }
  }

  // The tab's page drawn again from the report as it is on disk; one changed since Go disarms Ship.
  redraw(review) {
    const { shown, html } = this.#read(review.file);
    this.#draw(review, shown, html);
  }

  // A closed tab is told nothing: its webview is gone.
  state(review) {
    if (this.panels.get(review.file) !== review) return;
    review.ship = this.ship(review);
    review.panel.post({ type: "state", ship: review.ship, busy: review.busy, doing: review.doing });
  }

  // A message from review's page. Only the button's go or ship, and a link's href, are taken from
  // it; the issue is the tab's own.
  async receive(review, message) {
    if (message?.type === "link") {
      const target = linkTarget(path.dirname(review.file), message.href, this.host.exists);
      if (target?.url) await this.host.execute("workbench.action.browser.open", target.url);
      else if (target?.file) await this.host.execute("vscode.open", target.file);
      else this.host.say("warning", `Plan: the report's link ${message.href} opens nothing: only a web link, or a file in .tmp/${review.id}/ that exists, opens.`);
      return;
    }
    if (message?.type !== "go" && message?.type !== "ship") return;
    if (review.busy) return;
    const now = this.host.stamp(review.file);
    if (now === null && review.shown !== null) {
      this.host.say("error", `Plan: #${review.id}'s report is gone; nothing ran.`);
      this.state(review);
      return;
    }
    if ((message.shown || null) !== review.shown || now !== review.shown) {
      // show() says so itself when the change disarms Ship.
      const armed = this.ship(review);
      if (!this.show(review.file)) this.state(review);
      else if (armed === review.ship) {
        this.host.say("warning", `Plan: #${review.id}'s report changed; it's shown again. Press ${review.ship ? "Ship" : "Go"} once you've read it.`);
      }
      return;
    }
    if (message.type === "ship" && !this.ship(review)) {
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

module.exports = { reportFile, reportRound, reportChange, stagedDiff, stagedSection, reportPage, linkTarget, Reports };
