"""Schema compatibility for the Daily asset columns (rollback safety).

1.11.0 adds three nullable columns to `daily_arxiv_candidates`. The previous
release must keep working against the migrated database, so the migration is
additive and the old statements (explicit column lists) are exercised here.
"""
from __future__ import annotations

import sqlite3

from ipaper.database.db_manager import init_db_schema

NEW_COLUMNS = {"thumbnail_status", "thumbnail_error_code", "requested_stage"}

# The exact statements the 1.10.0 code path uses, kept verbatim on purpose.
OLD_INSERT = """INSERT INTO daily_arxiv_candidates
   (owner_id,arxiv_id,release_date,topic_id,relevance_score,selection_reason,
    artifact_status,retry_count,next_retry_at,asset_job_id,claimed_at,
    last_attempt_at,artifact_error_code,updated_at)
   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
   ON CONFLICT(owner_id,arxiv_id) DO UPDATE SET
     artifact_status=excluded.artifact_status,
     retry_count=excluded.retry_count,
     artifact_error_code=excluded.artifact_error_code,
     updated_at=excluded.updated_at"""

OLD_FINISH = """UPDATE daily_arxiv_candidates
   SET artifact_status=?, retry_count=?, next_retry_at=?,
       artifact_error_code=?, asset_job_id=NULL, claimed_at=NULL, updated_at=?
   WHERE owner_id=? AND arxiv_id=? AND asset_job_id=?"""


def columns(db_path):
    with sqlite3.connect(db_path) as connection:
        return {row[1] for row in connection.execute("PRAGMA table_info(daily_arxiv_candidates)")}


def seed_legacy_database(db_path):
    """A 1.10.0-shaped database: the table exists without the new columns."""
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE daily_arxiv_candidates (
                 owner_id TEXT NOT NULL,
                 arxiv_id TEXT NOT NULL,
                 release_date TEXT,
                 topic_id TEXT,
                 relevance_score REAL,
                 selection_reason TEXT,
                 artifact_status TEXT NOT NULL DEFAULT 'candidate',
                 retry_count INTEGER NOT NULL DEFAULT 0,
                 next_retry_at TEXT,
                 asset_job_id TEXT,
                 claimed_at TEXT,
                 last_attempt_at TEXT,
                 artifact_error_code TEXT,
                 updated_at TEXT,
                 PRIMARY KEY (owner_id, arxiv_id))"""
        )
        connection.execute(
            "INSERT INTO daily_arxiv_candidates"
            " (owner_id,arxiv_id,release_date,artifact_status,retry_count,updated_at)"
            " VALUES ('owner-1','2609.19915v1','2026-09-18','retry_wait',3,'2026-09-18T00:00:00+00:00')"
        )
        connection.commit()


def test_migration_is_additive_and_preserves_rows(tmp_path):
    db_path = str(tmp_path / "ipaper.db")
    seed_legacy_database(db_path)
    before = columns(db_path)
    assert NEW_COLUMNS.isdisjoint(before)

    init_db_schema(db_path)

    after = columns(db_path)
    assert NEW_COLUMNS <= after
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT artifact_status, retry_count, thumbnail_status, thumbnail_error_code,"
            " requested_stage FROM daily_arxiv_candidates WHERE arxiv_id='2609.19915v1'"
        ).fetchone()
    # Existing data survives; the new fields are simply unset.
    assert row[:2] == ("retry_wait", 3)
    assert row[2:] == (None, None, None)


def test_previous_release_statements_still_work_after_migration(tmp_path):
    db_path = str(tmp_path / "ipaper.db")
    seed_legacy_database(db_path)
    init_db_schema(db_path)

    with sqlite3.connect(db_path) as connection:
        # A 1.10.0 upsert (explicit column list) must still insert and update.
        connection.execute(
            OLD_INSERT,
            (
                "owner-1", "2609.19915v1", "2026-09-18", None, 3.0, "reason",
                "candidate", 0, None, None, None, None, None,
                "2026-09-18T01:00:00+00:00",
            ),
        )
        connection.execute(
            OLD_INSERT,
            (
                "owner-1", "2609.19916v1", "2026-09-18", None, 2.0, "reason",
                "queued", 0, None, "job-1", "2026-09-18T01:00:00+00:00", None, None,
                "2026-09-18T01:00:00+00:00",
            ),
        )
        # A 1.10.0 asset finish must still update a claimed row.
        connection.execute(
            OLD_FINISH,
            ("ready", 0, None, None, "2026-09-18T02:00:00+00:00",
             "owner-1", "2609.19916v1", "job-1"),
        )
        connection.commit()
        status = connection.execute(
            "SELECT artifact_status FROM daily_arxiv_candidates WHERE arxiv_id='2609.19916v1'"
        ).fetchone()[0]
    assert status == "ready"


def test_new_fields_do_not_change_old_reads_or_counts(tmp_path):
    db_path = str(tmp_path / "ipaper.db")
    seed_legacy_database(db_path)
    init_db_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        # `SELECT *` (what the previous release uses) still yields a usable row.
        row = dict(
            zip(
                [column[0] for column in connection.execute(
                    "SELECT * FROM daily_arxiv_candidates"
                ).description],
                connection.execute("SELECT * FROM daily_arxiv_candidates").fetchone(),
            )
        )
        count = connection.execute("SELECT COUNT(*) FROM daily_arxiv_candidates").fetchone()[0]
    assert row["arxiv_id"] == "2609.19915v1"
    assert count == 1