#!/usr/bin/env python3
"""Create a Redmine issue from a filled-in copy of references/redmine-issue.md.

    new_issue.py .tmp/79/retire-src.md            # create it, relate it, read it back
    new_issue.py --check .tmp/79/retire-src.md    # every check, reads only, nothing written

Run it inside the checkout of the repo the issue is for. The project is the one whose homepage is
that checkout's origin, as redmine/setup.rb sets it; outside a checkout, or for a repo no project
names, it refuses. The front matter holds the other fields, by the names Redmine shows:

    tracker     Bug, Feature or Support, as the project has them
    subject     the title
    version     a version the project can use, by name; blank for none
    status      New by default; Icebox for an issue not planned yet
    points      1, 2, 3, 5, 8 or 13; lane, one of the Lane field's values; ordinal, a number
    parent      an issue number; blocked_by and relates, issue numbers separated by commas

Blank fields are left out. The body after the front matter is the description, and it must pass
outline.py's check, run from inside the repo so the repo's lenses count. Every name is looked up
in Redmine and every value checked before anything is written.

A second run finds the issue by its subject in the project, creates nothing new, and adds any
relation still missing, so a run that stopped part way is finished by running it again. The issue
is read back at the end, and any field Redmine didn't keep is named.

The API key comes from the Keychain item redmine-gh-plan (REDMINE_KEYCHAIN picks another), and
goes only in a request header. REDMINE_URL defaults to http://127.0.0.1:3010.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

import outline
from redmine import Redmine, Refused, by_name, canonical, every, field_id, issue_fields, unkept

FIELDS = [
    "tracker",
    "subject",
    "version",
    "status",
    "points",
    "lane",
    "ordinal",
    "parent",
    "blocked_by",
    "relates",
]
CUSTOM = {"points": "Points", "lane": "Lane", "ordinal": "Ordinal"}


def parse(text: str) -> tuple[dict[str, str], str]:
    """The front matter's fields, blanks dropped, and the body after it."""
    m = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text.replace("\r\n", "\n"), re.S)
    if not m:
        raise Refused(
            "the file starts with a --- front matter block, as references/redmine-issue.md does"
        )
    fields = {}
    for n, line in enumerate(m[1].split("\n"), start=2):
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if key == "project":
            raise Refused(
                f"line {n}: the project comes from the repo new_issue runs in; delete the line"
            )
        if not sep or key not in FIELDS:
            raise Refused(f"line {n}: {line!r} isn't one of {', '.join(FIELDS)}")
        if key in fields:
            raise Refused(f"line {n}: {key} is given twice")
        if value.strip():
            fields[key] = value.strip()
    for key in ("tracker", "subject"):
        if key not in fields:
            raise Refused(f"{key} is blank")
    return fields, m[2].strip() + "\n"


def numbers(key: str, value: str) -> list[int]:
    out = []
    for part in value.split(","):
        part = part.strip().lstrip("#")
        if not part.isdigit() or int(part) < 1:
            raise Refused(f"{key}: {part!r} isn't an issue number")
        out.append(int(part))
    return out


def github(url: str) -> str:
    """A GitHub URL or remote, as github.com/<owner>/<repo>, so SSH and HTTPS forms compare equal.

    Any user or token before the host is dropped, so none is matched on or printed.
    """
    url = url.strip().lower().rstrip("/").removesuffix(".git")
    return re.sub(r"^(https?://|ssh://)?([^@/]+@)?github\.com[:/]", "github.com/", url)


def project_of(redmine, origin: str | None) -> str:
    """The identifier of the project whose homepage is origin; Refused outside a checkout or for no match."""
    if not origin:
        raise Refused("run new_issue inside the checkout of the repo the issue is for")
    origin = github(origin)
    found = [
        p["identifier"]
        for p in every(redmine, "/projects.json", "projects")
        if p.get("homepage") and github(p["homepage"]) == origin
    ]
    if not found:
        raise Refused(
            f"no project has {origin} as its homepage; redmine/setup.rb sets it for each repo in REPOS"
        )
    if len(found) > 1:
        raise Refused(f"{', '.join(found)} all have {origin} as their homepage; give it to one")
    return found[0]


def resolve(redmine, fields: dict[str, str], identifier: str) -> tuple[dict, dict[str, list[int]]]:
    """The issue as Redmine's POST takes it, and the relations to add; Refused naming the first bad field."""
    project = redmine.send("GET", f"/projects/{identifier}.json?include=trackers")["project"]
    issue = {
        "project_id": project["id"],
        "subject": fields["subject"],
        "tracker_id": by_name(project["trackers"], fields["tracker"], "tracker")["id"],
    }
    if "version" in fields:
        versions = redmine.send("GET", f"/projects/{identifier}/versions.json")["versions"]
        issue["fixed_version_id"] = by_name(versions, fields["version"], "version")["id"]
    statuses = redmine.send("GET", "/issue_statuses.json")["issue_statuses"]
    issue["status_id"] = by_name(statuses, fields.get("status", "New"), "status")["id"]

    custom = issue_fields(redmine)
    values = []
    for key, name in CUSTOM.items():
        if key not in fields:
            continue
        found = field_id(custom, name, key, fields[key])
        if key == "ordinal" and not fields[key].isdigit():
            raise Refused(f"ordinal: {fields[key]!r} isn't a number")
        values.append({"id": found, "value": fields[key]})
    if values:
        issue["custom_fields"] = values

    relations = {
        key: numbers(key, fields[key]) for key in ("blocked_by", "relates") if key in fields
    }
    if "parent" in fields:
        parent = numbers("parent", fields["parent"])
        if len(parent) != 1:
            raise Refused("parent: give one issue number")
        issue["parent_issue_id"] = parent[0]
    for n in [issue.get("parent_issue_id")] + [n for ns in relations.values() for n in ns]:
        if n is not None:
            redmine.send(
                "GET", f"/issues/{n}.json"
            )  # an issue that doesn't exist is a 404, Refused
    return issue, relations


def existing(redmine, project_id: int, subject: str) -> int | None:
    """The number of the project's one issue with this subject, open or closed; None if it has none."""
    found, offset = [], 0
    while True:
        page = redmine.send(
            "GET",
            f"/issues.json?project_id={project_id}&status_id=*&subproject_id=!*&limit=100&offset={offset}",
        )
        found += [i["id"] for i in page["issues"] if i["subject"] == subject]
        offset += len(page["issues"])
        if not page["issues"] or offset >= page["total_count"]:
            break
    if len(found) > 1:
        raise Refused(
            f"{', '.join(f'#{n}' for n in found)} all have this subject; give it a distinct one"
        )
    return found[0] if found else None


def held(relations: list[dict]) -> set:
    """The relations as Redmine keeps them, so 124 blocked 104 matches 104 blocks 124."""
    return {canonical(r["issue_id"], r["relation_type"], r["issue_to_id"]) for r in relations}


def kind(key: str) -> str:
    return "blocked" if key == "blocked_by" else "relates"


def run(
    file: Path, redmine, check_only: bool, lenses: list[str], extra: list[str], origin: str | None
) -> str:
    fields, body = parse(file.read_text(encoding="utf-8"))
    findings = outline.check(body, lenses, extra)
    if findings:
        raise Refused(
            "the body doesn't conform to the outline:\n" + "\n".join(f"  - {f}" for f in findings)
        )
    project = project_of(redmine, origin)
    issue, relations = resolve(redmine, fields, project)
    n = existing(redmine, issue["project_id"], issue["subject"])
    if check_only:
        return f"{file} is ready for {project}: {'#' + str(n) + ' exists, and a run adds only its missing relations' if n else 'a run creates it'}"

    if n is None:
        n = redmine.send("POST", "/issues.json", {"issue": {**issue, "description": body}})[
            "issue"
        ]["id"]
        made = "created"
    else:
        made = "already there"
    have = held(
        redmine.send("GET", f"/issues/{n}.json?include=relations")["issue"].get("relations", [])
    )
    added = 0
    for key, others in relations.items():
        for other in others:
            if canonical(n, kind(key), other) not in have:
                redmine.send(
                    "POST",
                    f"/issues/{n}/relations.json",
                    {"relation": {"issue_to_id": other, "relation_type": kind(key)}},
                )
                added += 1

    back = redmine.send("GET", f"/issues/{n}.json?include=relations")["issue"]
    kept_rels = held(back.get("relations", []))
    missing = [
        f"{key} #{o}"
        for key, others in relations.items()
        for o in others
        if canonical(n, kind(key), o) not in kept_rels
    ]
    lost = unkept(back, issue) + missing
    if lost and made == "created":
        raise Refused(f"#{n} is created, but Redmine didn't keep: {', '.join(lost)}")
    if lost:
        raise Refused(
            f"#{n} was already there, and differs from the file in: {', '.join(lost)}; edit it in Redmine"
        )
    url = redmine.url + f"/issues/{n}"
    return f"#{n} {made} in {project}, {added} relation{'s' if added != 1 else ''} added: {url}"


def origin() -> str | None:
    """The origin remote of the checkout new_issue runs in; None outside one, or with no origin."""
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
    if url.returncode != 0:
        return None
    return url.stdout.strip() or None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--check", action="store_true", help="check everything, reading only; write nothing"
    )
    p.add_argument("file", type=Path, help="a filled-in copy of references/redmine-issue.md")
    a = p.parse_args(argv)
    lenses, extra = outline.load_expected()
    try:
        print(f"new_issue: {run(a.file, Redmine(), a.check, lenses, extra, origin())}")
    except Refused as e:
        sys.exit(f"new_issue: {a.file}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
