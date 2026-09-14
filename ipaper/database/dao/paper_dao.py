import json
import sqlite3
from ..connection import get_db
from ipaper.security.identity import current_user_id

class PaperDAO:
    COLUMN_MAP = {
        'id': 'id',
        'title': 'title',
        'authors': 'authors',
        'abstract': 'abstract',
        'arxiv_published_date': 'published_date',
        'arxiv_url': 'url',
        'arxiv_id': 'arxiv_id',
        'subject': 'category',
        'upload_date': 'download_date',
        'file_path': 'file_path',
        'starred': 'starred',
        'read_time': 'read_time',
        'translation_status': 'translation_status',
        'analysis_status': 'analysis_status',
        'is_daily': 'is_daily',
        'daily_date': 'daily_date',
        'thumbnail_path': 'thumbnail_path'
    }

    @staticmethod
    def _dict_to_row(paper_dict):
        row = {}
        metadata = paper_dict.copy()
        
        for field, col in PaperDAO.COLUMN_MAP.items():
            val = metadata.pop(field, None)
            row[col] = val
        
        # Handle special cases or defaults
        if row.get('starred') is not None:
            row['starred'] = 1 if row['starred'] else 0
        else:
            row['starred'] = 0
            
        row['metadata'] = json.dumps(metadata)
        return row

    @staticmethod
    def _row_to_dict(row):
        d = {}
        row_dict = dict(row)
        
        # Extract metadata first
        if row_dict.get('metadata'):
            try:
                d.update(json.loads(row_dict['metadata']))
            except:
                pass
        
        # Overwrite with column values
        for field, col in PaperDAO.COLUMN_MAP.items():
            if col in row_dict and row_dict[col] is not None:
                d[field] = row_dict[col]
                
        # Boolean conversion
        d['starred'] = bool(d.get('starred'))
        d['is_daily'] = bool(d.get('is_daily'))
        
        return d

    @staticmethod
    def save_paper(paper_data):
        try:
            db = get_db()
            row = PaperDAO._dict_to_row(paper_data)
            row['owner_id'] = current_user_id()
            
            cols = list(row.keys())
            placeholders = ', '.join(['?'] * len(cols))
            assignments = ", ".join(
                f"{column}=excluded.{column}" for column in cols if column != "id"
            )
            sql = (
                f'INSERT INTO papers ({", ".join(cols)}) VALUES ({placeholders}) '
                f'ON CONFLICT(id) DO UPDATE SET {assignments} '
                'WHERE papers.owner_id=excluded.owner_id'
            )
            cursor = db.execute(sql, list(row.values()))
            if cursor.rowcount != 1:
                db.rollback()
                raise PermissionError("paper_not_found")
            db.commit()
        except Exception as e:
            print(f"Error saving paper {paper_data.get('id')}: {e}")
            raise

    @staticmethod
    def get_paper(paper_id):
        db = get_db()
        row = db.execute('SELECT * FROM papers WHERE id=? AND owner_id=?', (paper_id, current_user_id())).fetchone()
        if row:
            return PaperDAO._row_to_dict(row)
        return None

    @staticmethod
    def get_paper_by_arxiv_id(arxiv_id):
        db = get_db()
        row = db.execute('SELECT * FROM papers WHERE arxiv_id=? AND owner_id=?', (arxiv_id, current_user_id())).fetchone()
        if row:
            return PaperDAO._row_to_dict(row)
        return None

    @staticmethod
    def get_paper_by_path(file_path):
        db = get_db()
        row = db.execute('SELECT * FROM papers WHERE file_path=? AND owner_id=?', (file_path, current_user_id())).fetchone()
        if row:
            return PaperDAO._row_to_dict(row)
        return None

    @staticmethod
    def list_papers(filter_dict=None):
        db = get_db()
        sql = 'SELECT * FROM papers WHERE owner_id=?'
        params = [current_user_id()]
        if filter_dict:
            clauses = []
            for k, v in filter_dict.items():
                col = PaperDAO.COLUMN_MAP.get(k, k) # Try mapped name, else use key (might fail if not column)
                # Simple SQL construction - valid for internal use
                clauses.append(f"{col} = ?")
                params.append(v)
            if clauses:
                sql += ' AND ' + ' AND '.join(clauses)
        
        rows = db.execute(sql, params).fetchall()
        return [PaperDAO._row_to_dict(row) for row in rows]
        
    @staticmethod
    def delete_paper(paper_id):
        db = get_db()
        owner = current_user_id()
        # Additive reading state is removed in the same transaction. Payload
        # files remain under the backup-aware bounded artifact cleanup.
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='reading_bookmarks'").fetchone():
            db.execute('DELETE FROM reading_bookmarks WHERE paper_id=? AND owner_id=?', (paper_id,owner))
        db.execute('DELETE FROM papers WHERE id=? AND owner_id=?', (paper_id, owner))
        db.commit()

    @staticmethod
    def get_all_papers():
        db = get_db()
        rows = db.execute('SELECT * FROM papers WHERE owner_id=?', (current_user_id(),)).fetchall()
        return [PaperDAO._row_to_dict(row) for row in rows]

    @staticmethod
    def get_daily_papers(date, category=None):
        db = get_db()
        sql = 'SELECT * FROM papers WHERE owner_id=? AND is_daily = 1 AND daily_date = ?'
        params = [current_user_id(), date]
        rows = db.execute(sql, params).fetchall()
        papers = [PaperDAO._row_to_dict(row) for row in rows]
        candidates = {
            row['arxiv_id']: dict(row)
            for row in db.execute(
                'SELECT * FROM daily_arxiv_candidates WHERE owner_id=? AND release_date=?',
                (current_user_id(), date),
            ).fetchall()
        }
        for paper in papers:
            candidate = candidates.get(paper.get('arxiv_id'))
            if not candidate:
                continue
            paper.update({
                'artifact_status': candidate['artifact_status'],
                'asset_retry_count': candidate['retry_count'],
                'asset_next_retry_at': candidate['next_retry_at'],
                'asset_job_id': candidate.get('asset_job_id'),
                'asset_claimed_at': candidate.get('claimed_at'),
                'asset_last_attempt_at': candidate.get('last_attempt_at'),
                'artifact_error_code': candidate.get('artifact_error_code'),
            })
        if not category or category == "all":
            return papers
        wanted = category.strip().lower()
        result = []
        for paper in papers:
            categories = {str(value).strip().lower() for value in paper.get('categories', [])}
            categories.add(str(paper.get('subject') or '').strip().lower())
            categories.add(str(paper.get('fetch_category') or '').strip().lower())
            topic_ids = {
                str(value.get('id') or '').strip().lower()
                for value in paper.get('matched_topics', []) if isinstance(value, dict)
            }
            if wanted in categories or wanted in topic_ids:
                result.append(paper)
        return result

    @staticmethod
    def get_available_daily_dates():
        db = get_db()
        rows = db.execute('SELECT DISTINCT daily_date FROM papers WHERE owner_id=? AND is_daily=1 ORDER BY daily_date DESC', (current_user_id(),)).fetchall()
        return [row['daily_date'] for row in rows if row['daily_date']]

    @staticmethod
    def delete_old_daily_papers(cutoff_date):
        db = get_db()
        # Delete papers where is_daily=1 and daily_date < cutoff_date
        # Note: string comparison works for YYYY-MM-DD
        db.execute('DELETE FROM papers WHERE owner_id=? AND is_daily=1 AND daily_date<?', (current_user_id(), cutoff_date))
        db.commit()
