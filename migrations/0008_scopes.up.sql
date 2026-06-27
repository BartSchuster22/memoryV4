CREATE TABLE scopes (
    key TEXT PRIMARY KEY,
    parent_key TEXT REFERENCES scopes(key),
    description TEXT
);
CREATE INDEX idx_records_scope ON records(scope);
