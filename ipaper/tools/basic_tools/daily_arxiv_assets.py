"""Persistent, single-flight Daily arXiv artifact scheduling."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from ipaper.security.identity import Identity, run_as_identity


INFRASTRUCTURE_ERRORS = frozenset({
    "document_queue_full",
    "document_worker_unavailable",
    "document_staging_unavailable",
    "interrupted",
})
ACTIVE_STATES = frozenset({"candidate", "queued", "downloading", "validating", "retry_wait"})
BACKOFF_MINUTES = (5, 30, 120, 360)


@dataclass(frozen=True)
class AssetResult:
    success: bool
    error_code: str | None = None


class DailyAssetCoordinator:
    """Claim at most one persisted artifact task and process owners fairly."""

    def __init__(
        self,
        db_path: str,
        processor: Callable[[str, str, Callable[[str, str | None], None]], AssetResult],
        *,
        poll_interval: float = 1.0,
        autostart: bool = True,
    ) -> None:
        self.db_path = db_path
        self.processor = processor
        self.poll_interval = poll_interval
        self._condition = threading.Condition()
        self._stopping = False
        self._last_owner: str | None = None
        self._thread: threading.Thread | None = None
        self.recover()
        if autostart:
            self._thread = threading.Thread(
                target=self._run, name="daily-asset-coordinator", daemon=True
            )
            self._thread.start()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def recover(self) -> None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE daily_arxiv_candidates
                   SET artifact_status='queued', asset_job_id=NULL, claimed_at=NULL,
                       artifact_error_code=CASE
                         WHEN artifact_error_code IS NULL THEN 'interrupted'
                         ELSE artifact_error_code END,
                       next_retry_at=NULL, updated_at=?
                   WHERE artifact_status IN ('downloading','validating')""",
                (now,),
            )

    def enqueue(self, owner_id: str, arxiv_id: str, *, force: bool = False) -> dict | None:
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM daily_arxiv_candidates WHERE owner_id=? AND "
                "(arxiv_id=? OR arxiv_id LIKE ? || 'v%' OR arxiv_id LIKE '%/' || ?"
                " OR arxiv_id LIKE '%/' || ? || 'v%')",
                (owner_id, arxiv_id, arxiv_id, arxiv_id, arxiv_id),
            ).fetchone()
            if row is None:
                return None
            # Address the row by its stored key from here on.
            arxiv_id = row["arxiv_id"]
            if row["artifact_status"] == "ready" and not force:
                return dict(row)
            if row["artifact_status"] in {"queued", "downloading", "validating"}:
                return dict(row)
            connection.execute(
                """UPDATE daily_arxiv_candidates
                   SET artifact_status='queued', next_retry_at=NULL, asset_job_id=NULL,
                       claimed_at=NULL, artifact_error_code=NULL, updated_at=?
                   WHERE owner_id=? AND arxiv_id=?""",
                (now, owner_id, arxiv_id),
            )
            row = connection.execute(
                "SELECT * FROM daily_arxiv_candidates WHERE owner_id=? AND arxiv_id=?",
                (owner_id, arxiv_id),
            ).fetchone()
        with self._condition:
            self._condition.notify_all()
        return dict(row)

    def _eligible_rows(self, connection: sqlite3.Connection) -> list[sqlite3.Row]:
        now = self._now()
        return connection.execute(
            """SELECT * FROM daily_arxiv_candidates
               WHERE artifact_status IN ('candidate','queued')
                  OR (artifact_status='retry_wait' AND (next_retry_at IS NULL OR next_retry_at<=?))
               ORDER BY updated_at, owner_id, arxiv_id""",
            (now,),
        ).fetchall()

    def _choose_fair(self, rows: list[sqlite3.Row]) -> sqlite3.Row | None:
        if not rows:
            return None
        owners = sorted({row["owner_id"] for row in rows})
        owner = owners[0]
        if self._last_owner in owners:
            owner = owners[(owners.index(self._last_owner) + 1) % len(owners)]
        self._last_owner = owner
        return next(row for row in rows if row["owner_id"] == owner)

    def _claim(self) -> dict | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._choose_fair(self._eligible_rows(connection))
            if row is None:
                connection.rollback()
                return None
            now = self._now()
            job_id = str(uuid.uuid4())
            cursor = connection.execute(
                """UPDATE daily_arxiv_candidates
                   SET artifact_status='downloading', asset_job_id=?, claimed_at=?,
                       last_attempt_at=?, next_retry_at=NULL, artifact_error_code=NULL,
                       updated_at=?
                   WHERE owner_id=? AND arxiv_id=?
                     AND (artifact_status IN ('candidate','queued')
                       OR (artifact_status='retry_wait' AND (next_retry_at IS NULL OR next_retry_at<=?)))""",
                (job_id, now, now, now, row["owner_id"], row["arxiv_id"], now),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            return {**dict(row), "asset_job_id": job_id, "claimed_at": now}

    def _set_stage(self, owner_id: str, arxiv_id: str, job_id: str, stage: str) -> None:
        if stage not in {"downloading", "validating"}:
            return
        with self._connect() as connection:
            connection.execute(
                """UPDATE daily_arxiv_candidates SET artifact_status=?, updated_at=?
                   WHERE owner_id=? AND arxiv_id=? AND asset_job_id=?""",
                (stage, self._now(), owner_id, arxiv_id, job_id),
            )

    def _finish(self, task: dict, result: AssetResult) -> None:
        now = datetime.now(timezone.utc)
        code = result.error_code or "document_processing_failed"
        attempts = int(task.get("retry_count") or 0)
        if result.success:
            status, next_retry, code = "ready", None, None
        elif code in INFRASTRUCTURE_ERRORS:
            status, next_retry = "retry_wait", now + timedelta(seconds=60)
        else:
            attempts += 1
            status = "failed" if attempts >= 5 else "retry_wait"
            delay = BACKOFF_MINUTES[min(attempts - 1, len(BACKOFF_MINUTES) - 1)]
            next_retry = None if status == "failed" else now + timedelta(minutes=delay)
        with self._connect() as connection:
            connection.execute(
                """UPDATE daily_arxiv_candidates
                   SET artifact_status=?, retry_count=?, next_retry_at=?,
                       artifact_error_code=?, asset_job_id=NULL, claimed_at=NULL, updated_at=?
                   WHERE owner_id=? AND arxiv_id=? AND asset_job_id=?""",
                (status, attempts, next_retry.isoformat() if next_retry else None, code,
                 now.isoformat(), task["owner_id"], task["arxiv_id"], task["asset_job_id"]),
            )

    def run_once(self) -> bool:
        task = self._claim()
        if task is None:
            return False
        owner_id, arxiv_id, job_id = task["owner_id"], task["arxiv_id"], task["asset_job_id"]
        try:
            identity = Identity(owner_id, owner_id, "user")
            callback = lambda stage, remote_id=None: self._set_stage(
                owner_id, arxiv_id, job_id, stage
            )
            result = run_as_identity(identity, self.processor, owner_id, arxiv_id, callback)
            if not isinstance(result, AssetResult):
                result = AssetResult(bool(result), None if result else "document_processing_failed")
        except Exception:
            result = AssetResult(False, "document_processing_failed")
        self._finish(task, result)
        return True

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
            if not self.run_once():
                with self._condition:
                    self._condition.wait(timeout=self.poll_interval)

    def queue_position(self, owner_id: str, arxiv_id: str) -> int | None:
        with self._connect() as connection:
            rows = self._eligible_rows(connection)
        for index, row in enumerate(rows, start=1):
            if row["owner_id"] == owner_id and row["arxiv_id"] == arxiv_id:
                return index
        return None

    def shutdown(self, *, timeout: float = 5.0) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=timeout)
