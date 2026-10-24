"""Read-only gh helpers shared by the scripts. Nothing here writes to GitHub."""

import json
import subprocess
import sys


def gh_json(args: list[str], repo: str | None = None):
    cmd = ["gh", *args]
    if repo:
        cmd += ["--repo", repo]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"gh failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return json.loads(result.stdout)
