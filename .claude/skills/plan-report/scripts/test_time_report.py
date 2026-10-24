"""Offline tests for time_report.py. Run from scripts/: python3 -m unittest test_time_report"""

import unittest
from datetime import datetime, timedelta, timezone

from time_report import Interval, build, fix_script, render

UTC = timezone.utc
R = "example-org/app"
START = datetime(2026, 10, 12, 7, tzinfo=UTC)
END = START + timedelta(days=1)
NOW = START + timedelta(hours=10)


def at(h, m=0):
    return START + timedelta(hours=h, minutes=m)


def iv(id, h1, m1, h2, m2, *tags):
    return Interval(id, at(h1, m1), at(h2, m2) if h2 is not None else None, {f"repo:{R}", *tags})


def issue(n, title, closed, reason="COMPLETED"):
    return {
        "number": n,
        "title": title,
        "closedAt": closed.isoformat().replace("+00:00", "Z"),
        "stateReason": reason,
    }


class Report(unittest.TestCase):
    def test_rows_totals_and_corrections(self):
        issues = [
            issue(7, "[1] Config", at(2)),
            issue(8, "[2] Model", at(4)),
            issue(9, "[3] Dropped", at(4), "NOT_PLANNED"),
            issue(5, "[1] Untouched", at(5)),
        ]
        intervals = [
            iv(6, 0, 0, 0, 30),  # untagged, before #7's branch
            iv(5, 0, 30, 1, 30, f"{R}#7"),
            iv(4, 1, 30, 2, 0, f"{R}#7"),
            iv(3, 2, 30, 3, 0, f"{R}#8"),
            iv(2, 4, 10, 4, 20, f"{R}#8"),  # after #8 closed
            iv(1, 5, 0, None, None, f"{R}#8"),  # still running on a closed issue
            Interval(99, at(0), at(9), {"repo:other/repo"}),  # another repo: ignored
        ]
        rows, not_planned, found, fixes = build(issues, intervals, R, START, END, NOW)
        self.assertEqual([r.number for r in rows], [7, 8, 5])
        self.assertEqual(not_planned, 1)
        self.assertEqual(rows[0].seconds, 5400)
        self.assertEqual((rows[1].entries, rows[1].running), (2, 1))
        joined = "\n".join(found)
        self.assertIn("@6", joined)
        self.assertIn("has no issue tag", joined)
        self.assertIn("starts after GitHub #8 closed", joined)
        self.assertIn("still running, tagged #8; GitHub #8 closed", joined)
        self.assertFalse([f for f in fixes if "timew stop" in f.reason])
        self.assertIn("#5 closed with no tagged time", joined)
        self.assertIn((6, f"{R}#7"), [(f.interval.id, f.add) for f in fixes])
        script = fix_script(fixes)
        self.assertIn(f"timew tag @6 '{R}#7'", script)
        self.assertIn("check 6 20261012T070000Z", script)

        text = render(rows, not_planned, 1.0, "test")
        self.assertIn("#7       1    1.0     1.50     +0.50   +50%", text)
        self.assertIn("#5       1    1.0       —          no snapshot", text)
        self.assertIn("1 closed as not planned, left out", text)

    def test_flapping(self):
        intervals = [
            iv(10 - k, 1, 2 * k, 1, 2 * k + 2, *([f"{R}#7"] if k % 2 else [])) for k in range(6)
        ]
        _, _, found, _ = build([issue(7, "[1] X", at(3))], intervals, R, START, END, NOW)
        self.assertTrue(any("flapping between untagged ↔ #7" in f for f in found), found)

    def test_assign_retags_untagged_and_post_close_slices(self):
        intervals = [iv(3, 0, 0, 0, 30), iv(2, 3, 0, 3, 30, f"{R}#6"), iv(1, 4, 0, 4, 30)]
        _, _, _, fixes = build(
            [issue(6, "[5] Six", at(2))], intervals, R, START, END, NOW, assign=60
        )
        self.assertEqual(
            [(f.interval.id, f.add, f.remove) for f in fixes],
            [(2, f"{R}#60", f"{R}#6"), (3, f"{R}#60", None), (1, f"{R}#60", None)],
        )
        self.assertIn(f"timew untag @2 '{R}#6'", fix_script(fixes))

    def test_outside_the_period_is_not_checked(self):
        old = [iv(3, -30, 0, -29, 0)]
        _, _, found, _ = build([], old, R, START, END, NOW)
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
