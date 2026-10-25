"""Offline tests for the gh-issues scripts. Run from scripts/: python3 -m unittest test_scripts"""

import io
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

import issue_body
import issue_relations
import new_issue
import redmine_set
from outline import check, expected
from pr_threads import (
    emit as threads_emit,
    post as threads_post,
    read_replies,
    read_reply_dir,
    write_drafts,
)
from redmine import Refused, every, reasons, unkept

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
- [ ] Tests prove the thing.
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
        f = check(
            "### Summary\nx\n\n### Scope\n- y\n\n### Acceptance\n- z\n\n### Lenses\n",
            self.lenses,
            self.extra,
        )
        self.assertIn(
            "old outline (Summary, Scope, Acceptance): rewrite to Task, Guardrails, Lenses and Done",
            f,
        )

    def test_a_ticked_lens_needs_a_done_condition(self):
        f = check(
            GOOD.replace("- [ ] L1: no secret appears in the diff.\n", ""), self.lenses, self.extra
        )
        self.assertEqual(f, ["L1: [x] but no Done condition names it; add - [ ] L1: …"])

    def test_done_names_only_ticked_lenses(self):
        f = check(
            GOOD.replace("- [ ] L1: no secret", "- [ ] L2: no secret"), self.lenses, self.extra
        )
        self.assertIn("Done: L2 is named but not [x] under Lenses", f)

    def test_lenses_left_in_guardrails_are_named(self):
        f = check(GOOD.replace("\n### Lenses\n", ""), self.lenses, self.extra)
        self.assertIn("missing section: ### Lenses", f)
        self.assertIn("Guardrails: lens lines (L1, L2, L11) belong under ### Lenses", f)

    def test_lens_marks(self):
        body = (
            GOOD.replace("- [ ] L2 Privacy and data", "- [n] L2 Privacy and data")
            .replace("- [x] L1 Security: no secrets touched", "- [x] L1 Security")
            .replace("- [ ] L11 Invariants", "")
        )
        f = check(body, self.lenses, self.extra)
        self.assertIn("L1: [x] needs a note saying what the lens requires", f)
        self.assertTrue(any(x.startswith("L2: mark [n]") for x in f))
        self.assertIn("L11: lens missing", f)

    def test_blank_lens_with_note(self):
        f = check(
            GOOD.replace("- [ ] L2 Privacy and data", "- [ ] L2 Privacy and data: none"),
            self.lenses,
            self.extra,
        )
        self.assertEqual(f, ["L2: has a note but is [ ]; mark it [x] or drop the note"])

    def test_one_done_condition_is_too_few(self):
        f = check(
            GOOD.replace("- [ ] Tests prove the thing.\n", "").replace(
                "- [ ] The thing is done.\n", ""
            ),
            self.lenses,
            self.extra,
        )
        self.assertEqual(len(f), 1)
        self.assertTrue(f[0].startswith("Done: 1 pass/fail condition(s); a Task needs at least 2"))

    def test_done_lines_are_checkboxes(self):
        f = check(
            GOOD.replace("- [ ] The thing is done.", "- The thing is done."),
            self.lenses,
            self.extra,
        )
        self.assertIn(
            "Done: '- The thing is done.' is not a checkbox; write each condition as - [ ] …", f
        )

    def test_a_ticked_done_line_is_named(self):
        f = check(
            GOOD.replace("- [ ] The thing is done.", "- [x] The thing is done."),
            self.lenses,
            self.extra,
        )
        self.assertEqual(
            f,
            [
                "Done: 'The thing is done.' is ticked; an issue's description is never ticked, the PR body is"
            ],
        )

    def test_bug_shape(self):
        bug = GOOD.replace("Do the thing.", "**Steps** run it. **Expected** y. **Actual** z.")
        self.assertEqual(check(bug, self.lenses, self.extra), [])


class Threads(unittest.TestCase):
    FOUND = [
        {
            "thread": "PRRT_1",
            "resolved": False,
            "comment": 11,
            "path": "a.py",
            "line": 3,
            "author": "Copilot",
            "body": "x",
        }
    ]

    def test_reply_then_resolve_paced(self):
        script = threads_emit("o/r", 73, self.FOUND, read_replies("11\tFixed: it's done.\n\n"))
        self.assertIn(
            "gh api repos/o/r/pulls/73/comments/11/replies -f body='Fixed: it'\"'\"'s done.' --silent",
            script,
        )
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
    """pr.sh after the merge, against a fake gh: it leaves the cleanup to Done, which checks what
    git would delete or overwrite."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        top = Path(self.tmp.name)
        bin = top / "bin"
        bin.mkdir()
        self.state = top / "state"
        self.state.write_text("MERGED\n")
        self.head = top / "head"
        self.calls = top / "calls"
        (bin / "gh").write_text(
            f'#!/bin/sh\necho "$*" >> "{self.calls}"\ncase "$*" in\n  "repo view"*) echo main ;;\n'
            f'  *"--json state"*) cat "{self.state}" ;;\n'
            f'  *"--json headRefOid"*) cat "{self.head}" ;;\nesac\n'
        )
        (bin / "gh").chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{bin}:{os.environ['PATH']}",
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }

        def git(cwd, *args):
            return subprocess.run(
                ["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True
            )

        self.main = top / "main"
        git(top, "init", "-q", "-b", "main", "main")
        git(self.main, "commit", "-q", "--allow-empty", "-m", "start")
        self.tree = top / "tree"
        git(self.main, "worktree", "add", "-q", "-b", "fix/9-x", str(self.tree))
        (self.main / ".git" / "info" / "exclude").write_text("HANDOFF.md\n.tmp\n")
        (self.tree / "HANDOFF.md").write_text("notes")
        self.head.write_text(git(self.tree, "rev-parse", "HEAD").stdout.decode())

    def tearDown(self):
        self.tmp.cleanup()

    def pr(self, *args):
        return subprocess.run(
            ["sh", str(Path(__file__).parent / "pr.sh"), *args],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def test_a_merged_pr_points_to_done_and_removes_nothing(self):
        for args in [(), ("done",)]:
            run = self.pr(*args)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("press Done on #9 in Next up", run.stdout)
            self.assertTrue((self.tree / "HANDOFF.md").exists())
        branches = subprocess.run(
            ["git", "branch"], cwd=self.main, capture_output=True, text=True
        ).stdout
        self.assertIn("fix/9-x", branches)

    def test_a_merged_pr_refuses_work_it_lacks(self):
        (self.tree / "new.txt").write_text("unpushed")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("has work it lacks", run.stderr)
        self.assertIn("new.txt", run.stderr)
        (self.tree / "new.txt").unlink()
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "more"],
            cwd=self.tree,
            env=self.env,
            check=True,
        )
        self.assertEqual(self.pr("done").returncode, 2)

    def test_a_merged_pr_gets_the_body_so_a_late_done_tick_reaches_it(self):
        body = self.tree / ".tmp" / "9" / "pr-body.md"
        body.parent.mkdir(parents=True)
        body.write_text("Fixes #9.\n\n## Done\n\n- [x] Checked after the merge.\n")
        run = self.pr("done")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("pb 9", self.calls.read_text().splitlines())
        self.assertTrue(body.exists())

    def test_an_old_issue_md_is_named_as_no_longer_sent_and_left(self):
        old = self.tree / ".tmp" / "9" / "issue.md"
        old.parent.mkdir(parents=True)
        old.write_text("- [x] Done\n")
        run = self.pr("done")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(".tmp/9/issue.md is no longer sent", run.stderr)
        self.assertEqual(old.read_text(), "- [x] Done\n")

    def test_pr_done_refuses_a_pr_that_has_not_merged(self):
        self.state.write_text("OPEN\n")
        run = self.pr("done")
        self.assertEqual(run.returncode, 2)
        self.assertIn("has not merged", run.stderr)


class PrStaged(unittest.TestCase):
    """pr.sh from Ship, which sets PR_STAGED: it commits what Go staged, and nothing changed since.
    PrDone's fixture, with the PR open."""

    repo, tearDown, pr = PrDone.setUp, PrDone.tearDown, PrDone.pr

    def setUp(self):
        self.repo()
        self.state.write_text("OPEN\n")
        self.env["PR_STAGED"] = "1"
        message = self.tree / ".tmp" / "9" / "commit.txt"
        message.parent.mkdir(parents=True)
        message.write_text("Reviewed (fixes #9)\n")
        (self.tree / "reviewed.txt").write_text("staged by Go")
        subprocess.run(["git", "add", "-A"], cwd=self.tree, check=True)

    def committed(self):
        return subprocess.run(
            ["git", "show", "--name-only", "--format=%s", "HEAD"],
            cwd=self.tree,
            capture_output=True,
            text=True,
        ).stdout.split()

    def test_a_change_made_after_go_is_refused_and_nothing_is_committed(self):
        (self.tree / "reviewed.txt").write_text("edited after Go")
        (self.tree / "late.txt").write_text("new after Go")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("changed since Go staged the tree", run.stderr)
        self.assertIn("late.txt\n", run.stderr)
        self.assertIn("reviewed.txt\n", run.stderr)
        self.assertEqual(self.committed(), ["start"])

    def test_what_go_staged_is_committed(self):
        self.pr()  # The fetch then fails: the fixture has no origin.
        self.assertEqual(self.committed(), ["Reviewed", "(fixes", "#9)", "reviewed.txt"])


class PrRebase(unittest.TestCase):
    """pr.sh with an open PR whose branch was pushed: it rebases onto origin's main before the push,
    against a bare origin, a fake gh and a second clone that pushes behind its back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        top = Path(self.tmp.name)
        bin = top / "bin"
        bin.mkdir()
        self.calls = top / "calls"
        self.calls.touch()
        (bin / "gh").write_text(
            f'#!/bin/sh\necho "$*" >> "{self.calls}"\ncase "$*" in\n  "repo view"*) echo main ;;\n'
            '  *"--json state"*) echo OPEN ;;\n'
            '  *"--json url"*) echo https://github.com/o/r/pull/1 ;;\nesac\n'
        )
        (bin / "open").write_text("#!/bin/sh\n")
        # A race script, when present, runs just before pr's push: after its fetch, before the lease.
        self.race = top / "race"
        self.real_git = shutil.which("git")
        # A race that fails makes the push fail.
        (bin / "git").write_text(
            f'#!/bin/sh\n[ "$1" != push ] || [ ! -x "{self.race}" ] || "{self.race}" || exit\n'
            f'exec "{self.real_git}" "$@"\n'
        )
        for tool in ("gh", "open", "git"):
            (bin / tool).chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{bin}:{os.environ['PATH']}",
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }
        self.origin = top / "origin.git"
        self.git(top, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.other = top / "other"
        self.git(top, "clone", "-q", str(self.origin), str(self.other))
        self.commit(self.other, "a.txt", "one\n", "start")
        self.git(self.other, "push", "-q", "origin", "HEAD:main")
        self.tree = top / "tree"
        self.git(top, "clone", "-q", str(self.origin), str(self.tree))
        (self.tree / ".git" / "info" / "exclude").write_text(".tmp\n")
        self.git(self.tree, "checkout", "-q", "-b", "feat/9-x")
        self.commit(self.tree, "a.txt", "one\nbranch\n", "Work (fixes #9)")
        self.git(self.tree, "push", "-q", "-u", "origin", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, cwd, name, text, message):
        (cwd / name).write_text(text)
        self.git(cwd, "add", "-A")
        self.git(cwd, "commit", "-q", "-m", message)
        return self.git(cwd, "rev-parse", "HEAD")

    def on_origin(self, ref):
        return self.git(self.origin, "rev-parse", ref)

    def advance_main(self, name="c.txt", text="main\n"):
        self.git(self.other, "pull", "-q", "origin", "main")
        tip = self.commit(self.other, name, text, "Main moves on")
        self.git(self.other, "push", "-q", "origin", "HEAD:main")
        return tip

    def pr(self):
        return subprocess.run(
            ["sh", str(Path(__file__).parent / "pr.sh")],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def test_a_branch_behind_main_is_rebased_and_pushed_with_no_merge(self):
        tip = self.advance_main()
        local_main = self.git(self.tree, "rev-parse", "main")
        run = self.pr()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f"rebased onto {tip[:7]}", run.stdout)
        self.assertEqual(self.on_origin("feat/9-x"), self.git(self.tree, "rev-parse", "HEAD"))
        self.assertEqual(self.on_origin("feat/9-x^"), tip)
        self.assertEqual(self.git(self.origin, "rev-list", "--merges", "main..feat/9-x"), "")
        # Invariant 2: main, on origin and in the clone, is never written.
        self.assertEqual(self.on_origin("main"), tip)
        self.assertEqual(self.git(self.tree, "rev-parse", "main"), local_main)
        self.assertIn("pb 9", self.calls.read_text().splitlines())
        self.assertNotIn("copilot", self.calls.read_text())

    def test_a_rebase_with_a_new_commit_pushes_it(self):
        self.advance_main()
        message = self.tree / ".tmp" / "9" / "commit.txt"
        message.parent.mkdir(parents=True)
        message.write_text("More\n")
        (self.tree / "b.txt").write_text("more\n")
        run = self.pr()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("rebased onto", run.stdout)
        self.assertEqual(self.on_origin("feat/9-x"), self.git(self.tree, "rev-parse", "HEAD"))
        self.assertEqual(self.git(self.tree, "log", "-1", "--format=%s"), "More")

    def test_a_branch_on_mains_tip_is_pushed_without_a_rebase_or_force(self):
        message = self.tree / ".tmp" / "9" / "commit.txt"
        message.parent.mkdir(parents=True)
        message.write_text("More\n")
        (self.tree / "b.txt").write_text("more\n")
        before = self.git(self.tree, "rev-parse", "HEAD")
        run = self.pr()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("already on main", run.stdout)
        self.assertNotIn("rebased", run.stdout)
        self.assertNotIn("replacing", run.stdout)
        self.assertEqual(self.git(self.tree, "rev-parse", "HEAD^"), before)
        self.assertEqual(self.on_origin("feat/9-x"), self.git(self.tree, "rev-parse", "HEAD"))

    def test_a_conflicting_rebase_is_aborted_and_nothing_is_pushed(self):
        self.advance_main("a.txt", "one\nmain\n")
        before = self.git(self.tree, "rev-parse", "HEAD")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("  a.txt\n", run.stderr)
        self.assertIn("git rebase origin/main", run.stderr)
        self.assertEqual(self.git(self.tree, "rev-parse", "HEAD"), before)
        self.assertEqual(self.git(self.tree, "status", "--porcelain"), "")
        self.assertFalse((self.tree / ".git" / "rebase-merge").exists())
        self.assertEqual(self.on_origin("feat/9-x"), before)
        self.assertNotIn("pb 9", self.calls.read_text().splitlines())
        # Rebased by hand as the message says: the copy's context differs, so pr refuses it by patch and
        # names the push that replaces GitHub's, leased on its head.
        subprocess.run(
            ["git", "rebase", "origin/main"], cwd=self.tree, env=self.env, capture_output=True
        )
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("a rebase is in progress", run.stderr)
        (self.tree / "a.txt").write_text("one\nmain\nbranch\n")
        self.git(self.tree, "add", "a.txt")
        self.git(self.tree, "-c", "core.editor=true", "rebase", "--continue")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertEqual(self.on_origin("feat/9-x"), before)
        lease = f"--force-with-lease=refs/heads/feat/9-x:{before}"
        self.assertIn(lease, run.stderr)
        self.git(self.tree, "push", "-q", lease, "origin", "HEAD")
        run = self.pr()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("already on main", run.stdout)
        self.assertEqual(self.on_origin("feat/9-x"), self.git(self.tree, "rev-parse", "HEAD"))

    def test_a_conflicting_rebase_on_the_apply_backend_is_aborted(self):
        self.git(self.tree, "config", "rebase.backend", "apply")
        self.advance_main("a.txt", "one\nmain\n")
        before = self.git(self.tree, "rev-parse", "HEAD")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("so it is aborted", run.stderr)
        self.assertEqual(self.git(self.tree, "rev-parse", "HEAD"), before)
        self.assertFalse((self.tree / ".git" / "rebase-apply").exists())

    def test_another_clones_push_is_refused_and_stays_on_github(self):
        self.advance_main()
        self.git(self.other, "fetch", "-q", "origin")
        self.git(self.other, "checkout", "-q", "-b", "feat/9-x", "origin/feat/9-x")
        theirs = self.commit(self.other, "d.txt", "theirs\n", "From elsewhere")
        self.git(self.other, "push", "-q", "origin", "HEAD:feat/9-x")
        before = self.git(self.tree, "rev-parse", "HEAD")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("has commits feat/9-x lacks", run.stderr)
        self.assertIn("From elsewhere", run.stderr)
        self.assertEqual(self.on_origin("feat/9-x"), theirs)
        self.assertEqual(self.git(self.tree, "rev-parse", "HEAD"), before)

    def test_a_push_from_elsewhere_after_the_fetch_fails_the_lease(self):
        self.advance_main()
        self.git(self.other, "fetch", "-q", "origin")
        self.git(self.other, "checkout", "-q", "-b", "feat/9-x", "origin/feat/9-x")
        theirs = self.commit(self.other, "d.txt", "theirs\n", "From elsewhere")
        # After pr's fetch and check, before its push: only the lease can catch it, and a bare
        # --force would overwrite it.
        self.race.write_text(
            f"#!/bin/sh\n'{self.real_git}' -C '{self.other}' push -q origin HEAD:feat/9-x\n"
        )
        self.race.chmod(0o755)
        run = self.pr()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("rebased onto", run.stdout)
        self.assertIn("stale info", run.stderr)
        self.assertEqual(self.on_origin("feat/9-x"), theirs)

    def test_a_pushed_commit_dropped_by_a_reset_is_refused(self):
        self.advance_main()
        pushed = self.on_origin("feat/9-x")
        self.git(self.tree, "reset", "-q", "--hard", "HEAD^")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("Work (fixes #9)", run.stderr)
        self.assertIn(
            f"git push --force-with-lease=refs/heads/feat/9-x:{pushed} origin HEAD", run.stderr
        )
        self.assertEqual(self.on_origin("feat/9-x"), pushed)

    def test_a_rebase_that_fails_without_a_conflict_says_why(self):
        self.advance_main()
        hook = self.tree / ".git" / "hooks" / "pre-rebase"
        hook.write_text("#!/bin/sh\necho 'pre-rebase hook says no' >&2\nexit 1\n")
        hook.chmod(0o755)
        before = self.git(self.tree, "rev-parse", "HEAD")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("  pre-rebase hook says no", run.stderr)
        self.assertEqual(self.git(self.tree, "rev-parse", "HEAD"), before)
        self.assertEqual(self.on_origin("feat/9-x"), before)

    def test_a_rebase_whose_push_failed_is_refused_with_a_leased_push(self):
        # Main edits a line beside the branch's, so the rebased copy's patch-id differs.
        lines = "".join(f"{i}\n" for i in range(1, 10))
        self.advance_main("g.txt", lines)
        self.git(self.tree, "fetch", "-q", "origin")
        self.git(self.tree, "reset", "-q", "--hard", "origin/main")
        original = self.commit(
            self.tree, "g.txt", lines.replace("7\n", "seven\n"), "Work (fixes #9)"
        )
        self.git(self.tree, "push", "-q", "--force", "origin", "HEAD")
        self.advance_main("g.txt", lines.replace("5\n", "five\n"))
        self.race.write_text("#!/bin/sh\nexit 1\n")
        self.race.chmod(0o755)
        run = self.pr()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("rebased onto", run.stdout)
        self.assertEqual(self.on_origin("feat/9-x"), original)

        def patch_id(rev):
            show = self.git(self.tree, "show", rev) + "\n"
            return subprocess.run(
                ["git", "patch-id"], input=show, capture_output=True, text=True
            ).stdout.split()[0]

        self.assertNotEqual(patch_id("HEAD"), patch_id(original))
        # The next pr can't tell the copy from a commit that replaced GitHub's, so it refuses, and names
        # a push leased on GitHub's head, which a fetch since can't widen.
        self.race.unlink()
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertEqual(self.on_origin("feat/9-x"), original)
        lease = f"--force-with-lease=refs/heads/feat/9-x:{original}"
        self.assertIn(lease, run.stderr)
        self.git(self.tree, "push", "-q", lease, "origin", "HEAD")
        run = self.pr()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.on_origin("feat/9-x"), self.git(self.tree, "rev-parse", "HEAD"))

    def test_githubs_update_with_rebase_is_not_taken_for_lost_commits(self):
        self.advance_main()
        self.git(self.other, "fetch", "-q", "origin")
        self.git(self.other, "checkout", "-q", "-b", "feat/9-x", "origin/feat/9-x")
        self.git(self.other, "rebase", "-q", "origin/main")
        self.git(self.other, "push", "-q", "--force", "origin", "HEAD:feat/9-x")
        run = self.pr()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.on_origin("feat/9-x^"), self.on_origin("main"))

    def test_a_merge_in_progress_is_refused_before_the_commit(self):
        head = self.git(self.tree, "rev-parse", "HEAD")
        (self.tree / ".git" / "MERGE_HEAD").write_text(head + "\n")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("a merge is in progress", run.stderr)

    def test_a_merge_on_github_with_edits_of_its_own_is_refused(self):
        self.advance_main()
        self.git(self.other, "fetch", "-q", "origin")
        self.git(self.other, "checkout", "-q", "-b", "feat/9-x", "origin/feat/9-x")
        self.git(self.other, "merge", "-q", "--no-commit", "origin/main")
        (self.other / "d.txt").write_text("fixed in the merge\n")
        self.git(self.other, "add", "d.txt")
        self.git(self.other, "commit", "-q", "-m", "Merge main")
        self.git(self.other, "push", "-q", "origin", "HEAD:feat/9-x")
        merge = self.on_origin("feat/9-x")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("Merge main", run.stderr)
        self.assertEqual(self.on_origin("feat/9-x"), merge)

    def test_an_ignored_file_main_starts_tracking_is_refused_before_the_rebase(self):
        (self.tree / ".git" / "info" / "exclude").write_text("local.cfg\n")
        (self.tree / "local.cfg").write_text("mine\n")
        self.advance_main("local.cfg", "main's\n")
        before = self.git(self.tree, "rev-parse", "HEAD")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("  local.cfg\n", run.stderr)
        self.assertEqual((self.tree / "local.cfg").read_text(), "mine\n")
        self.assertEqual(self.git(self.tree, "rev-parse", "HEAD"), before)

    def test_an_ignored_file_at_a_path_main_renames_to_is_refused(self):
        self.advance_main("old.cfg", "main's\n")
        self.git(self.tree, "fetch", "-q", "origin")
        self.git(self.tree, "rebase", "-q", "origin/main")
        self.git(self.tree, "push", "-q", "-f", "origin", "HEAD")
        self.git(self.other, "mv", "old.cfg", "local.cfg")
        self.git(self.other, "commit", "-q", "-m", "Rename")
        self.git(self.other, "push", "-q", "origin", "HEAD:main")
        (self.tree / ".git" / "info" / "exclude").write_text("local.cfg\n")
        (self.tree / "local.cfg").write_text("mine\n")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("  local.cfg\n", run.stderr)
        self.assertEqual((self.tree / "local.cfg").read_text(), "mine\n")

    def test_an_ignored_file_at_a_non_ascii_path_main_adds_is_refused(self):
        (self.tree / ".git" / "info" / "exclude").write_text("é.cfg\n")
        (self.tree / "é.cfg").write_text("mine\n")
        self.advance_main("é.cfg", "main's\n")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("  é.cfg\n", run.stderr)
        self.assertEqual((self.tree / "é.cfg").read_text(), "mine\n")

    def test_a_file_the_branch_and_main_both_add_is_rebased(self):
        self.commit(self.tree, "x.txt", "same\n", "Add x")
        self.git(self.tree, "push", "-q", "origin", "HEAD")
        tip = self.advance_main("x.txt", "same\n")
        run = self.pr()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f"rebased onto {tip[:7]}", run.stdout)

    def test_a_local_merge_is_refused_before_the_rebase(self):
        self.advance_main()
        self.git(self.tree, "fetch", "-q", "origin")
        self.git(self.tree, "merge", "-q", "--no-commit", "origin/main")
        (self.tree / "d.txt").write_text("fixed in the merge\n")
        self.git(self.tree, "add", "d.txt")
        self.git(self.tree, "commit", "-q", "-m", "Merge main")
        self.git(self.tree, "push", "-q", "origin", "HEAD")
        self.advance_main("e.txt")
        merge = self.git(self.tree, "rev-parse", "HEAD")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("Merge main", run.stderr)
        self.assertEqual(self.git(self.tree, "rev-parse", "HEAD"), merge)
        self.assertEqual(self.on_origin("feat/9-x"), merge)

    def test_an_unreachable_origin_stops_before_the_push(self):
        self.advance_main()
        self.git(self.tree, "remote", "set-url", "origin", str(self.origin.parent / "gone.git"))
        before = self.git(self.tree, "rev-parse", "HEAD")
        run = self.pr()
        self.assertEqual(run.returncode, 2)
        self.assertIn("couldn't fetch origin", run.stderr)
        self.assertIn("gone.git", run.stderr)
        self.assertEqual(self.git(self.tree, "rev-parse", "HEAD"), before)
        self.assertNotIn("pb 9", self.calls.read_text().splitlines())


class PostReplies(unittest.TestCase):
    """pr_threads.py --post: a folder of <comment id>.md replies, posted and resolved once each."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.calls = []
        self.found = [
            {
                "thread": "PRRT_1",
                "resolved": False,
                "comment": 11,
                "path": "a.py",
                "line": 3,
                "author": "Copilot",
                "body": "x",
                "hunk": "@@ -1,3 +1,3 @@\n a\n-b\n+c",
                "replies": [],
            }
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def send(self):
        threads_post(
            "o/r",
            73,
            "me",
            read_reply_dir(self.dir, 73, self.found),
            api=self.calls.append,
            pause=0,
        )

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
        for name, text, why in [
            ("99.md", "x", "does not start a review thread"),
            ("notes.md", "x", "name it <comment id>.md"),
            ("12.md", " \n", "has no reply"),
            ("12.md", "<!-- context\n\nReply.", "no closing -->"),
            ("12.md", "<!-- context -->\n", "has no reply"),
        ]:
            self.found.append(
                {
                    "thread": "PRRT_2",
                    "resolved": False,
                    "comment": 12,
                    "path": "b.py",
                    "line": 1,
                    "author": "Copilot",
                    "body": "y",
                    "replies": [],
                }
            )
            (self.dir / name).write_text(text)
            with self.assertRaisesRegex(ValueError, why):
                self.send()
            (self.dir / name).unlink()
        self.assertEqual(self.calls, [])
        self.assertTrue((self.dir / "11.md").exists())

    def test_drafts_carry_the_context_and_only_the_reply_is_posted(self):
        self.found[0]["body"] = "Line one.\n\nThe --> ends it."
        self.found.append(
            {
                "thread": "PRRT_2",
                "resolved": True,
                "comment": 12,
                "path": "b.py",
                "line": 1,
                "author": "Copilot",
                "body": "y",
                "hunk": "@@",
                "replies": [],
            }
        )
        (self.dir / "11.md").write_text("Fixed: it's done.\n")
        self.assertEqual(write_drafts(self.dir, self.found), [self.dir / "11.md"])
        text = (self.dir / "11.md").read_text()
        self.assertTrue(text.startswith("<!-- Context, not posted"))
        for shown in [
            "a.py:3, Copilot",
            "> Line one.\n>\n> The --&gt; ends it.",
            "```diff\n@@ -1,3 +1,3 @@\n a\n-b\n+c\n```",
        ]:
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
            self.issues[n]["relations"].append(
                {
                    "issue_id": n,
                    "issue_to_id": rel["issue_to_id"],
                    "relation_type": rel["relation_type"],
                }
            )
            return None
        if path == "/projects/gh-plan.json":
            return {
                "project": {
                    "id": 2,
                    "trackers": [{"id": 1, "name": "Bug"}, {"id": 2, "name": "Feature"}],
                }
            }
        if path == "/projects.json":
            # gh-plan on the second page, so its match needs every page.
            listed = [
                {"id": 1, "identifier": "plan", "homepage": ""},
                {
                    "id": 3,
                    "identifier": "tempotag",
                    "homepage": "https://github.com/example/tempotag",
                },
                {
                    "id": 2,
                    "identifier": "gh-plan",
                    "homepage": "https://github.com/example/gh-plan",
                },
            ]
            offset = int(urllib.parse.parse_qs(query).get("offset", ["0"])[0])
            return {"projects": listed[offset : offset + 2], "total_count": len(listed)}
        if path == "/projects/gh-plan/versions.json":
            return {"versions": [{"id": 2, "name": "Redmine migration"}]}
        if path == "/issue_statuses.json":
            return {"issue_statuses": [{"id": 1, "name": "New"}, {"id": 7, "name": "Icebox"}]}
        if path == "/custom_fields.json":
            return {
                "custom_fields": [
                    {
                        "id": 1,
                        "name": "Points",
                        "customized_type": "issue",
                        "possible_values": [{"value": v} for v in "1 2 3 5 8 13".split()],
                    },
                    {
                        "id": 2,
                        "name": "Lane",
                        "customized_type": "issue",
                        "possible_values": [{"value": "infra"}],
                    },
                    {"id": 6, "name": "Ordinal", "customized_type": "issue"},
                    {"id": 9, "name": "Points", "customized_type": "project"},
                ]
            }
        if path == "/issues.json":
            listed = [{"id": i["id"], "subject": i["subject"]} for i in self.issues.values()]
            return {"issues": listed, "total_count": len(listed)}
        n = int(path.split("/")[2].removesuffix(".json"))
        if n not in self.issues:
            raise Refused(f"Redmine GET {path}: HTTP 404", 404)
        i = self.issues[n]
        return {
            "issue": {
                "id": n,
                "subject": i["subject"],
                "project": {"id": i.get("project_id", 2)},
                "tracker": {"id": i.get("tracker_id", 2)},
                "status": {"id": i.get("status_id", 1)},
                "fixed_version": {"id": i["fixed_version_id"]} if "fixed_version_id" in i else None,
                "custom_fields": i.get("custom_fields", []),
                "relations": i["relations"],
            }
        }


class NewIssue(unittest.TestCase):
    """new_issue.py: every name checked before a write, a second run makes nothing new, and a dropped field is named."""

    HEAD = "---\ntracker: Feature\nsubject: Retire it\nversion: Redmine migration\nstatus: Icebox\npoints: 3\nlane: infra\nordinal: 350\nparent:\nrelates: #79\n---\n\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "issue.md"
        self.lenses, self.extra = expected(GLOBAL, REPO)

    def tearDown(self):
        self.tmp.cleanup()

    ORIGIN = "https://github.com/example/gh-plan.git"

    def run_it(self, redmine, head=None, check_only=False, origin=ORIGIN):
        self.file.write_text((head or self.HEAD) + GOOD)
        return new_issue.run(self.file, redmine, check_only, self.lenses, self.extra, origin)

    def test_creates_relates_and_reads_back(self):
        r = FakeTracker()
        self.assertEqual(
            self.run_it(r),
            "#80 created in gh-plan, 1 relation added: http://redmine.test/issues/80",
        )
        issue = r.posts[0][1]["issue"]
        self.assertEqual(
            (
                issue["project_id"],
                issue["tracker_id"],
                issue["fixed_version_id"],
                issue["status_id"],
            ),
            (2, 2, 2, 7),
        )
        self.assertEqual(
            issue["custom_fields"],
            [{"id": 1, "value": "3"}, {"id": 2, "value": "infra"}, {"id": 6, "value": "350"}],
        )
        self.assertTrue(issue["description"].startswith("### Task\n"))
        self.assertNotIn("parent_issue_id", issue)
        self.assertEqual(
            r.posts[1],
            (
                "/issues/80/relations.json",
                {"relation": {"issue_to_id": 79, "relation_type": "relates"}},
            ),
        )

    def test_a_second_run_makes_nothing_and_a_stopped_one_is_finished(self):
        r = FakeTracker()
        self.run_it(r)
        self.assertEqual(
            self.run_it(r),
            "#80 already there in gh-plan, 0 relations added: http://redmine.test/issues/80",
        )
        self.assertEqual(len(r.posts), 2)
        r.issues[80]["relations"] = []
        self.assertEqual(
            self.run_it(r),
            "#80 already there in gh-plan, 1 relation added: http://redmine.test/issues/80",
        )

    def test_check_writes_nothing(self):
        r = FakeTracker()
        self.assertIn("a run creates it", self.run_it(r, check_only=True))
        self.assertEqual(r.posts, [])

    def test_blocked_by_is_a_blocked_relation(self):
        r = FakeTracker()
        self.run_it(r, self.HEAD.replace("relates: #79", "blocked_by: 79"))
        self.assertEqual(
            r.posts[1][1], {"relation": {"issue_to_id": 79, "relation_type": "blocked"}}
        )

    def test_blocked_by_finds_the_blocks_relation_redmine_keeps(self):
        r = FakeTracker()
        head = self.HEAD.replace("relates: #79", "blocked_by: 79")
        self.run_it(r)
        r.issues[80]["relations"] = [
            {"issue_id": 79, "issue_to_id": 80, "relation_type": "blocks"},
            {"issue_id": 80, "issue_to_id": 79, "relation_type": "relates"},
        ]
        self.assertEqual(
            self.run_it(r, head),
            "#80 already there, 0 relations added: http://redmine.test/issues/80",
        )

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
            ("ordinal: 350", "estimate: 3", "isn't one of tracker"),
            ("tracker: Feature", "project: gh-plan\ntracker: Feature", "comes from the repo"),
        ]:
            r = FakeTracker()
            with self.subTest(new), self.assertRaisesRegex(Refused, says):
                self.run_it(r, self.HEAD.replace(old, new))
            self.assertEqual(r.posts, [])

    def test_the_project_is_the_one_whose_homepage_is_the_origin(self):
        for origin in (
            self.ORIGIN,
            "git@github.com:Example/gh-plan.git",
            "ssh://git@github.com/example/gh-plan.git",
            "https://x-access-token:secret@github.com/example/gh-plan/",
        ):
            r = FakeTracker()
            with self.subTest(origin):
                self.run_it(r, origin=origin)
                self.assertEqual(r.posts[0][1]["issue"]["project_id"], 2)

    def test_no_checkout_or_no_matching_project_refuses_before_any_write(self):
        for origin, says in [
            (None, "^run new_issue inside the checkout of the repo the issue is for$"),
            (
                "https://github.com/example/plan.git",
                "^no project has github.com/example/plan as its homepage",
            ),
        ]:
            for check_only in (True, False):
                r = FakeTracker()
                with (
                    self.subTest(origin=origin, check_only=check_only),
                    self.assertRaisesRegex(Refused, says),
                ):
                    self.run_it(r, check_only=check_only, origin=origin)
                self.assertEqual(r.posts, [])

    def test_a_body_off_the_outline_refuses_before_any_write(self):
        r = FakeTracker()
        self.file.write_text(self.HEAD + "### Task\nDo it.\n")
        with self.assertRaisesRegex(Refused, "missing section: ### Done"):
            new_issue.run(self.file, r, False, self.lenses, self.extra, self.ORIGIN)
        self.assertEqual(r.posts, [])

    def test_a_status_redmine_dropped_is_named(self):
        with self.assertRaisesRegex(Refused, "#80 is created, but Redmine didn't keep: status"):
            self.run_it(FakeTracker(drop_status=True))


BODY = GOOD


class FakeIssue:
    """A Redmine holding one issue, #90, for issue_body.py; it stores descriptions with \\r\\n, as Redmine does."""

    url = "http://redmine.test"

    def __init__(self, description="### Task\r\nOld.\r\n", keep=True, touch=None):
        self.issue = {
            "id": 90,
            "subject": "Code search",
            "status": {"id": 2},
            "description": description,
            "custom_fields": [{"id": 1, "value": "3"}],
            "updated_on": "then",
        }
        self.puts = []
        self.keep, self.touch = keep, touch

    def send(self, method, path, body=None):
        if path != "/issues/90.json":
            raise Refused(f"Redmine {method} {path}: HTTP 404", 404)
        if method == "PUT":
            self.puts.append(body)
            if self.keep:
                self.issue["description"] = body["issue"]["description"].replace("\n", "\r\n")
            if self.touch:
                self.issue.update(self.touch)
            self.issue["updated_on"] = "now"
            return None
        return {"issue": dict(self.issue)}


class IssueBody(unittest.TestCase):
    """issue_body.py: the outline check and the diff before any write, and a read-back that names what Redmine didn't keep."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "issue-90-body.md"
        self.file.write_text(BODY)
        self.lenses, self.extra = expected(GLOBAL, REPO)
        out = mock.patch("sys.stdout", new_callable=io.StringIO)
        self.out = out.start()
        self.addCleanup(out.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, redmine, n=90, check_only=False, expect=None):
        if expect is None and not check_only and n == 90:
            expect = issue_body.digest(issue_body.normal(redmine.issue["description"]))
        return issue_body.run(n, self.file, redmine, check_only, self.lenses, self.extra, expect)

    def test_replaces_the_description_and_reads_it_back(self):
        r = FakeIssue()
        self.assertEqual(
            self.run_it(r), "#90's description replaced: http://redmine.test/issues/90"
        )
        self.assertEqual(r.puts, [{"issue": {"description": BODY}}])
        self.assertEqual(issue_body.normal(r.issue["description"]), BODY)

    def test_check_shows_the_diff_and_writes_nothing(self):
        r = FakeIssue()
        said = self.run_it(r, check_only=True)
        seen = issue_body.digest("### Task\nOld.\n")
        self.assertEqual(
            said, f"{self.file} is ready; to write it: issue_body --expect {seen} 90 {self.file}"
        )
        self.assertIn("--- #90 in Redmine\n", self.out.getvalue())
        self.assertIn("-Old.\n", self.out.getvalue())
        self.assertIn("+Do the thing.\n", self.out.getvalue())
        self.assertEqual(r.puts, [])

    def test_a_description_edited_since_check_is_not_overwritten(self):
        r = FakeIssue()
        seen = issue_body.digest(issue_body.normal(r.issue["description"]))
        r.issue["description"] = "### Task\r\nOld, with a typo fixed.\r\n"
        for expect in (seen, ""):
            with (
                self.subTest(expect=expect),
                self.assertRaisesRegex(Refused, "isn't the one --check showed"),
            ):
                self.run_it(r, expect=expect)
        self.assertEqual(r.puts, [])

    def test_a_matching_file_writes_nothing(self):
        r = FakeIssue(description=BODY.replace("\n", "\r\n") + "\r\n")
        self.assertEqual(
            self.run_it(r), f"#90's description already matches {self.file}; nothing written"
        )
        self.assertEqual(r.puts, [])

    def test_a_body_off_the_outline_refuses_before_any_request(self):
        r = FakeIssue()
        r.send = mock.Mock()
        self.file.write_text("### Task\nDo it.\n")
        with self.assertRaisesRegex(Refused, "missing section: ### Done"):
            self.run_it(r)
        r.send.assert_not_called()

    def test_a_ticked_done_line_is_refused(self):
        r = FakeIssue()
        self.file.write_text(GOOD.replace("- [ ] Tests prove", "- [x] Tests prove"))
        with self.assertRaisesRegex(Refused, "'Tests prove the thing.' is ticked"):
            self.run_it(r)
        self.assertEqual(r.puts, [])

    def test_front_matter_is_refused_even_after_a_bom(self):
        for head in ("", "\ufeff"):
            self.file.write_text(head + "---\nsubject: x\n---\n" + BODY)
            with self.subTest(bom=bool(head)), self.assertRaisesRegex(Refused, "front matter"):
                self.run_it(FakeIssue())

    def test_a_file_that_cannot_be_read_is_refused(self):
        for content in (None, b"\xff\xfe###"):
            if content is None:
                self.file.unlink(missing_ok=True)
            else:
                self.file.write_bytes(content)
            with self.subTest(content=content), self.assertRaisesRegex(Refused, "can't read it"):
                self.run_it(FakeIssue())

    def test_an_unknown_issue_is_named(self):
        r = FakeIssue()
        with self.assertRaisesRegex(Refused, "#91 doesn't exist"):
            self.run_it(r, n=91)
        self.assertEqual(r.puts, [])

    def test_a_description_redmine_did_not_keep_is_named(self):
        with self.assertRaisesRegex(Refused, "differs from what was sent in: description;"):
            self.run_it(FakeIssue(keep=False))

    def test_another_field_that_changed_is_named(self):
        touch = {"status": {"id": 5}, "custom_fields": [{"id": 1, "value": "5"}]}
        with self.assertRaisesRegex(
            Refused, "differs from what was sent in: custom_fields, status;"
        ):
            self.run_it(FakeIssue(touch=touch))


class FakePlan:
    """A Redmine holding #69, closed in version M0 (closed), and versions for redmine_set.py; it keeps every write."""

    url = "http://redmine.test"
    STATUSES = [
        {"id": 1, "name": "New", "is_closed": False},
        {"id": 2, "name": "In Progress", "is_closed": False},
        {"id": 5, "name": "Closed", "is_closed": True},
    ]

    def __init__(self, keep=True):
        self.versions = {
            1: {"id": 1, "name": "M0", "status": "closed", "description": "", "sharing": "tree"},
            8: {
                "id": 8,
                "name": "M1 Claude review",
                "status": "open",
                "description": None,
                "sharing": "tree",
            },
            9: {
                "id": 9,
                "name": "M2 Locked",
                "status": "locked",
                "description": "",
                "sharing": "tree",
            },
        }
        for v in self.versions.values():
            v["project"] = {"id": 2, "name": "Plan"}
        self.issue = {
            "id": 69,
            "subject": "Old title",
            "project": {"id": 2},
            "status": dict(self.STATUSES[2]),
            "fixed_version": {"id": 1, "name": "M0"},
            "description": "### Task\r\nOld.\r\n",
            "custom_fields": [{"id": 1, "value": "3"}],
            "journals": [{"id": 1, "notes": "Started."}],
        }
        self.writes = []
        self.keep = keep

    def send(self, method, path, body=None):
        path, _, _ = path.partition("?")
        if method != "GET":
            self.writes.append((method, path, body))
            if not self.keep:
                return None
        if path == "/issues/69.json":
            if method == "PUT":
                self.put_issue(body["issue"])
                return None
            return {"issue": {**self.issue, "allowed_statuses": self.STATUSES}}
        if path == "/projects/plan.json":
            return {"project": {"id": 2}}
        if path == "/issue_statuses.json":
            return {
                "issue_statuses": FakePlan.STATUSES
                + [{"id": 7, "name": "Icebox", "is_closed": False}]
            }
        if path in ("/projects/2/versions.json", "/projects/plan/versions.json"):
            if method == "POST":
                n = max(self.versions) + 1
                self.versions[n] = {
                    "id": n,
                    "description": "",
                    "status": "open",
                    "project": {"id": 2, "name": "Plan"},
                    **body["version"],
                }
                return {"version": {"id": n}}
            return {"versions": [dict(v) for v in self.versions.values()]}
        if path.startswith("/versions/"):
            n = int(path.split("/")[2].removesuffix(".json"))
            if method == "PUT":
                self.versions[n].update(body["version"])
                return None
            return {"version": dict(self.versions[n])}
        if path == "/custom_fields.json":
            return {
                "custom_fields": [
                    {
                        "id": 1,
                        "name": "Points",
                        "customized_type": "issue",
                        "possible_values": [{"value": v} for v in "1 2 3 5 8 13".split()],
                    }
                ]
            }
        raise Refused(f"Redmine {method} {path}: HTTP 404", 404)

    def put_issue(self, put):
        i = self.issue
        if "status_id" in put:
            i["status"] = dict(next(s for s in self.STATUSES if s["id"] == put["status_id"]))
        if "fixed_version_id" in put:
            v = self.versions.get(put["fixed_version_id"])
            i["fixed_version"] = {"id": v["id"], "name": v["name"]} if v else None
        if "subject" in put:
            i["subject"] = put["subject"]
        if "custom_fields" in put:
            i["custom_fields"] = put["custom_fields"]
        if "description" in put:
            i["description"] = put["description"].replace("\n", "\r\n")
        if "notes" in put:
            i["journals"] = i["journals"] + [
                {"id": max([j["id"] for j in i["journals"]], default=0) + 1, "notes": put["notes"]}
            ]


class RedmineSet(unittest.TestCase):
    """redmine_set.py: each transition, --check, a rerun that writes nothing, and the refusals before any write."""

    def setUp(self):
        out = mock.patch("sys.stdout", new_callable=io.StringIO)
        self.out = out.start()
        self.addCleanup(out.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lenses, self.extra = expected(GLOBAL, REPO)

    def issue(self, r, *args):
        a = redmine_set.parser().parse_args(["issue", "69", *args])
        return redmine_set.set_issue(r, 69, a, self.lenses, self.extra)

    def version(self, r, *args):
        a = redmine_set.parser().parse_args(["version", *args])
        return redmine_set.set_version(r, a.name, a)

    def test_status_and_note_change_together(self):
        r = FakePlan()
        said = self.issue(r, "--no-version", "--status", "In Progress", "--note", "Reopened.")
        self.assertEqual(said, "#69 changed: http://redmine.test/issues/69")
        self.assertEqual(
            r.writes,
            [
                (
                    "PUT",
                    "/issues/69.json",
                    {"issue": {"status_id": 2, "fixed_version_id": "", "notes": "Reopened."}},
                )
            ],
        )
        for line in ("status: Closed → In Progress", "version: M0 → none", "note: Reopened."):
            self.assertIn(line, self.out.getvalue())

    def test_version_is_set_and_cleared(self):
        r = FakePlan()
        self.issue(r, "--version", "M1 Claude review")
        self.assertEqual(r.issue["fixed_version"]["id"], 8)
        self.assertIn("version: M0 → M1 Claude review", self.out.getvalue())
        self.issue(r, "--no-version")
        self.assertIsNone(r.issue["fixed_version"])
        self.assertEqual(r.writes[1][2], {"issue": {"fixed_version_id": ""}})

    def test_subject_points_and_description(self):
        r = FakePlan()
        file = Path(self.tmp.name) / "issue-69-body.md"
        file.write_text(GOOD)
        seen = issue_body.digest("### Task\nOld.\n")
        args = ["--subject", "New title", "--points", "5", "--description", str(file)]
        self.issue(r, *args, "--expect", seen)
        self.assertEqual(
            r.writes[0][2]["issue"],
            {
                "subject": "New title",
                "custom_fields": [{"id": 1, "value": "5"}],
                "description": GOOD,
            },
        )
        out = self.out.getvalue()
        self.assertIn("subject: Old title → New title", out)
        self.assertIn("points: 3 → 5", out)
        self.assertIn("+Do the thing.\n", out)

    def test_a_description_edited_since_check_is_not_overwritten(self):
        r = FakePlan()
        file = Path(self.tmp.name) / "issue-69-body.md"
        file.write_text(GOOD)
        a = redmine_set.parser().parse_args(["--check", "issue", "69", "--description", str(file)])
        said = redmine_set.set_issue(r, 69, a, self.lenses, self.extra)
        seen = issue_body.digest("### Task\nOld.\n")
        self.assertIn(f"without --check and with --expect {seen}", said)
        r.issue["description"] = "### Task\r\nOld, edited.\r\n"
        for expect in ([], ["--expect", seen]):
            with (
                self.subTest(expect=expect),
                self.assertRaisesRegex(Refused, "isn't the one --check showed"),
            ):
                self.issue(r, "--description", str(file), *expect)
        self.assertEqual(r.writes, [])

    def test_front_matter_in_a_description_is_refused(self):
        r = FakePlan()
        file = Path(self.tmp.name) / "draft.md"
        file.write_text("---\nsubject: x\n---\n" + GOOD)
        with self.assertRaisesRegex(Refused, "starts with front matter"):
            self.issue(r, "--description", str(file))
        self.assertEqual(r.writes, [])

    def test_a_note_repeats_unless_it_is_the_latest(self):
        r = FakePlan()
        r.issue["journals"] = [
            {"id": 1, "notes": "Reopened."},
            {"id": 2, "notes": "Closed."},
            {"id": 3, "notes": ""},
        ]
        self.issue(r, "--note", "Reopened.")
        self.assertEqual(r.writes[0][2], {"issue": {"notes": "Reopened."}})
        self.assertEqual(
            self.issue(r, "--note", "Reopened."), "#69 is already as asked; nothing written"
        )

    def test_points_with_no_listed_values_are_taken_as_given(self):
        r = FakePlan()
        field = {"id": 1, "name": "Points", "possible_values": []}
        with mock.patch.object(redmine_set, "issue_fields", return_value=[field]):
            self.issue(r, "--points", "4")
        self.assertEqual(r.writes[0][2], {"issue": {"custom_fields": [{"id": 1, "value": "4"}]}})

    def test_points_without_an_admin_key_use_the_issue_s_own_field(self):
        r = FakePlan()
        r.issue["custom_fields"] = [{"id": 1, "name": "Points", "value": "3"}]
        forbidden = Refused("Redmine GET /custom_fields.json: HTTP 403", 403)
        with mock.patch.object(redmine_set, "issue_fields", side_effect=forbidden):
            self.issue(r, "--points", "5")
        self.assertEqual(r.writes[0][2], {"issue": {"custom_fields": [{"id": 1, "value": "5"}]}})
        r.issue["custom_fields"] = []
        with (
            mock.patch.object(redmine_set, "issue_fields", side_effect=forbidden),
            self.assertRaisesRegex(Refused, "no issue custom field 'Points'"),
        ):
            self.issue(r, "--points", "8")
        broken = Refused("Redmine GET /custom_fields.json: HTTP 500", 500)
        with (
            mock.patch.object(redmine_set, "issue_fields", side_effect=broken),
            self.assertRaisesRegex(Refused, "HTTP 500"),
        ):
            self.issue(r, "--points", "8")
        self.assertEqual(len(r.writes), 1)

    def test_a_repeated_note_is_sent_with_a_change(self):
        r = FakePlan()
        r.issue["journals"] = [{"id": 1, "notes": "Moved."}]
        self.issue(r, "--version", "M1 Claude review", "--note", "Moved.")
        self.assertEqual(r.writes[0][2], {"issue": {"fixed_version_id": 8, "notes": "Moved."}})

    def test_a_status_with_nowhere_to_go_says_so_and_why(self):
        r = FakePlan()
        r.STATUSES = [r.STATUSES[2]]
        with self.assertRaisesRegex(
            Refused, "it can't move to any status now; Redmine allows only its workflow's"
        ):
            self.issue(r, "--status", "New")
        self.assertEqual(r.writes, [])

    def test_the_reopen_check_reads_the_versions_once(self):
        r = FakePlan()
        with mock.patch.object(r, "send", wraps=r.send) as send, self.assertRaises(Refused):
            self.issue(r, "--status", "In Progress")
        paths = [c.args[1] for c in send.call_args_list]
        self.assertEqual(paths.count("/projects/2/versions.json"), 1)
        self.assertFalse([p for p in paths if p.startswith("/versions/")])

    def test_create_makes_its_own_beside_another_project_s_shared_version(self):
        r = FakePlan()
        r.versions[20] = {
            "id": 20,
            "name": "M7",
            "status": "open",
            "project": {"id": 3, "name": "tempotag"},
        }
        said = self.version(r, "M7", "--create")
        self.assertEqual(said, "version M7 created: http://redmine.test/versions/21")
        self.assertEqual(r.writes[0][1], "/projects/plan/versions.json")

    def test_expect_without_a_description_is_refused(self):
        with (
            mock.patch("sys.stderr", new_callable=io.StringIO) as err,
            self.assertRaises(SystemExit),
        ):
            redmine_set.main(["issue", "69", "--expect", "3f2a9c01d4e5"])
        self.assertIn("give it with --description", err.getvalue())

    def test_a_version_name_two_projects_share_is_refused(self):
        r = FakePlan()
        r.versions[20] = {
            "id": 20,
            "name": "M1 Claude review",
            "status": "open",
            "project": {"id": 3, "name": "tempotag"},
        }
        with self.assertRaisesRegex(
            Refused, "2 versions are named M1 Claude review, in Plan, tempotag"
        ):
            self.issue(r, "--version", "M1 Claude review")
        self.assertEqual(r.writes, [])

    def test_the_latest_note_is_found_in_either_journal_order(self):
        r = FakePlan()
        r.issue["journals"] = [{"id": 9, "notes": "Moved."}, {"id": 4, "notes": "Started."}]
        self.assertEqual(
            self.issue(r, "--note", "Moved."), "#69 is already as asked; nothing written"
        )
        self.issue(r, "--note", "Started.")
        self.assertEqual(r.writes[0][2], {"issue": {"notes": "Started."}})

    def test_a_status_change_reads_no_status_list_when_allowed(self):
        r = FakePlan()
        r.issue["fixed_version"] = None
        with mock.patch.object(r, "send", wraps=r.send) as send:
            self.issue(r, "--status", "In Progress")
        self.assertNotIn("/issue_statuses.json", [c.args[1] for c in send.call_args_list])

    def test_a_version_name_two_projects_share_is_refused_for_a_version_too(self):
        r = FakePlan()
        r.versions[20] = {
            "id": 20,
            "name": "M0",
            "status": "open",
            "project": {"id": 3, "name": "tempotag"},
        }
        with self.assertRaisesRegex(Refused, "2 versions are named M0"):
            self.version(r, "M0", "--status", "open")
        self.assertEqual(
            self.version(r, "M0", "--create", "--status", "closed"),
            "version M0 is already as asked; nothing written",
        )
        self.assertEqual(r.writes, [])

    def test_check_prints_each_change_and_writes_nothing(self):
        r = FakePlan()
        a = redmine_set.parser().parse_args(
            ["--check", "issue", "69", "--status", "Closed", "--subject", "New", "--note", "Hi."]
        )
        said = redmine_set.set_issue(r, 69, a, self.lenses, self.extra)
        self.assertEqual(said, "#69: 2 changes to make; nothing written")
        self.assertIn("subject: Old title → New", self.out.getvalue())
        self.assertIn("note: Hi.", self.out.getvalue())
        self.assertEqual(r.writes, [])
        self.assertIn(
            "nothing written",
            self.version(r, "M1 Claude review", "--status", "closed", "--check"),
        )
        self.assertIn("would be created", self.version(r, "M9", "--create", "--check"))
        self.assertEqual(r.writes, [])

    def test_a_second_run_writes_nothing_and_says_so(self):
        r = FakePlan()
        args = ["--version", "M1 Claude review", "--points", "5", "--note", "Moved."]
        self.issue(r, *args)
        self.assertEqual(self.issue(r, *args), "#69 is already as asked; nothing written")
        self.assertIn("latest note is already this one", self.out.getvalue())
        self.assertEqual(len(r.writes), 1)
        self.version(r, "M1 Claude review", "--status", "closed")
        self.assertEqual(
            self.version(r, "M1 Claude review", "--status", "closed"),
            "version M1 Claude review is already as asked; nothing written",
        )
        self.assertEqual(len(r.writes), 2)

    def test_version_closes_and_is_created_shared_with_the_tree(self):
        r = FakePlan()
        self.assertEqual(
            self.version(r, "M1 Claude review", "--status", "closed"),
            "version M1 Claude review changed: http://redmine.test/versions/8",
        )
        self.assertEqual(
            r.writes[0], ("PUT", "/versions/8.json", {"version": {"status": "closed"}})
        )
        self.assertIn("status: open → closed", self.out.getvalue())
        said = self.version(r, "M5 Next", "--create", "--description", "Next up.")
        self.assertEqual(said, "version M5 Next created: http://redmine.test/versions/10")
        self.assertEqual(
            r.writes[1],
            (
                "POST",
                "/projects/plan/versions.json",
                {"version": {"name": "M5 Next", "sharing": "tree", "description": "Next up."}},
            ),
        )
        self.assertEqual(
            self.version(r, "M5 Next", "--create", "--description", "Next up."),
            "version M5 Next is already as asked; nothing written",
        )

    def test_reopening_in_a_closed_version_names_both_ways_on(self):
        r = FakePlan()
        with self.assertRaises(Refused) as e:
            self.issue(r, "--status", "In Progress")
        said = str(e.exception)
        self.assertIn("#69 is in version M0, which is closed", said)
        self.assertIn("--no-version", said)
        self.assertIn("redmine-set version M0 --status open", said)
        self.assertEqual(r.writes, [])

    def test_bad_names_and_values_refuse_before_any_write(self):
        for args, says in [
            (["--status", "Done"], "no status 'Done'"),
            (["--status", "Icebox"], "can't move from Closed to Icebox; it can move to: New"),
            (["--version", "M9"], "no version 'M9'; versions: M0, M1 Claude review"),
            (["--version", "M2 Locked"], "M2 Locked is locked.*'M2 Locked' --status open"),
            (["--points", "4"], "points: '4' isn't one of 1, 2, 3"),
            (["--description", "/no/such/file"], "can't read it"),
        ]:
            r = FakePlan()
            with self.subTest(args), self.assertRaisesRegex(Refused, says):
                self.issue(r, "--note", "Never sent.", *args)
            self.assertEqual(r.writes, [])
        for args, says in [
            (["M9", "--status", "closed"], "no version 'M9'"),
            (["M0", "--status", "shut"], "isn't one of open, locked, closed"),
        ]:
            r = FakePlan()
            with self.subTest(args), self.assertRaisesRegex(Refused, says):
                self.version(r, *args)
            self.assertEqual(r.writes, [])

    def test_a_description_off_the_outline_refuses_before_any_write(self):
        r = FakePlan()
        file = Path(self.tmp.name) / "bad.md"
        file.write_text("### Task\nDo it.\n")
        with self.assertRaisesRegex(Refused, "missing section: ### Done"):
            self.issue(r, "--description", str(file))
        self.assertEqual(r.writes, [])

    def test_an_unknown_issue_is_named(self):
        r = FakePlan()
        a = redmine_set.parser().parse_args(["issue", "70", "--status", "Closed"])
        with self.assertRaisesRegex(Refused, "#70 doesn't exist"):
            redmine_set.set_issue(r, 70, a, self.lenses, self.extra)

    def test_a_write_redmine_did_not_keep_is_named(self):
        with self.assertRaisesRegex(Refused, "didn't keep: version, subject, note;"):
            self.issue(FakePlan(keep=False), "--no-version", "--subject", "New", "--note", "Hi.")
        with self.assertRaisesRegex(Refused, "M1 Claude review was written.*didn't keep: status"):
            self.version(FakePlan(keep=False), "M1 Claude review", "--status", "closed")

    def test_a_dropped_status_names_what_was_kept(self):
        r = FakePlan()
        real = r.put_issue
        r.put_issue = lambda put: real({k: v for k, v in put.items() if k != "status_id"})
        with self.assertRaisesRegex(
            Refused, "didn't keep: status; it kept: version, note; check it in Redmine"
        ):
            self.issue(r, "--no-version", "--status", "In Progress", "--note", "Reopened.")

    def test_main_prints_the_result_and_exits_on_a_refusal(self):
        r = FakePlan()
        with mock.patch.object(redmine_set, "Redmine", return_value=r):
            self.assertEqual(redmine_set.main(["issue", "#69", "--subject", "New"]), 0)
            self.assertIn("redmine-set: #69 changed", self.out.getvalue())
            with self.assertRaisesRegex(SystemExit, "redmine-set: no version 'M9'"):
                redmine_set.main(["version", "M9", "--status", "open"])


class RedmineErrors(unittest.TestCase):
    """redmine.py: Redmine's own error text reaches the message."""

    def error(self, body):
        e = urllib.error.HTTPError("u", 422, "Unprocessable", {}, io.BytesIO(body))
        self.addCleanup(e.close)
        return e

    def test_redmine_error_text_is_shown(self):
        e = self.error(b'{"errors":["An issue assigned to a closed version cannot be reopened"]}')
        self.assertEqual(reasons(e), ": An issue assigned to a closed version cannot be reopened")

    def test_a_string_of_errors_is_one_reason(self):
        self.assertEqual(reasons(self.error(b'{"errors": "Forbidden"}')), ": Forbidden")

    def test_a_body_without_errors_adds_nothing(self):
        for body in (b"", b"<html>", b"{}", b'{"errors": []}'):
            with self.subTest(body):
                self.assertEqual(reasons(self.error(body)), "")


class Every(unittest.TestCase):
    """redmine.every: each page of a listing, its query kept."""

    class Pages:
        def __init__(self, items, total=True):
            self.items, self.total, self.paths = items, total, []

        def send(self, method, path, body=None):
            self.paths.append(path)
            q = urllib.parse.parse_qs(path.partition("?")[2])
            offset, limit = int(q["offset"][0]), int(q["limit"][0])
            page = {"issues": self.items[offset : offset + min(limit, 2)]}
            if self.total:
                page["total_count"] = len(self.items)
            return page

    def test_pages_a_path_with_a_query(self):
        r = self.Pages([{"id": n} for n in range(1, 6)])
        self.assertEqual(
            [i["id"] for i in every(r, "/issues.json?sort=id", "issues")], [1, 2, 3, 4, 5]
        )
        self.assertEqual(r.paths[0], "/issues.json?sort=id&limit=100&offset=0")
        self.assertEqual(r.paths[-1], "/issues.json?sort=id&limit=100&offset=4")

    def test_a_path_without_a_query_and_a_listing_without_a_count(self):
        r = self.Pages([{"id": 1}, {"id": 2}, {"id": 3}], total=False)
        self.assertEqual(every(r, "/issues.json", "issues"), [{"id": 1}, {"id": 2}])
        self.assertEqual(r.paths, ["/issues.json?limit=100&offset=0"])


class ReadBack(unittest.TestCase):
    """redmine.unkept: the one read-back check new_issue, issue_body and redmine_set share."""

    BACK = {
        "status": {"id": 2},
        "fixed_version": None,
        "subject": "Title",
        "description": "### Task\r\nDo it.\r\n",
        "custom_fields": [{"id": 1, "value": "3"}],
        "journals": [
            {"id": 1, "notes": "Older."},
            {"id": 2, "notes": "Latest."},
            {"id": 3, "notes": ""},
        ],
    }

    def test_a_write_redmine_kept_names_nothing(self):
        sent = {
            "status_id": 2,
            "fixed_version_id": "",
            "subject": "Title",
            "description": "### Task\nDo it.\n",
            "custom_fields": [{"id": 1, "value": "3"}],
            "notes": "Latest.",
        }
        self.assertEqual(unkept(self.BACK, sent), [])

    def test_each_field_redmine_did_not_keep_is_named(self):
        sent = {
            "project_id": 2,
            "status_id": 5,
            "fixed_version_id": 8,
            "subject": "New",
            "description": "### Task\nOther.\n",
            "custom_fields": [{"id": 1, "value": "5"}, {"id": 2, "value": "infra"}],
            "notes": "Older.",
        }
        self.assertEqual(
            unkept(self.BACK, sent, {1: "points"}),
            [
                "project",
                "status",
                "version",
                "subject",
                "description",
                "points",
                "custom field 2",
                "note",
            ],
        )


class FakeRelations:
    """A Redmine holding issues 104, 107, 115 and 124 and their relations, for issue_relations.py."""

    def __init__(self, relations=(), keep=True, refuse=None, refuse_delete=None):
        self.rels = {i + 1: dict(r, id=i + 1) for i, r in enumerate(relations)}
        self.writes = []
        self.keep, self.refuse, self.refuse_delete = keep, refuse, refuse_delete

    def send(self, method, path, body=None):
        self.writes += [(method, path, body)] if method != "GET" else []
        if method == "POST":
            n = int(path.split("/")[2])
            r = body["relation"]
            if r["issue_to_id"] == self.refuse:
                raise Refused(f"Redmine POST {path}: HTTP 422: circular dependency", 422)
            if self.keep:
                rid = max(self.rels, default=0) + 1
                self.rels[rid] = {
                    "id": rid,
                    "issue_id": n,
                    "issue_to_id": r["issue_to_id"],
                    "relation_type": r["relation_type"],
                }
            return None
        if method == "DELETE":
            rid = int(path.split("/")[2].split(".")[0])
            if rid == self.refuse_delete:
                raise Refused(f"Redmine DELETE {path}: HTTP 403", 403)
            if self.keep:
                del self.rels[rid]
            return None
        n = int(path.split("/")[2].split(".")[0])
        if n not in {104, 107, 115, 124}:
            raise Refused(f"Redmine GET {path}: HTTP 404", 404)
        mine = [r for r in self.rels.values() if n in (r["issue_id"], r["issue_to_id"])]
        return {"issue": {"id": n, "relations": [dict(r) for r in mine]}}


class IssueRelations(unittest.TestCase):
    """issue_relations.py: adds only what's missing, matches reverse spellings, refuses a clash, reads back."""

    def setUp(self):
        out = mock.patch("sys.stdout", new_callable=io.StringIO)
        self.out = out.start()
        self.addCleanup(out.stop)

    def test_adds_each_missing_relation_and_reads_it_back(self):
        r = FakeRelations()
        said = issue_relations.run("104 blocks 124 115 relates 104".split(), r, False, False)
        self.assertEqual(said, "2 relations added")
        self.assertEqual(
            r.writes,
            [
                (
                    "POST",
                    "/issues/104/relations.json",
                    {"relation": {"issue_to_id": 124, "relation_type": "blocks"}},
                ),
                (
                    "POST",
                    "/issues/104/relations.json",
                    {"relation": {"issue_to_id": 115, "relation_type": "relates"}},
                ),
            ],
        )

    def test_a_reverse_spelling_finds_the_relation_already_there(self):
        r = FakeRelations([{"issue_id": 104, "issue_to_id": 124, "relation_type": "blocks"}])
        said = issue_relations.run("124 blocked_by 104".split(), r, False, False)
        self.assertEqual(said, "0 relations added")
        self.assertIn("#104 blocks #124 is already there", self.out.getvalue())
        self.assertEqual(r.writes, [])

    def test_check_writes_nothing_and_prints_the_command(self):
        r = FakeRelations()
        said = issue_relations.run("124 blocks 107".split(), r, True, False)
        self.assertEqual(said, "1 to add; to write: issue_relations 124 blocks 107")
        self.assertIn("would add #124 blocks #107", self.out.getvalue())
        self.assertEqual(r.writes, [])

    def test_another_relation_between_the_pair_is_refused_before_any_write(self):
        r = FakeRelations([{"issue_id": 104, "issue_to_id": 124, "relation_type": "relates"}])
        with self.assertRaisesRegex(Refused, "already hold #104 relates #124; remove it first"):
            issue_relations.run("115 relates 104 104 blocks 124".split(), r, False, False)
        self.assertEqual(r.writes, [])

    def test_removes_a_relation_by_its_id(self):
        r = FakeRelations([{"issue_id": 104, "issue_to_id": 124, "relation_type": "blocks"}])
        said = issue_relations.run("124 blocked 104".split(), r, False, True)
        self.assertEqual(said, "1 relation removed")
        self.assertEqual(r.writes, [("DELETE", "/relations/1.json", None)])

    def test_a_relation_redmine_did_not_keep_is_named(self):
        with self.assertRaisesRegex(Refused, "didn't add #104 blocks #124"):
            issue_relations.run("104 blocks 124".split(), FakeRelations(keep=False), False, False)

    def test_a_relation_named_twice_is_written_once(self):
        r = FakeRelations()
        said = issue_relations.run("104 blocks 124 124 blocked_by 104".split(), r, False, False)
        self.assertEqual(said, "1 relation added")
        self.assertEqual(len(r.writes), 1)

    def test_two_arguments_joining_one_pair_differently_are_refused(self):
        r = FakeRelations()
        with self.assertRaisesRegex(Refused, "join the same two issues; give one"):
            issue_relations.run("104 blocks 124 124 precedes 104".split(), r, False, False)
        self.assertEqual(r.writes, [])

    def test_removes_one_of_two_relations_redmine_holds_between_a_pair(self):
        r = FakeRelations(
            [
                {"issue_id": 104, "issue_to_id": 124, "relation_type": "blocks"},
                {"issue_id": 124, "issue_to_id": 104, "relation_type": "precedes"},
            ]
        )
        said = issue_relations.run("104 blocks 124".split(), r, False, True)
        self.assertEqual(said, "1 relation removed")
        self.assertEqual(r.writes, [("DELETE", "/relations/1.json", None)])

    def test_a_run_refused_part_way_names_what_it_wrote_and_a_rerun_finishes(self):
        r = FakeRelations(refuse=107)
        words = "104 blocks 124 124 blocks 107 115 relates 104".split()
        with self.assertRaisesRegex(Refused, "HTTP 422: circular dependency"):
            issue_relations.run(words, r, False, False)
        self.assertIn("added #104 blocks #124", self.out.getvalue())
        self.assertNotIn("added #104 relates #115", self.out.getvalue())
        r.refuse = None
        self.assertEqual(issue_relations.run(words, r, False, False), "2 relations added")
        clean = FakeRelations()
        issue_relations.run(words, clean, False, False)

        def held(f):
            return {(x["issue_id"], x["relation_type"], x["issue_to_id"]) for x in f.rels.values()}

        self.assertEqual(held(r), held(clean))

    def test_check_names_only_what_is_left_without_hash_signs(self):
        r = FakeRelations([{"issue_id": 104, "issue_to_id": 124, "relation_type": "blocks"}])
        said = issue_relations.run(
            ["#104", "blocks", "#124", "#124", "blocks", "#107"], r, True, False
        )
        self.assertEqual(said, "1 to add; to write: issue_relations 124 blocks 107")
        said = issue_relations.run(["#104", "blocks", "#124"], r, True, False)
        self.assertEqual(said, "0 to add")

    def test_a_relation_already_there_beside_another_is_skipped(self):
        r = FakeRelations(
            [
                {"issue_id": 104, "issue_to_id": 124, "relation_type": "blocks"},
                {"issue_id": 124, "issue_to_id": 104, "relation_type": "precedes"},
            ]
        )
        self.assertEqual(
            issue_relations.run("104 blocks 124".split(), r, False, False), "0 relations added"
        )
        self.assertEqual(r.writes, [])

    def test_removes_relates_kept_as_two_rows(self):
        r = FakeRelations(
            [
                {"issue_id": 104, "issue_to_id": 124, "relation_type": "relates"},
                {"issue_id": 124, "issue_to_id": 104, "relation_type": "relates"},
            ]
        )
        said = issue_relations.run("124 relates 104".split(), r, False, True)
        self.assertEqual(said, "1 relation removed")
        self.assertEqual(r.rels, {})

    def test_removes_two_relations_between_a_pair_in_one_run(self):
        r = FakeRelations(
            [
                {"issue_id": 104, "issue_to_id": 124, "relation_type": "blocks"},
                {"issue_id": 124, "issue_to_id": 104, "relation_type": "precedes"},
            ]
        )
        said = issue_relations.run("104 blocks 124 124 precedes 104".split(), r, False, True)
        self.assertEqual(said, "2 relations removed")
        self.assertEqual(r.rels, {})

    def test_adding_precedes_says_redmine_may_move_dates(self):
        issue_relations.run("104 precedes 124".split(), FakeRelations(), False, False)
        self.assertIn("may have moved #124's dates", self.out.getvalue())

    def test_check_says_precedes_may_move_dates(self):
        issue_relations.run("104 precedes 124".split(), FakeRelations(), True, False)
        self.assertIn("may move #124's dates", self.out.getvalue())

    def test_a_remove_refused_part_way_names_the_rows_it_removed_and_a_rerun_finishes(self):
        rows = [
            {"issue_id": 104, "issue_to_id": 124, "relation_type": "relates"},
            {"issue_id": 124, "issue_to_id": 104, "relation_type": "relates"},
        ]
        r = FakeRelations(rows, refuse_delete=2)
        with self.assertRaisesRegex(Refused, "HTTP 403"):
            issue_relations.run("104 relates 124".split(), r, False, True)
        self.assertIn("removed 1 of 2 rows of #104 relates #124", self.out.getvalue())
        r.refuse_delete = None
        self.assertEqual(
            issue_relations.run("104 relates 124".split(), r, False, True), "1 relation removed"
        )
        self.assertEqual(r.rels, {})

    def test_an_issue_that_does_not_exist_is_named(self):
        with self.assertRaisesRegex(Refused, "#999 doesn't exist"):
            issue_relations.run("104 blocks 999".split(), FakeRelations(), False, False)

    def test_parse_refuses_each_bad_relation(self):
        for words, why in [
            ("104 blocks", "three words"),
            ("104 blocks x", "two issue numbers"),
            ("104 block 124", "'block' isn't a type"),
            ("104 blocks 104", "related to itself"),
        ]:
            with self.subTest(words), self.assertRaisesRegex(Refused, why):
                issue_relations.parse(words.split())

    def test_main_exits_non_zero_when_redmine_did_not_keep_a_relation(self):
        with mock.patch.object(issue_relations, "Redmine", return_value=FakeRelations(keep=False)):
            with self.assertRaises(SystemExit) as e:
                issue_relations.main("104 blocks 124".split())
        self.assertIn("didn't add #104 blocks #124", str(e.exception.code))

    def test_main_names_a_typo_before_reading_the_keychain(self):
        with mock.patch.object(issue_relations, "Redmine", side_effect=AssertionError):
            with self.assertRaises(SystemExit) as e:
                issue_relations.main("104 block 124".split())
        self.assertIn("'block' isn't a type", str(e.exception.code))

    def test_main_prints_what_it_did(self):
        with mock.patch.object(issue_relations, "Redmine", return_value=FakeRelations()):
            self.assertEqual(issue_relations.main("104 blocks 124".split()), 0)
        self.assertIn("issue_relations: 1 relation added", self.out.getvalue())

    def test_a_refusal_names_every_other_relation_between_the_pair(self):
        r = FakeRelations(
            [
                {"issue_id": 104, "issue_to_id": 124, "relation_type": "blocks"},
                {"issue_id": 124, "issue_to_id": 104, "relation_type": "precedes"},
            ]
        )
        with self.assertRaisesRegex(
            Refused,
            "hold #104 blocks #124, #124 precedes #104; remove them first: "
            "issue_relations --remove 104 blocks 124 124 precedes 104",
        ):
            issue_relations.run("104 relates 124".split(), r, False, False)

    def test_removing_a_relation_not_there_from_a_pair_holding_another_is_refused(self):
        r = FakeRelations([{"issue_id": 104, "issue_to_id": 124, "relation_type": "precedes"}])
        with self.assertRaisesRegex(
            Refused,
            "hold #104 precedes #124, not #104 blocks #124; "
            "to remove that: issue_relations --remove 104 precedes 124",
        ):
            issue_relations.run("115 relates 104 104 blocks 124".split(), r, False, True)
        self.assertEqual(r.writes, [])

    def test_removing_both_relations_of_a_pair_after_one_went_finishes(self):
        r = FakeRelations([{"issue_id": 104, "issue_to_id": 124, "relation_type": "precedes"}])
        said = issue_relations.run("104 blocks 124 104 precedes 124".split(), r, False, True)
        self.assertEqual(said, "1 relation removed")
        self.assertEqual(r.rels, {})

    def test_a_refused_write_reads_back_the_writes_before_it(self):
        r = FakeRelations(keep=False, refuse=107)
        with self.assertRaisesRegex(
            Refused, "circular dependency; and Redmine didn't add #104 blocks #124"
        ):
            issue_relations.run("104 blocks 124 124 blocks 107".split(), r, False, False)

    def test_a_remove_refusal_names_only_what_the_run_does_not_remove(self):
        r = FakeRelations(
            [
                {"issue_id": 104, "issue_to_id": 124, "relation_type": "blocks"},
                {"issue_id": 104, "issue_to_id": 124, "relation_type": "relates"},
            ]
        )
        with self.assertRaisesRegex(
            Refused,
            "hold #104 relates #124, not #124 precedes #104; "
            "to remove that: issue_relations --remove 104 relates 124$",
        ):
            issue_relations.run("104 blocks 124 124 precedes 104".split(), r, False, True)
        self.assertEqual(r.writes, [])

    def test_a_read_back_that_fails_after_a_refusal_keeps_redmine_s_reason(self):
        r = FakeRelations(refuse=107)
        send = r.send

        def failing(method, path, body=None):
            if method == "GET" and r.writes and r.writes[-1][2]["relation"]["issue_to_id"] == 107:
                raise Refused(f"Redmine GET {path}: HTTP 500", 500)
            return send(method, path, body)

        r.send = failing
        with self.assertRaisesRegex(
            Refused, "circular dependency; and reading back what was written failed: .*HTTP 500"
        ):
            issue_relations.run("104 blocks 124 124 blocks 107".split(), r, False, False)

    def test_parse_refuses_an_issue_number_below_one(self):
        for words in (
            "104 blocks -1",
            "0 blocks 124",
            "1_04 blocks 124",
            "+104 blocks 124",
            "104 blocks ##124",
            "104 blocks 0124",
            "104 blocks \u0661\u0662",
        ):
            with self.subTest(words), self.assertRaisesRegex(Refused, "two issue numbers"):
                issue_relations.parse(words.split())


if __name__ == "__main__":
    unittest.main()
