// Shipping an issue: what Ship checks before Go, and the PR it puts on the issue once pr succeeds.
// No vscode import: plain functions, tested under node --test.
const fs = require("fs");
const path = require("path");
const { run, git } = require("./start");

// The issue and its checkout from a review file's path, <root>/.tmp/<n>/{commit.txt,pr-body.md};
// null for any other file.
function reviewFile(file) {
  const match = /^(.+)\/\.tmp\/([1-9][0-9]*)\/(?:commit\.txt|pr-body\.md)$/.exec(file);
  return match ? { root: match[1], id: Number(match[2]) } : null;
}

// Whether a tab's input is one Ship left open for issue id in root: a review file, a diff of a file
// in root, or the Changes view of root. Once pr commits, the Changes view may list nothing, so an
// empty one counts too.
function shipTab(input, root, id) {
  const inRoot = (uri) => Boolean(uri?.fsPath) && uri.fsPath.startsWith(`${root}/`);
  if (!input) return false;
  if (input.uri) return ["commit.txt", "pr-body.md"].some((name) => input.uri.fsPath === path.join(root, ".tmp", String(id), name));
  if (input.modified) return inRoot(input.modified);
  if (Array.isArray(input.textDiffs)) return input.textDiffs.length === 0 || input.textDiffs.some((d) => inRoot(d.modified));
  return false;
}

// What pr will find in root for issue id: its branch, whether the tree has changes, and each reason
// it can't run yet. .tmp/ is ignored, so the review files never count as changes.
async function check(root, id) {
  const branch = (await git(root, "branch", "--show-current")).trim();
  const dirty = (await git(root, "status", "--porcelain")).trim() !== "";
  const problems = [];
  if (!new RegExp(`^(?:[\\w-]+/)?${id}-`).test(branch)) problems.push(`the branch ${branch || "(none)"} isn't #${id}'s`);
  if (!fs.existsSync(path.join(root, ".tmp", String(id), "pr-body.md"))) problems.push(`.tmp/${id}/pr-body.md is missing`);
  if (dirty && !fs.existsSync(path.join(root, ".tmp", String(id), "commit.txt"))) {
    problems.push(`the tree has changes and .tmp/${id}/commit.txt is missing`);
  }
  return { branch, dirty, problems };
}

// The branch's PR, as gh reports it: { url, number, title }.
async function pullRequest(root, branch) {
  return JSON.parse(await run("gh", ["pr", "view", branch, "--json", "url,number,title"], root));
}

// A PR as backfill.rb links it: [<repo>#<n>](<url>).
function prLink(pr) {
  return `[${new URL(pr.url).pathname.split("/")[2]}#${pr.number}](${pr.url})`;
}

// Adds the PR's link to the issue's Pull requests field, with a journal note. Nothing changes when
// the field links it already. Returns whether it changed the issue.
async function linkPullRequest({ redmine, id, pr }) {
  const issue = await redmine.issue(id);
  const field = (issue.custom_fields || []).find((f) => f.name === "Pull requests");
  if (!field) throw new Error(`#${id} has no Pull requests field; run setup.rb`);
  const value = field.value || "";
  if (value.split(/[\s,()[\]]+/).includes(pr.url)) return false;
  const link = prLink(pr);
  await redmine.update(id, {
    custom_fields: [{ id: field.id, value: value ? `${value}, ${link}` : link }],
    notes: `Opened ${link}: ${pr.title}`,
  });
  return true;
}

module.exports = { reviewFile, shipTab, check, pullRequest, prLink, linkPullRequest };
