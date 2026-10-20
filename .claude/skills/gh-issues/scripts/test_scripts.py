"""Offline tests for the gh-issues scripts. Run from scripts/: python3 -m unittest test_scripts"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from outline import check, expected
from pr_threads import emit as threads_emit, post as threads_post, read_replies, read_reply_dir, write_drafts
import checklist
import new_issue

GLOBAL = "| L1 Security | q |\n| L2 Privacy and data | q |\n"
REPO = "| L11 Invariants | q |\n- **Sections:** the global ones, then `### Notes`.\n"

GOOD = """### Task
Do the thing.
- In scope: the thing.

### Guardrails
- Out of scope: the other thing.

### Lenses
- [x] L1 Security: no secrets touched
- [ ] L2 Privacy and data
- [ ] L11 Invariants

### Done
- [ ] The thing is done.
- [x] Tests prove the thing.
- [ ] L1: no secret appears in the diff.

### Notes
Anything.
"""


class Outline(unittest.TestCase):
    def setUp(self):
        self.lenses, self.extra = expected(GLOBAL, REPO)

    def test_reads_lenses_and_sections_from_both_files(self):
        self.assertEqual(self.lenses, ["L1", "L2", "L11"])
        self.assertEqual(self.extra, ["Notes"])

    def test_conforming_body(self):
        self.assertEqual(check(GOOD, self.lenses, self.extra), [])

    def test_old_style_issue(self):
        f = check("Walk up from cwd.\n\n## Planning\n\n- Estimate: 1\n", self.lenses, self.extra)
        self.assertIn("missing section: ### Task", f)
        self.assertIn("old-style heading: ## Planning", f)

    def test_old_outline_is_named(self):
        f = check("### Summary\nx\n\n### Scope\n- y\n\n### Acceptance\n- z\n\n### Lenses\n",
                  self.lenses, self.extra)
        self.assertIn("old outline (Summary, Scope, Acceptance): rewrite to Task, Guardrails, Lenses and Done", f)

    def test_a_ticked_lens_needs_a_done_condition(self):
        f = check(GOOD.replace("- [ ] L1: no secret appears in the diff.\n", ""), self.lenses, self.extra)
        self.assertEqual(f, ["L1: [x] but no Done condition names it; add - [ ] L1: …"])

    def test_done_names_only_ticked_lenses(self):
        f = check(GOOD.replace("- [ ] L1: no secret", "- [ ] L2: no secret"), self.lenses, self.extra)
        self.assertIn("Done: L2 is named but not [x] under Lenses", f)

    def test_lenses_left_in_guardrails_are_named(self):
        f = check(GOOD.replace("\n### Lenses\n", ""), self.lenses, self.extra)
        self.assertIn("missing section: ### Lenses", f)
        self.assertIn("Guardrails: lens lines (L1, L2, L11) belong under ### Lenses", f)

    def test_lens_marks(self):
        body = GOOD.replace("- [ ] L2 Privacy and data", "- [n] L2 Privacy and data") \
                   .replace("- [x] L1 Security: no secrets touched", "- [x] L1 Security") \
                   .replace("- [ ] L11 Invariants", "")
        f = check(body, self.lenses, self.extra)
        self.assertIn("L1: [x] needs a note saying what the lens requires", f)
        self.assertTrue(any(x.startswith("L2: mark [n]") for x in f))
        self.assertIn("L11: lens missing", f)

    def test_blank_lens_with_note(self):
        f = check(GOOD.replace("- [ ] L2 Privacy and data", "- [ ] L2 Privacy and data: none"),
                  self.lenses, self.extra)
        self.assertEqual(f, ["L2: has a note but is [ ]; mark it [x] or drop the note"])

    def test_one_done_condition_is_too_few(self):
        f = check(GOOD.replace("- [x] Tests prove the thing.\n", "").replace("- [ ] The thing is done.\n", ""),
                  self.lenses, self.extra)
        self.assertEqual(len(f), 1)
        self.assertTrue(f[0].startswith("Done: 1 pass/fail condition(s); a Task needs at least 2"))

    def test_done_lines_are_checkboxes(self):
        f = check(GOOD.replace("- [ ] The thing is done.", "- The thing is done."), self.lenses, self.extra)
        self.assertIn("Done: '- The thing is done.' is not a checkbox; write each condition as - [ ] …", f)

    def test_bug_shape(self):
        bug = GOOD.replace("Do the thing.", "**Steps** run it. **Expected** y. **Actual** z.")
        self.assertEqual(check(bug, self.lenses, self.extra), [])


class Threads(unittest.TestCase):
    FOUND = [{"thread": "PRRT_1", "resolved": False, "comment": 11, "path": "a.py", "line": 3,
              "author": "Copilot", "body": "x"}]

    def test_reply_then_resolve_paced(self):
        script = threads_emit("o/r", 73, self.FOUND, read_replies("11\tFixed: it's done.\n\n"))
        self.assertIn("gh api repos/o/r/pulls/73/comments/11/replies -f body='Fixed: it'\"'\"'s done.' --silent", script)
        self.assertIn("resolveReviewThread", script)
        self.assertIn("export GH_PAGER=cat", script)
        self.assertIn("-f id=PRRT_1", script)
        self.assertEqual(script.count("sleep 1"), 1)

    def test_rejects(self):
        with self.assertRaisesRegex(ValueError, "line 1"):
            read_replies("not-a-number\treply")
        with self.assertRaisesRegex(ValueError, "line 1"):
            read_replies("11 no tab")
        with self.assertRaisesRegex(ValueError, "does not start a review thread"):
            threads_emit("o/r", 73, self.FOUND, [(99, "x")])


class PrDone(unittest.TestCase):
    """pr.sh done against a fake gh: a merged branch's worktree goes only when its ignored files are
    disposable, since git worktree remove deletes them unasked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        top = Path(self.tmp.name)
        bin = top / "bin"
        bin.mkdir()
        # gh: the default branch is main, and the branch's PR merged at the worktree's HEAD.
        (bin / "gh").write_text('#!/bin/sh\ncase "$*" in\n  "repo view"*) echo main ;;\n'
                                '  *"--json state"*) echo MERGED ;;\n  *headRefOid*) git rev-parse HEAD ;;\nesac\n')
        (bin / "security").write_text("#!/bin/sh\nexit 1\n")
        for f in bin.iterdir():
            f.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin}:{os.environ['PATH']}", "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"}
        git = lambda cwd, *args: subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True)
        git(top, "init", "-q", "--bare", "-b", "main", "origin.git")
        git(top, "clone", "-q", "origin.git", "main")
        self.main = top / "main"
        (self.main / ".gitignore").write_text("HANDOFF.md\n/.tmp\nnode_modules/\n")
        git(self.main, "add", ".")
        git(self.main, "commit", "-qm", "start")
        git(self.main, "push", "-q", "origin", "main")
        self.tree = top / "tree"
        git(self.main, "worktree", "add", "-q", "-b", "fix/9-x", str(self.tree))
        (self.tree / "node_modules" / "x").mkdir(parents=True)
        (self.tree / "node_modules" / "x" / "a.js").write_text("")
        (self.tree / ".tmp" / "9").mkdir(parents=True)
        (self.tree / ".tmp" / "9" / "notes.md").write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def pr_done(self):
        return subprocess.run(["sh", str(Path(__file__).parent / "pr.sh"), "done"], cwd=self.tree, env=self.env,
                              capture_output=True, text=True)

    def test_an_ignored_file_done_would_keep_stops_the_removal(self):
        (self.tree / "HANDOFF.md").write_text("notes")
        run = self.pr_done()
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("ignored files its removal would delete", run.stderr)
        self.assertIn("HANDOFF.md", run.stderr)
        self.assertNotIn("node_modules", run.stderr)
        self.assertNotIn(".tmp", run.stderr)
        self.assertTrue((self.tree / "HANDOFF.md").exists())

    def test_only_disposable_ignored_files_remove_the_worktree_and_branch(self):
        run = self.pr_done()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(self.tree.exists())
        branches = subprocess.run(["git", "branch"], cwd=self.main, capture_output=True, text=True).stdout
        self.assertNotIn("fix/9-x", branches)


class PostReplies(unittest.TestCase):
    """pr_threads.py --post: a folder of <comment id>.md replies, posted and resolved once each."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.calls = []
        self.found = [{"thread": "PRRT_1", "resolved": False, "comment": 11, "path": "a.py", "line": 3,
                       "author": "Copilot", "body": "x", "hunk": "@@ -1,3 +1,3 @@\n a\n-b\n+c", "replies": []}]

    def tearDown(self):
        self.tmp.cleanup()

    def send(self):
        threads_post("o/r", 73, "me", read_reply_dir(self.dir, 73, self.found), api=self.calls.append, pause=0)

    def test_posts_resolves_and_deletes(self):
        (self.dir / "11.md").write_text("Fixed: it's done.\n")
        self.send()
        self.assertEqual(self.calls[0][:2], ["api", "repos/o/r/pulls/73/comments/11/replies"])
        self.assertIn("body=Fixed: it's done.", self.calls[0])
        self.assertIn("id=PRRT_1", self.calls[1])
        self.assertFalse((self.dir / "11.md").exists())

    def test_a_reply_already_posted_is_not_posted_again(self):
        # A run stopped after the reply and before the resolve.
        self.found[0]["replies"] = [("me", "Fixed: it's done.")]
        (self.dir / "11.md").write_text("Fixed: it's done.\n")
        self.send()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("id=PRRT_1", self.calls[0])

    def test_a_resolved_thread_with_the_reply_writes_nothing(self):
        self.found[0].update(resolved=True, replies=[("me", "Fixed.")])
        (self.dir / "11.md").write_text("Fixed.")
        self.send()
        self.assertEqual(self.calls, [])
        self.assertFalse((self.dir / "11.md").exists())

    def test_someone_elses_identical_reply_still_gets_yours(self):
        self.found[0]["replies"] = [("other", "Fixed.")]
        (self.dir / "11.md").write_text("Fixed.")
        self.send()
        self.assertEqual(len(self.calls), 2)

    def test_any_bad_file_stops_before_the_first_post(self):
        (self.dir / "11.md").write_text("Fixed.")
        for name, text, why in [("99.md", "x", "does not start a review thread"),
                                ("notes.md", "x", "name it <comment id>.md"),
                                ("12.md", " \n", "has no reply"),
                                ("12.md", "<!-- context\n\nReply.", "no closing -->"),
                                ("12.md", "<!-- context -->\n", "has no reply")]:
            self.found.append({"thread": "PRRT_2", "resolved": False, "comment": 12, "path": "b.py",
                               "line": 1, "author": "Copilot", "body": "y", "replies": []})
            (self.dir / name).write_text(text)
            with self.assertRaisesRegex(ValueError, why):
                self.send()
            (self.dir / name).unlink()
        self.assertEqual(self.calls, [])
        self.assertTrue((self.dir / "11.md").exists())

    def test_drafts_carry_the_context_and_only_the_reply_is_posted(self):
        self.found[0]["body"] = "Line one.\n\nThe --> ends it."
        self.found.append({"thread": "PRRT_2", "resolved": True, "comment": 12, "path": "b.py", "line": 1,
                           "author": "Copilot", "body": "y", "hunk": "@@", "replies": []})
        (self.dir / "11.md").write_text("Fixed: it's done.\n")
        self.assertEqual(write_drafts(self.dir, self.found), [self.dir / "11.md"])
        text = (self.dir / "11.md").read_text()
        self.assertTrue(text.startswith("<!-- Context, not posted"))
        for shown in ["a.py:3, Copilot", "> Line one.\n>\n> The --&gt; ends it.", "```diff\n@@ -1,3 +1,3 @@\n a\n-b\n+c\n```"]:
            self.assertIn(shown, text)
        self.assertTrue(text.endswith("-->\n\nFixed: it's done.\n"))
        self.assertFalse((self.dir / "12.md").exists())
        # A second run leaves a drafted file as it is.
        self.assertEqual(write_drafts(self.dir, self.found), [])
        self.send()
        self.assertIn("body=Fixed: it's done.", self.calls[0])

    def test_a_long_hunk_shows_its_last_lines(self):
        self.found[0]["hunk"] = "\n".join(f"line {i}" for i in range(40))
        write_drafts(self.dir, self.found)
        text = (self.dir / "11.md").read_text()
        self.assertIn("```diff\n...\nline 25\n", text)
        self.assertNotIn("line 24\n", text)


class FakeRedmine:
    def __init__(self, description, keep=False):
        self.description_ = description
        self.keep = keep
        self.puts = []

    def description(self, n):
        return self.description_

    def send(self, method, path, body=None):
        self.puts.append((path, body))
        if not self.keep:
            self.description_ = body["issue"]["description"]


class Checklist(unittest.TestCase):
    """checklist.py: only checkbox state may change."""

    ISSUE = "### Done\r\n- [ ] Its sub-issues are closed.\r\n- [x] L12: offline.\r\n* [ ] L7: the buttons."

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "issue.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_ticks_only_the_boxes_keeping_line_endings_and_notes_each(self):
        self.file.write_text("### Done\n- [x] Its sub-issues are closed.\n- [x] L12: offline.\n* [ ] L7: the buttons.\n")
        redmine = FakeRedmine(self.ISSUE)
        self.assertEqual(checklist.run(79, self.file, redmine), "#79: 1 box changed")
        path, body = redmine.puts[0]
        self.assertEqual(path, "/issues/79.json")
        self.assertEqual(body["issue"]["description"], self.ISSUE.replace("- [ ] Its", "- [x] Its"))
        self.assertIn("* Ticked: Its sub-issues are closed.", body["issue"]["notes"])
        self.assertFalse(self.file.exists())

    def test_unticks_too(self):
        self.file.write_text(self.ISSUE.replace("[x] L12", "[ ] L12"))
        redmine = FakeRedmine(self.ISSUE)
        checklist.run(79, self.file, redmine)
        self.assertIn("* Unticked: L12: offline.", redmine.puts[0][1]["issue"]["notes"])

    def test_no_box_to_change_writes_nothing_and_deletes_the_file(self):
        self.file.write_text(self.ISSUE)
        redmine = FakeRedmine(self.ISSUE)
        self.assertEqual(checklist.run(79, self.file, redmine), "#79: no box to change")
        self.assertEqual(redmine.puts, [])
        self.assertFalse(self.file.exists())

    def test_any_other_difference_is_refused_and_the_file_kept(self):
        for wanted in [self.ISSUE.replace("closed.", "closed, all three."),
                       self.ISSUE + "\n- [ ] A new line.",
                       self.ISSUE.rsplit("\r\n", 1)[0]]:
            self.file.write_text(wanted)
            redmine = FakeRedmine(self.ISSUE)
            with self.assertRaisesRegex(checklist.Refused, "differs by more than its box"):
                checklist.run(79, self.file, redmine)
            self.assertEqual(redmine.puts, [])
            self.assertTrue(self.file.exists())

    def test_a_description_redmine_kept_is_refused_and_the_file_kept(self):
        self.file.write_text(self.ISSUE.replace("- [ ] Its", "- [x] Its"))
        with self.assertRaisesRegex(checklist.Refused, "kept #79's old description"):
            checklist.run(79, self.file, FakeRedmine(self.ISSUE, keep=True))
        self.assertTrue(self.file.exists())


if __name__ == "__main__":
    unittest.main()


class FakeTracker:
    """A Redmine answering new_issue.py's reads, keeping what it posts."""

    url = "http://redmine.test"

    def __init__(self, drop_status=False):
        self.issues = {79: {"id": 79, "subject": "Older", "relations": []}}
        self.posts = []
        self.drop_status = drop_status

    def send(self, method, path, body=None):
        path, _, query = path.partition("?")
        if method == "POST":
            self.posts.append((path, body))
            if path == "/issues.json":
                n = max(self.issues) + 1
                self.issues[n] = {"id": n, "relations": [], **body["issue"]}
                if self.drop_status:
                    self.issues[n]["status_id"] = 1
                return {"issue": {"id": n}}
            n = int(path.split("/")[2])
            rel = body["relation"]
            self.issues[n]["relations"].append({"issue_id": n, "issue_to_id": rel["issue_to_id"], "relation_type": rel["relation_type"]})
            return None
        if path == "/projects/gh-plan.json":
            return {"project": {"id": 2, "trackers": [{"id": 1, "name": "Bug"}, {"id": 2, "name": "Feature"}]}}
        if path == "/projects/gh-plan/versions.json":
            return {"versions": [{"id": 2, "name": "Redmine migration"}]}
        if path == "/issue_statuses.json":
            return {"issue_statuses": [{"id": 1, "name": "New"}, {"id": 7, "name": "Icebox"}]}
        if path == "/custom_fields.json":
            return {"custom_fields": [
                {"id": 1, "name": "Points", "customized_type": "issue", "possible_values": [{"value": v} for v in "1 2 3 5 8 13".split()]},
                {"id": 2, "name": "Lane", "customized_type": "issue", "possible_values": [{"value": "infra"}]},
                {"id": 6, "name": "Ordinal", "customized_type": "issue"},
                {"id": 9, "name": "Points", "customized_type": "project"},
            ]}
        if path == "/issues.json":
            listed = [{"id": i["id"], "subject": i["subject"]} for i in self.issues.values()]
            return {"issues": listed, "total_count": len(listed)}
        n = int(path.split("/")[2].removesuffix(".json"))
        if n not in self.issues:
            raise checklist.Refused(f"Redmine GET {path}: HTTP 404")
        i = self.issues[n]
        return {"issue": {"id": n, "subject": i["subject"], "project": {"id": i.get("project_id", 2)},
                          "tracker": {"id": i.get("tracker_id", 2)}, "status": {"id": i.get("status_id", 1)},
                          "fixed_version": {"id": i["fixed_version_id"]} if "fixed_version_id" in i else None,
                          "custom_fields": i.get("custom_fields", []), "relations": i["relations"]}}


class NewIssue(unittest.TestCase):
    """new_issue.py: every name checked before a write, a second run makes nothing new, and a dropped field is named."""

    HEAD = "---\nproject: gh-plan\ntracker: Feature\nsubject: Retire it\nversion: Redmine migration\nstatus: Icebox\npoints: 3\nlane: infra\nordinal: 350\nparent:\nrelates: #79\n---\n\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "issue.md"
        self.lenses, self.extra = expected(GLOBAL, REPO)

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, redmine, head=None, check_only=False):
        self.file.write_text((head or self.HEAD) + GOOD)
        return new_issue.run(self.file, redmine, check_only, self.lenses, self.extra)

    def test_creates_relates_and_reads_back(self):
        r = FakeTracker()
        self.assertEqual(self.run_it(r), "#80 created, 1 relation added: http://redmine.test/issues/80")
        issue = r.posts[0][1]["issue"]
        self.assertEqual((issue["project_id"], issue["tracker_id"], issue["fixed_version_id"], issue["status_id"]), (2, 2, 2, 7))
        self.assertEqual(issue["custom_fields"], [{"id": 1, "value": "3"}, {"id": 2, "value": "infra"}, {"id": 6, "value": "350"}])
        self.assertTrue(issue["description"].startswith("### Task\n"))
        self.assertNotIn("parent_issue_id", issue)
        self.assertEqual(r.posts[1], ("/issues/80/relations.json", {"relation": {"issue_to_id": 79, "relation_type": "relates"}}))

    def test_a_second_run_makes_nothing_and_a_stopped_one_is_finished(self):
        r = FakeTracker()
        self.run_it(r)
        self.assertEqual(self.run_it(r), "#80 already there, 0 relations added: http://redmine.test/issues/80")
        self.assertEqual(len(r.posts), 2)
        r.issues[80]["relations"] = []
        self.assertEqual(self.run_it(r), "#80 already there, 1 relation added: http://redmine.test/issues/80")

    def test_check_writes_nothing(self):
        r = FakeTracker()
        self.assertIn("a run creates it", self.run_it(r, check_only=True))
        self.assertEqual(r.posts, [])

    def test_blocked_by_is_a_blocked_relation(self):
        r = FakeTracker()
        self.run_it(r, self.HEAD.replace("relates: #79", "blocked_by: 79"))
        self.assertEqual(r.posts[1][1], {"relation": {"issue_to_id": 79, "relation_type": "blocked"}})

    def test_bad_fields_refuse_before_any_write(self):
        for old, new, says in [
            ("points: 3", "points: 4", "isn't one of 1, 2, 3"),
            ("lane: infra", "lane: ui", "isn't one of infra"),
            ("tracker: Feature", "tracker: chore", "no tracker 'chore'"),
            ("version: Redmine migration", "version: Phase 9", "no version 'Phase 9'"),
            ("status: Icebox", "status: Done", "no status 'Done'"),
            ("ordinal: 350", "ordinal: soon", "isn't a number"),
            ("relates: #79", "relates: #404", "HTTP 404"),
            ("relates: #79", "relates: next", "isn't an issue number"),
            ("parent:", "parent: 79, 80", "one issue number"),
            ("subject: Retire it", "subject:", "subject is blank"),
            ("ordinal: 350", "estimate: 3", "isn't one of project"),
        ]:
            r = FakeTracker()
            with self.subTest(new), self.assertRaisesRegex(checklist.Refused, says):
                self.run_it(r, self.HEAD.replace(old, new))
            self.assertEqual(r.posts, [])

    def test_a_body_off_the_outline_refuses_before_any_write(self):
        r = FakeTracker()
        self.file.write_text(self.HEAD + "### Task\nDo it.\n")
        with self.assertRaisesRegex(checklist.Refused, "missing section: ### Done"):
            new_issue.run(self.file, r, False, self.lenses, self.extra)
        self.assertEqual(r.posts, [])

    def test_a_status_redmine_dropped_is_named(self):
        with self.assertRaisesRegex(checklist.Refused, "#80 is created, but Redmine didn't keep: status_id"):
            self.run_it(FakeTracker(drop_status=True))
