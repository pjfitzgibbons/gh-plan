// Redmine's REST API, for the panel and for Start. No vscode import, so it runs under plain node too.
// The API key comes from the macOS Keychain once per client, and the repository web service's key
// each time it's needed; neither is written anywhere.
const { execFile } = require("child_process");

function apiKey(service, what = "your Redmine API key") {
  return new Promise((resolve, reject) => {
    execFile("security", ["find-generic-password", "-s", service, "-w"], (error, stdout) => {
      if (error) reject(new Error(`no Keychain item "${service}"; store ${what} in it`));
      else resolve(stdout.trim());
    });
  });
}

class Redmine {
  // settings: { url, project, query, service, sysService, readTimeout }
  constructor(settings) {
    this.settings = { readTimeout: 30000, ...settings, url: settings.url.replace(/\/+$/, "") };
  }

  // A read gives up after readTimeout ms (30s by default), answer and body both, so a stalled
  // Redmine ends each refresh with a message instead of leaving it waiting. A write, and the read
  // that checks one, has no limit: a write that timed out might still apply, and the message would
  // say it hadn't.
  async get(path, limited = true) {
    const response = await this.send("GET", path, undefined, limited);
    return response.json().catch((error) => {
      throw error.name === "TimeoutError" ? this.late() : error;
    });
  }

  // One Keychain read for every request this client sends, so a refresh's reads spawn one
  // security, not one each. A read that fails is tried again on the next request.
  key() {
    this.keyRead ??= apiKey(this.settings.service).catch((error) => {
      this.keyRead = undefined;
      throw error;
    });
    return this.keyRead;
  }

  late() {
    const { url, readTimeout } = this.settings;
    return new Error(`Redmine at ${url} didn't answer within ${readTimeout / 1000}s`);
  }

  async send(method, path, body, limited = false) {
    const { url, service, readTimeout } = this.settings;
    const headers = { "X-Redmine-API-Key": await this.key() };
    if (body) headers["Content-Type"] = "application/json";
    const signal = limited ? AbortSignal.timeout(readTimeout) : undefined;
    let response;
    try {
      response = await fetch(url + path, { method, headers, body: body && JSON.stringify(body), signal });
    } catch (error) {
      if (error.name === "TimeoutError") throw this.late();
      throw new Error(`Redmine can't be reached at ${url}: ${error.cause?.code || error.message}`);
    }
    if (response.status === 401) {
      // So the next request reads the key again, once it's fixed.
      this.keyRead = undefined;
      throw new Error(`Redmine refused the key in Keychain item "${service}" (401); store your API key from My account`);
    }
    if (!response.ok) {
      // A refused write says why in errors[]; a read only has its status.
      const reasons = await response.json().then((r) => r.errors || [], () => []);
      throw new Error(`Redmine ${method} ${path}: HTTP ${response.status}${reasons.length ? `: ${reasons.join("; ")}` : ""}`);
    }
    return response;
  }

  // The saved query's issues, in its order, with what a row shows. known is the caller's
  // { url, identifiers } from earlier lists, kept so a refresh reads /projects.json only when an
  // issue names a project it hasn't seen: a project's identifier never changes once it's made.
  async list(known = {}) {
    const { url, project, query } = this.settings;
    if (known.url !== url) Object.assign(known, { url, identifiers: new Map() });
    // A wrong key reads as anonymous, not as a refusal, wherever anonymous may read: the first read
    // fails it. The query is found by name each time, so a renamed or replaced one is never read.
    const [, { queries }] = await Promise.all([this.get("/users/current.json"), this.get("/queries.json?limit=100")]);
    const found = queries.find((q) => q.name === query);
    if (!found) throw new Error(`no saved query "${query}" that you can see`);
    const { issues } = await this.get(
      `/issues.json?query_id=${found.id}&project_id=${encodeURIComponent(project)}&limit=100`
    );
    // An issue names its project's id, not its identifier, which is what a marker is keyed by.
    const identifiers = known.identifiers;
    if (issues.some((issue) => !identifiers.has(issue.project.id))) {
      const { projects } = await this.get("/projects.json?limit=100");
      for (const p of projects) identifiers.set(p.id, p.identifier);
    }
    return issues.map((issue) => ({
      id: issue.id,
      subject: issue.subject,
      status: issue.status.name,
      project: identifiers.get(issue.project.id) || issue.project.name,
      parent: issue.parent && issue.parent.id,
      points: field(issue, "Points"),
      pullRequests: field(issue, "Pull requests"),
    }));
  }

  // One issue in full: the first click on its row asks for it.
  async issue(id) {
    const { issue } = await this.get(`/issues/${Number(id)}.json?include=children,relations,journals`);
    return issue;
  }

  // An issue names its project by id; the checkout map is keyed by identifier.
  async projectIdentifier(id) {
    const { project } = await this.get(`/projects/${Number(id)}.json`);
    return project.identifier;
  }

  async statusId(name) {
    const { issue_statuses } = await this.get("/issue_statuses.json");
    const found = issue_statuses.find((s) => s.name === name);
    if (!found) throw new Error(`no issue status "${name}" in Redmine`);
    // update names the status it sent by these, if Redmine drops it.
    this.statusNames = new Map(issue_statuses.map((s) => [s.id, s.name]));
    return found.id;
  }

  // Has Redmine read the project's new commits now, as opening its Repository tab does, so a
  // fixes #<n> commit closes its issue. The web service takes its own key, sent in the body so
  // Redmine's log of the request line never holds it. /sys never reloads Redmine's settings, so a
  // key setup.rb just stored is refused until an API call or page view reloads them; Done reads the
  // issue first.
  async fetchCommits(project) {
    const { url, sysService } = this.settings;
    const key = await apiKey(sysService, "the key of Redmine's repository web service");
    let response;
    try {
      response = await fetch(`${url}/sys/fetch_changesets`, { method: "POST", body: new URLSearchParams({ key, id: project }) });
    } catch (error) {
      throw new Error(`Redmine can't be reached at ${url}: ${error.cause?.code || error.message}`);
    }
    if (response.status === 403) {
      throw new Error(`Redmine refused the key in Keychain item "${sysService}" (403); run setup.rb with SYS_API_KEY`);
    }
    if (!response.ok) throw new Error(`Redmine POST /sys/fetch_changesets for ${project}: HTTP ${response.status}`);
  }

  // fields as Redmine's issue PUT takes them; notes becomes a journal entry. Redmine answers 204 and
  // drops a status it won't allow, keeping the rest (docs/failure-modes.md), so a write that sets a
  // status reads the issue back, and refuses if the status isn't the one it sent.
  async update(id, fields) {
    const path = `/issues/${Number(id)}.json`;
    await this.send("PUT", path, { issue: fields });
    if (fields.status_id === undefined) return;
    const { issue } = await this.get(path, false);
    if (issue.status.id === Number(fields.status_id)) return;
    const sent = this.statusNames?.get(Number(fields.status_id)) || `status ${fields.status_id}`;
    throw new Error(
      `Redmine kept #${id} ${issue.status.name}, not ${sent}. It drops a status when the key's roles have no workflow ` +
        `transition to it, when an open issue blocks a close or an open subtask does, and on a subtask of a closed ` +
        `parent, which only closes${fields.notes ? "; the note was saved" : ""}. Fix that, then try again`
    );
  }
}

function field(issue, name) {
  const found = (issue.custom_fields || []).find((f) => f.name === name);
  return found && found.value ? found.value : "";
}

module.exports = { Redmine, field };
