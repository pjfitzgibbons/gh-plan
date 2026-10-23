// A new window can't be handed a task directly, so Start leaves one in the extension's global
// storage, which every window shares; the worktree's window picks it up when it opens, or at once if
// it was open already, opens the issue's page and starts Claude.
// One file per worktree, so a second Start before the first window opens doesn't replace the first's.
// A handoff is kept until Claude starts, so a reload can retry it; after a day, opening that
// worktree no longer starts Claude unasked.
// The vscode API is passed in, so node --test can stub it.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const HANDOFF_FRESH_MS = 24 * 60 * 60 * 1000;

function handoffFile(storage, dir) {
  return path.join(storage, `handoff-${crypto.createHash("sha256").update(dir).digest("hex").slice(0, 16)}.json`);
}

// The issue's page on the Redmine that plan.redmineUrl names.
function issueUrl(redmineUrl, id) {
  return `${redmineUrl.replace(/\/+$/, "")}/issues/${id}`;
}

// handoff: { dir, prompt, url }.
function writeHandoff(storage, handoff) {
  const file = handoffFile(storage, handoff.dir);
  fs.mkdirSync(storage, { recursive: true });
  // Written aside and renamed in, so a watching window never reads it half written.
  fs.writeFileSync(`${file}.tmp`, JSON.stringify({ ...handoff, at: Date.now() }));
  fs.renameSync(`${file}.tmp`, file);
}

// In the worktree's window: if the handoff is fresh and for this folder, open the issue's page in
// the leftmost group, then start Claude beside it. A page that fails to open is reported and Claude
// starts anyway. A handoff Claude didn't take stays, so the issue's Open button can try again; a
// handoff from before the page, with no url, starts Claude alone.
async function takeHandoffFor(vscode, storage, folder) {
  const file = handoffFile(storage, folder);
  let handoff;
  try {
    handoff = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return;
  }
  if (folder !== handoff.dir) return;
  if (Date.now() - handoff.at > HANDOFF_FRESH_MS) {
    fs.rmSync(file, { force: true });
    return;
  }
  if (handoff.url) await openPage(vscode, handoff.url);
  try {
    // Undocumented: Claude Code's own command, (prompt, args, location), read from its source (2.1.301).
    await vscode.commands.executeCommand("claude-vscode.terminal.open", handoff.prompt, [], "beside");
  } catch (error) {
    vscode.window.showErrorMessage(`Plan: couldn't start Claude Code: ${error.message}. To retry, reload this window, or press Open on the issue in Next up.`);
    return;
  }
  fs.rmSync(file, { force: true });
}

async function openPage(vscode, url) {
  // Only a web page, as Next up's open message checks.
  if (!/^https?:\/\//.test(url)) {
    vscode.window.showErrorMessage(`Plan: couldn't open the issue's page ${url}: not an http(s) URL. Check plan.redmineUrl.`);
    return;
  }
  try {
    // Read from VS Code's source (1.142): browser.open takes a URL or { url, openToSide,
    // reuseUrlFilter }, and no view column. Under workbench.browser.newTabPlacement's default,
    // activeGroup, it opens in the active group, so the first group is focused before it; the
    // setting's sideGroup and window send it to a group of their own. No reuseUrlFilter: a tab it
    // finds is navigated again, losing what was typed in it, so an Open's retry adds a second tab.
    await vscode.commands.executeCommand("workbench.action.focusFirstEditorGroup");
    await vscode.commands.executeCommand("workbench.action.browser.open", url);
  } catch (error) {
    vscode.window.showErrorMessage(`Plan: couldn't open the issue's page ${url}: ${error.message}`);
  }
}

module.exports = { HANDOFF_FRESH_MS, handoffFile, issueUrl, writeHandoff, takeHandoffFor };
