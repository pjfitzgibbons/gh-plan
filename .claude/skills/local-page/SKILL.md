---
name: local-page
description: Show Peter an HTML page (a demo, explainer, mock-up, report or Complete page) by writing it to a local file and serving it on 127.0.0.1. Use whenever a page, HTML demo or artifact would be made; never publish to claude.ai, which a global hook refuses.
---

# Local pages

HTML pages for Peter stay on this machine. The Artifact tool's publish is refused by
`~/.claude/hooks/no_claude_ai_pages.py`. It refuses Claude Docs writes (`mcp__claude_ai_Claude_Docs__*`) too: a document is a local Markdown or HTML file. Reading, listing and opening existing artifacts and docs still work.

1. **Write the page** with Write, as one self-contained `.html` file. Put it in the place the work
   names; if no place fits, use the repo's `.tmp/` (ignored, and removed by Done), as
   `.tmp/<issue or topic>/<name>.html`. Never write it to the scratchpad, which Peter can't find.
2. **Serve it:**

   ```sh
   python3 ~/.claude/skills/local-page/scripts/serve_page.py <file.html> --open
   ```

   It serves the file's directory with `python3 -m http.server`, bound to 127.0.0.1 on a free
   port, and reuses a server already running for that directory. It prints the page's URL and
   the `vscode://pjfitzgibbons.worktask/browse?url=…` link. `--open` opens that link in VS Code's
   Integrated Browser, never the system browser.
3. **Tell Peter** the file's path and the URL. The link opens in the VS Code window that was
   focused last, which may not be this worktree's (gh-plan #92); say so if more than one window
   is open.
4. **Stop it** when the page is finished with: `serve_page.py <file.html> --stop`.

Pages load nothing from the network unless the page needs it; inline the CSS and scripts.
