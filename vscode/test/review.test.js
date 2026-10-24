// node --test vscode/test: the Review tab's open-or-refresh, its page and what its button and links
// run, without VS Code.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { reportFile, reportRound, reportPage, linkTarget, Reports } = require("../review");

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
});

const PAGE = { id: 110, round: 2, written: "24 Oct 2026, 14:32", shown: "1:2", ship: false, nonce: "N0nce", cspSource: "vscode-webview:", css: "review.css", js: "review.js" };

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
  let armed = false;
  let mtime = 1000;
  const host = {
    open(review) {
      const panel = { review, html: null, revealed: 0, posts: [], disposers: [] };
      panel.reveal = () => panel.revealed++;
      panel.setHtml = (html) => (panel.html = html);
      panel.post = (message) => panel.posts.push(message);
      panel.onDispose = (callback) => panel.disposers.push(callback);
      panel.dispose = () => panel.disposers.forEach((callback) => callback());
      panels.push(panel);
      return panel;
    },
    render: (review, html) => `${review.shown}|${review.ship}|${html}`,
    stamp: (at) => (fs.existsSync(at) ? `${fs.statSync(at).mtimeMs}:${fs.statSync(at).size}` : null),
    read: (at) => fs.readFileSync(at, "utf8"),
    exists: (at) => fs.existsSync(at),
    armed: () => armed,
    disarm: () => (armed = false),
    execute: async (...args) => ran.push(args),
    say: (level, text) => said.push([level, text]),
  };
  // Each write gets its own modified time, as two saves a moment apart do.
  const write = (text) => {
    fs.writeFileSync(file, text);
    mtime += 1000;
    fs.utimesSync(file, mtime / 1000, mtime / 1000);
  };
  return { root, dir, file, panels, ran, said, host, write, arm: (on) => (armed = on), reports: new Reports(host) };
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
  arm(true);
  reports.armedChanged();
  assert.deepEqual(panels[0].posts.at(-1), { type: "state", ship: true, busy: false });
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
  reports.show(file);
  arm(true);
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
