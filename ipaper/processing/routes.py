from __future__ import annotations

import json
import re
import sqlite3

from flask import Blueprint, jsonify, request, send_file
from ipaper.security.outbound import OutboundPolicyError
from ipaper.document_worker.client import DocumentWorkerUnavailable
from ipaper.security.paths import PathSecurityError, safe_join
from .common import ProcessingError, encoded, now
from .pipeline import file_digest
from .translation import LANGUAGES


def register_processing_routes(app, service):
    blueprint = Blueprint("processing", __name__)
    app.extensions["processing"] = service

    @blueprint.before_request
    def limits():
        request.max_content_length = 65536

    @blueprint.after_request
    def private(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @blueprint.errorhandler(ProcessingError)
    def known(error):
        return jsonify(error=error.code), error.status

    @blueprint.errorhandler(OutboundPolicyError)
    def outbound(error):
        return jsonify(error=error.reason), 400

    @blueprint.errorhandler(PathSecurityError)
    def path_error(error):
        return jsonify(error="source_file_unavailable"), 404

    @blueprint.errorhandler(DocumentWorkerUnavailable)
    def worker_unavailable(error):
        return jsonify(error="document_worker_unavailable"), 503

    @blueprint.errorhandler(sqlite3.Error)
    def persistence_error(error):
        return jsonify(error="processing_storage_failed"), 503

    def body(allowed):
        value = request.get_json(silent=True)
        if not isinstance(value, dict) or set(value) - set(allowed):
            raise ProcessingError("invalid_processing_request")
        return value

    def public_result(result, *, pipeline=None):
        pipeline = pipeline or service.pipeline()
        doc = pipeline.store.document(result["document_id"])
        config = json.loads(result["config_json"])
        try:
            if result["kind"].startswith("babeldoc_"):
                from ipaper.security.paths import ensure_confined
                current = ensure_confined(pipeline.store.papers_root, doc["file_ref"], must_exist=True, require_file=True)
            else:
                current = pipeline.paper_file(result["paper_id"])
            stale = file_digest(current) != doc["sha256"]
            if config.get("sourceSha256"):
                stale = stale or file_digest(pipeline.paper_file(result["paper_id"])) != config["sourceSha256"]
        except (OSError, ValueError):
            stale = True
        return {"id": result["id"], "paperId": result["paper_id"], "kind": result["kind"],
                "status": result["status"], "documentId": doc["id"], "sourceHash": doc["sha256"],
                "pageCount": doc["page_count"], "parseId": result["parse_id"],
                "sourceLanguage": result["source_language"], "targetLanguage": result["target_language"],
                "model": config.get("model"), "normalizer": config.get("normalizer"), "provenance": config.get("provenance"),
                "createdAt": result["created_at"], "updatedAt": result["updated_at"],
                "blockCount": result["block_count"], "stale": stale}

    def public_job(job):
        checkpoint=json.loads(job["checkpoint_json"])
        cloud_tasks=[{"part":int(index)+1,"batchId":value["batchId"]} for index,value in checkpoint.get("parts",{}).items() if value.get("batchId")]
        return {"id": job["id"], "paperId": job["paper_id"], "kind": job["kind"], "resultId": job["result_id"] or checkpoint.get("understandingResultId") or checkpoint.get("selectionTranslationId"),
                "status": job["status"], "stage": job["stage"], "completed": job["completed"],
                "total": job["total"], "error": job["error"], "createdAt": job["created_at"],
                "updatedAt": job["updated_at"], "budget": json.loads(job["budget_json"]),
                "usage": json.loads(job["usage_json"]), "supplierCancellationConfirmed": False,"estimate":checkpoint.get("estimate"),"cloudTasks":cloud_tasks}

    @blueprint.get("/api/paper/<paper_id>/results")
    def results(paper_id):
        pipeline = service.pipeline()
        # Historical layout files are registered by reference only; their files
        # are never moved or retranslated. Worker validation is local and free.
        try:
            pipeline.register_layouts(paper_id)
        except Exception:
            registration = "layout_registration_unavailable"
        else:
            registration = None
        rows = pipeline.store.results(paper_id)
        return jsonify(registrationWarning=registration, results=[public_result(row, pipeline=pipeline) for row in rows
                if row["kind"] != "document_parts" and not json.loads(row["config_json"]).get("internalPart")])

    @blueprint.get("/api/results/<result_id>")
    def result_detail(result_id):
        return jsonify(result=public_result(service.pipeline().store.result(result_id)))

    def public_block(block, result_id):
        block = dict(block)
        image = block.pop("image", None)
        block.pop("textHash", None)
        block["imageUrl"] = None
        if image:
            store = service.pipeline().store
            result = store.result(result_id)
            parse_id = result["parse_id"] or result_id
            parsed = store.result(parse_id)
            entries = json.loads(parsed["manifest_json"]).get("entries", [])
            asset = next((entry for entry in entries if entry["path"] == image), None)
            if asset:
                block["imageUrl"] = f"/api/results/{parse_id}/assets/{asset['sha256']}"
        return block

    @blueprint.get("/api/results/<result_id>/blocks")
    def blocks(result_id):
        try:
            after = int(request.args.get("after", "-1"))
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            raise ProcessingError("invalid_cursor") from None
        values = service.pipeline().store.blocks(result_id, after=after, limit=limit)
        return jsonify(blocks=[public_block(block, result_id) for block in values],
                       nextCursor=values[-1]["order"] if len(values) == limit else None)

    @blueprint.get("/api/results/<result_id>/blocks/<block_id>")
    def block_detail(result_id, block_id):
        return jsonify(block=public_block(service.pipeline().store.block(result_id, block_id), result_id))

    @blueprint.get("/api/results/<result_id>/assets/<sha256>")
    def asset(result_id, sha256):
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ProcessingError("asset_not_found", 404)
        store = service.pipeline().store
        result = store.result(result_id)
        entry = next((entry for entry in json.loads(result["manifest_json"]).get("entries", [])
                      if entry["sha256"] == sha256 and entry["path"].lower().endswith((".png", ".jpg", ".jpeg"))), None)
        if not entry:
            raise ProcessingError("asset_not_found", 404)
        path = safe_join(store.artifact_directory(result_id), entry["path"], must_exist=True, require_file=True)
        return send_file(path, conditional=True, download_name=sha256 + path.suffix, max_age=0)

    @blueprint.get("/api/documents/<document_id>/file")
    def document_file(document_id):
        pipeline = service.pipeline()
        doc = pipeline.store.document(document_id)
        if doc["kind"] == "original":
            path = pipeline.paper_file(doc["paper_id"])
        else:
            from ipaper.security.paths import ensure_confined, user_storage_root
            path = ensure_confined(pipeline.store.papers_root, doc["file_ref"], must_exist=True, require_file=True)
            path.relative_to(user_storage_root(pipeline.store.papers_root, pipeline.store.owner))
        if file_digest(path) != doc["sha256"]:
            raise ProcessingError("source_expired", 409)
        return send_file(path, conditional=True, download_name="original.pdf", max_age=0)

    @blueprint.route("/api/documents/<document_id>/reading-position", methods=["GET", "PUT"])
    def document_position(document_id):
        store = service.pipeline().store
        doc = store.document(document_id)
        key = "document_position_v1:" + document_id
        with store.connection(write=request.method == "PUT") as db:
            store.paper_exists(db, doc["paper_id"])
            if request.method == "GET":
                row = db.execute("SELECT value FROM user_settings_v2 WHERE owner_id=? AND key=?", (store.owner,key)).fetchone()
                if row:
                    return jsonify(json.loads(row[0]))
                # Preserve the old dual-PDF position only for the matching
                # PDF.js fingerprint; Reader performs that fingerprint check.
                if doc["kind"] == "babeldoc_dual":
                    legacy=db.execute("SELECT value FROM user_settings_v2 WHERE owner_id=? AND key=?",(store.owner,"reading_position_v1:"+doc["paper_id"])).fetchone()
                    if legacy:
                        return jsonify(json.loads(legacy[0]))
                return jsonify({})
            data = body({"document","page","offset","zoom","rotation","fingerprint","sessionId"})
            page, offset, zoom = data.get("page"), data.get("offset"), data.get("zoom")
            if (type(page) is not int or not 1 <= page <= doc["page_count"]
                    or type(offset) not in {int,float} or not 0 <= offset <= 1
                    or not (zoom in ("width","page") or type(zoom) in {int,float} and .25 <= zoom <= 4)
                    or data.get("rotation") not in (0,90,180,270)
                    or not isinstance(data.get("fingerprint"),str) or len(data["fingerprint"]) > 128
                    or data.get("document") not in ("original","translated")):
                raise ProcessingError("invalid_reading_position")
            if data.get("sessionId") and not db.execute("SELECT 1 FROM chats WHERE owner_id=? AND paper_id=? AND session_id=?",(store.owner,doc["paper_id"],data["sessionId"])).fetchone():
                raise ProcessingError("session_not_found",404)
            db.execute("INSERT INTO user_settings_v2(owner_id,key,value) VALUES(?,?,?) ON CONFLICT(owner_id,key) DO UPDATE SET value=excluded.value",(store.owner,key,encoded({data["document"]:data})))
        return jsonify(data)

    @blueprint.post("/api/paper/<paper_id>/processing/preview")
    def preview(paper_id):
        body(set())
        return jsonify(service.pipeline().preflight(paper_id))

    @blueprint.post("/api/paper/<paper_id>/processing/estimate")
    def estimate(paper_id):
        data = body({"kind", "preflightId", "parseResultId", "translationResultId", "sourceLanguage", "targetLanguage", "pages", "blockIds", "budget", "contentVersion", "allowPartial", "previousResultId", "analysisResultId", "format"})
        if data.get("kind") in {"overview","interpretation","analysis_export"}:
            return jsonify(service.understanding().create(paper_id,data,preview=True))
        # Pure admission preview: no job, cloud, model or worker submission.
        return jsonify(service.pipeline().create(paper_id, data, preview=True))

    @blueprint.post("/api/paper/<paper_id>/processing/jobs")
    def create(paper_id):
        data = body({"kind", "preflightId", "parseResultId", "translationResultId", "sourceLanguage", "targetLanguage", "pages", "blockIds", "budget", "contentVersion", "allowPartial", "previousResultId", "analysisResultId", "format"})
        target=service.understanding() if data.get("kind") in {"overview","interpretation","analysis_export"} else service.pipeline()
        job, created = target.create(paper_id, data)
        service.wake.set()
        return jsonify(job=public_job(job), reused=not created), 202 if created else 200

    @blueprint.get("/api/processing/jobs")
    def job_list():
        return jsonify(jobs=[public_job(job) for job in service.pipeline().jobs.list()])

    @blueprint.get("/api/processing/jobs/<job_id>")
    def job_detail(job_id):
        return jsonify(job=public_job(service.pipeline().jobs.get(job_id)))

    @blueprint.get("/api/processing/jobs/<job_id>/events")
    def events(job_id):
        try:
            after = int(request.args.get("after", "0"))
            if after < 0:
                raise ValueError
        except ValueError:
            raise ProcessingError("invalid_cursor") from None
        rows = service.pipeline().jobs.events(job_id, after)
        return jsonify(events=[{"sequence": row["sequence"], "kind": row["kind"], "data": json.loads(row["data_json"]), "createdAt": row["created_at"]} for row in rows])

    @blueprint.post("/api/processing/jobs/<job_id>/cancel")
    def cancel(job_id):
        body(set())
        pipeline = service.pipeline()
        pipeline.jobs.cancel(job_id)
        return jsonify(job=public_job(pipeline.jobs.get(job_id)))

    @blueprint.post("/api/processing/jobs/<job_id>/resume")
    def resume(job_id):
        body(set())
        pipeline = service.pipeline()
        pipeline.jobs.resume(job_id)
        service.wake.set()
        return jsonify(job=public_job(pipeline.jobs.get(job_id)))

    @blueprint.route("/api/settings/structured-translation", methods=["GET", "PUT"])
    def settings():
        profiles = service.pipeline().profiles
        if request.method == "PUT":
            data = body({"model", "baseUrl", "key", "clearKey"})
            service.policy.validate(data.get("baseUrl"), purpose="ai")
            if "clearKey" in data and type(data["clearKey"]) is not bool:
                raise ProcessingError("invalid_model_config")
            profiles.save(data.get("model"), data.get("baseUrl"), key=data.get("key"), clear_key=data.get("clearKey", False))
        return jsonify(profile=profiles.get(), languages=LANGUAGES)

    @blueprint.post("/api/settings/structured-translation/initialize")
    def initialize():
        body(set())
        pipeline = service.pipeline()
        pipeline.profiles.initialize_from_babeldoc(pipeline.settings(), service.credentials)
        return jsonify(profile=pipeline.profiles.get())

    @blueprint.post("/api/paper/<paper_id>/sources")
    def selection(paper_id):
        data = body({"resultId", "blockId", "revisionId", "start", "end", "field"})
        return jsonify(source=service.sources().create_block_selection(paper_id, data.get("resultId"), data.get("blockId"),
            start=data.get("start"), end=data.get("end"), revision_id=data.get("revisionId"), field=data.get("field", "text")))

    @blueprint.post("/api/paper/<paper_id>/pdf-sources")
    def pdf_selection(paper_id):
        data = body({"documentId","document","page","text"})
        if data.get("document") not in ("original","translated"):
            raise ProcessingError("invalid_document_kind")
        return jsonify(source=service.sources().create_pdf_selection(service.pipeline(),paper_id,
            page=data.get("page"),text=data.get("text"),document_id=data.get("documentId"),translated=data["document"]=="translated"))

    @blueprint.get("/api/sources/<source_id>")
    def resolve_source(source_id):
        try:
            value=service.sources().resolve(source_id)
        except ProcessingError as exc:
            if exc.status!=404:raise
            value=service.understanding().files.resolve(source_id)
        return jsonify(source=value)

    @blueprint.route("/api/results/<result_id>/reading-position", methods=["GET", "PUT"])
    def position(result_id):
        store = service.pipeline().store
        result = store.result(result_id)
        if request.method == "GET":
            with store.connection() as db:
                row = db.execute("SELECT value_json FROM processing_reading_positions WHERE owner_id=? AND result_id=?", (store.owner, result_id)).fetchone()
            return jsonify(position=json.loads(row[0]) if row else None)
        data = body({"blockId", "offset", "display", "sessionId"})
        store.block(result_id, data.get("blockId"))
        offset = data.get("offset")
        if (type(offset) not in {int, float} or not 0 <= offset <= 1
                or data.get("display") not in ("original", "translated", "bilingual")
                or data.get("sessionId") is not None and (not isinstance(data["sessionId"],str) or len(data["sessionId"])>128)):
            raise ProcessingError("invalid_reading_position")
        with store.connection(write=True) as db:
            if data.get("sessionId") and not db.execute("SELECT 1 FROM chats WHERE owner_id=? AND paper_id=? AND session_id=?", (store.owner, result["paper_id"], data["sessionId"])).fetchone():
                raise ProcessingError("session_not_found", 404)
            db.execute("INSERT INTO processing_reading_positions VALUES (?,?,?,?,?) ON CONFLICT(owner_id,paper_id,result_id) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                       (store.owner, result["paper_id"], result_id, encoded(data), now()))
        return jsonify(position=data)

    from .understanding_routes import attach_understanding_routes
    attach_understanding_routes(blueprint,service,body)
    from .reading_routes import attach_reading_routes
    attach_reading_routes(blueprint,service,body,public_job)
    app.register_blueprint(blueprint)
