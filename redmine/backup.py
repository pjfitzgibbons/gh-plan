#!/usr/bin/env python3
"""Back Redmine up and export it to the USB drive, once a day (redmine/README.md, Backup and export).

    python3 redmine/backup.py           back up and export, unless today's folder is there
    python3 redmine/backup.py --plist   print the LaunchAgent that runs it nightly

Each run writes one dated folder, <drive>/redmine_backups/<YYYY-MM-DD>/:

    redmine.db      the database, through SQLite's online backup API, integrity-checked
    files.tar       the attachments volume
    export/issues/<id>.json
                    every issue with its journal, relations and custom fields, from the REST API

It builds the folder as .<date>.partial and renames it when complete, so a failed run leaves no
dated folder and the next run starts again. It keeps the newest KEEP dated folders and removes
the older ones. Beside them it keeps a .lock and clears its own leftover partials; nothing else in
redmine_backups is touched.

It reads Redmine and never writes to it, and it writes nothing outside the drive. When the drive
isn't mounted, Docker isn't running or any step fails, it logs why, shows a caution alert that
stays until dismissed, and exits 1.

Stdlib only, and Python 3.9, so launchd runs it with /usr/bin/python3.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path

LABEL = "local.gh-plan.redmine-backup"
VOLUME = Path(os.environ.get("REDMINE_BACKUP_VOLUME", "/Volumes/FAMILY_PHOTOS"))
KEEP = 14
HOUR = 3
COMPOSE = Path(__file__).resolve().parent / "compose.yml"
URL = os.environ.get("REDMINE_URL", "http://127.0.0.1:3010")
KEYCHAIN = os.environ.get("REDMINE_KEYCHAIN", "redmine-gh-plan")
DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
# Everything an issue's page shows but its custom fields, which every issue response carries.
INCLUDE = "children,attachments,relations,changesets,journals"

# The online backup API copies a consistent snapshot while Redmine keeps running, as sqlite3's
# .backup does. The container has no sqlite3 CLI, so Ruby's sqlite3 gem does it. Redmine runs
# the database in WAL mode, which the copy inherits; switching the copy to a rollback journal
# makes it one self-contained file, readable without a -wal or -shm beside it.
RUBY_BACKUP = """
require "sqlite3"
require "tempfile"
src = SQLite3::Database.new("sqlite/redmine.db", readonly: true)
Tempfile.create(["backup", ".db"]) do |f|
  dst = SQLite3::Database.new(f.path)
  b = SQLite3::Backup.new(dst, "main", src, "main")
  b.step(-1)
  b.finish
  dst.execute("PRAGMA journal_mode=DELETE")
  dst.close
  $stdout.binmode
  $stdout.write(File.binread(f.path))
end
"""


class Failure(Exception):
    """A reason the run stopped. Its message is what the log and the alert say."""


def log(message: str) -> None:
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"{stamp} {message}", file=sys.stderr, flush=True)


def alert(message: str) -> None:
    """A caution alert on screen until dismissed. Blocks until then; launchd waits with it."""
    script = ["on run argv", "display alert (item 1 of argv) message (item 2 of argv) as critical", "end run"]
    args = ["osascript"] + [part for line in script for part in ("-e", line)]
    done = subprocess.run(args + ["Redmine backup failed", message], capture_output=True, text=True)
    if done.returncode != 0:
        log(f"the alert didn't show: {done.stderr.strip()}")


def mounted(volume: Path) -> bool:
    """The volume is a mount point other than /. A bare folder, even on another device, doesn't count."""
    return os.path.ismount(volume) and volume.resolve() != Path("/")


def docker(args: list[str], out=None) -> bytes:
    """Run docker compose against Redmine's project. stdout goes to `out` when given."""
    cmd = ["docker", "compose", "-f", str(COMPOSE)] + args
    try:
        done = subprocess.run(cmd, stdout=out or subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise Failure("docker is not on PATH") from None
    if done.returncode != 0:
        detail = done.stderr.decode(errors="replace").strip().splitlines()
        raise Failure(f"docker compose {' '.join(args[:2])} failed: {detail[-1] if detail else done.returncode}")
    return done.stdout or b""


def docker_running() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def backup_db(dest: Path) -> set[int]:
    """Copy the database to dest, check it, and return its issue IDs."""
    with dest.open("wb") as out:
        docker(["exec", "-T", "-u", "redmine", "redmine", "bundle", "exec", "ruby", "-e", RUBY_BACKUP], out)
    db = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    try:
        check = db.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise Failure(f"the backup fails its integrity check: {check}")
        return {n for (n,) in db.execute("SELECT id FROM issues")}
    except sqlite3.DatabaseError as e:
        raise Failure(f"the backup isn't a readable database: {e}") from None
    finally:
        db.close()


def backup_files(dest: Path) -> None:
    with dest.open("wb") as out:
        docker(["exec", "-T", "redmine", "tar", "-C", "/usr/src/redmine/files", "-cf", "-", "."], out)


def api_key() -> str:
    done = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True)
    if done.returncode != 0:
        raise Failure(f"no Keychain item {KEYCHAIN} holds the Redmine API key")
    return done.stdout.strip()


def get(path: str, key: str) -> dict:
    """GET one REST path. Read only: the job never sends anything else."""
    request = urllib.request.Request(URL + path, headers={"X-Redmine-API-Key": key})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except OSError as e:
        raise Failure(f"GET {path.split('?')[0]} failed: {e}") from None


def export(dest: Path, key: str) -> set[int]:
    """Write each issue to dest/<id>.json, and return their IDs."""
    ids: list[int] = []
    while True:
        page = get(f"/issues.json?status_id=*&sort=id&limit=100&offset={len(ids)}", key)
        ids += [issue["id"] for issue in page["issues"]]
        if not page["issues"] or len(ids) >= page["total_count"]:
            break
    dest.mkdir(parents=True)
    for n in ids:
        issue = get(f"/issues/{n}.json?include={INCLUDE}", key)
        (dest / f"{n}.json").write_text(json.dumps(issue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return set(ids)


def prune(root: Path, keep: int, made: str) -> list[str]:
    """Remove all but the newest `keep` dated folders, never `made`. Returns the names removed."""
    dated = sorted(p.name for p in root.iterdir() if p.is_dir() and DATED.fullmatch(p.name))
    old = [name for name in dated[:-keep] if name != made]  # a clock gone back still keeps tonight's
    for name in old:
        shutil.rmtree(root / name)
    return old


def run(volume: Path, today: datetime.date) -> int:
    if not mounted(volume):
        raise Failure(f"{volume} isn't mounted. Nothing was written.")
    root = volume / "redmine_backups"
    day = root / today.isoformat()
    if day.exists():
        log(f"{day} is already there; nothing to do")
        return 0
    if not docker_running():
        raise Failure(f"Docker isn't running, so Redmine can't be backed up. Nothing was written to {volume}.")
    root.mkdir(exist_ok=True)
    with (root / ".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure(f"another backup holds {root / '.lock'}") from None
        if day.exists():  # the run that held the lock made it
            log(f"{day} is already there; nothing to do")
            return 0
        for stale in root.glob(".*.partial"):
            if DATED.fullmatch(stale.name[1 : -len(".partial")]):  # only what this job made
                shutil.rmtree(stale)
        partial = root / f".{today.isoformat()}.partial"
        partial.mkdir()
        try:
            issues = backup_db(partial / "redmine.db")
            backup_files(partial / "files.tar")
            exported = export(partial / "export" / "issues", api_key())
            # An issue the key can't see (an archived project), or one a deletion shifted off a page.
            # An issue made since the database was copied is exported only, which is fine.
            missing = sorted(issues - exported)
            if missing:
                shown = ", ".join(f"#{n}" for n in missing[:5]) + (", …" if len(missing) > 5 else "")
                raise Failure(f"the export is missing {len(missing)} of the database's issues: {shown}")
            partial.rename(day)
        finally:
            if partial.exists():
                shutil.rmtree(partial)
        log(f"{day}: database with {len(issues)} issues, attachments, {len(exported)} issues exported")
        for name in prune(root, KEEP, day.name):
            log(f"removed {root / name}, older than the newest {KEEP}")
    return 0


def plist() -> str:
    """The LaunchAgent: nightly at HOUR, once on wake if asleep then, and at login."""
    script = Path(__file__).resolve()
    if (script.parents[1] / ".git").is_file():
        # launchd would run a script that `pr done` deletes, and fail before it could alert.
        raise Failure(f"{script} is in a worktree; make the plist from the main checkout")
    docker_dir = os.path.dirname(shutil.which("docker") or "/usr/local/bin/docker")
    path = ":".join(dict.fromkeys([docker_dir, "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]))
    logfile = Path.home() / "Library" / "Logs" / "gh-plan-redmine-backup.log"
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": ["/usr/bin/python3", str(script)],
            "StartCalendarInterval": {"Hour": HOUR, "Minute": 0},
            "RunAtLoad": True,
            "EnvironmentVariables": {"PATH": path},
            "StandardOutPath": str(logfile),
            "StandardErrorPath": str(logfile),
        }
    ).decode()


def main(argv: list[str]) -> int:
    if argv == ["--plist"]:
        try:
            print(plist(), end="")
        except Failure as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    if argv:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        return run(VOLUME, datetime.date.today())
    except Exception as e:  # every failure is shown, not left in a log nobody reads
        message = str(e) if isinstance(e, Failure) else f"{type(e).__name__}: {e}"
        log(message)
        alert(message)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
