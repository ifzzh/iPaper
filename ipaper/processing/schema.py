"""Additive schema; older iPaper versions may safely ignore these tables."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS processing_documents (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 kind TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
 page_count INTEGER NOT NULL, geometry_json TEXT NOT NULL, file_ref TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(owner_id,paper_id,kind,sha256)
);
CREATE INDEX IF NOT EXISTS processing_documents_paper ON processing_documents(owner_id,paper_id);
CREATE TABLE IF NOT EXISTS processing_results (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 document_id TEXT NOT NULL REFERENCES processing_documents(id),
 parse_id TEXT REFERENCES processing_results(id), kind TEXT NOT NULL,
 status TEXT NOT NULL, source_language TEXT NOT NULL, target_language TEXT NOT NULL,
 config_json TEXT NOT NULL, config_fingerprint TEXT NOT NULL,
 manifest_json TEXT NOT NULL DEFAULT '{}', bytes INTEGER NOT NULL DEFAULT 0,
 block_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS processing_results_paper ON processing_results(owner_id,paper_id,created_at);
CREATE INDEX IF NOT EXISTS processing_results_cache ON processing_results(owner_id,document_id,kind,config_fingerprint);
CREATE TABLE IF NOT EXISTS processing_blocks (
 result_id TEXT NOT NULL REFERENCES processing_results(id), block_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL, body_offset INTEGER NOT NULL, body_size INTEGER NOT NULL,
 text_hash TEXT NOT NULL, source_json TEXT NOT NULL,
 PRIMARY KEY(result_id,block_id), UNIQUE(result_id,ordinal)
);
CREATE TABLE IF NOT EXISTS processing_block_translations (
 result_id TEXT NOT NULL REFERENCES processing_results(id), block_id TEXT NOT NULL,
 generation INTEGER NOT NULL DEFAULT 0, active_revision TEXT, status TEXT NOT NULL,
 error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(result_id,block_id)
);
CREATE TABLE IF NOT EXISTS processing_translation_revisions (
 id TEXT PRIMARY KEY, result_id TEXT NOT NULL REFERENCES processing_results(id),
 block_id TEXT NOT NULL, generation INTEGER NOT NULL, body_file TEXT NOT NULL,
 sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processing_jobs (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 document_id TEXT REFERENCES processing_documents(id), result_id TEXT REFERENCES processing_results(id),
 parent_id TEXT REFERENCES processing_jobs(id), kind TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, request_json TEXT NOT NULL, status TEXT NOT NULL,
 stage TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
 reserved_bytes INTEGER NOT NULL DEFAULT 0, budget_json TEXT NOT NULL, usage_json TEXT NOT NULL,
 checkpoint_json TEXT NOT NULL DEFAULT '{}', cancel_requested INTEGER NOT NULL DEFAULT 0,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(owner_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS processing_jobs_queue ON processing_jobs(status,created_at);
CREATE INDEX IF NOT EXISTS processing_jobs_owner ON processing_jobs(owner_id,status);
CREATE TABLE IF NOT EXISTS processing_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES processing_jobs(id),
 kind TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processing_attempts (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES processing_jobs(id),
 kind TEXT NOT NULL, unit TEXT NOT NULL, status TEXT NOT NULL,
 request_json TEXT NOT NULL, response_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processing_sources (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 document_id TEXT NOT NULL REFERENCES processing_documents(id),
 result_id TEXT REFERENCES processing_results(id), block_id TEXT, revision_id TEXT,
 selection_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processing_chat_sources (
 owner_id TEXT NOT NULL, session_id TEXT NOT NULL, message_key TEXT NOT NULL,
 sources_json TEXT NOT NULL, PRIMARY KEY(owner_id,session_id,message_key)
);
CREATE TABLE IF NOT EXISTS processing_profiles (
 owner_id TEXT PRIMARY KEY, model TEXT NOT NULL, base_url TEXT NOT NULL,
 secret_envelope TEXT, revision TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processing_reading_positions (
 owner_id TEXT NOT NULL, paper_id TEXT NOT NULL, result_id TEXT NOT NULL,
 value_json TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,paper_id,result_id)
);
CREATE TABLE IF NOT EXISTS processing_layout_jobs (
 job_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, snapshot_json TEXT NOT NULL, result_id TEXT
);
CREATE TABLE IF NOT EXISTS processing_backup_leases (
 id TEXT PRIMARY KEY, expires_at TEXT NOT NULL
);
"""

from .understanding_schema import SCHEMA as UNDERSTANDING_SCHEMA
SCHEMA += UNDERSTANDING_SCHEMA

from .reading_schema import SCHEMA as READING_SCHEMA
SCHEMA += READING_SCHEMA
