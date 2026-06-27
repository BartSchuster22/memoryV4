CREATE TABLE retrieval_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    lane TEXT NOT NULL,
    scope TEXT,
    degraded INTEGER NOT NULL CHECK (degraded IN (0,1)),
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_retrieval_events_scope ON retrieval_events(scope);
