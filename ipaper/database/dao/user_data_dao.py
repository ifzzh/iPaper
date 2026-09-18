import json
from ..connection import get_db
from ipaper.security.identity import current_user_id

class ReadingHistoryDAO:
    @staticmethod
    def add_history(date, duration, paper_id, timestamp, tick_id=None, source="reader"):
        """Append one reading interval row (owner scoped).

        ``tick_id`` makes the write idempotent: the unique index on
        (owner_id, tick_id, date) turns a retried request into a no-op while an
        interval that crosses UTC+8 midnight still writes one row per day.
        """
        db = get_db()
        if tick_id:
            db.execute(
                "INSERT OR IGNORE INTO reading_history"
                " (date,duration,paper_id,timestamp,owner_id,tick_id,source)"
                " VALUES (?,?,?,?,?,?,?)",
                (date, duration, paper_id, timestamp, current_user_id(), tick_id, source),
            )
        else:
            db.execute(
                "INSERT INTO reading_history"
                " (date,duration,paper_id,timestamp,owner_id,source)"
                " VALUES (?,?,?,?,?,?)",
                (date, duration, paper_id, timestamp, current_user_id(), source),
            )
        db.commit()

    @staticmethod
    def get_activity_since(since_date):
        """Per-day totals and distinct papers for the current owner.

        ``duration`` is stored in seconds; rows written before the UTC+8 change
        keep their original date key and are never shifted.
        """
        db = get_db()
        rows = db.execute(
            "SELECT date,"
            "       SUM(duration) AS seconds,"
            "       COUNT(DISTINCT paper_id) AS papers,"
            "       COUNT(paper_id) AS attributed_rows"
            "  FROM reading_history"
            " WHERE owner_id=? AND date>=?"
            " GROUP BY date"
            " ORDER BY date",
            (current_user_id(), since_date),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def get_day_papers(date):
        """Distinct papers with a recorded interval on one UTC+8 day.

        Owner scoped through the joining papers table, so a shared day key can
        never expose another user's titles.
        """
        db = get_db()
        rows = db.execute(
            "SELECT DISTINCT h.paper_id AS paper_id, p.title AS title, p.arxiv_id AS arxiv_id"
            "  FROM reading_history h"
            "  LEFT JOIN papers p ON p.id = h.paper_id AND p.owner_id = h.owner_id"
            " WHERE h.owner_id=? AND h.date=? AND h.paper_id IS NOT NULL"
            " ORDER BY title",
            (current_user_id(), date),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def has_tick(tick_id):
        if not tick_id:
            return False
        db = get_db()
        row = db.execute(
            "SELECT 1 FROM reading_history WHERE owner_id=? AND tick_id=? LIMIT 1",
            (current_user_id(), tick_id),
        ).fetchone()
        return bool(row)

class ReadingListDAO:
    @staticmethod
    def add_item(paper_id, added_at, status='unread'):
        db = get_db()
        cursor = db.execute(
            '''INSERT INTO reading_list (paper_id,owner_id,added_at,status)
               VALUES (?,?,?,?)
               ON CONFLICT(paper_id) DO UPDATE SET
                   added_at=excluded.added_at,status=excluded.status
               WHERE reading_list.owner_id=excluded.owner_id''',
            (paper_id, current_user_id(), added_at, status),
        )
        if cursor.rowcount != 1:
            db.rollback()
            raise PermissionError("reading_list_item_not_found")
        db.commit()

    @staticmethod
    def get_list():
        db = get_db()
        rows = db.execute('SELECT * FROM reading_list WHERE owner_id=? ORDER BY added_at DESC', (current_user_id(),)).fetchall()
        return [dict(row) for row in rows]
        
    @staticmethod
    def remove_item(paper_id):
        db = get_db()
        db.execute('DELETE FROM reading_list WHERE paper_id=? AND owner_id=?', (paper_id, current_user_id()))
        db.commit()


class DailyArxivReadDAO:
    @staticmethod
    def mark_read(arxiv_id: str, read_at: int) -> None:
        if not arxiv_id:
            return
        db = get_db()
        db.execute(
            "INSERT OR REPLACE INTO daily_arxiv_reads_v2 (owner_id,arxiv_id,read_at) VALUES (?,?,?)",
            (current_user_id(), arxiv_id, int(read_at)),
        )
        db.commit()

    @staticmethod
    def get_read_ids(arxiv_ids):
        if not arxiv_ids:
            return []

        cleaned = [x for x in arxiv_ids if isinstance(x, str) and x.strip()]
        if not cleaned:
            return []

        placeholders = ",".join(["?"] * len(cleaned))
        db = get_db()
        rows = db.execute(
            f"SELECT arxiv_id FROM daily_arxiv_reads_v2 WHERE owner_id=? AND arxiv_id IN ({placeholders})",
            (current_user_id(), *cleaned),
        ).fetchall()
        return [dict(r).get("arxiv_id") for r in rows if dict(r).get("arxiv_id")]
