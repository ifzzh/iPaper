"""Owner/version-bound navigation and bounded full-result text search. No AI."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata

from ipaper.security.paths import ensure_confined, paper_asset_paths
from .common import ProcessingError, encoded, fingerprint, identifier, now
from .pipeline import file_digest, _PREFLIGHT_LOCK, _PREFLIGHT_OWNERS


def normalized(text, case_sensitive=False):
    # Match presentation-independent whitespace/ligatures; never fuzzy-match.
    output, mapping, space = [], [], False
    for index, char in enumerate(text):
        if char == "\u00ad":
            continue
        value = unicodedata.normalize("NFKC", char)
        if not case_sensitive:
            value = value.casefold()
        for part in value:
            if part.isspace():
                if space:
                    continue
                part, space = " ", True
            else:
                space = False
            output.append(part)
            mapping.append(index)
    return "".join(output), mapping


def fields(content):
    for name in ("text", "caption"):
        if content.get(name):
            yield name, content[name]
    for r, row in enumerate(content.get("table") or []):
        for c, cell in enumerate(row):
            if cell.get("text"):
                yield f"cell:{r}:{c}", cell["text"]


class ReadingTools:
    def __init__(self, pipeline):
        self.pipeline, self.store = pipeline, pipeline.store
        self._checked = set()

    def document(self, paper_id, data):
        """Register only missing document geometry, using existing offline Worker."""
        if data.get("documentId"):
            doc = self.store.document(data["documentId"])
            if doc["paper_id"] != paper_id:
                raise ProcessingError("source_not_found", 404)
            self.check_document(doc)
            return self.public_document(doc)
        variant = data.get("document", "original")
        if variant not in {"original", "translated"}:
            raise ProcessingError("invalid_document_kind")
        source, kind = self.pipeline.paper_file(paper_id), "original"
        if variant == "translated":
            assets = paper_asset_paths(self.store.papers_root, source)
            source = (
                assets.chinese_dual
                if assets.chinese_dual.exists()
                else assets.chinese_mono
            )
            kind = "babeldoc_dual" if source == assets.chinese_dual else "babeldoc_mono"
        try:
            sha = file_digest(source)
        except OSError:
            raise ProcessingError("source_file_unavailable", 404) from None
        with self.store.connection() as db:
            known = db.execute(
                "SELECT * FROM processing_documents WHERE owner_id=? AND paper_id=? AND kind=? AND sha256=?",
                (self.store.owner, paper_id, kind, sha),
            ).fetchone()
        if known:
            doc_id = self.store.register_document(
                paper_id,
                kind=kind,
                sha256=sha,
                size=source.stat().st_size,
                geometry=json.loads(known["geometry_json"]),
                file_ref=str(source.relative_to(self.store.papers_root)),
            )
        else:
            with _PREFLIGHT_LOCK:
                if self.store.owner in _PREFLIGHT_OWNERS or len(_PREFLIGHT_OWNERS) >= 2:
                    raise ProcessingError("document_preflight_busy", 429)
                _PREFLIGHT_OWNERS.add(self.store.owner)
            try:
                doc_id = self.pipeline.inspect_document(paper_id, source, kind)
            finally:
                with _PREFLIGHT_LOCK:
                    _PREFLIGHT_OWNERS.discard(self.store.owner)
        return self.public_document(self.store.document(doc_id))

    @staticmethod
    def public_document(doc):
        return {
            "id": doc["id"],
            "paperId": doc["paper_id"],
            "kind": doc["kind"],
            "sha256": doc["sha256"],
            "pageCount": doc["page_count"],
        }

    def check_document(self, doc):
        if doc["id"] in self._checked:
            return
        with self.store.connection() as db:
            self.store.paper_exists(db, doc["paper_id"])
        try:
            path = (
                self.pipeline.paper_file(doc["paper_id"])
                if doc["kind"] == "original"
                else ensure_confined(
                    self.store.papers_root,
                    doc["file_ref"],
                    must_exist=True,
                    require_file=True,
                )
            )
            if file_digest(path) != doc["sha256"]:
                raise ProcessingError("source_expired", 409)
        except (OSError, ValueError):
            raise ProcessingError("source_expired", 409) from None
        self._checked.add(doc["id"])

    def result(self, result_id):
        result = self.store.result(result_id)
        if result["kind"] not in {"structure", "structured_translation"} or json.loads(
            result["config_json"]
        ).get("internalPart"):
            raise ProcessingError("reading_result_unavailable", 404)
        parse = self.store.result(result["parse_id"] or result_id)
        return result, parse

    def revision(self, result_id):
        result, parse = self.result(result_id)
        digest = hashlib.sha256(
            encoded([result_id, parse["id"], parse["manifest_json"]]).encode()
        )
        translated = 0
        with self.store.connection() as db:
            for row in db.execute(
                "SELECT block_id,active_revision,status,generation FROM processing_block_translations WHERE result_id=? ORDER BY block_id",
                (result_id,),
            ):
                digest.update(encoded(list(row)).encode())
                translated += bool(row[1])
        return digest.hexdigest(), translated, parse["block_count"]

    def navigation(self, result_id, *, after=-1, page=None):
        result, parse = self.result(result_id)
        if type(after) is not int or after < -1:
            raise ProcessingError("invalid_cursor")
        doc = self.store.document(result["document_id"])
        stale = False
        try:
            self.check_document(doc)
        except ProcessingError:
            stale = True
        if page is not None:
            if type(page) is not int or not 1 <= page <= doc["page_count"]:
                raise ProcessingError("invalid_reading_position")
            with self.store.connection() as db:
                indices = [
                    row[0]
                    for row in db.execute(
                        """SELECT ordinal FROM processing_blocks
                    WHERE result_id=? AND ordinal>? AND (json_extract(source_json,'$.page')=? OR
                    EXISTS(SELECT 1 FROM json_each(source_json,'$.regions') WHERE json_extract(value,'$.page')=?))
                    ORDER BY ordinal LIMIT 100""",
                        (parse["id"], after, page, page),
                    )
                ]
            blocks = [
                self.store.blocks(result_id, after=index - 1, limit=1)[0]
                for index in indices
            ]
        else:
            blocks = self.store.blocks(result_id, after=after, limit=100)
        items = []
        for block in blocks:
            if page is None and not (
                block.get("level") is not None or block["type"] in {"title", "heading"}
            ):
                continue
            source = block["source"]
            items.append(
                {
                    "blockId": block["id"],
                    "order": block["order"],
                    "type": block["type"],
                    "level": block.get("level"),
                    "title": (block.get("text") or block.get("caption") or "")[:240],
                    "page": source.get("page"),
                    "precision": "none" if stale else source.get("precision", "none"),
                    "regions": [] if stale else source.get("regions", []),
                    "translation": {
                        k: (block.get("translation") or {}).get(k)
                        for k in ("status", "revision", "error")
                    },
                }
            )
        return {
            "items": items,
            "nextCursor": blocks[-1]["order"] if len(blocks) == 100 else None,
            "documentId": doc["id"],
            "sourceHash": doc["sha256"],
            "stale": stale,
            "targetLanguage": result["target_language"],
            "totalBlocks": parse["block_count"],
        }

    def search(self, result_id, data, codec):
        query = data.get("query")
        scope, case = data.get("scope", "original"), data.get("caseSensitive", False)
        if (
            not isinstance(query, str)
            or not 1 <= len(query.strip()) <= 256
            or scope not in {"original", "translated", "both"}
            or any(0xD800 <= ord(c) <= 0xDFFF for c in query)
            or type(case) is not bool
        ):
            raise ProcessingError("invalid_reading_search")
        needle = normalized(query, case)[0].strip()
        if not needle:
            raise ProcessingError("invalid_reading_search")
        revision, translated, total = self.revision(result_id)
        binding = fingerprint(
            [self.store.owner, result_id, query, scope, case, revision]
        )
        ordinal, field_index, offset = 0, 0, 0
        if data.get("cursor"):
            try:
                cursor = codec.loads(data["cursor"], max_age=3600)
                if cursor["binding"] != binding:
                    raise ValueError()
                ordinal, field_index, offset = cursor["position"]
            except Exception:
                raise ProcessingError("reading_search_expired", 409) from None
        blocks = self.store.blocks(result_id, after=ordinal - 1, limit=40)
        matches, next_position = [], None
        for block in blocks:
            values = []
            if scope in {"original", "both"}:
                values.extend(("original", name, text) for name, text in fields(block))
            translation = (block.get("translation") or {}).get("content")
            if translation and scope in {"translated", "both"}:
                values.extend(
                    ("translated", name, text) for name, text in fields(translation)
                )
            for fi, (display, name, text) in enumerate(values):
                if block["order"] == ordinal and fi < field_index:
                    continue
                haystack, mapping = normalized(text, case)
                start = offset if block["order"] == ordinal and fi == field_index else 0
                while (at := haystack.find(needle, start)) >= 0:
                    end = at + len(needle)
                    lo, hi = mapping[at], mapping[end - 1] + 1
                    matches.append(
                        {
                            "blockId": block["id"],
                            "order": block["order"],
                            "field": name,
                            "display": display,
                            "start": lo,
                            "end": hi,
                            "page": block["source"].get("page"),
                            "before": text[max(0, lo - 50) : lo],
                            "match": text[lo:hi],
                            "after": text[hi : hi + 70],
                        }
                    )
                    start = end
                    if len(matches) == 100:
                        next_position = [block["order"], fi, start]
                        break
                if next_position:
                    break
            if next_position:
                break
        if next_position is None and len(blocks) == 40:
            next_position = [blocks[-1]["order"] + 1, 0, 0]
        if self.revision(result_id)[0] != revision:
            raise ProcessingError("reading_search_expired", 409)
        return {
            "matches": matches,
            "cursor": (
                codec.dumps({"binding": binding, "position": next_position})
                if next_position
                else None
            ),
            "scannedBlocks": min(total, next_position[0]) if next_position else total,
            "totalBlocks": total,
            "translatedBlocks": translated,
            "revision": revision,
            "complete": next_position is None,
        }

    def target(self, paper_id, document_id, result_id, location):
        doc = self.store.document(document_id)
        if doc["paper_id"] != paper_id or not isinstance(location, dict):
            raise ProcessingError("source_not_found", 404)
        offset = location.get("offset", 0)
        if (
            type(offset) not in (int, float)
            or not math.isfinite(offset)
            or not 0 <= offset <= 1
        ):
            raise ProcessingError("invalid_reading_position")
        value = {"offset": offset}
        if result_id:
            result, _ = self.result(result_id)
            if result["document_id"] != document_id:
                raise ProcessingError("source_version_mismatch", 409)
            block = self.store.block(result_id, location.get("blockId"))
            display = location.get("display", "original")
            if display not in {"original", "translated", "bilingual"}:
                raise ProcessingError("invalid_reading_position")
            value.update(
                blockId=block["id"],
                display=display,
                translationRevision=(block.get("translation") or {}).get("revision"),
            )
        else:
            page = location.get("page")
            if type(page) is not int or not 1 <= page <= doc["page_count"]:
                raise ProcessingError("invalid_reading_position")
            value["page"] = page
            self.check_document(doc)
        return doc, value

    def bookmark_public(self, row):
        item = {
            "id": row["id"],
            "name": row["name"],
            "documentId": row["document_id"],
            "resultId": row["result_id"],
            "location": json.loads(row["location_json"]),
            "revision": row["revision"],
            "createdAt": row["created_at"],
            "stale": False,
            "canNavigate": True,
            "notice": "",
        }
        try:
            doc, current = self.target(
                row["paper_id"], row["document_id"], row["result_id"], item["location"]
            )
            item["documentKind"] = doc["kind"]
            if row["result_id"]:
                try:
                    self.check_document(doc)
                except ProcessingError:
                    item.update(
                        stale=True, notice="源文件已变化，将打开保留的结构版本。"
                    )
                if current.get("translationRevision") != item["location"].get(
                    "translationRevision"
                ):
                    item["location"]["offset"] = 0
                    item["notice"] = "译文已更新，将定位到该块开头。"
        except (ProcessingError, OSError, ValueError):
            item.update(
                stale=True,
                canNavigate=False,
                notice="书签对应的文件或内容版本已不可用。",
            )
        return item

    def bookmarks(self, paper_id):
        with self.store.connection() as db:
            self.store.paper_exists(db, paper_id)
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM reading_bookmarks WHERE owner_id=? AND paper_id=? ORDER BY created_at",
                    (self.store.owner, paper_id),
                )
            ]
        return [self.bookmark_public(row) for row in rows]

    @staticmethod
    def name(value):
        if (
            not isinstance(value, str)
            or not 0 < len(value.strip()) <= 120
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
        ):
            raise ProcessingError("invalid_bookmark_name")
        return value.strip()

    def add_bookmark(self, paper_id, data):
        doc, location = self.target(
            paper_id, data.get("documentId"), data.get("resultId"), data.get("location")
        )
        name = self.name(data.get("name"))
        dedupe = fingerprint(
            [
                doc["id"],
                data.get("resultId"),
                {k: v for k, v in location.items() if k != "translationRevision"},
            ]
        )
        with self.store.connection(write=True) as db:
            previous = db.execute(
                "SELECT * FROM reading_bookmarks WHERE owner_id=? AND paper_id=? AND dedupe_key=?",
                (self.store.owner, paper_id, dedupe),
            ).fetchone()
            if previous:
                return dict(previous)
            counts = db.execute(
                "SELECT count(*),coalesce(sum(paper_id=?),0) FROM reading_bookmarks WHERE owner_id=?",
                (paper_id, self.store.owner),
            ).fetchone()
            if counts[0] >= 5000 or counts[1] >= 200:
                raise ProcessingError("bookmark_limit", 413)
            stamp, row_id = now(), identifier()
            db.execute(
                "INSERT INTO reading_bookmarks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row_id,
                    self.store.owner,
                    paper_id,
                    doc["id"],
                    data.get("resultId"),
                    name,
                    encoded(location),
                    dedupe,
                    identifier(),
                    stamp,
                    stamp,
                ),
            )
            return dict(
                db.execute(
                    "SELECT * FROM reading_bookmarks WHERE id=?", (row_id,)
                ).fetchone()
            )

    def mutate_bookmark(self, paper_id, row_id, data, *, delete=False):
        with self.store.connection(write=True) as db:
            self.store.paper_exists(db, paper_id)
            row = db.execute(
                "SELECT * FROM reading_bookmarks WHERE id=? AND owner_id=? AND paper_id=?",
                (identifier(row_id), self.store.owner, paper_id),
            ).fetchone()
            if not row:
                raise ProcessingError("bookmark_not_found", 404)
            if data.get("revision") != row["revision"]:
                raise ProcessingError("bookmark_changed", 409)
            if delete:
                db.execute("DELETE FROM reading_bookmarks WHERE id=?", (row_id,))
                return None
            db.execute(
                "UPDATE reading_bookmarks SET name=?,revision=?,updated_at=? WHERE id=?",
                (self.name(data.get("name")), identifier(), now(), row_id),
            )
            return dict(
                db.execute(
                    "SELECT * FROM reading_bookmarks WHERE id=?", (row_id,)
                ).fetchone()
            )
