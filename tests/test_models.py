from __future__ import annotations

import pytest

from app.models import (
    Artifact,
    AuditEvent,
    Entity,
    Lifecycle,
    Record,
    Relation,
    RetrievalEvent,
    Role,
)


def test_governed_object_models_accept_only_canonical_roles_and_lifecycles() -> None:
    record = Record(
        id="rec_1",
        entity_id="ent_1",
        entity_type="decision",
        title="Storage decision",
        topic="storage",
        content="SQLite-only in P1.",
        role="canonical",
        lifecycle="live",
        author_actor="human:operator",
        write_policy={"promote_requires": ["promote"], "supersede_requires": ["supersede"]},
    )

    assert record.role is Role.CANONICAL
    assert record.lifecycle is Lifecycle.LIVE
    assert record.author_actor == "human:operator"
    assert record.write_policy == {"promote_requires": ["promote"], "supersede_requires": ["supersede"]}

    with pytest.raises(ValueError, match="invalid role"):
        Record(
            id="rec_bad_role",
            entity_type="decision",
            title="Bad role",
            topic="storage",
            content="Invalid role must not pass validation.",
            role="draft",
            lifecycle="live",
            author_actor="human:operator",
            write_policy={},
        )

    with pytest.raises(ValueError, match="invalid lifecycle"):
        Record(
            id="rec_bad_lifecycle",
            entity_type="decision",
            title="Bad lifecycle",
            topic="storage",
            content="Invalid lifecycle must not pass validation.",
            role="active",
            lifecycle="deleted",
            author_actor="human:operator",
            write_policy={},
        )

    with pytest.raises(ValueError, match="author_actor must not be empty"):
        Record(
            id="rec_missing_actor",
            entity_type="decision",
            title="Missing actor",
            topic="storage",
            content="Governed records must name the proposing or writing actor.",
            role="active",
            lifecycle="working",
            author_actor="",
            write_policy={},
        )


def test_core_governed_models_share_scope_and_non_empty_validation() -> None:
    entity = Entity(id="ent_1", entity_type="decision", name="Decision", scope_path="org:acme")
    relation = Relation(
        id="rel_1",
        source_id="rec_1",
        target_id="rec_2",
        relation_type="derived_from",
        role=Role.EVIDENCE,
        lifecycle=Lifecycle.WORKING,
        scope_path="org:acme",
    )
    artifact = Artifact(
        id="art_1",
        artifact_type="mermaid",
        uri="refs/map.mmd",
        role="active",
        lifecycle="working",
        scope_path="org:acme",
    )
    audit = AuditEvent(action="create_record", actor="tester", record_id="rec_1")
    retrieval = RetrievalEvent(query="storage", actor="tester", record_ids=["rec_1"], scope_path="org:acme")

    assert entity.scope_path == relation.scope_path == artifact.scope_path == retrieval.scope_path == "org:acme"
    assert artifact.role is Role.ACTIVE
    assert artifact.lifecycle is Lifecycle.WORKING
    assert audit.detail == {}

    with pytest.raises(ValueError, match="id must not be empty"):
        Entity(id="", entity_type="decision", name="Decision")

    with pytest.raises(ValueError, match="scope_path must not be empty"):
        RetrievalEvent(query="storage", actor="tester", record_ids=[], scope_path="")
