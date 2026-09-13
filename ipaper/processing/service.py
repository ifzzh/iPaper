from __future__ import annotations
import sqlite3
import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor

from ipaper.security.identity import Identity, current_user_id, run_as_identity
from .store import ProcessingStore
from .profiles import StructuredCredentialCipher, StructuredProfiles
from .pipeline import ProcessingPipeline
from .sources import Sources


class ProcessingService:
    def __init__(self, db_path, papers_root, key_file, credentials, policy):
        self.db_path, self.papers_root = db_path, papers_root
        self.cipher = StructuredCredentialCipher.from_file(key_file)
        self.cipher.validate_all(db_path)
        self.credentials, self.policy = credentials, policy
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="structure-task")
        self.active = {}
        self.thread = None

    def pipeline(self, owner=None):
        store = ProcessingStore(self.db_path, self.papers_root, owner or current_user_id())
        return ProcessingPipeline(store, StructuredProfiles(store, self.cipher), self.credentials, self.policy)

    def sources(self):
        pipeline = self.pipeline()
        return Sources(pipeline.store, pipeline.paper_file)

    def understanding(self):
        from .understanding import Understanding
        return Understanding(self.pipeline())

    def initialize_ifzzh(self):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            user = db.execute("SELECT id,username,role FROM users WHERE username='ifzzh'").fetchone()
        if user:
            def initialize():
                pipeline = self.pipeline(user["id"])
                settings = pipeline.settings()
                config = settings.get("llmConfigs", {}).get("translate", {})
                if config.get("llmModel") and config.get("llmBaseUrl") and self.credentials.configured("translate"):
                    pipeline.profiles.initialize_from_babeldoc(settings, self.credentials)
            run_as_identity(Identity(user["id"], user["username"], user["role"]), initialize)

    def start(self):
        if self.thread:
            return
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE understanding_chat_turns SET status='interrupted',error='server_restarted' WHERE status IN ('preparing','streaming')")
            owners = [row[0] for row in db.execute("SELECT DISTINCT owner_id FROM processing_jobs WHERE status IN ('queued','running','cancelling')")]
        for owner in owners:
            self.pipeline(owner).jobs.recover()
        self.thread = threading.Thread(target=self._dispatch, name="structured-dispatch", daemon=True)
        self.thread.start()

    def _dispatch(self):
        last_cleanup = 0.0
        while not self.stop.is_set():
            try:
                self._dispatch_once()
                if time.monotonic() - last_cleanup > 3600 and not self.active:
                    from .maintenance import cleanup
                    with sqlite3.connect(self.db_path) as db:
                        owners = [r[0] for r in db.execute("SELECT DISTINCT owner_id FROM processing_jobs")]
                    for owner in owners:
                        try:
                            cleanup(self.pipeline(owner).store)
                        except Exception:
                            logging.getLogger(__name__).warning("structured_checkpoint_cleanup_failed")
                    last_cleanup = time.monotonic()
            except Exception:
                # A temporary DB/Worker failure must not kill dispatch for all
                # users. Never log provider exceptions or credential contents.
                logging.getLogger(__name__).warning("structured_dispatch_iteration_failed")
            self.wake.wait(1)
            self.wake.clear()

    def _dispatch_once(self):
        for job_id, future in list(self.active.items()):
            if not future.done():
                continue
            if not future.cancelled() and future.exception() is not None:
                with sqlite3.connect(self.db_path) as db:
                    row = db.execute("SELECT owner_id FROM processing_jobs WHERE id=?", (job_id,)).fetchone()
                if row:
                    self.pipeline(row[0]).jobs.finish(job_id, "interrupted", error="processing_dispatch_interrupted")
                logging.getLogger(__name__).warning("structured_task_interrupted")
            del self.active[job_id]
        if len(self.active) >= 2:
            return
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            jobs = list(db.execute("""SELECT j.id,j.owner_id,u.username,u.role FROM processing_jobs j
                JOIN users u ON u.id=j.owner_id WHERE j.status='queued' AND u.status='active'
                ORDER BY j.created_at LIMIT 20"""))
        for job in jobs:
            if self.stop.is_set() or len(self.active) >= 2:
                break
            if job["id"] in self.active:
                continue
            identity = Identity(job["owner_id"], job["username"], job["role"])
            pipeline = self.pipeline(job["owner_id"])
            self.active[job["id"]] = self.pool.submit(run_as_identity, identity, pipeline.run, job["id"])

    def shutdown(self):
        self.stop.set()
        self.wake.set()
        # Durable cancellation stops subsequent requests; interrupted in-flight
        # calls retain their bounded timeout and unknown outcome semantics.
        for job_id in list(self.active):
            with sqlite3.connect(self.db_path) as db:
                row = db.execute("SELECT owner_id FROM processing_jobs WHERE id=?", (job_id,)).fetchone()
            if row:
                self.pipeline(row[0]).jobs.cancel(job_id)
        if self.thread:
            self.thread.join(timeout=2)
        self.pool.shutdown(wait=False, cancel_futures=True)
