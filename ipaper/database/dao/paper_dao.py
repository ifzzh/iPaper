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
        metadata.pop("_metadata_inspection", None)
        
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
        from ipaper.metadata.store import prepare_save, after_save, in_library
        db = get_db()
        owner = current_user_id()
        try:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute('SELECT * FROM papers WHERE id=?', (paper_data['id'],)).fetchone()
            if previous and previous['owner_id'] != owner:
                raise PermissionError("paper_not_found")
            incoming = prepare_save(db, owner, paper_data, previous)
            row = PaperDAO._dict_to_row(incoming)
            row['owner_id'] = owner
            cols = list(row)
            assignments = ', '.join(f'{col}=excluded.{col}' for col in cols if col != 'id')
            db.execute(f'INSERT INTO papers ({", ".join(cols)}) VALUES ({", ".join("?" for _ in cols)}) '
                       f'ON CONFLICT(id) DO UPDATE SET {assignments} WHERE papers.owner_id=excluded.owner_id', list(row.values()))
            entering = not previous or (not in_library(PaperDAO._row_to_dict(previous)) and in_library(incoming))
            after_save(db, owner, incoming, new=entering)
            db.commit()
            return PaperDAO.get_paper(paper_data['id'])
        except BaseException:
            db.rollback()
            raise

    @staticmethod
    def patch_state(paper_id, changes, *, increments=None):
        """Mutate only requested runtime fields under the database write lock."""
        from ipaper.metadata.model import LEGACY
        if set(changes) & set(LEGACY.values()):
            raise ValueError("use_metadata_edit")
        increments = increments or {}
        if set(increments) - {'read_time', 'analysis_view_time'}:
            raise ValueError("invalid_duration_field")
        db = get_db()
        owner = current_user_id()
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM papers WHERE id=? AND owner_id=?', (paper_id, owner)).fetchone()
            if not row:
                raise PermissionError('paper_not_found')
            data = PaperDAO._row_to_dict(row)
            data.update(changes)
            for key, amount in increments.items():
                data[key] = int(data.get(key) or 0) + int(amount)
            values = PaperDAO._dict_to_row(data)
            db.execute('UPDATE papers SET '+','.join(k+'=?' for k in values if k!='id')+' WHERE id=? AND owner_id=?',
                       [v for k,v in values.items() if k!='id']+[paper_id,owner])
            db.commit()
            return PaperDAO.get_paper(paper_id)
        except BaseException:
            db.rollback()
            raise

    @staticmethod
    def get_paper(paper_id):
        db = get_db()
        row = db.execute('SELECT * FROM papers WHERE id=? AND owner_id=?', (paper_id, current_user_id())).fetchone()
        if row:
            return PaperDAO._row_to_dict(row)
        return None

    @staticmethod
    def find_papers_by_identity(arxiv_id, *, daily_only=False):
        """Owner-scoped rows for one arXiv identity (base id, any revision).

        The Daily queue stores `2609.19915v1` while the paper row stores
        `2609.19915`; matching by raw string made the asset worker believe the
        paper did not exist. The predicate is anchored to the base id, so it can
        never reach a different paper.
        """
        from ipaper.arxiv_identity import identity_params, identity_sql, parse_arxiv_identity

        identity = parse_arxiv_identity(arxiv_id)
        if identity is None:
            return []
        predicate, _ = identity_sql("arxiv_id")
        sql = f"SELECT * FROM papers WHERE owner_id=? AND {predicate}"
        params = [current_user_id(), *identity_params(arxiv_id)]
        if daily_only:
            sql += " AND is_daily = 1"
        rows = [PaperDAO._row_to_dict(row) for row in get_db().execute(sql, params).fetchall()]
        return rows

    @staticmethod
    def get_paper_by_arxiv_id(arxiv_id):
        from ipaper.arxiv_identity import best_match

        rows = PaperDAO.find_papers_by_identity(arxiv_id)
        if not rows:
            # Unknown formats keep the historical exact-match behaviour.
            db = get_db()
            row = db.execute(
                'SELECT * FROM papers WHERE arxiv_id=? AND owner_id=?',
                (arxiv_id, current_user_id()),
            ).fetchone()
            return PaperDAO._row_to_dict(row) if row else None
        return best_match(rows, arxiv_id)

    @staticmethod
    def get_daily_paper_by_identity(arxiv_id):
        """The Daily row for this identity, used by assets, retries and promotion."""
        from ipaper.arxiv_identity import best_match

        rows = PaperDAO.find_papers_by_identity(arxiv_id, daily_only=True)
        if not rows:
            return None
        return best_match(rows, arxiv_id)

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
        from ipaper.metadata.store import exists
        if exists(db):
            db.execute("UPDATE metadata_items SET status='deleted',stage='deleted' WHERE owner_id=? AND paper_id=? AND status IN ('queued','running','waiting')", (owner,paper_id))
            for table in ('bibliography','bibliography_revisions','bibliography_inspections','bibliography_candidates'):
                db.execute(f'DELETE FROM {table} WHERE owner_id=? AND paper_id=?',(owner,paper_id))
            db.execute('DELETE FROM metadata_duplicate_dismissals WHERE owner_id=? AND (paper_id=? OR other_id=?)',(owner,paper_id,paper_id))
            db.execute('INSERT OR REPLACE INTO metadata_index_events(owner_id,paper_id,revision) VALUES (?,?,0)',(owner,paper_id))
        db.execute('DELETE FROM papers WHERE id=? AND owner_id=?', (paper_id, owner))
        from ipaper.keywords.store import exists as has_keywords
        if has_keywords(db):
            for table in ('keyword_links','keyword_exclusions','keyword_papers','keyword_pending'):
                db.execute(f'DELETE FROM {table} WHERE owner_id=? AND paper_id=?',(owner,paper_id))
            db.execute("UPDATE keyword_items SET status='deleted',error='paper_not_found' WHERE owner_id=? AND paper_id=? AND status IN ('queued','running')",(owner,paper_id))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='topic_nodes'").fetchone():
            for table in ('topic_links','topic_exclusions','topic_papers','topic_pending','topic_legacy_observed','topic_observed'):
                db.execute(f'DELETE FROM {table} WHERE owner_id=? AND paper_id=?',(owner,paper_id))
            db.execute("UPDATE topic_items SET status='deleted',error='paper_not_found' WHERE owner_id=? AND paper_id=? AND status IN ('queued','running')",(owner,paper_id))
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
        from ipaper.tools.basic_tools.daily_arxiv import normalize_arxiv_id

        candidates = {}
        for row in db.execute(
            'SELECT * FROM daily_arxiv_candidates WHERE owner_id=? AND release_date=?',
            (current_user_id(), date),
        ).fetchall():
            record = dict(row)
            candidates[normalize_arxiv_id(record['arxiv_id'])] = record
        for paper in papers:
            candidate = candidates.get(normalize_arxiv_id(paper.get('arxiv_id')))
            if not candidate:
                continue
            paper.update({
                # The candidate key keeps its version suffix; the repair and
                # retry paths need it to address the right row.
                'candidate_arxiv_id': candidate['arxiv_id'],
                'artifact_status': candidate['artifact_status'],
                'asset_retry_count': candidate['retry_count'],
                'asset_next_retry_at': candidate['next_retry_at'],
                'asset_job_id': candidate.get('asset_job_id'),
                'asset_claimed_at': candidate.get('claimed_at'),
                'asset_last_attempt_at': candidate.get('last_attempt_at'),
                'artifact_error_code': candidate.get('artifact_error_code'),
                # Preview state is tracked separately from the PDF state so the
                # view can say "PDF 可读、封面待生成" instead of guessing.
                'thumbnail_status': candidate.get('thumbnail_status'),
                'thumbnail_error_code': candidate.get('thumbnail_error_code'),
                'requested_stage': candidate.get('requested_stage'),
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
