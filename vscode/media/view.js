// The panel's page. Each issue is a <details>: its row is the <summary>, so a click anywhere on the
// row opens it, and its <section> is filled from the host on the first open only.
(() => {
  const vscode = acquireVsCodeApi();
  const list = document.getElementById("list");
  const status = document.getElementById("status");
  const INVERSE = { blocks: "blocked by", precedes: "follows", duplicates: "duplicated by", copied_to: "copied from" };
  let base = "";

  const escape = (text) =>
    String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const issueLink = (id, text = `#${id}`) => `<a href="${escape(base)}/issues/${Number(id)}">${escape(text)}</a>`;

  // Redmine's Markdown, lightly: links, issue numbers, code spans and headings. Line breaks are kept
  // by the stylesheet. Everything is escaped first, so nothing in the text becomes markup.
  function markdown(text) {
    return escape(text)
      .replace(/^#{1,6} +(.+)$/gm, "<strong>$1</strong>")
      .replace(/^(\s*)[-*] \[([ xX])\]/gm, (_, indent, mark) => `${indent}${mark === " " ? "☐" : "☑"}`)
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(
        /\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)|(https?:\/\/[^\s<]+)|(^|[^\w&/])#(\d+)\b/gm,
        (_, text, url, bare, before, id) =>
          url ? `<a href="${url}">${text}</a>` : bare ? `<a href="${bare}">${bare}</a>` : before + issueLink(id)
      );
  }

  // A link or a button never toggles its row: a link is handed to the host, which opens a new tab,
  // and Start or Done asks the host to start or finish the issue.
  document.addEventListener(
    "click",
    (event) => {
      const link = event.target.closest("a[href], button[data-type]");
      if (!link) return;
      event.preventDefault();
      event.stopPropagation();
      if (link.matches("button")) vscode.postMessage({ type: link.dataset.type, id: Number(link.dataset.id) });
      else vscode.postMessage({ type: "open", url: link.href });
    },
    true
  );

  // New issues start; In Progress ones reopen their worktree, which is the same request, and once
  // they link a PR, Done finishes them after its merge.
  const button = (type, label, title, id) =>
    `<button class="action" data-type="${type}" data-id="${Number(id)}" title="${escape(title)}">${escape(label)}</button>`;

  function row(issue, nested) {
    const details = document.createElement("details");
    details.dataset.id = issue.id;
    if (nested) details.classList.add("child");
    details.innerHTML = `<summary>
        <span class="repo" title="${escape(issue.project)}">${escape(issue.marker)}</span>
        <span class="id">${issueLink(issue.id)}</span>
        <span class="points">${escape(issue.points)}</span>
        <span class="subject">${escape(issue.subject)}</span>
        ${issue.status === "New" ? "" : `<span class="badge">${escape(issue.status)}</span>`}
        <span class="actions">${[
          issue.done ? button("done", "Done", `Finish #${Number(issue.id)} once its PR has merged`, issue.id) : "",
          issue.action ? button("start", issue.action, `${issue.action} #${Number(issue.id)} in its worktree`, issue.id) : "",
        ].join("")}</span>
      </summary>
      <section>Loading…</section>`;
    details.addEventListener("toggle", () => {
      if (!details.open || details.dataset.loaded) return;
      details.dataset.loaded = "pending";
      vscode.postMessage({ type: "detail", id: issue.id });
    });
    return details;
  }

  function renderList({ issues }) {
    const open = new Set([...list.querySelectorAll("details[open]")].map((d) => d.dataset.id));
    const ids = new Set(issues.map((i) => i.id));
    status.textContent = issues.length ? "" : "Nothing in Next up.";
    list.replaceChildren(...issues.map((issue) => row(issue, ids.has(issue.parent))));
    // A refresh reloads what was open, so it shows the issue as it is now.
    list.querySelectorAll("details").forEach((d) => open.has(d.dataset.id) && (d.open = true));
  }

  function renderDetail({ id, issue, error }) {
    const details = list.querySelector(`details[data-id="${id}"]`);
    if (!details) return;
    const section = details.querySelector("section");
    if (error) {
      delete details.dataset.loaded; // the next open tries again
      section.innerHTML = `<p class="error">${escape(error)}</p>`;
      return;
    }
    details.dataset.loaded = "yes";
    const value = (name) => (issue.custom_fields || []).find((f) => f.name === name)?.value || "";
    const meta = [issue.tracker?.name, issue.status?.name, value("Lane"), issue.fixed_version?.name]
      .filter(Boolean).map(escape).join(" · ");
    const dates = issue.start_date || issue.due_date ? `${issue.start_date || "?"} → ${issue.due_date || "?"}` : "";
    const relations = (issue.relations || []).map((r) =>
      r.issue_id === issue.id
        ? `${escape(r.relation_type.replace("_", " "))} ${issueLink(r.issue_to_id)}`
        : `${escape(INVERSE[r.relation_type] || r.relation_type)} ${issueLink(r.issue_id)}`
    );
    const children = (issue.children || []).map((c) => `<li>${issueLink(c.id)} ${escape(c.subject)}</li>`);
    const notes = (issue.journals || []).filter((j) => j.notes).reverse().map((j) =>
      `<li><div class="when">${escape(j.user?.name)} · ${escape(j.created_on.slice(0, 10))}</div>
        <div class="text">${markdown(j.notes)}</div></li>`
    );
    section.innerHTML = [
      `<p class="meta">${meta}${dates ? `<br>${escape(dates)}` : ""}${issue.assigned_to ? `<br>${escape(issue.assigned_to.name)}` : ""}</p>`,
      issue.parent ? `<p>Parent ${issueLink(issue.parent.id)}</p>` : "",
      relations.length ? `<p>${relations.join(", ")}</p>` : "",
      value("Pull requests") ? `<p>${markdown(value("Pull requests"))}</p>` : "",
      children.length ? `<ul class="children">${children.join("")}</ul>` : "",
      issue.description ? `<div class="text">${markdown(issue.description)}</div>` : "",
      notes.length ? `<h4>Notes</h4><ul class="notes">${notes.join("")}</ul>` : "",
      `<p>${issueLink(issue.id, "Open in Redmine")}</p>`,
    ].join("");
  }

  window.addEventListener("message", ({ data }) => {
    if (data.type === "list") {
      base = data.base;
      renderList(data);
    } else if (data.type === "detail") {
      renderDetail(data);
    } else if (data.type === "error") {
      status.innerHTML = `<span class="error">${escape(data.message)}</span>`;
    }
  });
  vscode.postMessage({ type: "ready" });
})();
