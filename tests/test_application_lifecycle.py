"""Real temporary SQLite/API regressions documenting the hard-delete blocker.

These tests intentionally prove retention, NOT successful deletion. Core must return
False until owner-side scrub covers these copies and receipt-unbound retrieval logs,
provenance, linked artifacts (including external URIs), and supersession relations.
No fixtures here claim fetched source truth; all strings are synthetic test content.
Backup expiry remains an operator policy; no enforced expiry is claimed.
"""
import json
import sqlite3

from fastapi.testclient import TestClient

from app.main import create_app


SCOPE = "org:test/project:test/application:11111111-1111-4111-8111-111111111111/subject:dns"


def test_transition_and_patch_do_not_scrub_historical_idempotent_copies(tmp_path, monkeypatch):
    path = tmp_path / "application.sqlite3"
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(path))
    monkeypatch.setenv("MEMORYV4_API_KEYS", json.dumps({"test-admin": {
        "actor": "unify:test", "scope_path": SCOPE, "permissions": ["memory.admin"]
    }}))
    headers = {"Authorization": "Bearer test-admin"}
    payload = {"title": "synthetic private title", "content": "synthetic-private-original",
               "role": "active", "lifecycle": "working", "scope_path": SCOPE,
               "provenance": {"quote": "synthetic-private-original"},
               "attrs": {"receiptId": "22222222-2222-4222-8222-222222222222"}}
    create_headers = {**headers, "Idempotency-Key": "application-create-test"}
    with TestClient(create_app()) as client:
        first = client.post("/records", headers=create_headers, json=payload)
        assert first.status_code == 201, first.text
        rid = first.json()["id"]
        patch = client.patch(f"/records/{rid}", headers={**headers,
            "Idempotency-Key": "application-patch-test", "If-Match": "1"},
            json={"content": "replacement", "provenance": {}})
        assert patch.status_code == 200, patch.text
        archived = client.post(f"/records/{rid}/transition", headers={**headers,
            "Idempotency-Key": "application-archive-test", "If-Match": "2",
            "X-MemoryV4-Reason": "synthetic retention regression"}, json={"lifecycle": "archived"})
        assert archived.status_code == 200, archived.text
        replay = client.post("/records", headers=create_headers, json=payload)
        assert replay.status_code == 201
        assert replay.json()["content"] == "synthetic-private-original"
        assert replay.json()["provenance"]["quote"] == "synthetic-private-original"
        deletion = client.delete(f"/records/{rid}", headers=headers)
        assert deletion.status_code == 405
        with sqlite3.connect(path) as conn:
            cached = conn.execute("SELECT response_json FROM idempotency_requests").fetchall()
            assert any("synthetic-private-original" in row[0] for row in cached)
            retained = conn.execute("SELECT content FROM records WHERE id=?", (rid,)).fetchone()
            assert retained == ("replacement",)


def test_supersession_preserves_history_and_exact_scope_authorization(tmp_path, monkeypatch):
    path = tmp_path / "lineage.sqlite3"
    monkeypatch.setenv("MEMORYV4_DB_PATH", str(path))
    monkeypatch.setenv("MEMORYV4_API_KEYS", json.dumps({"test-admin": {
        "actor": "unify:test", "scope_path": SCOPE, "permissions": ["memory.admin"]
    }}))
    headers = {"Authorization": "Bearer test-admin"}
    with TestClient(create_app()) as client:
        payload = {"title": "synthetic", "content": "historical-private-quote", "role": "active",
                   "lifecycle": "working", "scope_path": SCOPE}
        denied = client.post("/records", headers={**headers, "Idempotency-Key": "outside-scope-test"},
                             json={**payload, "scope_path": "org:other"})
        assert denied.status_code == 403
        first = client.post("/records", headers={**headers, "Idempotency-Key": "lineage-create-test"}, json=payload)
        assert first.status_code == 201
        rid = first.json()["id"]
        promotion_headers = {**headers, "Idempotency-Key": "lineage-promote-test", "If-Match": "1",
                             "X-MemoryV4-Reason": "synthetic policy test"}
        promoted = client.post(f"/records/{rid}/promote", headers=promotion_headers)
        assert promoted.status_code == 200
        supersede_headers = {**headers, "Idempotency-Key": "lineage-supersede-test", "If-Match": "2",
                             "X-MemoryV4-Reason": "synthetic correction test"}
        replacement = client.post(f"/records/{rid}/supersede", headers=supersede_headers,
                                  json={"content": "corrected-private-quote"})
        assert replacement.status_code == 200, replacement.text
        replay = client.post(f"/records/{rid}/supersede", headers=supersede_headers,
                             json={"content": "corrected-private-quote"})
        assert replay.json()["id"] == replacement.json()["id"]
        stale = client.post(f"/records/{rid}/supersede", headers={**supersede_headers,
            "Idempotency-Key": "lineage-stale-test"}, json={"content": "stale"})
        assert stale.status_code == 412
        with sqlite3.connect(path) as conn:
            rows = conn.execute("SELECT content, lifecycle FROM records").fetchall()
            assert ("historical-private-quote", "superseded") in rows
            assert ("corrected-private-quote", "live") in rows
            assert len(rows) == 2
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='records_fts'").fetchone():
                assert conn.execute("SELECT content FROM records_fts WHERE id=?", (rid,)).fetchone() == ("historical-private-quote",)
