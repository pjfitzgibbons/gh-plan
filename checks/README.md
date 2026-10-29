# Checkers

Each file here is a checker that `scripts/assess` runs after the test suites and lints. A checker
codifies a review rule: once the same finding has come up often enough, a checker catches it before
any reviewer has to.

## Writing a checker

- **A Python file, run from the repo root.** `<rule>.py`, starting `#!/usr/bin/env python3`.
  Commit it with its `+x` bit (`chmod +x`, then `git add`). A file here without it, or a directory
  here, fails `assess` rather than being skipped. A check a review round wrote for a PR
  (`.tmp/<n>/<k>-check-<rule>.py`) has the same interface, so it can be committed here as it is.
- **The rule on the first comment line.** `# rule: <name>`, after the shebang, where `<name>` is the
  kebab-case rule the reviewer gives the finding, such as `git-exit-unchecked`.
  `scripts/assess --rules` lists every checker's rule, and `(none)` for a checker missing the line.
- **One line per match.** `file:lines: message`, such as `vscode/done.js:214-219: …` or
  `vscode/done.js:214: …`, with the path relative to the repo root, on stdout, and exit 0. A
  checker passes when it prints nothing. One that prints anything, to stdout or stderr, fails, so a
  warning fails it too; a non-zero exit means the checker itself failed.
- **Read only.** A checker reads the repo and prints. It changes no file, opens no connection and
  reads no secret.
- **Fast.** `assess` runs every checker on every push, and CI's job has ten minutes for all of it.

An example, `no-bare-git-push.py`:

```python
#!/usr/bin/env python3
# rule: no-bare-git-push
# Every push goes through pr; nothing else runs git push.
# git grep reads tracked files only, so never node_modules/; its errors print, and fail the check.
import subprocess

out = subprocess.run(
    ["git", "grep", "-n", "git push", "--", "vscode/*.js", ":!vscode/test/"],
    capture_output=True, text=True,
)
if out.returncode > 1:
    raise SystemExit(out.stderr)
for line in out.stdout.splitlines():
    file, lineno, _ = line.split(":", 2)
    print(f"{file}:{lineno}: git push outside pr")
```

`README.md` and dotfiles are not checkers.
