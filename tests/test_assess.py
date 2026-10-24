"""`scripts/assess` runs every check, reports each on one line, and fails if any failed.

The suites here are stubs, `python -c` one-liners, so the test never runs the real pytest inside
itself; the checkers are stub executables in a temporary `checks/`.
"""

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "assess"

_loader = importlib.machinery.SourceFileLoader("assess", str(SCRIPT))
_spec = importlib.util.spec_from_loader("assess", _loader)
assess = importlib.util.module_from_spec(_spec)
_loader.exec_module(assess)


def suite(name: str, code: str) -> tuple[str, str, list[str]]:
    return (name, ".", [sys.executable, "-c", code])


PASSES = suite("passes", "print('all good')")
FAILS = suite("fails", "print('boom'); raise SystemExit(3)")


def checker(root: Path, name: str, body: str, executable: bool = True) -> Path:
    path = root / "checks" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755 if executable else 0o644)
    return path


def lines(out: str) -> list[str]:
    """The result lines, before the summary."""
    return [line for line in out.split("\nassess:")[0].splitlines() if line[:4] in ("PASS", "FAIL")]


def test_all_pass(tmp_path, capsys):
    checker(tmp_path, "clean", "# rule: clean-rule\n")
    assert assess.main([], root=tmp_path, suites=[PASSES]) == 0
    out = capsys.readouterr().out
    assert [line.split()[:2] for line in lines(out)] == [
        ["PASS", "passes"],
        ["PASS", "checks/clean"],
    ]
    assert "all good" not in out  # a passing check's output only with -v
    assert "assess: 2 passed, 0 failed" in out


def test_verbose_prints_passing_output(tmp_path, capsys):
    assert assess.main(["-v"], root=tmp_path, suites=[PASSES]) == 0
    assert "all good" in capsys.readouterr().out


def test_verbose_streams_output_while_the_check_runs(tmp_path, capsys):
    """A check that hangs until CI's timeout still shows how far it got."""
    go = tmp_path / "go"
    code = (
        "import pathlib, time; print('started', flush=True)\n"
        f"for _ in range(200):\n    if pathlib.Path({str(go)!r}).exists(): break\n    time.sleep(0.05)"
    )
    runner = threading.Thread(
        target=assess.run, args=([sys.executable, "-c", code], tmp_path, True)
    )
    runner.start()
    seen = ""
    for _ in range(100):
        seen += capsys.readouterr().out
        if "started" in seen:
            break
        time.sleep(0.05)
    go.touch()
    runner.join()
    assert "started" in seen


def test_two_failures_both_reported(tmp_path, capsys):
    checker(tmp_path, "finds", "# rule: a-rule\nprint('vscode/x.js:12: a violation')\n")
    assert assess.main([], root=tmp_path, suites=[FAILS, PASSES]) == 1
    out = capsys.readouterr().out
    assert [line.split()[:2] for line in lines(out)] == [
        ["FAIL", "fails"],
        ["PASS", "passes"],
        ["FAIL", "checks/finds"],
    ]
    assert "boom" in out
    assert "vscode/x.js:12: a violation" in out
    assert "assess: 1 passed, 2 failed" in out


def test_checker_that_exits_nonzero_silently_fails(tmp_path, capsys):
    checker(tmp_path, "crashes", "raise SystemExit(1)\n")
    assert assess.main([], root=tmp_path, suites=[]) == 1
    assert lines(capsys.readouterr().out)[0].startswith("FAIL  checks/crashes")


def test_checker_without_exec_bit_fails(tmp_path, capsys):
    checker(tmp_path, "forgotten", "# rule: r\n", executable=False)
    assert assess.main([], root=tmp_path, suites=[]) == 1
    assert "checks/forgotten: not executable" in capsys.readouterr().out


def test_command_that_cannot_start_fails(tmp_path, capsys):
    missing = ("missing", ".", [str(tmp_path / "no-such-tool")])
    assert assess.main([], root=tmp_path, suites=[missing, PASSES]) == 1
    out = capsys.readouterr().out
    assert lines(out)[0].startswith("FAIL  missing")
    assert lines(out)[1].startswith("PASS  passes")


def test_readme_and_dotfiles_are_not_checkers(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "README.md").write_text("# Checkers\n")
    (tmp_path / "checks" / ".DS_Store").write_bytes(b"\0")
    assert assess.checkers(tmp_path / "checks") == []
    assert assess.checkers(tmp_path / "absent") == []


def test_rules_lists_each_checker(tmp_path, capsys):
    checker(tmp_path, "b-check", "# rule: git-exit-unchecked\nprint()\n")
    checker(tmp_path, "a-check", "import sys\n# rule: after-code\n")
    checker(tmp_path, "c-check", "# a comment, not a rule\n")
    assert assess.main(["--rules"], root=tmp_path) == 0
    assert capsys.readouterr().out.splitlines() == [
        "after-code\tchecks/a-check",
        "git-exit-unchecked\tchecks/b-check",
        "(none)\tchecks/c-check",
    ]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("#!/bin/sh\n# rule: x-y\n", "x-y"),
        ("#!/bin/sh\n#!not a shebang\n# rule: x-y\n", None),
        ("# rule:x\n", "x"),
        ("# rule:\n", None),
        ("", None),
    ],
)
def test_rule(tmp_path, text, expected):
    path = tmp_path / "c"
    path.write_text(text)
    assert assess.rule(path) == expected


def test_rule_of_unreadable_file(tmp_path):
    path = tmp_path / "binary"
    path.write_bytes(b"\xff\xfe\x00")
    assert assess.rule(path) is None


def test_script_runs_on_python_3_9():
    """Annotations stay unevaluated, so an older python3 on PATH doesn't crash at import."""
    assert "from __future__ import annotations" in SCRIPT.read_text()


def test_script_is_executable_and_lists_repo_rules():
    """The committed script runs as a command, from any directory."""
    assert os.access(SCRIPT, os.X_OK)
    p = subprocess.run([str(SCRIPT), "--rules"], cwd="/", capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
