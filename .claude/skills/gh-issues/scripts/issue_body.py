#!/usr/bin/env python3
"""Replace a Redmine issue's description with a file's body.

    issue_body.py 90 .tmp/90/issue-90-body.md            # show the diff, write it, read it back
    issue_body.py --check 90 .tmp/90/issue-90-body.md    # the checks and the diff, nothing written

The file holds the description alone, no front matter, and it must pass outline.py's check, run
from inside the repo so the repo's lenses count, with no Done line ticked. The diff against the issue's current description
is shown before anything is written, and a file that matches it writes nothing.

The description is the only field written. The issue is read back after the write, and a
description Redmine didn't keep as sent, or any other field that changed, is named and exits
non-zero. Line endings and trailing blank lines don't count: Redmine stores \\r\\n.

The API key comes from the Keychain item redmine-gh-plan (REDMINE_KEYCHAIN picks another), and
goes only in a request header. REDMINE_URL defaults to http://127.0.0.1:3010.
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

import outline
from redmine import Redmine, Refused

UNCOMPARED = {"description", "updated_on"}


def normal(text: str) -> str:
    return text.lstrip("\ufeff").replace("\r\n", "\n").strip() + "\n"


def fetch(redmine, n: int) -> dict:
    try:
        return redmine.send("GET", f"/issues/{n}.json")["issue"]
    except Refused as e:
        if "HTTP 404" in str(e):
            raise Refused(f"#{n} doesn't exist") from None
        raise


def diff(n: int, now: str, body: str, file: Path) -> str:
    return "".join(difflib.unified_diff(now.splitlines(keepends=True), body.splitlines(keepends=True),
                                        f"#{n} in Redmine", str(file)))


def changed(before: dict, after: dict) -> list[str]:
    """Each field other than the description that differs between the two reads of the issue."""
    return sorted(k for k in (before.keys() | after.keys()) - UNCOMPARED if before.get(k) != after.get(k))


def run(n: int, file: Path, redmine, check_only: bool, lenses: list[str], extra: list[str]) -> str:
    body = normal(file.read_text(encoding="utf-8"))
    if body.startswith("---\n"):
        raise Refused("the file starts with front matter; give the description alone, its fields aren't read")
    findings = outline.check(body, lenses, extra)
    done = outline.sections(body).get("Done", "")
    findings += [f"Done: {l.strip()!r} is ticked; an issue's description is never ticked, the PR body is"
                 for l in done.splitlines() if re.match(r"^- \[x\] ", l.strip())]
    if findings:
        raise Refused("the body doesn't conform to the outline:\n" + "\n".join(f"  - {f}" for f in findings))
    before = fetch(redmine, n)
    now = normal(before.get("description") or "")
    if now == body:
        return f"#{n}'s description already matches {file}; nothing written"
    print(diff(n, now, body, file), end="")
    if check_only:
        return f"{file} is ready: a run replaces #{n}'s description with it"

    redmine.send("PUT", f"/issues/{n}.json", {"issue": {"description": body}})
    after = fetch(redmine, n)
    lost = ([] if normal(after.get("description") or "") == body else ["description"]) + changed(before, after)
    if lost:
        raise Refused(f"#{n} was written, and differs from what was sent in: {', '.join(lost)}; check it in Redmine")
    return f"#{n}'s description replaced: {redmine.url}/issues/{n}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="check the file and show the diff, reading only; write nothing")
    p.add_argument("issue", type=lambda s: int(s.lstrip("#")), help="the issue's number")
    p.add_argument("file", type=Path, help="the new description, in the issue outline")
    a = p.parse_args(argv)
    rw = outline.repo_writer()
    lenses, extra = outline.expected(
        outline.GLOBAL_WRITER.read_text(encoding="utf-8") if outline.GLOBAL_WRITER.is_file() else "",
        rw.read_text(encoding="utf-8") if rw else "",
    )
    try:
        print(f"issue_body: {run(a.issue, a.file, Redmine(), a.check, lenses, extra)}")
    except Refused as e:
        sys.exit(f"issue_body: {a.file}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
