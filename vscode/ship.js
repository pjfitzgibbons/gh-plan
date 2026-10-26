// Shipping an issue: what Go checks before Ship, and the PR it puts on the issue once pr succeeds.
// No vscode import: plain functions, tested under node --test.
const fs = require("fs");
const path = require("path");
const { run, git } = require("./start");

// A round's files for pr, in .tmp/<n>/: beside the commit message and PR body, the review replies
// pr posts, one <comment id>.md each. A reply's name is digits, as pr_threads.py reads it.
// package.json's Go and Ship match the same names.
const ROUND = String.raw`(?:commit\.txt|pr-body\.md|replies\/[0-9]+\.md)`;
const REVIEW_FILE = new RegExp(String.raw`^(.+)\/\.tmp\/([1-9][0-9]*)\/${ROUND}$`);
const ROUND_FILE = new RegExp(`^${ROUND}$`);

// The issue and its checkout from a round file's path, <root>/.tmp/<n>/<round file>; null for any
// other file.
function reviewFile(file) {
  const match = REVIEW_FILE.exec(file);
  return match ? { root: match[1], id: Number(match[2]) } : null;
}

function isRoundFile(file, dir) {
  return ROUND_FILE.test(path.relative(dir, file));
}

const isFile = (file) => Boolean(fs.statSync(file, { throwIfNoEntry: false })?.isFile());

// What pr has yet to send from .tmp/<n>/ (dir): every file in replies/ but dotfiles such as
// .DS_Store, since pr deletes each reply it sends and refuses or skips a misnamed one, and the
// edits keepEdits saved beside one. A folder in replies/ is never sent, so it's left alone. Replies
// are in comment id order.
function unsent(dir) {
  const replies = path.join(dir, "replies");
  const names = fs.statSync(replies, { throwIfNoEntry: false })?.isDirectory() ? fs.readdirSync(replies) : [];
  const files = names.filter((name) => !name.startsWith("."));
  files.sort((a, b) => (parseInt(a, 10) || Infinity) - (parseInt(b, 10) || Infinity) || a.localeCompare(b));
  return files.map((name) => path.join(replies, name)).filter(isFile);
}

// Saves text, edits made to a round file after pr sent and deleted it, beside it as
// <file>.unsent, or .unsent2 and on if that's taken; never over a file. Returns the path. unsent()
// lists a reply's, so Done keeps the worktree until it's dealt with.
function keepEdits(file, text) {
  for (let n = 1; ; n++) {
    const kept = `${file}.unsent${n > 1 ? n : ""}`;
    try {
      fs.writeFileSync(kept, text, { flag: "wx" });
      return kept;
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }
  }
}

// What pr has yet to send from every issue's .tmp/<n>/ in root, as paths relative to root.
function unsentIn(root) {
  const tmp = path.join(root, ".tmp");
  const ids = fs.statSync(tmp, { throwIfNoEntry: false })?.isDirectory()
    ? fs.readdirSync(tmp).filter((name) => /^[1-9][0-9]*$/.test(name))
    : [];
  return ids.flatMap((id) => unsent(path.join(tmp, id))).map((file) => path.relative(root, file));
}

// The round's files that exist, in the order they open, the PR body last so it's in front; none
// until the PR body and something else for pr are both written.
function roundFiles(dir) {
  const at = (name) => path.join(dir, name);
  if (!isFile(at("pr-body.md"))) return [];
  const files = [at("commit.txt"), ...unsent(dir)].filter((file) => isRoundFile(file, dir) && isFile(file));
  return files.length ? [...files, at("pr-body.md")] : [];
}

// Whether a tab's input is one Go left open for issue id in root: a round file, a diff of a file
// in root, or the Changes view of root. Once pr commits, the Changes view may list nothing, so an
// empty one counts too.
function shipTab(input, root, id) {
  const inRoot = (uri) => Boolean(uri?.fsPath) && uri.fsPath.startsWith(`${root}/`);
  if (!input) return false;
  if (input.uri) return isRoundFile(input.uri.fsPath, path.join(root, ".tmp", String(id)));
  if (input.modified) return inRoot(input.modified);
  if (Array.isArray(input.textDiffs)) return input.textDiffs.length === 0 || input.textDiffs.some((d) => inRoot(d.modified));
  return false;
}

// Stages every change, as pr's commit does, so what Go shows staged is what Ship commits.
async function stage(root) {
  await git(root, "add", "-A");
}

// What Go staged, as git diff prints it, for the Review tab; renames as renames, and paths as they
// are, unquoted. The prefixes and blank context lines are set here, so a user's diff config can't
// change what stagedDiff reads. Output over execFile's 1 MB buffer refuses.
function stagedChanges(root) {
  return git(root, "-c", "core.quotepath=false", "-c", "diff.suppressBlankEmpty=false", "diff", "--cached", "--no-color", "--no-ext-diff", "-M", "--src-prefix=a/", "--dst-prefix=b/");
}

// Opens what Go staged, and returns why not, or null once it's open. VS Code's git extension reads
// the index on its own schedule, so straight after stage() its staged group can still be empty, and
// git.viewStagedChanges says so and opens nothing: the repository's status() reads it first.
// repository is the git extension's for the checkout, or null without one.
async function openStaged(repository, execute) {
  if (!repository) return "VS Code's git extension has no repository for it.";
  try {
    await repository.status();
  } catch (error) {
    return `VS Code's git extension couldn't read it: ${sentence(error)}`;
  }
  await execute("git.viewStagedChanges");
  return null;
}

// The files changed since Go staged the tree: an edit or deletion the index lacks, or a new file
// git doesn't ignore.
async function unstaged(root) {
  const names = async (...args) => (await git(root, ...args)).split("\0").filter(Boolean);
  const files = [...await names("diff", "--name-only", "-z"), ...await names("ls-files", "--others", "--exclude-standard", "-z")];
  return [...new Set(files)].sort();
}

// What pr will find in root for issue id: its branch, whether the tree has changes, and each reason
// it can't run yet. .tmp/ is ignored, so the review files never count as changes. For Ship, staged
// also refuses a file changed since Go staged the tree, which pr would commit unreviewed.
async function check(root, id, { staged = false } = {}) {
  const branch = (await git(root, "branch", "--show-current")).trim();
  const dirty = (await git(root, "status", "--porcelain")).trim() !== "";
  const problems = [];
  if (staged) {
    const late = await unstaged(root);
    if (late.length) problems.push(`${late.join(", ")} changed since Go; press Go to stage and review ${late.length > 1 ? "them" : "it"}`);
  }
  if (!new RegExp(`^(?:[\\w-]+/)?${id}-`).test(branch)) problems.push(`the branch ${branch || "(none)"} isn't #${id}'s`);
  if (!fs.existsSync(path.join(root, ".tmp", String(id), "pr-body.md"))) problems.push(`.tmp/${id}/pr-body.md is missing`);
  if (dirty && !fs.existsSync(path.join(root, ".tmp", String(id), "commit.txt"))) {
    problems.push(`the tree has changes and .tmp/${id}/commit.txt is missing`);
  }
  // pr_threads.py would refuse these only after pr has pushed, or post two replies to one thread.
  const dir = path.join(root, ".tmp", String(id));
  const threads = new Map();
  for (const file of unsent(dir)) {
    const rel = path.relative(dir, file);
    if (!isRoundFile(file, dir)) {
      problems.push(`.tmp/${id}/${rel} isn't a reply pr can send; name it replies/<comment id>.md`);
      continue;
    }
    if (!fs.readFileSync(file, "utf8").trim()) problems.push(`.tmp/${id}/${rel} is empty`);
    const thread = parseInt(path.basename(rel), 10);
    if (threads.has(thread)) problems.push(`.tmp/${id}/${threads.get(thread)} and ${rel} both reply to comment ${thread}`);
    else threads.set(thread, rel);
  }
  return { branch, dirty, problems };
}

// An error's message as a sentence, so the next one doesn't run into it.
const sentence = (error) => (/[.!?]$/.test(error.message) ? error.message : `${error.message}.`);

// How long each gh call about a PR may take, so a stalled one ends with a message.
const GH_TIMEOUT = 60000;

// The PR for ref, a branch or a PR's URL, as gh reports it: fields, by default { url, number, title }.
async function pullRequest(root, ref, fields = "url,number,title") {
  return JSON.parse(await run("gh", ["pr", "view", ref, "--json", fields], root, GH_TIMEOUT));
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

// What Ship does once pr succeeds in root: links the branch's PR on issue id, through the client
// redmine() returns. Never throws: returns the message for VS Code, and whether it reports a failure.
async function shipped({ root, id, branch, redmine }) {
  try {
    const pr = await pullRequest(root, branch);
    const linked = await linkPullRequest({ redmine: redmine(), id, pr });
    return { failed: false, message: `#${id} ${linked ? "now links" : "already links"} ${pr.url}` };
  } catch (error) {
    return { failed: true, message: `#${id} shipped, but Redmine wasn't told. ${sentence(error)} Go and Ship again to retry.` };
  }
}

module.exports = {
  reviewFile, isRoundFile, unsent, unsentIn, keepEdits, roundFiles, shipTab, stage, stagedChanges, openStaged, check, pullRequest, prLink, linkPullRequest,
  shipped,
};
