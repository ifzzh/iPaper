SCHEMA = """
CREATE TABLE IF NOT EXISTS bibliography (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 fields_json TEXT NOT NULL, provenance_json TEXT NOT NULL, projection_json TEXT NOT NULL,
 original_title TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id)
);
CREATE TABLE IF NOT EXISTS bibliography_revisions (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, revision INTEGER NOT NULL,
 fields_json TEXT NOT NULL, provenance_json TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id,revision)
);
CREATE TABLE IF NOT EXISTS bibliography_inspections (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, sha256 TEXT NOT NULL,
 data_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(owner_id,paper_id,sha256)
);
CREATE TABLE IF NOT EXISTS bibliography_candidates (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 input_revision INTEGER NOT NULL, sha256 TEXT NOT NULL DEFAULT '',
 record_json TEXT NOT NULL, verdict TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bibliography_candidates_paper ON bibliography_candidates(owner_id,paper_id);
CREATE TABLE IF NOT EXISTS metadata_batches (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL, selection_key TEXT NOT NULL,
 cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata_items (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 input_revision INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'queued',
 checkpoint_json TEXT NOT NULL DEFAULT '{}', requests INTEGER NOT NULL DEFAULT 0,
 error TEXT, next_run REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS metadata_items_pending ON metadata_items(status,next_run,created_at);
CREATE INDEX IF NOT EXISTS metadata_items_paper ON metadata_items(owner_id,paper_id);
CREATE TABLE IF NOT EXISTS metadata_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, owner_id TEXT NOT NULL,
 kind TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata_http_cache (
 owner_id TEXT NOT NULL, cache_key TEXT NOT NULL, provider TEXT NOT NULL,
 data_json TEXT NOT NULL, expires_at REAL NOT NULL, PRIMARY KEY(owner_id,cache_key)
);
CREATE TABLE IF NOT EXISTS metadata_provider_limits (
 provider TEXT PRIMARY KEY, next_request REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS metadata_index_events (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, revision INTEGER NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner_id,paper_id)
);
CREATE TABLE IF NOT EXISTS metadata_duplicate_dismissals (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, other_id TEXT NOT NULL, signature TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id,other_id)
);
"""
