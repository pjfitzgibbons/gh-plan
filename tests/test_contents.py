"""Every context file over 100 lines opens with a contents list that matches its headings.

A model reading a long file may preview only its first lines; the list right under the title shows
the whole scope, so it knows to read on. The list is generated from the `##` and `###` headings,
and this test regenerates it, so a heading added, renamed or removed fails here until the list
follows.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
LIMIT = 100
HEADING = re.compile(r"^(##|###) (.+?)\s*$")
ANCHOR = re.compile(r'^<a id="([^"]+)"></a>$')


def context_files() -> list[Path]:
    files = [
        ROOT / "README.md",
        ROOT / "CLAUDE.md",
        *ROOT.glob("docs/**/*.md"),
        *ROOT.glob(".claude/**/*.md"),
    ]
    return sorted(f for f in files if f.is_file())


def slug(text: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation dropped, each space a hyphen."""
    return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")


def contents(text: str) -> list[str]:
    """The contents list for a file: one link per heading, `###` indented under its `##`."""
    lines, out, fenced, seen = text.splitlines(), [], False, {}
    for i, line in enumerate(lines):
        if line.startswith("```"):
            fenced = not fenced
        if fenced or not (m := HEADING.match(line)) or m[2] == "Contents":
            continue
        a = ANCHOR.match(lines[i - 1]) if i else None
        anchor = a[1] if a else slug(m[2])
        if not a:  # GitHub numbers a repeated slug: x, x-1, x-2
            n = seen.get(anchor, 0)
            seen[anchor] = n + 1
            anchor = f"{anchor}-{n}" if n else anchor
        out.append(f"{'  ' if m[1] == '###' else ''}- [{m[2]}](#{anchor})")
    return out


def with_contents(text: str) -> str:
    """The file with its contents list right under the `#` title, replacing any list already there."""
    lines = text.splitlines(keepends=True)
    rest = lines[1:]
    if "".join(rest).lstrip("\n").startswith("## Contents\n"):
        start = next(i for i, line in enumerate(rest) if line == "## Contents\n") + 1
        while start < len(rest) and rest[start] == "\n":
            start += 1
        end = next(
            (i for i in range(start, len(rest)) if not rest[i].startswith(("- ", "  - "))),
            len(rest),
        )
        rest = rest[end:]
    block = "\n## Contents\n\n" + "".join(line + "\n" for line in contents(text)) + "\n"
    return lines[0] + block + "".join(rest).lstrip("\n")


def listed(text: str) -> list[str] | None:
    """The contents list as written, or None when the file doesn't open with one."""
    lines = text.splitlines()
    if lines[1:4] != ["", "## Contents", ""]:
        return None
    out = []
    for line in lines[4:]:
        if not line.startswith(("- ", "  - ")):
            break
        out.append(line)
    return out


LONG = [f for f in context_files() if len(f.read_text(encoding="utf-8").splitlines()) > LIMIT]


@pytest.mark.parametrize("path", LONG, ids=lambda p: str(p.relative_to(ROOT)))
def test_a_long_file_opens_with_its_contents(path):
    text = path.read_text(encoding="utf-8")
    assert listed(text) is not None, f"{path.name}: no ## Contents list under the title"
    assert listed(text) == contents(text), (
        f"{path.name}: the contents list doesn't match the headings"
    )


def test_slug_matches_github():
    assert (
        slug("Backlog.md — installed v1.53.0, throwaway git repo")
        == "backlogmd--installed-v1530-throwaway-git-repo"
    )


def test_with_contents_replaces_a_stale_list():
    text = "# T\n\n## Contents\n\n- [Old](#old)\n\n## New\nx\n"
    assert with_contents(text) == "# T\n\n## Contents\n\n- [New](#new)\n\n## New\nx\n"


def test_headings_in_code_fences_are_skipped():
    assert contents("# T\n```\n## not a heading\n```\n## Real\n") == ["- [Real](#real)"]
