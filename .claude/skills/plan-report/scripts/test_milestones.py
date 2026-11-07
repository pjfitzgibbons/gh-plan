"""Offline tests for milestones.py. Run from scripts/: python3 -m unittest test_milestones"""

import json
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from milestones import (
    NONE,
    Issue,
    Milestone,
    assemble,
    build,
    main,
    next_up_query,
    options_file,
    parse,
    plan_order,
    render_html,
    render_text,
    rerun_options,
    saved_options,
    version_order,
    write_atomically,
)
from redmine import Refused


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


RAW = {
    "id": 1,
    "subject": "Task",
    "status": {"id": 1, "name": "New"},
    "fixed_version": {"name": "M1"},
    "custom_fields": [{"name": "Points", "value": "3"}, {"name": "Ordinal", "value": ""}],
}


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


class Ordering(unittest.TestCase):
    def test_versions_sort_numbers_by_value_whatever_the_case(self):
        names = ["tempo", "M10", "Old", "M2"]
        ordered = version_order([{"name": n} for n in names])
        self.assertEqual([v["name"] for v in ordered], ["M2", "M10", "Old", "tempo"])

    def test_only_in_progress_comes_first_and_counts_as_active(self):
        self.assertEqual(issue(1, "Feedback").kind, "open")
        self.assertEqual(issue(1, "Resolved").kind, "open")
        order = plan_order([issue(1, "Feedback", ordinal=2), issue(2, ordinal=1)], ["M1"])
        self.assertEqual([i.id for i in order], [2, 1])

    def test_a_blocker_chain_through_an_issue_outside_the_list_still_holds(self):
        a, b, c = issue(1, blockers={2}), issue(2, blockers={3}, version="Ice"), issue(3)
        self.assertEqual(plan_order([a, c], ["M1"], {2: b, 1: a, 3: c}), [c, a])

    def test_a_chain_cycle_outside_the_list_ends(self):
        a = issue(1, blockers={2})
        b, c = issue(2, blockers={3}), issue(3, blockers={2})
        self.assertEqual(plan_order([a], ["M1"], {1: a, 2: b, 3: c}), [a])

    def test_a_cycle_through_an_outside_issue_does_not_hold_an_issue_back_from_itself(self):
        a, b, c = issue(1, blockers={2}), issue(2, blockers={1}), issue(3)
        order = plan_order([a, c], ["M1"], {1: a, 2: b, 3: c})
        self.assertEqual([i.id for i in order], [1, 3])

    def test_a_rejected_issue_with_no_points_is_not_unpointed(self):
        m = Milestone("M1", issues=[issue(1, pts=None), issue(2, "Rejected", True, pts=None)])
        self.assertEqual(m.points, (0, 0, 1))


class Parse(unittest.TestCase):
    def test_only_a_blocks_relation_pointing_at_the_issue_is_a_blocker(self):
        relations = [
            {"relation_type": "blocks", "issue_id": 2, "issue_to_id": 1},
            {"relation_type": "blocks", "issue_id": 1, "issue_to_id": 3},
            {"relation_type": "relates", "issue_id": 4, "issue_to_id": 1},
        ]
        raw = dict(RAW, relations=relations)
        self.assertEqual(parse(raw, {5}).blockers, {2})

    def test_custom_fields_status_and_version(self):
        parsed = parse(RAW, {5})
        self.assertEqual((parsed.points, parsed.ordinal), (3, None))
        self.assertEqual((parsed.version, parsed.status, parsed.closed), ("M1", "New", False))
        self.assertTrue(parse(RAW, {1}).closed)


class NextUpQuery(unittest.TestCase):
    PLAN = {"id": 1}
    CHILD = {"id": 2, "parent": {"id": 1}}

    def query(self, n, project):
        return {"id": n, "name": "Next up", "project_id": project}

    def test_the_projects_own_query_comes_before_its_parents_and_a_global_one(self):
        queries = [self.query(1, None), self.query(2, 1), self.query(3, 2)]
        self.assertEqual(next_up_query(queries, self.CHILD)["id"], 3)

    def test_another_projects_query_is_never_picked(self):
        with self.assertRaises(Refused):
            next_up_query([self.query(3, 2)], self.PLAN)


class Assemble(unittest.TestCase):
    def test_next_up_picks_the_issues_and_recent_are_newest_first(self):
        issues = [
            issue(1, blockers={4}),
            issue(2, "Closed", True, closed_on="2026-10-01"),
            issue(3, "Closed", True, closed_on="2026-10-02"),
            issue(4),
            issue(5),
        ]
        options = Namespace(n_next=5, n_closed=1, all=False)
        plan = assemble("http://r", V, issues, {1, 4}, options)
        self.assertEqual([i.id for i in plan.upcoming], [4, 1])
        self.assertEqual([i.id for i in plan.recent], [3])
        self.assertEqual(set(plan.open_ids), {1, 4, 5})


class Html(unittest.TestCase):
    def test_a_page_escapes_issue_text_and_links_blockers(self):
        a = Issue(1, "<b>", "New", False, None, None, "M1", blockers=frozenset({2}))
        b = issue(2)
        plan = assemble("http://r", V, [a, b], {1, 2}, Namespace(n_next=5, n_closed=5, all=False))
        page = render_html(plan, datetime(2026, 11, 6, 12, 0))
        self.assertIn("&lt;b&gt;", page)
        self.assertNotIn("<b>", page.split("</style>")[1])
        self.assertIn('blocked by <a href="http://r/issues/2">#2</a>', page)
        self.assertIn("id=regen", page)


class Files(unittest.TestCase):
    def test_a_write_leaves_no_file_aside(self):
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "a/b/page.html"
            write_atomically(page, "one")
            write_atomically(page, "two")
            self.assertEqual(page.read_text(), "two")
            self.assertEqual([f.name for f in page.parent.iterdir()], ["page.html"])

    def test_options_round_trip_beside_the_page_and_are_empty_when_absent(self):
        options = Namespace(n_next=8, n_closed=2, all=True, project="tempotag")
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "page.html"
            self.assertEqual(saved_options(page), [])
            options_file(page).write_text(json.dumps(rerun_options(options)))
            self.assertEqual(
                saved_options(page),
                ["--next", "8", "--closed", "2", "--project", "tempotag", "--all"],
            )


class PageOptions(unittest.TestCase):
    def test_two_pages_in_a_folder_keep_their_own_options(self):
        with tempfile.TemporaryDirectory() as d:
            default, other = Path(d) / "progress.html", Path(d) / "other.html"
            options_file(default).write_text('["--all"]')
            options_file(other).write_text('["--project", "tempotag"]')
            self.assertEqual(saved_options(default), ["--all"])


class FakeRedmine:
    """Answers each path with its listing; refused or empty answers stand in for a bad Redmine."""

    url = "http://r"

    def __init__(self, fail_with=None):
        self.fail_with = fail_with

    def send(self, method, path):
        if self.fail_with:
            raise self.fail_with
        if path.startswith("/issue_statuses"):
            return {"issue_statuses": [{"id": 1}, {"id": 5, "is_closed": True}]}
        if path.endswith("/versions.json"):
            return {"versions": [{"name": "M1", "status": "open"}]}
        if path.startswith("/projects/"):
            return {"project": {"id": 1}}
        if path.startswith("/queries"):
            return {"queries": [{"id": 8, "name": "Next up", "project_id": 1}], "total_count": 1}
        return {"issues": [RAW], "total_count": 1}


class Report(unittest.TestCase):
    def test_a_refused_redmine_stops_with_one_line(self):
        refused = FakeRedmine(Refused("Redmine can't be reached at http://r: refused"))
        with tempfile.TemporaryDirectory() as d, patch("milestones.Redmine", return_value=refused):
            with self.assertRaises(SystemExit) as stop:
                main(["--out", f"{d}/page.html"])
        self.assertEqual(
            stop.exception.code,
            "report-milestones: Redmine can't be reached at http://r: refused",
        )

    def test_an_unwritable_page_stops_with_one_line(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch("milestones.Redmine", return_value=FakeRedmine()),
        ):
            (Path(d) / "file").write_text("")
            with self.assertRaises(SystemExit) as stop:
                main(["--out", f"{d}/file/page.html"])
        self.assertIn("report-milestones: ", str(stop.exception.code))

    def test_a_non_json_answer_stops_with_one_line(self):
        bad = FakeRedmine(ValueError("Expecting value"))
        with tempfile.TemporaryDirectory() as d, patch("milestones.Redmine", return_value=bad):
            with self.assertRaises(SystemExit) as stop:
                main(["--out", f"{d}/page.html"])
        self.assertEqual(stop.exception.code, "report-milestones: Expecting value")

    def test_an_empty_answer_stops_with_one_line(self):
        fake = FakeRedmine()
        fake.send = lambda method, path: None
        with tempfile.TemporaryDirectory() as d, patch("milestones.Redmine", return_value=fake):
            with self.assertRaises(SystemExit) as stop:
                main(["--out", f"{d}/page.html"])
        self.assertIn("an empty answer", str(stop.exception.code))

    def test_a_run_writes_the_page_and_its_options(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch("milestones.Redmine", return_value=FakeRedmine()),
        ):
            self.assertEqual(main(["--out", f"{d}/page.html", "--all"]), 0)
            self.assertIn("Milestone progress", Path(d, "page.html").read_text())
            self.assertIn("--all", saved_options(Path(d) / "page.html"))


if __name__ == "__main__":
    unittest.main()
