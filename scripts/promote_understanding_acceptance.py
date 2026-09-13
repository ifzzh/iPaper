#!/usr/bin/env python3
"""Promote a separately authorized and verified single-paper acceptance package.

Offline operator tool, dry-run by default. Stop both writers and capture the
release backup first. Copy no users, keys, settings or old processing results.
Existing analysis heads are never overwritten. An interrupted copy rolls back
new database rows and removes only the newly created artifact directories.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ipaper.processing.common import encoded, identifier
from ipaper.processing.pipeline import file_digest
from ipaper.processing.store import ProcessingStore
from ipaper.security.paths import safe_join, ensure_confined, user_storage_root
from scripts.promote_processing_acceptance import insert


def promote(source_root, target, paper_id, *, apply=False, require_live=True):
    root = Path(source_root).resolve()
    report = json.loads((root / "result.json").read_text())
    receipt = json.loads((root / "request-receipt.json").read_text())
    if not report.get("passed") or (
        require_live and not (report.get("live") and receipt.get("live"))
    ):
        raise ValueError("verified_real_acceptance_required")
    if (
        receipt["ownerId"] != target.owner
        or receipt["paperId"] != paper_id
        or report["paperId"] != paper_id
    ):
        raise ValueError("acceptance_identity_mismatch")
    owner = report["ownerId"]
    source_store = ProcessingStore(root / "ipaper.db", root / "papers", owner)
    rows = {}
    tables = (
        "understanding_artifacts",
        "understanding_heads",
        "understanding_evidence",
        "understanding_chat_turns",
        "processing_jobs",
        "chats",
    )
    with sqlite3.connect(f'file:{root / "ipaper.db"}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for table in tables:
            rows[table] = [
                dict(r)
                for r in db.execute(
                    f"SELECT * FROM {table} WHERE owner_id=? AND paper_id=?",
                    (owner, paper_id),
                )
            ]
        for table in ("processing_events", "processing_attempts"):
            rows[table] = [
                dict(r)
                for r in db.execute(
                    f"SELECT t.* FROM {table} t JOIN processing_jobs j ON j.id=t.job_id WHERE j.owner_id=? AND j.paper_id=?",
                    (owner, paper_id),
                )
            ]
        rows["processing_chat_sources"] = [
            dict(r)
            for r in db.execute(
                "SELECT t.* FROM processing_chat_sources t JOIN chats c ON c.owner_id=t.owner_id AND c.session_id=t.session_id WHERE c.owner_id=? AND c.paper_id=?",
                (owner, paper_id),
            )
        ]
        source_parse = dict(
            db.execute(
                "SELECT * FROM processing_results WHERE id=? AND owner_id=?",
                (report["parseId"], owner),
            ).fetchone()
        )
    if not rows["understanding_artifacts"] or sum(map(len, rows.values())) > 100000:
        raise ValueError("acceptance_row_limit")
    if {r["kind"] for r in rows["understanding_heads"]} != {
        "overview",
        "interpretation",
    }:
        raise ValueError("verified_results_missing")
    if len(rows["understanding_chat_turns"]) != 2 or any(
        r["status"] != "completed" for r in rows["understanding_chat_turns"]
    ):
        raise ValueError("verified_chats_missing")
    if any(
        r["status"] != "completed"
        or r["kind"] not in {"overview", "interpretation", "analysis_export"}
        or r["parent_id"]
        or r["result_id"]
        or r["document_id"]
        for r in rows["processing_jobs"]
    ):
        raise ValueError("acceptance_task_not_completed")
    ids = {identifier(r["id"]) for r in rows["understanding_artifacts"]}
    kinds = {
        "content_snapshot",
        "analysis_chunk",
        "analysis_reduction",
        "overview",
        "interpretation",
        "analysis_export",
    }
    files = {}
    for row in rows["understanding_artifacts"]:
        if (
            row["kind"] not in kinds
            or row["status"] != "completed"
            or (row["source_id"] and row["source_id"] not in ids)
        ):
            raise ValueError("unverified_artifact")
        entries = json.loads(row["manifest_json"])["entries"]
        if sum(e["size"] for e in entries) != row["bytes"]:
            raise ValueError("artifact_size_mismatch")
        for e in entries:
            path = safe_join(
                source_store.artifact_directory(row["id"]),
                e["path"],
                must_exist=True,
                require_file=True,
            )
            if (
                not 0 <= e["size"] <= 64 * 1024**2
                or path.stat().st_size != e["size"]
                or file_digest(path) != e["sha256"]
            ):
                raise ValueError("artifact_hash_mismatch")
            files[(row["id"], e["path"])] = (path, e["sha256"], e["size"])
        if row["kind"] == "content_snapshot":
            body = json.loads(
                (source_store.artifact_directory(row["id"]) / "body.json").read_text()
            )
            if (
                body["sourceHash"] != report["sourceHash"]
                or body["parseId"] != report["parseId"]
                or not body["coverage"]["complete"]
            ):
                raise ValueError("snapshot_source_mismatch")
    if any(r["snapshot_id"] not in ids for r in rows["understanding_evidence"]):
        raise ValueError("evidence_snapshot_missing")
    evidence = {r["id"] for r in rows["understanding_evidence"]}
    for row in rows["processing_chat_sources"]:
        if any(v not in evidence for v in json.loads(row["sources_json"]).values()):
            raise ValueError("chat_source_mismatch")
    for values in rows.values():
        for row in values:
            if "owner_id" in row:
                row["owner_id"] = target.owner
    for row in rows["processing_events"]:
        row.pop("sequence")
    for row in rows["processing_jobs"]:
        row["reserved_bytes"] = 0
    created = []
    total = sum(v[2] for v in files.values())
    try:
        with target.connection(write=True) as db:
            target.paper_exists(db, paper_id)
            if (
                db.execute(
                    "SELECT 1 FROM processing_jobs WHERE status IN ('queued','running','cancelling')"
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM understanding_chat_turns WHERE status IN ('preparing','streaming')"
                ).fetchone()
            ):
                raise ValueError("stop_all_active_tasks")
            path = db.execute(
                "SELECT file_path FROM papers WHERE owner_id=? AND id=?",
                (target.owner, paper_id),
            ).fetchone()[0]
            original = ensure_confined(
                user_storage_root(target.papers_root, target.owner),
                path,
                must_exist=True,
                require_file=True,
            )
            if (
                file_digest(original) != receipt["sourceHash"]
                or receipt["sourceHash"] != report["sourceHash"]
            ):
                raise ValueError("source_changed_before_promotion")
            parsed = db.execute(
                "SELECT * FROM processing_results WHERE owner_id=? AND paper_id=? AND id=?",
                (target.owner, paper_id, report["parseId"]),
            ).fetchone()
            if not parsed or {
                k: v for k, v in dict(parsed).items() if k != "owner_id"
            } != {k: v for k, v in source_parse.items() if k != "owner_id"}:
                raise ValueError("parse_changed_before_promotion")
            if db.execute(
                "SELECT 1 FROM understanding_heads WHERE owner_id=? AND paper_id=?",
                (target.owner, paper_id),
            ).fetchone():
                raise ValueError("target_analysis_already_exists")
            cfg = db.execute(
                "SELECT value FROM user_settings_v2 WHERE owner_id=? AND key='agentic_settings'",
                (target.owner,),
            ).fetchone()
            model = json.loads(cfg[0])["llmConfigs"]["interpret"] if cfg else {}
            revision = db.execute(
                "SELECT updated_at FROM agentic_secrets_v2 WHERE owner_id=? AND name='interpret'",
                (target.owner,),
            ).fetchone()
            if not revision or revision[0] != receipt["credentialRevision"]:
                raise ValueError("credential_configuration_changed")
            for row in rows["understanding_artifacts"]:
                if row["kind"] in {"overview", "interpretation"}:
                    config = json.loads(row["config_json"])
                    if (
                        config["model"],
                        config["baseUrl"],
                        config["credentialRevision"],
                    ) != (model.get("llmModel"), model.get("llmBaseUrl"), revision[0]):
                        raise ValueError("generation_configuration_changed")
                if (
                    db.execute(
                        "SELECT 1 FROM understanding_artifacts WHERE id=?", (row["id"],)
                    ).fetchone()
                    or target.artifact_directory(row["id"]).exists()
                ):
                    raise ValueError("target_artifact_exists")
            if (
                total > target.quotas()[1]
                or target.occupied_bytes(db) + total > target.quotas()[1]
            ):
                raise ValueError("owner_quota_exceeded")
            if apply:
                stat = original.stat()
                for rid in ids:
                    directory = target.artifact_directory(rid, create=True)
                    created.append(directory)
                    for (artifact, relative), (source, sha, size) in files.items():
                        if artifact != rid:
                            continue
                        dest = safe_join(directory, relative)
                        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        with source.open("rb") as reader, dest.open("xb") as writer:
                            shutil.copyfileobj(reader, writer, 1024**2)
                            writer.flush()
                            os.fsync(writer.fileno())
                        dest.chmod(0o600)
                        if file_digest(dest) != sha:
                            raise ValueError("promotion_copy_hash_mismatch")
                    if os.geteuid() == 0:
                        for path in [directory, *directory.rglob("*")]:
                            os.chown(path, stat.st_uid, stat.st_gid)
                for table, values in rows.items():
                    for row in values:
                        insert(db, table, row)
    except BaseException:
        for directory in created:
            shutil.rmtree(directory)
        raise
    return {
        "apply": apply,
        "live": report["live"],
        "artifacts": len(ids),
        "files": len(files),
        "bytes": total,
        "jobs": len(rows["processing_jobs"]),
        "chats": 2,
        "oldRowsOverwritten": False,
        "credentialsCopied": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "database", "papers", "owner", "paper"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        encoded(
            promote(
                args.source,
                ProcessingStore(args.database, args.papers, args.owner),
                args.paper,
                apply=args.apply,
            )
        )
    )


if __name__ == "__main__":
    main()
