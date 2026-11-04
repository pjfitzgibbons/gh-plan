"""`tmux/plan-session` and `tmux/plan.tmux` against a temporary repo and a tmux server of their own.

Each test gets a repo with worktrees, a temporary HOME whose `.tmux.conf` sources `plan.tmux`,
and its own `TMUX_TMPDIR`, so the server is the test's and never a person's. `nvim`, `claude`,
`pbcopy`, `open` and `gh` are stand-ins on PATH that log what they were asked. A client that
attaches runs in a pseudo-terminal, as it would in a person's terminal.
"""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "tmux" / "plan-session"
PLAN_TMUX = ROOT / "tmux" / "plan.tmux"
PREFIX = b"\x02"  # C-b, tmux's default prefix

_loader = importlib.machinery.SourceFileLoader("plan_session", str(SCRIPT))
_spec = importlib.util.spec_from_loader("plan_session", _loader)
plan_session = importlib.util.module_from_spec(_spec)
_loader.exec_module(plan_session)

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux isn't installed")

STAND_INS = {
    "nvim": "exec sleep 600",
    "claude": 'echo started >> "$PLAN_TEST_LOGS/claude"; exec sleep 600',
    "pbcopy": 'cat > "$PLAN_TEST_LOGS/clipboard"',
    "open": 'echo "$@" >> "$PLAN_TEST_LOGS/open"',
    "gh": 'echo "$(pwd -P) $*" >> "$PLAN_TEST_LOGS/gh"',
}


class Plan:
    """The test's repo, environment and tmux server."""

    def __init__(self, root: Path):
        self.root = root
        self.repo = root / "repo"
        self.logs = root / "logs"
        self.logs.mkdir()
        self.env = self._environment()
        self._make_repo()
        self.clients: list[Client] = []

    def _environment(self) -> dict[str, str]:
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, body in STAND_INS.items():
            stand_in = bin_dir / name
            stand_in.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            stand_in.chmod(0o755)
        home = self.root / "home"
        home.mkdir()
        (home / ".tmux.conf").write_text(f"source-file '{PLAN_TMUX}'\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("TMUX", "REDMINE_"))}
        env.update(
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            TMUX_TMPDIR=str(self.root),
            SHELL="/bin/sh",
            TERM="xterm-256color",
            PLAN_TEST_LOGS=str(self.logs),
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Test",
            GIT_AUTHOR_EMAIL="test@example.com",
            GIT_COMMITTER_NAME="Test",
            GIT_COMMITTER_EMAIL="test@example.com",
        )
        return env

    def _make_repo(self) -> None:
        self.git("init", "-q", "-b", "main", str(self.repo), cwd=self.root)
        self.git("commit", "-q", "--allow-empty", "-m", "first")

    def git(self, *args: str, cwd: Path | None = None) -> None:
        subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, check=True)

    def worktree(self, branch: str) -> Path:
        path = (self.root / branch.replace("/", "-")).resolve()
        self.git("worktree", "add", "-q", "-b", branch, str(path))
        return path

    def plan_session(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), *args], cwd=self.repo, env=self.env, capture_output=True, text=True
        )

    def tmux(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["tmux", *args], env=self.env, capture_output=True, text=True)

    def attach(self, *command: str) -> "Client":
        client = Client(command, cwd=self.repo, env=self.env)
        self.clients.append(client)
        return client

    def client_sessions(self) -> list[str]:
        return self.tmux("list-clients", "-F", "#{client_session}").stdout.split()

    def windows(self, session: str) -> list[str]:
        return self.tmux("list-windows", "-t", f"={session}", "-F", "#{window_name}").stdout.split()

    def panes(self, session: str) -> "dict[str, Pane]":
        """The session's panes by where they sit: `left`, `top right` or `bottom right`."""
        fields = "#{pane_left}|#{pane_top}|#{pane_active}|#{pane_start_command}|#{pane_start_path}"
        listed = self.tmux("list-panes", "-t", f"={session}:", "-F", fields).stdout
        return dict(Pane.parse(line) for line in listed.splitlines())

    def client_key_tables(self) -> list[str]:
        return self.tmux("list-clients", "-F", "#{client_key_table}").stdout.split()

    def shown_messages(self, text: str) -> list[str]:
        """Messages shown to a client that contain `text`; the log's command lines left out."""
        log = self.tmux("show-messages").stdout.splitlines()
        return [line for line in log if text in line and " command: " not in line]

    def log(self, name: str) -> str:
        path = self.logs / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def close(self) -> None:
        self.tmux("kill-server")
        for client in self.clients:
            client.close()


class Pane(NamedTuple):
    command: str  # the command it started with, empty for the shell
    path: Path
    active: bool

    @staticmethod
    def parse(line: str) -> tuple[str, "Pane"]:
        left, top, active, command, path = line.split("|")
        place = "left" if left == "0" else ("top right" if top == "0" else "bottom right")
        return place, Pane(command.strip('"'), Path(path).resolve(), active == "1")


def plan_layout(worktree: Path) -> dict[str, Pane]:
    """The panes plan-session makes: Claude, active, beside nvim over a shell."""
    return {
        "left": Pane("claude", worktree, True),
        "top right": Pane("nvim", worktree, False),
        "bottom right": Pane("", worktree, False),
    }


class Client:
    """A tmux client in a pseudo-terminal; what it prints is read and dropped, so it never blocks."""

    def __init__(self, command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        self.master, follower = os.openpty()
        self.process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
            stdin=follower,
            stdout=follower,
            stderr=follower,
            start_new_session=True,
        )
        os.close(follower)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        try:
            while os.read(self.master, 4096):
                pass
        except OSError:
            pass

    def press(self, keys: bytes) -> None:
        os.write(self.master, keys)

    def close(self) -> None:
        self.process.kill()
        self.process.wait()
        os.close(self.master)


def eventually(check, timeout: float = 10.0):
    """Poll `check` until it returns something truthy, and return it; fail at the timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if found := check():
            return found
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {check.__doc__ or check}")


@pytest.fixture
def plan():
    # A short root under the system's temporary directory: the server's socket path must fit
    # in sun_path, which pytest's tmp_path can overrun on macOS.
    root = Path(tempfile.mkdtemp(prefix="ps-")).resolve()
    made = Plan(root)
    yield made
    made.close()
    shutil.rmtree(root, ignore_errors=True)


def test_first_call_outside_tmux_attaches_to_a_new_session_of_three_panes(plan):
    worktree = plan.worktree("feat/7-thing")

    plan.attach(str(SCRIPT), "7")

    eventually(lambda: plan.client_sessions() == ["plan-7"])
    assert plan.windows("plan-7") == ["plan"]
    assert plan.panes("plan-7") == plan_layout(worktree)
    assert plan.tmux("show-options", "-v", "-t", "=plan-7:", "@plan_issue").stdout.strip() == "7"


def test_first_call_inside_tmux_switches_the_client_to_the_new_session(plan):
    worktree = plan.worktree("fix/7-thing")
    plan.tmux("new-session", "-d", "-s", "outer", "-c", str(plan.repo))
    plan.attach("tmux", "attach-session", "-t", "=outer")
    eventually(lambda: plan.client_sessions() == ["outer"])

    # The pane's login shell runs macOS's path_helper, which puts /usr/bin, and its Python 3.9,
    # first; the README asks for 3.10 or newer, so the script runs under the suite's own Python.
    plan.tmux("send-keys", "-t", "=outer:", f"{sys.executable} {SCRIPT} 7", "Enter")

    eventually(lambda: plan.client_sessions() == ["plan-7"])
    assert plan.windows("plan-7") == ["plan"]
    assert plan.panes("plan-7") == plan_layout(worktree)


def test_second_call_goes_to_the_same_session_and_starts_no_second_claude(plan):
    plan.worktree("chore/7-thing")
    plan.attach(str(SCRIPT), "7")
    eventually(lambda: plan.client_sessions() == ["plan-7"])
    eventually(lambda: plan.log("claude"))

    plan.attach(str(SCRIPT), "7")

    eventually(lambda: plan.client_sessions() == ["plan-7", "plan-7"])
    assert plan.tmux("list-sessions", "-F", "#{session_name}").stdout.split() == ["plan-7"]
    assert list(plan.panes("plan-7")) == ["left", "top right", "bottom right"]
    assert plan.log("claude") == "started\n"


def test_an_issue_with_no_worktree_is_refused_naming_start_and_nothing_is_made(plan):
    plan.worktree("feat/70-another-issue")  # 70 starts with 7 but isn't issue 7

    done = plan.plan_session("7")

    assert done.returncode == 1
    assert "press Start" in done.stderr
    assert plan.tmux("list-sessions").returncode != 0  # no server, so no session


def test_an_issue_with_two_worktrees_is_refused_naming_both_and_nothing_is_made(plan):
    first = plan.worktree("feat/8-one")
    second = plan.worktree("fix/8-two")

    done = plan.plan_session("8")

    assert done.returncode == 1
    assert str(first) in done.stderr
    assert str(second) in done.stderr
    assert plan.tmux("list-sessions").returncode != 0


def test_a_listed_worktree_whose_folder_is_gone_is_refused_naming_prune_and_nothing_is_made(plan):
    shutil.rmtree(plan.worktree("feat/7-thing"))

    done = plan.plan_session("7")

    assert done.returncode == 1
    assert "git worktree prune" in done.stderr
    assert plan.tmux("list-sessions").returncode != 0


def test_a_running_session_is_gone_to_while_a_rebase_has_detached_its_worktree(plan):
    worktree = plan.worktree("feat/7-thing")
    plan.attach(str(SCRIPT), "7")
    eventually(lambda: plan.client_sessions() == ["plan-7"])
    plan.git("checkout", "-q", "--detach", cwd=worktree)

    plan.attach(str(SCRIPT), "7")

    eventually(lambda: plan.client_sessions() == ["plan-7", "plan-7"])


def test_only_claudes_pane_lets_escapes_through_tmux(plan):
    plan.worktree("feat/7-thing")
    plan.attach(str(SCRIPT), "7")
    eventually(lambda: plan.client_sessions() == ["plan-7"])

    fields = "#{pane_start_command}=#{allow-passthrough}"
    listed = plan.tmux("list-panes", "-t", "=plan-7:", "-F", fields).stdout.split()

    assert sorted(listed) == ["=off", "claude=on", "nvim=off"]


def test_a_missing_or_non_numeric_issue_is_a_usage_error(plan):
    assert plan.plan_session().returncode == 2
    assert plan.plan_session("#7").returncode == 2
    assert plan.plan_session("\u00b2").returncode == 2  # a digit to isdigit, not to int


def test_two_first_calls_at_once_share_one_whole_session(plan):
    worktree = plan.worktree("feat/7-thing")

    plan.attach(str(SCRIPT), "7")
    plan.attach(str(SCRIPT), "7")

    eventually(lambda: plan.client_sessions() == ["plan-7", "plan-7"])
    assert plan.windows("plan-7") == ["plan"]
    assert plan.panes("plan-7") == plan_layout(worktree)
    eventually(lambda: plan.log("claude"))
    assert plan.log("claude") == "started\n"


def test_v_shift_then_y_copies_the_selected_line_to_the_clipboard(plan):
    plan.tmux("new-session", "-d", "-s", "copy", "printf 'alpha\\nbeta\\n'; exec sleep 600")
    eventually(lambda: "beta" in plan.tmux("capture-pane", "-p", "-t", "=copy:").stdout)

    plan.tmux("copy-mode", "-t", "=copy:")
    plan.tmux("send-keys", "-t", "=copy:", "-X", "top-line")
    plan.tmux("send-keys", "-t", "=copy:", "V")
    plan.tmux("send-keys", "-t", "=copy:", "y")

    assert eventually(lambda: plan.log("clipboard")) == "alpha\n"


def test_prefix_i_opens_the_issue_and_prefix_g_its_pull_request(plan):
    worktree = plan.worktree("feat/7-thing")
    client = plan.attach(str(SCRIPT), "7")
    eventually(lambda: plan.client_sessions() == ["plan-7"])

    client.press(PREFIX + b"i")
    client.press(PREFIX + b"g")

    assert eventually(lambda: plan.log("open")) == "http://127.0.0.1:3010/issues/7\n"
    assert eventually(lambda: plan.log("gh")) == f"{worktree} pr view --web\n"


def test_prefix_i_and_g_outside_a_plan_session_keep_tmux_own_actions(plan):
    plan.tmux("new-session", "-d", "-s", "outer")
    client = plan.attach("tmux", "attach-session", "-t", "=outer")
    eventually(lambda: plan.client_sessions() == ["outer"])

    client.press(PREFIX + b"i")
    assert eventually(lambda: plan.shown_messages("current pane"))  # tmux's window details
    client.press(PREFIX + b"g")
    assert eventually(lambda: plan.client_key_tables() == ["move"])

    assert plan.log("open") == ""
    assert plan.log("gh") == ""


def porcelain_z(*blocks: str) -> str:
    """`git worktree list --porcelain -z` output from blocks written one line per `\\n`."""
    return "".join(block.replace("\n", "\0") + "\0\0" for block in blocks)


def test_listed_worktrees_reads_each_branch_and_marks_the_main_checkout():
    porcelain = porcelain_z(
        "worktree /repo\nbare",
        "worktree /wt/a\nHEAD abc\nbranch refs/heads/feat/7-a",
        "worktree /wt/b\nHEAD def\ndetached",
    )

    assert plan_session.listed_worktrees(porcelain) == [
        plan_session.Worktree(Path("/repo"), None, main=True),
        plan_session.Worktree(Path("/wt/a"), "feat/7-a", main=False),
        plan_session.Worktree(Path("/wt/b"), None, main=False),
    ]


def test_issue_worktrees_matches_the_branches_start_makes():
    branches = [
        "main",
        "feat/7-a",
        "feat/70-b",
        "spike/7-c",
        "fix/7",
        "chore/7-d",
        "7-e",
        "feat/7-",
    ]
    porcelain = porcelain_z(
        *(f"worktree /wt/{n}\nbranch refs/heads/{branch}" for n, branch in enumerate(branches))
    )

    found = plan_session.issue_worktrees(7, porcelain)

    assert found == [Path("/wt/1"), Path("/wt/5"), Path("/wt/6"), Path("/wt/7")]


def test_issue_worktrees_finds_a_detached_worktree_in_starts_folder():
    porcelain = porcelain_z(
        "worktree /r/repo\nbranch refs/heads/main",
        "worktree /r/repo.worktrees/8\nHEAD abc\ndetached",
        "worktree /r/repo.worktrees/7\nHEAD def\ndetached",
    )

    assert plan_session.issue_worktrees(7, porcelain) == [Path("/r/repo.worktrees/7")]


def test_a_worktree_whose_path_has_a_line_break_is_refused_and_nothing_is_made(plan):
    worktree = plan.root / "two\nlines" / "7"  # a line break splits plain porcelain; -z keeps it
    plan.git("worktree", "add", "-q", "-b", "feat/7-thing", str(worktree))

    done = plan.plan_session("7")

    assert done.returncode == 1
    assert "line break" in done.stderr
    assert repr(str(worktree)) in done.stderr
    assert plan.tmux("list-sessions").returncode != 0


def use_plan_environment(plan, monkeypatch):
    """Run the module's own functions against the test's server: its env, and no TMUX, so a
    suite run inside a person's tmux never reaches their server."""
    monkeypatch.delenv("TMUX", raising=False)
    for name, value in plan.env.items():
        monkeypatch.setenv(name, value)


def test_losing_the_race_waits_for_the_other_session_and_never_removes_it(plan, monkeypatch):
    worktree = plan.worktree("feat/7-thing")
    plan.tmux("new-session", "-d", "-s", "plan-7")  # another plan-session, part way through
    threading.Timer(0.3, plan.tmux, ["set-option", "-t", "=plan-7:", "@plan_issue", "7"]).start()
    use_plan_environment(plan, monkeypatch)

    plan_session.make_session("plan-7", 7, worktree)

    assert plan.tmux("show-options", "-v", "-t", "=plan-7:", "@plan_issue").stdout.strip() == "7"


def test_a_session_left_half_made_by_another_call_is_refused_and_kept(plan, monkeypatch):
    plan.tmux("new-session", "-d", "-s", "plan-7")
    use_plan_environment(plan, monkeypatch)

    with pytest.raises(plan_session.Refused, match="still half-made"):
        plan_session.wait_until_complete("plan-7", seconds=0.3)

    assert plan.tmux("has-session", "-t", "=plan-7").returncode == 0
