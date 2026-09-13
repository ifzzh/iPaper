"""Durable task admission, checkpoints, budgets and cancellation semantics."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from .common import ProcessingError, encoded, fingerprint, identifier, now

DEFAULT_BUDGET = {"requests": 200, "inputTokens": 500_000, "outputTokens": 250_000, "seconds": 7200}
TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted"}
ACTIVE = {"queued", "running", "cancelling"}


class ProcessingJobs:
    def __init__(self, store, *, result_quota=1024**3, owner_quota=10*1024**3):
        self.store = store
        self.result_quota, self.owner_quota = result_quota, owner_quota

    @staticmethod
    def budget(value=None):
        result = dict(DEFAULT_BUDGET)
        if value is not None:
            if not isinstance(value, dict) or set(value) - set(result):
                raise ProcessingError("invalid_budget")
            for key, number in value.items():
                if type(number) is not int or not 1 <= number <= DEFAULT_BUDGET[key]:
                    raise ProcessingError("invalid_budget")
                result[key] = number
        return result

    def create(self, paper_id, kind, request, *, document_id=None, result_id=None, budget=None, reservation=None):
        if kind not in {"parse", "translate", "parse_translate", "retranslate", "overview", "interpretation", "analysis_export"}:
            raise ProcessingError("invalid_processing_kind")
        budget = self.budget(budget)
        reservation = self.result_quota if reservation is None else reservation
        if type(reservation) is not int or not 0 < reservation <= self.result_quota:
            raise ProcessingError("invalid_quota_reservation")
        key = fingerprint({"paper": paper_id, "kind": kind, "request": request, "budget": budget})
        stamp, job_id = now(), identifier()
        with self.store.connection(write=True) as db:
            self.store.paper_exists(db, paper_id)
            previous = db.execute("SELECT * FROM processing_jobs WHERE owner_id=? AND idempotency_key=?", (self.store.owner, key)).fetchone()
            if previous:
                return dict(previous), False
            for table, row_id in (("processing_documents", document_id), ("processing_results", result_id)):
                if row_id and self.store._owned(db, table, row_id)["paper_id"] != paper_id:
                    raise ProcessingError("source_version_mismatch", 409)
            if db.execute("SELECT count(*) FROM processing_jobs WHERE status='queued'").fetchone()[0] >= 20:
                raise ProcessingError("processing_queue_full", 429)
            if db.execute("SELECT 1 FROM processing_jobs WHERE owner_id=? AND status IN ('queued','running','cancelling')", (self.store.owner,)).fetchone():
                raise ProcessingError("user_processing_busy", 409)
            occupied = self.store.occupied_bytes(db)
            reserved = db.execute("SELECT coalesce(sum(reserved_bytes),0) FROM processing_jobs WHERE owner_id=?", (self.store.owner,)).fetchone()[0]
            if occupied + reserved + reservation > self.owner_quota:
                raise ProcessingError("owner_quota_exceeded", 413)
            db.execute("""INSERT INTO processing_jobs
                (id,owner_id,paper_id,document_id,result_id,kind,idempotency_key,request_json,status,stage,
                 reserved_bytes,budget_json,usage_json,created_at,updated_at)
                 VALUES (?,?,?,?,?,?,?,?,'queued','queued',?,?,?,?,?)""",
                (job_id, self.store.owner, paper_id, document_id, result_id, kind, key, encoded(request),
                 reservation, encoded(budget), encoded({"requests": 0, "inputTokens": 0, "outputTokens": 0, "seconds": 0}), stamp, stamp))
            self._event(db, job_id, "queued", {"budget": budget})
            return self.store._owned(db, "processing_jobs", job_id), True

    def _event(self, db, job_id, kind, data):
        # Callers pass a closed set of statuses/counters, never provider error
        # strings, signed transfer URLs, credentials or full response bodies.
        db.execute("INSERT INTO processing_events(job_id,kind,data_json,created_at) VALUES (?,?,?,?)", (job_id, kind, encoded(data), now()))

    def get(self, job_id):
        with self.store.connection() as db:
            row = self.store._owned(db, "processing_jobs", job_id)
            self.store.paper_exists(db, row["paper_id"])
            return row

    def list(self, *, limit=100):
        with self.store.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM processing_jobs WHERE owner_id=? ORDER BY created_at DESC LIMIT ?", (self.store.owner, min(max(limit, 1), 200)))]

    def events(self, job_id, after=0):
        self.get(job_id)
        with self.store.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM processing_events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT 200", (job_id, after))]

    def checkpoint(self, job_id, stage, value, *, completed=None, total=None, result_id=None):
        if len(encoded(value)) > 256 * 1024:
            raise ProcessingError("checkpoint_size_limit")
        with self.store.connection(write=True) as db:
            job = self.store._owned(db, "processing_jobs", job_id)
            if job["status"] not in ACTIVE:
                raise ProcessingError("job_not_active", 409)
            run_started = json.loads(job["checkpoint_json"]).get("runStarted")
            if run_started:
                value = {**value, "runStarted": run_started}
            if result_id:
                result = self.store._owned(db, "processing_results", result_id)
                if result["paper_id"] != job["paper_id"]:
                    raise ProcessingError("source_version_mismatch", 409)
            db.execute("""UPDATE processing_jobs SET stage=?,checkpoint_json=?,updated_at=?,
                completed=?,total=?,result_id=? WHERE id=?""", (stage, encoded(value), now(),
                completed if completed is not None else job["completed"],
                total if total is not None else job["total"], result_id or job["result_id"], job_id))
            self._event(db, job_id, "progress", {"stage": stage, "completed": completed, "total": total})

    def check(self, job_id):
        job = self.get(job_id)
        with self.store.connection() as db:
            user=db.execute("SELECT status FROM users WHERE id=?",(self.store.owner,)).fetchone()
        if not user or user["status"]!="active":
            raise ProcessingError("processing_cancelled",409)
        if job["cancel_requested"] or job["status"] == "cancelling":
            raise ProcessingError("processing_cancelled", 409)
        if job["status"] != "running":
            raise ProcessingError("job_not_running", 409)
        usage, budget = json.loads(job["usage_json"]), json.loads(job["budget_json"])
        started = json.loads(job["checkpoint_json"]).get("runStarted")
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds() if started else 0
        if usage["seconds"] + elapsed >= budget["seconds"]:
            raise ProcessingError("processing_time_budget", 409)
        return job

    def claim(self, job_id):
        with self.store.connection(write=True) as db:
            job = self.store._owned(db, "processing_jobs", job_id)
            if job["status"] != "queued":
                return False
            checkpoint = json.loads(job["checkpoint_json"])
            checkpoint["runStarted"] = now()
            db.execute("UPDATE processing_jobs SET status='running',checkpoint_json=?,updated_at=? WHERE id=?", (encoded(checkpoint), now(), job_id))
            self._event(db, job_id, "running", {})
            return True

    def reserve_attempt(self, job_id, kind, unit, *, input_tokens=0, output_tokens=0, metadata=None):
        self.check(job_id)
        attempt_id = identifier()
        with self.store.connection(write=True) as db:
            job = self.store._owned(db, "processing_jobs", job_id)
            if job["status"] != "running" or job["cancel_requested"]:
                raise ProcessingError("processing_cancelled", 409)
            usage, budget = json.loads(job["usage_json"]), json.loads(job["budget_json"])
            if kind == "model":
                for key, increment in (("requests", 1), ("inputTokens", input_tokens), ("outputTokens", output_tokens)):
                    usage[key] += increment
                    if usage[key] > budget[key]:
                        raise ProcessingError("processing_budget_exceeded", 409)
                db.execute("UPDATE processing_jobs SET usage_json=?,updated_at=? WHERE id=?", (encoded(usage), now(), job_id))
            db.execute("INSERT INTO processing_attempts(id,job_id,kind,unit,status,request_json,created_at,updated_at) VALUES (?,?,?,?,'started',?,?,?)", (attempt_id, job_id, kind, unit, encoded(metadata or {}), now(), now()))
            self._event(db, job_id, "request_started", {"attempt": attempt_id, "kind": kind, "unit": unit})
        return attempt_id

    def finish_attempt(self, job_id, attempt_id, status, metadata=None):
        if status not in {"completed", "rejected", "unknown", "failed"}:
            raise ValueError("invalid_attempt_status")
        with self.store.connection(write=True) as db:
            self.store._owned(db, "processing_jobs", job_id)
            changed = db.execute("UPDATE processing_attempts SET status=?,response_json=?,updated_at=? WHERE id=? AND job_id=? AND status='started'", (status, encoded(metadata or {}), now(), attempt_id, job_id)).rowcount
            if changed:
                self._event(db, job_id, "request_finished", {"attempt": attempt_id, "status": status})

    def finish(self, job_id, status, *, error=None):
        if status not in TERMINAL:
            raise ValueError("invalid_terminal_status")
        # Terminal checkpoints still occupy storage and must not become a way
        # to evade the account quota with repeated failed/cancelled tasks.
        retained = self._checkpoint_bytes(job_id)
        with self.store.connection(write=True) as db:
            job = self.store._owned(db, "processing_jobs", job_id)
            if job["status"] in TERMINAL:
                return
            usage = json.loads(job["usage_json"])
            checkpoint = json.loads(job["checkpoint_json"])
            started = checkpoint.pop("runStarted", None)
            if started:
                usage["seconds"] += max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds()))
            db.execute("UPDATE processing_jobs SET status=?,error=?,usage_json=?,checkpoint_json=?,reserved_bytes=?,updated_at=? WHERE id=?",
                       (status, error, encoded(usage), encoded(checkpoint), retained, now(), job_id))
            self._event(db, job_id, status, {"error": error})

    def _checkpoint_bytes(self, job_id):
        root = self.store.artifact_directory(job_id)
        if not root.exists():
            return 0
        # Server-generated checkpoints only; refuse symlinks rather than
        # following anything outside this owner's artifact tree.
        from ipaper.security.paths import ensure_confined_tree
        ensure_confined_tree(self.store.papers_root, root)
        total = sum(path.stat().st_size for path in root.rglob('*') if path.is_file())
        return total

    def cancel(self, job_id):
        with self.store.connection(write=True) as db:
            job = self.store._owned(db, "processing_jobs", job_id)
            if job["status"] in {"completed", "failed", "cancelled", "partial"}:
                return
            status = "cancelling" if job["status"] == "running" else "cancelled"
            db.execute("UPDATE processing_jobs SET cancel_requested=1,status=?,reserved_bytes=?,updated_at=? WHERE id=?", (status, job["reserved_bytes"] if status == "cancelling" else self._checkpoint_bytes(job_id), now(), job_id))
            self._event(db, job_id, status, {"supplierCancellationConfirmed": False})

    def resume(self, job_id):
        with self.store.connection(write=True) as db:
            job = self.store._owned(db, "processing_jobs", job_id)
            if job.get("error") == "checkpoint_expired":
                raise ProcessingError("checkpoint_expired", 409)
            if job["status"] not in {"partial", "interrupted", "failed", "cancelled"}:
                raise ProcessingError("job_not_resumable", 409)
            if db.execute("SELECT 1 FROM processing_jobs WHERE owner_id=? AND status IN ('queued','running','cancelling')", (self.store.owner,)).fetchone():
                raise ProcessingError("user_processing_busy", 409)
            if db.execute("SELECT count(*) FROM processing_jobs WHERE status='queued'").fetchone()[0] >= 20:
                raise ProcessingError("processing_queue_full", 429)
            # A submitted-but-unknown cloud creation cannot be recovered by
            # recreating a task. A recorded batch ID may be queried on resume.
            unknown = db.execute("SELECT unit,response_json FROM processing_attempts WHERE job_id=? AND kind='cloud_create' AND status IN ('started','unknown')", (job_id,)).fetchall()
            parts=json.loads(job["checkpoint_json"]).get("parts",{})
            for attempt in unknown:
                # An accepted batch checkpoint can be queried even if a crash
                # occurred immediately before marking the create attempt done.
                unit=attempt["unit"]
                index=unit[5:] if isinstance(unit,str) and unit.startswith("part-") else None
                if index is None or not parts.get(index,{}).get("batchId"):
                    raise ProcessingError("cloud_submission_unknown_no_resubmit",409)
            occupied = self.store.occupied_bytes(db)
            reserved = db.execute("SELECT coalesce(sum(reserved_bytes),0) FROM processing_jobs WHERE owner_id=? AND id!=?", (self.store.owner,job_id)).fetchone()[0]
            if occupied + reserved + self.result_quota > self.owner_quota:
                raise ProcessingError("owner_quota_exceeded", 413)
            db.execute("UPDATE processing_jobs SET status='queued',cancel_requested=0,error=NULL,reserved_bytes=?,updated_at=? WHERE id=?", (self.result_quota, now(), job_id))
            self._event(db, job_id, "resume_requested", {"unknownModelRequestsMayHaveBeenCharged": True})

    def recover(self):
        # Called once at Web startup, before scheduling. No network calls.
        with self.store.connection(write=True) as db:
            jobs = list(db.execute("SELECT * FROM processing_jobs WHERE owner_id=? AND status IN ('queued','running','cancelling')", (self.store.owner,)))
            for job in jobs:
                checkpoint = json.loads(job["checkpoint_json"])
                usage = json.loads(job["usage_json"])
                started = checkpoint.pop("runStarted",None)
                # The last durable timestamp bounds known running time. Time
                # while Web was down is not silently credited to a fresh run.
                if started:
                    usage["seconds"] += max(0,int((datetime.fromisoformat(job["updated_at"])-datetime.fromisoformat(started)).total_seconds()))
                db.execute("UPDATE processing_jobs SET status='interrupted',error='service_restarted',checkpoint_json=?,usage_json=?,updated_at=? WHERE id=?",
                           (encoded(checkpoint),encoded(usage),now(),job["id"]))
                db.execute("UPDATE processing_attempts SET status='unknown',updated_at=? WHERE job_id=? AND status='started'", (now(), job["id"]))
                self._event(db, job["id"], "interrupted", {"requiresExplicitResume": True})
