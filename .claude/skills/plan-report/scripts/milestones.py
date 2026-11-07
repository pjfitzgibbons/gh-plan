#!/usr/bin/env python3
"""Progress per milestone (Redmine version), with the next issues and the last ones closed.

    report-milestones                      # open versions; writes .tmp/milestones/progress.html
    report-milestones --all --next 8 --closed 8 --out report.html
    report-milestones --project tempotag

Every issue in a milestone is listed and counted, whatever its status: closed and rejected issues
stay in the list, marked. Progress is closed issues (Rejected included, as Redmine closes it) over
all issues, shown as a bar, a count and a percentage, with points beside it.

The report opens with two plain lists, each issue keyed by its milestone: the first N open issues
of Redmine's Next up query in plan order, each with the open issues that block it, and the N most
recently closed. Below them each milestone
lists all its issues as #<n>(<points>), coloured by status, with no other call-out: its open issues
in plan order, then its closed ones in the order they closed.

Plan order: milestone (in version order, numbers by value, no milestone last), then In Progress
before the rest, then Ordinal, then priority (highest first), then issue number; and an issue never
comes before an open issue that blocks it, whatever its milestone, even through a blocker outside
the list. A closed milestone is shown when it holds one of those issues. Reads Redmine through the
gh-issues skill's client; writes only the HTML file and, beside it, the options it ran with, which
report-server's Regenerate reuses.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gh-issues/scripts"))

from redmine import Redmine, Refused, by_name, every  # noqa: E402

PROJECT = "plan"
NEXT_UP = "Next up"
NONE = "No milestone"
PAGE = Path(".tmp/milestones/progress.html")


@dataclass
class Issue:
    id: int
    subject: str
    status: str
    closed: bool
    points: int | None
    closed_on: str | None
    version: str | None
    priority: int = 0  # Redmine's priority id: Low 1 … Immediate 5
    ordinal: int | None = None
    blockers: frozenset[int] = frozenset()  # the issues with a "blocks" relation to this one

    @property
    def kind(self) -> str:
        if self.status == "Rejected":
            return "rejected"
        if self.closed:
            return "closed"
        return "active" if self.status == "In Progress" else "open"


@dataclass
class Milestone:
    name: str
    description: str = ""
    status: str = "open"
    due: str | None = None
    issues: list[Issue] = field(default_factory=list)

    def count(self, kind: str) -> int:
        return sum(i.kind == kind for i in self.issues)

    @property
    def done(self) -> int:
        return sum(i.closed for i in self.issues)

    @property
    def percent(self) -> int:
        return round(100 * self.done / len(self.issues)) if self.issues else 0

    @property
    def points(self) -> tuple[int, int, int]:
        """(points closed, points in all, issues with no points)"""
        done = sum(i.points or 0 for i in self.issues if i.closed and i.status != "Rejected")
        total = sum(i.points or 0 for i in self.issues if i.status != "Rejected")
        unpointed = sum(i.points is None and i.status != "Rejected" for i in self.issues)
        return done, total, unpointed


@dataclass
class Plan:
    """What the report shows."""

    url: str
    upcoming: list[Issue]
    recent: list[Issue]
    milestones: list[Milestone]
    open_ids: Set[int]


def parse(raw: dict, closed_ids: set[int]) -> Issue:
    def number(name):
        v = next((c.get("value") for c in raw.get("custom_fields", []) if c["name"] == name), None)
        return int(v) if v and str(v).isdigit() else None

    return Issue(
        raw["id"],
        raw["subject"],
        raw["status"]["name"],
        raw["status"]["id"] in closed_ids,
        number("Points"),
        raw.get("closed_on"),
        (raw.get("fixed_version") or {}).get("name"),
        (raw.get("priority") or {}).get("id", 0),
        number("Ordinal"),
        frozenset(
            r["issue_id"]
            for r in raw.get("relations", [])
            if r["relation_type"] == "blocks" and r["issue_to_id"] == raw["id"]
        ),
    )


def natural_key(name: str) -> list[int | str]:
    """Sorts M2 before M10, whatever the case."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name.casefold())]


def version_order(versions: list[dict]) -> list[dict]:
    return sorted(versions, key=lambda v: natural_key(v["name"]))


def listed_blockers(i: Issue, listed: Set[int], chain: Mapping[int, Issue]) -> set[int]:
    """The listed issues holding i back, directly or through open issues outside the list."""
    found, seen, waiting = set(), {i.id}, list(i.blockers)
    while waiting:
        b = waiting.pop()
        if b in seen:
            continue
        seen.add(b)
        if b in listed:
            found.add(b)
        elif b in chain:
            waiting.extend(chain[b].blockers)
    return found


def plan_order(
    issues: list[Issue], versions: list[str], chain: Mapping[int, Issue] | None = None
) -> list[Issue]:
    """Open issues in plan order: milestone (in the order of versions, no milestone last), In
    Progress first, Ordinal, priority, issue number; and never before an open issue that blocks it.
    chain holds the open issues outside the list, whose blockers count too. A blocking cycle is
    broken at its first issue by that key."""
    rank = {v: n for n, v in enumerate(versions)}

    def key(i: Issue):
        return (
            rank.get(i.version, len(rank)),
            i.kind != "active",
            i.ordinal if i.ordinal is not None else float("inf"),
            -i.priority,
            i.id,
        )

    left = {i.id: i for i in issues if not i.closed}
    held_by = {i.id: listed_blockers(i, left.keys(), chain or {}) for i in left.values()}
    out = []
    while left:
        ready = [i for i in left.values() if not held_by[i.id] & left.keys()] or list(left.values())
        out.append(left.pop(min(ready, key=key).id))
    return out


def build(
    versions: list[dict],
    issues: list[Issue],
    highlighted: Sequence[Issue] = (),
    show_closed: bool = False,
) -> list[Milestone]:
    """Milestones in version order, each with all its issues. A closed one is shown with --all, or
    when it holds a highlighted issue (one of the next or last closed)."""
    ms = {
        v["name"]: Milestone(v["name"], v.get("description") or "", v["status"], v.get("due_date"))
        for v in version_order(versions)
    }
    for i in issues:
        if i.version in ms:
            ms[i.version].issues.append(i)
    wanted = {n for n, m in ms.items() if show_closed or m.status != "closed"} | {
        i.version for i in highlighted
    }
    chain = open_by_id(issues)
    for m in ms.values():
        done = sorted((i for i in m.issues if i.closed), key=lambda i: (i.closed_on or "", i.id))
        m.issues = plan_order(m.issues, list(ms), chain) + done
    return [m for n, m in ms.items() if n in wanted]


def open_by_id(issues: list[Issue]) -> dict[int, Issue]:
    return {i.id: i for i in issues if not i.closed}


def last_closed(issues: list[Issue], n: int) -> list[Issue]:
    closed = (i for i in issues if i.closed)
    return sorted(closed, key=lambda i: (i.closed_on or "", i.id), reverse=True)[:n]


def chip(i: Issue) -> str:
    return f"#{i.id}({i.points if i.points is not None else '–'})"


def bar(m: Milestone, width: int = 30) -> str:
    filled = round(width * m.done / len(m.issues)) if m.issues else 0
    return "█" * filled + "░" * (width - filled)


def blocked_by(i: Issue, open_ids: Set[int]) -> str:
    return " ".join(f"#{b}" for b in sorted(i.blockers & open_ids))


def render_text(
    upcoming: list[Issue],
    recent: list[Issue],
    milestones: list[Milestone],
    open_ids: Set[int] = frozenset(),
) -> str:
    out = [f"Next {len(upcoming)}"]
    for n, i in enumerate(upcoming, 1):
        out.append(f"  {n}. #{i.id:<4} {i.version or NONE:<20} {i.status:<12} {i.subject}")
        if blockers := blocked_by(i, open_ids):
            out.append(f"         blocked by {blockers}")
    out += ["", f"Last {len(recent)} closed"]
    out += [
        f"  {n}. #{i.id:<4} {i.version or NONE:<20} {(i.closed_on or '')[:10]:<12} {i.subject}"
        for n, i in enumerate(recent, 1)
    ]
    out.append("")
    for m in milestones:
        done, total, _ = m.points
        out.append(f"{m.name}{' (closed)' if m.status == 'closed' else ''}")
        out.append(
            f"  {bar(m)}  {m.done}/{len(m.issues)} closed  {m.percent}%  ·  {done}/{total} pts"
        )
        out.append("  " + " ".join(chip(i) + ("✓" if i.closed else "") for i in m.issues))
        out.append("")
    return "\n".join(out)


CSS = """
:root{--bg:#fbfaf7;--card:#fff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;--track:#ecebe6;
--closed:#2f8f5b;--warn:#a87400;--rejected:#9a9890;--active:#2f6fd0;--open:#c9c7c0}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;--muted:#a3a19a;
--line:#34332f;--track:#2a2926;--closed:#4cb87d;--warn:#e3b341;--rejected:#77756e;--active:#6ea2f2;--open:#4a4944}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:920px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 20px}.legend{display:flex;flex-wrap:wrap;gap:14px;color:var(--muted);font-size:13px;margin-bottom:20px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:16px}
h2{font-size:17px;margin:0}h2 .tag{font-size:12px;font-weight:500;color:var(--muted);margin-left:8px}
.exit{color:var(--muted);margin:4px 0 12px;font-size:14px}
.prog{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.bar{flex:1 1 260px;display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--track)}
.bar span{display:block;height:100%}.nums{font-variant-numeric:tabular-nums;white-space:nowrap}
.pct{font-size:20px;font-weight:650;margin-right:8px}.pts{color:var(--muted);font-size:13px}
h3{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:16px 0 6px}
ul{list-style:none;padding:0;margin:0}li{display:flex;flex-wrap:wrap;gap:2px 10px;padding:7px 0;border-top:1px solid var(--line);align-items:baseline}
li:first-child{border-top:0}.place{min-width:18px;color:var(--muted);font-variant-numeric:tabular-nums}\n.ms{min-width:130px;font-size:13px;color:var(--muted);white-space:nowrap}
.id{min-width:44px;font-variant-numeric:tabular-nums}.id a{color:inherit}
.st{font-size:12px;padding:1px 7px;border-radius:9px;white-space:nowrap;border:1px solid var(--line)}
.st.closed{color:var(--closed);border-color:var(--closed)}.st.rejected{color:var(--rejected)}
.st.active{color:var(--active);border-color:var(--active)}.subj{flex-basis:100%;padding-left:28px}.when,.p{color:var(--muted);font-size:13px;white-space:nowrap}
li.closed .subj,li.rejected .subj{color:var(--muted)}li.rejected .subj{text-decoration:line-through}
.chips{margin:0;line-height:2;font-variant-numeric:tabular-nums}
.chip{color:var(--ink);text-decoration:none;margin-right:6px}
.chip.closed{color:var(--closed)}.chip.rejected{color:var(--rejected);text-decoration:line-through}
.chip.active{color:var(--active)}
.blk{flex-basis:100%;padding-left:28px;font-size:13px;color:var(--warn)}.blk a{color:inherit}
.regen{margin-left:10px;font:inherit;font-size:13px;color:var(--active);background:none;
border:1px solid var(--active);border-radius:6px;padding:1px 9px;cursor:pointer}.regen:disabled{opacity:.5;cursor:default}
#regen-said{margin-left:8px;font-size:13px}
"""


# Regenerate posts to /regen on serve_report.py, which reruns this script and answers once the new
# page is written; the page then reloads. Served any other way, there's no /regen, and it says so.
REGEN = """<script>
document.getElementById("regen").addEventListener("click", async ev => {
  const b = ev.currentTarget, said = document.getElementById("regen-said");
  b.disabled = true; said.textContent = "regenerating…";
  try {
    const r = await fetch("/regen", {method: "POST"});
    if (r.ok) return location.reload();
    said.textContent = r.status === 501 ? "serve this page with report-server to regenerate it" : await r.text();
  } catch (e) { said.textContent = `no server: ${e.message}`; }
  b.disabled = false;
});
</script>"""


def issue_link(base: str, n: int) -> str:
    return f'<a href="{escape(base)}/issues/{n}">#{n}</a>'


def span_if(text: str, cls: str) -> str:
    return f"<span class={cls}>{text}</span>" if text else ""


def row(base: str, place: int, i: Issue, when: str = "", blockers: Sequence[int] = ()) -> str:
    points = f"{i.points}pt" if i.points is not None else "–pt"
    links = " ".join(issue_link(base, b) for b in blockers)
    blocked = f"blocked by {links}" if blockers else ""
    return (
        f'<li class="{i.kind}"><span class=place>{place}</span>'
        f"<span class=id>{issue_link(base, i.id)}</span>"
        f'<span class=ms>{escape(i.version or NONE)}</span><span class="st {i.kind}">{escape(i.status)}</span>'
        f"{span_if(escape(when), 'when')}<span class=p>{points}</span>"
        f"<span class=subj>{escape(i.subject)}</span>{span_if(blocked, 'blk')}</li>"
    )


def top_lists(plan: Plan) -> str:
    next_rows = "".join(
        row(plan.url, n, i, blockers=sorted(i.blockers & plan.open_ids))
        for n, i in enumerate(plan.upcoming, 1)
    )
    closed_rows = "".join(
        row(plan.url, n, i, (i.closed_on or "")[:10]) for n, i in enumerate(plan.recent, 1)
    )
    return (
        f"<section><h3>Next {len(plan.upcoming)}</h3><ul>{next_rows}</ul>"
        f"<h3>Last {len(plan.recent)} closed</h3><ul>{closed_rows}</ul></section>"
    )


def milestone_tag(m: Milestone) -> str:
    if m.status == "closed":
        return "closed"
    return f"due {m.due}" if m.due else ""


def progress(m: Milestone) -> str:
    total = len(m.issues) or 1
    segments = "".join(
        f'<span style="width:{100 * m.count(k) / total:.2f}%;background:var(--{k})" '
        f'title="{m.count(k)} {k}"></span>'
        for k in ("closed", "rejected", "active")
    )
    done, points, unpointed = m.points
    extra = f" · {unpointed} unpointed" if unpointed else ""
    return (
        f'<div class=prog><div class=bar role=img aria-label="{m.percent}% closed">{segments}</div>'
        f"<span class=nums><span class=pct>{m.percent}%</span>{m.done} / {len(m.issues)} closed"
        f"<br><span class=pts>{m.count('active')} in progress · {m.count('rejected')} rejected · "
        f"{done} / {points} points{extra}</span></span></div>"
    )


def chip_link(base: str, i: Issue) -> str:
    return (
        f'<a class="chip {i.kind}" href="{escape(base)}/issues/{i.id}" '
        f'title="{escape(i.status)}: {escape(i.subject)}">{chip(i)}</a>'
    )


def milestone_section(m: Milestone, base: str) -> str:
    tag = span_if(escape(milestone_tag(m)), "tag")
    exit_line = f"<p class=exit>{escape(m.description)}</p>" if m.description else ""
    chips = " ".join(chip_link(base, i) for i in m.issues)
    return (
        f"<section><h2>{escape(m.name)}{tag}</h2>{exit_line}{progress(m)}"
        f"<p class=chips>{chips}</p></section>"
    )


def legend() -> str:
    return "".join(
        f'<span><span class=sw style="background:var(--{k})"></span>{k.replace("active", "in progress")}</span>'
        for k in ("closed", "rejected", "active", "open")
    )


def render_html(plan: Plan, when: datetime) -> str:
    sections = "".join(milestone_section(m, plan.url) for m in plan.milestones)
    return (
        f'<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,'
        f'initial-scale=1"><title>Milestone progress</title><style>{CSS}</style></head><body><main>'
        f"<h1>Milestone progress</h1><p class=sub>{escape(plan.url)} · {when:%a %b %d %H:%M}"
        f" <button id=regen class=regen>Regenerate</button><span id=regen-said></span></p>"
        f"<div class=legend>{legend()}</div>{top_lists(plan)}{sections}</main>{REGEN}</body></html>"
    )


def read(r: Redmine, path: str, key: str):
    reply = r.send("GET", path)
    if reply is None:
        raise Refused(f"Redmine GET {path}: an empty answer")
    return reply[key]


def next_up_query(queries: list[dict], project: dict) -> dict:
    """The project's own Next up, else its parent's, else a global one."""
    scope = [project["id"], (project.get("parent") or {}).get("id"), None]
    in_scope = [q for q in queries if q.get("project_id") in scope]
    return by_name(
        sorted(in_scope, key=lambda q: scope.index(q.get("project_id"))), NEXT_UP, "query"
    )


def assemble(
    url: str, versions: list[dict], issues: list[Issue], next_ids: Set[int], a: argparse.Namespace
) -> Plan:
    """Next up picks the issues (open, Icebox left out); the report orders them."""
    still_open = open_by_id(issues)
    order = [v["name"] for v in version_order(versions)]
    picked = [i for i in issues if i.id in next_ids]
    upcoming = plan_order(picked, order, still_open)[: a.n_next]
    recent = last_closed(issues, a.n_closed)
    milestones = build(versions, issues, upcoming + recent, a.all)
    return Plan(url, upcoming, recent, milestones, still_open.keys())


def read_plan(r: Redmine, a: argparse.Namespace) -> Plan:
    statuses = read(r, "/issue_statuses.json", "issue_statuses")
    closed_ids = {s["id"] for s in statuses if s.get("is_closed")}
    project = read(r, f"/projects/{a.project}.json", "project")
    versions = read(r, f"/projects/{a.project}/versions.json", "versions")
    query = next_up_query(every(r, "/queries.json", "queries"), project)
    project_issues = f"/issues.json?project_id={a.project}"
    raw = every(r, f"{project_issues}&status_id=*&include=relations&sort=id", "issues")
    next_up = every(r, f"{project_issues}&query_id={query['id']}", "issues")
    issues = [parse(i, closed_ids) for i in raw]
    return assemble(r.url, versions, issues, {i["id"] for i in next_up}, a)


def repo_root() -> Path:
    run = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if run.returncode != 0:
        raise Refused(f"{Path.cwd()} isn't inside a git repo")
    return Path(run.stdout.strip())


def write_atomically(path: Path, text: str) -> None:
    """Renamed in, so a window reading the file, or a second run, never meets half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    aside = path.with_name(f".{path.name}.{os.getpid()}")
    aside.write_text(text, encoding="utf-8")
    os.replace(aside, path)


def rerun_options(a: argparse.Namespace) -> list[str]:
    options = ["--next", str(a.n_next), "--closed", str(a.n_closed), "--project", a.project]
    return [*options, "--all"] if a.all else options


def options_file(page: Path) -> Path:
    """One per page, so a page written with --out leaves the default page's options alone."""
    return page.with_suffix(".options.json")


def saved_options(page: Path) -> list[str]:
    """The options the page was written with; none for a page from before they were kept."""
    try:
        return json.loads(options_file(page).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def report(a: argparse.Namespace) -> None:
    out = a.out or repo_root() / PAGE
    r = Redmine()
    plan = read_plan(r, a)
    write_atomically(options_file(out), json.dumps(rerun_options(a)))
    write_atomically(out, render_html(plan, datetime.now()))
    print(render_text(plan.upcoming, plan.recent, plan.milestones, plan.open_ids))
    print(f"wrote {out}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--next", type=int, default=5, dest="n_next")
    p.add_argument("--closed", type=int, default=5, dest="n_closed")
    p.add_argument("--all", action="store_true", help="also show closed milestones")
    p.add_argument("--project", default=PROJECT, help="Redmine project (default: %(default)s)")
    p.add_argument("--out", type=Path, help=f"default: {PAGE} in the repo")
    a = p.parse_args(argv)

    try:
        report(a)
    except (Refused, OSError, ValueError) as e:
        sys.exit(f"report-milestones: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
