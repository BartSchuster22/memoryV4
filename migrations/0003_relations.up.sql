CREATE TABLE relations (
    id TEXT PRIMARY KEY,
    source_record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    target_record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_relations_source ON relations(source_record_id);
CREATE INDEX idx_relations_target ON relations(target_record_id);
