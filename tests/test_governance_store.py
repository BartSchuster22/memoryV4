import sqlite3

import pytest

from app.migrations import migrate_down, migrate_up
from app.schemas import Lifecycle, MemoryRecord, MemoryRole, SourceRef, validate_lifecycle_transition
from app.sqlite_store import SqliteStore


def test_lifecycle_transition_validation_rejects_invalid_regression() -> None:
    assert validate_lifecycle_transition(Lifecycle.WORKING, Lifecycle.LIVE) is None

    with pytest.raises(ValueError, match="Invalid lifecycle transition"):
        validate_lifecycle_transition(Lifecycle.ARCHIVED, Lifecycle.LIVE)


def test_sqlite_store_creates_records_and_audit_events() -> None:
    store = SqliteStore.in_memory()
    record = MemoryRecord(
        id="rec_a",
        role=MemoryRole.CANONICAL,
        lifecycle=Lifecycle.WORKING,
        scope="tenant/project",
        content="memory governance spine",
        source_refs=[SourceRef(kind="chat", uri="session://abc")],
        author_actor="chatboard",
        write_policy="verification_required",
    )

    store.create_record(record, actor="chatboard")

    assert store.get_record("rec_a") == record
    audits = store.list_audit_events(record_id="rec_a")
    assert [(event.action, event.actor, event.record_id) for event in audits] == [
        ("record.create", "chatboard", "rec_a")
    ]


def test_lifecycle_transition_writes_audit_event() -> None:
    store = SqliteStore.in_memory()
    store.create_record(
        MemoryRecord(
            id="rec_live",
            role=MemoryRole.ACTIVE,
            lifecycle=Lifecycle.WORKING,
            scope="tenant/project",
            content="candidate memory",
            author_actor="verifier",
            write_policy="verified_only",
        ),
        actor="writer",
    )

    store.transition_lifecycle("rec_live", Lifecycle.LIVE, actor="verifier")

    assert store.get_record("rec_live").lifecycle is Lifecycle.LIVE
    assert [event.action for event in store.list_audit_events(record_id="rec_live")] == [
        "record.create",
        "record.lifecycle_transition",
    ]


def test_supersession_sets_old_record_superseded_and_preserves_integrity() -> None:
    store = SqliteStore.in_memory()
    store.create_record(
        MemoryRecord(
            id="rec_old",
            role=MemoryRole.CANONICAL,
            lifecycle=Lifecycle.LIVE,
            scope="tenant/project",
            content="old canonical memory",
            author_actor="human",
            write_policy="verified_only",
        ),
        actor="human",
    )
    store.create_record(
        MemoryRecord(
            id="rec_new",
            role=MemoryRole.CANONICAL,
            lifecycle=Lifecycle.LIVE,
            scope="tenant/project",
            content="new canonical memory",
            author_actor="human",
            write_policy="verified_only",
        ),
        actor="human",
    )

    store.supersede_record("rec_old", "rec_new", actor="human", reason="updated fact")

    old = store.get_record("rec_old")
    assert old.lifecycle is Lifecycle.SUPERSEDED
    assert old.superseded_by == "rec_new"
    assert store.list_supersessions()[0].old_record_id == "rec_old"
    assert store.list_supersessions()[0].new_record_id == "rec_new"
    assert store.assert_supersession_integrity() is None


def test_keyword_lexical_rank_and_vector_placeholder_degraded_behavior() -> None:
    store = SqliteStore.in_memory()
    store.create_record(
        MemoryRecord(
            id="rec_kw",
            role=MemoryRole.EVIDENCE,
            lifecycle=Lifecycle.LIVE,
            scope="tenant/project",
            content="alpha keyword evidence",
            author_actor="agent",
            write_policy="append_only",
        ),
        actor="agent",
    )

    lexical = store.lexical_rank("keyword", scope="tenant/project", limit=5)
    vector = store.vector_rank([0.1, 0.2], scope="tenant/project", limit=5)

    assert [item.record_id for item in lexical] == ["rec_kw"]
    assert lexical[0].degraded is False
    assert vector == []
    events = store.list_retrieval_events()
    assert [(event.lane, event.degraded) for event in events] == [
        ("lexical", False),
        ("vector", True),
    ]


def test_migrations_apply_through_0012_and_down_removes_new_objects() -> None:
    conn = sqlite3.connect(":memory:")

    migrate_up(conn)

    versions = [row[0] for row in conn.execute("select version from schema_migrations order by version")]
    assert versions[-1] == "0012"
    assert "records" in _table_names(conn)
    assert "retrieval_events" in _table_names(conn)

    migrate_down(conn, target_version="0000")

    assert _table_names(conn) == {"schema_migrations"}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
        )
    }
