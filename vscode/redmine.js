// Redmine's REST API, read-only, for the panel. No vscode import, so it runs under plain node too.
// The API key comes from the macOS Keychain each time it is needed; it is never stored here.
const { execFile } = require("child_process");

function apiKey(service) {
  return new Promise((resolve, reject) => {
    execFile("security", ["find-generic-password", "-s", service, "-w"], (error, stdout) => {
      if (error) reject(new Error(`no Keychain item "${service}"; store your Redmine API key in it`));
      else resolve(stdout.trim());
    });
  });
}

class Redmine {
  // settings: { url, project, query, service }
  constructor(settings) {
    this.settings = { ...settings, url: settings.url.replace(/\/+$/, "") };
  }

  async get(path) {
    const { url, service } = this.settings;
    const response = await fetch(url + path, { headers: { "X-Redmine-API-Key": await apiKey(service) } });
    if (response.status === 401) {
      throw new Error(`Redmine refused the key in Keychain item "${service}" (401); store your API key from My account`);
    }
    if (!response.ok) throw new Error(`Redmine ${path}: HTTP ${response.status}`);
    return response.json();
  }

  // The saved query's issues, in its order, with what a row shows.
  async list() {
    const { project, query } = this.settings;
    // A wrong key reads as anonymous, not as a refusal, wherever anonymous may read: this fails it.
    await this.get("/users/current.json");
    const { queries } = await this.get("/queries.json?limit=100");
    const found = queries.find((q) => q.name === query);
    if (!found) throw new Error(`no saved query "${query}" that you can see`);
    const { issues } = await this.get(
      `/issues.json?query_id=${found.id}&project_id=${encodeURIComponent(project)}&limit=100`
    );
    // An issue names its project's id, not its identifier, which is what a marker is keyed by.
    const { projects } = await this.get("/projects.json?limit=100");
    const identifiers = new Map(projects.map((p) => [p.id, p.identifier]));
    return issues.map((issue) => ({
      id: issue.id,
      subject: issue.subject,
      status: issue.status.name,
      project: identifiers.get(issue.project.id) || issue.project.name,
      parent: issue.parent && issue.parent.id,
      points: field(issue, "Points"),
    }));
  }

  // One issue in full: the first click on its row asks for it.
  async issue(id) {
    const { issue } = await this.get(`/issues/${Number(id)}.json?include=children,relations,journals`);
    return issue;
  }
}

function field(issue, name) {
  const found = (issue.custom_fields || []).find((f) => f.name === name);
  return found && found.value ? found.value : "";
}

module.exports = { Redmine };
