#!/usr/bin/env python3
"""What got done on the last few active days, what is in progress, and what comes next.

    status.py                 # last 3 active days, next 5
    status.py --days 5 --next 8 --json

An active day is a local date with tracked time for this repo or an issue closed in it, so
weekends and gaps are skipped rather than shown empty. Reads GitHub, git and `timew export`;
writes nothing.

"Next" stands in for gh-plan's `plan next` until it ships: open issues not in the Icebox and not
blocked by an open issue, ordered by milestone and then backlog ID. Work in progress comes first.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from time_report import TITLE_ESTIMATE, parse_intervals  # noqa: E402

BACKLOG = re.compile(r"Backlog ID:\s*([\w.]+)")
MILESTONE = re.compile(r"Milestone:\s*(\S+)")
BRANCH_ISSUE = re.compile(r"^(?:[^/]+/)?(\d+)-")


@dataclass
class Task:
    number: int
    title: str
    estimate: int | None
    milestone: str | None
    backlog: str | None
    blocked_by: list[int] = field(default_factory=list)
    wip: list[str] = field(default_factory=list)  # why it counts as in progress


def parse(issue: dict) -> Task:
    body = issue.get("body") or ""
    m, b, e = MILESTONE.search(body), BACKLOG.search(body), TITLE_ESTIMATE.match(issue["title"])
    blockers = (issue.get("blockedBy") or {}).get("nodes", [])
    return Task(issue["number"], TITLE_ESTIMATE.sub("", issue["title"]).strip(), int(e[1]) if e else None,
                m[1] if m and m[1] not in ("—", "-") else None, b[1] if b else None,
                [n["number"] for n in blockers if n.get("state") == "OPEN"])


def backlog_key(backlog: str | None) -> tuple:
    if not backlog:
        return (2, ())
    parts = backlog.split(".")
    return (1 if not parts[0].isdigit() else 0, tuple(int(p) if p.isdigit() else p for p in parts))


def milestone_key(milestone: str | None) -> tuple:
    if milestone is None:
        return (1, 0, "")
    m = re.search(r"(\d+)$", milestone)
    return (0, int(m[1]) if m else 10**6, milestone)


def mark_wip(tasks: dict[int, Task], branches: list[str], current: str, running_tags: set[str], slug: str,
             assigned: dict[int, list[str]] | None = None, me: str | None = None):
    for n, logins in (assigned or {}).items():
        if me in logins and n in tasks:
            tasks[n].wip.append(f"assigned to {me}")
    for b in branches:
        if (m := BRANCH_ISSUE.match(b)) and int(m[1]) in tasks:
            tasks[int(m[1])].wip.append(f"current branch {b}" if b == current else f"branch {b}")
    for t in running_tags:
        if t.startswith(f"{slug}#") and (n := int(t.rsplit("#", 1)[1])) in tasks:
            tasks[n].wip.append("timer running")


def next_tasks(tasks: dict[int, Task], count: int) -> list[Task]:
    wip = [t for t in tasks.values() if t.wip]
    ready = [t for t in tasks.values() if not t.wip and t.milestone and not t.blocked_by]
    wip.sort(key=lambda t: (not any(w.startswith("current") for w in t.wip), backlog_key(t.backlog)))
    ready.sort(key=lambda t: (milestone_key(t.milestone), backlog_key(t.backlog), t.number))
    return wip + ready[: max(0, count - len(wip))]


def active_days(closed: list[dict], intervals, slug: str, today: date, days: int) -> list[date]:
    seen = {datetime.fromisoformat(i["closedAt"].replace("Z", "+00:00")).astimezone().date() for i in closed}
    for i in intervals:
        if f"repo:{slug}" in i.tags or any(t.startswith(f"{slug}#") for t in i.tags):
            seen.add(i.start.astimezone().date())
    return sorted(d for d in seen if d <= today)[-days:]  # oldest first


def tracked_by_day(intervals, slug: str, now: datetime) -> dict[date, float]:
    hours = defaultdict(float)
    for i in intervals:
        if f"repo:{slug}" in i.tags or any(t.startswith(f"{slug}#") for t in i.tags):
            hours[i.start.astimezone().date()] += i.seconds(now) / 3600
    return hours


def render(days: list[date], closed: list[dict], hours: dict[date, float], upcoming: list[Task]) -> str:
    out = []
    by_day = defaultdict(list)
    for i in closed:
        if i.get("stateReason") == "NOT_PLANNED":
            continue
        by_day[datetime.fromisoformat(i["closedAt"].replace("Z", "+00:00")).astimezone().date()].append(i)
    out.append(f"Done, last {len(days)} active days")
    for d in days:
        items = sorted(by_day.get(d, []), key=lambda i: i["closedAt"])
        pts = sum(int(m[1]) for i in items if (m := TITLE_ESTIMATE.match(i["title"])))
        out.append(f"  {d:%a %b %d}   {len(items)} closed · {pts} pts · {hours.get(d, 0):.1f} h tracked")
        for i in items:
            out.append(f"    #{i['number']:<4} {i['title']}")
    out.append("")
    out.append(f"Next {len(upcoming)}")
    for t in upcoming:
        est = f"[{t.estimate}]" if t.estimate else "[?]"
        where = f"{t.milestone or 'icebox'} {t.backlog or ''}".strip()
        flag = f"   WIP: {', '.join(t.wip)}" if t.wip else ""
        blocked = f"   blocked by {', '.join(f'#{n}' for n in t.blocked_by)}" if t.wip and t.blocked_by else ""
        out.append(f"  #{t.number:<4} {est:<4} {where:<14} {t.title}{flag}{blocked}")
    return "\n".join(out)


def sh(cmd: list[str], default: str = "") -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else default


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--next", type=int, default=5, dest="count")
    p.add_argument("--repo")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    slug = a.repo or json.loads(sh(["gh", "repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]
    open_issues = json.loads(sh(["gh", "issue", "list", "--repo", slug, "--state", "open", "--limit", "1000",
                                 "--json", "number,title,body,blockedBy,assignees"], "[]"))
    closed = json.loads(sh(["gh", "issue", "list", "--repo", slug, "--state", "closed", "--limit", "200",
                            "--json", "number,title,closedAt,stateReason"], "[]"))
    intervals = parse_intervals(json.loads(sh(["timew", "export"], "[]") or "[]"))
    now = datetime.now(timezone.utc)

    tasks = {i["number"]: parse(i) for i in open_issues}
    branches = sh(["git", "branch", "--format=%(refname:short)"]).split()
    current = sh(["git", "branch", "--show-current"]).strip()
    running = {t for i in intervals if i.end is None for t in i.tags}
    me = sh(["gh", "api", "user", "--jq", ".login"]).strip() or None
    assigned = {i["number"]: [a["login"] for a in i.get("assignees") or []] for i in open_issues}
    mark_wip(tasks, branches, current, running, slug, assigned, me)

    days = active_days(closed, intervals, slug, date.today(), a.days)
    closed_in = [i for i in closed if datetime.fromisoformat(i["closedAt"].replace("Z", "+00:00")).astimezone().date() in days]
    upcoming = next_tasks(tasks, a.count)

    if a.json:
        print(json.dumps({"repo": slug, "days": [d.isoformat() for d in days], "closed": closed_in,
                          "next": [t.__dict__ for t in upcoming]}, indent=2))
        return 0
    print(f"{slug} · status {date.today():%a %b %d}\n")
    print(render(days, closed_in, tracked_by_day(intervals, slug, now), upcoming))
    return 0


if __name__ == "__main__":
    sys.exit(main())
