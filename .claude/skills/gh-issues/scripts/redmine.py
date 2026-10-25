"""The Redmine API client the gh-issues scripts (new_issue.py, issue_body.py, redmine_set.py,
issue_relations.py) write through.

The API key comes from the Keychain item redmine-gh-plan (REDMINE_KEYCHAIN picks another), and
goes only in a request header. REDMINE_URL defaults to http://127.0.0.1:3010.
"""

import http.client
import json
import os
import subprocess
import urllib.error
import urllib.request

FORWARD = ["relates", "blocks", "precedes", "duplicates", "copied_to"]
REVERSE = {
    "blocked": "blocks",
    "blocked_by": "blocks",
    "follows": "precedes",
    "duplicated": "duplicates",
    "copied_from": "copied_to",
}


def canonical(a: int, kind: str, b: int) -> tuple[int, str, int]:
    """The relation as Redmine keeps it: a forward type, and relates with the lower issue first."""
    if kind in REVERSE:
        return b, REVERSE[kind], a
    if kind == "relates":
        return min(a, b), kind, max(a, b)
    return a, kind, b


class Refused(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code  # the HTTP status, when Redmine answered with an error


# A field Redmine takes as <name>_id and returns as {"id": …}, and the name a message gives it.
LINKS = {
    "project_id": ("project", "project"),
    "tracker_id": ("tracker", "tracker"),
    "status_id": ("status", "status"),
    "fixed_version_id": ("fixed_version", "version"),
    "parent_issue_id": ("parent", "parent"),
}


def normal(text: str) -> str:
    """A description as compared: no BOM, \\n line endings, one trailing newline. Redmine stores \\r\\n."""
    return text.lstrip("\ufeff").replace("\r\n", "\n").strip() + "\n"


def latest_note(journals: list[dict]) -> str | None:
    """The newest journal note by id: Redmine reverses journals for a user who reads comments newest-first."""
    noted = [j for j in journals if j.get("notes")]
    return max(noted, key=lambda j: j["id"])["notes"] if noted else None


def by_name(items: list[dict], name: str, what: str) -> dict:
    found = [i for i in items if i["name"] == name]
    if not found:
        raise Refused(f"no {what} {name!r}; {what}s: {', '.join(i['name'] for i in items)}")
    return found[0]


def every(redmine, path: str, key: str) -> list[dict]:
    """Every item a Redmine listing holds under key, each page of it."""
    found, offset = [], 0
    sep = "&" if "?" in path else "?"
    while True:
        page = redmine.send("GET", f"{path}{sep}limit=100&offset={offset}")
        found += page[key]
        offset += len(page[key])
        if not page[key] or offset >= page["total_count"]:
            return found


def issue_fields(redmine) -> list[dict]:
    """The issue custom fields, with their values; Redmine lists them only for an admin's key."""
    return [
        f
        for f in redmine.send("GET", "/custom_fields.json")["custom_fields"]
        if f["customized_type"] == "issue"
    ]


def field_id(fields: list[dict], name: str, key: str, value: str) -> int:
    """The id of the custom field with this name; Refused when there's none, or value isn't one of its values."""
    field = by_name(fields, name, "issue custom field")
    allowed = [v["value"] for v in field.get("possible_values", [])]
    if allowed and value not in allowed:
        raise Refused(f"{key}: {value!r} isn't one of {', '.join(allowed)}")
    return field["id"]


def fetch(redmine, n: int, include: str = "") -> dict:
    try:
        return redmine.send(
            "GET", f"/issues/{n}.json" + (f"?include={include}" if include else "")
        )["issue"]
    except Refused as e:
        if e.code == 404:
            raise Refused(f"#{n} doesn't exist") from None
        raise


def unkept(issue: dict, sent: dict, names: dict[int, str] | None = None) -> list[str]:
    """Each field of an issue write that the issue as read back doesn't hold, by name.

    sent is the body's "issue" as POSTed or PUT. A cleared link ("" or None) is kept when the read-back
    has none. A note is kept when it is the latest note in the journals, read with include=journals.
    A custom field is named from names by its id, else "custom field <id>".
    """
    lost = []
    for key, value in sent.items():
        if key in LINKS:
            attr, name = LINKS[key]
            have = (issue.get(attr) or {}).get("id")
            if have != (value if value not in ("", None) else None):
                lost.append(name)
        elif key == "subject" and issue.get("subject") != value:
            lost.append("subject")
        elif key == "description" and normal(issue.get("description") or "") != normal(value):
            lost.append("description")
        elif key == "notes":
            if latest_note(issue.get("journals", [])) != value:
                lost.append("note")
        elif key == "custom_fields":
            have = {f["id"]: f.get("value") for f in issue.get("custom_fields", [])}
            lost += [
                (names or {}).get(f["id"], f"custom field {f['id']}")
                for f in value
                if have.get(f["id"]) != f["value"]
            ]
    return lost


def reasons(e: urllib.error.HTTPError) -> str:
    """Redmine's own error text, as its 422 body {"errors": [...]} gives it; empty for any other body."""
    try:
        errors = json.loads(e.read())["errors"]
    except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError):
        return ""
    if isinstance(errors, str):
        errors = [errors]
    return ": " + "; ".join(str(m) for m in errors) if errors else ""


class Redmine:
    def __init__(self):
        self.url = os.environ.get("REDMINE_URL", "http://127.0.0.1:3010").rstrip("/")
        service = os.environ.get("REDMINE_KEYCHAIN", "redmine-gh-plan")
        found = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
        )
        if found.returncode != 0:
            raise Refused(f"no Keychain item {service}; store your API key from My account in it")
        self.key = found.stdout.strip()

    def send(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.url + path,
            data=data,
            method=method,
            headers={"X-Redmine-API-Key": self.key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise Refused(f"Redmine {method} {path}: HTTP {e.code}{reasons(e)}", e.code) from None
        except urllib.error.URLError as e:
            raise Refused(f"Redmine can't be reached at {self.url}: {e.reason}") from None
        return json.loads(raw) if raw.strip() else None
