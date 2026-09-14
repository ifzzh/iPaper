"""Bounded checkpoint retention and consistent DB+immutable-artifact backups."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ipaper.security.paths import safe_join, ensure_confined_tree
from .common import encoded, identifier, now
from .store import ProcessingStore


def _digest(path):
    digest=hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024**2),b''):
            digest.update(chunk)
    return digest.hexdigest()


def cleanup(store, *, checkpoint_days=7, log_days=30):
    cutoff=(datetime.now(timezone.utc)-timedelta(days=checkpoint_days)).isoformat()
    logs_cutoff=(datetime.now(timezone.utc)-timedelta(days=log_days)).isoformat()
    with store.connection(write=True) as db:
        if db.execute('SELECT 1 FROM processing_backup_leases WHERE expires_at>?',(now(),)).fetchone():
            return 0
        # Cache payloads share the immutable artifact store and backup lease.
        # Remove indexes first, then files only after commit: interruption may
        # leave an invisible orphan, never a visible half-deleted cache entry.
        expired = list(db.execute("""SELECT a.id FROM understanding_artifacts a
            LEFT JOIN reading_selection_cache c ON c.owner_id=a.owner_id AND c.artifact_id=a.id
            WHERE a.owner_id=? AND a.kind='selection_translation' AND
              ((c.referenced=0 AND c.expires_at<?) OR
               (c.artifact_id IS NULL AND a.created_at<?) OR
               NOT EXISTS(SELECT 1 FROM papers p WHERE p.owner_id=a.owner_id AND p.id=a.paper_id))
            LIMIT 100""", (store.owner, now(), cutoff)))
        for row in expired:
            db.execute("DELETE FROM reading_selection_cache WHERE owner_id=? AND artifact_id=?", (store.owner,row[0]))
            db.execute("DELETE FROM understanding_artifacts WHERE owner_id=? AND id=? AND kind='selection_translation'", (store.owner,row[0]))
        # Terminal task summaries remain available. Temporary selected text is
        # not an unbounded second cache in request_json after retention expires.
        db.execute("""UPDATE processing_jobs SET request_json=json_remove(request_json,'$.text')
            WHERE owner_id=? AND kind='selection_translate' AND updated_at<?
            AND status IN ('completed','failed','cancelled','interrupted')
            AND NOT EXISTS(SELECT 1 FROM reading_selection_cache c WHERE c.owner_id=processing_jobs.owner_id
                AND c.cache_key=json_extract(processing_jobs.request_json,'$.cacheKey') AND (c.expires_at>? OR c.referenced=1))""", (store.owner,logs_cutoff,now()))
        db.execute("DELETE FROM reading_bookmarks WHERE owner_id=? AND NOT EXISTS(SELECT 1 FROM papers WHERE id=reading_bookmarks.paper_id AND owner_id=reading_bookmarks.owner_id)", (store.owner,))
        jobs=list(db.execute("""SELECT id FROM processing_jobs WHERE owner_id=? AND updated_at<?
            AND status IN ('completed','partial','failed','cancelled','interrupted')
            AND NOT EXISTS (SELECT 1 FROM processing_results WHERE id=processing_jobs.id) LIMIT 50""",(store.owner,cutoff)))
        for job in jobs:
            path=store.artifact_directory(job['id'])
            if path.exists():
                ensure_confined_tree(store.papers_root,path)
                shutil.rmtree(path)
            db.execute("UPDATE processing_jobs SET reserved_bytes=0,error=CASE WHEN status='completed' THEN error ELSE 'checkpoint_expired' END WHERE id=?",(job['id'],))
        db.execute("""DELETE FROM processing_events WHERE created_at<? AND job_id IN
            (SELECT id FROM processing_jobs WHERE owner_id=? AND status IN ('completed','partial','failed','cancelled','interrupted'))""",(logs_cutoff,store.owner))
    for row in expired:
        path = store.artifact_directory(row[0])
        if path.exists():
            ensure_confined_tree(store.papers_root, path)
            shutil.rmtree(path)
    return len(jobs) + len(expired)


def backup(database, papers_root, destination):
    """No restore overwrites: publish a new backup directory only after verification."""
    database,papers_root,destination=Path(database),Path(papers_root),Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('backup_destination_exists')
    destination.mkdir(mode=0o700,parents=True)
    lease=identifier()
    snapshot=destination/'ipaper.db'
    files=[]
    try:
        with sqlite3.connect(database,timeout=30) as db:
            # Also supports a pre-1.2 DB; no schema mutation is needed merely to
            # back it up before the additive upgrade.
            has_processing=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='processing_results'").fetchone())
            if has_processing:
                db.execute('INSERT INTO processing_backup_leases VALUES (?,?)',(lease,(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()))
                db.commit()
            with sqlite3.connect(snapshot) as target:
                db.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise RuntimeError('backup_integrity_failed')
        snapshot.chmod(0o600)
        with sqlite3.connect(snapshot) as db:
            db.row_factory=sqlite3.Row
            expected={}
            if has_processing:
                for result in db.execute('SELECT * FROM processing_results'):
                    store=ProcessingStore(database,papers_root,result['owner_id'])
                    root=store.artifact_directory(result['id'])
                    for entry in json.loads(result['manifest_json']).get('entries',[]):
                        path=safe_join(root,entry['path'],must_exist=True,require_file=True)
                        expected[str(path.relative_to(papers_root))]=(path,entry['sha256'])
                for revision in db.execute('SELECT r.*,p.owner_id FROM processing_translation_revisions r JOIN processing_results p ON p.id=r.result_id'):
                    store=ProcessingStore(database,papers_root,revision['owner_id'])
                    path=safe_join(store.artifact_directory(revision['result_id']),revision['body_file'],must_exist=True,require_file=True)
                    expected[str(path.relative_to(papers_root))]=(path,revision['sha256'])
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='understanding_artifacts'").fetchone():
                    for artifact in db.execute('SELECT * FROM understanding_artifacts'):
                        store=ProcessingStore(database,papers_root,artifact['owner_id'])
                        for entry in json.loads(artifact['manifest_json'])['entries']:
                            path=safe_join(store.artifact_directory(artifact['id']),entry['path'],must_exist=True,require_file=True)
                            expected[str(path.relative_to(papers_root))]=(path,entry['sha256'])
        for relative,(source,expected_hash) in expected.items():
            target=safe_join(destination, 'artifacts', relative)
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            with source.open('rb') as reader,target.open('xb') as writer:
                shutil.copyfileobj(reader,writer,1024**2)
                writer.flush();os.fsync(writer.fileno())
            target.chmod(0o600)
            actual=_digest(target)
            if actual!=expected_hash:
                raise RuntimeError('backup_artifact_hash_mismatch')
            files.append({'path':'artifacts/'+relative,'sha256':actual,'size':target.stat().st_size})
        manifest={'createdAt':now(),'database':{'path':'ipaper.db','sha256':_digest(snapshot)},'files':files,'processingSchemaPresent':has_processing,
                  'restorePolicy':'Restore only to an empty isolated directory; production rollback retains the current database and immutable artifacts.'}
        target=destination/'manifest.json';target.write_text(encoded(manifest),encoding='utf-8');target.chmod(0o600)
        return {'databaseVerified':True,'immutableFiles':len(files),'bytes':sum(f['size'] for f in files),'manifestSha256':_digest(target)}
    except BaseException:
        # Retain an incomplete backup for diagnosis, but never give it a complete
        # manifest or claim it is usable for restoration.
        (destination/'INCOMPLETE').write_text('Backup did not complete. Do not restore.\n')
        (destination/'INCOMPLETE').chmod(0o600)
        raise
    finally:
        if locals().get('has_processing'):
            with sqlite3.connect(database,timeout=30) as db:
                db.execute('DELETE FROM processing_backup_leases WHERE id=?',(lease,))
