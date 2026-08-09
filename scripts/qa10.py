#!/usr/bin/env python3
"""Execute the production-critical MemoryV4 QA10 gates and emit a truthful scorecard."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "qa10-scorecard.json"

GATES = [
    (
        "authentication-permissions",
        [
            "tests/test_contract.py",
            "tests/test_governance.py::test_action_permissions_and_canonical_creation_boundary",
            "tests/test_operational_apis.py::test_findings_are_scoped_filterable_paginated_and_permissioned",
            "tests/test_operational_apis.py::test_audit_and_retrieval_event_apis_filter_scope_time_and_cursors",
            "tests/test_operational_apis.py::test_usage_requires_admin_and_returns_governed_counts",
        ],
    ),
    (
        "scope-isolation",
        [
            "tests/test_production_qa.py::test_tenant_project_agent_user_and_session_isolation",
            "tests/test_core_objects.py::test_reference_integrity_and_scope_boundaries",
        ],
    ),
    (
        "write-policy-promotion",
        [
            "tests/test_governance.py::test_write_policies_versions_and_canonical_immutability",
            "tests/test_governance.py::test_promotion_requires_permission_version_reason_and_is_idempotent",
        ],
    ),
    (
        "lifecycle-supersession",
        ["tests/test_lifecycle.py"],
    ),
    (
        "pagination-filtering-search",
        [
            "tests/test_core_objects.py::test_entity_composite_identity_pagination_update_and_visibility",
            "tests/test_core_objects.py::test_record_relation_artifact_search_and_context_graph",
            "tests/test_operational_apis.py::test_findings_are_scoped_filterable_paginated_and_permissioned",
            "tests/test_operational_apis.py::test_audit_and_retrieval_event_apis_filter_scope_time_and_cursors",
        ],
    ),
    (
        "concurrency-idempotency-retries",
        [
            "tests/test_governance.py::test_concurrent_idempotency_claim_creates_once",
            "tests/test_lifecycle.py::test_concurrent_supersession_claim_creates_one_replacement",
            "tests/test_lifecycle.py::test_concurrent_transition_claim_mutates_once",
            "tests/test_operational_apis.py::test_concurrent_finding_resolution_commits_once",
        ],
    ),
    (
        "audit-success-denial",
        [
            "tests/test_governance.py::test_idempotency_actor_derivation_and_denial_audit",
            "tests/test_operational_apis.py::test_finding_resolution_is_governed_idempotent_versioned_and_audited",
            "tests/test_operational_apis.py::test_resolution_hides_sibling_findings_and_audits_denial",
        ],
    ),
    (
        "content-limits-malformed-input",
        ["tests/test_production_qa.py::test_content_and_structured_field_limits_fail_closed"],
    ),
    (
        "migrations-rollback",
        [
            "tests/test_foundation.py::test_migrations_are_idempotent_and_reversible",
            "tests/test_persistence_recovery.py::test_atomic_migration_claim_rolls_back_failed_schema_change",
            "tests/test_persistence_recovery.py::test_atomic_migration_rollback_claim_survives_injected_down_failure",
            "tests/test_persistence_recovery.py::test_migration_history_checksum_gap_and_future_version_fail_closed",
        ],
    ),
    (
        "backup-restore-restart-crash-recovery",
        [
            "tests/test_persistence_recovery.py::test_online_backup_manifest_integrity_and_json_corruption_detection",
            "tests/test_persistence_recovery.py::test_restore_is_offline_only_and_preserves_verified_snapshot",
            "tests/test_persistence_recovery.py::test_interrupted_restore_marker_completes_before_startup",
            "tests/test_persistence_recovery.py::test_wal_commits_survive_abrupt_process_exit_and_restart",
            "tests/test_persistence_recovery.py::test_recovery_cli_backup_verify_and_restore_round_trip",
        ],
    ),
]


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    gate_results = []
    started = time.monotonic()
    for name, selectors in GATES:
        gate_started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *selectors],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        result = {
            "name": name,
            "status": "pass" if completed.returncode == 0 else "fail",
            "duration_seconds": round(time.monotonic() - gate_started, 3),
            "evidence": (completed.stdout + completed.stderr).strip(),
        }
        gate_results.append(result)
        print(f"[{result['status'].upper()}] {name}: {result['evidence']}")
        if completed.returncode != 0:
            break

    passed = len(gate_results) == len(GATES) and all(
        result["status"] == "pass" for result in gate_results
    )
    scorecard = {
        "schema": "memoryv4-qa10/v1",
        "status": "pass" if passed else "fail",
        "scope": "production-critical-core",
        "passed": sum(result["status"] == "pass" for result in gate_results),
        "total": len(GATES),
        "duration_seconds": round(time.monotonic() - started, 3),
        "gates": gate_results,
        "external_release_gates": [
            "UNIFY adapter and framework tools: owned and executed by the UNIFY QA suite",
            "UNIUI workflows: owned and executed by the UNIFY QA suite",
            "live topology, backup drill, and restart smoke: recorded in production release evidence",
        ],
    }
    OUTPUT.write_text(json.dumps(scorecard, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: scorecard[key] for key in ("status", "passed", "total")}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
