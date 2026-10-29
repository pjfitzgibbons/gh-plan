#!/usr/bin/env python3
"""Record an issue's review rounds and their outcome lines in Redmine (design 0005, §3.3).

    findings.py send N [--outcomes]     the review files, then outcomes.md with --outcomes
    findings.py reviewed N COMMIT       exit 0 if a review file or a Redmine round names COMMIT,
                                        1 if neither does, 2 if Redmine can't tell

`send` sends each review file `.tmp/N/review/<commit>.json` that review.py wrote, in the branch's
commit order. A review whose `ci` is success becomes the issue's next round, numbered from 1; one
whose `ci` isn't is recorded with no number and no rows. Each finding gets its fingerprint at its
commit (§3.1), and the no-repeat rule (§3.5) drops a repeat of a finding still open or already
answered. A `gone` verdict on an earlier `fixed` finding sets that row's `verified_round`; a
`still-there` one records it again, open. The round and its rows go in one PUT, are read back,
and the review file is deleted only once they match.

--outcomes, as pr runs it after its push, also sends `.tmp/N/findings/outcomes.md`, the outcome
lines a person accepted at Go (§3.4):

    finding/v1:3f9c0a7e5b21d4c8 · Fixed · ignored-file-overwrite · silent/files
    finding/v1:8b02d1f4c6e9a357 @2 · Wrong · repo-rename · -/nothing — it reads the name once

Every line is checked, and its row found, before anything is sent: a bad line stops the send with
nothing recorded. `@<round>` names an answered row; without it the line answers the fingerprint's
one open row. outcomes.md is deleted once every line reads back confirmed.

After a send it prints the issue's open-to-fix count, over Redmine and whatever is still waiting
to be sent. A file Redmine didn't take stays where it is, the send exits 1 naming it, and the next
send tries again; sending one twice records the same rows.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from redmine import Redmine, Refused

OUTCOMES = {
    "Fixed": "fixed",
    "Intentional": "intentional",
    "Wrong": "wrong",
    "Doesnt-matter": "doesnt-matter",
}
SEVERITIES = ["work", "files", "commits", "tracker", "nothing"]
RULE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FINGERPRINT = re.compile(r"^finding/v1:[0-9a-f]{16}$")
# A round line's fields from the review, beside commit, fingerprint and round; never an outcome
# or a confirmation, which only outcomes.md sends.
FIELDS = ["file", "line", "rule_id", "tag", "severity", "visibility", "summary", "body"]
RECORDED = ["commit", "base", "effort", "review_md", "summary", "controls", "dropped", "new_rules"]
# One outcome line: the fingerprint, an optional @round, then the outcome, the rule and the
# weighting, each after a middle dot, and a justification after a dash.
LINE = re.compile(
    r"^(?P<fp>\S+)(?:\s+@(?P<round>\d+))?\s+·\s+(?P<outcome>\S+)\s+·\s+(?P<rule>\S+)"
    r"\s+·\s+(?P<visibility>[^/\s]+)/(?P<severity>\S+)(?:\s+—\s*(?P<why>.*))?$"
)


class Stop(Exception):
    """A send that can't go on, with what to say; nothing after it was sent."""


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace", check=True
    ).stdout


def stripped(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def fingerprint(file: str, line: int | None, summary: str, text: str | None) -> tuple[str, bool]:
    """The finding's fingerprint (§3.1), and whether its snippet appears more than once in the
    file, so it's never matched as a repeat. text is the file at the round's commit. A finding with
    no file hashes `-` and its summary; one whose line can't be found in its file, the path and its
    summary."""
    said = "\n".join(stripped(summary))
    if not file:
        hashed, many = f"-\n{said}", False
    else:
        lines = stripped(text) if text is not None else []
        if not line or line > len(lines):
            hashed, many = f"{file}\n{said}", False
        else:

            def window(i: int) -> str:
                return "\n".join(lines[max(0, i - 1) : i + 2])

            snippet = window(line - 1)
            same = [i for i in range(len(lines)) if window(i) == snippet]
            hashed = f"{file}\n{snippet}\n{same.index(line - 1) + 1}"
            many = len(same) > 1
    return "finding/v1:" + hashlib.sha256(hashed.encode()).hexdigest()[:16], many


def to_fix(row: dict) -> bool:
    """A finding to fix (§3.2): silent and losing something, or a crash."""
    return (row.get("visibility") == "silent" and row.get("severity") != "nothing") or row.get(
        "rule_id"
    ) == "crash"


def weight(row: dict) -> tuple[bool, bool, bool]:
    """Heavier first: silent and losing something, then losing anything, then silent."""
    loses = row.get("severity") != "nothing"
    silent = row.get("visibility") == "silent"
    return (silent and loses, loses, silent)


def latest(rows: list[dict]) -> dict[str, dict]:
    """Each fingerprint's row in its latest round."""
    found = {}
    for row in sorted(rows, key=lambda r: r["round"]):
        found[row["fingerprint"]] = row
    return found


class Issue:
    """One issue's review files, outcomes.md and Redmine store."""

    def __init__(self, n: int, root: Path, redmine=None):
        self.n, self.root = n, root
        self.redmine = redmine
        self.folder = root / ".tmp" / str(n) / "review"
        self.outcomes = root / ".tmp" / str(n) / "findings" / "outcomes.md"

    def tracker(self):
        if self.redmine is None:
            self.redmine = Redmine()
        return self.redmine

    def store(self) -> tuple[list[dict], list[dict]]:
        got = self.tracker().send("GET", f"/issues/{self.n}/review_findings.json") or {}
        rows, rounds = got.get("review_findings"), got.get("review_rounds")
        if not isinstance(rows, list) or not isinstance(rounds, list):
            raise Refused(
                f"Redmine GET /issues/{self.n}/review_findings.json: no review_findings and "
                "review_rounds in its answer; is the review_findings plugin up to date?"
            )
        return rows, rounds

    def reviews(self) -> list[tuple[Path, dict]]:
        """The review files waiting to be sent, in the branch's commit order; a commit no longer
        on the branch, as after a rebase, by its commit time."""
        files = []
        for path in sorted(self.folder.glob("*.json")):
            if not re.fullmatch(r"[0-9a-f]{40}\.json", path.name):
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError) as e:
                raise Stop(f"{self.shown(path)} can't be read: {e}") from None
            if not isinstance(data, dict) or data.get("commit") != path.stem:
                raise Stop(f"{self.shown(path)} isn't a review of commit {path.stem}")
            files.append((path, data))
        if not files:
            return files
        try:
            branch = git("rev-list", "--reverse", "HEAD", cwd=self.root).split()
        except subprocess.CalledProcessError:
            branch = []
        order = {c: i for i, c in enumerate(branch)}

        def when(commit: str) -> int:
            try:
                return int(git("log", "-1", "--format=%ct", commit, cwd=self.root).strip() or 0)
            except subprocess.CalledProcessError:
                return 0

        return sorted(files, key=lambda f: (order.get(f[0].stem, len(order)), when(f[0].stem)))

    def shown(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def at(self, commit: str, file: str) -> str | None:
        try:
            return git("show", f"{commit}:{file}", cwd=self.root)
        except subprocess.CalledProcessError:
            return None

    def plan(self, data: dict, rows: list[dict], rounds: list[dict]) -> dict:
        """The PUT body that records one review: its round, numbered after the issue's latest, or
        the number Redmine already gave its commit, and its rows after the no-repeat rule."""
        commit = data["commit"]
        ci = data.get("ci") or {}
        state = ci.get("state") if isinstance(ci, dict) else None
        if state not in ("success", "failure", "none"):
            raise Stop(f"the review of {commit[:7]} has no ci state")
        known = next((r for r in rounds if r.get("commit") == commit), None)
        if state != "success":
            k = None
        elif known and known.get("round"):
            k = known["round"]
        else:
            k = (last_round(rows, rounds) or 0) + 1
        round_ = {f: data.get(f) for f in RECORDED}
        round_.update(round=k, ci=state, ci_summary=str(ci.get("summary") or "")[:255])
        body = {"review_round": round_}
        if k is None:
            return body
        earlier = latest([r for r in rows if 0 < r["round"] < k])
        new = {}
        for f in data.get("findings") or []:
            row = {key: f.get(key) for key in FIELDS}
            row["commit"] = commit
            text = self.at(commit, f["file"]) if f.get("file") else None
            fp, many = fingerprint(f.get("file") or "", f.get("line"), f.get("summary") or "", text)
            row.update(fingerprint=fp, round=k)
            before = earlier.get(fp)
            if many:
                if before:
                    row["body"] = (row.get("body") or "") + (
                        f"\n\nIts snippet appears more than once in {f['file']}, so it may repeat"
                        f" {fp} from round {before['round']}."
                    )
            elif before and not self.again(before, row):
                continue
            elif before and before.get("outcome") not in (None, "fixed"):
                row["body"] = (row.get("body") or "") + (
                    f"\n\nRound {before['round']} answered it {before['outcome']}"
                    f"{': ' + before['justification'] if before.get('justification') else ''};"
                    " this weighs more."
                )
            if fp in new:
                row = merged(new[fp], row)
            new[fp] = row
        lines = list(new.values())
        verdicts = data.get("verdicts") or []
        for v in verdicts:
            before = earlier.get(v.get("fingerprint"))
            if not before or before.get("outcome") != "fixed" or before.get("verified_round"):
                continue
            if v.get("verdict") == "gone":
                lines.append(
                    {
                        "fingerprint": before["fingerprint"],
                        "round": before["round"],
                        "verified_round": k,
                    }
                )
            elif v.get("verdict") == "still-there" and before["fingerprint"] not in new:
                row = {key: before.get(key) for key in FIELDS}
                row.update(commit=commit, fingerprint=before["fingerprint"], round=k)
                row["body"] = (row.get("body") or "") + (
                    f"\n\nRound {k} found it still there after round {before['round']}'s fix."
                )
                lines.append(row)
        body["review_findings"] = lines
        return body

    @staticmethod
    def again(before: dict, row: dict) -> bool:
        """The no-repeat rule (§3.5): whether a new finding is recorded when its fingerprint's
        latest row is `before`."""
        if before.get("outcome") is None:
            return False
        if before["outcome"] == "fixed":
            return True
        return weight(row)[0] and not weight(before)[0]

    def read_back(self, body: dict, rows: list[dict], rounds: list[dict]) -> list[str]:
        """What of body Redmine doesn't hold; empty when it all reads back."""
        sent = body["review_round"]
        missing = []
        held = next((r for r in rounds if r.get("commit") == sent["commit"]), None)
        if not held or held.get("round") != sent["round"]:
            missing.append(f"round {sent['round']} for {sent['commit'][:7]}")
        keyed = {(r["fingerprint"], r["round"]): r for r in rows}
        for line in body.get("review_findings", []):
            row = keyed.get((line["fingerprint"], line["round"]))
            if not row:
                missing.append(f"{line['fingerprint']} in round {line['round']}")
            elif "verified_round" in line and row.get("verified_round") != line["verified_round"]:
                missing.append(f"{line['fingerprint']}'s verified_round {line['verified_round']}")
        return missing

    def send_reviews(self) -> tuple[list[dict], list[dict], list[str]]:
        """Sends the review files in order; the store afterwards, and what was said."""
        said = []
        waiting = self.reviews()
        try:
            rows, rounds = self.store()
        except Refused as e:
            if not waiting:
                raise
            left = ", ".join(self.shown(p) for p, _ in waiting)
            raise Stop(f"{e}; kept {left} to send again") from None
        for i, (path, data) in enumerate(waiting):
            body = self.plan(data, rows, rounds)
            try:
                self.tracker().send("PUT", f"/issues/{self.n}/review_findings.json", body)
                rows, rounds = self.store()
            except Refused as e:
                left = ", ".join(self.shown(p) for p, _ in waiting[i:])
                raise Stop(f"{e}; kept {left} to send again") from None
            missing = self.read_back(body, rows, rounds)
            if missing:
                left = ", ".join(self.shown(p) for p, _ in waiting[i:])
                raise Stop(f"Redmine doesn't hold {', '.join(missing)} after the PUT; kept {left}")
            path.unlink()
            k = body["review_round"]["round"]
            count = len([r for r in body.get("review_findings", []) if "summary" in r])
            if k is None:
                said.append(
                    f"recorded the review of {data['commit'][:7]}, whose ci was"
                    f" {body['review_round']['ci']}, with no round; deleted {self.shown(path)}"
                )
            else:
                said.append(
                    f"recorded round {k} ({data['commit'][:7]}), {count}"
                    f" finding{'' if count == 1 else 's'}; deleted {self.shown(path)}"
                )
        return rows, rounds, said

    def parse_outcomes(self) -> list[dict]:
        """outcomes.md's lines, each checked; Stop names every bad one."""
        try:
            text = self.outcomes.read_text()
        except FileNotFoundError:
            return []
        lines, bad = [], []
        for number, raw in enumerate(text.splitlines(), 1):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if raw[:1].isspace() and lines:
                # A justification's further lines.
                lines[-1]["justification"] = (
                    lines[-1]["justification"] + "\n" + raw.strip()
                ).strip()
                continue
            m = LINE.match(raw.strip())
            if not m:
                bad.append(
                    f"line {number} isn't `<fingerprint> · <Outcome> · <rule> · <visibility>/<severity>`"
                )
                continue
            why = []
            if not FINGERPRINT.match(m["fp"]):
                why.append(f"{m['fp']!r} isn't a finding/v1: fingerprint")
            if m["outcome"] not in OUTCOMES:
                why.append(f"{m['outcome']!r} isn't one of {', '.join(OUTCOMES)}")
            if not RULE.match(m["rule"]):
                why.append(f"{m['rule']!r} isn't a kebab-case rule")
            if m["visibility"] not in ("silent", "seen", "-"):
                why.append(f"visibility {m['visibility']!r} isn't silent, seen or -")
            if m["severity"] not in SEVERITIES:
                why.append(f"severity {m['severity']!r} isn't one of {', '.join(SEVERITIES)}")
            if m["visibility"] == "-" and m["severity"] != "nothing":
                why.append(f"it loses {m['severity']} and has no visibility")
            if why:
                bad.append(f"line {number}: {'; '.join(why)}")
                continue
            lines.append(
                {
                    "number": number,
                    "fingerprint": m["fp"],
                    "at": int(m["round"]) if m["round"] else None,
                    "outcome": OUTCOMES[m["outcome"]],
                    "rule_id": m["rule"],
                    "visibility": "" if m["visibility"] == "-" else m["visibility"],
                    "severity": m["severity"],
                    "justification": (m["why"] or "").strip(),
                }
            )
        for line in lines:
            if line["outcome"] != "fixed" and not line["justification"]:
                bad.append(
                    f"line {line['number']}: {line['outcome']} needs a justification after —"
                )
        if bad:
            raise Stop(f"{self.shown(self.outcomes)}: " + "; ".join(bad))
        return lines

    def resolve(self, lines: list[dict], rows: list[dict]) -> list[dict]:
        """Each line's confirm line, its row found by fingerprint, or by @round; Stop names every
        line whose row isn't there, or is one of several open ones."""
        bad, out = [], []
        for line in lines:
            mine = [r for r in rows if r["fingerprint"] == line["fingerprint"]]
            if line["at"] is not None:
                found = [r for r in mine if r["round"] == line["at"]]
            else:
                found = [r for r in mine if r.get("outcome") is None]
            if len(found) != 1:
                where = f"in round {line['at']}" if line["at"] is not None else "open"
                bad.append(
                    f"line {line['number']}: {line['fingerprint']} has"
                    f" {'no row' if not found else str(len(found)) + ' rows'} {where}"
                )
                continue
            out.append(
                {
                    "confirm": True,
                    "fingerprint": line["fingerprint"],
                    "round": found[0]["round"],
                    **{k: line[k] for k in ("outcome", "justification", "rule_id", "severity")},
                    "visibility": line["visibility"],
                }
            )
        if bad:
            raise Stop(f"{self.shown(self.outcomes)}: " + "; ".join(bad) + "; nothing was recorded")
        return out

    def send(self, outcomes: bool = False) -> list[str]:
        """Sends the review files, then outcomes.md with outcomes; what was said."""
        lines = self.parse_outcomes() if outcomes else []
        if lines:
            # Every line must find its row, in Redmine or in a review waiting to go, before
            # anything is sent.
            self.resolve(lines, self.waiting(*self.store())[0])
        rows, rounds, said = self.send_reviews()
        if lines:
            confirm = self.resolve(lines, rows)
            try:
                self.tracker().send(
                    "PUT", f"/issues/{self.n}/review_findings.json", {"review_findings": confirm}
                )
                rows, rounds = self.store()
            except Refused as e:
                raise Stop(f"{e}; kept {self.shown(self.outcomes)} to send again") from None
            keyed = {(r["fingerprint"], r["round"]): r for r in rows}
            unread = [
                c["fingerprint"]
                for c in confirm
                if not (row := keyed.get((c["fingerprint"], c["round"])))
                or not row.get("confirmed_by_id")
                or row.get("outcome") != c["outcome"]
            ]
            if unread:
                raise Stop(
                    f"Redmine doesn't hold the outcomes of {', '.join(unread)};"
                    f" kept {self.shown(self.outcomes)}"
                )
            self.outcomes.unlink()
            said.append(
                f"recorded {len(confirm)} outcome{'' if len(confirm) == 1 else 's'};"
                f" deleted {self.shown(self.outcomes)}"
            )
        said.append(self.open_line(rows, rounds))
        return said

    def waiting(self, rows: list[dict], rounds: list[dict]) -> tuple[list[dict], list[dict]]:
        """The store with the reviews not yet sent added, as they'd be recorded."""
        for _, data in self.reviews():
            body = self.plan(data, rows, rounds)
            rounds = rounds + [body["review_round"]]
            rows = rows + [r for r in body.get("review_findings", []) if "summary" in r]
        return rows, rounds

    def drafted(self) -> dict[str, str]:
        """The outcome outcomes.md gives each fingerprint, for lines not yet sent."""
        try:
            return {line["fingerprint"]: line["outcome"] for line in self.parse_outcomes()}
        except Stop:
            return {}

    def open_to_fix(self, rows: list[dict]) -> int:
        """The stop rule's count (§3.5): findings to fix whose latest row has no outcome,
        recorded or drafted."""
        drafted = self.drafted()
        return sum(
            to_fix(r) and r.get("outcome") is None and r["fingerprint"] not in drafted
            for r in latest([r for r in rows if r["round"] > 0]).values()
        )

    def open_line(self, rows: list[dict], rounds: list[dict]) -> str:
        last = last_round(rows, rounds)
        where = f"round {last}" if last else "no round yet"
        return f"#{self.n}: {where}, {self.open_to_fix(rows)} open to fix"

    def earlier(self) -> list[dict]:
        """The issue's findings for the next round's review (§3.5): each fingerprint's latest
        row, from Redmine and the reviews not yet sent, with the outcome outcomes.md drafts for
        it when Redmine has none."""
        rows, rounds = self.waiting(*self.store())
        drafted = self.drafted()
        out = []
        for r in latest([r for r in rows if r["round"] > 0]).values():
            out.append(
                {
                    "fingerprint": r["fingerprint"],
                    "round": r["round"],
                    "file": r.get("file") or "",
                    "line": r.get("line"),
                    "summary": r.get("summary") or "",
                    "outcome": r.get("outcome") or drafted.get(r["fingerprint"]) or "open",
                    "verified": bool(r.get("verified_round")),
                }
            )
        return out

    def reviewed(self, commit: str) -> bool:
        """Whether a review file or a Redmine review names commit; Refused if Redmine can't tell."""
        if (self.folder / f"{commit}.json").exists():
            return True
        _, rounds = self.store()
        return any(r.get("commit") == commit for r in rounds)


def last_round(rows: list[dict], rounds: list[dict]) -> int | None:
    """The issue's latest round: rows recorded before review_rounds have no round of their own."""
    return max((r["round"] for r in rows + rounds if r.get("round")), default=None)


def merged(a: dict, b: dict) -> dict:
    """Two findings at one place in one round, as one row (§3.1): the heavier one's fields, the
    lighter one's tag, rule, weighting and summary added to the body."""
    heavy, light = (a, b) if weight(a) >= weight(b) else (b, a)
    out = dict(heavy)
    out["body"] = (heavy.get("body") or "") + (
        f"\n\nAlso found here: {light.get('tag')}, {light.get('rule_id')},"
        f" {light.get('visibility') or '-'}/{light.get('severity')}: {light.get('summary')}"
    )
    return out


def root_of() -> Path:
    return Path(git("rev-parse", "--show-toplevel").strip())


def send(n: int, root: Path | None = None, outcomes: bool = False, redmine=None) -> int:
    """findings.py send, as review.py and main call it: prints what it did; its exit code."""
    issue = Issue(n, root or root_of(), redmine)
    try:
        said = issue.send(outcomes)
    except (Stop, Refused) as e:
        print(f"findings: {e}", file=sys.stderr)
        try:
            rows, rounds = issue.waiting(*issue.store())
            print(f"findings: {issue.open_line(rows, rounds)}", file=sys.stderr)
        except (Stop, Refused, OSError):
            pass
        return 1
    except OSError as e:
        print(f"findings: {e}", file=sys.stderr)
        return 1
    for line in said:
        print(f"findings: {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("send", help="send the review files, and outcomes.md with --outcomes")
    s.add_argument("n", type=int)
    s.add_argument("--outcomes", action="store_true", help="also send .tmp/N/findings/outcomes.md")
    r = sub.add_parser("reviewed", help="whether a review file or a Redmine round names COMMIT")
    r.add_argument("n", type=int)
    r.add_argument("commit")
    a = p.parse_args(argv)
    if a.command == "send":
        return send(a.n, outcomes=a.outcomes)
    try:
        return 0 if Issue(a.n, root_of()).reviewed(a.commit) else 1
    except (Refused, OSError) as e:
        print(
            f"findings: Redmine can't say whether {a.commit[:7]} was reviewed: {e}", file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
