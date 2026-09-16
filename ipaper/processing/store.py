"""Transactional owner-scoped indices and immutable, atomically published files."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ipaper.document_worker.safety import bounded_copy
from ipaper.security.paths import safe_join, user_storage_root, ensure_confined_tree
from ipaper.environment import getenv
from .common import ProcessingError, encoded, fingerprint, identifier, now


class ProcessingStore:
    def __init__(self, db_path, papers_root, owner_id):
        self.db_path = str(db_path)
        # Existing tenant path helpers return canonical absolute paths. Use the
        # same base for relative file references, including local CLI checkouts.
        self.papers_root = Path(papers_root).resolve()
        self.owner = identifier(owner_id)

    def quotas(self):
        def read(name, default):
            try:
                value = int(getenv(name, str(default)))
                if value <= 0:
                    raise ValueError()
                return value
            except (ValueError, TypeError):
                raise ProcessingError("invalid_processing_quota", 503) from None
        return read("IPAPER_STRUCTURE_RESULT_MAX_BYTES", 1024**3), read("IPAPER_STRUCTURE_OWNER_MAX_BYTES", 10*1024**3)

    def check_quota(self, db, extra, *, result_id=None, job_id=None):
        maximum, owner_maximum = self.quotas()
        current = 0
        if result_id:
            current = self._owned(db, "processing_results", result_id)["bytes"]
        if current + extra > maximum:
            raise ProcessingError("result_quota_exceeded", 413)
        occupied = self.occupied_bytes(db)
        reserved = db.execute("SELECT coalesce(sum(reserved_bytes),0) FROM processing_jobs WHERE owner_id=? AND id!=?", (self.owner, job_id or "")).fetchone()[0]
        if occupied + reserved + extra > owner_maximum:
            raise ProcessingError("owner_quota_exceeded", 413)
        if job_id:
            self._owned(db, "processing_jobs", job_id)
            db.execute("UPDATE processing_jobs SET reserved_bytes=max(0,reserved_bytes-?) WHERE id=? AND owner_id=?", (extra, job_id, self.owner))

    @contextmanager
    def connection(self, *, write=False):
        db = sqlite3.connect(self.db_path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def occupied_bytes(self, db):
        total = db.execute("SELECT coalesce(sum(bytes),0) FROM processing_results WHERE owner_id=?", (self.owner,)).fetchone()[0]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='understanding_artifacts'").fetchone():
            total += db.execute("SELECT coalesce(sum(bytes),0) FROM understanding_artifacts WHERE owner_id=?", (self.owner,)).fetchone()[0]
        return total

    def _owned(self, db, table, row_id):
        # Table names are internal constants; never derive them from request data.
        if table not in {"processing_documents", "processing_results", "processing_jobs", "processing_sources"}:
            raise ValueError("invalid_table")
        row = db.execute(f"SELECT * FROM {table} WHERE id=? AND owner_id=?",
                         (identifier(row_id), self.owner)).fetchone()
        if not row:
            raise ProcessingError("result_not_found", 404)
        return dict(row)

    def paper_exists(self, db, paper_id):
        if not db.execute("SELECT id FROM papers WHERE id=? AND owner_id=?", (paper_id, self.owner)).fetchone():
            raise ProcessingError("paper_not_found", 404)

    def artifact_directory(self, result_id, *, create=False):
        user_storage_root(self.papers_root, self.owner, create=create)
        # safe_join only accepts relative components; ownership is part of the
        # server-generated path, independent of categories and uploaded names.
        target = safe_join(self.papers_root, ".users", self.owner, ".artifacts", identifier(result_id))
        if create:
            target.mkdir(mode=0o700, parents=True, exist_ok=False)
        return target

    def document(self, document_id):
        with self.connection() as db:
            return self._owned(db, "processing_documents", document_id)

    def register_document(self, paper_id, *, kind, sha256, size, geometry, file_ref=None):
        if kind not in {"original", "babeldoc_mono", "babeldoc_dual"}:
            raise ProcessingError("invalid_document_kind")
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ProcessingError("invalid_document_hash")
        if not isinstance(geometry, list) or not 0 < len(geometry) <= 2000 or size <= 0:
            raise ProcessingError("invalid_document_geometry")
        with self.connection(write=True) as db:
            self.paper_exists(db, paper_id)
            previous = db.execute("SELECT * FROM processing_documents WHERE owner_id=? AND paper_id=? AND kind=? AND sha256=?",
                                  (self.owner, paper_id, kind, sha256)).fetchone()
            if previous:
                # Category moves don't change the immutable document identity.
                frozen = str(previous["file_ref"] or "").startswith(f".users/{self.owner}/.artifacts/")
                if file_ref is not None and not frozen:
                    db.execute("UPDATE processing_documents SET file_ref=? WHERE id=?", (file_ref, previous["id"]))
                return previous["id"]
            doc_id = identifier()
            db.execute("INSERT INTO processing_documents VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (doc_id, self.owner, paper_id, kind, sha256, size, len(geometry), encoded(geometry), file_ref, now()))
            return doc_id

    def new_result(self, document_id, kind, config, *, parse_id=None, source_language="auto", target_language="zh-CN"):
        if kind not in {"document_parts", "structure", "structured_translation", "babeldoc_mono", "babeldoc_dual"}:
            raise ProcessingError("invalid_result_kind")
        with self.connection(write=True) as db:
            doc = self._owned(db, "processing_documents", document_id)
            if parse_id:
                parse = self._owned(db, "processing_results", parse_id)
                if parse["document_id"] != document_id or parse["kind"] != "structure":
                    raise ProcessingError("parse_version_mismatch", 409)
            result_id, stamp = identifier(), now()
            db.execute("""INSERT INTO processing_results
                (id,owner_id,paper_id,document_id,parse_id,kind,status,source_language,target_language,
                 config_json,config_fingerprint,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'pending',?,?,?,?,?,?)""",
                (result_id, self.owner, doc["paper_id"], document_id, parse_id, kind,
                 source_language, target_language, encoded(config), fingerprint(config), stamp, stamp))
            return result_id

    def results(self, paper_id):
        with self.connection() as db:
            self.paper_exists(db, paper_id)
            return [dict(row) for row in db.execute("SELECT * FROM processing_results WHERE owner_id=? AND paper_id=? ORDER BY created_at DESC,id DESC", (self.owner, paper_id))]

    def result(self, result_id):
        with self.connection() as db:
            result = self._owned(db, "processing_results", result_id)
            self.paper_exists(db, result["paper_id"])
            return result

    def publish_structure(self, result_id, output: Path, manifest: dict, *, job_id=None):
        result = self.result(result_id)
        if result["kind"] not in {"structure", "document_parts"} or result["status"] != "pending":
            raise ProcessingError("result_already_published", 409)
        target = self.artifact_directory(result_id)
        parent = target.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = safe_join(self.papers_root, ".users", self.owner, ".artifacts", identifier())
        temporary.mkdir(mode=0o700)
        total = 0
        published = False
        try:
            # Client has verified Worker manifest; recheck bytes while copying
            # across volumes to detect a changed staging file before publication.
            ensure_confined_tree(output.parent, output)
            for entry in manifest["entries"]:
                relative, size = entry["path"], entry["size"]
                if type(size) is not int or size < 0:
                    raise ProcessingError("invalid_manifest")
                total += size
                if total > 1024**3:
                    raise ProcessingError("result_quota_exceeded", 413)
                source, destination = safe_join(output, relative, must_exist=True, require_file=True), safe_join(temporary, relative)
                with source.open("rb") as handle:
                    actual = bounded_copy(handle, destination, size)
                digest = hashlib.sha256()
                with destination.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024**2), b""):
                        digest.update(chunk)
                if actual != size or digest.hexdigest() != entry["sha256"]:
                    raise ProcessingError("artifact_hash_mismatch", 409)
            entries = []
            if not (temporary / "blocks.jsonl").exists() and result["kind"] == "document_parts":
                (temporary / "blocks.jsonl").write_bytes(b"")
            with (temporary / "blocks.jsonl").open("rb") as handle:
                while True:
                    offset = handle.tell()
                    line = handle.readline(4 * 1024**2 + 1)
                    if not line:
                        break
                    if len(line) > 4 * 1024**2 or len(entries) >= 100_000:
                        raise ProcessingError("structure_block_limit")
                    block = json.loads(line)
                    entries.append((result_id, block["id"], len(entries), offset, len(line), block["textHash"], encoded(block["source"])))
            with self.connection(write=True) as db:
                current = self._owned(db, "processing_results", result_id)
                if current["status"] != "pending" or target.exists():
                    raise ProcessingError("result_already_published", 409)
                self.check_quota(db, total, result_id=result_id, job_id=job_id)
                os.rename(temporary, target)
                published = True
                descriptor = os.open(parent, os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                db.executemany("INSERT INTO processing_blocks VALUES (?,?,?,?,?,?,?)", entries)
                db.execute("UPDATE processing_results SET status='completed',manifest_json=?,bytes=?,block_count=?,updated_at=? WHERE id=? AND owner_id=?",
                           (encoded(manifest), total, len(entries), now(), result_id, self.owner))
                from ipaper.keywords.store import queue_change
                queue_change(db,self.owner,result['paper_id'])
                from ipaper.topics.store import queue_change as queue_topics
                queue_topics(db,self.owner,result['paper_id'])
        except BaseException:
            # Only this unpublished UUID tree can be removed; never old results.
            if published:
                shutil.rmtree(target)
            raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def publish_layout(self, result_id, source):
        """Freeze a validated PDF before changing the legacy latest-file alias."""
        result=self.result(result_id)
        if not result["kind"].startswith("babeldoc_"):
            raise ProcessingError("invalid_result_kind")
        if json.loads(result["manifest_json"]).get("entries"):
            return
        doc=self.document(result["document_id"])
        directory=self.artifact_directory(result_id)
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        temporary=directory/(identifier()+".tmp")
        target=directory/"result.pdf"
        published=False
        try:
            with Path(source).open("rb") as reader:
                bounded_copy(reader,temporary,doc["size"])
            digest=hashlib.sha256()
            with temporary.open("rb") as reader:
                for chunk in iter(lambda:reader.read(1024**2),b""):
                    digest.update(chunk)
            if temporary.stat().st_size!=doc["size"] or digest.hexdigest()!=doc["sha256"]:
                raise ProcessingError("artifact_hash_mismatch",409)
            with self.connection(write=True) as db:
                current=self._owned(db,"processing_results",result_id)
                if json.loads(current["manifest_json"]).get("entries"):
                    return
                self.check_quota(db,doc["size"],result_id=result_id)
                os.replace(temporary,target);published=True
                descriptor=os.open(directory,os.O_DIRECTORY)
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
                manifest={"entries":[{"path":"result.pdf","size":doc["size"],"sha256":doc["sha256"]}]}
                db.execute("UPDATE processing_documents SET file_ref=? WHERE id=? AND owner_id=?",
                           (str(target.relative_to(self.papers_root)),doc["id"],self.owner))
                db.execute("UPDATE processing_results SET status='completed',manifest_json=?,bytes=?,updated_at=? WHERE id=? AND owner_id=?",
                           (encoded(manifest),doc["size"],now(),result_id,self.owner))
        except BaseException:
            if published:target.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def blocks(self, result_id, *, after=-1, limit=50):
        if type(after) is not int or after < -1 or type(limit) is not int or not 1 <= limit <= 100:
            raise ProcessingError("invalid_cursor")
        result = self.result(result_id)
        parse_id = result["parse_id"] or result_id
        with self.connection() as db:
            self._owned(db, "processing_results", parse_id)
            rows = db.execute("SELECT * FROM processing_blocks WHERE result_id=? AND ordinal>? ORDER BY ordinal LIMIT ?", (parse_id, after, limit)).fetchall()
        contents = []
        if rows:
            with safe_join(self.artifact_directory(parse_id), "blocks.jsonl", must_exist=True, require_file=True).open("rb") as handle:
                for row in rows:
                    handle.seek(row["body_offset"])
                    block = json.loads(handle.read(row["body_size"]))
                    block["translation"] = self.translation(result_id, row["block_id"]) if result["kind"] == "structured_translation" else None
                    contents.append(block)
        return contents

    def block(self, result_id, block_id):
        if not isinstance(block_id,str) or not 0 < len(block_id) <= 200:
            raise ProcessingError("invalid_block_id")
        result = self.result(result_id)
        parse_id = result["parse_id"] or result_id
        with self.connection() as db:
            row = db.execute("SELECT ordinal FROM processing_blocks WHERE result_id=? AND block_id=?", (parse_id, block_id)).fetchone()
        if not row:
            raise ProcessingError("block_not_found", 404)
        return self.blocks(result_id, after=row["ordinal"] - 1, limit=1)[0]

    def begin_translation(self, result_id, block_id):
        block = self.block(result_id, block_id)
        with self.connection(write=True) as db:
            result = self._owned(db, "processing_results", result_id)
            if result["kind"] != "structured_translation":
                raise ProcessingError("invalid_result_kind")
            db.execute("""INSERT INTO processing_block_translations
                 (result_id,block_id,generation,status,updated_at) VALUES (?,?,1,'running',?)
                 ON CONFLICT(result_id,block_id) DO UPDATE SET generation=generation+1,
                 status='running',error=NULL,updated_at=excluded.updated_at""", (result_id, block_id, now()))
            generation = db.execute("SELECT generation FROM processing_block_translations WHERE result_id=? AND block_id=?", (result_id, block_id)).fetchone()[0]
        return generation, block

    def finish_translation(self, result_id, block_id, generation, value=None, *, error=None, job_id=None):
        result = self.result(result_id)
        revision, path, payload = identifier(), None, None
        try:
            with self.connection(write=True) as db:
                current = db.execute("SELECT * FROM processing_block_translations WHERE result_id=? AND block_id=?", (result_id, block_id)).fetchone()
                if not current or current["generation"] != generation or current["status"] != "running":
                    return False
                if error:
                    db.execute("UPDATE processing_block_translations SET status='failed',error=?,updated_at=? WHERE result_id=? AND block_id=?",
                               (error, now(), result_id, block_id))
                    return True
                payload = encoded(value).encode()
                if len(payload) > 4 * 1024**2:
                    raise ProcessingError("translation_size_limit")
                self.check_quota(db, len(payload), result_id=result_id, job_id=job_id)
                directory = self.artifact_directory(result_id)
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                path = safe_join(directory, revision + ".json")
                import io
                bounded_copy(io.BytesIO(payload), path, len(payload))
                db.execute("INSERT INTO processing_translation_revisions VALUES (?,?,?,?,?,?,?,?)", (revision, result_id, block_id, generation, path.name, hashlib.sha256(payload).hexdigest(), len(payload), now()))
                db.execute("UPDATE processing_block_translations SET active_revision=?,status='completed',error=NULL,updated_at=? WHERE result_id=? AND block_id=?",
                           (revision, now(), result_id, block_id))
                db.execute("UPDATE processing_results SET bytes=bytes+?,updated_at=? WHERE id=?", (len(payload), now(), result_id))
            return True
        except BaseException:
            if path and path.exists():
                path.unlink()
            raise

    def translation(self, result_id, block_id):
        self.result(result_id)
        with self.connection() as db:
            row = db.execute("""SELECT t.status,t.error,t.generation,r.id AS revision,r.body_file
                FROM processing_block_translations t LEFT JOIN processing_translation_revisions r
                ON r.id=t.active_revision WHERE t.result_id=? AND t.block_id=?""", (result_id, block_id)).fetchone()
        if not row:
            return None
        value = dict(row)
        if value.pop("body_file", None):
            path = safe_join(self.artifact_directory(result_id), row["body_file"], must_exist=True, require_file=True)
            value["content"] = json.loads(path.read_text("utf-8"))
        return value
