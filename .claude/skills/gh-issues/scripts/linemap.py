"""Where a finding's lines are at a later commit (design 0005, §3.5).

A finding's key names its lines at the commit of the round that raised it. `where` maps each of
those lines to the later commit, or to None when the line changed. When the raising commit is an
ancestor of the later one, `git blame -M` does it, so a block moved within the file keeps its
lines. After a rebase it isn't, and blame would take the merge base as its boundary, with that
commit's line numbers; a tree diff, `git diff -M -U0`, maps it instead, following renames and
shifts but not moves, so a moved block reads as changed. Both follow a renamed file.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
BLAME_HEADER = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)(?: (\d+))?$")


@dataclass
class Lines:
    """A finding's lines at a later commit: its file there, and each old line's new number, None
    for a line that changed."""

    file: str
    moved: dict[int, int | None]
    # Where the lines sit when none is still there: after the nearest unchanged line before them
    # and before the nearest one after, None for the file's end.
    between: tuple[int, int | None] = (1, None)

    def unchanged(self) -> bool:
        return all(new is not None for new in self.moved.values())

    def extent(self) -> tuple[int, int] | None:
        """The first and last of the lines that are still there, or None when none is."""
        kept = [new for new in self.moved.values() if new is not None]
        return (min(kept), max(kept)) if kept else None

    def span(self) -> tuple[int, int | None]:
        """The lines that are still there, or the gap where they were."""
        return self.extent() or self.between

    def shown(self) -> str:
        """The lines as a check reads them: `file:12-14`, and which old lines changed."""
        extent = self.extent()
        if extent is None:
            return f"{self.file}: every line changed"
        start, end = extent
        lines = str(start) if start == end else f"{start}-{end}"
        changed = [old for old, new in self.moved.items() if new is None]
        if not changed:
            return f"{self.file}:{lines}"
        return f"{self.file}:{lines}, its old line{'' if len(changed) == 1 else 's'} {', '.join(map(str, changed))} changed"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, errors="replace", check=True
    ).stdout


def succeeds(root: Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=root, capture_output=True).returncode == 0


def present(root: Path, commit: str) -> bool:
    """Whether the commit is in the repo, fetched from origin if it isn't, as after a gc."""
    if succeeds(root, "cat-file", "-e", f"{commit}^{{commit}}"):
        return True
    succeeds(root, "fetch", "--quiet", "--no-tags", "origin", commit)
    return succeeds(root, "cat-file", "-e", f"{commit}^{{commit}}")


def renamed_to(root: Path, file: str, since: str, commit: str) -> str | None:
    """The file's path at commit, or None when it was deleted."""
    out = git(root, "diff", "-M", "--name-status", "-z", since, commit).split("\0")
    i = 0
    while i < len(out) - 1:
        status = out[i]
        if status[:1] in "RC":
            old, new = out[i + 1], out[i + 2]
            if old == file and status[0] == "R":
                return new
            i += 3
            continue
        if out[i + 1] == file and status == "D":
            return None
        i += 2
    return file


def blamed(root: Path, file: str, now: str, since: str, commit: str) -> dict[int, int]:
    """Each line of file at since that's unchanged at commit, by blame, mapped to its number in
    now there."""
    out = git(root, "blame", "-M", "--porcelain", f"{since}..{commit}", "--", now)
    return {line.old: line.new for line in blame_lines(out) if line.boundary and line.file == file}


@dataclass
class Blamed:
    """One line of blame's porcelain output: its number in the commit it's credited to and now,
    that commit's name for its file, and whether that commit is the boundary, so unchanged."""

    old: int
    new: int
    file: str
    boundary: bool


@dataclass
class Group:
    """A run of blame's lines credited to one commit, and the filename it printed, if any."""

    sha: str
    lines: list[tuple[int, int]]
    file: str | None = None


def blame_groups(out: str) -> tuple[list[Group], set[str]]:
    """blame --porcelain's groups, and the commits it marked `boundary`. A group's first line
    carries a line count; `boundary` and `filename` follow it only the first time its commit
    appears, or `filename` on every group of a commit that touched more than one path."""
    groups, boundary = [], set()
    for text in out.splitlines():
        header = BLAME_HEADER.match(text)
        if header and header[4]:
            groups.append(Group(header[1], []))
        if header:
            groups[-1].lines.append((int(header[2]), int(header[3])))
        elif text == "boundary":
            boundary.add(groups[-1].sha)
        elif text.startswith("filename "):
            groups[-1].file = text.removeprefix("filename ")
    return groups, boundary


def blame_lines(out: str) -> list[Blamed]:
    """blame --porcelain's lines, each with its commit's filename, from its own group or the last
    the commit printed."""
    groups, boundary = blame_groups(out)
    files: dict[str, str] = {}
    lines = []
    for group in groups:
        if group.file:
            files[group.sha] = group.file
        file = files.get(group.sha, "")
        lines += [Blamed(old, new, file, group.sha in boundary) for old, new in group.lines]
    return lines


def hunks(diff: str) -> list[tuple[int, int, int, int]]:
    """A -U0 diff's hunks: old start and count, new start and count, a missing count being 1."""
    found = []
    for text in diff.splitlines():
        if m := HUNK.match(text):
            found.append((int(m[1]), count(m[2]), int(m[3]), count(m[4])))
    return found


def count(n: str | None) -> int:
    """A hunk's line count, which the diff leaves out when it is 1."""
    return 1 if n is None else int(n)


def diffed(
    root: Path, file: str, now: str, since: str, commit: str
) -> list[tuple[int, int, int, int]]:
    """The tree diff's hunks for file, from since to commit, where it is now."""
    return hunks(git(root, "diff", "-M", "-U0", "--no-ext-diff", since, commit, "--", file, now))


def shifted(old: int, hunks: list[tuple[int, int, int, int]]) -> int | None:
    """old's line number after the hunks, or None when a hunk removed or replaced it. A hunk that
    removes nothing inserts after its start line."""
    shift = 0
    for start, removed, _, added in hunks:
        if removed and start <= old < start + removed:
            return None
        last_before = start + removed - 1 if removed else start
        if last_before < old:
            shift += added - removed
    return old + shift


def gap(before: int, after: int | None) -> tuple[int, int | None]:
    """The lines between two unchanged ones, or the two themselves when nothing is between."""
    first, last = before + 1, None if after is None else after - 1
    if last is not None and last < first:
        return last, first
    return first, last


def nearest(kept: dict[int, int], start: int, end: int) -> tuple[int, int | None]:
    """The new numbers of the unchanged lines nearest before start and after end."""
    before = [old for old in kept if old < start]
    after = [old for old in kept if old > end]
    return (kept[max(before)] if before else 0), (kept[min(after)] if after else None)


def shifted_nearest(
    start: int, end: int, found: list[tuple[int, int, int, int]]
) -> tuple[int, int | None]:
    """nearest, by the tree diff's hunks: past the last hunk every line is unchanged."""
    before = next(
        (new for old in range(start - 1, 0, -1) if (new := shifted(old, found)) is not None), 0
    )
    beyond = end + 1 + sum(removed for _, removed, _, _ in found)
    after = next(
        (new for old in range(end + 1, beyond + 1) if (new := shifted(old, found)) is not None),
        None,
    )
    return before, after


def where(root: Path, file: str, start: int, end: int, since: str, commit: str) -> Lines | None:
    """The lines start to end of file at since, at commit; None when since isn't in the repo or
    origin, or the file was deleted."""
    if not present(root, since):
        return None
    now = renamed_to(root, file, since, commit)
    if now is None:
        return None
    if succeeds(root, "merge-base", "--is-ancestor", since, commit):
        kept = blamed(root, file, now, since, commit)
        moved = {old: kept.get(old) for old in range(start, end + 1)}
        around = nearest(kept, start, end)
    else:
        found = diffed(root, file, now, since, commit)
        moved = {old: shifted(old, found) for old in range(start, end + 1)}
        around = shifted_nearest(start, end, found)
    return Lines(now, moved, gap(*around))
