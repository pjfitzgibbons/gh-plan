# Checkers

Each file here is a checker that `scripts/assess` runs after the test suites and lints. A checker
codifies a review rule: once the same finding has come up often enough, a checker catches it before
any reviewer has to.

## Writing a checker

- **An executable, run from the repo root.** Any language with a shebang line and `#` comments,
  such as shell, Python or Ruby. Commit it with its `+x` bit (`chmod +x`, then `git add`). A file
  here without it fails `assess` rather than being skipped.
- **The rule on the first comment line.** `# rule: <name>`, after the shebang, where `<name>` is the
  kebab-case rule the reviewer gives the finding, such as `git-exit-unchecked`.
  `scripts/assess --rules` lists every checker's rule, and `(none)` for a checker missing the line.
- **One line per violation.** `file:line: message`, with the path relative to the repo root, on
  stdout. A checker that prints anything, to stdout or stderr, or exits non-zero, fails, so a
  warning fails it too.
- **Read only.** A checker reads the repo and prints. It changes no file, opens no connection and
  reads no secret.
- **Fast.** `assess` runs every checker on every push, and CI's job has ten minutes for all of it.

An example, in shell:

```sh
#!/bin/sh
# rule: no-bare-git-push
# Every push goes through pr; nothing else runs git push.
# git grep reads tracked files only, so never node_modules/; its errors print, and fail the check.
git grep -n 'git push' -- 'vscode/*.js' ':!vscode/test/' | sed 's/:\([0-9]*\):.*/:\1: git push outside pr/'
```

`README.md` and dotfiles are not checkers.
