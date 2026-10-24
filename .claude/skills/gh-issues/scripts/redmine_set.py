#!/usr/bin/env python3
"""Change a Redmine issue or version, one committed command for each transition.

    redmine_set.py issue 69 --status "In Progress" --note "Reopened: the fix didn't hold"
    redmine_set.py issue 69 --version "M2 Many windows"     # or --no-version to clear it
    redmine_set.py issue 69 --subject "…" --points 3
    redmine_set.py issue 69 --description .tmp/69/issue-69-body.md --expect 3f2a9c01d4e5  # from --check
    redmine_set.py version "M1 Claude review" --status closed
    redmine_set.py version "M5 Next" --create --description "…"    # shared with the project tree
    redmine_set.py --check issue 69 --status Closed               # what would change; nothing written

Every name and value is looked up in Redmine and checked before the first write: the status
against the issue's workflow, the version among those the issue's project can use, open ones only
for a new one, and the points among the Points field's values. A description file must pass
outline.py's check, run from inside the repo so the repo's lenses count, and its write needs the
--expect that --check prints, so an edit made in Redmine since isn't overwritten. Reopening an issue in a
closed version is refused before any write, as Redmine would refuse it.

Each run prints the fields it changes, then reads the issue or version back and names any field
Redmine didn't keep. A field already as asked is left alone, and a note that is already the issue's
latest isn't added again, so a second run with the same arguments writes nothing and says so.
Relations are new_issue's, and a change over many issues is a list of these lines in a script.

Versions are looked up in --project, plan by default, whose versions are shared with its
subprojects; --create makes one there with sharing: tree.

The API key comes from the Keychain item redmine-gh-plan (REDMINE_KEYCHAIN picks another), and
goes only in a request header. REDMINE_URL defaults to http://127.0.0.1:3010.
"""

import argparse
import shlex
import sys
from pathlib import Path

import outline
from issue_body import diff, digest, read_body
from redmine import (
    Redmine,
    Refused,
    by_name,
    fetch,
    field_id,
    issue_fields,
    latest_note,
    normal,
    unkept,
)

VERSION_STATUSES = ["open", "locked", "closed"]


def points_id(redmine, issue: dict, points: str) -> int:
    """The Points field's id, its value checked when the key can list custom fields; else the issue's own."""
    try:
        return field_id(issue_fields(redmine), "Points", "points", points)
    except Refused as e:
        if e.code != 403:
            raise
    # Only an admin lists custom fields; Redmine itself refuses a value that isn't one of Points'.
    return by_name(issue.get("custom_fields", []), "Points", "issue custom field")["id"]


def custom(issue: dict, field_id: int):
    return next(
        (f.get("value") for f in issue.get("custom_fields", []) if f["id"] == field_id), None
    )


def plan_issue(redmine, n: int, a, lenses: list[str], extra: list[str]):
    """The issue, the PUT that moves it as asked, and (field, line) for each change; Refused before any write."""
    issue = fetch(redmine, n, "allowed_statuses,journals" if a.note else "allowed_statuses")
    put, changes = {}, []
    listed = []

    def versions() -> list[dict]:
        if not listed:
            path = f"/projects/{issue['project']['id']}/versions.json"
            listed.append(redmine.send("GET", path)["versions"])
        return listed[0]

    status = issue["status"]
    if a.status is not None and a.status != status["name"]:
        allowed = issue.get("allowed_statuses", [])
        found = [s for s in allowed if s["name"] == a.status]
        if not found:
            statuses = redmine.send("GET", "/issue_statuses.json")["issue_statuses"]
            by_name(statuses, a.status, "status")  # Refused when no status has the name
            can = ", ".join(s["name"] for s in allowed if s["id"] != status["id"])
            raise Refused(
                f"#{n} can't move from {status['name']} to {a.status}; "
                + (f"it can move to: {can}" if can else "it can't move to any status now")
                + "; Redmine allows only its workflow's transitions, no close while a blocker or "
                "subtask is open, and no reopen under a closed parent (docs/failure-modes.md)"
            )
        status = found[0]
        put["status_id"] = status["id"]
        changes.append(("status", f"status: {issue['status']['name']} → {status['name']}"))

    version = issue.get("fixed_version")
    if a.no_version and version:
        put["fixed_version_id"] = ""
        changes.append(("version", f"version: {version['name']} → none"))
        version = None
    elif a.version is not None and (version or {}).get("name") != a.version:
        new = one_version(versions(), a.version)
        if new["status"] != "open":
            raise Refused(
                f"version {new['name']} is {new['status']}, and Redmine assigns issues only to open "
                f"versions; reopen it with redmine-set version {shlex.quote(new['name'])} --status open"
            )
        put["fixed_version_id"] = new["id"]
        changes.append(
            ("version", f"version: {(version or {}).get('name', 'none')} → {new['name']}")
        )
        version = new

    if issue["status"].get("is_closed") and not status.get("is_closed", True) and version:
        state = version.get("status") or next(
            (v["status"] for v in versions() if v["id"] == version["id"]), None
        )
        if state == "closed":
            name = shlex.quote(version["name"])
            raise Refused(
                f"#{n} is in version {version['name']}, which is closed, and Redmine won't reopen an "
                f"issue in a closed version; add --no-version to clear it, or reopen the version "
                f"first with redmine-set version {name} --status open"
            )

    if a.subject is not None and a.subject != issue["subject"]:
        put["subject"] = a.subject
        changes.append(("subject", f"subject: {issue['subject']} → {a.subject}"))

    if a.points is not None:
        points = points_id(redmine, issue, a.points)
        now = custom(issue, points) or "none"
        if a.points != now:
            put["custom_fields"] = [{"id": points, "value": a.points}]
            changes.append(("points", f"points: {now} → {a.points}"))

    if a.description is not None:
        try:
            body = read_body(a.description, lenses, extra)
        except Refused as e:
            raise Refused(f"{a.description}: {e}") from None
        now = normal(issue.get("description") or "")
        if body != now:
            if not a.check and a.expect != digest(now):
                raise Refused(
                    f"#{n}'s description isn't the one --check showed; run the command with "
                    "--check, review its diff, then add the --expect it prints"
                )
            put["description"] = body
            changes.append(
                (
                    "description",
                    f"description: replaced by {a.description}\n"
                    + diff(n, now, body, a.description),
                )
            )

    if a.note:
        # A rerun repeats nothing; a note with a change is sent even when it repeats the last one.
        if not put and latest_note(issue.get("journals", [])) == a.note:
            print(f"redmine-set: #{n}'s latest note is already this one; not added again")
        else:
            put["notes"] = a.note
            changes.append(("note", f"note: {a.note}"))
    return issue, put, changes


def set_issue(redmine, n: int, a, lenses: list[str], extra: list[str]) -> str:
    issue, put, changes = plan_issue(redmine, n, a, lenses, extra)
    if not changes:
        return f"#{n} is already as asked; nothing written"
    for _, line in changes:
        print(f"  {line}", end="" if line.endswith("\n") else "\n")
    if a.check:
        said = f"#{n}: {len(changes)} change{'s' if len(changes) != 1 else ''} to make; nothing written"
        if "description" in put:
            now = digest(normal(issue.get("description") or ""))
            said += f"; to write it, run the same command without --check and with --expect {now}"
        return said
    redmine.send("PUT", f"/issues/{n}.json", {"issue": put})
    points = {f["id"]: "points" for f in put.get("custom_fields", [])}
    lost = unkept(fetch(redmine, n, "journals" if "notes" in put else ""), put, points)
    if lost:
        kept = [field for field, _ in changes if field not in lost]
        raise Refused(
            f"#{n} was written, and Redmine didn't keep: {', '.join(lost)}"
            f"{'; it kept: ' + ', '.join(kept) if kept else ''}; check it in Redmine"
        )
    return f"#{n} changed: {redmine.url}/issues/{n}"


def one_version(versions: list[dict], name: str) -> dict:
    """The one version with this name; Refused when none has it, or when two projects each have one."""
    same = [v for v in versions if v["name"] == name]
    if not same:
        raise Refused(f"no version {name!r}; versions: {', '.join(v['name'] for v in versions)}")
    if len(same) > 1:
        where = ", ".join(v["project"]["name"] for v in same)
        raise Refused(f"{len(same)} versions are named {name}, in {where}; rename one in Redmine")
    return same[0]


def version_unkept(back: dict, sent: dict) -> list[str]:
    """Each field of a version write that the version as read back doesn't hold; a blank is none."""
    return [k for k, value in sent.items() if (back.get(k) or "") != value]


def set_version(redmine, name: str, a) -> str:
    versions = redmine.send("GET", f"/projects/{a.project}/versions.json")["versions"]
    if a.create:
        # The list holds versions shared in from other projects; --create looks only at its own.
        project = redmine.send("GET", f"/projects/{a.project}.json")["project"]["id"]
        versions = [v for v in versions if v["project"]["id"] == project]
    found = []
    if not a.create or any(v["name"] == name for v in versions):
        found = [one_version(versions, name)]
    wanted = {}
    if a.status is not None:
        if a.status not in VERSION_STATUSES:
            raise Refused(f"version status {a.status!r} isn't one of {', '.join(VERSION_STATUSES)}")
        wanted["status"] = a.status
    if a.version_description is not None:
        wanted["description"] = a.version_description

    if not found:
        made = {"name": name, "sharing": "tree", **wanted}
        for key, value in made.items():
            print(f"  {key}: {value}")
        if a.check:
            return f"version {name} would be created in {a.project}; nothing written"
        v = redmine.send("POST", f"/projects/{a.project}/versions.json", {"version": made})[
            "version"
        ]
        back = redmine.send("GET", f"/versions/{v['id']}.json")["version"]
        lost = version_unkept(back, made)
        if lost:
            raise Refused(f"version {name} is created, but Redmine didn't keep: {', '.join(lost)}")
        return f"version {name} created: {redmine.url}/versions/{v['id']}"

    v = found[0]
    put = {k: value for k, value in wanted.items() if (v.get(k) or "") != value}
    if not put:
        return f"version {name} is already as asked; nothing written"
    for key, value in put.items():
        print(f"  {key}: {v.get(key) or 'none'} → {value}")
    if a.check:
        return f"version {name}: {len(put)} change{'s' if len(put) != 1 else ''} to make; nothing written"
    redmine.send("PUT", f"/versions/{v['id']}.json", {"version": put})
    back = redmine.send("GET", f"/versions/{v['id']}.json")["version"]
    lost = version_unkept(back, put)
    if lost:
        raise Refused(f"version {name} was written, and Redmine didn't keep: {', '.join(lost)}")
    return f"version {name} changed: {redmine.url}/versions/{v['id']}"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="redmine-set",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="check everything and print each change, reading only; write nothing",
    )
    sub = p.add_subparsers(dest="what", required=True)

    i = sub.add_parser(
        "issue", help="change an issue's status, version, subject, points, description or journal"
    )
    i.add_argument("issue", type=lambda s: int(s.lstrip("#")), help="the issue's number")
    i.add_argument("--status", help="a status by name, as In Progress or Closed")
    which = i.add_mutually_exclusive_group()
    which.add_argument("--version", help="a version the issue's project can use, by name")
    which.add_argument("--no-version", action="store_true", help="clear the issue's version")
    i.add_argument("--subject", help="the new title")
    i.add_argument("--points", help="one of the Points field's values: 1, 2, 3, 5, 8 or 13")
    i.add_argument(
        "--description", type=Path, metavar="FILE", help="the new description, in the issue outline"
    )
    i.add_argument(
        "--expect",
        metavar="DIGEST",
        help="with --description: the description --check showed, as it printed it",
    )
    i.add_argument("--note", help="a note for the issue's journal")
    i.add_argument(
        "--check", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )

    v = sub.add_parser("version", help="change, or with --create make, a version")
    v.add_argument("name", help="the version's name")
    v.add_argument("--status", help="open, locked or closed")
    v.add_argument(
        "--description", dest="version_description", help="the version's one-line description"
    )
    v.add_argument(
        "--create", action="store_true", help="make the version if it isn't there, sharing: tree"
    )
    v.add_argument("--project", default="plan", help="the project that holds it; plan by default")
    v.add_argument(
        "--check", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )
    return p


def main(argv=None) -> int:
    p = parser()
    a = p.parse_args(argv)
    if a.what == "issue" and a.expect is not None and a.description is None:
        p.error("--expect names the description --check showed; give it with --description")
    try:
        redmine = Redmine()
        if a.what == "version":
            said = set_version(redmine, a.name, a)
        else:
            lenses, extra = outline.load_expected() if a.description is not None else ([], [])
            said = set_issue(redmine, a.issue, a, lenses, extra)
        print(f"redmine-set: {said}")
    except Refused as e:
        sys.exit(f"redmine-set: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
