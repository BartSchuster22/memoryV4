CREATE TABLE author_actors (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'agent',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE write_policies (
    name TEXT PRIMARY KEY,
    requires_verification INTEGER NOT NULL CHECK (requires_verification IN (0,1)),
    canonical_allowed INTEGER NOT NULL CHECK (canonical_allowed IN (0,1)),
    description TEXT NOT NULL
);
INSERT INTO write_policies(name, requires_verification, canonical_allowed, description) VALUES
  ('verification_required',1,0,'default governed write path'),
  ('verified_only',1,1,'verified actors may promote canonical/live'),
  ('append_only',0,0,'append evidence/exhaust only');
