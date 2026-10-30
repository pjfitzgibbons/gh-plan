#!/usr/bin/env python3
"""Record an issue's review rounds, their findings and their answers in Redmine (design 0005, §3.3).

    findings.py send N [--outcomes]     post the round files Redmine doesn't hold yet
    findings.py reviewed N COMMIT       exit 0 if a round file or a Redmine round names COMMIT,
                                        1 if neither does, 2 if Redmine can't tell
    findings.py add-missed N M FILE LINES RULE --summary S --severity S --visibility V [--body B]
                                        add a missed finding of issue M to .tmp/N/0-findings-M.md
    findings.py lines FILE              rewrite FILE's lines from its JSON block

A round's files sit in `.tmp/N/`, named by its number k: `<k>-findings.md`, which review.py
writes, and `<k>-outcomes.md`, a person's answers to it. `0-findings-<m>.md` and
`0-outcomes-<m>.md` hold missed findings, round 0, of issue m. Each file is a line per finding,
written from the fenced `json` block below them, which is the POST's body:

    vscode/done.js:214-219:ignored-file-overwrite · New · silent/files — The fast-forward overwrites an ignored file
    vscode/github.js:88:repo-rename · Intentional · seen/nothing — a repo renamed on GitHub is refused

`send` posts the files in round order, each round's findings before its outcomes, to
`POST /issues/<n>/review_findings.json`, and only `pr` passes --outcomes. It checks each file's
lines against its block first, so a file edited on one side only stops the send, naming the line.
A file Redmine already holds is skipped. Redmine skips a row it has and refuses, saving nothing, a
row it has with other values; a refusal or an unreachable Redmine stops the send there, keeps
every file, and exits 1. After the posts it prints each issue's open-to-fix count. It deletes
nothing: Done removes `.tmp/N/` once Redmine holds every file.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from redmine import Redmine, Refused

OUTCOMES = {
    "new": "New",
    "fixed": "Fixed",
    "intentional": "Intentional",
    "wrong": "Wrong",
    "doesnt-matter": "Doesnt-matter",
}
SEVERITIES = ["work", "files", "commits", "tracker", "nothing"]
VISIBILITIES = ["silent", "seen"]
RULE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME = re.compile(r"^(?P<k>\d+)-(?P<kind>findings|outcomes)(?:-(?P<m>\d+))?\.md$")


class Stop(Exception):
    """A send that can't go on, with what to say; nothing after it was sent."""


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace", check=True
    ).stdout


def key(row: dict) -> str:
    """`file:lines:rule`, with one number when the finding is one line."""
    start, end = row["line_start"], row["line_end"]
    lines = str(start) if start == end else f"{start}-{end}"
    return f"{row['file']}:{lines}:{row['rule_id']}"


def to_fix(row: dict) -> bool:
    """A finding to fix (§3.2): silent and losing something, or a crash."""
    return (row.get("visibility") == "silent" and row.get("severity") != "nothing") or row.get(
        "rule_id"
    ) == "crash"


def indented(text: str) -> list[str]:
    """text's first line, then each further line indented under it, so no line of it can be read
    as a finding's or as the fence."""
    first, *rest = (text or "").strip().splitlines() or [""]
    return [first.strip(), *[f"  {line.strip()}" for line in rest]]


def lines(data: dict) -> str:
    """The lines a person reads: the round, then a line per finding, in the block's order."""
    out = []
    if r := data.get("review_round"):
        ci = r.get("ci") or "none"
        said = f" ({r['ci_summary']})" if r.get("ci_summary") else ""
        out.append(f"Round {r['round']} · {r['commit']} · ci {ci}{said}")
        if r.get("summary"):
            out += [f"  {line}" for line in indented(r["summary"])]
        out.append("")
    for row in data.get("review_findings", []):
        weight = f"{row.get('visibility') or '-'}/{row.get('severity')}"
        line = f"{key(row)} · {OUTCOMES.get(row.get('outcome'), row.get('outcome'))} · {weight}"
        text = row.get("justification") if row.get("source") == "outcomes" else row.get("summary")
        if text and text.strip():
            first, *rest = indented(text)
            out += [f"{line} — {first}", *rest]
        else:
            out.append(line)
    return "\n".join(out).rstrip("\n") + "\n"


def fence(text: str) -> str:
    """A fence longer than any backtick run in text, so a summary can't close it."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def replace_text(path: Path, text: str) -> None:
    """Written aside and renamed in, so a reader never sees half a file, or an empty one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f".{path.name}.part")
    part.write_text(text)
    part.replace(path)


def write(path: Path, data: dict) -> None:
    """A round file: its lines, then its block."""
    block = json.dumps(data, indent=2, ensure_ascii=False)
    f = fence(block)
    replace_text(path, f"{lines(data)}\n{f}json\n{block}\n{f}\n")


def block(path: Path) -> tuple[list[str], dict]:
    """A round file's lines, and its block; Stop when it has no block that parses."""
    text = path.read_text().splitlines()
    end = next((i for i in range(len(text) - 1, -1, -1) if text[i].strip()), None)
    if end is None or not re.fullmatch(r"`{3,}", text[end]):
        raise Stop(f"{path.name}: no fenced json block at its end")
    opening = f"{text[end]}json"
    start = next((i for i in range(end - 1, -1, -1) if text[i] == opening), None)
    if start is None:
        raise Stop(f"{path.name}: no {opening} line opening its block")
    try:
        data = json.loads("\n".join(text[start + 1 : end]))
    except ValueError as e:
        raise Stop(f"{path.name}: its json block doesn't parse: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("review_findings", []), list):
        raise Stop(f"{path.name}: its json block isn't a POST body with review_findings")
    shown = text[:start]
    while shown and not shown[-1].strip():
        shown.pop()
    return shown, data


def read(path: Path) -> dict:
    """A round file's block, once its lines match it and every row is in the file's round; Stop
    naming the first line that doesn't."""
    shown, data = block(path)
    try:
        want = lines(data).rstrip("\n").splitlines()
    except (KeyError, TypeError, AttributeError) as e:
        raise Stop(f"{path.name}: a row in its block lacks {e}") from None
    for i, (a, b) in enumerate(zip(shown + [None], want + [None], strict=False)):
        if a != b:
            should = "end there" if b is None else f"read {b!r}"
            raise Stop(
                f"{path.name}: line {i + 1} doesn't match its json block, which says it should"
                f" {should}; edit both, or edit the block and run findings.py lines on the file"
            )
    found = NAME.match(path.name)
    if not found:
        raise Stop(f"{path.name}: not a round file's name, <k>-findings.md or <k>-outcomes.md")
    k, kind = int(found["k"]), found["kind"]
    source = "review" if kind == "findings" else "outcomes"
    for i, row in enumerate(data["review_findings"], 1):
        if not isinstance(row, dict) or row.get("round") != k or row.get("source") != source:
            raise Stop(f"{path.name}: row {i} isn't a round {k} {source} row")
    r = data.get("review_round")
    if r is not None and (kind != "findings" or k == 0 or r.get("round") != k):
        raise Stop(f"{path.name}: its review_round isn't round {k}")
    return data


class Issue:
    """One issue's round files and its Redmine ledger."""

    def __init__(self, n: int, root: Path, redmine=None):
        self.n, self.root = n, root
        self.redmine = redmine
        self.folder = root / ".tmp" / str(n)

    def tracker(self):
        if self.redmine is None:
            self.redmine = Redmine()
        return self.redmine

    def ledger(self, n: int | None = None) -> dict:
        """The GET of issue n's ledger, this issue's by default."""
        n = n or self.n
        path = f"/issues/{n}/review_findings.json"
        got = self.tracker().send("GET", path)
        lists = ("review_rounds", "review_findings", "findings")
        if (
            not isinstance(got, dict)
            or not isinstance(got.get("last_round"), int)
            or not all(isinstance(got.get(k), list) for k in lists)
        ):
            raise Refused(
                f"Redmine GET {path}: no ledger in its answer; is the review_findings plugin up"
                " to date?"
            )
        return got

    def files(self, outcomes: bool = True) -> list[tuple[Path, int]]:
        """The round files, in the order they're sent, each with the issue it's for."""
        found = []
        for path in self.folder.glob("*.md") if self.folder.is_dir() else []:
            name = NAME.match(path.name)
            if not name or (name["kind"] == "outcomes" and not outcomes):
                continue
            k = int(name["k"])
            if (k == 0) != bool(name["m"]):
                continue
            issue = int(name["m"]) if name["m"] else self.n
            found.append(((k, name["kind"] == "outcomes", issue), path, issue))
        return [(path, issue) for _, path, issue in sorted(found)]

    def held(self, data: dict, ledger: dict) -> bool:
        """Whether the ledger has the file's round and every row, with the same values."""
        if r := data.get("review_round"):
            have = next((x for x in ledger["review_rounds"] if x.get("round") == r["round"]), None)
            if not have or any(same(v) != same(have.get(k)) for k, v in r.items()):
                return False
        rows = {
            (x.get("key"), x.get("round"), x.get("source")): x for x in ledger["review_findings"]
        }
        for row in data["review_findings"]:
            have = rows.get((key(row), row["round"], row["source"]))
            if not have or any(same(v) != same(have.get(k)) for k, v in row.items()):
                return False
        return True

    def send(self, outcomes: bool = False) -> list[str]:
        """Posts each round file Redmine doesn't hold; what it did."""
        said, ledgers = [], {}
        for path, issue in self.files(outcomes):
            data = read(path)
            if issue not in ledgers:
                ledgers[issue] = self.ledger(issue)
            if self.held(data, ledgers[issue]):
                continue
            try:
                got = self.tracker().send("POST", f"/issues/{issue}/review_findings.json", data)
            except Refused as e:
                raise Stop(f"{e}; kept {self.shown(path)} to send again") from None
            got = got if isinstance(got, dict) else {}
            ledgers[issue] = self.ledger(issue)
            if not self.held(data, ledgers[issue]):
                raise Stop(
                    f"Redmine doesn't hold all of {self.shown(path)} after the POST; kept it"
                )
            added = got.get("created", 0)
            said.append(
                f"recorded {self.shown(path)}: {added} row{'' if added == 1 else 's'} added"
                + (f", {got['skipped']} already there" if got.get("skipped") else "")
                + (f", round {data['review_round']['round']}" if data.get("review_round") else "")
            )
        ledgers.setdefault(self.n, None)
        for issue in sorted(ledgers):
            said.append(open_line(issue, ledgers[issue] or self.ledger(issue)))
        return said

    def shown(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def local_rounds(self) -> list[dict]:
        """The review_round of each `<k>-findings.md`, as far as its block reads."""
        out = []
        for path, issue in self.files(outcomes=False):
            if issue != self.n:
                continue
            try:
                r = block(path)[1].get("review_round")
            except (Stop, OSError):
                continue
            if isinstance(r, dict):
                out.append(r)
        return out

    def next_round(self, ledger: dict) -> int:
        """The round after the last one, in Redmine or in a findings file not yet sent."""
        local = [k for r in self.local_rounds() if isinstance(k := r.get("round"), int)]
        return max([ledger["last_round"], *local]) + 1

    def reviewed(self, commit: str, ledger: dict | None = None) -> bool:
        """Whether a findings file or a Redmine round names commit; Refused if Redmine can't tell."""
        if any(r.get("commit") == commit for r in self.local_rounds()):
            return True
        ledger = ledger or self.ledger()
        return any(r.get("commit") == commit for r in ledger["review_rounds"])

    def add_missed(self, m: int, row: dict) -> Path:
        """Adds a missed finding of issue m to `0-findings-<m>.md`; the file."""
        path = self.folder / f"0-findings-{m}.md"
        data = read(path) if path.exists() else {"review_findings": []}
        if any(key(r) == key(row) for r in data["review_findings"]):
            raise Stop(f"{self.shown(path)} already has {key(row)}")
        data["review_findings"].append(row)
        write(path, data)
        return path


def same(v):
    """A value as Redmine keeps it: an empty string is NULL."""
    return None if isinstance(v, str) and not v.strip() else v


def open_line(n: int, ledger: dict) -> str:
    last = ledger["last_round"]
    where = f"round {last}" if last else "no round yet"
    unchecked = ledger.get("fixed_unchecked") or 0
    return f"#{n}: {where}, {ledger.get('open_to_fix', 0)} open to fix" + (
        f", {unchecked} fixed and not yet checked" if unchecked else ""
    )


def lines_of(text: str) -> tuple[int, int]:
    found = re.fullmatch(r"(\d+)(?:-(\d+))?", text)
    if not found:
        raise argparse.ArgumentTypeError(f"{text!r} isn't a line or a range, as 12 or 12-14")
    start, end = int(found[1]), int(found[2] or found[1])
    if start < 1 or end < start:
        raise argparse.ArgumentTypeError(f"{text!r} isn't a range from line 1 on")
    return start, end


def rule_of(text: str) -> str:
    if not RULE.match(text):
        raise argparse.ArgumentTypeError(f"{text!r} isn't a kebab-case rule")
    return text


def root_of() -> Path:
    return Path(git("rev-parse", "--show-toplevel").strip())


def send(n: int, root: Path | None = None, outcomes: bool = False, redmine=None) -> int:
    """findings.py send, as review.py and main call it: prints what it did; its exit code."""
    issue = Issue(n, root or root_of(), redmine)
    try:
        said = issue.send(outcomes)
    except (Stop, Refused, OSError) as e:
        print(f"findings: {e}", file=sys.stderr)
        return 1
    for line in said:
        print(f"findings: {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("send", help="post the round files Redmine doesn't hold yet")
    s.add_argument("n", type=int)
    s.add_argument("--outcomes", action="store_true", help="the outcomes files too, as pr sends")
    r = sub.add_parser("reviewed", help="whether a round file or a Redmine round names COMMIT")
    r.add_argument("n", type=int)
    r.add_argument("commit")
    m = sub.add_parser("add-missed", help="add a missed finding of issue M, as round 0")
    m.add_argument("n", type=int, help="the issue whose work found it")
    m.add_argument("m", type=int, help="the issue it escaped")
    m.add_argument("file")
    m.add_argument("lines", type=lines_of, help="a line, or a range as 12-14")
    m.add_argument("rule", type=rule_of)
    m.add_argument("--summary", required=True)
    m.add_argument("--severity", required=True, choices=SEVERITIES)
    m.add_argument("--visibility", choices=VISIBILITIES)
    m.add_argument("--body", default="")
    w = sub.add_parser("lines", help="rewrite a round file's lines from its json block")
    w.add_argument("file", type=Path)
    a = p.parse_args(argv)
    if a.command == "send":
        return send(a.n, outcomes=a.outcomes)
    if a.command == "lines":
        try:
            write(a.file, block(a.file)[1])
        except (Stop, OSError, KeyError, TypeError) as e:
            print(f"findings: {a.file}: {e}", file=sys.stderr)
            return 1
        return 0
    if a.command == "add-missed":
        if not a.visibility and a.severity != "nothing":
            p.error("--visibility is needed unless --severity is nothing")
        row = {
            "file": a.file,
            "line_start": a.lines[0],
            "line_end": a.lines[1],
            "rule_id": a.rule,
            "round": 0,
            "source": "review",
            "outcome": "new",
            "tag": "Important",
            "severity": a.severity,
            "visibility": a.visibility,
            "summary": a.summary[:255],
            "body": a.body,
        }
        issue = Issue(a.n, root_of())
        try:
            path = issue.add_missed(a.m, row)
        except (Stop, OSError) as e:
            print(f"findings: {e}", file=sys.stderr)
            return 1
        print(f"findings: added {key(row)} to {issue.shown(path)}")
        return 0
    try:
        return 0 if Issue(a.n, root_of()).reviewed(a.commit) else 1
    except (Refused, OSError) as e:
        print(
            f"findings: Redmine can't say whether {a.commit[:7]} was reviewed: {e}", file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
