#!/usr/bin/env python3
"""Add or remove relations between Redmine issues.

    issue_relations.py --check 104 blocks 124 124 blocks 107   # what a run would do, nothing written
    issue_relations.py 104 blocks 124 115 relates 104           # add each missing one, read them back
    issue_relations.py --remove 104 blocks 124                  # remove each, read back that it's gone

Each relation is three words: an issue, a type and another issue. The types are Redmine's,
relates, blocks, blocked, precedes, follows, duplicates, duplicated, copied_to and copied_from,
plus blocked_by for blocked. Redmine keeps a reverse type as its forward one, 124 blocked 104 as
104 blocks 124, so either spelling finds the same relation, and relates is the same either way.

Every issue is read before anything is written. A relation already there is skipped, and each
write is printed as it lands, so a run that stopped part way shows what it changed and is finished
by running it again. Adding refuses a pair of issues that already holds a relation of another
type, naming it to be removed first, and two arguments that join the same pair in different ways;
removing takes both. Removing a relation that isn't there, from a pair that holds another the run
doesn't name, is refused before any write, naming the one there. Adding precedes lets Redmine move the following issue's dates, and says so.
The issues are read back at the end, or after a refused write, and a relation Redmine didn't keep, or didn't remove, is
named and exits non-zero. Redmine's reason for a refusal, such as a circular dependency, is shown.

The API key comes from the Keychain item redmine-gh-plan (REDMINE_KEYCHAIN picks another), and
goes only in a request header. REDMINE_URL defaults to http://127.0.0.1:3010.
"""

import argparse
import functools
import re
import sys

from redmine import FORWARD, REVERSE, Redmine, Refused, canonical, fetch

Rel = tuple[int, str, int]

# Flushed, so each write is on screen as it lands even when stdout is a pipe.
say = functools.partial(print, flush=True)


def pair(rel: Rel) -> frozenset:
    return frozenset((rel[0], rel[2]))


def parse(words: list[str], remove: bool = False) -> list[Rel]:
    """The relations named as issue, type, issue, each once; Refused naming the first bad word."""
    if not words or len(words) % 3:
        raise Refused("give each relation as three words: an issue, a type and another issue")
    out: list[Rel] = []
    for i in range(0, len(words), 3):
        a, kind, b = words[i : i + 3]
        if not all(re.fullmatch(r"#?[1-9][0-9]*", w, re.ASCII) for w in (a, b)):
            raise Refused(f"'{words[i]} {kind} {words[i + 2]}' needs two issue numbers")
        a, b = int(a.lstrip("#")), int(b.lstrip("#"))
        if kind not in FORWARD and kind not in REVERSE:
            raise Refused(f"'{kind}' isn't a type; use one of {', '.join(FORWARD + list(REVERSE))}")
        if a == b:
            raise Refused(f"#{a} can't be related to itself")
        rel = canonical(a, kind, b)
        twin = next((r for r in out if pair(r) == pair(rel)), None)
        if twin and twin != rel and not remove:
            raise Refused(f"{show(twin)} and {show(rel)} join the same two issues; give one")
        if rel not in out:
            out.append(rel)
    return out


def relations(redmine, issues: set[int]) -> dict[Rel, set[int]]:
    """Every relation the issues hold, in its canonical form, and its ids: Redmine can keep relates
    as two rows, one each way."""
    out = {}
    for n in sorted(issues):
        for r in fetch(redmine, n, "relations").get("relations", []):
            rel = canonical(r["issue_id"], r["relation_type"], r["issue_to_id"])
            out.setdefault(rel, set()).add(r["id"])
    return out


def show(rel: Rel) -> str:
    return f"#{rel[0]} {rel[1]} #{rel[2]}"


def command(remove: bool, rels: list[Rel]) -> str:
    return " ".join(
        [
            "issue_relations",
            *(["--remove"] if remove else []),
            *(f"{a} {k} {b}" for a, k, b in rels),
        ]
    )


def run(words: list[str], redmine, check_only: bool, remove: bool) -> str:
    wanted = parse(words, remove)
    have = relations(redmine, {n for a, _, b in wanted for n in (a, b)})

    todo = []
    for rel in wanted:
        others = sorted(h for h in have if pair(h) == pair(rel) and h != rel)
        held = ", ".join(show(h) for h in others)
        if remove and rel not in have and not set(others) <= set(wanted):
            raise Refused(
                f"#{rel[0]} and #{rel[2]} hold {held}, not {show(rel)}; to remove that: "
                f"{command(True, others)}"
            )
        if others and not remove and rel not in have:
            raise Refused(
                f"#{rel[0]} and #{rel[2]} already hold {held}; "
                f"remove {'it' if len(others) == 1 else 'them'} first: "
                f"{command(True, others)}"
            )
        if (rel in have) == remove:
            todo.append(rel)
        else:
            say(f"issue_relations: {show(rel)} {'is not there' if remove else 'is already there'}")
    verb = "remove" if remove else "add"
    if check_only:
        for rel in todo:
            say(f"issue_relations: would {verb} {show(rel)}")
            if rel[1] == "precedes" and not remove:
                say(f"issue_relations: Redmine may move #{rel[2]}'s dates to follow #{rel[0]}'s")
        return f"{len(todo)} to {verb}" + (f"; to write: {command(remove, todo)}" if todo else "")

    # Redmine lists a relation on both its issues, so one end reads it back.
    def unkept(done: list[Rel]) -> list[Rel]:
        back = relations(redmine, {a for a, _, _ in done})
        return [rel for rel in done if (rel in back) == remove]

    # Each write is printed as it lands, so a run refused part way shows what it changed, and
    # running it again finishes the rest. The writes that landed before a refusal are read back.
    done: list[Rel] = []
    try:
        for rel in todo:
            write(redmine, rel, have, remove)
            done.append(rel)
    except Refused as e:
        wrong = unkept(done) if done else []
        if wrong:
            raise Refused(
                f"{e}; and Redmine didn't {verb} {', '.join(show(r) for r in wrong)}"
            ) from None
        raise
    wrong = unkept(todo)
    if wrong:
        raise Refused(
            f"Redmine didn't {verb} {', '.join(show(r) for r in wrong)}; check the issues in Redmine"
        )
    return f"{len(todo)} relation{'s' if len(todo) != 1 else ''} {'removed' if remove else 'added'}"


def write(redmine, rel: Rel, have: dict[Rel, set[int]], remove: bool) -> None:
    """Add or remove one relation, and say so once Redmine has answered."""
    a, kind, b = rel
    if remove:
        rows = sorted(have[rel])
        for i, rid in enumerate(rows):
            try:
                redmine.send("DELETE", f"/relations/{rid}.json")
            except Refused:
                if i:
                    say(f"issue_relations: removed {i} of {len(rows)} rows of {show(rel)}")
                raise
    else:
        redmine.send(
            "POST",
            f"/issues/{a}/relations.json",
            {"relation": {"issue_to_id": b, "relation_type": kind}},
        )
    say(f"issue_relations: {'removed' if remove else 'added'} {show(rel)}")
    if kind == "precedes" and not remove:
        say(f"issue_relations: Redmine may have moved #{b}'s dates to follow #{a}'s")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--check", action="store_true", help="say what a run would do; write nothing")
    p.add_argument("--remove", action="store_true", help="remove the relations instead of adding")
    p.add_argument("relation", nargs="+", help="issue type issue, repeated")
    a = p.parse_args(argv)
    try:
        parse(a.relation, a.remove)  # a typo is named before the Keychain is read
        say(f"issue_relations: {run(a.relation, Redmine(), a.check, a.remove)}")
    except Refused as e:
        sys.exit(f"issue_relations: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
