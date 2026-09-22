"""Authenticated reading tools, all under the existing processing boundary."""

import os
import re
from urllib.parse import quote
from flask import Response, jsonify, request
from itsdangerous import URLSafeTimedSerializer
from .common import ProcessingError
from .reading import ReadingTools
from .notes import MAX_MARKDOWN, PaperNotes
from .selection import SelectionTranslation


def attach_reading_routes(bp, service, body, public_job):
    codec = URLSafeTimedSerializer(os.urandom(32), salt="reading-search-v1")

    def reading():
        return ReadingTools(service.pipeline())

    def notes():
        return PaperNotes(service.pipeline())

    def note_body(allowed):
        # A main note can be up to MAX_MARKDOWN bytes; the processing blueprint's
        # default body cap is smaller, so raise it for exactly these routes.
        request.max_content_length = max(request.max_content_length or 0, MAX_MARKDOWN + 8192)
        return body(allowed)

    @bp.get("/api/paper/<paper_id>/reading/annotations")
    def annotations(paper_id):
        return jsonify(notes().annotations(paper_id, request.args))

    @bp.post("/api/paper/<paper_id>/reading/annotations")
    def create_annotation(paper_id):
        data = body({"kind", "color", "excerpt", "comment", "documentId", "resultId", "anchor", "context"})
        return jsonify(annotation=notes().create_annotation(paper_id, data)), 201

    @bp.get("/api/paper/<paper_id>/reading/annotations/deleted")
    def deleted_annotations(paper_id):
        return jsonify(annotations=notes().deleted_annotations(paper_id))

    @bp.get("/api/paper/<paper_id>/reading/annotations/<annotation_id>")
    def reading_annotation(paper_id, annotation_id):
        return jsonify(annotation=notes().annotation(paper_id, annotation_id))

    @bp.route("/api/paper/<paper_id>/reading/annotations/<annotation_id>", methods=["PUT", "DELETE"])
    def mutate_annotation(paper_id, annotation_id):
        data = body({"revision", "comment", "color", "excerpt", "restore"})
        annotation = notes().mutate_annotation(
            paper_id, annotation_id, data,
            delete=request.method == "DELETE", restore=bool(data.get("restore")),
        )
        return jsonify(annotation=annotation)

    @bp.get("/api/paper/<paper_id>/reading/note")
    def reading_note(paper_id):
        return jsonify(note=notes().note(paper_id))

    @bp.put("/api/paper/<paper_id>/reading/note")
    def save_reading_note(paper_id):
        data = note_body({"markdown", "revision"})
        return jsonify(note=notes().save_note(paper_id, data))

    @bp.post("/api/paper/<paper_id>/reading/note/excerpts")
    def insert_note_excerpt(paper_id):
        data = note_body({"annotationId", "revision"})
        tools = notes()
        result = tools.insert_excerpt(paper_id, data)
        return jsonify(note={**tools.note(paper_id), "duplicate": result.get("duplicate", False)})

    @bp.post("/api/paper/<paper_id>/reading/note/answers")
    def save_note_answer(paper_id):
        data = body({"sessionId", "messageKey", "messageIndex"})
        tools = notes()
        result = tools.save_answer(paper_id, data)
        return jsonify(note={**tools.note(paper_id), "duplicate": result.get("duplicate", False)})

    @bp.post("/api/paper/<paper_id>/reading/note/conflicts/<conflict_id>")
    def resolve_note_conflict(paper_id, conflict_id):
        data = body({"choice", "revision", "markdown"})
        return jsonify(note=notes().resolve_conflict(paper_id, conflict_id, data))

    @bp.get("/api/paper/<paper_id>/reading/note/export.md")
    def export_reading_note(paper_id):
        include = request.args.get("annotations", "1") != "0"
        text, title, _revision = notes().export_markdown(paper_id, include_annotations=include)
        # ASCII-safe filename plus RFC 5987 UTF-8 form; never put raw non-Latin-1
        # bytes in a plain header value.
        safe = re.sub(r"[^A-Za-z0-9.-]+", "-", title).strip("-")[:60] or "notes"
        encoded = quote(f"note-{title[:60] or 'notes'}.md", safe="")
        return Response(
            text,
            mimetype="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=note-{safe}.md; filename*=UTF-8''{encoded}",
                "Cache-Control": "private, no-store",
                "Content-Length": str(len(text.encode("utf-8"))),
            },
        )

    @bp.post("/api/paper/<paper_id>/reading-document")
    def document(paper_id):
        return jsonify(
            document=reading().document(paper_id, body({"document", "documentId"}))
        )

    @bp.post("/api/results/<result_id>/search")
    def search(result_id):
        return jsonify(
            reading().search(
                result_id, body({"query", "scope", "caseSensitive", "cursor"}), codec
            )
        )

    @bp.get("/api/results/<result_id>/navigation")
    def navigation(result_id):
        try:
            after = int(request.args.get("after", "-1"))
            page = int(request.args["page"]) if "page" in request.args else None
        except ValueError:
            raise ProcessingError("invalid_cursor") from None
        return jsonify(reading().navigation(result_id, after=after, page=page))

    @bp.route("/api/paper/<paper_id>/bookmarks", methods=["GET", "POST"])
    def bookmarks(paper_id):
        tools = reading()
        if request.method == "GET":
            return jsonify(bookmarks=tools.bookmarks(paper_id))
        row = tools.add_bookmark(
            paper_id, body({"documentId", "resultId", "name", "location"})
        )
        return jsonify(bookmark=tools.bookmark_public(row))

    @bp.route(
        "/api/paper/<paper_id>/bookmarks/<bookmark_id>", methods=["PUT", "DELETE"]
    )
    def bookmark(paper_id, bookmark_id):
        data = body({"revision", "name"})
        tools = reading()
        row = tools.mutate_bookmark(
            paper_id, bookmark_id, data, delete=request.method == "DELETE"
        )
        return jsonify(bookmark=tools.bookmark_public(row) if row else None)

    @bp.post("/api/paper/<paper_id>/selection-translation/preview")
    def selection_preview(paper_id):
        data = body(
            {
                "text",
                "sourceLanguage",
                "targetLanguage",
                "documentId",
                "resultId",
                "sourceId",
                "budget",
            }
        )
        return jsonify(
            SelectionTranslation(service.pipeline()).prepare(paper_id, data)[1]
        )

    @bp.post("/api/paper/<paper_id>/selection-translation")
    def selection_create(paper_id):
        data = body(
            {
                "text",
                "sourceLanguage",
                "targetLanguage",
                "documentId",
                "resultId",
                "sourceId",
                "budget",
                "retryJobId",
            }
        )
        value = SelectionTranslation(service.pipeline()).create(paper_id, data)
        if value.get("job"):
            value["job"] = public_job(value["job"])
            service.wake.set()
        return jsonify(value)

    @bp.get("/api/selection-translations/<translation_id>")
    def selection_result(translation_id):
        return jsonify(
            translation=SelectionTranslation(service.pipeline()).read(translation_id)
        )
