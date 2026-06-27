#!/usr/bin/env python3
"""Silent no-progress detector for MemoryV4 board-local runs.

Prints one alert line only when the repository has uncommitted work and the
latest commit is older than MEMORYV4_NO_PROGRESS_MAX_AGE_SECONDS.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> str:
    return subprocess.check_output(cmd, cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()


def main() -> int:
    repo = Path(os.environ.get("MEMORYV4_REPO", Path(__file__).resolve().parents[2]))
    max_age = int(os.environ.get("MEMORYV4_NO_PROGRESS_MAX_AGE_SECONDS", "3600"))
    if not (repo / ".git").exists():
        return 0
    status = run(["git", "status", "--porcelain"], repo)
    if not status:
        return 0
    try:
        last_commit = int(run(["git", "log", "-1", "--format=%ct"], repo))
    except Exception:
        return 0
    age = int(time.time()) - last_commit
    if age > max_age:
        print(
            f"memoryV4 no-progress: dirty tree with latest commit age {age}s > {max_age}s; "
            "preserve a patch under artifacts/patches/ and comment/block the Kanban card if stalled"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
