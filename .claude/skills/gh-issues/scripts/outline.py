#!/usr/bin/env python3
"""Check an issue description's body against the issue outline.

    outline.py .tmp/88/description.md

The front matter is skipped: only the `###` sections after it are checked. The body is Task,
Guardrails, Lenses and Done. Lenses is the lens checklist, every lens listed, and Done is a set of
pass/fail checkboxes, at least two of them, none ticked: the PR body ticks them. Every [x] lens has a Done condition that names it
(`- [ ] L6: …`), so the review topic is checked before the item is finished.

The outline is ~/.claude/agents/technical-writer.md (*Outlines*) plus the repo's
docs/agents/technical-writer.md: its lens table adds lenses, and any `### Name` it mentions
becomes a required section. Exit 0 when the body conforms, 1 with findings.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

GLOBAL_WRITER = Path.home() / ".claude/agents/technical-writer.md"
LENS_ROW = re.compile(r"^\|\s*(L\d+)\s+([^|]+?)\s*\|", re.M)
TASK = ["Task", "Guardrails", "Lenses", "Done"]
OLD = ["Summary", "Scope", "Acceptance", "Tests", "Steps", "Expected", "Actual", "Planning"]
MIN_DONE = 2
LENS_LINE = re.compile(r"^- \[(.)\] (L\d+)\b(.*)$")
DONE_LINE = re.compile(r"^- \[(.)\] (.*)$")


def repo_writer() -> Path | None:
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode != 0:
        return None
    path = Path(top.stdout.strip()) / "docs/agents/technical-writer.md"
    return path if path.is_file() else None


def load_expected() -> tuple[list[str], list[str]]:
    """Lens ids and extra sections from the global writer file and this repo's, read from disk."""
    rw = repo_writer()
    return expected(
        GLOBAL_WRITER.read_text(encoding="utf-8") if GLOBAL_WRITER.is_file() else "",
        rw.read_text(encoding="utf-8") if rw else "",
    )


def expected(global_text: str, repo_text: str) -> tuple[list[str], list[str]]:
    """Lens ids, and the extra sections the repo names."""
    lenses = [m[0] for m in LENS_ROW.findall(global_text + "\n" + repo_text)]
    lenses = sorted(set(lenses), key=lambda lens: int(lens[1:]))
    extra = re.findall(r"`### ([A-Z][\w ]*?)`", repo_text)
    return lenses, list(dict.fromkeys(extra))


def sections(body: str) -> dict[str, str]:
    out, name = {}, None
    for line in body.splitlines():
        if m := re.match(r"^### (.+?)\s*$", line):
            name = m[1]
            out[name] = ""
        elif name:
            out[name] += line + "\n"
    return out


def check(body: str, lenses: list[str], extra: list[str]) -> list[str]:
    found = sections(body)
    required = TASK + [s for s in extra if s not in TASK]
    findings = [f"missing section: ### {s}" for s in required if s not in found]
    findings += [
        f"old-style heading: {line}" for line in body.splitlines() if re.match(r"^## \w", line)
    ]
    old = [s for s in OLD if s in found and s not in required]
    if old:
        findings.append(
            f"old outline ({', '.join(old)}): rewrite to Task, Guardrails, Lenses and Done"
        )

    if "Done" in found:
        done = [m for line in found["Done"].splitlines() if (m := DONE_LINE.match(line.strip()))]
        findings += [
            f"Done: {m[2]!r} is ticked; an issue's description is never ticked, the PR body is"
            for m in done
            if m[1] == "x"
        ]
        findings += [f"Done: mark [{m[1]}] on {m[2]!r}; use [ ]" for m in done if m[1] not in " x"]
        loose = [
            line.strip()
            for line in found["Done"].splitlines()
            if line.strip().startswith("- ") and not DONE_LINE.match(line.strip())
        ]
        findings += [
            f"Done: {line!r} is not a checkbox; write each condition as - [ ] …" for line in loose
        ]
        if len(done) < MIN_DONE:
            findings.append(
                f"Done: {len(done)} pass/fail condition(s); a Task needs at least {MIN_DONE}, "
                "covering each part of it and what the tests prove"
            )

    strays = [
        m[2]
        for line in found.get("Guardrails", "").splitlines()
        if (m := LENS_LINE.match(line.strip()))
    ]
    if strays:
        findings.append(f"Guardrails: lens lines ({', '.join(strays)}) belong under ### Lenses")
    seen, ticked = {}, []
    for line in found.get("Lenses", "").splitlines():
        if m := LENS_LINE.match(line.strip()):
            mark, lens, note = m[1], m[2], m[3]
            seen[lens] = seen.get(lens, 0) + 1
            if mark == "x":
                ticked.append(lens)
            has_note = ":" in note and note.split(":", 1)[1].strip() != ""
            if mark not in " x":
                findings.append(f"{lens}: mark [{mark}]; use [x] with a note or [ ] without")
            elif mark == "x" and not has_note:
                findings.append(f"{lens}: [x] needs a note saying what the lens requires")
            elif mark == " " and has_note:
                findings.append(f"{lens}: has a note but is [ ]; mark it [x] or drop the note")
    if "Lenses" in found:
        findings += [f"{lens}: lens missing" for lens in lenses if lens not in seen]
        findings += [f"{lens}: listed {n} times" for lens, n in seen.items() if n > 1]
        findings += [f"{lens}: not a configured lens" for lens in seen if lens not in lenses]
        named = set(re.findall(r"^- \[.\] (L\d+):", found.get("Done", ""), re.M))
        findings += [
            f"{lens}: [x] but no Done condition names it; add - [ ] {lens}: …"
            for lens in ticked
            if lens not in named
        ]
        findings += [
            f"Done: {lens} is named but not [x] under Lenses"
            for lens in sorted(named - set(ticked), key=lambda lens: int(lens[1:]))
        ]
    return findings


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "item", type=Path, help="path to a file holding the description, or a new_issue draft"
    )
    a = p.parse_args(argv)
    body = a.item.read_text(encoding="utf-8")

    lenses, extra = load_expected()
    findings = check(body, lenses, extra)
    if not findings:
        print("conforms to the outline")
        return 0
    print(
        f"{len(findings)} finding(s) against the outline (lenses {lenses[0] if lenses else 'none'}–{lenses[-1] if lenses else ''}"
        f"{', plus ' + ', '.join(extra) if extra else ''}):"
    )
    for f in findings:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
