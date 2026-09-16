import sqlite3
from flask import Blueprint, jsonify, request
from ipaper.keywords.common import KeywordError
from ipaper.keywords.query import members, selected_ids
from .store import TopicError, nodes, member_ids
from .service import TERMINAL


def register_topic_routes(app, service):
    bp = Blueprint("topics", __name__)
    app.extensions["topics"] = service

    @bp.before_request
    def bounded():
        request.max_content_length = 1024 * 1024

    @bp.after_request
    def private(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.errorhandler(TopicError)
    @bp.errorhandler(KeywordError)
    def known(error):
        return jsonify(error=error.code), error.status

    @bp.errorhandler(sqlite3.Error)
    def storage(error):
        return jsonify(error="topic_storage_failed"), 503

    def body(allowed):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) - set(allowed):
            raise TopicError("invalid_topic_request")
        return data

    def selected(data):
        store = service.store()
        with store.connection() as db:
            return selected_ids(db, store.owner, data)

    @bp.route("/api/topics/settings", methods=["GET", "PUT"])
    def settings():
        owner = service.store().owner
        return jsonify(
            service.settings(
                owner, body({"automatic"}) if request.method == "PUT" else None
            )
        )

    @bp.get("/api/topics")
    def catalog():
        store = service.store()
        with store.connection() as db:
            tree = nodes(db, store.owner)
            valid = {p["id"] for p in members(db, store.owner, {})}
            public = []
            organized = set()
            for node in tree.values():
                if node["status"] != "active":
                    continue
                ids = member_ids(db, store.owner, [node["id"]]) & valid
                organized.update(ids)
                public.append({**node, "count": len(ids)})
            legacy_mapping = {
                r[0]: r[1]
                for r in db.execute(
                    "SELECT category_id,topic_id FROM topic_legacy_map WHERE owner_id=?",
                    (store.owner,),
                )
            }
            operations = [
                dict(r)
                for r in db.execute(
                    "SELECT id,kind,created_at,undone FROM topic_operations WHERE owner_id=? ORDER BY created_at DESC LIMIT 30",
                    (store.owner,),
                )
            ]
        return jsonify(
            deleted=[n for n in tree.values() if n["status"] == "deleted"],
            topics=public,
            legacyMapping=legacy_mapping,
            unorganized=len(valid - organized),
            total=len(valid),
            operations=operations,
            redirects={
                n["id"]: n["merged_into"] if n["status"] == "merged" else None
                for n in tree.values()
                if n["status"] != "active"
            },
        )

    @bp.post("/api/topics")
    def create():
        data = body({"name", "parentId", "definitionId"})
        from .definitions import BY_ID

        if data.get("definitionId") is not None and data["definitionId"] not in BY_ID:
            raise TopicError("invalid_topic_definition")
        return (
            jsonify(
                service.store().create(
                    data.get("name"), data.get("parentId"), data.get("definitionId")
                )
            ),
            201,
        )

    @bp.patch("/api/topics/<topic_id>")
    def edit(topic_id):
        data = body(
            {
                "action",
                "revision",
                "name",
                "parentId",
                "targetId",
                "targetRevision",
                "definitionId",
                "position",
            }
        )
        store = service.store()
        action = data.get("action")
        if action == "rename":
            store.rename(topic_id, data.get("name"), data.get("revision"))
        elif action == "reparent":
            store.reparent(topic_id, data.get("parentId"), data.get("revision"))
        elif action == "merge":
            store.merge(
                topic_id,
                data.get("targetId"),
                data.get("revision"),
                data.get("targetRevision"),
            )
        elif action == "bind":
            from .store import bind_definition

            bind_definition(
                store, topic_id, data.get("definitionId"), data.get("revision")
            )
        elif action == "restore":
            store.restore(topic_id, data.get("revision"))
        elif action == "order":
            store.order(topic_id, data.get("position"), data.get("revision"))
        elif action == "delete":
            store.delete(topic_id, data.get("revision"))
        else:
            raise TopicError("invalid_topic_action")
        return jsonify(success=True)

    @bp.post("/api/topics/operations/<operation_id>/undo")
    def undo(operation_id):
        body(set())
        service.store().undo(operation_id)
        return jsonify(success=True)

    @bp.route("/api/paper/<paper_id>/topics", methods=["GET", "PATCH"])
    def paper_topics(paper_id):
        store = service.store()
        if request.method == "GET":
            return jsonify(store.get(paper_id))
        data = body({"action", "revision", "topicId"})
        return jsonify(
            store.edit_paper(
                paper_id, data.get("action"), data.get("revision"), data.get("topicId")
            )
        )

    @bp.get("/api/topics/<topic_id>/export")
    def export(topic_id):
        import io
        from flask import send_file
        from ipaper.metadata.store import MetadataStore
        from ipaper.metadata.model import bibtex, arxiv, MetadataError

        store = service.store()
        with store.connection() as db:
            papers = members(db, store.owner, {"topicIds": [topic_id]})
        if len(papers) > 5000:
            raise TopicError("topic_export_limit", 413)
        metadata = MetadataStore(store.db_path, store.owner)
        output = []
        mode = request.args.get("format", "bibtex")
        if mode not in {"bibtex", "arxiv"}:
            raise TopicError("invalid_topic_request")
        for paper in papers:
            fields = metadata.get(paper["id"])["fields"]
            if mode == "bibtex":
                output.append(bibtex(paper["id"], fields))
            elif fields.get("arxiv_id"):
                try:
                    base, version = arxiv(fields["arxiv_id"])
                    version = fields.get("arxiv_version") or version
                    if base:
                        output.append(
                            "https://arxiv.org/abs/"
                            + base
                            + ("v" + str(version) if version else "")
                        )
                except MetadataError:
                    pass
        text = "\n\n".join(dict.fromkeys(output))
        if len(text.encode()) > 32 * 1024 * 1024:
            raise TopicError("topic_export_limit", 413)
        if mode == "arxiv":
            return jsonify(text=text, count=len(set(output)))
        return send_file(
            io.BytesIO(text.encode()),
            mimetype="application/x-bibtex",
            as_attachment=True,
            download_name="iPaper-研究主题.bib",
        )

    @bp.get("/api/topics/definitions")
    def definitions():
        from .definitions import DEFINITIONS

        return jsonify(
            [{"id": d.id, "name": d.name, "parent": d.parent} for d in DEFINITIONS]
        )

    @bp.post("/api/topics/expand/preview")
    def expand_preview():
        body(set())
        return jsonify(service.propose(service.store().owner))

    @bp.post("/api/topics/expand")
    def expand():
        data = body({"revision", "definitionIds"})
        owner = service.store().owner
        preview = service.propose(owner)
        if data.get("revision") != preview["revision"] or data.get("definitionIds") != [
            d["id"] for d in preview["definitions"]
        ]:
            raise TopicError("topic_catalog_changed", 409)
        service.expand(owner, preview)
        return jsonify(success=True)

    @bp.post("/api/topics/preview")
    def preview():
        data = body({"paperIds", "selectionId"})
        ids = selected(data)
        from .store import head

        store = service.store()
        with store.connection(write=True) as db:
            revisions = {
                paper: head(db, store.owner, paper)["revision"] for paper in ids
            }
        return jsonify(
            count=len(ids),
            paperIds=ids,
            revisions=revisions,
            requests=0,
            method="local",
        )

    @bp.post("/api/topics/batch")
    def batch_edit():
        data = body(
            {"paperIds", "selectionId", "action", "revisions", "topicId", "requestId"}
        )
        return jsonify(
            service.store().edit_batch(
                selected(data),
                data.get("action"),
                data.get("revisions"),
                data.get("topicId"),
                data.get("requestId"),
            )
        )

    @bp.post("/api/topics/jobs")
    def create_job():
        data = body({"paperIds", "selectionId"})
        store = service.store()
        return (
            jsonify(id=service.create(store.owner, selected(data)), kind="topics"),
            202,
        )

    def batch(batch_id):
        store = service.store()
        with store.connection() as db:
            row = db.execute(
                "SELECT * FROM topic_batches WHERE id=? AND owner_id=?",
                (batch_id, store.owner),
            ).fetchone()
            if not row:
                raise TopicError("topic_task_not_found", 404)
            counts = {
                r[0]: r[1]
                for r in db.execute(
                    "SELECT status,count(*) FROM topic_items WHERE owner_id=? AND batch_id=? GROUP BY status",
                    (store.owner, batch_id),
                )
            }
            try:
                offset = max(0, int(request.args.get("offset", 0)))
            except ValueError:
                raise TopicError("invalid_topic_query") from None
            items = [
                dict(r)
                for r in db.execute(
                    "SELECT i.id,i.paper_id,i.status,i.error,i.updated_at,p.title FROM topic_items i LEFT JOIN papers p ON p.id=i.paper_id AND p.owner_id=i.owner_id WHERE i.owner_id=? AND i.batch_id=? ORDER BY i.created_at,i.id LIMIT 50 OFFSET ?",
                    (store.owner, batch_id, offset),
                )
            ]
            events = [
                dict(r)
                for r in db.execute(
                    "SELECT e.sequence,e.item_id,e.kind,e.created_at FROM topic_events e JOIN topic_items i ON i.id=e.item_id AND i.owner_id=e.owner_id WHERE e.owner_id=? AND i.batch_id=? ORDER BY e.sequence DESC LIMIT 50",
                    (store.owner, batch_id),
                )
            ]
        terminal = set(counts) <= TERMINAL
        return {
            **dict(row),
            "kind": "topics",
            "counts": counts,
            "total": sum(counts.values()),
            "items": items,
            "events": events,
            "nextOffset": (
                offset + len(items)
                if offset + len(items) < sum(counts.values())
                else None
            ),
            "status": (
                (
                    "partial"
                    if any(
                        key in counts
                        for key in ("failed", "stale", "cancelled", "deleted")
                    )
                    else "completed"
                )
                if terminal
                else "running"
            ),
        }

    @bp.get("/api/topics/jobs")
    def jobs():
        store = service.store()
        with store.connection() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM topic_batches WHERE owner_id=? ORDER BY created_at DESC LIMIT 50",
                    (store.owner,),
                )
            ]
        return jsonify([batch(task) for task in ids])

    @bp.get("/api/topics/jobs/<batch_id>")
    def job(batch_id):
        return jsonify(batch(batch_id))

    @bp.post("/api/topics/jobs/<batch_id>/cancel")
    def cancel(batch_id):
        body(set())
        batch(batch_id)
        store = service.store()
        with store.connection(write=True) as db:
            db.execute(
                "UPDATE topic_batches SET cancel_requested=1 WHERE owner_id=? AND id=?",
                (store.owner, batch_id),
            )
            db.execute(
                "UPDATE topic_items SET status='cancelled' WHERE owner_id=? AND batch_id=? AND status='queued'",
                (store.owner, batch_id),
            )
        return jsonify(batch(batch_id))

    @bp.post("/api/topics/jobs/<batch_id>/retry")
    def retry(batch_id):
        body(set())
        batch(batch_id)
        store = service.store()
        with store.connection() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT paper_id FROM topic_items WHERE owner_id=? AND batch_id=? AND status IN ('failed','stale','cancelled')",
                    (store.owner, batch_id),
                )
            ]
        if not ids:
            raise TopicError("no_retryable_topics", 409)
        return jsonify(id=service.create(store.owner, ids), kind="topics"), 202

    app.register_blueprint(bp)
