"""Highlights, annotations and the per-paper main note.

Every record is owner-scoped, bound to a specific document/result revision and
revision-checked on write. Nothing here calls a model, the Worker, MinerU or OCR,
and no paper file is ever modified.
"""

from __future__ import annotations

import json
import re

from .common import ProcessingError, encoded, fingerprint, identifier, now
from .pipeline import file_digest

KINDS = {"highlight", "note", "page_note"}
COLORS = {"violet", "blue", "pink", "amber"}
ANCHOR_MODES = {"pdf", "structure", "page"}
MAX_EXCERPT = 8_000
MAX_COMMENT = 2_000
MAX_MARKDOWN = 262_144
MAX_ANNOTATIONS_PER_PAPER = 2_000
PAGE_LIMIT = 200


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

    # --- shared helpers ----------------------------------------------------

    def _paper(self, db, paper_id):
        self.store.paper_exists(db, paper_id)
        return paper_id

    def _document(self, paper_id, document_id):
        doc = self.store.document(document_id)
        if doc["paper_id"] != paper_id:
            raise ProcessingError("source_not_found", 404)
        return doc

    def _result(self, paper_id, result_id):
        result = self.store.result(result_id)
        if result["paper_id"] != paper_id:
            raise ProcessingError("source_not_found", 404)
        return result

    def _source_state(self, paper_id, document_id, result_id):
        """Current version of the bound source and whether it is still readable."""
        state = {"stale": False, "canNavigate": True, "notice": ""}
        try:
            doc = self._document(paper_id, document_id)
            path = self.pipeline.paper_file(paper_id)
            current = file_digest(path)
            state["documentKind"] = doc["kind"]
            state["documentSha"] = current
            if current != doc["sha256"]:
                # The file was replaced: the excerpt stays, but jumping to the old
                # position could point at different content, so say so instead.
                state.update(
                    stale=True, canNavigate=False,
                    notice="源文件已变化，位置可能不再对应；不会自动跳到新内容。",
                )
        except (ProcessingError, OSError, ValueError):
            state.update(stale=True, canNavigate=False, notice="来源文件不可用。")
        if result_id:
            try:
                result = self._result(paper_id, result_id)
                state["resultStatus"] = result["status"]
                state["resultRevision"] = result["updated_at"]
                if result["status"] != "completed":
                    state.update(stale=True, notice="该结构结果已不再有效。")
            except (ProcessingError, OSError, ValueError):
                state.update(stale=True, canNavigate=False, notice="结构来源不可用。")
        return state

    @staticmethod
    def _anchor(paper_id, data, doc, result):
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
        else:
            block_id = anchor.get("blockId")
            field = anchor.get("field")
            start, end = anchor.get("start"), anchor.get("end")
            if not isinstance(block_id, str) or not 0 < len(block_id) <= 200:
                raise ProcessingError("invalid_anchor")
            if field not in {"text", "caption"} and not re.fullmatch(r"cell:\d+:\d+", str(field or "")):
                raise ProcessingError("invalid_anchor")
            if type(start) is not int or type(end) is not int or start < 0 or end < start or end > 100_000:
                raise ProcessingError("invalid_anchor")
            clean.update({"blockId": block_id, "field": field, "start": start, "end": end})
            if anchor.get("translationRevision") is not None:
                revision = anchor["translationRevision"]
                if type(revision) is not int or revision < 0:
                    raise ProcessingError("invalid_anchor")
                clean["translationRevision"] = revision
            if result is None:
                raise ProcessingError("invalid_anchor")
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
        item.update(self._source_state(row["paper_id"], row["document_id"], row["result_id"]))
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
            "nextCursor": cursor + limit if len(rows) > limit else None,
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
            result = self._result(paper_id, result_id) if result_id else None
            existing = db.execute(
                "SELECT count(*) FROM reading_annotations WHERE owner_id=? AND paper_id=? AND deleted_at IS NULL",
                (self.store.owner, paper_id),
            ).fetchone()[0]
            if existing >= MAX_ANNOTATIONS_PER_PAPER:
                raise ProcessingError("annotation_limit_reached", 413)
            anchor = self._anchor(paper_id, data, doc, result)
            context = {
                "documentSha256": doc["sha256"],
                "documentKind": doc["kind"],
                "pageCount": doc["page_count"],
                "resultRevision": result["updated_at"] if result else None,
                "savedAt": now(),
            }
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
        """Keep one side explicitly: `choice` is `current` or `draft`."""
        choice = data.get("choice")
        if choice not in {"current", "draft"}:
            raise ProcessingError("invalid_conflict_choice")
        with self.store.connection(write=True) as db:
            self._paper(db, paper_id)
            conflict = db.execute(
                "SELECT * FROM reading_note_conflicts WHERE id=? AND owner_id=? AND paper_id=?",
                (conflict_id, self.store.owner, paper_id),
            ).fetchone()
            if conflict is None:
                raise ProcessingError("conflict_not_found", 404)
            if choice == "draft":
                timestamp = now()
                revision = fingerprint([paper_id, conflict["markdown"], timestamp])
                if self._note_row(db, paper_id):
                    db.execute(
                        "UPDATE reading_notes SET markdown=?, revision=?, updated_at=? WHERE owner_id=? AND paper_id=?",
                        (conflict["markdown"], revision, timestamp, self.store.owner, paper_id),
                    )
                else:
                    db.execute(
                        "INSERT INTO reading_notes (owner_id,paper_id,markdown,revision,created_at,updated_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (self.store.owner, paper_id, conflict["markdown"], revision, timestamp, timestamp),
                    )
            db.execute("DELETE FROM reading_note_conflicts WHERE id=?", (conflict_id,))
        return self.note(paper_id)

    # --- inserting excerpts and saved answers ------------------------------

    def _append(self, db, paper_id, block, *, entry_kind, annotation_id, message_id, dedupe_key, content):
        """Append one Markdown block atomically, so concurrent inserts are not lost."""
        row = self._note_row(db, paper_id)
        markdown = (row["markdown"] if row else "").rstrip("\n")
        addition = block.strip("\n")
        timestamp = now()
        if addition in markdown.split("\n\n"):
            # Already present: report success without inserting a second copy.
            return {"paperId": paper_id, "markdown": row["markdown"], "revision": row["revision"],
                    "duplicate": True}
        combined = f"{markdown}\n\n{addition}\n" if markdown else f"{addition}\n"
        if len(combined.encode("utf-8")) > MAX_MARKDOWN:
            raise ProcessingError("note_too_large", 413)
        revision = fingerprint([paper_id, combined, timestamp])
        if row:
            db.execute(
                "UPDATE reading_notes SET markdown=?, revision=?, updated_at=? WHERE owner_id=? AND paper_id=?",
                (combined, revision, timestamp, self.store.owner, paper_id),
            )
        else:
            db.execute(
                "INSERT INTO reading_notes (owner_id,paper_id,markdown,revision,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (self.store.owner, paper_id, combined, revision, timestamp, timestamp),
            )
        db.execute(
            "INSERT INTO reading_note_entries (id,owner_id,paper_id,kind,annotation_id,message_id,"
            "content_json,note_revision,dedupe_key,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (identifier(), self.store.owner, paper_id, entry_kind, annotation_id, message_id,
             encoded(content), revision, dedupe_key, timestamp),
        )
        return {"paperId": paper_id, "markdown": combined, "revision": revision, "duplicate": False}

    def insert_excerpt(self, paper_id, data):
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
            source = self._source_label(anchor)
            quote = "\n".join(f"> {line}" for line in row["excerpt"].splitlines() or [""])
            block = f"{quote}\n>\n> 来源：{source}" + (f"\n>\n> 批注：{row['comment']}" if row["comment"] else "")
            return self._append(
                db, paper_id, block,
                entry_kind="excerpt", annotation_id=annotation_id, message_id=None,
                dedupe_key=f"excerpt:{annotation_id}", content={"excerpt": row["excerpt"], "comment": row["comment"], "anchor": anchor},
            )

    def _source_label(self, anchor):
        if anchor.get("mode") == "structure":
            return f"结构内容 · 块 {anchor.get('blockId')}"
        if anchor.get("mode") == "pdf":
            return f"第 {anchor.get('page')} 页"
        return f"第 {anchor.get('page')} 页（页级记录）"

    def save_answer(self, paper_id, data):
        """Copy an already-persisted assistant answer into the note.

        Only existing text is copied: nothing is re-asked, and a streaming or
        failed turn is refused instead of being stored half-finished.
        """
        session_id = data.get("sessionId")
        message_key = data.get("messageKey")
        index = data.get("messageIndex")
        if not isinstance(session_id, str) or not session_id:
            raise ProcessingError("invalid_answer_reference")
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
            quote = "\n".join(f"> {line}" for line in text.strip().splitlines())
            source_line = "、".join(sources) if sources else "来源：该回答未记录可回访来源"
            block = f"### 已保存的 AI 回答\n\n{quote}\n>\n> {source_line}"
            current = self._note_row(db, paper_id)
            if current and text.strip() in current["markdown"]:
                return {"paperId": paper_id, "markdown": current["markdown"],
                        "revision": current["revision"], "duplicate": True}
            result = self._append(
                db, paper_id, block,
                entry_kind="answer", annotation_id=None, message_id=key,
                dedupe_key=f"answer:{session_id}:{key}",
                content={"text": text, "sources": sources, "sessionId": session_id, "messageKey": key},
            )
        return result

    def _answer_sources(self, db, session_id, message_key):
        """Human-readable, revisit-capable source labels recorded with the answer."""
        labels = []
        row = db.execute(
            "SELECT sources_json FROM processing_chat_sources WHERE owner_id=? AND session_id=? AND message_key=?",
            (self.store.owner, session_id, message_key),
        ).fetchone()
        if row is None:
            return labels
        try:
            mapping = json.loads(row["sources_json"])
        except ValueError:
            return labels
        for label, source_id in list(mapping.items())[:8]:
            labels.append(f"{label}（{str(source_id)[:8]}）")
        return labels

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
        body = (note["markdown"] if note else "").strip()
        lines += ["## 笔记", "", body if body else "_（还没有笔记内容）_", ""]
        if include_annotations and annotations:
            lines += ["## 摘录与批注", ""]
            for row in annotations:
                anchor = json.loads(row["anchor_json"])
                lines.append(f"- {self._source_label(anchor)}：{row['excerpt']}")
                if row["comment"]:
                    lines.append(f"  - 批注：{row['comment']}")
            lines.append("")
        if entries:
            lines += ["## 来源记录", ""]
            for entry in entries:
                content = json.loads(entry["content_json"])
                label = "摘录" if entry["kind"] == "excerpt" else "已保存的 AI 回答"
                anchor = content.get("anchor") or {}
                where = self._source_label(anchor) if anchor else "（见笔记正文引用）"
                lines.append(f"- {label} · {where}")
            lines.append("")
        text = "\n".join(lines).rstrip() + "\n"
        # Never leak absolute storage paths or session material.
        text = re.sub(r"/data/papers[^\s)]*", "(本地文件)", text)
        return text, (paper.get("title") or "paper"), (note["revision"] if note else None)