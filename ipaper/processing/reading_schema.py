"""Additive reading tools; existing document/result identities remain authoritative."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_bookmarks (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 document_id TEXT NOT NULL, result_id TEXT, name TEXT NOT NULL,
 location_json TEXT NOT NULL, dedupe_key TEXT NOT NULL, revision TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(owner_id,paper_id,dedupe_key)
);
CREATE INDEX IF NOT EXISTS reading_bookmarks_owner ON reading_bookmarks(owner_id,paper_id,created_at);
CREATE TABLE IF NOT EXISTS reading_selection_cache (
 owner_id TEXT NOT NULL, cache_key TEXT NOT NULL, paper_id TEXT NOT NULL,
 artifact_id TEXT NOT NULL REFERENCES understanding_artifacts(id),
 expires_at TEXT NOT NULL, referenced INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,cache_key)
);
"""
