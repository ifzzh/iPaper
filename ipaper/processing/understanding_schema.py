"""Additive tables; no runtime imports or changes to historical tables."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS understanding_artifacts (
 id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,paper_id TEXT NOT NULL,kind TEXT NOT NULL,
 source_id TEXT, fingerprint TEXT NOT NULL, config_json TEXT NOT NULL,
 status TEXT NOT NULL,manifest_json TEXT NOT NULL,bytes INTEGER NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(owner_id,kind,fingerprint)
);
CREATE INDEX IF NOT EXISTS understanding_paper ON understanding_artifacts(owner_id,paper_id,created_at);
CREATE TABLE IF NOT EXISTS understanding_heads (
 owner_id TEXT NOT NULL,paper_id TEXT NOT NULL,kind TEXT NOT NULL,result_id TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id,kind)
);
CREATE TABLE IF NOT EXISTS understanding_evidence (
 id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,paper_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,
 unit_id TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(owner_id,snapshot_id,unit_id)
);
CREATE TABLE IF NOT EXISTS understanding_chat_turns (
 id TEXT NOT NULL,owner_id TEXT NOT NULL,paper_id TEXT NOT NULL,session_id TEXT,
 status TEXT NOT NULL,context_json TEXT NOT NULL DEFAULT '{}',error TEXT,
 answer TEXT, message_key TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,id)
);
"""
