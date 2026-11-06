#!/usr/bin/env python3
"""Progress per milestone (Redmine version), with the next issues and the last ones closed.

    milestones.py                          # open versions; writes .tmp/milestones/progress.html
    milestones.py --all --next 8 --closed 8 --out report.html

Every issue in a milestone is listed and counted, whatever its status: closed and rejected issues
stay in the list, marked. Progress is closed issues (Rejected included, as Redmine closes it) over
all issues, shown as a bar, a count and a percentage, with points beside it.

The report opens with two plain lists, each issue keyed by its milestone: the first N open issues
of Redmine's Next up query in plan order, each with the open issues that block it, and the N most
recently closed. Below them each milestone
lists all its issues as #<n>(<points>), coloured by status, with no other call-out: its open issues
in plan order, then its closed ones in the order they closed.

Plan order: milestone (in version order, no milestone last), then In Progress before the rest, then
Ordinal, then priority (highest first), then issue number; and an issue never comes before an open
issue that blocks it, whatever its milestone. A closed milestone is
shown when it holds one of those issues. Reads Redmine through the gh-issues skill's client; writes only the HTML file.
"""

import argparse
import html
import sys
from collections.abc import Sequence, Set
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gh-issues/scripts"))

PROJECT = "plan"
NEXT_UP = "Next up"
NONE = "No milestone"


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
        return "active" if self.status in ("In Progress", "Resolved", "Feedback") else "open"


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
        return done, total, sum(i.points is None for i in self.issues)


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


def plan_order(issues: list[Issue], versions: list[str]) -> list[Issue]:
    """Open issues in plan order: milestone (in the order of versions, no milestone last), In
    Progress first, Ordinal, priority, issue number; and never before an open issue that blocks it.
    A blocking cycle is broken at its first issue by that key."""
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
    out = []
    while left:
        ready = [i for i in left.values() if not i.blockers & left.keys()] or list(left.values())
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
        for v in sorted(versions, key=lambda v: v["name"])
    }
    for i in issues:
        if i.version in ms:
            ms[i.version].issues.append(i)
    wanted = {n for n, m in ms.items() if show_closed or m.status != "closed"} | {
        i.version for i in highlighted
    }
    for m in ms.values():
        done = sorted((i for i in m.issues if i.closed), key=lambda i: (i.closed_on or "", i.id))
        m.issues = plan_order(m.issues, list(ms)) + done
    return [m for n, m in ms.items() if n in wanted]


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


def render_html(
    upcoming: list[Issue],
    recent: list[Issue],
    milestones: list[Milestone],
    base: str,
    when: datetime,
    open_ids: Set[int] = frozenset(),
) -> str:
    e = html.escape

    def row(n: int, i: Issue, when: str = "", blockers: Sequence[int] = ()) -> str:
        pts = f"{i.points}pt" if i.points is not None else "–pt"
        links = " ".join(f'<a href="{e(base)}/issues/{b}">#{b}</a>' for b in blockers)
        return (
            f'<li class="{i.kind}"><span class=place>{n}</span>'
            f'<span class=id><a href="{e(base)}/issues/{i.id}">#{i.id}</a></span>'
            f'<span class=ms>{e(i.version or NONE)}</span><span class="st {i.kind}">{e(i.status)}</span>'
            f"{f'<span class=when>{e(when)}</span>' if when else ''}<span class=p>{pts}</span>"
            f"<span class=subj>{e(i.subject)}</span>"
            f"{f'<span class=blk>blocked by {links}</span>' if blockers else ''}</li>"
        )

    top = (
        f"<section><h3>Next {len(upcoming)}</h3><ul>"
        + "".join(
            row(n, i, blockers=sorted(i.blockers & open_ids)) for n, i in enumerate(upcoming, 1)
        )
        + f"</ul><h3>Last {len(recent)} closed</h3><ul>"
        + "".join(row(n, i, (i.closed_on or "")[:10]) for n, i in enumerate(recent, 1))
        + "</ul></section>"
    )
    parts = []
    for m in milestones:
        tag = "closed" if m.status == "closed" else (f"due {m.due}" if m.due else "")
        s = [f"<section><h2>{e(m.name)}{f'<span class=tag>{e(tag)}</span>' if tag else ''}</h2>"]
        if m.description:
            s.append(f"<p class=exit>{e(m.description)}</p>")
        total = len(m.issues) or 1
        segs = "".join(
            f'<span style="width:{100 * m.count(k) / total:.2f}%;background:var(--{k})" '
            f'title="{m.count(k)} {k}"></span>'
            for k in ("closed", "rejected", "active")
        )
        done, pts, unpointed = m.points
        extra = f" · {unpointed} unpointed" if unpointed else ""
        s.append(
            f'<div class=prog><div class=bar role=img aria-label="{m.percent}% closed">{segs}</div>'
            f"<span class=nums><span class=pct>{m.percent}%</span>{m.done} / {len(m.issues)} closed"
            f"<br><span class=pts>{m.count('active')} in progress · {m.count('rejected')} rejected · "
            f"{done} / {pts} points{extra}</span></span></div>"
        )
        s.append(
            "<p class=chips>"
            + " ".join(
                f'<a class="chip {i.kind}" href="{e(base)}/issues/{i.id}" title="{e(i.status)}: {e(i.subject)}">'
                f"{chip(i)}</a>"
                for i in m.issues
            )
            + "</p></section>"
        )
        parts.append("".join(s))
    legend = "".join(
        f'<span><span class=sw style="background:var(--{k})"></span>{k.replace("active", "in progress")}</span>'
        for k in ("closed", "rejected", "active", "open")
    )
    return (
        f'<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,'
        f'initial-scale=1"><title>Milestone progress</title><style>{CSS}</style></head><body><main>'
        f"<h1>Milestone progress</h1><p class=sub>{e(base)} · {when:%a %b %d %H:%M}"
        f" <button id=regen class=regen>Regenerate</button><span id=regen-said></span></p>"
        f"<div class=legend>{legend}</div>{top}{''.join(parts)}</main>{REGEN}</body></html>"
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--next", type=int, default=5, dest="n_next")
    p.add_argument("--closed", type=int, default=5, dest="n_closed")
    p.add_argument("--all", action="store_true", help="also show closed milestones")
    p.add_argument("--out", type=Path, default=Path(".tmp/milestones/progress.html"))
    a = p.parse_args(argv)

    from redmine import Redmine

    r = Redmine()

    def every(query: str) -> list[dict]:
        out, offset = [], 0
        while True:
            page = r.send(
                "GET",
                f"/issues.json?project_id={PROJECT}&{query}&include=relations"
                f"&limit=100&offset={offset}",
            )
            out += page["issues"]
            offset += 100
            if offset >= page["total_count"]:
                return out

    closed_ids = {
        s["id"]
        for s in r.send("GET", "/issue_statuses.json")["issue_statuses"]
        if s.get("is_closed")
    }
    versions = r.send("GET", f"/projects/{PROJECT}/versions.json")["versions"]
    query = next(
        (q["id"] for q in r.send("GET", "/queries.json")["queries"] if q["name"] == NEXT_UP), None
    )
    if query is None:
        sys.exit(f"no Redmine query named {NEXT_UP!r}")
    issues = [parse(i, closed_ids) for i in every("status_id=*&fixed_version_id=*")]
    open_ids = {
        i["id"] for i in every("status_id=open")
    }  # a blocker may have no milestone, or be iceboxed
    # The whole of Next up, which picks the issues (open, Icebox left out); the report orders them.
    order = [v["name"] for v in sorted(versions, key=lambda v: v["name"])]
    upcoming = plan_order([parse(i, closed_ids) for i in every(f"query_id={query}")], order)
    recent = [
        parse(i, closed_ids)
        for i in r.send(
            "GET",
            f"/issues.json?project_id={PROJECT}&status_id=closed&sort=closed_on:desc"
            f"&limit={a.n_closed}",
        )["issues"]
    ]

    upcoming, recent = upcoming[: a.n_next], recent[: a.n_closed]
    milestones = build(versions, issues, upcoming + recent, a.all)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(render_html(upcoming, recent, milestones, r.url, datetime.now(), open_ids))
    print(render_text(upcoming, recent, milestones, open_ids))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
