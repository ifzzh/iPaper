"""Authenticated reading tools, all under the existing processing boundary."""

import os
from flask import jsonify, request
from itsdangerous import URLSafeTimedSerializer
from .common import ProcessingError
from .reading import ReadingTools
from .selection import SelectionTranslation


def attach_reading_routes(bp, service, body, public_job):
    codec = URLSafeTimedSerializer(os.urandom(32), salt="reading-search-v1")

    def reading():
        return ReadingTools(service.pipeline())

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
