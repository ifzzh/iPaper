#!/usr/bin/env python3
"""Read-only, one-paper acceptance bundle. Never copy production accounts/DB/keys.

The operator supplies a paper already authorized for acceptance. Published parse
assets and two existing PDFs are copied into a new private directory. This tool
cannot parse, translate, call a model or modify source data.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ipaper.processing.store import ProcessingStore
from ipaper.processing.understanding_store import UnderstandingStore
from ipaper.processing.pipeline import file_digest
from ipaper.security.paths import ensure_confined, safe_join, paper_asset_paths
from types import SimpleNamespace


class ReadOnlyStore(ProcessingStore):
    @contextmanager
    def connection(self, *, write=False):
        if write:
            raise ValueError("source_is_read_only")
        db = sqlite3.connect("file:" + self.db_path + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()


def prepare(database, papers, paper, destination, *, container_root=None):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("destination_exists")
    papers = Path(papers).resolve()
    with sqlite3.connect(
        "file:" + str(Path(database).resolve()) + "?mode=ro", uri=True
    ) as db:
        db.row_factory = sqlite3.Row
        record = db.execute(
            "SELECT id,owner_id,title,authors,abstract,file_path FROM papers WHERE id=?",
            (paper,),
        ).fetchone()
        if not record:
            raise ValueError("paper_not_found")
    store = ReadOnlyStore(database, papers, record["owner_id"])
    source = Path(record["file_path"])
    if container_root:
        source = papers / source.relative_to(container_root)
    source = ensure_confined(papers, source, must_exist=True, require_file=True)
    content = UnderstandingStore(
        SimpleNamespace(store=store, paper_file=lambda _: source)
    )
    body, _ = content.content(paper)
    if not body.get("coverage", {}).get("complete"):
        raise ValueError("complete_existing_parse_required")
    parsed = store.result(body["parseId"])
    document = store.document(parsed["document_id"])
    with store.connection() as db:
        split = db.execute(
            "SELECT * FROM processing_results WHERE owner_id=? AND document_id=? AND kind='document_parts' AND status='completed' ORDER BY created_at DESC LIMIT 1",
            (store.owner, document["id"]),
        ).fetchone()
        results = [
            dict(split),
            *[store.result(r) for r in json.loads(parsed["config_json"])["parts"]],
            parsed,
        ]
        blocks = [
            dict(row)
            for result in results
            for row in db.execute(
                "SELECT * FROM processing_blocks WHERE result_id=?", (result["id"],)
            )
        ]
    metadata = {
        k: record[k] for k in ("id", "owner_id", "title", "authors", "abstract")
    }
    document["file_ref"] = "input/original.pdf"
    bundle = {
        "format": 1,
        "paper": metadata,
        "documents": [document],
        "results": results,
        "blocks": blocks,
        "coverage": body["coverage"],
        "blockCount": body["blockCount"],
        "files": [],
    }
    destination.mkdir(mode=0o700, parents=True)

    def copy(source, relative, sha, maximum=1024**3):
        target = safe_join(destination, relative)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        size = 0
        with source.open("rb") as reader, target.open("xb") as writer:
            while chunk := reader.read(1024**2):
                size += len(chunk)
                if size > maximum:
                    raise ValueError("source_size_limit")
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        target.chmod(0o600)
        if file_digest(target) != sha:
            raise ValueError("source_changed")
        bundle["files"].append({"path": relative, "sha256": sha, "size": size})

    try:
        copy(source, "input/original.pdf", document["sha256"])
        translated = paper_asset_paths(papers, source).chinese_dual
        if translated.is_file():
            copy(translated, "input/translated.pdf", file_digest(translated))
        for result in results:
            for entry in json.loads(result["manifest_json"]).get("entries", []):
                path = safe_join(
                    store.artifact_directory(result["id"]),
                    entry["path"],
                    must_exist=True,
                    require_file=True,
                )
                copy(
                    path,
                    "artifacts/" + result["id"] + "/" + entry["path"],
                    entry["sha256"],
                )
        if sum(f["size"] for f in bundle["files"]) > 10 * 1024**3:
            raise ValueError("sample_size_limit")
        receipt = destination / "sample.json"
        receipt.write_text(json.dumps(bundle, ensure_ascii=False))
        receipt.chmod(0o600)
        return {
            "paper": paper,
            "blocks": body["blockCount"],
            "pages": body["coverage"]["totalPages"],
            "files": len(bundle["files"]),
            "bytes": sum(f["size"] for f in bundle["files"]),
            "sourceHash": document["sha256"],
            "paidRequests": 0,
        }
    except BaseException:
        (destination / "INCOMPLETE").write_text("Not a verified sample.\n")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("database", "papers", "paper", "destination"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--container-root")
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                args.database,
                args.papers,
                args.paper,
                args.destination,
                container_root=args.container_root,
            )
        )
    )


if __name__ == "__main__":
    main()
