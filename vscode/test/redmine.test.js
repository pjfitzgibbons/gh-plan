// node --test vscode/test: Redmine's REST client against a fake Redmine on localhost, with a fake
// `security` on PATH standing in for the Keychain.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { Redmine } = require("../redmine");

const STATUSES = [{ id: 1, name: "New" }, { id: 2, name: "In Progress" }, { id: 5, name: "Closed" }];

// A Redmine that answers 204 to every PUT and, when drops, keeps the old status as 7.0.2 does.
async function fakeRedmine(t, { drops }) {
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), "plan-redmine-"));
  t.after(() => fs.rmSync(bin, { recursive: true, force: true }));
  fs.writeFileSync(path.join(bin, "security"), "#!/bin/sh\necho test-key\n", { mode: 0o755 });
  const PATH = process.env.PATH;
  process.env.PATH = `${bin}:${PATH}`;
  t.after(() => (process.env.PATH = PATH));

  const issue = { id: 80, status: STATUSES[0], journals: [] };
  const requests = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      requests.push(`${req.method} ${req.url}`);
      if (req.method === "PUT") {
        const { issue: fields } = JSON.parse(body);
        if (fields.status_id && !drops) issue.status = STATUSES.find((s) => s.id === fields.status_id);
        if (fields.notes) issue.journals.push({ notes: fields.notes });
        res.writeHead(204).end();
      } else if (req.url === "/issue_statuses.json") {
        res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ issue_statuses: STATUSES }));
      } else {
        res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ issue }));
      }
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => (server.closeAllConnections(), server.close()));
  const redmine = new Redmine({ url: `http://127.0.0.1:${server.address().port}/`, service: "test" });
  return { redmine, issue, requests };
}

test("a status write Redmine drops throws, naming the issue and both statuses, and says the note was saved", async (t) => {
  const r = await fakeRedmine(t, { drops: true });
  const status_id = await r.redmine.statusId("In Progress");
  await assert.rejects(r.redmine.update(80, { status_id, notes: "Started." }), (e) => {
    assert.equal(
      e.message,
      "Redmine kept #80 New, not In Progress. It drops a status when the key's roles have no workflow transition to it, " +
        "when an open issue blocks a close or an open subtask does, and on a subtask of a closed parent, which only closes; " +
        "the note was saved. Fix that, then try again"
    );
    return true;
  });
  assert.deepEqual(r.issue.journals, [{ notes: "Started." }]);
  // The read-back writes nothing and never sends the status again.
  assert.deepEqual(r.requests, ["GET /issue_statuses.json", "PUT /issues/80.json", "GET /issues/80.json"]);
});

test("a status write Redmine applies passes, with one GET after the PUT", async (t) => {
  const r = await fakeRedmine(t, { drops: false });
  await r.redmine.update(80, { status_id: 2 });
  assert.equal(r.issue.status.name, "In Progress");
  assert.deepEqual(r.requests, ["PUT /issues/80.json", "GET /issues/80.json"]);
});

test("a dropped status the client never looked up is named by its id, with no note to mention", async (t) => {
  const r = await fakeRedmine(t, { drops: true });
  await assert.rejects(r.redmine.update(80, { status_id: 5 }), /^Error: Redmine kept #80 New, not status 5\. .*only closes\. Fix that/);
});

test("a write that sets no status reads nothing back", async (t) => {
  const r = await fakeRedmine(t, { drops: true });
  await r.redmine.update(80, { notes: "Opened a PR." });
  assert.deepEqual(r.requests, ["PUT /issues/80.json"]);
});

test("a read Redmine never answers gives up after readTimeout, saying so", async (t) => {
  const r = await fakeRedmine(t, { drops: false });
  const server = http.createServer(() => {});
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => (server.closeAllConnections(), server.close()));
  const url = `http://127.0.0.1:${server.address().port}`;
  const stalled = new Redmine({ ...r.redmine.settings, url, readTimeout: 200 });
  await assert.rejects(stalled.issue(80), { message: `Redmine at ${url} didn't answer within 0.2s` });
});

test("a read whose body stalls gives up after readTimeout, saying so", async (t) => {
  const r = await fakeRedmine(t, { drops: false });
  const server = http.createServer((req, res) => res.writeHead(200, { "Content-Type": "application/json" }).write("{"));
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => (server.closeAllConnections(), server.close()));
  const url = `http://127.0.0.1:${server.address().port}`;
  const stalled = new Redmine({ ...r.redmine.settings, url, readTimeout: 200 });
  await assert.rejects(stalled.issue(80), { message: `Redmine at ${url} didn't answer within 0.2s` });
});

test("list reads the Keychain once, reads projects only for one it hasn't seen, and tries a failed Keychain read again", async (t) => {
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), "plan-redmine-"));
  t.after(() => fs.rmSync(bin, { recursive: true, force: true }));
  const reads = path.join(bin, "reads");
  // Fails the first time, as a locked Keychain whose prompt is cancelled does.
  fs.writeFileSync(
    path.join(bin, "security"),
    `#!/bin/sh\necho x >> "${reads}"\n[ "$(wc -l < "${reads}")" -gt 1 ] || exit 1\necho test-key\n`,
    { mode: 0o755 }
  );
  const PATH = process.env.PATH;
  process.env.PATH = `${bin}:${PATH}`;
  t.after(() => (process.env.PATH = PATH));
  const answers = {
    "/users/current.json": { user: { id: 1 } },
    "/queries.json?limit=100": { queries: [{ id: 8, name: "Next up" }] },
    "/projects.json?limit=100": { projects: [{ id: 3, identifier: "plan" }] },
    "/issues.json?query_id=8&project_id=plan&limit=100": {
      issues: [{ id: 88, subject: "S", status: { name: "New" }, project: { id: 3, name: "Plan" } }],
    },
  };
  const keys = [];
  const urls = [];
  const server = http.createServer((req, res) => {
    keys.push(req.headers["x-redmine-api-key"]);
    urls.push(req.url);
    res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(answers[req.url]));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => (server.closeAllConnections(), server.close()));
  const redmine = new Redmine({ url: `http://127.0.0.1:${server.address().port}`, service: "test", project: "plan", query: "Next up" });

  const known = {};
  await assert.rejects(redmine.list(known), /no Keychain item "test"/);
  assert.deepEqual((await redmine.list(known)).map((i) => [i.id, i.project]), [[88, "plan"]]);
  assert.equal(fs.readFileSync(reads, "utf8").split("\n").filter(Boolean).length, 2);
  assert.deepEqual(keys, ["test-key", "test-key", "test-key", "test-key"]);
  // A second list knows project 3, so it doesn't read /projects.json again.
  urls.length = 0;
  assert.deepEqual((await redmine.list(known)).map((i) => [i.id, i.project]), [[88, "plan"]]);
  assert.ok(!urls.includes("/projects.json?limit=100"), urls.join(", "));
  assert.equal(urls.length, 3);
  // An issue in a project it hasn't seen reads them again.
  answers["/projects.json?limit=100"].projects.push({ id: 4, identifier: "tt" });
  answers["/issues.json?query_id=8&project_id=plan&limit=100"].issues.push(
    { id: 90, subject: "T", status: { name: "New" }, project: { id: 4, name: "Tempotag" } }
  );
  assert.deepEqual((await redmine.list(known)).map((i) => i.project), ["plan", "tt"]);
});

test("a 401 drops the key it read, so the next request reads the Keychain again", async (t) => {
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), "plan-redmine-"));
  t.after(() => fs.rmSync(bin, { recursive: true, force: true }));
  const key = path.join(bin, "key");
  fs.writeFileSync(key, "wrong-key\n");
  fs.writeFileSync(path.join(bin, "security"), `#!/bin/sh\ncat "${key}"\n`, { mode: 0o755 });
  const PATH = process.env.PATH;
  process.env.PATH = `${bin}:${PATH}`;
  t.after(() => (process.env.PATH = PATH));
  const server = http.createServer((req, res) => {
    if (req.headers["x-redmine-api-key"] !== "right-key") return res.writeHead(401).end();
    res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ issue: { id: 88 } }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => (server.closeAllConnections(), server.close()));
  const redmine = new Redmine({ url: `http://127.0.0.1:${server.address().port}`, service: "test" });

  await assert.rejects(redmine.issue(88), /refused the key in Keychain item "test" \(401\)/);
  fs.writeFileSync(key, "right-key\n");
  assert.deepEqual(await redmine.issue(88), { id: 88 });
});

test("a list with a refused key says so, even when the query read fails first", async (t) => {
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), "plan-redmine-"));
  t.after(() => fs.rmSync(bin, { recursive: true, force: true }));
  fs.writeFileSync(path.join(bin, "security"), "#!/bin/sh\necho wrong-key\n", { mode: 0o755 });
  const PATH = process.env.PATH;
  process.env.PATH = `${bin}:${PATH}`;
  t.after(() => (process.env.PATH = PATH));
  const server = http.createServer((req, res) => {
    if (req.url === "/queries.json?limit=100") return res.writeHead(403).end();
    setTimeout(() => res.writeHead(401).end(), 100);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => (server.closeAllConnections(), server.close()));
  const redmine = new Redmine({ url: `http://127.0.0.1:${server.address().port}`, service: "test", project: "plan", query: "Next up" });

  await assert.rejects(redmine.list(), /refused the key in Keychain item "test" \(401\)/);
});
