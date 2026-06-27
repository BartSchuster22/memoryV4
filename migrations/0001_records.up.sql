CREATE TABLE records (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('canonical','active','evidence','exhaust')),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('working','live','superseded','archived','expired')),
    scope TEXT NOT NULL,
    content TEXT NOT NULL,
    source_refs_json TEXT NOT NULL DEFAULT '[]',
    author_actor TEXT NOT NULL,
    write_policy TEXT NOT NULL,
    superseded_by TEXT REFERENCES records(id),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
