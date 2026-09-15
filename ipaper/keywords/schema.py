SCHEMA = """
CREATE TABLE IF NOT EXISTS keyword_tags (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active', merged_into TEXT, revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS keyword_tags_owner ON keyword_tags(owner_id,status);
CREATE TABLE IF NOT EXISTS keyword_names (
 owner_id TEXT NOT NULL, name_key TEXT NOT NULL, label TEXT NOT NULL, tag_id TEXT NOT NULL,
 blocked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner_id,name_key)
);
CREATE TABLE IF NOT EXISTS keyword_links (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, tag_id TEXT NOT NULL,
 manual INTEGER NOT NULL DEFAULT 0, method TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(owner_id,paper_id,tag_id)
);
CREATE INDEX IF NOT EXISTS keyword_links_tag ON keyword_links(owner_id,tag_id,paper_id);
CREATE TABLE IF NOT EXISTS keyword_exclusions (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, tag_id TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id,tag_id)
);
CREATE TABLE IF NOT EXISTS keyword_papers (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 input_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
 result_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id)
);
CREATE TABLE IF NOT EXISTS keyword_pending (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id)
);
CREATE TABLE IF NOT EXISTS keyword_observed (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, signature TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id)
);
CREATE TABLE IF NOT EXISTS keyword_batches (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, method TEXT NOT NULL,
 selection_key TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS keyword_items (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', input_key TEXT NOT NULL DEFAULT '',
 checkpoint_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}',
 requests INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS keyword_items_pending ON keyword_items(status,created_at);
CREATE INDEX IF NOT EXISTS keyword_items_paper ON keyword_items(owner_id,paper_id);
CREATE TABLE IF NOT EXISTS keyword_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL, item_id TEXT NOT NULL,
 kind TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS keyword_operations (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL, changes_json TEXT NOT NULL,
 undone INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS library_selections (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, paper_ids_json TEXT NOT NULL,
 query_json TEXT NOT NULL, created_at REAL NOT NULL
);
"""
