#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash). Enforces the Linux kernel's rule for AI
assistance on git commits and PR descriptions.

An AI is never an author. Only the human who reviewed the change can sign it
off and take responsibility for it, so no Co-Authored-By, Signed-off-by or
"Generated with" line may name an AI. The AI is recorded as the tool that
generated the work, with an Assisted-by trailer, and every commit Claude makes
must carry one:

    Assisted-by: AGENT_NAME:MODEL_VERSION [TOOL1] [TOOL2]

Source: https://docs.kernel.org/process/coding-assistants.html

A CLAUDE.md rule says so; this hook enforces it. Context might be ignored;
enforcement cannot.

Claude Code sends the pending tool call as JSON on stdin:
    {"tool_name": "Bash", "cwd": "...", "tool_input": {"command": "..."}}

Exit 0 allows the call. Exit 2 blocks it and returns stderr to the model,
which then rewrites the message.
"""

import json
import re
import shlex
import sys
from pathlib import Path

# A commit or PR verb counts only in command position: the start of a line, or
# right after ; && || | ( $(. Prose that merely *mentions* "git commit" (in a
# heredoc that edits a document, say) is not a commit and must pass.
COMMAND_POSITION = r"(?:^|[;&|(]\s*|\$\(\s*)"
GIT_GLOBAL_OPTIONS = r"(?:\s+-\S+(?:\s+[^-\s]\S*)?)*"  # -C <path>, -c k=v, --no-pager
GIT_COMMIT = COMMAND_POSITION + r"git" + GIT_GLOBAL_OPTIONS + r"\s+commit(?:\s|$)"
CHANGE_RECORD = re.compile(
    r"(?:" + GIT_COMMIT + r"|" + COMMAND_POSITION + r"gh\s+pr\s+(?:create|edit)(?:\s|$))",
    re.MULTILINE,
)
COMMIT = re.compile(GIT_COMMIT, re.MULTILINE)

# Markers of AI authorship. A Co-Authored-By or Signed-off-by naming a human is
# a legitimate record and does not match.
AI_AUTHORSHIP = re.compile(
    # [^\n"']* keeps each match inside one trailer, so a human's Co-Authored-By
    # beside an Assisted-by: Claude trailer on the same command line still passes.
    r"(?:co-authored-by|signed-off-by):[^\n\"']*(?:claude|anthropic)"
    r"|claude-session:"
    r"|generated with[^\n\"']*claude"
    r"|claude\.ai/code",
    re.IGNORECASE,
)

# The kernel's attribution trailer: an agent name and a model version.
ASSISTED_BY = re.compile(r"(?:^|[\s\"'])Assisted-by:\s*[A-Za-z][\w.-]*:[\w.-]+", re.MULTILINE)

# Commit options that keep an existing message instead of writing a new one.
# --fixup and --squash are not among them: they write a new fixup!/squash!
# commit, which needs a trailer like any other.
REUSE_OPTIONS = ("--no-edit", "--reuse-message", "-C")

# Characters that end one shell command and start the next.
SEPARATORS = ";&|()\n"

# git commit -F <file> and gh pr --body-file <file> keep the message where the
# command string can't show it. Those files are read too.
MESSAGE_FILE = re.compile(r"(?:-F|--file|--body-file)[= ]+['\"]?([^\s'\"]+)")

BLOCKED_AUTHORSHIP = """\
BLOCKED: commit message or PR description names an AI as an author.
An AI never co-authors or signs off; only the human who reviewed the change does.
Remove the Co-Authored-By, Signed-off-by, Claude-Session or "Generated with" line,
record the AI as `Assisted-by: AGENT_NAME:MODEL_VERSION`, and retry.
"""

BLOCKED_MISSING = """\
BLOCKED: commit message has no Assisted-by trailer.
Every commit Claude makes records the AI as the tool that generated it, for example:
    Assisted-by: Claude:claude-opus-5-5
Add the trailer as the last line of the message and retry.
"""


def is_change_record(command: str) -> bool:
    """True if the command commits or writes a PR description."""
    return CHANGE_RECORD.search(command) is not None


def read_file(name: str, cwd: str) -> str:
    """A message file's contents, or "" if it isn't a readable file."""
    path = Path(name)
    if not path.is_absolute():
        path = Path(cwd) / path
    return path.read_text(errors="replace") if path.is_file() else ""


def message_text(command: str, cwd: str = ".") -> str:
    """The command plus the contents of any message file it names."""
    text = command
    for match in MESSAGE_FILE.finditer(command):
        text += "\n" + read_file(match.group(1), cwd)
    return text


def shell_words(text: str) -> list[str]:
    """Split text the way the shell would, keeping separators as words."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=SEPARATORS)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # unbalanced quote: fall back to a rough split
        return text.split()


def commit_invocations(command: str):
    """Each git commit in the command, as (its arguments, the text after it).

    The arguments stop at the next command separator, so a trailer elsewhere in
    the command line can't stand in for one in the commit's own message.
    """
    for match in COMMIT.finditer(command):
        rest = command[match.start() :].lstrip(SEPARATORS + "$ \t")
        words = []
        for word in shell_words(rest):
            if word and all(c in SEPARATORS for c in word):
                break
            words.append(word)
        if "commit" in words:
            yield words[words.index("commit") + 1 :], rest


def reuses_message(args: list[str]) -> bool:
    return any(a.startswith(REUSE_OPTIONS) for a in args)


def commit_message(args: list[str], rest: str, cwd: str) -> str:
    """The commit's own message: its arguments plus any -F file it names."""
    text = "\n".join(args)
    for i, arg in enumerate(args):
        if arg in ("-F", "--file") and i + 1 < len(args):
            name = args[i + 1]
        elif arg.startswith("--file="):
            name = arg[len("--file=") :]
        elif arg.startswith("-F") and len(arg) > 2:
            name = arg[2:]
        else:
            continue
        # -F - reads stdin, usually a heredoc that follows the command.
        text += "\n" + (rest if name == "-" else read_file(name, cwd))
    return text


def violation(command: str, cwd: str = ".") -> str | None:
    """The whole rule. Returns the reason to block, or None to allow."""
    if not is_change_record(command):
        return None
    if AI_AUTHORSHIP.search(message_text(command, cwd)):
        return BLOCKED_AUTHORSHIP
    for args, rest in commit_invocations(command):
        if not reuses_message(args) and not ASSISTED_BY.search(commit_message(args, rest, cwd)):
            return BLOCKED_MISSING
    return None


def should_block(command: str, cwd: str = ".") -> bool:
    return violation(command, cwd) is not None


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--check":
        # Hand-check from a shell, no JSON needed:
        #   python3 no_ai_attribution.py --check 'git commit -m "..."'
        command, cwd = " ".join(sys.argv[2:]), "."
    else:
        payload = json.load(sys.stdin)
        command = (payload.get("tool_input") or {}).get("command") or ""
        cwd = payload.get("cwd") or "."
    reason = violation(command, cwd)
    if reason:
        sys.stderr.write(reason)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
