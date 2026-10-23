#!/usr/bin/env python3
"""Serve a local HTML page on 127.0.0.1 and print its URL.

    serve_page.py <file> [--open]    serve the file's directory, reusing a running server
    serve_page.py <file> --stop      stop the server for the file's directory

One server per directory, remembered in ~/.cache/local-page/. --open hands the
page to VS Code's Integrated Browser through the worktask extension's link.
"""
import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

STATE_DIR = Path.home() / ".cache" / "local-page"
HOST = "127.0.0.1"


def state_file(root):
    return STATE_DIR / (hashlib.sha1(str(root).encode()).hexdigest()[:16] + ".json")


def answers(port):
    try:
        with socket.create_connection((HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def command(pid):
    """The process's command line, or "" when it has gone."""
    return subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout.strip()


def ours(state):
    """Is the recorded pid still the http.server start() spawned for this port and directory? A
    pid reused after the server exited, by a reboot or a crash, is someone else's process."""
    return command(state["pid"]).endswith(
        f"-m http.server {state['port']} --bind {HOST} --directory {state['root']}")


def running(root):
    """The {pid, port} of this directory's server, if it is still up."""
    try:
        state = json.loads(state_file(root).read_text())
    except (OSError, ValueError):
        return None
    if state.get("root") == str(root) and alive(state["pid"]) and ours(state) and answers(state["port"]):
        return state
    return None


def free_port():
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def start(root):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    port = free_port()
    log = open(STATE_DIR / (state_file(root).stem + ".log"), "a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", HOST, "--directory", str(root)],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(50):
        if answers(port):
            break
        if proc.poll() is not None:
            sys.exit(f"serve_page: the server for {root} exited; see {log.name}")
        time.sleep(0.1)
    else:
        sys.exit(f"serve_page: the server for {root} did not answer on port {port}")
    state = {"pid": proc.pid, "port": port, "root": str(root)}
    state_file(root).write_text(json.dumps(state))
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file")
    parser.add_argument("--open", action="store_true", help="open it in VS Code's Integrated Browser")
    parser.add_argument("--stop", action="store_true", help="stop the server for the file's directory")
    args = parser.parse_args()

    page = Path(args.file).expanduser().resolve()
    root = page.parent
    if args.stop:
        # Signalled only when the pid is still the server's own; a slow server that doesn't answer
        # is stopped too, rather than left running untracked.
        try:
            state = json.loads(state_file(root).read_text())
        except (OSError, ValueError):
            state = None
        if state and state.get("root") == str(root) and alive(state["pid"]) and ours(state):
            os.kill(state["pid"], signal.SIGTERM)
            print(f"stopped the server on port {state['port']} for {root}")
        else:
            print(f"no server running for {root}")
        state_file(root).unlink(missing_ok=True)
        return
    if not page.is_file():
        sys.exit(f"serve_page: no such file: {page}")

    state = running(root) or start(root)
    url = f"http://{HOST}:{state['port']}/{urllib.parse.quote(page.name)}"
    link = "vscode://pjfitzgibbons.worktask/browse?url=" + urllib.parse.quote(url, safe="")
    print(url)
    print(link)
    if args.open:
        subprocess.run(["open", link], check=True)


if __name__ == "__main__":
    main()
