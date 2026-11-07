#!/usr/bin/env python3
"""Serve the milestone page on 127.0.0.1, with a /regen endpoint behind its Regenerate button.

    report-server [--open]    serve .tmp/milestones/ of the repo it's run in, reusing a running server
    report-server --stop      stop that repo's server

POST /regen reruns milestones.py in the repo, with the options the page was written with, and
answers 204 once the new page is written, or 500 with the script's error. It only takes a POST from its own page (the Origin header): any web page
in a browser can post to 127.0.0.1, and a regen it didn't ask for would still read Redmine. One
server per repo, on a port worked out from its path (8700-8799) so the address stays the same
across restarts, and remembered in ~/.cache/plan-report/. --open opens the page in the system
browser, as the local-page skill does.
"""

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from milestones import PAGE, Refused, repo_root, saved_options

HOST = "127.0.0.1"
STATE_DIR = Path.home() / ".cache" / "plan-report"
MILESTONES = Path(__file__).resolve().with_name("milestones.py")


class Handler(SimpleHTTPRequestHandler):
    root: Path
    port: int
    lock = threading.Lock()  # one regen at a time; a second click waits for the first

    def do_POST(self):
        if self.path != "/regen":
            return self.send_error(404)
        own = f"http://{HOST}:{self.port}"
        if self.headers.get("Origin") != own:
            return self.say(403, f"Regenerate only from this server's page: {own}/{PAGE.name}")
        failure = self.regenerate()
        if failure:
            return self.say(500, f"report-milestones failed: {failure}")
        self.send_response(204)
        self.end_headers()

    def regenerate(self) -> str | None:
        """Rerun milestones.py; what went wrong, or None once the new page is written."""
        options = saved_options(self.root / PAGE)
        with self.lock:
            try:
                run = subprocess.run(
                    [sys.executable, str(MILESTONES), *options],
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                return "no answer from Redmine in 2 minutes"
            except OSError as e:
                return f"can't run it in {self.root}: {e}"
        if run.returncode == 0:
            return None
        return (run.stderr or run.stdout).strip()[-2000:] or f"exit {run.returncode}"

    def say(self, status: int, message: str) -> None:
        """Plain text, which the page's Regenerate shows as it is."""
        body = message.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # a reload always shows the new page
        super().end_headers()


def serve(root: Path, port: int):
    Handler.root, Handler.port = root, port
    handler = partial(Handler, directory=str(root / PAGE.parent))
    ThreadingHTTPServer((HOST, port), handler).serve_forever()


def state_file(root):
    return STATE_DIR / (hashlib.sha1(str(root).encode()).hexdigest()[:16] + ".json")


def answers(port):
    try:
        with socket.create_connection((HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


def ours(state):
    """Is the recorded pid still this repo's server? A pid reused after a reboot or crash isn't."""
    try:
        os.kill(state["pid"], 0)
    except OSError:
        return False
    cmd = subprocess.run(
        ["ps", "-p", str(state["pid"]), "-o", "command="], capture_output=True, text=True
    ).stdout.strip()
    return cmd.endswith(f"--serve {state['port']} --root {state['root']}")


def running(root):
    try:
        state = json.loads(state_file(root).read_text())
    except (OSError, ValueError):
        return None
    return (
        state if state.get("root") == str(root) and ours(state) and answers(state["port"]) else None
    )


def port_for(root):
    """The repo's own port, worked out from its path so the page's address survives a restart; any
    free port when another process holds it."""
    preferred = 8700 + int(hashlib.sha1(str(root).encode()).hexdigest()[:8], 16) % 100
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind((HOST, port))
            except OSError:
                continue
            return s.getsockname()[1]


def start(root):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    port = port_for(root)
    log = open(STATE_DIR / (state_file(root).stem + ".log"), "a")
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", str(port), "--root", str(root)],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    for _ in range(50):
        if answers(port):
            break
        if proc.poll() is not None:
            sys.exit(f"report-server: the server for {root} exited; see {log.name}")
        time.sleep(0.1)
    else:
        sys.exit(f"report-server: the server for {root} did not answer on port {port}")
    state = {"pid": proc.pid, "port": port, "root": str(root)}
    state_file(root).write_text(json.dumps(state))
    return state


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--open", action="store_true", help="open it in the system browser")
    p.add_argument("--stop", action="store_true", help="stop this repo's server")
    p.add_argument("--serve", type=int, help=argparse.SUPPRESS)  # the server process itself
    p.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.serve:
        return serve(a.root, a.serve)

    try:
        root = repo_root()
    except (Refused, OSError) as e:
        sys.exit(f"report-server: {e}")
    if a.stop:
        try:
            state = json.loads(state_file(root).read_text())
        except (OSError, ValueError):
            state = None
        if state and state.get("root") == str(root) and ours(state):
            os.kill(state["pid"], signal.SIGTERM)
            print(f"stopped the server on port {state['port']} for {root}")
        else:
            print(f"no server running for {root}")
        state_file(root).unlink(missing_ok=True)
        return
    if not (root / PAGE).is_file():
        sys.exit(f"report-server: no {PAGE} in {root}; run report-milestones first")

    state = running(root) or start(root)
    url = f"http://{HOST}:{state['port']}/{PAGE.name}"
    print(url)
    if a.open:
        subprocess.run(["open", url], check=True)


if __name__ == "__main__":
    main()
