#!/usr/bin/env python3
"""Tick a Redmine issue's checkboxes from a copy of its description, and change nothing else.

    checklist.py 79 .tmp/79/issue.md          # what pr runs after its push

The file is the issue's description with boxes ticked or unticked. It is applied only when that is
its one difference from the description Redmine holds now, so an edit made in Redmine since the
copy was taken is never overwritten: any other difference stops it, naming the first such line.
The change is saved with a journal note listing each box, read back, and then the file is deleted.
It is safe to repeat: a file that changes no box is deleted with nothing written.

The API key comes from the Keychain item redmine-gh-plan (REDMINE_KEYCHAIN picks another), and
goes only in a request header. REDMINE_URL defaults to http://127.0.0.1:3010.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BOX = re.compile(r"^(\s*[-*+]\s+)\[([ xX])\]")


class Refused(Exception):
    pass


def lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").rstrip().split("\n")


def unboxed(line: str) -> str:
    return BOX.sub(r"\1[ ]", line)


def ticked(line: str) -> bool | None:
    m = BOX.match(line)
    return None if m is None else m.group(2) != " "


def changes(current: str, wanted: str) -> list[tuple[int, bool]]:
    """Each line whose box wanted changes, as (index, ticked); Refused if anything else differs."""
    have, want = lines(current), lines(wanted)
    for i in range(max(len(have), len(want))):
        a = have[i] if i < len(have) else None
        b = want[i] if i < len(want) else None
        if a is None or b is None or unboxed(a) != unboxed(b):
            shown = b if b is not None else a
            raise Refused(f"line {i + 1} differs by more than its box: {shown!r}")
    return [(i, ticked(b)) for i, (a, b) in enumerate(zip(have, want)) if ticked(a) != ticked(b)]


def apply(current: str, found: list[tuple[int, bool]]) -> str:
    """current with only the found boxes changed, its line endings kept."""
    out = current.replace("\r\n", "\n").split("\n")
    for i, on in found:
        out[i] = BOX.sub(lambda m: f"{m.group(1)}[{'x' if on else ' '}]", out[i], count=1)
    return ("\r\n" if "\r\n" in current else "\n").join(out)


def note(current: str, found: list[tuple[int, bool]]) -> str:
    have = lines(current)
    listed = "\n".join(f"* {'Ticked' if on else 'Unticked'}: {BOX.sub('', have[i]).strip()}" for i, on in found)
    return f"Checklist updated by pr:\n\n{listed}"


class Redmine:
    def __init__(self):
        self.url = os.environ.get("REDMINE_URL", "http://127.0.0.1:3010").rstrip("/")
        service = os.environ.get("REDMINE_KEYCHAIN", "redmine-gh-plan")
        found = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
        if found.returncode != 0:
            raise Refused(f"no Keychain item {service}; store your API key from My account in it")
        self.key = found.stdout.strip()

    def send(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"X-Redmine-API-Key": self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise Refused(f"Redmine {method} {path}: HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise Refused(f"Redmine can't be reached at {self.url}: {e.reason}") from None
        return json.loads(raw) if raw.strip() else None

    def description(self, n: int) -> str:
        return self.send("GET", f"/issues/{n}.json")["issue"].get("description") or ""


def run(n: int, file: Path, redmine) -> str:
    current = redmine.description(n)
    found = changes(current, file.read_text(encoding="utf-8"))
    if found:
        updated = apply(current, found)
        redmine.send("PUT", f"/issues/{n}.json", {"issue": {"description": updated, "notes": note(current, found)}})
        if lines(redmine.description(n)) != lines(updated):
            raise Refused(f"Redmine kept #{n}'s old description; {file} is kept")
    file.unlink()
    return f"#{n}: {len(found)} box{'es' if len(found) != 1 else ''} changed" if found else f"#{n}: no box to change"


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or not args[0].isdigit():
        sys.exit("usage: checklist.py <issue> <file>")
    file = Path(args[1])
    try:
        print(f"pr: {run(int(args[0]), file, Redmine())}")
    except Refused as e:
        sys.exit(f"pr: {file} not applied: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
