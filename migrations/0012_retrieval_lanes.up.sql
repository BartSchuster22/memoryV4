CREATE TABLE lexical_terms (
    record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    frequency INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(record_id, term)
);
CREATE TABLE vector_placeholders (
    record_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'not_configured',
    dimensions INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
