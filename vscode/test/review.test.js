// node --test vscode/test: the Review tab's open-or-refresh, its page and what its button and links
// run, without VS Code.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { reportFile, reportRound, stagedDiff, stagedSection, reportPage, linkTarget, Reports } = require("../review");

test("a report names its issue and checkout; any other file names none", () => {
  assert.deepEqual(reportFile("/work/gh-plan.worktrees/110/.tmp/110/report.html"), { root: "/work/gh-plan.worktrees/110", id: 110 });
  for (const file of ["/work/repo/.tmp/110/report.htm", "/work/repo/.tmp/0/report.html", "/work/repo/.tmp/110/sub/report.html", "/work/repo/.tmp/report.html", "/work/repo/.tmp/110/pr-body.md"]) {
    assert.equal(reportFile(file), null, file);
  }
});

test("the round comes from the report's plan-round meta", () => {
  assert.equal(reportRound('<head><meta name="plan-round" content="3"></head>'), 3);
  assert.equal(reportRound("<meta content='2' name='plan-round'>"), 2);
  assert.equal(reportRound("<p>round 3</p>"), null);
  assert.equal(reportRound('<meta name="plan-round" content="x">'), null);
  assert.equal(reportRound('<meta name=plan-round content=4 />'), 4);
  for (const html of ['<meta name="plan-rounds" content="3">', '<meta name="plan-round" data-content="3">', '<meta data-name="plan-round" content="3">', '<meta name="plan-round" content="3x">']) {
    assert.equal(reportRound(html), null, html);
  }
});

const PAGE = { id: 110, file: "/work/repo/.tmp/110/report.html", round: 2, written: "24 Oct 2026, 14:32", shown: "1:2", ship: false, nonce: "N0nce", cspSource: "vscode-webview:", css: "review.css", js: "review.js" };

// The page's own markup, the report's frame cut out: what runs outside the sandbox.
const outside = (page) => page.replace(/<iframe\b[^>]*>/, "<iframe>");

test("L1: the report runs in a frame sandboxed without scripts, under a policy that runs only the extension's script", () => {
  const report = '<script>alert(1)</script><button id="act" onclick="x()">Go</button><a href="command:plan.runPr">Ship</a>"\'><img src=x onerror=y>';
  const page = reportPage({ ...PAGE, html: report });
  const frames = page.match(/<iframe\b[^>]*>/g);
  assert.equal(frames.length, 1);
  const sandbox = /\bsandbox="([^"]*)"/.exec(frames[0]);
  assert.ok(sandbox, "the frame is sandboxed");
  assert.deepEqual(sandbox[1].split(/\s+/), ["allow-same-origin"]);
  const policy = /http-equiv="Content-Security-Policy" content="([^"]*)"/.exec(page)[1];
  assert.match(policy, /default-src 'none'/);
  assert.match(policy, /script-src 'nonce-N0nce';/);
  // Images only from the report itself, never fetched.
  assert.match(policy, /; img-src data:; /);
  assert.doesNotMatch(policy, /unsafe-eval|script-src[^;]*unsafe-inline/);
  // The report is the frame's srcdoc, escaped: none of it is the page's markup.
  const own = outside(page);
  assert.equal(own.match(/<script\b/g).length, 1);
  assert.match(own, /<script nonce="N0nce" src="review.js"><\/script>/);
  assert.equal(own.match(/<button\b/g).length, 1);
  assert.match(own, /<button id="act" type="button"><\/button>/);
  for (const piece of ["alert(1)", "onclick", "command:", "onerror"]) assert.ok(!own.includes(piece), piece);
  assert.ok(frames[0].includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
  assert.ok(frames[0].includes("&quot;&#39;&gt;"));
});

test("L5: the bar names the issue, the round and when the report was written", () => {
  const page = reportPage({ ...PAGE, html: "" });
  assert.match(page, /<span id="label">Review #110 · round 2 · written 24 Oct 2026, 14:32<\/span>/);
  assert.match(reportPage({ ...PAGE, round: null, html: "" }), /<span id="label">Review #110 · written /);
  assert.match(page, /data-shown="1:2" data-ship="" data-busy=""/);
  assert.match(reportPage({ ...PAGE, ship: true, busy: true, html: "" }), /data-ship="1" data-busy="1"/);
  assert.match(reportPage({ ...PAGE, file: '/w/"x"/.tmp/110/report.html', html: "" }), /data-file="\/w\/&quot;x&quot;\/.tmp\/110\/report.html"/);
});

test("L7: the button is a real button, and the page's colours are VS Code's theme's", () => {
  const css = fs.readFileSync(path.join(__dirname, "..", "media", "review.css"), "utf8");
  assert.match(css, /button:focus-visible \{[^}]*var\(--vscode-focusBorder\)/);
  const colours = css.match(/(?:^|[\s;{])(?:color|background):[^;]*;/g);
  for (const rule of colours) assert.match(rule, /var\(--vscode-/, rule);
});

test("a link opens a URL or a file in the issue's .tmp/<n>/, and nothing else", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "review-"));
  fs.writeFileSync(path.join(dir, "commit.txt"), "x");
  fs.mkdirSync(path.join(dir, "findings"));
  fs.writeFileSync(path.join(dir, "findings", "outcomes.md"), "x");
  const exists = (file) => fs.existsSync(file);
  assert.deepEqual(linkTarget(dir, "https://github.com/o/r/pull/1", exists), { url: "https://github.com/o/r/pull/1" });
  assert.deepEqual(linkTarget(dir, "commit.txt", exists), { file: path.join(dir, "commit.txt") });
  assert.deepEqual(linkTarget(dir, "findings/outcomes.md#L2", exists), { file: path.join(dir, "findings", "outcomes.md") });
  assert.deepEqual(linkTarget(dir, "./commit%2Etxt", exists), { file: path.join(dir, "commit.txt") });
  for (const href of ["command:plan.runPr", "COMMAND:plan.runPr", "file:///etc/passwd", "vscode://x", "javascript:x", "/etc/passwd", "../110/commit.txt", "../../outside", "missing.md", "#top", "", " ", "%E0%A4%A", null, 5]) {
    assert.equal(linkTarget(dir, href, exists), null, String(href));
  }
});

// A host like the extension's, over a real report on disk, that records what it's asked to do.
function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "review-"));
  const dir = path.join(root, ".tmp", "110");
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "report.html");
  const panels = [];
  const ran = [];
  const said = [];
  let armed = null;
  let mtime = 1000;
  const host = {
    open(review, from) {
      const panel = { review, from, html: null, revealed: 0, posts: [], disposers: [] };
      panel.reveal = () => panel.revealed++;
      panel.setHtml = (html) => (panel.html = html);
      panel.post = (message) => panel.posts.push(message);
      panel.onDispose = (callback) => panel.disposers.push(callback);
      panel.onVisible = (callback) => (panel.shownAgain = callback);
      panel.dispose = () => panel.disposers.forEach((callback) => callback());
      panels.push(panel);
      return panel;
    },
    render: (review, html) => `${review.shown}|${review.ship}|${html}${review.staged ? `|${review.staged.message}` : ""}`,
    stamp: (at) => (fs.existsSync(at) ? `${fs.statSync(at).mtimeMs}:${fs.statSync(at).size}` : null),
    read: (at) => fs.readFileSync(at, "utf8"),
    exists: (at) => fs.existsSync(at),
    armed: () => armed,
    disarm: () => (armed = null),
    execute: async (...args) => ran.push(args),
    say: (level, text) => said.push([level, text]),
  };
  // Each write gets its own modified time, as two saves a moment apart do.
  const write = (text) => {
    fs.writeFileSync(file, text);
    mtime += 1000;
    fs.utimesSync(file, mtime / 1000, mtime / 1000);
  };
  return { root, dir, file, panels, ran, said, host, write, arm: (report) => (armed = { report }), armed: () => armed, reports: new Reports(host) };
}

test("L3, L8: a report saved twice leaves one tab, showing the second file", () => {
  const { file, panels, reports, write } = fixture();
  write("<p>first</p>");
  const review = reports.show(file);
  assert.equal(panels.length, 1);
  assert.equal(review.id, 110);
  assert.match(panels[0].html, /<p>first<\/p>$/);
  write("<p>second</p>");
  assert.equal(reports.show(file), review);
  assert.equal(panels.length, 1, "no second tab");
  assert.equal(panels[0].revealed, 1, "the tab comes to the front");
  assert.match(panels[0].html, /<p>second<\/p>$/);
});

test("L8: a closed tab, as after a window reload, opens again on the next save", () => {
  const { file, panels, reports, write } = fixture();
  write("<p>first</p>");
  reports.show(file);
  panels[0].dispose();
  write("<p>second</p>");
  reports.show(file);
  assert.equal(panels.length, 2);
  assert.match(panels[1].html, /<p>second<\/p>$/);
  // The first tab's dispose, late, doesn't drop the second.
  panels[0].dispose();
  write("<p>third</p>");
  reports.show(file);
  assert.equal(panels.length, 2);
  assert.match(panels[1].html, /<p>third<\/p>$/);
});

test("a report that isn't one, or is gone, opens nothing", () => {
  const { dir, file, panels, reports } = fixture();
  assert.equal(reports.show(path.join(dir, "pr-body.md")), null);
  assert.equal(reports.show(file), null);
  assert.equal(panels.length, 0);
});

test("L8: Go runs Next up's Go on the issue's PR body; once armed, the button is Ship and runs Next up's Ship", async () => {
  const { root, file, panels, ran, reports, write, arm } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  await reports.receive(review, { type: "go", shown: review.shown });
  assert.deepEqual(ran, [["plan.openChanges", path.join(root, ".tmp", "110", "pr-body.md")]]);
  // Busy while it runs, then free again.
  assert.deepEqual(panels[0].posts.map((m) => m.busy), [true, false]);
  arm(review.shown);
  reports.armedChanged();
  assert.deepEqual(panels[0].posts.at(-1), { type: "state", ship: true, busy: false, doing: "go" });
  await reports.receive(review, { type: "ship", shown: review.shown });
  assert.deepEqual(ran.at(-1), ["plan.runPr"]);
});

test("Ship before Go runs nothing", async () => {
  const { file, ran, said, reports, write } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  await reports.receive(review, { type: "ship", shown: review.shown });
  assert.deepEqual(ran, []);
  assert.equal(said[0][0], "error");
});

test("L3: a click on a report since rewritten runs nothing and shows the new one", async () => {
  const { file, panels, ran, said, reports, write } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  const shown = review.shown;
  write("<p>round 2</p>");
  // The watcher hasn't shown it yet: the click is on round 1's page.
  await reports.receive(review, { type: "go", shown });
  assert.deepEqual(ran, []);
  assert.match(panels[0].html, /round 2/);
  assert.equal(said[0][0], "warning");
  // A click from round 1's page that arrives after round 2 is shown is refused too.
  await reports.receive(review, { type: "go", shown });
  assert.deepEqual(ran, []);
  await reports.receive(review, { type: "go", shown: review.shown });
  assert.equal(ran.length, 1);
});

test("L3: a new report after Go disarms Ship, so Ship never ships a round not shown", () => {
  const { file, said, reports, write, arm } = fixture();
  write("<p>round 1</p>");
  arm(reports.show(file).shown);
  // Shown again unchanged, as after a duplicate watcher event: Ship stays armed.
  const review = reports.show(file);
  assert.equal(review.ship, true);
  write("<p>round 2</p>");
  reports.show(file);
  assert.equal(review.ship, false);
  assert.match(said.at(-1)[1], /changed since Go/);
});

test("a second click while Go runs is ignored", async () => {
  const { file, ran, reports, write, host } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  let finish;
  host.execute = (...args) => (ran.push(args), new Promise((resolve) => (finish = resolve)));
  const first = reports.receive(review, { type: "go", shown: review.shown });
  await reports.receive(review, { type: "go", shown: review.shown });
  finish();
  await first;
  assert.equal(ran.length, 1);
});

test("L1: a link from the report opens only what linkTarget allows; any other message runs nothing", async () => {
  const { dir, file, ran, reports, write } = fixture();
  write("<p>round 1</p>");
  fs.writeFileSync(path.join(dir, "commit.txt"), "x");
  const review = reports.show(file);
  await reports.receive(review, { type: "link", href: "command:plan.runPr" });
  await reports.receive(review, { type: "link", href: "../../../etc/passwd" });
  await reports.receive(review, { type: "run", command: "plan.runPr" });
  await reports.receive(review, null);
  assert.deepEqual(ran, []);
  await reports.receive(review, { type: "link", href: "commit.txt" });
  await reports.receive(review, { type: "link", href: "https://github.com/o/r/pull/1" });
  assert.deepEqual(ran, [["vscode.open", path.join(dir, "commit.txt")], ["workbench.action.browser.open", "https://github.com/o/r/pull/1"]]);
});

test("L3: a report Go never saw disarms Ship, even in a new tab; the same report reopened doesn't", () => {
  const { file, panels, said, reports, write, arm } = fixture();
  // Go pressed from the title bar, then the report written: its first tab shows Go.
  arm(null);
  write("<p>round 1</p>");
  const review = reports.show(file);
  assert.equal(review.ship, false);
  assert.match(said.at(-1)[1], /changed since Go/);
  arm(review.shown);
  panels[0].dispose();
  const again = reports.show(file);
  assert.equal(again.ship, true, "a closed tab reopened on the report Go saw keeps Ship");
});

test("L3: a stale Ship click says once to press Go", async () => {
  const { file, ran, said, reports, write, arm } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  arm(review.shown);
  const shown = review.shown;
  write("<p>round 2</p>");
  await reports.receive(review, { type: "ship", shown });
  assert.deepEqual(ran, []);
  assert.equal(said.length, 1);
  assert.match(said[0][1], /press Go again/);
  assert.equal(review.ship, false);
});

test("while Go or Ship runs, the tab is told which one", async () => {
  const { file, panels, reports, write, arm } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  arm(review.shown);
  reports.armedChanged();
  await reports.receive(review, { type: "ship", shown: review.shown });
  assert.deepEqual(panels[0].posts.at(-2), { type: "state", ship: true, busy: true, doing: "ship" });
  assert.match(reportPage({ ...PAGE, busy: true, doing: "ship", html: "" }), /data-doing="ship"/);
  assert.match(reportPage({ ...PAGE, html: "" }), /data-doing="go"/);
});

test("a click on a report since deleted runs nothing and says it's gone; a report gone mid-read shows nothing", async () => {
  const { file, ran, said, reports, write, host } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  fs.unlinkSync(file);
  await reports.receive(review, { type: "go", shown: review.shown });
  assert.deepEqual(ran, []);
  assert.deepEqual(said, [["error", "Plan: #110's report is gone; nothing ran."]]);
  write("<p>round 2</p>");
  host.read = () => {
    throw new Error("ENOENT");
  };
  assert.equal(reports.show(file), null);
});

test("the round's other files opening bring an open report back in front", () => {
  const { file, panels, reports, write } = fixture();
  reports.reveal(file);
  write("<p>round 1</p>");
  reports.show(file);
  reports.reveal(file);
  assert.equal(panels[0].revealed, 1);
});

test("after a window reload, the report Go saw opens with Ship still armed", () => {
  const { file, said, host, write, arm } = fixture();
  write("<p>round 1</p>");
  arm(host.stamp(file));
  // A new Reports, as after a reload: it has shown nothing yet.
  const review = new Reports(host).show(file);
  assert.equal(review.ship, true);
  assert.deepEqual(said, []);
});

test("Ship's last check finds a report written after Go, or one written where Go found none", () => {
  const { root, file, reports, host, write } = fixture();
  assert.equal(reports.changedSinceGo({ root, id: 110, report: null }), false);
  write("<p>round 1</p>");
  assert.equal(reports.changedSinceGo({ root, id: 110, report: null }), true);
  const report = host.stamp(file);
  assert.equal(reports.changedSinceGo({ root, id: 110, report }), false);
  write("<p>round 2</p>");
  assert.equal(reports.changedSinceGo({ root, id: 110, report }), true);
});

test("a tab closed while its Go runs is told nothing once Go ends", async () => {
  const { file, panels, reports, write, host } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  host.execute = async () => panels[0].dispose();
  panels[0].post = (message) => {
    if (!reports.panels.has(file)) throw new Error("Webview is disposed");
    panels[0].posts.push(message);
  };
  await reports.receive(review, { type: "go", shown: review.shown });
  assert.deepEqual(panels[0].posts.map((m) => m.busy), [true]);
  reports.armedChanged();
});

test("a tab VS Code restores after a reload becomes the report's tab; a second for the same report doesn't", () => {
  const { file, panels, reports, write } = fixture();
  write("<p>round 1</p>");
  const restored = { restored: true };
  const review = reports.show(file, restored);
  assert.equal(panels[0].from, restored);
  assert.match(panels[0].html, /round 1/);
  assert.equal(reports.show(file, { restored: true }), null);
  assert.equal(panels.length, 1);
  assert.equal(reports.show(file), review);
});

test("a tab shows Ship only for the report Go saw, even when Go armed it after the tab showed a newer one", () => {
  const { file, reports, write, arm, said } = fixture();
  write("<p>round 1</p>");
  const old = reports.show(file).shown;
  write("<p>round 2</p>");
  const review = reports.show(file);
  // Go stamped round 1 before its checks and armed after the tab showed round 2.
  arm(old);
  reports.armedChanged();
  assert.equal(review.ship, false);
  assert.deepEqual(said, []);
});

test("a report shown again unchanged keeps its page, and its place in it", () => {
  const { file, panels, reports, write, arm } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  const page = panels[0].html;
  panels[0].html = "kept";
  arm(review.shown);
  assert.equal(reports.show(file), review);
  assert.equal(panels[0].html, "kept");
  assert.equal(panels[0].posts.at(-1).ship, true);
  assert.ok(page.endsWith("<p>round 1</p>"));
});

test("a tab shown again after being hidden is told the state", () => {
  const { file, panels, reports, write, arm } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  arm(review.shown);
  panels[0].shownAgain();
  assert.deepEqual(panels[0].posts.at(-1), { type: "state", ship: true, busy: false, doing: null });
});

test("a link that opens nothing says so", async () => {
  const { file, ran, said, reports, write } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  await reports.receive(review, { type: "link", href: "../../vscode/review.js" });
  assert.deepEqual(ran, []);
  assert.equal(said.length, 1);
  assert.match(said[0][1], /opens nothing/);
});

test("a disarm by a new report marks the Go it undid, for a Ship whose checks are running", () => {
  const { file, reports, write, arm, armed } = fixture();
  write("<p>round 1</p>");
  arm(reports.show(file).shown);
  const taken = armed();
  write("<p>round 2</p>");
  reports.show(file);
  assert.equal(taken.warned, true);
  assert.equal(armed(), null);
});

const DIFF = `diff --git a/a.js b/a.js
index 1111111..2222222 100644
--- a/a.js
+++ b/a.js
@@ -3,3 +3,3 @@ function f() {
 keep
-old <b>
+new <script>x()</script>
 keep
diff --git a/new.md b/new.md
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new.md
@@ -0,0 +1,2 @@
+one
+two
diff --git a/gone.txt b/gone.txt
deleted file mode 100644
index 4444444..0000000
--- a/gone.txt
+++ /dev/null
@@ -1 +0,0 @@
-bye
diff --git a/old name.js b/new name.js
similarity 100%
rename from old name.js
rename to new name.js
diff --git a/logo.png b/logo.png
index 5555555..6666666 100644
Binary files a/logo.png and b/logo.png differ
diff --git a/run.sh b/run.sh
old mode 100644
new mode 100755
`;

test("the staged diff reads as files: modified, added, deleted, renamed, binary and mode-only", () => {
  const files = stagedDiff(DIFF);
  assert.deepEqual(
    files.map(({ path, from, status, binary, adds, dels }) => [path, from, status, binary, adds, dels]),
    [
      ["a.js", "a.js", "modified", false, 1, 1],
      ["new.md", "new.md", "added", false, 2, 0],
      ["gone.txt", "gone.txt", "deleted", false, 0, 1],
      ["new name.js", "old name.js", "renamed", false, 0, 0],
      ["logo.png", "logo.png", "modified", true, 0, 0],
      ["run.sh", "run.sh", "modified", false, 0, 0],
    ],
  );
  assert.deepEqual(files[0].hunks, [{ old: 3, new: 3, lines: [" keep", "-old <b>", "+new <script>x()</script>", " keep"] }]);
  assert.deepEqual(stagedDiff(""), []);
});

test("L1: the staged diff and commit message show escaped, outside the frame, numbered by line", () => {
  const section = stagedSection({ message: "Fix <it>\n\nAssisted-by: x", files: stagedDiff(DIFF) });
  assert.match(section, /<h2>Staged changes · 6 files \+3 −2<\/h2>/);
  assert.ok(section.includes("Fix &lt;it&gt;"));
  assert.ok(section.includes("+new &lt;script&gt;x()&lt;/script&gt;"));
  assert.doesNotMatch(section, /<script|<b>|<it>/);
  assert.match(section, /<span class="ln">4<\/span><span class="ln"><\/span><span class="del">-old &lt;b&gt;<\/span>/);
  assert.match(section, /<span class="ln"><\/span><span class="ln">4<\/span><span class="add">/);
  assert.ok(section.includes("<code>old name.js → new name.js</code>"));
  assert.match(section, /A binary file/);
  assert.match(section, /Only its mode changed/);
  const page = reportPage({ ...PAGE, html: "<p>r</p>", ship: true, staged: { message: "m", files: stagedDiff(DIFF) } });
  assert.ok(outside(page).includes('<section id="staged"'));
  assert.equal(outside(page).match(/<script\b/g).length, 1);
});

test("with no report yet, the tab says so and has no frame", () => {
  const page = reportPage({ ...PAGE, html: null, round: null, written: null, shown: null, staged: { message: "m", files: [] } });
  assert.match(page, /<span id="label">Review #110 · no report yet<\/span>/);
  assert.doesNotMatch(page, /<iframe/);
  assert.match(page, /<p id="empty">/);
  assert.match(page, /data-shown=""/);
});

test("Go's staged changes open the tab with no report, and Ship ships them", async () => {
  const { root, panels, ran, reports, arm } = fixture();
  arm(null);
  const review = reports.staged({ root, id: 110 }, { message: "m", diff: DIFF });
  assert.equal(panels.length, 1);
  assert.equal(review.shown, null);
  assert.equal(review.ship, true);
  assert.equal(review.staged.files.length, 6);
  assert.equal(panels[0].html, "null|true|null|m");
  await reports.receive(review, { type: "ship", shown: "" });
  assert.equal(ran.length, 1);
});

test("staged changes show only for the Go armed on the report shown, and a new report hides them", () => {
  const { root, file, reports, write, arm } = fixture();
  write("<p>round 1</p>");
  // Not armed: nothing staged shows.
  assert.equal(reports.staged({ root, id: 110 }, { message: "m", diff: DIFF }).staged, null);
  arm(reports.show(file).shown);
  const review = reports.staged({ root, id: 110 }, { message: "m", diff: DIFF });
  assert.equal(review.staged.files.length, 6);
  // The same report again keeps them.
  reports.show(file);
  assert.equal(review.staged.files.length, 6);
  write("<p>round 2</p>");
  reports.show(file);
  assert.equal(review.staged, null);
});

test("a tab with no report isn't opened by show, nor restored", () => {
  const { file, panels, reports } = fixture();
  assert.equal(reports.show(file), null);
  assert.equal(reports.show(file, {}), null);
  assert.equal(panels.length, 0);
});

test("a path with a space keeps no tab git adds after it", () => {
  const files = stagedDiff("diff --git a/my file.txt b/my file.txt\nnew file mode 100644\n--- /dev/null\n+++ b/my file.txt\t\n@@ -0,0 +1 @@\n+x\ndiff --git a/old one.txt b/old one.txt\ndeleted file mode 100644\n--- a/old one.txt\t\n+++ /dev/null\n@@ -1 +0,0 @@\n-y\n");
  assert.deepEqual(files.map((file) => file.path), ["my file.txt", "old one.txt"]);
});

test("a new Go drops the last Go's staged changes before it stages, and redraws the tab without them", () => {
  const { root, file, panels, reports, write, arm } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  arm(review.shown);
  reports.staged({ root, id: 110 }, { message: "m", diff: DIFF });
  assert.match(panels[0].html, /\|m$/);
  // Go arms again before staging: the old diff goes, and the page is drawn again without it.
  reports.armedChanged();
  assert.equal(review.staged, null);
  assert.equal(panels[0].html, `${review.shown}|true|<p>round 1</p>`);
  reports.staged({ root, id: 110 }, { message: "m2", diff: DIFF });
  assert.equal(review.staged.message, "m2");
});

test("a redraw reads the report as it is now: one written since Go shows with its own stamp, and disarms Ship", () => {
  const { root, file, panels, reports, write, arm, armed, said } = fixture();
  write("<p>round 1</p>");
  const review = reports.show(file);
  arm(review.shown);
  reports.staged({ root, id: 110 }, { message: "m", diff: DIFF });
  write("<p>round 2</p>");
  reports.armedChanged();
  assert.equal(armed(), null);
  assert.equal(review.ship, false);
  assert.equal(panels[0].html, `${review.shown}|false|<p>round 2</p>`);
  assert.match(said.at(-1)[1], /report changed since Go/);
});
