SCHEMA_SCRIPT = """
-- Local identities. Passwords and bearer-equivalent values are never stored raw.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    username_normalized TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    password_changed_at INTEGER
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    csrf_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);

CREATE TABLE IF NOT EXISTS invite_codes (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    used_by TEXT,
    revoked_at INTEGER,
    FOREIGN KEY(created_by) REFERENCES users(id),
    FOREIGN KEY(used_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS password_reset_codes (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    revoked_at INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by) REFERENCES users(id)
);

-- Papers table
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    title TEXT,
    authors TEXT, 
    abstract TEXT,
    published_date TEXT, -- Maps to arxiv_published_date
    url TEXT, -- Maps to arxiv_url
    arxiv_id TEXT,
    category TEXT, -- Maps to subject
    download_date TEXT, -- Maps to upload_date
    file_path TEXT,
    thumbnail_path TEXT,
    starred INTEGER DEFAULT 0,
    read_time INTEGER DEFAULT 0,
    translation_status TEXT,
    analysis_status TEXT,
    is_daily INTEGER DEFAULT 0,
    daily_date TEXT,
    metadata TEXT -- Stores other fields from Paper dataclass
);

-- Categories table
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    name TEXT NOT NULL,
    parent_id TEXT,
    display_name TEXT,
    FOREIGN KEY(parent_id) REFERENCES categories(id)
);

-- User Settings table (Key-Value store)
CREATE TABLE IF NOT EXISTS user_settings_v2 (
    owner_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT -- JSON string
    ,PRIMARY KEY(owner_id, key)
);

-- Kept empty on v0.9 installations so the v0.6 offline credential rollback
-- tooling can still inspect older backups. Runtime reads only user_settings_v2.
CREATE TABLE IF NOT EXISTS user_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Reading History
CREATE TABLE IF NOT EXISTS reading_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, -- YYYY-MM-DD
    paper_id TEXT,
    duration INTEGER,
    timestamp INTEGER, -- Unix timestamp
    owner_id TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    FOREIGN KEY(paper_id) REFERENCES papers(id)
);

-- Chat History
CREATE TABLE IF NOT EXISTS chats (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    paper_id TEXT,
    history TEXT, -- JSON array of messages
    created_at TEXT,
    updated_at TEXT,
    title TEXT,
    FOREIGN KEY(paper_id) REFERENCES papers(id)
);

-- Reading List
CREATE TABLE IF NOT EXISTS reading_list (
    paper_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    added_at TEXT,
    status TEXT, -- 'unread', 'reading', 'read'
    FOREIGN KEY(paper_id) REFERENCES papers(id)
);

-- Daily Arxiv Task Status
CREATE TABLE IF NOT EXISTS daily_arxiv_tasks_v2 (
    owner_id TEXT NOT NULL,
    date TEXT,
    category TEXT,
    status TEXT,
    metadata TEXT, -- JSON
    PRIMARY KEY (owner_id, date, category)
);

-- Institution Mapping
CREATE TABLE IF NOT EXISTS institution_map_v2 (
    owner_id TEXT NOT NULL,
    original_name TEXT NOT NULL,
    normalized_name TEXT,
    PRIMARY KEY(owner_id, original_name)
);

-- Daily arXiv Read Status
CREATE TABLE IF NOT EXISTS daily_arxiv_reads_v2 (
    owner_id TEXT NOT NULL,
    arxiv_id TEXT NOT NULL,
    read_at INTEGER,
    PRIMARY KEY(owner_id, arxiv_id)
);

CREATE TABLE IF NOT EXISTS daily_arxiv_topics (
    owner_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    name TEXT NOT NULL,
    quota INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, topic_id)
);

CREATE TABLE IF NOT EXISTS daily_arxiv_candidates (
    owner_id TEXT NOT NULL,
    arxiv_id TEXT NOT NULL,
    release_date TEXT NOT NULL,
    topic_id TEXT,
    relevance_score REAL NOT NULL DEFAULT 0,
    selection_reason TEXT,
    artifact_status TEXT NOT NULL DEFAULT 'candidate',
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    asset_job_id TEXT,
    claimed_at TEXT,
    last_attempt_at TEXT,
    artifact_error_code TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(owner_id, arxiv_id)
);
CREATE INDEX IF NOT EXISTS idx_daily_candidates_release
    ON daily_arxiv_candidates(owner_id, release_date, relevance_score DESC);

-- Isolated translation worker jobs. Credentials are deliberately never stored.
CREATE TABLE IF NOT EXISTS translation_jobs (
    job_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    paper_id TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    error_code TEXT,
    stage TEXT,
    stage_progress INTEGER NOT NULL DEFAULT 0,
    stage_current INTEGER NOT NULL DEFAULT 0,
    stage_total INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    heartbeat_at TEXT,
    queue_order INTEGER NOT NULL DEFAULT 0,
    config_fingerprint TEXT,
    recoverable_until TEXT,
    worker_event_sequence INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(paper_id) REFERENCES papers(id)
);

CREATE INDEX IF NOT EXISTS idx_translation_jobs_paper_status
    ON translation_jobs(paper_id, status);
CREATE INDEX IF NOT EXISTS idx_translation_jobs_queue
    ON translation_jobs(status, queue_order, created_at);

CREATE TABLE IF NOT EXISTS translation_job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    stage TEXT,
    message TEXT,
    progress INTEGER,
    stage_progress INTEGER,
    stage_current INTEGER,
    stage_total INTEGER,
    FOREIGN KEY(job_id) REFERENCES translation_jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_translation_events_owner_id
    ON translation_job_events(owner_id, id);

-- Isolated document validation/import jobs. No file paths or secrets are stored.
CREATE TABLE IF NOT EXISTS document_jobs (
    job_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
    kind TEXT NOT NULL,
    paper_id TEXT,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    FOREIGN KEY(paper_id) REFERENCES papers(id)
);

CREATE INDEX IF NOT EXISTS idx_document_jobs_status
    ON document_jobs(status, created_at);

-- API credentials encrypted with the deployment-only settings key.
CREATE TABLE IF NOT EXISTS agentic_secrets_v2 (
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (name IN ('translate', 'interpret', 'dailyArxiv', 'mineru')),
    PRIMARY KEY(owner_id, name)
);

-- Legacy encrypted rows are copied into agentic_secrets_v2 by the v0.9
-- tenant migration. New runtime writes never target this table.
CREATE TABLE IF NOT EXISTS agentic_secrets (
    name TEXT PRIMARY KEY,
    ciphertext TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (name IN ('translate', 'interpret', 'dailyArxiv', 'mineru'))
);

CREATE TABLE IF NOT EXISTS ai_providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    origin TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY(created_by) REFERENCES users(id)
);
"""

from ipaper.metadata.schema import SCHEMA as METADATA_SCHEMA
SCHEMA_SCRIPT += METADATA_SCHEMA
from ipaper.keywords.schema import SCHEMA as KEYWORD_SCHEMA
SCHEMA_SCRIPT += KEYWORD_SCHEMA

from ipaper.topics.schema import SCHEMA as TOPIC_SCHEMA
SCHEMA_SCRIPT += TOPIC_SCHEMA
