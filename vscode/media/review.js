// The Review tab's one script: the extension's button, and the report's frame dressed in VS Code's
// theme. The frame runs no script of its own; this one reaches in only to copy the theme's colours
// and to hand each link click to the extension, which decides what, if anything, it opens.
(function () {
  const vscode = acquireVsCodeApi();
  const body = document.body;
  const button = document.getElementById("act");
  const hint = document.getElementById("hint");
  const frame = document.getElementById("report");
  const id = body.dataset.id;
  const state = { ship: body.dataset.ship === "1", busy: body.dataset.busy === "1", doing: body.dataset.doing };
  // What VS Code hands back after a window reload, so the extension shows the report again.
  vscode.setState({ file: body.dataset.file });

  // While Go or Ship runs, the button names the one running: Ship disarms before pr starts, and Go
  // arms before it stages.
  function draw() {
    const ship = state.busy ? state.doing === "ship" : state.ship;
    button.textContent = ship ? "Ship" : "Go";
    button.disabled = state.busy;
    button.setAttribute("aria-busy", String(state.busy));
    hint.textContent = state.busy
      ? ship ? `pr ${id} is running` : "Checking and staging"
      : ship ? `Runs pr ${id}` : "Checks the round's files and opens the staged changes";
  }

  button.addEventListener("click", () => {
    if (state.busy) return;
    state.busy = true;
    state.doing = state.ship ? "ship" : "go";
    draw();
    vscode.postMessage({ type: state.doing, shown: body.dataset.shown });
  });

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (message?.type !== "state") return;
    state.ship = Boolean(message.ship);
    state.busy = Boolean(message.busy);
    if (message.doing) state.doing = message.doing;
    draw();
  });

  // VS Code sets its theme as variables on this page's root and changes them with the theme; the
  // frame is a document of its own, so it gets a copy, and a base style that uses them.
  const BASE = `body { color: var(--vscode-foreground); background: var(--vscode-editor-background);
  font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); line-height: 1.5;
  margin: 0; padding: 12px 20px; }
a { color: var(--vscode-textLink-foreground); }
a:focus-visible { outline: 1px solid var(--vscode-focusBorder); }
code, pre { font-family: var(--vscode-editor-font-family); }
pre { background: var(--vscode-textCodeBlock-background); padding: 8px; overflow: auto; }`;

  // Only the theme's variables and classes are copied, and the last copy's removed, so the report's
  // own style and class on its root stay.
  let lent = { props: [], classes: [] };
  function theme() {
    const doc = frame.contentDocument;
    if (!doc?.documentElement) return;
    const root = doc.documentElement;
    const from = document.documentElement.style;
    const props = Array.from(from).filter((name) => name.startsWith("--"));
    const classes = Array.from(body.classList);
    for (const name of lent.props) if (!props.includes(name)) root.style.removeProperty(name);
    for (const name of props) root.style.setProperty(name, from.getPropertyValue(name));
    root.classList.remove(...lent.classes.filter((name) => !classes.includes(name)));
    root.classList.add(...classes);
    lent = { props, classes };
    if (!doc.getElementById("plan-theme")) {
      const style = doc.createElement("style");
      style.id = "plan-theme";
      style.textContent = BASE;
      // First, so the report's own styles still win.
      (doc.head || doc.documentElement).prepend(style);
    }
  }

  function dress() {
    const doc = frame.contentDocument;
    if (!doc || doc.planDressed) return;
    doc.planDressed = true;
    theme();
    doc.addEventListener("click", (event) => {
      const link = event.target.closest?.("a, area");
      const href = link && (link.getAttribute("href") ?? link.getAttributeNS("http://www.w3.org/1999/xlink", "href"));
      if (href === null || href === undefined) return;
      event.preventDefault();
      // A srcdoc frame resolves #id against this page's address, so an in-report link is scrolled
      // here rather than followed.
      if (href.startsWith("#")) {
        let target;
        try {
          target = doc.getElementById(decodeURIComponent(href.slice(1)));
        } catch {
          target = doc.getElementById(href.slice(1));
        }
        if (target) target.scrollIntoView();
        else if (href === "#" || href === "#top") doc.scrollingElement?.scrollTo(0, 0);
        return;
      }
      vscode.postMessage({ type: "link", href });
    });
  }

  // A short report's frame can finish loading before this script runs.
  frame.addEventListener("load", dress);
  if (frame.contentDocument?.readyState === "complete" && frame.contentDocument.URL === "about:srcdoc") dress();
  new MutationObserver(theme).observe(document.documentElement, { attributes: true, attributeFilter: ["style"] });
  new MutationObserver(theme).observe(body, { attributes: true, attributeFilter: ["class"] });

  draw();
})();
