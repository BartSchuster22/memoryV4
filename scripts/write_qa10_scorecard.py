from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    scorecard = {
        "score": 1,
        "max_score": 10,
        "status": "partial",
        "phase": "P2",
        "commit": commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gates": [
            {
                "id": 1,
                "name": "migrations",
                "status": "pass",
                "evidence": ["python3 -m pytest tests/test_migrations_p2.py -q"],
                "notes": "P2 additive migrations 0006-0012 pass fresh DB, P1 upgrade fixture, rerun/no-op, and down-path checks.",
            },
            *[
                {
                    "id": gate_id,
                    "name": name,
                    "status": "pending",
                    "evidence": [],
                    "notes": "Gate belongs to a later MemoryV4 phase.",
                }
                for gate_id, name in [
                    (2, "storage-seam parity"),
                    (3, "retrieval quality"),
                    (4, "governance ranking"),
                    (5, "supersession and contradiction integrity"),
                    (6, "provenance and orphan scan"),
                    (7, "idempotence and decay safety"),
                    (8, "scope isolation"),
                    (9, "security"),
                    (10, "durability"),
                ]
            ],
        ],
    }
    path = Path("build/qa10-scorecard.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scorecard, indent=2, sort_keys=True) + "\n")
    print(path.read_text(), end="")


if __name__ == "__main__":
    main()
