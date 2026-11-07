#!/usr/bin/env python3
"""stage.py N: stage exactly what .tmp/N/manifest.txt names, for the report to show and pr to commit.

The manifest holds one path or directory per line, relative to the repo's root; blank lines don't
count. A path is taken literally, so a glob or a pathspec's magic means nothing. The index is reset
first, so a file staged earlier that the manifest no longer names doesn't ship. Any other change is
refused by name once the manifest is staged, as pr would refuse it. Exits 2 on a refusal: the
working tree is untouched, and a rerun after fixing the manifest starts over.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


class Refused(Exception):
    """Why the manifest can't be staged, in words for the terminal."""


def git(*args: str) -> str:
    """git's stdout, or Refused with its stderr."""
    done = subprocess.run(["git", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused(done.stderr.strip())
    return done.stdout


def entries(manifest: Path) -> list[str]:
    """The manifest's paths, refusing a manifest that's missing or names nothing."""
    if not manifest.is_file():
        raise Refused(f"{manifest} is missing; list each file or directory to ship, one per line")
    lines = [line.strip() for line in manifest.read_text().splitlines()]
    named = [line for line in lines if line]
    if not named:
        raise Refused(f"{manifest} names nothing")
    return named


def whole_tree(root: Path, named: list[str]) -> list[str]:
    """Each entry that is the repo's root, which would stage everything, as git add -A does."""
    return [entry for entry in named if (root / entry).resolve() == root.resolve()]


def stage(named: list[str]) -> None:
    """Make the index HEAD plus the named paths, and nothing else."""
    git("reset", "-q")
    git("--literal-pathspecs", "add", "--", *named)


def left_out() -> list[str]:
    """Each change the index lacks: an edit, a deletion, or a new file git doesn't ignore."""
    changed = git("diff", "--name-only", "-z")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z")
    return sorted({name for name in (changed + untracked).split("\0") if name})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage what .tmp/N/manifest.txt names.")
    parser.add_argument("n", type=int, help="the issue number")
    args = parser.parse_args(argv)
    try:
        root = Path(git("rev-parse", "--show-toplevel").rstrip("\n"))
        os.chdir(root)  # The manifest's paths are the root's, wherever this ran from.
        manifest = root / ".tmp" / str(args.n) / "manifest.txt"
        named = entries(manifest)
        if whole_tree(root, named):
            raise Refused(f"{manifest} names the whole tree; list each file or directory")
        stage(named)
        missed = left_out()
    except Refused as why:
        print(f"stage: {why}", file=sys.stderr)
        return 2
    if missed:
        print("stage: not in the manifest, so pr would refuse them:", file=sys.stderr)
        print("\n".join(f"  {name}" for name in missed), file=sys.stderr)
        print("stage: add each to the manifest, or delete or ignore it", file=sys.stderr)
        return 2
    print(git("diff", "--cached", "--name-status"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
