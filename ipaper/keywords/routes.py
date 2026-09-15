import json
import sqlite3
from flask import Blueprint, jsonify, request
from ipaper.security.identity import current_user_id
from ipaper.core.base_paper import Paper
from .common import KeywordError
from .query import members, selection, selected_ids


def register_keyword_routes(app, service):
    bp = Blueprint("keywords", __name__)
    app.extensions["keywords"] = service

    @bp.before_request
    def limits():
        request.max_content_length = 256 * 1024

    @bp.after_request
    def private(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.errorhandler(KeywordError)
    def known(e):
        return jsonify(error=e.code), e.status

    @bp.errorhandler(sqlite3.Error)
    def db_failure(e):
        return jsonify(error="keyword_storage_failed"), 503

    from ipaper.processing.common import ProcessingError

    @bp.errorhandler(ProcessingError)
    def processing_error(e):
        return jsonify(error=e.code), e.status

    def body(allowed):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) - set(allowed):
            raise KeywordError("invalid_keyword_request")
        return data

    def integer(value, default=0):
        try:
            return max(0, int(value))
        except (ValueError, TypeError):
            raise KeywordError("invalid_library_query") from None

    def ids(data, db=None):
        store = service.store()
        if db is not None:
            return selected_ids(db, store.owner, data)
        with store.connection() as conn:
            return selected_ids(conn, store.owner, data)

    def public_paper(p):
        obj = Paper.from_dict(p).to_dict()
        # Public projection: never expose server file paths or free-form extra.
        public = {
            k: obj.get(k)
            for k in (
                "id",
                "title",
                "authors",
                "year",
                "abstract",
                "starred",
                "has_chinese_version",
                "translation_status",
                "analysis_status",
                "arxiv_published_date",
                "arxiv_url",
                "github",
                "homepage",
                "notes",
                "affiliation",
                "journal",
                "has_analysis_result",
                "category_id",
            )
        }
        return public

    @bp.get("/api/library/index")
    def library_index():
        # Existing shell tab titles/details need a metadata index. No directory
        # walk, PDF inspection or content generation is performed by this read.
        store = service.store()
        with store.connection() as db:
            return jsonify([public_paper(p) for p in members(db, store.owner, {})])

    @bp.get("/api/library/navigation")
    def navigation():
        from ipaper.tools.basic_tools.category_manager import get_categories
        from ipaper.security.paths import category_storage_id
        from pathlib import PurePath

        tree = get_categories("")
        store = service.store()
        with store.connection() as db:
            papers = members(db, store.owner, {})

        def count(node):
            ids = {node["id"]}
            for child in node.get("children", []):
                ids.update(count(child))
            folders = {category_storage_id(i) for i in ids}
            node["pdf_count"] = sum(
                p.get("category_id") in ids
                or PurePath(p["file_path"]).parent.name in folders
                for p in papers
            )
            return ids

        count(tree)
        tree["pdf_count"] = len(papers)
        return jsonify(tree)

    @bp.get("/api/library/papers")
    def library():
        try:
            query = json.loads(request.args.get("filter", "{}"))
        except (ValueError, RecursionError):
            raise KeywordError("invalid_library_query") from None
        page = max(1, integer(request.args.get("page", "1")))
        limit = min(100, max(1, integer(request.args.get("limit", "50"))))
        store = service.store()
        with store.connection() as db:
            rows = members(db, store.owner, query)
            chosen = rows[(page - 1) * limit : page * limit]
            output = []
            for p in chosen:
                public = public_paper(p)
                public["tags"] = [
                    {"id": r[0], "name": r[1]}
                    for r in db.execute(
                        "SELECT t.id,t.name FROM keyword_links l JOIN keyword_tags t ON t.id=l.tag_id AND t.owner_id=l.owner_id WHERE l.owner_id=? AND l.paper_id=? AND t.status='active' ORDER BY l.manual DESC,t.name,t.id",
                        (store.owner, p["id"]),
                    )
                ]
                output.append(public)
        return jsonify(items=output, total=len(rows), page=page, limit=limit)

    @bp.post("/api/library/selections")
    def select():
        data = body({"selection"})
        store = service.store()
        with store.connection(True) as db:
            return jsonify(selection(db, store.owner, data.get("selection", {})))

    @bp.get("/api/tags")
    def tags():
        return jsonify(
            redirects=service.store().redirects(),
            tags=service.store().tags(
                request.args.get("query", "")[:200], request.args.get("deleted") == "1"
            ),
        )

    @bp.get("/api/tags/operations")
    def operations():
        return jsonify(operations=service.store().operations())

    @bp.post("/api/tags/operations/<operation_id>/undo")
    def undo(operation_id):
        body(set())
        return jsonify(service.store().undo(operation_id))

    @bp.patch("/api/tags/<tag_id>")
    def edit_tag(tag_id):
        return jsonify(
            service.store().edit_tag(
                tag_id,
                body(
                    {
                        "action",
                        "revision",
                        "name",
                        "aliases",
                        "targetId",
                        "targetRevision",
                    }
                ),
            )
        )

    @bp.get("/api/paper/<paper_id>/tags")
    def paper_tags(paper_id):
        return jsonify(service.store().get(paper_id))

    @bp.patch("/api/paper/<paper_id>/tags")
    def edit_paper(paper_id):
        data = body({"action", "revision", "name", "tagId"})
        if type(data.get("revision")) is not int:
            raise KeywordError("tag_revision_conflict", 409)
        store = service.store()
        result = store.edit_papers(
            [paper_id],
            data.get("action"),
            name=data.get("name"),
            tag_id=data.get("tagId"),
            revisions={paper_id: data["revision"]},
        )
        return jsonify(**store.get(paper_id), **result)

    @bp.post("/api/tags/batch")
    def batch_edit():
        data = body({"paperIds", "selectionId", "action", "name", "tagId", "revisions"})
        if not isinstance(data.get("revisions"), dict):
            raise KeywordError("tag_revision_conflict", 409)
        return jsonify(
            service.store().edit_papers(
                ids(data),
                data.get("action"),
                name=data.get("name"),
                tag_id=data.get("tagId"),
                revisions=data["revisions"],
            )
        )

    @bp.route("/api/keywords/settings", methods=["GET", "PUT"])
    def settings():
        return jsonify(
            service.store().settings(
                body({"automatic"}) if request.method == "PUT" else None
            )
        )

    @bp.post("/api/keywords/preview")
    def preview():
        data = body({"paperIds", "selectionId", "selection", "method", "inputScope"})
        method = data.get("method", "local")
        if method not in {"local", "model"}:
            raise KeywordError("invalid_keyword_method")
        paper_ids = ids(data)
        if method == "local":
            store = service.store()
            with store.connection() as db:
                revisions = {
                    pid: (
                        db.execute(
                            "SELECT revision FROM keyword_papers WHERE owner_id=? AND paper_id=?",
                            (store.owner, pid),
                        ).fetchone()
                        or [1]
                    )[0]
                    for pid in paper_ids
                }
            return jsonify(
                count=len(paper_ids),
                paperIds=paper_ids,
                revisions=revisions,
                method=method,
                requests=0,
                mineruRequests=0,
            )
        return jsonify(
            service.prepare(
                current_user_id(),
                paper_ids,
                method,
                data.get("inputScope", "available"),
            )
        )

    @bp.post("/api/keywords/jobs")
    def create():
        data = body(
            {
                "paperIds",
                "selectionId",
                "selection",
                "method",
                "previewKey",
                "inputScope",
            }
        )
        bid = service.create(
            current_user_id(),
            ids(data),
            data.get("method", "local"),
            data.get("previewKey"),
            input_scope=data.get("inputScope", "available"),
        )
        return jsonify(id=bid), 202

    @bp.get("/api/keywords/jobs")
    def jobs():
        return jsonify(jobs=service.store().batches())

    @bp.get("/api/keywords/jobs/<job_id>")
    def job(job_id):
        return jsonify(
            service.store().batch(
                job_id,
                integer(request.args.get("after", "0")),
                integer(request.args.get("limit", "50")),
            )
        )

    @bp.post("/api/keywords/jobs/<job_id>/cancel")
    def cancel(job_id):
        body(set())
        result = service.store().cancel(job_id)
        service.wake.set()
        return jsonify(result)

    @bp.post("/api/keywords/jobs/<job_id>/retry")
    def retry(job_id):
        data = body({"previewKey", "inputScope"})
        store = service.store()
        prior = store.batch(job_id, limit=0)
        if prior["method"] == "model":
            with store.connection() as db:
                paper_ids = [
                    r[0]
                    for r in db.execute(
                        "SELECT paper_id FROM keyword_items WHERE owner_id=? AND batch_id=? AND status IN ('failed','stale','interrupted','cancelled')",
                        (store.owner, job_id),
                    )
                ]
            bid = service.create(
                store.owner,
                paper_ids,
                "model",
                data.get("previewKey"),
                data.get("inputScope", "available"),
            )
        else:
            bid = store.retry(job_id)
        service.wake.set()
        return jsonify(id=bid), 202

    app.register_blueprint(bp)
