"""Explicit acceptance-only reuse of retained model responses; no HTTP fallback."""

import json
import sqlite3
from pathlib import Path

from ipaper.processing.common import encoded, fingerprint
from ipaper.processing.pipeline import file_digest
from ipaper.processing.store import ProcessingStore
from ipaper.processing.understanding import Understanding, batches, model_units
from ipaper.security.paths import safe_join


class OverviewReplay:
    def __init__(self, root, config):
        root = Path(root).resolve()
        receipt = json.loads((root / "request-receipt.json").read_text())
        if not receipt.get("live") or any(
            receipt[k] != config[k]
            for k in ("paperId", "ownerId", "sourceHash", "credentialRevision")
        ):
            raise ValueError("replay_identity_mismatch")
        with sqlite3.connect(f'file:{root / "ipaper.db"}?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            jobs = db.execute(
                "SELECT * FROM processing_jobs WHERE kind='overview' AND paper_id=?",
                (config["paperId"],),
            ).fetchall()
            if len(jobs) != 1:
                raise ValueError("replay_job_ambiguous")
            job = jobs[0]
            request = json.loads(job["request_json"])
            previous = request["config"]
            if any(previous[k] != config["profile"][k] for k in ("model", "baseUrl")):
                raise ValueError("replay_model_changed")
            artifact = db.execute(
                "SELECT * FROM understanding_artifacts WHERE id=? AND owner_id=?",
                (request["snapshotId"], job["owner_id"]),
            ).fetchone()
            attempts = db.execute(
                "SELECT unit,status FROM processing_attempts WHERE job_id=? ORDER BY created_at,id",
                (job["id"],),
            ).fetchall()
        store = ProcessingStore(root / "ipaper.db", root / "papers", job["owner_id"])
        path = safe_join(
            store.artifact_directory(artifact["id"]),
            "body.json",
            must_exist=True,
            require_file=True,
        )
        entry = next(
            e
            for e in json.loads(artifact["manifest_json"])["entries"]
            if e["path"] == "body.json"
        )
        if path.stat().st_size != entry["size"] or file_digest(path) != entry["sha256"]:
            raise ValueError("replay_snapshot_hash_mismatch")
        source = json.loads(path.read_text())
        if (
            source["sourceHash"] != config["sourceHash"]
            or not source["coverage"]["complete"]
        ):
            raise ValueError("replay_source_mismatch")
        groups = batches(model_units(source))
        if len(groups) != 5 or [(a["unit"], a["status"]) for a in attempts[:5]] != [
            (f"analysis-{i}", "completed") for i in range(5)
        ]:
            raise ValueError("replay_attempt_sequence_mismatch")
        self.entries, self.index = [], 0
        for i, group in enumerate(groups):
            path = root / f"response-{i + 1}.json"
            response = json.loads(path.read_text())
            if (
                response.get("status") != "completed"
                or response.get("error")
                or response.get("finishReason") != "stop"
            ):
                raise ValueError("replay_response_incomplete")
            messages = Understanding.prompt(
                None, previous, "overview", group, compact=True
            )
            self.entries.append(
                {
                    "messages": messages,
                    "response": response,
                    "provenance": {
                        "originalJob": job["id"],
                        "originalUnit": f"analysis-{i}",
                        "responseSha256": file_digest(path),
                        "sourceSha256": entry["sha256"],
                        "reconstructedRequestSha256": fingerprint(messages),
                        "requestHashCapturedOriginally": False,
                    },
                }
            )

    def take(self, messages, output):
        if self.index >= len(self.entries):
            raise ValueError("replay_exhausted")
        entry = self.entries[self.index]
        if output != 2048 or encoded(messages) != encoded(entry["messages"]):
            raise ValueError("replay_request_changed")
        self.index += 1
        return entry["response"], entry["provenance"]
