"""Highlights, annotations and the per-paper main note.

Every record is owner-scoped, bound to a specific document/result revision and
revision-checked on write. Nothing here calls a model, the Worker, MinerU or OCR,
and no paper file is ever modified.
"""

from __future__ import annotations

import hashlib
import json
import re

from ipaper.security.paths import ensure_confined
from .common import ProcessingError, encoded, fingerprint, identifier, now
from .pipeline import file_digest

KINDS = {"highlight", "note", "page_note"}
# Invisible body markers keep insertions idempotent; they never reach an export.
NOTE_MARKER = re.compile(r"<!-- ipaper:(?:excerpt|answer):[^>]*? -->\n?")

CONTENT_LABELS = {
    "original": "原文 PDF",
    "babeldoc_dual": "版式译文（双语）",
    "babeldoc_mono": "版式译文",
}
# How a record's own content kind is shown, independent of the document kind.
CONTENT_KIND_LABELS = {
    "pdf_original": "原文 PDF",
    "pdf_translated": "版式译文",
    "structure_original": "结构原文",
    "structure_translated": "结构译文",
    "page": "页级记录",
}
COLORS = {"violet", "blue", "pink", "amber"}
ANCHOR_MODES = {"pdf", "structure", "page"}
MAX_EXCERPT = 8_000
MAX_COMMENT = 2_000
MAX_MARKDOWN = 262_144
MAX_ANNOTATIONS_PER_PAPER = 2_000
PAGE_LIMIT = 200


def _normalise(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _text(value, limit, code):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ProcessingError(code)
    value = value.strip("\n") if code == "invalid_note_markdown" else value.strip()
    if len(value) > limit:
        raise ProcessingError(code + "_too_large", 413)
    return value


class PaperNotes:
    """Reading annotations and the owner's main note for one paper."""

    def __init__(self, pipeline):
        self.pipeline, self.store = pipeline, pipeline.store
        self._digests, self._blocks = {}, {}

    # --- shared helpers ----------------------------------------------------

    def _paper(self, db, paper_id):
        self.store.paper_exists(db, paper_id)
        return paper_id

    def _document(self, paper_id, document_id):
        doc = self.store.document(document_id)
        if doc["paper_id"] != paper_id:
            raise ProcessingError("source_not_found", 404)
        return doc

    def _result(self, paper_id, result_id, document_id=None):
        result = self.store.result(result_id)
        if result["paper_id"] != paper_id:
            raise ProcessingError("source_not_found", 404)
        if document_id and result["document_id"] != document_id:
            # A result belongs to exactly one document revision; never mix them.
            raise ProcessingError("source_not_found", 404)
        return result

    def _document_path(self, doc):
        """The document's own controlled file, never the original standing in."""
        if doc["kind"] == "original":
            return self.pipeline.paper_file(doc["paper_id"])
        return ensure_confined(
            self.store.papers_root, doc["file_ref"], must_exist=True, require_file=True
        )

    def _digest(self, doc):
        key = (doc["id"], doc["sha256"])
        if key not in self._digests:
            self._digests[key] = file_digest(self._document_path(doc))
        return self._digests[key]

    def _block(self, result_id, block_id):
        key = (result_id, block_id)
        if key not in self._blocks:
            try:
                self._blocks[key] = self.store.block(result_id, block_id)
            except ProcessingError:
                self._blocks[key] = None
        return self._blocks[key]

    @staticmethod
    def _field_text(block, field, *, translated=False):
        """The exact text a structure anchor refers to, for the requested side."""
        content = (
            (block.get("translation") or {}).get("content")
            if translated
            else {k: block.get(k) for k in ("text", "caption", "table")}
        )
        if not isinstance(content, dict):
            return None
        if field in {"text", "caption"}:
            value = content.get(field)
            return value if isinstance(value, str) else None
        match = re.fullmatch(r"cell:(\d+):(\d+)", str(field))
        if not match:
            return None
        rows = content.get("table") or []
        r, c = int(match.group(1)), int(match.group(2))
        try:
            value = rows[r][c].get("text")
        except (IndexError, AttributeError, TypeError):
            return None
        return value if isinstance(value, str) else None

    def _source_state(self, row):
        """Whether the stored source still exists and still says the same thing."""
        anchor = json.loads(row["anchor_json"])
        context = json.loads(row["context_json"] or "{}")
        kind = context.get("contentKind") or self._content_kind(anchor, context)
        state = {
            "stale": False,
            "canNavigate": True,
            "notice": "",
            "contentKind": kind,
            "contentKindLabel": CONTENT_KIND_LABELS.get(kind, "阅读内容"),
            "contentLabel": CONTENT_KIND_LABELS.get(kind) or CONTENT_LABELS.get(context.get("documentKind"), "阅读内容"),
            "sourcePage": anchor.get("page"),
            "sourceBlock": anchor.get("blockId"),
            "sourceOrder": context.get("blockOrder"),
        }
        try:
            doc = self._document(row["paper_id"], row["document_id"])
            current = self._digest(doc)
        except (ProcessingError, OSError, ValueError):
            state.update(stale=True, canNavigate=False, notice="来源文件不可用。")
            return state
        state["documentKind"] = doc["kind"]
        state["documentSha"] = current
        if current != doc["sha256"] or doc["sha256"] != context.get("documentSha256"):
            # The file was replaced: the excerpt stays, but jumping to the old
            # position could point at different content, so say so instead.
            state.update(
                stale=True, canNavigate=False,
                notice="来源文件已变化，位置可能不再对应；不会自动跳到新内容。",
            )
            return state
        if not row["result_id"]:
            return state
        try:
            result = self._result(row["paper_id"], row["result_id"])
        except (ProcessingError, OSError, ValueError):
            state.update(stale=True, canNavigate=False, notice="结构来源不可用。")
            return state
        state["resultStatus"] = result["status"]
        state["resultRevision"] = result["updated_at"]
        if anchor.get("mode") != "structure":
            return state
        block = self._block(row["result_id"], anchor.get("blockId"))
        if block is None:
            state.update(stale=True, canNavigate=False, notice="该块已不在当前结构结果中，保留摘录但不定位。")
            return state
        state["sourceOrder"] = block.get("order")
        translated = anchor.get("translationRevision") is not None
        text = self._field_text(block, anchor.get("field"), translated=translated)
        if text is None:
            state.update(stale=True, canNavigate=False, notice="该字段在当前结果中不存在，保留摘录但不定位。")
            return state
        if translated:
            current_revision = (block.get("translation") or {}).get("revision")
            if not current_revision or str(current_revision) != str(anchor.get("translationRevision")):
                state.update(stale=True, canNavigate=False, notice="译文已更新，旧位置不再对应；保留摘录但不标记。")
                return state
        start, end = int(anchor.get("start") or 0), int(anchor.get("end") or 0)
        if end > len(text) or _normalise(text[start:end]) != _normalise(row["excerpt"]):
            state.update(stale=True, canNavigate=False, notice="来源文字已变化（重新解析或重译），保留摘录但不标记。")
            return state
        return state

    @staticmethod
    def _content_kind(anchor, context):
        if anchor.get("mode") == "structure":
            return "structure_translated" if anchor.get("translationRevision") is not None else "structure_original"
        if anchor.get("mode") == "page":
            return "page"
        kind = context.get("documentKind")
        return "pdf_translated" if kind in {"babeldoc_dual", "babeldoc_mono"} else "pdf_original"

    def _anchor(self, data, doc, result):
        anchor = data.get("anchor")
        if not isinstance(anchor, dict):
            raise ProcessingError("invalid_anchor")
        mode = anchor.get("mode")
        if mode not in ANCHOR_MODES:
            raise ProcessingError("invalid_anchor")
        context = data.get("context") or {}
        if not isinstance(context, dict):
            raise ProcessingError("invalid_anchor")
        clean = {
            "mode": mode,
            "before": _text(context.get("before"), 200, "invalid_anchor"),
            "after": _text(context.get("after"), 200, "invalid_anchor"),
        }
        if mode in {"pdf", "page"}:
            page = anchor.get("page")
            if type(page) is not int or not 1 <= page <= int(doc["page_count"]):
                raise ProcessingError("invalid_anchor")
            clean["page"] = page
            rects = anchor.get("rects") or []
            if mode == "pdf":
                if not isinstance(rects, list) or not 1 <= len(rects) <= 200:
                    raise ProcessingError("invalid_anchor")
                prepared = []
                for rect in rects:
                    if not isinstance(rect, dict) or set(rect) - {"x", "y", "w", "h", "page"}:
                        raise ProcessingError("invalid_anchor")
                    values = {}
                    for key in ("x", "y", "w", "h"):
                        value = rect.get(key)
                        if type(value) not in {int, float} or not 0 <= float(value) <= 1:
                            raise ProcessingError("invalid_anchor")
                        values[key] = round(float(value), 6)
                    rect_page = rect.get("page", page)
                    if type(rect_page) is not int or not 1 <= rect_page <= int(doc["page_count"]):
                        raise ProcessingError("invalid_anchor")
                    values["page"] = rect_page
                    prepared.append(values)
                clean["rects"] = prepared
            else:
                clean["rects"] = []
            return clean

        block_id = anchor.get("blockId")
        field = anchor.get("field")
        start, end = anchor.get("start"), anchor.get("end")
        if not isinstance(block_id, str) or not 0 < len(block_id) <= 200:
            raise ProcessingError("invalid_anchor")
        if field not in {"text", "caption"} and not re.fullmatch(r"cell:\d+:\d+", str(field or "")):
            raise ProcessingError("invalid_anchor")
        if type(start) is not int or type(end) is not int or start < 0 or end < start or end > 100_000:
            raise ProcessingError("invalid_anchor")
        if result is None:
            raise ProcessingError("invalid_anchor")
        revision = anchor.get("translationRevision")
        translated = revision is not None
        if translated and not (
            (isinstance(revision, str) and 0 < len(revision) <= 128) or (type(revision) is int and revision >= 0)
        ):
            # Modern translation revisions are opaque strings; integers stay
            # readable so 1.13.0 rows remain valid.
            raise ProcessingError("invalid_anchor")
        block = self._block(result["id"], block_id)
        if block is None:
            raise ProcessingError("invalid_anchor", details={"blockId": block_id})
        text = self._field_text(block, field, translated=translated)
        if text is None:
            raise ProcessingError("invalid_anchor", details={"field": field})
        if end > len(text):
            raise ProcessingError("invalid_anchor", details={"end": end, "length": len(text)})
        if translated:
            # The anchor must name the revision that is really attached to this
            # block of this result, not merely be a string of the right shape.
            current = (block.get("translation") or {}).get("revision")
            if not current or str(current) != str(revision):
                raise ProcessingError(
                    "invalid_anchor",
                    details={"field": "translationRevision", "current": current},
                )
        clean.update({
            "blockId": block_id,
            "field": field,
            "start": start,
            "end": end,
            "textLength": len(text),
            "textSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
        if translated:
            clean["translationRevision"] = revision
        return clean

    @staticmethod
    def _revision(value):
        if not isinstance(value, str) or not 0 < len(value) <= 128:
            raise ProcessingError("invalid_revision")
        return value

    def public_annotation(self, row):
        item = {
            "id": row["id"],
            "paperId": row["paper_id"],
            "documentId": row["document_id"],
            "resultId": row["result_id"],
            "kind": row["kind"],
            "color": row["color"],
            "excerpt": row["excerpt"],
            "comment": row["comment"],
            "anchor": json.loads(row["anchor_json"]),
            "context": json.loads(row["context_json"] or "{}"),
            "revision": row["revision"],
            "deleted": bool(row["deleted_at"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        state = self._source_state(row)
        item.update(state)
        # Reading order within the paper: document first, then page/block order.
        item["orderKey"] = [
            row["document_id"],
            0 if item.get("contentKind", "").endswith("original") or state.get("contentKind") == "page" else 1,
            state.get("sourceOrder") if state.get("sourceOrder") is not None else -1,
            item["anchor"].get("page") or 0,
            row["created_at"],
        ]
        item["excerpt"] = row["excerpt"]
        return item

    # --- annotations -------------------------------------------------------

    def annotations(self, paper_id, query):
        document_id = query.get("documentId")
        kind = query.get("kind")
        if kind is not None and kind not in KINDS:
            raise ProcessingError("invalid_annotation_kind")
        try:
            cursor = int(query.get("cursor", "0"))
            limit = int(query.get("limit", "50"))
        except (TypeError, ValueError):
            raise ProcessingError("invalid_cursor") from None
        if cursor < 0 or not 1 <= limit <= PAGE_LIMIT:
            raise ProcessingError("invalid_cursor")
        clauses = ["owner_id=?", "paper_id=?", "deleted_at IS NULL"]
        params = [self.store.owner, paper_id]
        if document_id:
            clauses.append("document_id=?")
            params.append(document_id)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        sql = (
            "SELECT * FROM reading_annotations WHERE " + " AND ".join(clauses)
            + " ORDER BY created_at, id LIMIT ? OFFSET ?"
        )
        with self.store.connection() as db:
            self._paper(db, paper_id)
            rows = [dict(row) for row in db.execute(sql, (*params, limit + 1, cursor))]
        page = rows[:limit]
        return {
            "annotations": [self.public_annotation(row) for row in page],
            # Opaque string cursor: clients must not depend on its numeric value.
            "nextCursor": str(cursor + limit) if len(rows) > limit else None,
            "total": self._count(paper_id),
        }

    def _count(self, paper_id):
        with self.store.connection() as db:
            return db.execute(
                "SELECT count(*) FROM reading_annotations WHERE owner_id=? AND paper_id=? AND deleted_at IS NULL",
                (self.store.owner, paper_id),
            ).fetchone()[0]

    def create_annotation(self, paper_id, data):
        excerpt = _text(data.get("excerpt"), MAX_EXCERPT, "invalid_excerpt")
        comment = _text(data.get("comment"), MAX_COMMENT, "invalid_comment")
        kind = data.get("kind") or ("note" if comment else "highlight")
        if kind not in KINDS:
            raise ProcessingError("invalid_annotation_kind")
        color = data.get("color") or "violet"
        if color not in COLORS:
            raise ProcessingError("invalid_color")
        document_id = data.get("documentId")
        if not isinstance(document_id, str) or not document_id:
            raise ProcessingError("invalid_document")
        result_id = data.get("resultId")
        if result_id is not None and not isinstance(result_id, str):
            raise ProcessingError("invalid_result")
        if kind in {"highlight", "note"} and not excerpt:
            raise ProcessingError("invalid_excerpt")
        with self.store.connection(write=True) as db:
            self._paper(db, paper_id)
            doc = self._document(paper_id, document_id)
            result = self._result(paper_id, result_id, document_id) if result_id else None
            existing = db.execute(
                "SELECT count(*) FROM reading_annotations WHERE owner_id=? AND paper_id=? AND deleted_at IS NULL",
                (self.store.owner, paper_id),
            ).fetchone()[0]
            if existing >= MAX_ANNOTATIONS_PER_PAPER:
                raise ProcessingError("annotation_limit_reached", 413)
            anchor = self._anchor(data, doc, result)
            block = (
                self._block(result["id"], anchor["blockId"])
                if result is not None and anchor.get("mode") == "structure"
                else None
            )
            context = {
                "documentSha256": doc["sha256"],
                "documentKind": doc["kind"],
                "documentLabel": CONTENT_LABELS.get(doc["kind"], "阅读内容"),
                "pageCount": doc["page_count"],
                "resultId": result["id"] if result else None,
                "resultKind": result["kind"] if result else None,
                "resultStatus": result["status"] if result else None,
                "resultRevision": result["updated_at"] if result else None,
                "translationRevision": anchor.get("translationRevision"),
                "blockOrder": block.get("order") if block else None,
                "textLength": anchor.get("textLength"),
                "textSha256": anchor.get("textSha256"),
                "savedAt": now(),
            }
            context["contentKind"] = self._content_kind(anchor, context)
            annotation_id = identifier()
            revision = fingerprint([annotation_id, excerpt, comment, anchor, color])
            timestamp = now()
            db.execute(
                "INSERT INTO reading_annotations (id,owner_id,paper_id,document_id,result_id,kind,color,"
                "excerpt,comment,anchor_json,context_json,revision,deleted_at,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
                (annotation_id, self.store.owner, paper_id, document_id, result_id, kind, color,
                 excerpt, comment, encoded(anchor), encoded(context), revision, timestamp, timestamp),
            )
            row = db.execute("SELECT * FROM reading_annotations WHERE id=?", (annotation_id,)).fetchone()
        return self.public_annotation(dict(row))

    def mutate_annotation(self, paper_id, annotation_id, data, *, delete=False, restore=False):
        revision = self._revision(data.get("revision"))
        with self.store.connection(write=True) as db:
            self._paper(db, paper_id)
            row = db.execute(
                "SELECT * FROM reading_annotations WHERE id=? AND owner_id=? AND paper_id=?",
                (annotation_id, self.store.owner, paper_id),
            ).fetchone()
            if row is None:
                raise ProcessingError("annotation_not_found", 404)
            row = dict(row)
            if row["revision"] != revision:
                raise ProcessingError("annotation_revision_conflict", 409)
            if delete:
                if row["deleted_at"]:
                    return self.public_annotation(row)
                timestamp = now()
                new_revision = fingerprint([row["revision"], "deleted"])
                db.execute(
                    "UPDATE reading_annotations SET deleted_at=?, revision=?, updated_at=? WHERE id=?",
                    (timestamp, new_revision, timestamp, annotation_id),
                )
            elif restore:
                if not row["deleted_at"]:
                    return self.public_annotation(row)
                timestamp = now()
                new_revision = fingerprint([row["revision"], "restored"])
                db.execute(
                    "UPDATE reading_annotations SET deleted_at=NULL, revision=?, updated_at=? WHERE id=?",
                    (new_revision, timestamp, annotation_id),
                )
            else:
                comment = (
                    _text(data["comment"], MAX_COMMENT, "invalid_comment")
                    if "comment" in data
                    else row["comment"]
                )
                color = data.get("color", row["color"])
                if color not in COLORS:
                    raise ProcessingError("invalid_color")
                excerpt = (
                    _text(data["excerpt"], MAX_EXCERPT, "invalid_excerpt")
                    if "excerpt" in data
                    else row["excerpt"]
                )
                timestamp = now()
                new_revision = fingerprint([row["revision"], comment, color, excerpt])
                db.execute(
                    "UPDATE reading_annotations SET comment=?, color=?, excerpt=?, revision=?, updated_at=? WHERE id=?",
                    (comment, color, excerpt, new_revision, timestamp, annotation_id),
                )
            updated = db.execute("SELECT * FROM reading_annotations WHERE id=?", (annotation_id,)).fetchone()
        return self.public_annotation(dict(updated))

    def annotation(self, paper_id, annotation_id):
        """One annotation by id, for linking note excerpts back to their source."""
        with self.store.connection() as db:
            self._paper(db, paper_id)
            row = db.execute(
                "SELECT * FROM reading_annotations WHERE id=? AND owner_id=? AND paper_id=?",
                (annotation_id, self.store.owner, paper_id),
            ).fetchone()
        if row is None:
            raise ProcessingError("annotation_not_found", 404)
        return self.public_annotation(dict(row))

    def deleted_annotations(self, paper_id, *, limit=PAGE_LIMIT):
        """Recently deleted records, so the UI can offer a clear undo."""
        with self.store.connection() as db:
            self._paper(db, paper_id)
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM reading_annotations WHERE owner_id=? AND paper_id=? AND deleted_at IS NOT NULL"
                    " ORDER BY deleted_at DESC LIMIT ?",
                    (self.store.owner, paper_id, limit),
                )
            ]
        return [self.public_annotation(row) for row in rows]

    # --- the main note -----------------------------------------------------

    def _note_row(self, db, paper_id):
        return db.execute(
            "SELECT * FROM reading_notes WHERE owner_id=? AND paper_id=?",
            (self.store.owner, paper_id),
        ).fetchone()

    def note(self, paper_id):
        with self.store.connection() as db:
            self._paper(db, paper_id)
            row = self._note_row(db, paper_id)
            entries = [
                dict(item)
                for item in db.execute(
                    "SELECT * FROM reading_note_entries WHERE owner_id=? AND paper_id=? ORDER BY created_at, id",
                    (self.store.owner, paper_id),
                )
            ]
            conflicts = [
                dict(item)
                for item in db.execute(
                    "SELECT * FROM reading_note_conflicts WHERE owner_id=? AND paper_id=? ORDER BY created_at DESC LIMIT 20",
                    (self.store.owner, paper_id),
                )
            ]
        return {
            "paperId": paper_id,
            "markdown": row["markdown"] if row else "",
            "revision": row["revision"] if row else None,
            "exists": row is not None,
            "createdAt": row["created_at"] if row else None,
            "updatedAt": row["updated_at"] if row else None,
            "entries": [self.public_entry(item) for item in entries],
            "conflicts": [self.public_conflict(item) for item in conflicts],
        }

    @staticmethod
    def public_entry(row):
        return {
            "id": row["id"],
            "kind": row["kind"],
            "annotationId": row["annotation_id"],
            "messageId": row["message_id"],
            "content": json.loads(row["content_json"]),
            "createdAt": row["created_at"],
        }

    @staticmethod
    def public_conflict(row):
        return {
            "id": row["id"],
            "markdown": row["markdown"],
            "baseRevision": row["base_revision"],
            "currentRevision": row["current_revision"],
            "createdAt": row["created_at"],
        }

    def save_note(self, paper_id, data):
        markdown = data.get("markdown")
        if not isinstance(markdown, str):
            raise ProcessingError("invalid_note_markdown")
        if len(markdown.encode("utf-8")) > MAX_MARKDOWN:
            raise ProcessingError("invalid_note_markdown_too_large", 413)
        base = data.get("revision")
        if base is not None and not isinstance(base, str):
            raise ProcessingError("invalid_revision")
        timestamp = now()
        with self.store.connection(write=True) as db:
            self._paper(db, paper_id)
            row = self._note_row(db, paper_id)
            current = row["revision"] if row else None
            if row is not None and markdown == row["markdown"]:
                # The client is re-confirming text the server already holds (a
                # duplicate or late retry): there is nothing to overwrite, so
                # answer with the current note instead of manufacturing a
                # conflict whose two sides are identical.
                return {
                    "paperId": paper_id,
                    "markdown": row["markdown"],
                    "revision": row["revision"],
                    "updatedAt": row["updated_at"],
                    "exists": True,
                    "duplicate": True,
                }
            if base != current:
                # Two writers diverged: commit the incoming draft on this same
                # connection *before* failing, so the request rolls back the note
                # save but the recoverable draft survives, and never silently
                # overwrite either side.
                draft = identifier()
                db.execute(
                    "INSERT INTO reading_note_conflicts (id,owner_id,paper_id,markdown,base_revision,current_revision,created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (draft, self.store.owner, paper_id, markdown,
                     base or "", current or "", timestamp),
                )
                db.commit()
                raise ProcessingError(
                    "note_revision_conflict", 409,
                    details={"conflictId": draft, "currentRevision": current},
                )
            revision = fingerprint([paper_id, markdown, timestamp])
            if row:
                db.execute(
                    "UPDATE reading_notes SET markdown=?, revision=?, updated_at=? WHERE owner_id=? AND paper_id=?",
                    (markdown, revision, timestamp, self.store.owner, paper_id),
                )
            else:
                db.execute(
                    "INSERT INTO reading_notes (owner_id,paper_id,markdown,revision,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (self.store.owner, paper_id, markdown, revision, timestamp, timestamp),
                )
            updated = self._note_row(db, paper_id)
        return {
            "paperId": paper_id,
            "markdown": updated["markdown"],
            "revision": updated["revision"],
            "updatedAt": updated["updated_at"],
            "exists": True,
        }

    def resolve_conflict(self, paper_id, conflict_id, data):
        """Apply one side of a conflict, bound to the revision the user saw.

        `revision` is the server revision displayed with the conflict. If the
        note moved on in the meantime, the decision is refused and the newer
        content is preserved as a conflict of its own instead of being erased.
        """
        choice = data.get("choice")
        if choice not in {"current", "draft"}:
            raise ProcessingError("invalid_conflict_choice")
        if "revision" not in data:
            # A decision without the revision it was made against cannot be
            # checked, and could overwrite text the user never saw.
            raise ProcessingError("invalid_revision")
        expected = data.get("revision")
        if expected is not None and not isinstance(expected, str):
            raise ProcessingError("invalid_revision")
        # The text the user actually confirmed in the editor. Without it the
        # stored conflict snapshot is used (1.13.x clients).
        confirmed = data.get("markdown")
        if confirmed is not None:
            if not isinstance(confirmed, str):
                raise ProcessingError("invalid_note_markdown")
            if len(confirmed.encode("utf-8")) > MAX_MARKDOWN:
                raise ProcessingError("invalid_note_markdown_too_large", 413)
        timestamp = now()
        with self.store.connection(write=True) as db:
            self._paper(db, paper_id)
            conflict = db.execute(
                "SELECT * FROM reading_note_conflicts WHERE id=? AND owner_id=? AND paper_id=?",
                (conflict_id, self.store.owner, paper_id),
            ).fetchone()
            if conflict is None:
                raise ProcessingError("conflict_not_found", 404)
            conflict = dict(conflict)
            row = self._note_row(db, paper_id)
            current_revision = row["revision"] if row else None
            # Strict comparison: null only matches a note that really has no
            # revision yet; it is never a wildcard that skips the check.
            if expected != current_revision:
                # The user decided on a version that is no longer current: keep
                # the newer text as a recoverable conflict and refuse the stale
                # decision rather than letting the old draft overwrite it.
                replacement = identifier()
                db.execute(
                    "INSERT INTO reading_note_conflicts (id,owner_id,paper_id,markdown,base_revision,current_revision,created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (replacement, self.store.owner, paper_id, (row["markdown"] if row else ""),
                     expected or "", current_revision or "", timestamp),
                )
                db.commit()
                raise ProcessingError(
                    "note_revision_conflict", 409,
                    details={"conflictId": conflict["id"], "currentRevision": current_revision,
                             "preservedConflictId": replacement, "reason": "stale_decision"},
                )
            if choice == "draft":
                wanted = confirmed if confirmed is not None else conflict["markdown"]
                if row and row["markdown"] != wanted:
                    # Keep the version being replaced, so choosing the draft can
                    # never be the only copy of the other side.
                    db.execute(
                        "INSERT INTO reading_note_conflicts (id,owner_id,paper_id,markdown,base_revision,current_revision,created_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (identifier(), self.store.owner, paper_id, row["markdown"],
                         conflict["base_revision"], current_revision or "", timestamp),
                    )
                revision = fingerprint([paper_id, wanted, timestamp])
                if row:
                    db.execute(
                        "UPDATE reading_notes SET markdown=?, revision=?, updated_at=? WHERE owner_id=? AND paper_id=?",
                        (wanted, revision, timestamp, self.store.owner, paper_id),
                    )
                else:
                    db.execute(
                        "INSERT INTO reading_notes (owner_id,paper_id,markdown,revision,created_at,updated_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (self.store.owner, paper_id, wanted, revision, timestamp, timestamp),
                    )
            # The applied conflict stays recorded (marked with how it was kept)
            # instead of being deleted; other drafts remain available.
            db.execute(
                "UPDATE reading_note_conflicts SET current_revision=? WHERE id=?",
                (f"resolved:{choice}:{current_revision or ''}", conflict["id"]),
            )
        return self.note(paper_id)

    def _append(self, db, paper_id, block, *, entry_kind, annotation_id, message_id,
                dedupe_key, content, marker):
        """Record provenance once per identity and keep the body consistent.

        The note body carries an invisible marker per inserted block, so
        "already inserted" is decided by that exact identity rather than by a
        substring of the user's prose (which also removes the duplicate-key
        storage error on repeated saves).
        """
        entry = db.execute(
            "SELECT * FROM reading_note_entries WHERE owner_id=? AND paper_id=? AND dedupe_key=?",
            (self.store.owner, paper_id, dedupe_key),
        ).fetchone()
        row = self._note_row(db, paper_id)
        markdown = row["markdown"] if row else ""
        timestamp = now()
        if marker in markdown:
            # Already in the body: nothing to add, keep the single provenance row.
            if entry is None:
                db.execute(
                    "INSERT INTO reading_note_entries (id,owner_id,paper_id,kind,annotation_id,message_id,"
                    "content_json,note_revision,dedupe_key,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (identifier(), self.store.owner, paper_id, entry_kind, annotation_id, message_id,
                     encoded(content), row["revision"] if row else "", dedupe_key, timestamp),
                )
                db.commit()
            return {
                "paperId": paper_id,
                "markdown": markdown,
                "revision": row["revision"] if row else None,
                "duplicate": True,
                "alreadyPresent": True,
            }
        body = f"{markdown.rstrip(chr(10))}\n\n{block.strip(chr(10))}\n{marker}\n" if markdown.strip() else f"{block.strip(chr(10))}\n{marker}\n"
        if len(body.encode("utf-8")) > MAX_MARKDOWN:
            raise ProcessingError("note_too_large", 413)
        revision = fingerprint([paper_id, body, timestamp])
        if row:
            db.execute(
                "UPDATE reading_notes SET markdown=?, revision=?, updated_at=? WHERE owner_id=? AND paper_id=?",
                (body, revision, timestamp, self.store.owner, paper_id),
            )
        else:
            db.execute(
                "INSERT INTO reading_notes (owner_id,paper_id,markdown,revision,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (self.store.owner, paper_id, body, revision, timestamp, timestamp),
            )
        if entry is None:
            db.execute(
                "INSERT INTO reading_note_entries (id,owner_id,paper_id,kind,annotation_id,message_id,"
                "content_json,note_revision,dedupe_key,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (identifier(), self.store.owner, paper_id, entry_kind, annotation_id, message_id,
                 encoded(content), revision, dedupe_key, timestamp),
            )
        else:
            db.execute(
                "UPDATE reading_note_entries SET note_revision=? WHERE id=?",
                (revision, entry["id"]),
            )
        return {
            "paperId": paper_id,
            "markdown": body,
            "revision": revision,
            "duplicate": entry is not None,
            "alreadyPresent": False,
        }

    def insert_excerpt(self, paper_id, data):
        """Insert one annotation's excerpt into the main note.

        Provenance is one row per (owner, paper, annotation). If the user later
        removed the text from the body, inserting again restores the body text
        without creating a second source record.
        """
        annotation_id = data.get("annotationId")
        if not isinstance(annotation_id, str) or not annotation_id:
            raise ProcessingError("invalid_annotation")
        base = data.get("revision")
        if base is not None and not isinstance(base, str):
            raise ProcessingError("invalid_revision")
        with self.store.connection(write=True) as db:
            self._paper(db, paper_id)
            row = db.execute(
                "SELECT * FROM reading_annotations WHERE id=? AND owner_id=? AND paper_id=? AND deleted_at IS NULL",
                (annotation_id, self.store.owner, paper_id),
            ).fetchone()
            if row is None:
                raise ProcessingError("annotation_not_found", 404)
            row = dict(row)
            current = self._note_row(db, paper_id)
            if base != (current["revision"] if current else None):
                raise ProcessingError(
                    "note_revision_conflict", 409,
                    details={"currentRevision": current["revision"] if current else None},
                )
            anchor = json.loads(row["anchor_json"])
            context = json.loads(row["context_json"] or "{}")
            source = self._source_label(anchor, context)
            quote = "\n".join(f"> {line}" for line in row["excerpt"].splitlines() or [""])
            block = (
                f"{quote}\n>\n> 来源：{source}"
                + (f"\n>\n> 批注：{row['comment']}" if row["comment"] else "")
            )
            return self._append(
                db, paper_id, block,
                entry_kind="excerpt", annotation_id=annotation_id, message_id=None,
                dedupe_key=f"excerpt:{annotation_id}",
                content={"excerpt": row["excerpt"], "comment": row["comment"],
                         "anchor": anchor, "context": context, "label": source},
                marker=f"<!-- ipaper:excerpt:{annotation_id} -->",
            )

    def _source_label(self, anchor, context=None):
        context = context or {}
        label = CONTENT_LABELS.get(context.get("documentKind"), "阅读内容")
        if context.get("contentKind") == "structure_translated":
            label = "结构译文"
        elif context.get("contentKind") == "structure_original":
            label = "结构原文"
        if anchor.get("mode") == "structure":
            where = f"块 {anchor.get('blockId')}"
            if anchor.get("translationRevision") is not None:
                where += f" · 译文修订 {str(anchor['translationRevision'])[:8]}"
            return f"{label} · {where}"
        if anchor.get("mode") == "page":
            return f"{label} · 第 {anchor.get('page')} 页（页级记录）"
        return f"{label} · 第 {anchor.get('page')} 页"

    def save_answer(self, paper_id, data):
        """Copy an already-persisted assistant answer into the note.

        Sources are validated against this owner and this paper only.

        Only existing text is copied: nothing is re-asked, and a streaming or
        failed turn is refused instead of being stored half-finished. Repeated
        clicks and retries are decided by the stored (owner, paper, session,
        message) identity, never by a substring of the note body.
        """
        session_id = data.get("sessionId")
        message_key = data.get("messageKey")
        index = data.get("messageIndex")
        if not isinstance(session_id, str) or not session_id:
            raise ProcessingError("invalid_answer_reference")
        self._paper_id = paper_id
        with self.store.connection(write=True) as db:
            self._paper(db, paper_id)
            chat = db.execute(
                "SELECT * FROM chats WHERE session_id=? AND owner_id=? AND paper_id=?",
                (session_id, self.store.owner, paper_id),
            ).fetchone()
            turn = None
            if isinstance(message_key, str) and message_key:
                turn = db.execute(
                    "SELECT * FROM understanding_chat_turns WHERE session_id=? AND owner_id=?"
                    " AND paper_id=? AND message_key=?",
                    (session_id, self.store.owner, paper_id, message_key),
                ).fetchone()
            if chat is None and turn is None:
                raise ProcessingError("answer_not_found", 404)

            text, key = None, message_key
            if turn is not None:
                turn = dict(turn)
                if turn["status"] != "completed" or not (turn.get("answer") or "").strip():
                    # Streaming, failed or empty: not a saved answer yet.
                    raise ProcessingError("answer_not_persisted", 409)
                text, key = turn["answer"], turn["message_key"]
            else:
                history = json.loads(chat["history"] or "[]")
                if type(index) is not int or not 0 <= index < len(history):
                    raise ProcessingError("answer_not_found", 404)
                entry = history[index]
                if not isinstance(entry, dict) or entry.get("role") != "assistant":
                    raise ProcessingError("answer_not_assistant", 400)
                text = entry.get("content")
                key = message_key or str(entry.get("timestamp") or index)
            if not isinstance(text, str) or not text.strip():
                raise ProcessingError("answer_not_persisted", 409)

            # Sources recorded with the message are copied as-is; no model call.
            sources = self._answer_sources(db, session_id, key)
            labels = [item["label"] + (f"（{item['where']}）" if item.get("where") else "") for item in sources]
            quote = "\n".join(f"> {line}" for line in text.strip().splitlines())
            source_line = "来源：" + ("、".join(labels) if labels else "该回答未记录可回访来源")
            marker = f"<!-- ipaper:answer:{hashlib.sha256(f'{session_id}:{key}'.encode()).hexdigest()[:16]} -->"
            block = f"### 已保存的 AI 回答\n\n{quote}\n>\n> {source_line}"
            return self._append(
                db, paper_id, block,
                entry_kind="answer", annotation_id=None, message_id=key,
                dedupe_key=f"answer:{session_id}:{key}",
                content={"text": text, "sources": sources, "sessionId": session_id,
                         "messageKey": key, "paperId": paper_id},
                marker=marker,
            )

    def _answer_sources(self, db, session_id, message_key):
        """Full, revisit-capable source identities recorded with the answer."""
        row = db.execute(
            "SELECT sources_json FROM processing_chat_sources WHERE owner_id=? AND session_id=? AND message_key=?",
            (self.store.owner, session_id, message_key),
        ).fetchone()
        if row is None:
            return []
        try:
            mapping = json.loads(row["sources_json"])
        except ValueError:
            return []
        if not isinstance(mapping, dict):
            return []
        sources = []
        for label, source_id in list(mapping.items())[:16]:
            source = {"label": str(label), "sourceId": str(source_id), "where": "",
                      "page": None, "blockId": None}
            # Resolve the recorded id to the real page/block, strictly within the
            # owner's own artifacts for this paper; otherwise keep the identity.
            record = db.execute(
                "SELECT * FROM understanding_artifacts WHERE id=? AND owner_id=? AND paper_id=?",
                (str(source_id), self.store.owner, self._paper_id),
            ).fetchone()
            if record is not None:
                record = dict(record)
                try:
                    payload = json.loads(record.get("manifest_json") or "{}")
                except ValueError:
                    payload = {}
                if isinstance(payload, dict):
                    page = payload.get("page")
                    block = payload.get("blockId") or payload.get("block_id")
                    if isinstance(page, int):
                        source["page"] = page
                    if isinstance(block, str) and 0 < len(block) <= 200:
                        source["blockId"] = block
            where = []
            if source["blockId"]:
                where.append(f"块 {source['blockId']}")
            if source["page"]:
                where.append(f"第 {source['page']} 页")
            source["where"] = " · ".join(where)
            sources.append(source)
        return sources

    # --- Markdown export ---------------------------------------------------

    def export_markdown(self, paper_id, *, include_annotations=True):
        with self.store.connection() as db:
            self._paper(db, paper_id)
            paper = db.execute(
                "SELECT * FROM papers WHERE id=? AND owner_id=?", (paper_id, self.store.owner)
            ).fetchone()
            if paper is None:
                raise ProcessingError("paper_not_found", 404)
            paper = dict(paper)
            note = self._note_row(db, paper_id)
            entries = [
                dict(item) for item in db.execute(
                    "SELECT * FROM reading_note_entries WHERE owner_id=? AND paper_id=? ORDER BY created_at",
                    (self.store.owner, paper_id),
                )
            ]
            annotations = [
                dict(item) for item in db.execute(
                    "SELECT * FROM reading_annotations WHERE owner_id=? AND paper_id=? AND deleted_at IS NULL ORDER BY created_at",
                    (self.store.owner, paper_id),
                )
            ]
        lines = [f"# {paper.get('title') or '论文笔记'}", ""]
        authors = paper.get("authors")
        if authors:
            lines += [f"- 作者：{authors}", ""]
        identity = []
        if paper.get("arxiv_id"):
            identity.append(f"arXiv:{paper['arxiv_id']}")
        metadata = {}
        try:
            metadata = json.loads(paper.get("metadata") or "{}")
        except (ValueError, TypeError):
            metadata = {}
        doi = metadata.get("doi") if isinstance(metadata, dict) else None
        if doi:
            identity.append(f"DOI:{doi}")
        if paper.get("published_date"):
            identity.append(str(paper["published_date"]))
        if identity:
            lines += [f"- 标识：{' · '.join(identity)}", ""]
        lines += [f"- 导出时间：{now()}", ""]
        body = NOTE_MARKER.sub("", note["markdown"] if note else "").strip()
        lines += ["## 笔记", "", body if body else "_（还没有笔记内容）_", ""]
        if include_annotations and annotations:
            lines += ["## 摘录与批注", ""]
            for row in annotations:
                anchor = json.loads(row["anchor_json"])
                context = json.loads(row["context_json"] or "{}")
                lines.append(f"- {self._source_label(anchor, context)}：{row['excerpt']}")
                if row["comment"]:
                    lines.append(f"  - 批注：{row['comment']}")
            lines.append("")
        if entries:
            lines += ["## 来源记录", ""]
            for entry in entries:
                content = json.loads(entry["content_json"])
                label = "摘录" if entry["kind"] == "excerpt" else "已保存的 AI 回答"
                anchor = content.get("anchor") or {}
                where = content.get("label") or (self._source_label(anchor, content.get("context") or {}) if anchor else "见笔记正文引用")
                lines.append(f"- {label} · {where}")
                for source in content.get("sources") or []:
                    suffix = f" · {source['where']}" if source.get("where") else ""
                    lines.append(f"  - 来源 {source['label']}（{source['sourceId']}）{suffix}")
            lines.append("")
        text = "\n".join(lines).rstrip() + "\n"
        # Never leak absolute storage paths or session material.
        text = re.sub(r"/data/papers[^\s)]*", "(本地文件)", text)
        return text, (paper.get("title") or "paper"), (note["revision"] if note else None)