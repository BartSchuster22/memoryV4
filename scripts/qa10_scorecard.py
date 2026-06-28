from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

PENDING_GATES = [
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

scorecard = {
    "score": 1,
    "max_score": 10,
    "status": "pending",
    "phase": "P2",
    "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "gates": [
        {
            "id": 1,
            "name": "migrations",
            "status": "pass",
            "evidence": ["python3 -m pytest -q tests/test_migrations_p2.py"],
            "notes": "0001→0012 fresh DB, seeded V3-like upgrade, rerun no-op, additive P2 down paths, and sqlite-vec fallback feature flag covered.",
        },
        *[
            {
                "id": gate_id,
                "name": name,
                "status": "pending",
                "evidence": [],
                "notes": "Scheduled for a later MemoryV4 phase.",
            }
            for gate_id, name in PENDING_GATES
        ],
    ],
}

Path("build").mkdir(exist_ok=True)
Path("build/qa10-scorecard.json").write_text(json.dumps(scorecard, indent=2, sort_keys=True) + "\n")
print(json.dumps(scorecard, indent=2, sort_keys=True))
