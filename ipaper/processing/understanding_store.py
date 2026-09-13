"""Versioned single-paper content. Legacy text never invents PDF geometry."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path

from ipaper.security.paths import (
    safe_join,
    ensure_confined,
    user_storage_root,
    paper_asset_paths,
)
from .common import ProcessingError, encoded, fingerprint, identifier, now
from .pipeline import file_digest


MAX_CONTENT_BYTES = 64 * 1024**2
MAX_UNITS = 100_000


def text_units(text, *, prefix="u"):
    """Bound paragraphs without dropping the end of a long document."""
    number = 0
    for paragraph in re.split(r"\n\s*\n", text):
        if not paragraph.strip():
            continue
        while paragraph:
            piece, paragraph = paragraph[:2000], paragraph[2000:]
            yield {
                "id": f"{prefix}{number}",
                "text": piece,
                "type": "text",
                "source": {"precision": "none", "regions": []},
            }
            number += 1


class UnderstandingStore:
    def __init__(self, pipeline):
        self.pipeline, self.store = pipeline, pipeline.store
        self._snapshots = {}

    def row(self, row_id):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM understanding_artifacts WHERE owner_id=? AND id=?",
                (self.store.owner, identifier(row_id)),
            ).fetchone()
            if not row:
                raise ProcessingError("understanding_not_found", 404)
            self.store.paper_exists(db, row["paper_id"])
            return dict(row)

    def body(self, row_id):
        row = self.row(row_id)
        if row_id in self._snapshots:
            return self._snapshots[row_id]
        path = safe_join(
            self.store.artifact_directory(row_id),
            "body.json",
            must_exist=True,
            require_file=True,
        )
        entry = next(
            e
            for e in json.loads(row["manifest_json"])["entries"]
            if e["path"] == "body.json"
        )
        if (
            path.stat().st_size > MAX_CONTENT_BYTES
            or file_digest(path) != entry["sha256"]
        ):
            raise ProcessingError("content_snapshot_invalid", 409)
        value = json.loads(path.read_text(encoding="utf-8"))
        if row["kind"] == "content_snapshot":
            self._snapshots[row_id] = value
        return value

    def publish(
        self,
        paper_id,
        kind,
        body,
        *,
        config=None,
        source_id=None,
        status="completed",
        assets=None,
        key=None,
        job_id=None,
        head=False,
        previous=None,
    ):
        config = config or {}
        payload = encoded(body).encode()
        if len(payload) > MAX_CONTENT_BYTES:
            raise ProcessingError("content_size_limit", 413)
        digest = key or fingerprint([paper_id, kind, body, config])
        with self.store.connection() as db:
            self.store.paper_exists(db, paper_id)
            existing = db.execute(
                "SELECT id FROM understanding_artifacts WHERE owner_id=? AND kind=? AND fingerprint=?",
                (self.store.owner, kind, digest),
            ).fetchone()
            if existing:
                return existing[0]
        row_id = identifier()
        root = self.store.artifact_directory(row_id, create=True)
        entries, total = [], 0
        try:
            (root / "body.json").write_bytes(payload)
            for name, source in (assets or {}).items():
                target = safe_join(root, name)
                target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                source = ensure_confined(
                    user_storage_root(self.store.papers_root, self.store.owner),
                    source,
                    must_exist=True,
                    require_file=True,
                )
                if source.stat().st_size > 32 * 1024**2:
                    raise ProcessingError("analysis_image_too_large", 413)
                with source.open("rb") as reader, target.open("xb") as writer:
                    copied = 0
                    while chunk := reader.read(1024**2):
                        copied += len(chunk)
                        if copied > 32 * 1024**2:
                            raise ProcessingError("analysis_image_too_large", 413)
                        writer.write(chunk)
                if name.startswith("images/") and Path(name).stem != file_digest(
                    target
                ):
                    raise ProcessingError("content_snapshot_invalid", 409)
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    size = path.stat().st_size
                    total += size
                    path.chmod(0o600)
                    with path.open("rb") as handle:
                        os.fsync(handle.fileno())
                    entries.append(
                        {
                            "path": path.relative_to(root).as_posix(),
                            "size": size,
                            "sha256": file_digest(path),
                        }
                    )
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            with self.store.connection(write=True) as db:
                self.store.check_quota(db, total, job_id=job_id)
                db.execute(
                    "INSERT INTO understanding_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row_id,
                        self.store.owner,
                        paper_id,
                        kind,
                        source_id,
                        digest,
                        encoded(config),
                        status,
                        encoded({"entries": entries}),
                        total,
                        now(),
                    ),
                )
                if head and status in {"completed", "partial"}:
                    current = db.execute(
                        "SELECT result_id FROM understanding_heads WHERE owner_id=? AND paper_id=? AND kind=?",
                        (self.store.owner, paper_id, kind),
                    ).fetchone()
                    if (current[0] if current else None) == previous:
                        db.execute(
                            "INSERT INTO understanding_heads VALUES(?,?,?,?) ON CONFLICT(owner_id,paper_id,kind) DO UPDATE SET result_id=excluded.result_id",
                            (self.store.owner, paper_id, kind, row_id),
                        )
            return row_id
        except sqlite3.IntegrityError:
            shutil.rmtree(root)
            with self.store.connection() as db:
                existing = db.execute(
                    "SELECT id FROM understanding_artifacts WHERE owner_id=? AND kind=? AND fingerprint=?",
                    (self.store.owner, kind, digest),
                ).fetchone()
                if existing:
                    return existing[0]
            raise
        except BaseException:
            shutil.rmtree(root)
            raise

    def current_hash(self, paper_id):
        try:
            return file_digest(self.pipeline.paper_file(paper_id))
        except (OSError, ValueError):
            return None

    def legacy(self, paper_id, *, analysis=False):
        with self.store.connection() as db:
            self.store.paper_exists(db, paper_id)
            row = db.execute(
                "SELECT file_path FROM papers WHERE id=? AND owner_id=?",
                (paper_id, self.store.owner),
            ).fetchone()
        # A missing PDF must not hide an existing analysis. Validate its stored
        # location without requiring the original file to still exist.
        root = user_storage_root(self.store.papers_root, self.store.owner)
        path = ensure_confined(root, row[0], must_exist=False)
        assets = paper_asset_paths(self.store.papers_root, path)
        candidates = [assets.analysis_result] if analysis else []
        if not analysis:
            outputs = path.parent / "outputs"
            if outputs.is_dir():
                for folder in sorted(outputs.iterdir(), key=lambda p: p.name):
                    if folder.name == path.stem or folder.name.startswith(
                        (path.stem + "_", path.stem + "-")
                    ):
                        directory = ensure_confined(
                            root, folder / "vlm", must_exist=False
                        )
                        if directory.is_dir():
                            candidates.extend(
                                sorted(
                                    p
                                    for p in directory.glob("*.md")
                                    if p.name != "result.md"
                                )
                            )
            # Canonical asset path has priority over historical alternate names.
            candidates.sort(
                key=lambda p: (p.parent != assets.analysis_directory / "vlm", p.name)
            )
        for candidate in candidates:
            try:
                candidate = ensure_confined(
                    root, candidate, must_exist=True, require_file=True
                )
            except (OSError, ValueError):
                continue
            if candidate.stat().st_size > MAX_CONTENT_BYTES:
                raise ProcessingError("content_size_limit", 413)
            with candidate.open("rb") as handle:
                raw = handle.read(MAX_CONTENT_BYTES + 1)
            if len(raw) > MAX_CONTENT_BYTES:
                raise ProcessingError("content_size_limit", 413)
            text = raw.decode("utf-8")
            if text.strip():
                return candidate, text
        return None, ""

    def _coverage(self, parsed, doc):
        config = json.loads(parsed["config_json"])
        parts = config.get("parts", [])
        if parsed["status"] != "completed" or config.get("internalPart") or not parts:
            return {
                "complete": False,
                "pages": [],
                "totalPages": doc["page_count"],
                "reason": "unverified_parse_coverage",
            }
        # 1.2 merged results name every validated part. Match these to the
        # completed split manifest, instead of inferring pages from text blocks.
        with self.store.connection() as db:
            split = db.execute(
                "SELECT * FROM processing_results WHERE owner_id=? AND document_id=? AND kind='document_parts' AND status='completed' ORDER BY created_at DESC LIMIT 1",
                (self.store.owner, doc["id"]),
            ).fetchone()
        try:
            if not split:
                raise ValueError()
            manifest = json.loads(split["manifest_json"])
            entry = next(e for e in manifest["entries"] if e["path"] == "result.json")
            path = safe_join(
                self.store.artifact_directory(split["id"]),
                entry["path"],
                must_exist=True,
                require_file=True,
            )
            if file_digest(path) != entry["sha256"]:
                raise ValueError()
            info = json.loads(path.read_text())
            if len(parts) != len(info["parts"]):
                raise ValueError()
            covered = []
            for index, part_id in enumerate(parts):
                part = self.store.result(part_id)
                pcfg = json.loads(part["config_json"])
                if (
                    part["document_id"] != doc["id"]
                    or part["status"] != "completed"
                    or pcfg.get("part") != index
                    or not pcfg.get("internalPart")
                ):
                    raise ValueError()
                descriptor = info["parts"][index]
                covered.extend(
                    range(descriptor["firstPage"], descriptor["lastPage"] + 1)
                )
            if covered != list(range(1, doc["page_count"] + 1)):
                raise ValueError()
            return {
                "complete": True,
                "pages": covered,
                "totalPages": doc["page_count"],
                "reason": "verified_merged_parts",
            }
        except (ValueError, KeyError, StopIteration, OSError):
            return {
                "complete": False,
                "pages": [],
                "totalPages": doc["page_count"],
                "reason": "unverified_parse_coverage",
            }

    def content(self, paper_id):
        sha = self.current_hash(paper_id)
        choices = []
        for row in self.store.results(paper_id):
            cfg = json.loads(row["config_json"])
            if (
                row["kind"] != "structure"
                or row["status"] not in {"completed", "partial"}
                or cfg.get("internalPart")
                or cfg.get("normalizer") != "mineru-content-list-v1/1"
            ):
                continue
            doc = self.store.document(row["document_id"])
            if doc["sha256"] != sha:
                continue
            coverage = self._coverage(row, doc)
            choices.append((row, doc, coverage))
        choices.sort(key=lambda x: not x[2]["complete"])
        if choices:
            row, doc, coverage = choices[0]
            units, images, count = [], {}, 0
            entries = {
                e["path"]: e
                for e in json.loads(row["manifest_json"]).get("entries", [])
            }
            after = -1
            while True:
                blocks = self.store.blocks(row["id"], after=after, limit=100)
                if not blocks:
                    break
                for block in blocks:
                    text = block.get("text") or ""
                    if block.get("table"):
                        text += "\n" + "\n".join(
                            " | ".join(c["text"] for c in cells)
                            for cells in block["table"]
                        )
                    if block.get("caption"):
                        text += "\n" + block["caption"]
                    image = block.get("image")
                    if image in entries and image.lower().endswith(
                        (".png", ".jpg", ".jpeg")
                    ):
                        name = (
                            "images/"
                            + entries[image]["sha256"]
                            + Path(image).suffix.lower()
                        )
                        images[name] = safe_join(
                            self.store.artifact_directory(row["id"]),
                            image,
                            must_exist=True,
                            require_file=True,
                        )
                    else:
                        name = None
                    for unit in text_units(
                        text
                        or ("[论文图片，未识别文字]" if name else "[非文本结构块]"),
                        prefix=f"u{count}_",
                    ):
                        unit.update(
                            blockId=block["id"],
                            type=block["type"],
                            source=block["source"],
                            image=name,
                        )
                        units.append(unit)
                    count += 1
                after = blocks[-1]["order"]
                if (
                    len(units) > MAX_UNITS
                    or sum(len(u["text"]) for u in units) > MAX_CONTENT_BYTES
                ):
                    raise ProcessingError("content_size_limit", 413)
            body = {
                "kind": "structure",
                "paperId": paper_id,
                "documentId": doc["id"],
                "sourceHash": sha,
                "parseId": row["id"],
                "coverage": coverage,
                "blockCount": count,
                "units": units,
            }
            return body, images
        path, text = self.legacy(paper_id)
        if path:
            images, missing = self.legacy_images(path, text)
            body = {
                "kind": "legacy_markdown",
                "paperId": paper_id,
                "documentId": None,
                "sourceHash": None,
                "parseId": None,
                "legacyHash": hashlib.sha256(text.encode()).hexdigest(),
                "coverage": {
                    "complete": False,
                    "pages": [],
                    "totalPages": None,
                    "reason": "legacy_coverage_unknown",
                },
                "units": list(text_units(text)),
                "missingImages": missing,
            }
            return body, images
        return {
            "kind": "missing",
            "paperId": paper_id,
            "units": [],
            "coverage": {
                "complete": False,
                "pages": [],
                "totalPages": None,
                "reason": "no_content",
            },
        }, {}

    def legacy_images(self, path, text):
        images, missing = {}, []
        from urllib.parse import urlparse, parse_qs, unquote

        for raw in re.findall(r"!\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", text):
            raw = unquote(raw)
            parsed = urlparse(raw)
            relative = (
                parse_qs(parsed.query).get("path", [""])[0]
                if raw.startswith("/api/paper/")
                else raw
            )
            try:
                if (
                    parsed.scheme
                    or raw.startswith("//")
                    or not relative.lower().endswith((".png", ".jpg", ".jpeg"))
                ):
                    raise ValueError()
                image = safe_join(
                    path.parent, relative, must_exist=True, require_file=True
                )
                images[raw] = image
            except (OSError, ValueError):
                missing.append(raw[:120])
        return images, missing

    def snapshot(self, paper_id, *, expected=None, allow_partial=False):
        body, images = self.content(paper_id)
        version = fingerprint(
            {k: v for k, v in body.items() if k != "units"}
            | {"textHash": fingerprint(body["units"])}
        )
        if expected and expected != version:
            raise ProcessingError("content_version_changed", 409)
        if not body["units"]:
            raise ProcessingError("paper_content_missing", 409)
        if not body["coverage"]["complete"] and not allow_partial:
            raise ProcessingError("content_coverage_confirmation_required", 409)
        if body["kind"] == "legacy_markdown":
            canonical = {}
            for raw, path in images.items():
                name = "images/" + file_digest(path) + path.suffix.lower()
                canonical[name] = path
                for unit in body["units"]:
                    unit["text"] = unit["text"].replace(
                        "](" + raw + ")", "](" + name + ")"
                    )
            images = canonical
        body["version"] = version
        sid = self.publish(
            paper_id, "content_snapshot", body, assets=images, key=version
        )
        return sid, self.body(sid)

    def status(self, paper_id):
        body, _ = self.content(paper_id)
        version = fingerprint(
            {k: v for k, v in body.items() if k != "units"}
            | {"textHash": fingerprint(body["units"])}
        )
        _, legacy = self.legacy(paper_id, analysis=True)
        return {
            k: body.get(k) for k in ("kind", "coverage", "parseId", "blockCount")
        } | {
            "version": version,
            "unitCount": len(body["units"]),
            "hasLegacyAnalysis": bool(legacy),
            "available": bool(body["units"]),
        }

    def evidence(self, snapshot_id, unit_id):
        row, body = self.row(snapshot_id), self.body(snapshot_id)
        unit = next((u for u in body["units"] if u["id"] == unit_id), None)
        if not unit:
            raise ProcessingError("source_not_found", 404)
        with self.store.connection(write=True) as db:
            old = db.execute(
                "SELECT id FROM understanding_evidence WHERE owner_id=? AND snapshot_id=? AND unit_id=?",
                (self.store.owner, snapshot_id, unit_id),
            ).fetchone()
            if old:
                return old[0]
            source_id = identifier()
            db.execute(
                "INSERT INTO understanding_evidence VALUES(?,?,?,?,?,?)",
                (
                    source_id,
                    self.store.owner,
                    row["paper_id"],
                    snapshot_id,
                    unit_id,
                    now(),
                ),
            )
        return source_id

    def resolve(self, source_id):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM understanding_evidence WHERE owner_id=? AND id=?",
                (self.store.owner, identifier(source_id)),
            ).fetchone()
        if not row:
            raise ProcessingError("source_not_found", 404)
        body = self.body(row["snapshot_id"])
        unit = next(u for u in body["units"] if u["id"] == row["unit_id"])
        stale = bool(
            body.get("sourceHash")
            and self.current_hash(row["paper_id"]) != body["sourceHash"]
        )
        source = unit["source"]
        return {
            "id": source_id,
            "paperId": row["paper_id"],
            "documentId": body.get("documentId"),
            "resultId": body.get("parseId"),
            "snapshotId": row["snapshot_id"],
            "unitId": unit["id"],
            "blockId": unit.get("blockId"),
            "revisionId": None,
            "text": unit["text"],
            "page": source.get("page"),
            "precision": "none" if stale else source.get("precision", "none"),
            "regions": [] if stale else source.get("regions", []),
            "stale": stale,
            "document": "original",
            "canNavigate": not stale and source.get("precision") in {"page", "region"},
            "canReadExcerpt": True,
        }
