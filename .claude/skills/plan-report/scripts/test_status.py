"""Offline tests for status.py. Run from scripts/: python3 -m unittest test_status"""

import unittest
from datetime import date, datetime, timezone

from status import active_days, mark_wip, next_tasks, parse
from time_report import Interval

R = "example-org/app"


def issue(n, est, ms, backlog, blockers=()):
    body = f"### Planning\n- Backlog ID: {backlog}\n- Estimate: {est}\n- Milestone: {ms}\n"
    return {
        "number": n,
        "title": f"[{est}] Task {n}",
        "body": body,
        "blockedBy": {"nodes": [{"number": b, "state": s} for b, s in blockers]},
    }


class Next(unittest.TestCase):
    def setUp(self):
        raw = [
            issue(10, 2, "phase-1", "1.4"),
            issue(9, 3, "phase-1", "1.3"),
            issue(2, 1, "phase-0", "0.1"),
            issue(16, 3, "phase-1", "1.10", [(10, "OPEN")]),
            issue(15, 3, "phase-1", "1.9", [(8, "CLOSED")]),
            issue(49, 3, "— (Icebox)", "U.3"),
            issue(30, 2, "phase-2", "2.1"),
        ]
        self.tasks = {i["number"]: parse(i) for i in raw}

    def test_order_skips_blocked_and_icebox(self):
        got = [t.number for t in next_tasks(self.tasks, 5)]
        self.assertEqual(got, [2, 9, 10, 15, 30])

    def test_wip_comes_first_current_branch_before_others(self):
        mark_wip(self.tasks, ["main", "feat/30-x", "feat/16-y"], "feat/16-y", {f"{R}#30"}, R)
        got = next_tasks(self.tasks, 4)
        self.assertEqual([t.number for t in got], [16, 30, 2, 9])
        self.assertEqual(got[0].wip, ["current branch feat/16-y"])
        self.assertEqual(got[1].wip, ["branch feat/30-x", "timer running"])

    def test_assigned_to_me_is_wip_without_a_branch(self):
        mark_wip(
            self.tasks,
            ["main"],
            "main",
            set(),
            R,
            {10: ["someone-else", "me"], 9: ["someone-else"]},
            "me",
        )
        got = next_tasks(self.tasks, 3)
        self.assertEqual([(t.number, t.wip) for t in got[:1]], [(10, ["assigned to me"])])
        self.assertEqual([t.number for t in got], [10, 2, 9])

    def test_icebox_parsed_as_no_milestone(self):
        self.assertIsNone(self.tasks[49].milestone)
        self.assertEqual(self.tasks[15].blocked_by, [])


class Days(unittest.TestCase):
    def test_last_active_days_skip_gaps(self):
        utc = timezone.utc
        closed = [{"closedAt": "2026-10-05T18:00:00Z"}]
        intervals = [
            Interval(1, datetime(2026, 10, 12, 15, tzinfo=utc), None, {f"repo:{R}"}),
            Interval(
                2,
                datetime(2026, 10, 9, 15, tzinfo=utc),
                datetime(2026, 10, 9, 16, tzinfo=utc),
                {f"repo:{R}"},
            ),
            Interval(
                3,
                datetime(2026, 10, 11, 15, tzinfo=utc),
                datetime(2026, 10, 11, 16, tzinfo=utc),
                {"repo:other/x"},
            ),
        ]
        days = active_days(closed, intervals, R, date(2026, 10, 12), 3)
        self.assertEqual(days, [date(2026, 10, 5), date(2026, 10, 9), date(2026, 10, 12)])


if __name__ == "__main__":
    unittest.main()
