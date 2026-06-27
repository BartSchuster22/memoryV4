CREATE TABLE source_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    uri TEXT NOT NULL,
    fragment TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_source_refs_record_id ON source_refs(record_id);
