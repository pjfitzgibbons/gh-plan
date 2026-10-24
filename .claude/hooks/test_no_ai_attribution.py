#!/usr/bin/env python3
"""Tests for no_ai_attribution.py: no AI authorship, and an Assisted-by trailer
on every commit Claude makes (the Linux kernel's rule).

Run:  python3 .claude/hooks/test_no_ai_attribution.py

A control without a test is an assertion. The suite passes whole or fails.

The fixtures below look like real commits because, to the hook, they are.
Writing this file through a shell heredoc trips the live hook for exactly
that reason; it was written with a file tool instead. Nothing here commits.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import no_ai_attribution as hook  # noqa: E402

HOOK = Path(hook.__file__)

TRAILER = "Assisted-by: Claude:claude-opus-5-5"

ALLOW = [
    ("commit with trailer", f'git commit -m "Act 0.3: attribution hook" -m "{TRAILER}"'),
    (
        "trailer with tools",
        'git commit -m "Fix" -m "Assisted-by: Claude:claude-opus-5-5 coccinelle sparse"',
    ),
    (
        "human co-author",
        f'git commit -m "Fix" -m "Co-Authored-By: Jane Doe <jane@example.com>" -m "{TRAILER}"',
    ),
    (
        "human sign-off",
        f'git commit -m "Fix" -m "{TRAILER}" -m "Signed-off-by: Jane Doe <jane@example.com>"',
    ),
    ("trailer in heredoc", f"git commit -m \"$(cat <<'M'\nAct 0.3: hook\n\n{TRAILER}\nM\n)\""),
    ("unrelated command", "ls -la && echo Co-Authored-By: Claude"),
    ("amend no-edit", "git commit --amend --no-edit"),
    ("reuse message", "git commit -C HEAD"),
    ("fixup with trailer", f'git commit --fixup abc1234 -m "{TRAILER}"'),
    ("stdin heredoc", f"git commit -F - <<'M'\nFix\n\n{TRAILER}\nM"),
    (
        "two commits, both with trailer",
        f'git commit -m "A" -m "{TRAILER}" && git commit -m "B" -m "{TRAILER}"',
    ),
    (
        "prose in heredoc",
        "python3 - <<'PY'\ns=\"if it is a `git commit` whose text carries `Co-Authored-By: Claude`\"\nPY",
    ),
    ("committer in prose", 'echo "the git committer said Co-Authored-By: Claude"'),
    ("gh pr view", "gh pr view 1 --json body | grep Claude-Session"),
    ("pr needs no trailer", 'gh pr create --title x --body "Closes #6"'),
]

# Commits Claude makes must record the AI as the generator.
MISSING_TRAILER = [
    ("plain commit", 'git commit -m "Act 0.3: attribution hook"'),
    ("agent without model", 'git commit -m "Fix" -m "Assisted-by: Claude"'),
    ("chained commit", 'git add -A && git commit -m "Fix"'),
    ("fixup", "git commit --fixup abc1234"),
    ("squash", "git commit --squash abc1234"),
    ("trailer before the commit", f'echo "{TRAILER}" && git commit -m "Fix"'),
    ("trailer after the commit", f'git commit -m "Fix" && echo "{TRAILER}"'),
    ("trailer on the next line", f'git commit -m "Fix"\necho "{TRAILER}"'),
    ("later reuse doesn't exempt", 'git commit -m "Fix" && git commit --amend --no-edit'),
    ("second commit bare", f'git commit -m "A" -m "{TRAILER}"; git commit -m "B"'),
]

BLOCK = [
    (
        "co-author beside trailer",
        f'git commit -m "Fix" -m "{TRAILER}" -m "Co-Authored-By: Claude <noreply@anthropic.com>"',
    ),
    (
        "claude sign-off",
        f'git commit -m "Fix" -m "{TRAILER}" -m "Signed-off-by: Claude <noreply@anthropic.com>"',
    ),
    (
        "claude co-author",
        'git commit -m "Fix" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"',
    ),
    (
        "claude-session heredoc",
        "git commit -m \"$(cat <<'M'\nAct 0.3: hook\n\nClaude-Session: https://claude.ai/code/session_abc\nM\n)\"",
    ),
    (
        "gh pr generated-with",
        'gh pr create --title x --body "🤖 Generated with [Claude Code](https://claude.com/claude-code)"',
    ),
    ("chained after &&", 'git add -A && git commit -m "x" -m "Claude-Session: y"'),
    (
        "git -C path commit",
        'git -C /tmp/x commit -m "x" -m "Co-Authored-By: Claude <noreply@anthropic.com>"',
    ),
    ("commit on second line", 'cd /tmp\ngit commit -m "Generated with Claude Code"'),
    ("inside $( )", 'x=$(git commit -m "claude.ai/code/session_1")'),
]


class RuleTests(unittest.TestCase):
    """The pure function, no process boundary."""

    def test_allows(self):
        for label, command in ALLOW:
            with self.subTest(label):
                self.assertFalse(hook.should_block(command), label)

    def test_blocks(self):
        for label, command in BLOCK:
            with self.subTest(label):
                self.assertTrue(hook.should_block(command), label)

    def test_blocks_missing_trailer(self):
        for label, command in MISSING_TRAILER:
            with self.subTest(label):
                self.assertEqual(hook.violation(command), hook.BLOCKED_MISSING, label)

    def test_authorship_wins_over_missing_trailer(self):
        """A message with both faults reports the authorship one."""
        command = 'git commit -m "Fix" -m "Co-Authored-By: Claude <noreply@anthropic.com>"'
        self.assertEqual(hook.violation(command), hook.BLOCKED_AUTHORSHIP)

    def test_blocks_message_file(self):
        """git commit -F hides the message in a file; the file is read."""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "msg.txt").write_text(
                f"Msg\n\n{TRAILER}\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
            )
            self.assertTrue(hook.should_block("git commit -F msg.txt", cwd=tmp))
            self.assertTrue(hook.should_block(f"git commit -F {tmp}/msg.txt"))

    def test_message_file_trailer(self):
        """A trailer inside a -F file counts; a file without one is blocked."""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "good.txt").write_text(f"Msg\n\n{TRAILER}\n")
            Path(tmp, "bare.txt").write_text("Msg\n")
            self.assertFalse(hook.should_block("git commit -F good.txt", cwd=tmp))
            self.assertEqual(
                hook.violation("git commit -F bare.txt", cwd=tmp), hook.BLOCKED_MISSING
            )


class ProcessTests(unittest.TestCase):
    """The hook as Claude Code runs it: JSON on stdin, exit code out."""

    @staticmethod
    def run_hook(command: str) -> subprocess.CompletedProcess:
        payload = json.dumps(
            {"tool_name": "Bash", "cwd": os.getcwd(), "tool_input": {"command": command}}
        )
        return subprocess.run(
            [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True
        )

    def test_exit_0_allows(self):
        result = self.run_hook(ALLOW[0][1])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_exit_2_blocks_with_reason(self):
        result = self.run_hook(BLOCK[0][1])
        self.assertEqual(result.returncode, 2)
        self.assertIn("BLOCKED", result.stderr)

    def test_missing_command_allows(self):
        result = subprocess.run(
            [sys.executable, str(HOOK)], input="{}", capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
