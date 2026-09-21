"""Persistent per-paper reading notes: annotations, the main note and excerpts.

Additive tables only; existing document/result identities stay authoritative and
no paper file, hash or translation is touched.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_annotations (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 document_id TEXT NOT NULL, result_id TEXT,
 kind TEXT NOT NULL, color TEXT NOT NULL,
 excerpt TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '',
 anchor_json TEXT NOT NULL, context_json TEXT NOT NULL DEFAULT '{}',
 revision TEXT NOT NULL, deleted_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reading_annotations_owner_paper
 ON reading_annotations(owner_id,paper_id,created_at);
CREATE INDEX IF NOT EXISTS reading_annotations_owner_document
 ON reading_annotations(owner_id,document_id);

CREATE TABLE IF NOT EXISTS reading_notes (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 markdown TEXT NOT NULL DEFAULT '', revision TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id)
);

CREATE TABLE IF NOT EXISTS reading_note_entries (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 kind TEXT NOT NULL, annotation_id TEXT, message_id TEXT,
 content_json TEXT NOT NULL, note_revision TEXT NOT NULL,
 dedupe_key TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(owner_id,paper_id,dedupe_key)
);
CREATE INDEX IF NOT EXISTS reading_note_entries_owner_paper
 ON reading_note_entries(owner_id,paper_id,created_at);

CREATE TABLE IF NOT EXISTS reading_note_conflicts (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 markdown TEXT NOT NULL, base_revision TEXT NOT NULL, current_revision TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reading_note_conflicts_owner_paper
 ON reading_note_conflicts(owner_id,paper_id,created_at);
"""