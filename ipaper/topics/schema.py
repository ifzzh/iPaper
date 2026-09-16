SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_nodes (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
 parent_id TEXT, definition_id TEXT, sort_order INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'active', merged_into TEXT,
 revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS topic_owner_parent ON topic_nodes(owner_id,parent_id,status);
CREATE TABLE IF NOT EXISTS topic_links (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, topic_id TEXT NOT NULL,
 manual INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,paper_id,topic_id)
);
CREATE TABLE IF NOT EXISTS topic_exclusions (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, topic_id TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id,topic_id)
);
CREATE TABLE IF NOT EXISTS topic_papers (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 auto_blocked INTEGER NOT NULL DEFAULT 0, input_key TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(owner_id,paper_id)
);
CREATE TABLE IF NOT EXISTS topic_legacy_map (
 owner_id TEXT NOT NULL, category_id TEXT NOT NULL, topic_id TEXT NOT NULL,
 PRIMARY KEY(owner_id,category_id)
);
CREATE TABLE IF NOT EXISTS topic_pending (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id)
);
"""
SCHEMA += """
CREATE TABLE IF NOT EXISTS topic_operations (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL,
 before_json TEXT NOT NULL, after_json TEXT NOT NULL,
 undone INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
"""
SCHEMA += """
CREATE TABLE IF NOT EXISTS topic_batches (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, selection_key TEXT NOT NULL,
 cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_items (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, batch_id TEXT NOT NULL,
 paper_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 input_key TEXT NOT NULL DEFAULT '', error TEXT, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS topic_item_dispatch ON topic_items(status,owner_id,created_at);
CREATE TABLE IF NOT EXISTS topic_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
 item_id TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL
);
"""
SCHEMA += """
CREATE TABLE IF NOT EXISTS topic_legacy_observed (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, category_id TEXT,
 PRIMARY KEY(owner_id,paper_id)
);
"""
SCHEMA += """
CREATE TABLE IF NOT EXISTS topic_catalog_state (
 owner_id TEXT PRIMARY KEY, initialized_at TEXT NOT NULL, definition_version TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS topic_active_definition ON topic_nodes(owner_id,definition_id)
 WHERE status='active' AND definition_id IS NOT NULL;
"""
SCHEMA += """
CREATE TABLE IF NOT EXISTS topic_mutation_receipts (
 owner_id TEXT NOT NULL, request_id TEXT NOT NULL, payload_key TEXT NOT NULL,
 result_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,request_id)
);
"""
SCHEMA += """
CREATE TABLE IF NOT EXISTS topic_observed (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, signature TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id)
);
"""
