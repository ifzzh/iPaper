import sqlite3

from .models import SCHEMA_SCRIPT
from ipaper.processing.schema import SCHEMA as PROCESSING_SCHEMA


_TRANSLATION_COLUMNS = {
    "output_mode": "TEXT NOT NULL DEFAULT 'dual'",
    "error_code": "TEXT",
    "stage": "TEXT",
    "stage_progress": "INTEGER NOT NULL DEFAULT 0",
    "stage_current": "INTEGER NOT NULL DEFAULT 0",
    "stage_total": "INTEGER NOT NULL DEFAULT 0",
    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
    "heartbeat_at": "TEXT",
    "queue_order": "INTEGER NOT NULL DEFAULT 0",
    "config_fingerprint": "TEXT",
    "recoverable_until": "TEXT",
    "worker_event_sequence": "INTEGER NOT NULL DEFAULT 0",
}

_DAILY_ASSET_COLUMNS = {
    "asset_job_id": "TEXT",
    "claimed_at": "TEXT",
    "last_attempt_at": "TEXT",
    "artifact_error_code": "TEXT",
    # Preview state is independent from the PDF state: a readable PDF with a
    # failed preview must still be readable, and only the preview needs a retry.
    "thumbnail_status": "TEXT",
    "thumbnail_error_code": "TEXT",
    "requested_stage": "TEXT",
}


def _ensure_translation_columns(connection: sqlite3.Connection) -> None:
    existing = {
        row[1] for row in connection.execute("PRAGMA table_info(translation_jobs)")
    }
    for name, declaration in _TRANSLATION_COLUMNS.items():
        if name not in existing:
            connection.execute(
                f"ALTER TABLE translation_jobs ADD COLUMN {name} {declaration}"
            )


def _ensure_daily_asset_columns(connection: sqlite3.Connection) -> None:
    existing = {
        row[1] for row in connection.execute("PRAGMA table_info(daily_arxiv_candidates)")
    }
    for name, declaration in _DAILY_ASSET_COLUMNS.items():
        if name not in existing:
            connection.execute(
                f"ALTER TABLE daily_arxiv_candidates ADD COLUMN {name} {declaration}"
            )


_READING_HISTORY_COLUMNS = {
    "tick_id": "TEXT",
    "source": "TEXT NOT NULL DEFAULT 'legacy'",
}


def _ensure_reading_history_columns(connection: sqlite3.Connection) -> None:
    """Add the idempotency fields used by the UTC+8 reading activity meter."""
    existing = {
        row[1] for row in connection.execute("PRAGMA table_info(reading_history)")
    }
    for name, declaration in _READING_HISTORY_COLUMNS.items():
        if name not in existing:
            connection.execute(
                f"ALTER TABLE reading_history ADD COLUMN {name} {declaration}"
            )


def init_db_schema(db_path: str = "db/ipaper.db") -> None:
    """Initialize and verify the SQLite schema before serving requests."""
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA_SCRIPT)
        connection.executescript(PROCESSING_SCHEMA)
        _ensure_translation_columns(connection)
        _ensure_daily_asset_columns(connection)
        _ensure_reading_history_columns(connection)
        # One row per (owner, tick, business day): a retried request is a no-op
        # while an interval crossing UTC+8 midnight still writes both days.
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_reading_history_tick
               ON reading_history(owner_id, tick_id, date)
               WHERE tick_id IS NOT NULL"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_reading_history_owner_date
               ON reading_history(owner_id, date)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_daily_candidates_asset_queue
               ON daily_arxiv_candidates(artifact_status, next_retry_at, updated_at)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_translation_jobs_queue
               ON translation_jobs(status, queue_order, created_at)"""
        )
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError("database integrity check failed")
