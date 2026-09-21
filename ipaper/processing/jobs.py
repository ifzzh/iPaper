"""Durable task admission, checkpoints, budgets and cancellation semantics."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from .common import ProcessingError, encoded, fingerprint, identifier, now

import os

# Kinds whose model work is a whole-paper structured translation. Only these
# receive the larger envelope: BabelDOC, selection translation, overview,
# interpretation and the plain MinerU parse keep their previous limits.
TRANSLATION_KINDS = frozenset({"translate", "parse_translate", "retranslate"})
BUDGET_KEYS = ("requests", "inputTokens", "outputTokens", "seconds")
BUDGET_LABELS = {"requests": "模型请求", "inputTokens": "输入 token 预留", "outputTokens": "输出 token 预留", "seconds": "累计执行时间（秒）"}

DEFAULT_BUDGET = {"requests": 200, "inputTokens": 500_000, "outputTokens": 250_000, "seconds": 7200}
# A full paper no longer has to fit a small fixed envelope: the default covers
# the reported 15-page / 271-block scope (245 base requests) even if every base
# request used its one allowed 429 retry.
DEFAULT_TRANSLATION_BUDGET = {
    "requests": 500,
    "inputTokens": 1_000_000,
    "outputTokens": 500_000,
    "seconds": 14_400,
}
_DEPLOY_MAX_TRANSLATION_BUDGET = {
    "requests": 2_000,
    "inputTokens": 5_000_000,
    "outputTokens": 2_000_000,
    "seconds": 43_200,
}
_MAX_ENV = {
    "requests": "IPAPER_TRANSLATION_MAX_REQUESTS",
    "inputTokens": "IPAPER_TRANSLATION_MAX_INPUT_TOKENS",
    "outputTokens": "IPAPER_TRANSLATION_MAX_OUTPUT_TOKENS",
    "seconds": "IPAPER_TRANSLATION_MAX_SECONDS",
}


def _deploy_max_budget() -> dict:
    """Deploy-wide ceiling for structured translation, overridable per component.

    An unreadable override fails loudly at import: a silently clamped or ignored
    limit would be worse than refusing to start.
    """
    ceiling = dict(_DEPLOY_MAX_TRANSLATION_BUDGET)
    for key, variable in _MAX_ENV.items():
        raw = os.environ.get(variable)
        if raw is None or raw == "":
            continue
        if not raw.strip().isdigit() or int(raw) < 1:
            raise RuntimeError(
                f"{variable} must be a positive integer, got {raw!r}"
            )
        ceiling[key] = int(raw)
    return ceiling


MAX_TRANSLATION_BUDGET = _deploy_max_budget()


def _builtin_default(kind: str | None = None) -> dict:
    return dict(DEFAULT_TRANSLATION_BUDGET if kind in TRANSLATION_KINDS else DEFAULT_BUDGET)


def default_budget(kind: str | None = None) -> dict:
    """The default envelope, capped by a lower deployment ceiling.

    ``IPAPER_TRANSLATION_MAX_*`` configures the maximum, not the default. When an
    operator lowers the ceiling below the built-in default, the effective default
    is the ceiling itself: a default that silently exceeded its own ceiling would
    make every admission check meaningless.
    """
    builtin = _builtin_default(kind)
    ceiling = budget_ceiling(kind)
    return {key: min(builtin[key], ceiling[key]) for key in BUDGET_KEYS}


def default_capped_by_ceiling(kind: str | None = None) -> bool:
    return default_budget(kind) != _builtin_default(kind)


def budget_ceiling(kind: str | None = None) -> dict:
    """The largest envelope this deployment accepts for the kind.

    Translation kinds get the deploy ceiling; every other kind keeps the old
    fixed values so this change cannot silently widen them.
    """
    return dict(MAX_TRANSLATION_BUDGET if kind in TRANSLATION_KINDS else DEFAULT_BUDGET)
TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted"}
ACTIVE = {"queued", "running", "cancelling"}


class ProcessingJobs:
    def __init__(self, store, *, result_quota=1024**3, owner_quota=10*1024**3):
        self.store = store
        self.result_quota, self.owner_quota = result_quota, owner_quota

    @staticmethod
    def budget(value=None, kind=None):
        """The effective budget for a job kind.

        Defaults come from the kind, and an explicit value may only narrow or
        widen within that kind's deploy ceiling. Every number must be a positive
        integer; anything else is rejected instead of being coerced.
        """
        result = default_budget(kind)
        ceiling = budget_ceiling(kind)
        if value is not None:
            if not isinstance(value, dict) or set(value) - set(result):
                raise ProcessingError("invalid_budget")
            for key, number in value.items():
                if type(number) is not int or not 1 <= number <= ceiling[key]:
                    raise ProcessingError(
                        "invalid_budget",
                        details={
                            "dimension": key,
                            "dimensionLabel": BUDGET_LABELS.get(key, key),
                            "limit": ceiling[key],
                            "allowedCeiling": ceiling,
                        },
                    )
                result[key] = number
        # Validate the *composed* envelope on every path, including defaults and
        # partially specified values: no admission may exceed the deployment cap.
        over = {
            key: {"dimension": key, "dimensionLabel": BUDGET_LABELS.get(key, key),
                  "value": result[key], "limit": ceiling[key]}
            for key in BUDGET_KEYS
            if result[key] > ceiling[key]
        }
        if over:
            raise ProcessingError(
                "invalid_budget_configuration", 500,
                details={"overage": over, "budget": result, "limit": ceiling,
                         "hint": "IPAPER_TRANSLATION_MAX_* configures the maximum; a ceiling below the built-in default caps the default."},
            )
        return result

    @staticmethod
    def budget_ceiling(kind=None):
        return budget_ceiling(kind)

    @staticmethod
    def default_capped_by_ceiling(kind=None):
        return default_capped_by_ceiling(kind)

    @staticmethod
    def check_scope(estimate, budget, usage=None):
        """Which dimensions a scope overruns, with required and available values."""
        usage = usage or {}
        overage = {}
        for key in ("requests", "inputTokens", "outputTokens"):
            required = int(estimate.get(key) or 0) + int(usage.get(key) or 0)
            limit = int(budget.get(key) or 0)
            if required > limit:
                overage[key] = {
                    "dimension": key,
                    "dimensionLabel": BUDGET_LABELS.get(key, key),
                    "required": required,
                    "remaining": max(0, limit - int(usage.get(key) or 0)),
                    "limit": limit,
                    "alreadyUsed": int(usage.get(key) or 0),
                }
        return overage

    def create(self, paper_id, kind, request, *, document_id=None, result_id=None, budget=None, reservation=None):
        if kind not in {"parse", "translate", "parse_translate", "retranslate", "overview", "interpretation", "analysis_export", "selection_translate"}:
            raise ProcessingError("invalid_processing_kind")
        budget = self.budget(budget, kind=kind)
        reservation = self.result_quota if reservation is None else reservation
        if type(reservation) is not int or not 0 < reservation <= self.result_quota:
            raise ProcessingError("invalid_quota_reservation")
        identity_request = request
        if kind == "selection_translate":
            # Equivalent verified source objects may have different UUIDs.
            # Deduplicate their effective selection/configuration, not the
            # incidental ID minted while opening the selection UI.
            identity_request = {k: request.get(k) for k in ("cacheKey", "retryJobId", "renewAfter")}
        key = fingerprint({"paper": paper_id, "kind": kind, "request": identity_request, "budget": budget})
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

    def check_status(self, job_id):
        """Cancellation and status only — no execution-time check.

        Publishing already-saved work needs no clock, so a continuation that has
        nothing left to request must not fail just because its time limit is spent.
        """
        job = self.get(job_id)
        with self.store.connection() as db:
            user = db.execute("SELECT status FROM users WHERE id=?", (self.store.owner,)).fetchone()
        if not user or user["status"] != "active":
            raise ProcessingError("processing_cancelled", 409)
        if job["cancel_requested"] or job["status"] == "cancelling":
            raise ProcessingError("processing_cancelled", 409)
        if job["status"] != "running":
            raise ProcessingError("job_not_running", 409)
        return job

    def check(self, job_id):
        job = self.check_status(job_id)
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

    def resume(self, job_id, budget=None):
        """Continue a stopped job, optionally raising its whole-task envelope.

        The adjusted budget is the task's cumulative total: already reserved
        requests/tokens/seconds are never reset, and it may not be lower than
        what the task has already used.
        """
        with self.store.connection(write=True) as db:
            job = self.store._owned(db, "processing_jobs", job_id)
            if job["kind"] == "selection_translate":
                raise ProcessingError("selection_explicit_retry_required", 409)
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
            previous = json.loads(job["budget_json"])
            usage = json.loads(job["usage_json"])
            adjusted = previous
            if budget is not None:
                if not isinstance(budget, dict) or not budget:
                    raise ProcessingError("invalid_budget")
                merged = {**previous, **budget}
                adjusted = self.budget(merged, kind=job["kind"])
                below = {
                    key: {"dimension": key, "dimensionLabel": BUDGET_LABELS.get(key, key),
                          "used": int(usage.get(key) or 0), "requested": adjusted[key]}
                    for key in BUDGET_KEYS
                    if adjusted[key] < int(usage.get(key) or 0)
                }
                if below:
                    raise ProcessingError(
                        "budget_below_usage",
                        details={"overage": below, "usage": usage, "budget": adjusted},
                    )
            db.execute("UPDATE processing_jobs SET status='queued',cancel_requested=0,error=NULL,reserved_bytes=?,budget_json=?,updated_at=? WHERE id=?",
                       (self.result_quota, encoded(adjusted), now(), job_id))
            self._event(db, job_id, "resume_requested", {"unknownModelRequestsMayHaveBeenCharged": True})
            if adjusted != previous:
                self._event(db, job_id, "budget_adjusted", {"before": previous, "after": adjusted})

    def actual_usage(self, job_id):
        """Supplier-reported usage, summed from finished attempts.

        Missing reports are counted, never turned into zeros: callers must show
        this as partial. The reserved usage in ``usage_json`` is a conservative
        pre-charge and is reported separately.
        """
        self.get(job_id)  # owner-scoped existence check
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT status,response_json FROM processing_attempts WHERE job_id=? AND kind='model'",
                (job_id,),
            ).fetchall()
        input_tokens = output_tokens = 0
        input_reported = output_reported = 0
        for row in rows:
            try:
                response = json.loads(row["response_json"] or "{}")
            except ValueError:
                response = {}
            # Each dimension is judged on its own: a supplier that reports only
            # one of them must not make the other look like a trustworthy zero.
            if type(response.get("inputTokens")) is int:
                input_tokens += int(response["inputTokens"])
                input_reported += 1
            if type(response.get("outputTokens")) is int:
                output_tokens += int(response["outputTokens"])
                output_reported += 1
        total = len(rows)
        input_missing = total - input_reported
        output_missing = total - output_reported
        return {
            "requests": total,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "inputReportedAttempts": input_reported,
            "outputReportedAttempts": output_reported,
            "inputMissingAttempts": input_missing,
            "outputMissingAttempts": output_missing,
            "reportedAttempts": min(input_reported, output_reported),
            "missingAttempts": max(input_missing, output_missing),
            "inputComplete": bool(total) and input_missing == 0,
            "outputComplete": bool(total) and output_missing == 0,
            "complete": bool(total) and input_missing == 0 and output_missing == 0,
        }

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


for _kind in sorted(TRANSLATION_KINDS):
    if default_capped_by_ceiling(_kind):
        print(
            "[ipaper] deployment ceiling lowers the default translation budget for"
            f" {_kind}: {default_budget(_kind)} (built-in {_builtin_default(_kind)})"
        )
        break
