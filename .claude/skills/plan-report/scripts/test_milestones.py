"""Offline tests for milestones.py. Run from scripts/: python3 -m unittest test_milestones"""

import unittest

from milestones import NONE, Issue, build, plan_order, render_text


def issue(
    n,
    status="New",
    closed=False,
    pts=2,
    version="M1",
    priority=2,
    ordinal=None,
    blockers=(),
    closed_on="2026-10-20",
):
    return Issue(
        n,
        f"Task {n}",
        status,
        closed,
        pts,
        closed_on if closed else None,
        version,
        priority,
        ordinal,
        frozenset(blockers),
    )


V = [
    {"name": "M2", "status": "open"},
    {"name": "M1", "status": "open"},
    {"name": "Old", "status": "closed"},
]


class Build(unittest.TestCase):
    def test_closed_and_rejected_issues_stay_counted(self):
        issues = [
            issue(1),
            issue(2, "Closed", True, 3),
            issue(3, "Rejected", True, 5),
            issue(4, "In Progress"),
        ]
        m1 = build(V, issues)[0]
        self.assertEqual((m1.name, len(m1.issues), m1.done, m1.percent), ("M1", 4, 2, 50))
        self.assertEqual(m1.points, (3, 7, 0))  # a rejected issue's points leave the total

    def test_closed_milestone_shown_only_when_it_holds_a_highlight_or_all(self):
        old = issue(9, "Closed", True, version="Old")
        self.assertNotIn("Old", [m.name for m in build(V, [old])])
        self.assertIn("Old", [m.name for m in build(V, [old], [old])])
        self.assertIn("Old", [m.name for m in build(V, [old], show_closed=True)])

    def test_lists_on_top_keyed_by_milestone_and_every_issue_as_a_chip(self):
        a, b, c = (
            issue(1, pts=None),
            issue(2, "Closed", True, 3),
            issue(5, "Closed", True, 1, version=None),
        )
        text = render_text([a], [b, c], build(V, [a, b]))
        head, milestones = text.split("\nM1", 1)
        self.assertIn("1. #1    M1 ", head)
        self.assertIn(f"2. #5    {NONE}", head)
        self.assertIn(
            "#1(–) #2(3)✓", milestones
        )  # the next and the closed stay in the milestone's list


class PlanOrder(unittest.TestCase):
    def ids(self, issues, versions=("M1", "M2")):
        return [i.id for i in plan_order(issues, list(versions))]

    def test_milestone_first_then_in_progress_ordinal_priority_number(self):
        issues = [
            issue(1, version="M2"),
            issue(2, version=None),
            issue(3),
            issue(4, priority=4),
            issue(5, "In Progress"),
            issue(6, ordinal=1),
            issue(7, "Closed", True),
        ]
        self.assertEqual(self.ids(issues), [5, 6, 4, 3, 1, 2])

    def test_never_before_an_open_blocker_even_in_a_later_milestone(self):
        issues = [issue(1, blockers={2}), issue(2, version="M2"), issue(3), issue(4, blockers={9})]
        self.assertEqual(
            self.ids(issues), [3, 4, 2, 1]
        )  # #9 isn't open here, so it holds nothing back

    def test_a_cycle_is_broken_not_dropped(self):
        self.assertEqual(self.ids([issue(1, blockers={2}), issue(2, blockers={1})]), [1, 2])

    def test_milestone_lists_open_in_plan_order_then_closed_as_they_closed(self):
        issues = [
            issue(1, blockers={3}),
            issue(2, "Closed", True, closed_on="2026-10-22"),
            issue(3),
            issue(4, "Closed", True, closed_on="2026-10-21"),
        ]
        self.assertEqual([i.id for i in build(V, issues)[0].issues], [3, 1, 4, 2])

    def test_next_lists_each_issues_open_blockers(self):
        a, b = issue(1, blockers={2, 3, 9}), issue(2)
        text = render_text([b, a], [], [], open_ids={1, 2, 3})
        self.assertIn("blocked by #2 #3", text)  # #9 is closed, so it blocks nothing
        self.assertEqual(text.count("blocked by"), 1)


if __name__ == "__main__":
    unittest.main()
