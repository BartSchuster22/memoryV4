#!/usr/bin/env python3
"""Silent review-gate/deadlock check for MemoryV4.

Alerts when HEAD has implementation commits after the last review marker.
The marker is artifacts/watchdogs/last_reviewed_commit and should contain the
SHA approved by ulrich/test gates. Absence of the marker is healthy until a
caller explicitly sets MEMORYV4_REQUIRE_REVIEW_MARKER=1.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def git(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()


def main() -> int:
    repo = Path(os.environ.get("MEMORYV4_REPO", Path(__file__).resolve().parents[2]))
    marker = repo / "artifacts" / "watchdogs" / "last_reviewed_commit"
    if not (repo / ".git").exists():
        return 0
    head = git(["rev-parse", "HEAD"], repo)
    if not marker.exists():
        if os.environ.get("MEMORYV4_REQUIRE_REVIEW_MARKER") == "1":
            print(f"memoryV4 review-gate: no review marker for HEAD {head}; route implementation review to ulrich")
        return 0
    reviewed = marker.read_text(encoding="utf-8").strip()
    if not reviewed or reviewed == head:
        return 0
    try:
        count = git(["rev-list", "--count", f"{reviewed}..HEAD"], repo)
    except Exception:
        print(f"memoryV4 review-gate: marker {reviewed} is not an ancestor of HEAD {head}; route to ulrich")
        return 0
    if count != "0":
        print(f"memoryV4 review-gate: {count} commit(s) after last ulrich marker {reviewed}; do not self-approve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
