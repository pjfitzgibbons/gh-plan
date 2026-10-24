"""The Redmine API client the gh-issues scripts (new_issue.py, issue_body.py) write through.

The API key comes from the Keychain item redmine-gh-plan (REDMINE_KEYCHAIN picks another), and
goes only in a request header. REDMINE_URL defaults to http://127.0.0.1:3010.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request


class Refused(Exception):
    pass


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
            raise Refused(f"Redmine {method} {path}: HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise Refused(f"Redmine can't be reached at {self.url}: {e.reason}") from None
        return json.loads(raw) if raw.strip() else None
