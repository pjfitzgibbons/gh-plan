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
