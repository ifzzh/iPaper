import json
from datetime import datetime, timezone
from ..connection import get_db
from ipaper.security.identity import current_user_id

class DailyArxivDAO:
    @staticmethod
    def mark_asset_file_missing(arxiv_id: str) -> bool:
        """Requeue a candidate that claims ``ready`` while its file is gone.

        Owner-scoped and idempotent: only the caller's row is touched, and only
        when it is currently marked ready. The coordinator then re-fetches the
        asset through the existing Document Worker path.
        """
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        cursor = db.execute(
            """UPDATE daily_arxiv_candidates
               SET artifact_status='retry_wait', next_retry_at=?, artifact_error_code='asset_file_missing',
                   asset_job_id=NULL, claimed_at=NULL, updated_at=?
               WHERE owner_id=? AND (arxiv_id=? OR arxiv_id LIKE ? || 'v%' OR arxiv_id LIKE '%/' || ? OR arxiv_id LIKE '%/' || ? || 'v%') AND artifact_status='ready'""",
            (now, now, current_user_id(), arxiv_id, arxiv_id, arxiv_id, arxiv_id),
        )
        db.commit()
        return cursor.rowcount == 1

    @staticmethod
    def save_task(date, category, status, metadata=None):
        db = get_db()
        metadata_json = json.dumps(metadata) if metadata else None
        db.execute('INSERT OR REPLACE INTO daily_arxiv_tasks_v2 (owner_id,date,category,status,metadata) VALUES (?,?,?,?,?)',
                   (current_user_id(), date, category, status, metadata_json))
        db.commit()

    @staticmethod
    def get_task(date, category):
        db = get_db()
        row = db.execute('SELECT * FROM daily_arxiv_tasks_v2 WHERE owner_id=? AND date=? AND category=?', (current_user_id(), date, category)).fetchone()
        if row:
            d = dict(row)
            if d['metadata']:
                try:
                    d['metadata'] = json.loads(d['metadata'])
                except:
                    pass
            return d
        return None

    @staticmethod
    def save_topics(topics):
        db = get_db()
        owner_id = current_user_id()
        now = datetime.now(timezone.utc).isoformat()
        db.execute('DELETE FROM daily_arxiv_topics WHERE owner_id=?', (owner_id,))
        for topic in topics or []:
            db.execute(
                '''INSERT INTO daily_arxiv_topics
                   (owner_id,topic_id,name,quota,config_json,updated_at)
                   VALUES (?,?,?,?,?,?)''',
                (owner_id, topic['id'], topic['name'], int(topic['quota']),
                 json.dumps(topic, ensure_ascii=False), now),
            )
        db.commit()

    @staticmethod
    def save_candidate(paper):
        matches = paper.get('matched_topics') or []
        topic_id = matches[0].get('id') if matches and isinstance(matches[0], dict) else None
        db = get_db()
        db.execute(
            '''INSERT INTO daily_arxiv_candidates
               (owner_id,arxiv_id,release_date,topic_id,relevance_score,selection_reason,
                artifact_status,retry_count,next_retry_at,asset_job_id,claimed_at,
                last_attempt_at,artifact_error_code,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(owner_id,arxiv_id) DO UPDATE SET
                 release_date=excluded.release_date, topic_id=excluded.topic_id,
                 relevance_score=excluded.relevance_score,
                 selection_reason=excluded.selection_reason,
                 artifact_status=excluded.artifact_status,
                 retry_count=excluded.retry_count, next_retry_at=excluded.next_retry_at,
                 asset_job_id=excluded.asset_job_id, claimed_at=excluded.claimed_at,
                 last_attempt_at=excluded.last_attempt_at,
                 artifact_error_code=excluded.artifact_error_code,
                 updated_at=excluded.updated_at''',
            (current_user_id(), paper['arxiv_id'], paper.get('fetch_date') or paper.get('daily_date'),
             topic_id, float(paper.get('relevance_score', 0) or 0), paper.get('selection_reason'),
             paper.get('artifact_status', 'candidate'), int(paper.get('asset_retry_count', 0) or 0),
             paper.get('asset_next_retry_at'), paper.get('asset_job_id'), paper.get('asset_claimed_at'),
             paper.get('asset_last_attempt_at'), paper.get('artifact_error_code'),
             datetime.now(timezone.utc).isoformat()),
        )
        db.commit()

    @staticmethod
    def get_candidate(arxiv_id):
        """Look up a candidate, tolerating a missing or extra version suffix."""
        if not arxiv_id:
            return None
        row = get_db().execute(
            "SELECT * FROM daily_arxiv_candidates WHERE owner_id=? AND "
            + "(arxiv_id=? OR arxiv_id LIKE ? || 'v%' OR arxiv_id LIKE '%/' || ? OR arxiv_id LIKE '%/' || ? || 'v%')",
            (current_user_id(), arxiv_id, arxiv_id, arxiv_id, arxiv_id),
        ).fetchone()
        return dict(row) if row else None
