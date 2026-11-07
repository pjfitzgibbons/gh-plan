"""Offline tests for serve_report.py: its handler on a loopback port, milestones.py stubbed."""

import json
import socket
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import serve_report
from milestones import PAGE, Refused, options_file
from serve_report import HOST, Handler, answers, main, ours, port_for, running, state_file


def finished(returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, "", stderr)


class Regen(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / PAGE.parent).mkdir(parents=True)
        handler = partial(Handler, directory=str(self.root / PAGE.parent))
        self.server = ThreadingHTTPServer((HOST, 0), handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        Handler.root, Handler.port = self.root, self.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def post(self, path="/regen", origin=None):
        origin = origin or f"http://{HOST}:{self.port}"
        request = urllib.request.Request(
            f"http://{HOST}:{self.port}{path}", method="POST", headers={"Origin": origin}
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def stub(self, **kwargs):
        patcher = patch.object(serve_report.subprocess, "run", **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_own_page_regenerates_with_the_options_it_was_written_with(self):
        options_file(self.root / PAGE).write_text(json.dumps(["--all"]))
        run = self.stub(return_value=finished())
        self.assertEqual(self.post(), (204, ""))
        self.assertEqual(run.call_args.args[0][-1], "--all")
        self.assertEqual(run.call_args.kwargs["cwd"], self.root)

    def test_another_origin_gets_plain_text_and_never_runs_it(self):
        run = self.stub(return_value=finished())
        status, body = self.post(origin="http://evil.example")
        self.assertEqual(status, 403)
        self.assertIn(f"http://{HOST}:{self.port}/{PAGE.name}", body)
        self.assertNotIn("<!DOCTYPE", body)
        run.assert_not_called()

    def test_localhost_is_another_origin(self):
        self.stub(return_value=finished())
        self.assertEqual(self.post(origin=f"http://localhost:{self.port}")[0], 403)

    def test_a_failed_run_answers_its_error(self):
        self.stub(return_value=finished(1, "report-milestones: Redmine can't be reached"))
        status, body = self.post()
        self.assertEqual(status, 500)
        self.assertIn("Redmine can't be reached", body)

    def test_a_failed_run_with_no_output_is_still_a_failure(self):
        self.stub(return_value=finished(3))
        self.assertEqual(self.post(), (500, "report-milestones failed: exit 3"))

    def test_a_repo_folder_that_has_gone_answers_500(self):
        self.stub(side_effect=FileNotFoundError("no such directory"))
        status, body = self.post()
        self.assertEqual(status, 500)
        self.assertIn("no such directory", body)

    def test_a_run_that_times_out_answers_500(self):
        self.stub(side_effect=subprocess.TimeoutExpired("milestones.py", 120))
        status, body = self.post()
        self.assertEqual(status, 500)
        self.assertIn("in 2 minutes", body)

    def test_any_other_path_is_not_found(self):
        self.assertEqual(self.post("/other")[0], 404)


class Lifecycle(unittest.TestCase):
    STATE = {"pid": 123, "port": 8745, "root": "/repo"}

    def ps_says(self, command):
        return patch.object(
            serve_report.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, command, ""),
        )

    def test_a_live_pid_running_this_repos_server_is_ours(self):
        with (
            patch.object(serve_report.os, "kill"),
            self.ps_says("python serve_report.py --serve 8745 --root /repo\n"),
        ):
            self.assertTrue(ours(self.STATE))

    def test_a_reused_pid_is_not_ours(self):
        with patch.object(serve_report.os, "kill"), self.ps_says("vim notes.txt\n"):
            self.assertFalse(ours(self.STATE))

    def test_a_dead_pid_is_not_ours(self):
        with patch.object(serve_report.os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(ours(self.STATE))

    def test_a_port_another_process_holds_gives_way_to_a_free_one(self):
        root = Path("/some/repo")
        preferred = port_for(root)
        with socket.socket() as holder:
            holder.bind((HOST, preferred))
            self.assertNotEqual(port_for(root), preferred)


class Commands(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name, value in (("STATE_DIR", self.root / "state"), ("repo_root", lambda: self.root)):
            patcher = patch.object(serve_report, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        serve_report.STATE_DIR.mkdir()
        self.state = {"pid": 123, "port": 8745, "root": str(self.root)}

    def remember(self):
        state_file(self.root).write_text(json.dumps(self.state))

    def page(self):
        (self.root / PAGE).parent.mkdir(parents=True)
        (self.root / PAGE).write_text("page")

    def test_outside_a_repo_it_stops_with_one_line(self):
        with patch.object(serve_report, "repo_root", side_effect=Refused("not a repo")):
            with self.assertRaises(SystemExit) as stop:
                main([])
        self.assertEqual(stop.exception.code, "report-server: not a repo")

    def test_with_no_page_it_names_the_command_to_run(self):
        with self.assertRaises(SystemExit) as stop:
            main([])
        self.assertIn("run report-milestones first", stop.exception.code)

    def test_stop_ends_our_server_and_forgets_it(self):
        self.remember()
        with patch.object(serve_report, "ours", return_value=True):
            with patch.object(serve_report.os, "kill") as kill:
                main(["--stop"])
        kill.assert_called_once_with(123, serve_report.signal.SIGTERM)
        self.assertFalse(state_file(self.root).exists())

    def test_stop_with_no_server_kills_nothing(self):
        with patch.object(serve_report.os, "kill") as kill:
            main(["--stop"])
        kill.assert_not_called()

    def test_a_running_server_is_reused(self):
        self.page()
        self.remember()
        with patch.object(serve_report, "ours", return_value=True):
            with patch.object(serve_report, "answers", return_value=True):
                with patch.object(serve_report, "start") as start:
                    main([])
        start.assert_not_called()

    def test_a_server_that_exits_at_once_is_reported(self):
        self.page()
        with patch.object(serve_report.subprocess, "Popen") as popen:
            popen.return_value.poll.return_value = 1
            with patch.object(serve_report, "answers", return_value=False):
                with self.assertRaises(SystemExit) as stop:
                    main([])
        self.assertIn("exited", stop.exception.code)

    def test_a_new_server_is_remembered(self):
        self.page()
        with patch.object(serve_report.subprocess, "Popen") as popen:
            popen.return_value.pid = 456
            with patch.object(serve_report, "answers", return_value=True):
                main([])
        self.assertEqual(json.loads(state_file(self.root).read_text())["pid"], 456)

    def test_open_starts_the_browser_on_the_page(self):
        self.page()
        self.remember()
        with patch.object(serve_report, "running", return_value=self.state):
            with patch.object(serve_report.subprocess, "run") as run:
                main(["--open"])
        self.assertEqual(run.call_args.args[0][0], "open")

    def test_no_state_means_nothing_running(self):
        self.assertIsNone(running(self.root))

    def test_a_port_nothing_listens_on_does_not_answer(self):
        with socket.socket() as spare:
            spare.bind((HOST, 0))
            self.assertFalse(answers(spare.getsockname()[1]))


if __name__ == "__main__":
    unittest.main()
