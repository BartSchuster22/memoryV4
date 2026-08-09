"""Deterministic, non-destructive MemoryV3 to MemoryV4 adoption tooling."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.migrations import migrate
from app.recovery import DatabaseLock
from app.schemas import Artifact, Entity, Record, Relation, ScopePath
from app.sqlite_runtime import connect_sqlite

SCHEMA = "memoryv3-memoryv4-adoption/v1"
ACTOR = "migration:memoryv3-adoption"
SOURCE_TABLES = (
    "entities",
    "records",
    "record_content",
    "relations",
    "artifacts",
    "explorer_items",
)


class AdoptionError(RuntimeError):
    """Fail-closed migration error."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)


def connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def require_tables(connection: sqlite3.Connection) -> None:
    present = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = sorted(set(SOURCE_TABLES) - present)
    if missing:
        raise AdoptionError(f"MemoryV3 snapshot is missing tables: {', '.join(missing)}")


def source_counts(connection: sqlite3.Connection) -> dict[str, int]:
    require_tables(connection)
    return {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in SOURCE_TABLES
    }


def logical_source_digest(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for table in SOURCE_TABLES:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        order = ",".join(f'"{column}"' for column in columns)
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            digest.update(table.encode())
            digest.update(b"\0")
            digest.update(canonical(dict(row)).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def create_snapshot(source: Path, output: Path) -> dict[str, Any]:
    """Create an SQLite online-backup snapshot from a query-only MemoryV3 source."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise AdoptionError(f"refusing to overwrite existing export: {output}")
    with connect_readonly(source) as source_connection:
        require_tables(source_connection)
        before = source_counts(source_connection)
        with sqlite3.connect(output) as destination:
            source_connection.backup(destination)
        after = source_counts(source_connection)
    if before != after:
        output.unlink(missing_ok=True)
        raise AdoptionError("MemoryV3 source counts changed while exporting; retry the snapshot")
    with connect_readonly(output) as snapshot:
        quick_check = snapshot.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise AdoptionError(f"export quick_check failed: {quick_check}")
        logical_sha256 = logical_source_digest(snapshot)
    os.chmod(output, 0o440)
    manifest = {
        "schema": SCHEMA,
        "kind": "source-export",
        "source_read_only": True,
        "source_unchanged": True,
        "snapshot": output.name,
        "snapshot_sha256": sha256_file(output),
        "logical_sha256": logical_sha256,
        "counts": before,
        "quick_check": quick_check,
    }
    write_json(output.with_suffix(output.suffix + ".manifest.json"), manifest)
    return manifest


def parse_json(raw: str, expected: type, field: str, object_id: str) -> Any:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AdoptionError(f"{object_id}: malformed {field}") from exc
    if not isinstance(value, expected):
        raise AdoptionError(f"{object_id}: {field} must decode to {expected.__name__}")
    return value


def relation_id(key: str) -> str:
    return f"rel_m3_{sha256_bytes(key.encode())[:32]}"


def provenance(source_digest: str, kind: str, source_id: str) -> dict[str, Any]:
    return {
        "adoption": {
            "source": "memory-v3",
            "source_kind": kind,
            "source_id": source_id,
            "source_logical_sha256": source_digest,
            "mapping_schema": SCHEMA,
        }
    }


def validated_entity(payload: dict[str, Any]) -> dict[str, Any]:
    return Entity.model_validate(payload).model_dump(mode="json")


def validated_record(payload: dict[str, Any]) -> dict[str, Any]:
    return Record.model_validate(payload).model_dump(mode="json")


def validated_relation(payload: dict[str, Any]) -> dict[str, Any]:
    return Relation.model_validate(payload).model_dump(mode="json", by_alias=True)


def validated_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    return Artifact.model_validate(payload).model_dump(mode="json")


def create_plan(snapshot: Path, output: Path, scope_path: str) -> dict[str, Any]:
    ScopePath.validate(scope_path)
    snapshot_sha = sha256_file(snapshot)
    with connect_readonly(snapshot) as connection:
        require_tables(connection)
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise AdoptionError("MemoryV3 snapshot failed quick_check")
        source_digest = logical_source_digest(connection)
        counts = source_counts(connection)
        exceptions: list[dict[str, Any]] = []
        entities: dict[tuple[str, str], dict[str, Any]] = {}

        for row in connection.execute("SELECT * FROM entities ORDER BY entity_type, entity_id"):
            attrs = parse_json(row["attributes_json"], dict, "attributes_json", row["entity_id"])
            attrs["memoryv3"] = {"state": row["state"], "source_entity": True}
            item = validated_entity(
                {
                    "id": row["entity_id"],
                    "entity_type": row["entity_type"],
                    "name": row["title"] or row["entity_id"],
                    "scope_path": scope_path,
                    "attrs": attrs,
                    "version": 1,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
            entities[(item["entity_type"], item["id"])] = item

        explorer = [
            dict(row)
            for row in connection.execute("SELECT * FROM explorer_items ORDER BY id")
        ]
        explorer_types = {
            row["id"]: "collection" if row["kind"] == "folder" else "document"
            for row in explorer
        }
        for row in explorer:
            attrs = parse_json(row["attributes_json"], dict, "attributes_json", row["id"])
            attrs["memoryv3"] = {
                "explorer_item": True,
                "kind": row["kind"],
                "parent_id": row["parent_id"],
                "slug": row["slug"],
                "record_id": row["record_id"],
                "artifact_id": row["artifact_id"],
                "favorite": bool(row["favorite"]),
                "last_opened_at": row["last_opened_at"],
                "deleted_at": row["deleted_at"],
            }
            item = validated_entity(
                {
                    "id": row["id"],
                    "entity_type": explorer_types[row["id"]],
                    "name": row["name"],
                    "scope_path": scope_path,
                    "attrs": attrs,
                    "version": 1,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
            key = (item["entity_type"], item["id"])
            if key in entities:
                raise AdoptionError(f"duplicate mapped entity: {key}")
            entities[key] = item

        source_relations = [
            dict(row) for row in connection.execute("SELECT * FROM relations ORDER BY id")
        ]
        missing_endpoints: set[tuple[str, str]] = set()
        for row in source_relations:
            for side in ("from", "to"):
                key = (row[f"{side}_entity_type"], row[f"{side}_entity_id"])
                if key not in entities:
                    missing_endpoints.add(key)
        for entity_type, entity_id in sorted(missing_endpoints):
            entities[(entity_type, entity_id)] = validated_entity(
                {
                    "id": entity_id,
                    "entity_type": entity_type,
                    "name": entity_id,
                    "scope_path": scope_path,
                    "attrs": {
                        "memoryv3": {
                            "synthesized_from_dangling_relation_endpoint": True,
                        }
                    },
                    "version": 1,
                    "created_at": "1970-01-01T00:00:00+00:00",
                    "updated_at": "1970-01-01T00:00:00+00:00",
                }
            )
        if missing_endpoints:
            exceptions.append(
                {
                    "classification": "resolved",
                    "code": "dangling-relation-endpoints",
                    "count": len(missing_endpoints),
                    "resolution": "synthesized provenance-marked placeholder entities",
                    "objects": [
                        f"{kind}:{identifier}"
                        for kind, identifier in sorted(missing_endpoints)
                    ],
                }
            )

        content = {
            row["record_id"]: row["content"]
            for row in connection.execute("SELECT record_id, content FROM record_content")
        }
        records: list[dict[str, Any]] = []
        for row in connection.execute("SELECT * FROM records ORDER BY id"):
            record_id = row["id"]
            if record_id not in content or not content[record_id]:
                raise AdoptionError(f"{record_id}: missing record content")
            tags = parse_json(row["tags_json"], list, "tags_json", record_id)
            source_refs = parse_json(
                row["source_refs_json"], list, "source_refs_json", record_id
            )
            supersedes = parse_json(
                row["supersedes_json"], list, "supersedes_json", record_id
            )
            if len(supersedes) > 1:
                raise AdoptionError(f"{record_id}: MemoryV4 supports one supersedes link")
            lifecycle = row["lifecycle"]
            previous_lifecycle = None
            deleted_at = None
            changed_at = None
            if lifecycle in {"archived", "expired", "superseded"}:
                previous_lifecycle = "live"
                changed_at = row["updated_at"]
            if lifecycle == "archived":
                deleted_at = row["deleted_at"] or row["updated_at"]
            if lifecycle == "superseded" and not row["superseded_by"]:
                raise AdoptionError(f"{record_id}: superseded lifecycle has no replacement")
            attrs = {
                "memoryv3": {
                    "schema_version": row["schema_version"],
                    "locked_by_author": bool(row["locked_by_author"]),
                    "deleted_by": row["deleted_by"],
                    "delete_mode": row["delete_mode"],
                }
            }
            records.append(
                validated_record(
                    {
                        "id": record_id,
                        "title": row["title"],
                        "content": content[record_id],
                        "role": row["role"],
                        "lifecycle": lifecycle,
                        "write_policy": row["write_policy"],
                        "scope_path": scope_path,
                        "entity": {
                            "entity_type": row["entity_type"],
                            "id": row["entity_id"],
                        },
                        "topic": row["topic"],
                        "tags": tags,
                        "confidence": row["confidence"],
                        "source_refs": source_refs,
                        "provenance": provenance(source_digest, "record", record_id),
                        "attrs": attrs,
                        "author_actor": row["author_actor"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "supersedes": supersedes[0] if supersedes else None,
                        "superseded_by": row["superseded_by"],
                        "previous_lifecycle": previous_lifecycle,
                        "lifecycle_changed_at": changed_at,
                        "deleted_at": deleted_at,
                        "version": 1,
                    }
                )
            )

        relations: list[dict[str, Any]] = []
        for row in source_relations:
            source_id = str(row["id"])
            attrs = parse_json(
                row["attributes_json"], dict, "attributes_json", f"relation:{source_id}"
            )
            item_provenance = provenance(source_digest, "relation", source_id)
            item_provenance["memoryv3_attributes"] = attrs
            relations.append(
                validated_relation(
                    {
                        "id": relation_id(f"source-relation:{source_id}"),
                        "from": {
                            "kind": "entity",
                            "entity_type": row["from_entity_type"],
                            "id": row["from_entity_id"],
                        },
                        "to": {
                            "kind": "entity",
                            "entity_type": row["to_entity_type"],
                            "id": row["to_entity_id"],
                        },
                        "relation_type": row["relation_type"],
                        "scope_path": scope_path,
                        "provenance": item_provenance,
                        "author_actor": ACTOR,
                        "version": 1,
                        "created_at": row["created_at"],
                        "updated_at": row["created_at"],
                    }
                )
            )

        record_ids = {record["id"] for record in records}
        source_artifact_ids = {
            row[0] for row in connection.execute("SELECT id FROM artifacts")
        }
        for row in explorer:
            source_id = row["id"]
            child_ref = {
                "kind": "entity",
                "entity_type": explorer_types[source_id],
                "id": source_id,
            }
            if row["parent_id"]:
                relations.append(
                    validated_relation(
                        {
                            "id": relation_id(f"explorer-parent:{source_id}"),
                            "from": {
                                "kind": "entity",
                                "entity_type": explorer_types[row["parent_id"]],
                                "id": row["parent_id"],
                            },
                            "to": child_ref,
                            "relation_type": "contains",
                            "scope_path": scope_path,
                            "provenance": provenance(
                                source_digest, "explorer-parent", source_id
                            ),
                            "author_actor": ACTOR,
                            "version": 1,
                            "created_at": row["created_at"],
                            "updated_at": row["updated_at"],
                        }
                    )
                )
            if row["record_id"]:
                if row["record_id"] not in record_ids:
                    raise AdoptionError(f"{source_id}: explorer record target is missing")
                relations.append(
                    validated_relation(
                        {
                            "id": relation_id(f"explorer-record:{source_id}"),
                            "from": child_ref,
                            "to": {"kind": "record", "id": row["record_id"]},
                            "relation_type": "represents",
                            "scope_path": scope_path,
                            "provenance": provenance(
                                source_digest, "explorer-record", source_id
                            ),
                            "author_actor": ACTOR,
                            "version": 1,
                            "created_at": row["created_at"],
                            "updated_at": row["updated_at"],
                        }
                    )
                )
            if row["artifact_id"] and row["artifact_id"] not in source_artifact_ids:
                raise AdoptionError(f"{source_id}: explorer artifact target is missing")

        artifacts: list[dict[str, Any]] = []
        for row in connection.execute("SELECT * FROM artifacts ORDER BY id"):
            artifact_provenance = provenance(source_digest, "artifact", row["id"])
            artifact_provenance["memoryv3"] = {
                "size_bytes": row["size_bytes"],
                "mime_type": row["mime_type"],
                "sensitive": bool(row["sensitive"]),
                "exportable": bool(row["exportable"]),
            }
            artifacts.append(
                validated_artifact(
                    {
                        "id": row["id"],
                        "record_id": row["record_id"],
                        "entity": {
                            "entity_type": row["entity_type"],
                            "id": row["entity_id"],
                        },
                        "artifact_type": row["mime_type"] or "file",
                        "uri": row["path"],
                        "checksum": f"sha256:{row['sha256']}",
                        "scope_path": scope_path,
                        "provenance": artifact_provenance,
                        "author_actor": ACTOR,
                        "version": 1,
                        "created_at": row["created_at"],
                        "updated_at": row["created_at"],
                    }
                )
            )

        excluded = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "audit_events",
                "retrieval_events",
                "stream_events",
                "identity_refs",
                "import_batches",
                "import_items",
                "backups",
                "web_users",
                "web_sessions",
            )
        }
        exceptions.append(
            {
                "classification": "expected",
                "code": "operational-state-not-replayed",
                "counts": excluded,
                "resolution": (
                    "preserved in the immutable source snapshot; authentication, old audits, "
                    "retrieval telemetry, import staging, and backup registry are not live objects"
                ),
            }
        )

    object_payload = {
        "entities": sorted(entities.values(), key=lambda row: (row["entity_type"], row["id"])),
        "records": records,
        "relations": sorted(relations, key=lambda row: row["id"]),
        "artifacts": artifacts,
    }
    object_sha = sha256_bytes(canonical(object_payload).encode())
    plan = {
        "schema": SCHEMA,
        "kind": "adoption-plan",
        "source": {
            "snapshot": snapshot.name,
            "snapshot_sha256": snapshot_sha,
            "logical_sha256": source_digest,
            "counts": counts,
            "source_unchanged": True,
        },
        "mapping": {
            "scope_path": scope_path,
            "record_ids_preserved": True,
            "entity_composite_ids_preserved": True,
            "explorer_folders": "collection entities plus contains relations",
            "explorer_files": "document entities plus represents relations",
            "missing_relation_endpoints": "provenance-marked placeholder entities",
            "operational_state": "snapshot-only; not replayed as live MemoryV4 operations",
        },
        "objects": object_payload,
        "object_sha256": object_sha,
        "counts": {kind: len(rows) for kind, rows in object_payload.items()},
        "exceptions": exceptions,
        "fatal_exceptions": 0,
    }
    write_json(output, plan)
    return plan


def load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("schema") != SCHEMA or plan.get("kind") != "adoption-plan":
        raise AdoptionError("unsupported adoption plan schema")
    actual = sha256_bytes(canonical(plan["objects"]).encode())
    if actual != plan.get("object_sha256"):
        raise AdoptionError("adoption plan object checksum mismatch")
    if plan.get("fatal_exceptions"):
        raise AdoptionError("adoption plan contains fatal exceptions")
    return plan


def entity_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["id"],
        row["entity_type"],
        row["name"],
        row["scope_path"],
        canonical(row["attrs"]),
        row["version"],
        row["created_at"],
        row["updated_at"],
    )


def record_values(row: dict[str, Any]) -> tuple[Any, ...]:
    entity = row.get("entity") or {}
    return (
        row["id"],
        row["title"],
        row["content"],
        row["role"],
        row["lifecycle"],
        row["scope_path"],
        entity.get("entity_type"),
        row.get("topic"),
        canonical(row["source_refs"]),
        canonical(row["provenance"]),
        canonical(row["attrs"]),
        row["author_actor"],
        row.get("superseded_by"),
        row["write_policy"],
        row["version"],
        entity.get("id"),
        canonical(row["tags"]),
        row.get("confidence"),
        row.get("supersedes"),
        row.get("deleted_at"),
        row.get("previous_lifecycle"),
        row.get("lifecycle_changed_at"),
        row["created_at"],
        row["updated_at"],
    )


def relation_values(row: dict[str, Any]) -> tuple[Any, ...]:
    source = row["from"]
    target = row["to"]
    return (
        row["id"],
        source["kind"],
        source.get("entity_type"),
        source["id"],
        target["kind"],
        target.get("entity_type"),
        target["id"],
        row["relation_type"],
        row["scope_path"],
        canonical(row["provenance"]),
        row["author_actor"],
        row["version"],
        row["created_at"],
        row["updated_at"],
    )


def artifact_values(row: dict[str, Any]) -> tuple[Any, ...]:
    entity = row.get("entity") or {}
    return (
        row["id"],
        row.get("record_id"),
        entity.get("entity_type"),
        entity.get("id"),
        row["artifact_type"],
        row["uri"],
        row.get("checksum"),
        row["scope_path"],
        canonical(row["provenance"]),
        row["author_actor"],
        row["version"],
        row["created_at"],
        row["updated_at"],
    )


OBJECT_SQL = {
    "entities": (
        "SELECT id,entity_type,name,scope_path,attrs_json,version,created_at,updated_at "
        "FROM entities WHERE entity_type=? AND id=?",
        "INSERT INTO entities(id,entity_type,name,scope_path,attrs_json,version,"
        "created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        entity_values,
    ),
    "records": (
        "SELECT id,title,content,role,lifecycle,scope_path,entity_type,topic,source_refs_json,"
        "provenance_json,attrs_json,author_actor,superseded_by,write_policy,version,entity_id,"
        "tags_json,confidence,supersedes,deleted_at,previous_lifecycle,lifecycle_changed_at,"
        "created_at,updated_at FROM records WHERE id=?",
        "INSERT INTO records(id,title,content,role,lifecycle,scope_path,entity_type,topic,"
        "source_refs_json,provenance_json,attrs_json,author_actor,superseded_by,write_policy,"
        "version,entity_id,tags_json,confidence,supersedes,deleted_at,previous_lifecycle,"
        "lifecycle_changed_at,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        record_values,
    ),
    "relations": (
        "SELECT id,from_kind,from_entity_type,from_id,to_kind,to_entity_type,to_id,relation_type,"
        "scope_path,provenance_json,author_actor,version,created_at,updated_at "
        "FROM relations WHERE id=?",
        "INSERT INTO relations(id,from_kind,from_entity_type,from_id,to_kind,to_entity_type,to_id,"
        "relation_type,scope_path,provenance_json,author_actor,version,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        relation_values,
    ),
    "artifacts": (
        "SELECT id,record_id,entity_type,entity_id,artifact_type,uri,checksum,scope_path,"
        "provenance_json,author_actor,version,created_at,updated_at FROM artifacts WHERE id=?",
        "INSERT INTO artifacts(id,record_id,entity_type,entity_id,artifact_type,uri,checksum,"
        "scope_path,provenance_json,author_actor,version,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        artifact_values,
    ),
}


def apply_plan(plan_path: Path, target: Path, dry_run: bool = False) -> dict[str, Any]:
    plan = load_plan(plan_path)
    result = {
        "schema": SCHEMA,
        "dry_run": dry_run,
        "committed": False,
        "created": {kind: 0 for kind in OBJECT_SQL},
        "existing": {kind: 0 for kind in OBJECT_SQL},
        "mismatches": [],
    }
    with DatabaseLock(target, exclusive=True):
        migrate(target)
        with connect_sqlite(target) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for kind in ("entities", "records", "relations", "artifacts"):
                    select_sql, insert_sql, values_fn = OBJECT_SQL[kind]
                    for row in plan["objects"][kind]:
                        params = values_fn(row)
                        key = (
                            (row["entity_type"], row["id"])
                            if kind == "entities"
                            else (row["id"],)
                        )
                        existing = connection.execute(select_sql, key).fetchone()
                        if existing is not None:
                            if tuple(existing) != params:
                                result["mismatches"].append({"kind": kind, "id": row["id"]})
                            else:
                                result["existing"][kind] += 1
                            continue
                        connection.execute(insert_sql, params)
                        if kind == "records":
                            connection.execute(
                                "INSERT INTO records_fts(id,title,content) VALUES(?,?,?)",
                                (row["id"], row["title"], row["content"]),
                            )
                        result["created"][kind] += 1
                if result["mismatches"]:
                    raise AdoptionError(
                        f"target has {len(result['mismatches'])} conflicting object IDs"
                    )
                if dry_run:
                    connection.rollback()
                else:
                    total_created = sum(result["created"].values())
                    if total_created:
                        connection.execute(
                            "INSERT INTO audit_events(action,object_type,object_id,actor,"
                            "scope_path,"
                            "detail_json,created_at) VALUES(?,?,?,?,?,?,datetime('now'))",
                            (
                                "adoption.memoryv3",
                                "migration",
                                plan["source"]["logical_sha256"],
                                ACTOR,
                                plan["mapping"]["scope_path"],
                                canonical(
                                    {
                                        "schema": SCHEMA,
                                        "plan_object_sha256": plan["object_sha256"],
                                        "created": result["created"],
                                        "source_counts": plan["source"]["counts"],
                                    }
                                ),
                            ),
                        )
                    connection.commit()
                    result["committed"] = True
            except Exception:
                connection.rollback()
                raise
    return result


def reconcile(plan_path: Path, target: Path) -> dict[str, Any]:
    plan = load_plan(plan_path)
    report = {
        "schema": SCHEMA,
        "status": "pass",
        "matched": {kind: 0 for kind in OBJECT_SQL},
        "missing": [],
        "mismatched": [],
        "source_counts": plan["source"]["counts"],
        "planned_counts": plan["counts"],
        "exceptions": plan["exceptions"],
    }
    with connect_sqlite(target, readonly=True) as connection:
        for kind, (select_sql, _, values_fn) in OBJECT_SQL.items():
            for row in plan["objects"][kind]:
                key = (row["entity_type"], row["id"]) if kind == "entities" else (row["id"],)
                existing = connection.execute(select_sql, key).fetchone()
                if existing is None:
                    report["missing"].append({"kind": kind, "id": row["id"]})
                elif tuple(existing) != values_fn(row):
                    report["mismatched"].append({"kind": kind, "id": row["id"]})
                else:
                    report["matched"][kind] += 1
        imported_record_ids = [row["id"] for row in plan["objects"]["records"]]
        fts_count = 0
        for start in range(0, len(imported_record_ids), 500):
            chunk = imported_record_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            fts_count += connection.execute(
                f"SELECT COUNT(*) FROM records_fts WHERE id IN ({placeholders})", chunk
            ).fetchone()[0]
        report["fts_records"] = fts_count
        report["integrity"] = connection.execute("PRAGMA quick_check").fetchone()[0]
    if (
        report["missing"]
        or report["mismatched"]
        or report["fts_records"] != plan["counts"]["records"]
        or report["integrity"] != "ok"
    ):
        report["status"] = "fail"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--source", type=Path, required=True)
    snapshot_parser.add_argument("--output", type=Path, required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--snapshot", type=Path, required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--scope", default="org:aquiero")
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--target", type=Path, required=True)
    apply_parser.add_argument("--dry-run", action="store_true")
    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("--plan", type=Path, required=True)
    reconcile_parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "snapshot":
        output = create_snapshot(args.source, args.output)
    elif args.command == "plan":
        output = create_plan(args.snapshot, args.output, args.scope)
    elif args.command == "apply":
        output = apply_plan(args.plan, args.target, args.dry_run)
    else:
        output = reconcile(args.plan, args.target)
    print(json.dumps(output, indent=2, sort_keys=True))
    if args.command == "reconcile" and output["status"] != "pass":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
