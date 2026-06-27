CREATE TABLE supersessions (
    old_record_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE RESTRICT,
    new_record_id TEXT NOT NULL REFERENCES records(id) ON DELETE RESTRICT,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (old_record_id <> new_record_id)
);
CREATE INDEX idx_supersessions_new_record_id ON supersessions(new_record_id);
