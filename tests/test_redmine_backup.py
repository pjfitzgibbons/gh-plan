"""redmine/backup.py: the nightly backup and export. Docker, the API and the alert are faked; the
drive is a folder that the tests call mounted or not."""

import datetime
import fcntl
import importlib.util
import json
import plistlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "backup", Path(__file__).parents[1] / "redmine" / "backup.py"
)
backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup)

NOW = datetime.datetime(2026, 10, 20, 14, 30, 5)
TODAY = NOW.date()
ISSUES = {1: "first", 2: "second", 3: "third"}
FINDINGS = {2: [{"fingerprint": "finding/v1:0000000000000001", "round": 1, "outcome": "fixed"}]}


def _db(path: Path, issues: int) -> None:
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE issues (id INTEGER)")
    db.executemany("INSERT INTO issues VALUES (?)", [(n,) for n in range(1, issues + 1)])
    db.commit()
    db.close()


class Fake:
    """Redmine, Docker and the screen, recording what the job asked of them."""

    def __init__(self, monkeypatch, tmp_path: Path):
        self.alerts: list[str] = []
        self.calls: list[str] = []
        self.mounted = True
        self.docker_up = True
        self.fail: str | None = None
        self.source = tmp_path / "live.db"
        _db(self.source, len(ISSUES))
        monkeypatch.setattr(backup, "mounted", lambda volume: self.mounted and volume.is_dir())
        monkeypatch.setattr(backup, "docker_running", lambda: self.docker_up)
        monkeypatch.setattr(backup, "docker", self.docker)
        monkeypatch.setattr(backup, "api_key", lambda: "k")
        monkeypatch.setattr(backup, "get", self.get)
        monkeypatch.setattr(backup, "alert", self.alerts.append)

    def step(self, name: str) -> None:
        self.calls.append(name)
        if self.fail == name:
            raise backup.Failure(f"{name} broke")

    def docker(self, args, out=None):
        self.step("tar" if "tar" in args else "db")
        out.write(b"tar" if "tar" in args else self.source.read_bytes())
        return b""

    def get(self, path, key):
        assert key == "k"
        if path.startswith("/issues.json"):
            self.step("list")
            offset = int(path.rsplit("offset=", 1)[1])
            page = [{"id": n} for n in sorted(ISSUES)][offset : offset + 2]
            return {"issues": page, "total_count": len(ISSUES)}
        if path.endswith("/review_findings.json"):
            self.step("findings")
            return {"review_findings": FINDINGS.get(int(path.split("/")[2]), [])}
        self.step("issue")
        n = int(path.split("/")[2].split(".")[0])
        return {"issue": {"id": n, "subject": ISSUES[n], "journals": []}}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    return Fake(monkeypatch, tmp_path)


@pytest.fixture
def volume(tmp_path):
    (tmp_path / "drive").mkdir()
    return tmp_path / "drive"


def _main(monkeypatch, volume: Path, *argv: str, now: datetime.datetime = NOW) -> int:
    monkeypatch.setattr(backup, "VOLUME", volume)
    return backup.main(list(argv), now)


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_a_run_leaves_a_dated_backup_and_export(fake, volume, monkeypatch):
    assert _main(monkeypatch, volume) == 0
    day = volume / "redmine_backups" / "2026-10-20"
    assert sorted(p.name for p in day.iterdir()) == ["export", "files.tar", "redmine.db"]
    with closing(sqlite3.connect(day / "redmine.db")) as db:
        assert db.execute("SELECT count(*) FROM issues").fetchone() == (3,)
    assert sorted(p.name for p in (day / "export" / "issues").iterdir()) == [
        "1.json",
        "2-findings.json",
        "2.json",
        "3.json",
    ]
    assert (
        json.loads((day / "export" / "issues" / "2.json").read_text())["issue"]["subject"]
        == "second"
    )
    assert json.loads((day / "export" / "issues" / "2-findings.json").read_text()) == {
        "review_findings": FINDINGS[2]
    }
    assert not list((volume / "redmine_backups").glob(".*.partial"))
    assert fake.alerts == []


def test_the_drive_unmounted_writes_nothing_and_alerts_its_path(fake, tmp_path, monkeypatch):
    missing = tmp_path / "FAMILY_PHOTOS"
    assert _main(monkeypatch, missing) == 1
    assert not missing.exists()
    assert fake.alerts == [f"{missing} isn't mounted. Nothing was written."]
    assert fake.calls == []


def test_a_bare_folder_on_the_system_disk_is_not_the_drive(tmp_path):
    assert backup.mounted(tmp_path) is False
    assert backup.mounted(tmp_path / "absent") is False
    assert backup.mounted(Path("/")) is False


def test_a_mount_point_is_the_drive(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.os.path, "ismount", lambda path: Path(path) == tmp_path)
    assert backup.mounted(tmp_path) is True


def test_docker_down_writes_nothing_and_alerts(fake, volume, monkeypatch):
    fake.docker_up = False
    assert _main(monkeypatch, volume) == 1
    assert _tree(volume) == []
    assert "Docker isn't running" in fake.alerts[0]


def test_a_second_run_that_day_does_nothing(fake, volume, monkeypatch):
    assert _main(monkeypatch, volume) == 0
    before = _tree(volume)
    fake.calls.clear()
    fake.docker_up = False  # it doesn't even look
    assert _main(monkeypatch, volume) == 0
    assert fake.calls == [] and fake.alerts == [] and _tree(volume) == before


def test_again_writes_a_timed_folder_beside_todays(fake, volume, monkeypatch):
    assert _main(monkeypatch, volume) == 0
    root = volume / "redmine_backups"
    (root / "2026-10-20" / "nightly").write_text("")
    assert _main(monkeypatch, volume, "--again") == 0
    assert (root / "2026-10-20" / "nightly").exists()
    timed = root / "2026-10-20-143005"
    assert backup.DATED.fullmatch(timed.name)
    assert sorted(p.name for p in timed.iterdir()) == ["export", "files.tar", "redmine.db"]
    assert fake.alerts == []


def test_a_second_again_in_the_same_second_fails(fake, volume, monkeypatch):
    assert _main(monkeypatch, volume, "--again") == 0
    before = _tree(volume)
    assert _main(monkeypatch, volume, "--again") == 1
    assert "already there" in fake.alerts[-1]
    assert _tree(volume) == before


def test_a_failed_again_leaves_no_timed_folder(fake, volume, monkeypatch):
    assert _main(monkeypatch, volume) == 0
    fake.fail = "tar"
    root = volume / "redmine_backups"
    nightly = _tree(root / "2026-10-20")
    assert _main(monkeypatch, volume, "--again") == 1
    assert _tree(root / "2026-10-20") == nightly
    assert not list(root.glob("2026-10-20-*"))
    assert not list(root.glob(".*.partial"))


@pytest.mark.parametrize("step", ["db", "tar", "list", "issue", "findings"])
def test_a_failed_step_leaves_no_dated_folder_and_the_next_run_finishes(
    fake, volume, monkeypatch, step
):
    fake.fail = step
    assert _main(monkeypatch, volume) == 1
    assert fake.alerts == [f"{step} broke"]
    assert _tree(volume) == ["redmine_backups", "redmine_backups/.lock"]
    fake.fail = None
    assert _main(monkeypatch, volume) == 0
    assert (volume / "redmine_backups" / "2026-10-20" / "export" / "issues" / "3.json").exists()


def test_an_unexpected_error_is_alerted_too(fake, volume, monkeypatch):
    monkeypatch.setattr(backup, "backup_files", lambda dest: 1 / 0)
    assert _main(monkeypatch, volume) == 1
    assert fake.alerts == ["ZeroDivisionError: division by zero"]


def test_a_corrupt_copy_fails(fake, volume, monkeypatch, tmp_path):
    fake.source.write_bytes(b"not a database" * 100)
    assert _main(monkeypatch, volume) == 1
    assert "isn't a readable database" in fake.alerts[0]
    assert not (volume / "redmine_backups" / "2026-10-20").exists()


def test_an_issue_a_deletion_shifts_off_a_page_fails(fake, volume, monkeypatch):
    real = fake.get

    def shifted(path, key):  # issue 1 deleted after the first page, so the second starts one on
        if path.startswith("/issues.json") and not path.endswith("offset=0"):
            base, offset = path.rsplit("offset=", 1)
            path = f"{base}offset={int(offset) + 1}"
        return real(path, key)

    monkeypatch.setattr(backup, "get", shifted)
    assert _main(monkeypatch, volume) == 1
    assert fake.alerts == ["the export is missing 1 of the database's issues: #3"]
    assert not (volume / "redmine_backups" / "2026-10-20").exists()


def test_an_issue_made_after_the_database_copy_is_exported_too(fake, volume, monkeypatch):
    _db(fake.source, len(ISSUES) - 1)  # the database was copied before #3 was made
    assert _main(monkeypatch, volume) == 0
    assert (volume / "redmine_backups" / "2026-10-20" / "export" / "issues" / "3.json").exists()


def test_an_export_short_of_the_database_fails(fake, volume, monkeypatch):
    _db(fake.source, len(ISSUES) + 1)  # one more issue than the API shows the key
    assert _main(monkeypatch, volume) == 1
    assert fake.alerts == ["the export is missing 1 of the database's issues: #4"]
    assert not (volume / "redmine_backups" / "2026-10-20").exists()


def test_stale_partials_are_cleared(fake, volume, monkeypatch):
    stale = volume / "redmine_backups" / ".2026-10-19.partial"
    stale.mkdir(parents=True)
    (stale / "redmine.db").write_text("half")
    mine = volume / "redmine_backups" / ".manual-copy.partial"
    mine.mkdir()
    assert _main(monkeypatch, volume) == 0
    assert not stale.exists()
    assert mine.is_dir()


def test_a_run_while_another_holds_the_lock_refuses(fake, volume, monkeypatch):
    root = volume / "redmine_backups"
    root.mkdir()
    with (root / ".lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert _main(monkeypatch, volume) == 1
    assert "another backup holds" in fake.alerts[0]
    assert not (root / "2026-10-20").exists()


def test_keeps_the_newest_copies_and_nothing_else_is_touched(fake, volume, monkeypatch):
    root = volume / "redmine_backups"
    old = [(TODAY - datetime.timedelta(days=d)).isoformat() for d in range(1, 16)]
    for name in old:
        (root / name).mkdir(parents=True)
    (root / "redmine-20261017-0603.db").write_text("by hand")
    assert _main(monkeypatch, volume) == 0
    kept = sorted(p.name for p in root.iterdir() if backup.DATED.fullmatch(p.name))
    assert len(kept) == backup.KEEP == 14
    assert kept[-1] == "2026-10-20" and kept[0] == "2026-10-07"
    assert (root / "redmine-20261017-0603.db").read_text() == "by hand"


def test_timed_copies_go_with_the_nightly_ones_around_them(fake, volume, monkeypatch):
    root = volume / "redmine_backups"
    for d in range(1, 16):
        day = (TODAY - datetime.timedelta(days=d)).isoformat()
        (root / day).mkdir(parents=True)
        (root / f"{day}-120000").mkdir()
    assert _main(monkeypatch, volume, "--again") == 0
    assert _main(monkeypatch, volume) == 0
    kept = sorted(p.name for p in root.iterdir() if backup.DATED.fullmatch(p.name))
    nightly = [name for name in kept if backup.NIGHTLY.fullmatch(name)]
    assert len(nightly) == backup.KEEP and nightly[0] == "2026-10-07"
    assert [name for name in kept if name not in nightly] == [
        f"{(TODAY - datetime.timedelta(days=d)).isoformat()}-120000" for d in range(13, 0, -1)
    ] + ["2026-10-20-143005"]


def test_a_clock_gone_back_keeps_tonights_copy(fake, volume, monkeypatch):
    root = volume / "redmine_backups"
    for d in range(14):
        (root / (TODAY + datetime.timedelta(days=d + 1)).isoformat()).mkdir(parents=True)
    assert _main(monkeypatch, volume) == 0
    assert (root / "2026-10-20").is_dir()


def _checkout(tmp_path: Path, monkeypatch, git: str) -> Path:
    script = tmp_path / "gh-plan" / "redmine" / "backup.py"
    script.parent.mkdir(parents=True)
    script.touch()
    if git == "dir":
        (tmp_path / "gh-plan" / ".git").mkdir()
    else:
        (tmp_path / "gh-plan" / ".git").write_text("gitdir: /elsewhere\n")
    monkeypatch.setattr(backup, "__file__", str(script))
    return script


def test_the_plist_runs_nightly_and_at_login(tmp_path, monkeypatch):
    script = _checkout(tmp_path, monkeypatch, "dir")
    agent = plistlib.loads(backup.plist().encode())
    assert agent["Label"] == backup.LABEL
    assert agent["ProgramArguments"] == ["/usr/bin/python3", str(script.resolve())]
    assert agent["StartCalendarInterval"] == {"Hour": 3, "Minute": 0}
    assert agent["RunAtLoad"] is True
    assert agent["StandardErrorPath"].endswith("Library/Logs/gh-plan-redmine-backup.log")


def test_no_plist_from_a_worktree(tmp_path, monkeypatch, capsys):
    _checkout(tmp_path, monkeypatch, "file")
    assert backup.main(["--plist"]) == 1
    assert "is in a worktree; make the plist from the main checkout" in capsys.readouterr().err


def test_plist_and_usage(tmp_path, monkeypatch, capsys):
    _checkout(tmp_path, monkeypatch, "dir")
    assert backup.main(["--plist"]) == 0
    assert "<key>Label</key>" in capsys.readouterr().out
    assert backup.main(["--bogus"]) == 2
    assert "--plist" in capsys.readouterr().err


def _stub(tmp_path: Path, monkeypatch, name: str, script: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / name).write_text(f"#!/bin/sh\n{script}\n")
    (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")


def test_docker_failures_name_the_command_and_its_last_line(tmp_path, monkeypatch):
    _stub(
        tmp_path,
        monkeypatch,
        "docker",
        'echo noise >&2; echo "service redmine is not running" >&2; exit 1',
    )
    assert backup.docker_running() is False
    with pytest.raises(
        backup.Failure, match="docker compose exec -T failed: service redmine is not running"
    ):
        backup.docker(["exec", "-T", "redmine", "true"])


def test_docker_output_reaches_the_file(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "docker", "printf data")
    assert backup.docker_running() is True
    backup.backup_files(tmp_path / "files.tar")
    assert (tmp_path / "files.tar").read_bytes() == b"data"


def test_no_docker_on_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert backup.docker_running() is False
    with pytest.raises(backup.Failure, match="docker is not on PATH"):
        backup.docker(["ps"])


def test_the_key_comes_from_the_keychain(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "security", 'echo "$@" > "$(dirname "$0")/args"; echo secret')
    assert backup.api_key() == "secret"
    assert (tmp_path / "bin" / "args").read_text().split() == [
        "find-generic-password",
        "-s",
        backup.KEYCHAIN,
        "-w",
    ]
    _stub(tmp_path, monkeypatch, "security", "exit 44")
    with pytest.raises(backup.Failure, match=f"no Keychain item {backup.KEYCHAIN}"):
        backup.api_key()


def test_an_unreachable_api_fails_without_the_query(monkeypatch):
    monkeypatch.setattr(backup, "URL", "http://127.0.0.1:9")
    with pytest.raises(backup.Failure, match=r"GET /issues\.json failed"):
        backup.get("/issues.json?status_id=*", "k")


def test_the_alert_is_a_critical_osascript_alert(tmp_path, monkeypatch, capsys):
    _stub(
        tmp_path,
        monkeypatch,
        "osascript",
        'for a in "$@"; do echo "$a"; done > "$(dirname "$0")/args"',
    )
    backup.alert("/Volumes/X isn't mounted")
    args = (tmp_path / "bin" / "args").read_text().splitlines()
    assert "as critical" in args[3] and args[-2:] == [
        "Redmine backup failed",
        "/Volumes/X isn't mounted",
    ]
    _stub(tmp_path, monkeypatch, "osascript", "echo 'not allowed' >&2; exit 1")
    backup.alert("x")
    assert "the alert didn't show: not allowed" in capsys.readouterr().err
