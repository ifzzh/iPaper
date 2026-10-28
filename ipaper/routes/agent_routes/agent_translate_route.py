from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from flask import Response, jsonify, request, send_file, stream_with_context

from ipaper.core.base_paper import Paper
from ipaper.processing.digest_cache import file_digest
from ipaper.core.paper_store import paper_store
from ipaper.database.dao.translation_job_dao import TranslationJobDAO
from ipaper.database.dao.settings_dao import SettingsDAO
from ipaper.security.identity import Identity, run_as_identity
from ipaper.security.agentic_credentials import AgenticCredentialStore
from ipaper.security.outbound import OutboundPolicy, OutboundPolicyError
from ipaper.security.paths import PathSecurityError, paper_asset_paths, verified_paper_path
from ipaper.tools.agent_tools.translate_pdf import (
    TranslationDependencies,
    start_monitor,
)
from ipaper.tools.agent_tools.translation_worker_client import (
    TranslationWorkerClient,
    TranslationWorkerRejected,
    TranslationWorkerUnavailable,
)
from ipaper.tools.api_test_utils import test_llm_api  # retained import for legacy test patches
from ipaper.processing.pipeline import file_digest

CategoryPath = List[str]


def register_agent_translate_routes(
    app,
    *,
    translation_tasks: Dict[str, Dict[str, Any]],
    translation_tasks_lock: threading.Lock,
    get_categories: Callable[[], dict],
    get_category_path: Callable[[dict, str], CategoryPath | None],
    get_papers_in_category: Callable[[str, CategoryPath], List[Paper]],
    save_paper_metadata: Callable[[str, Any], None],
    agentic_settings_file: str,
    upload_folder: str,
    credential_store: AgenticCredentialStore | None = None,
    outbound_policy: OutboundPolicy | None = None,
) -> None:
    del agentic_settings_file
    worker_client = TranslationWorkerClient()
    deps = TranslationDependencies(
        translation_tasks=translation_tasks,
        translation_tasks_lock=translation_tasks_lock,
        get_categories=get_categories,
        get_category_path=get_category_path,
        get_papers_in_category=get_papers_in_category,
        save_paper_metadata=save_paper_metadata,
        upload_folder=upload_folder,
        worker_client=worker_client,
    )
    processing = app.extensions.get("processing")
    if processing:
        deps.before_publish = lambda paper_id: processing.pipeline().register_layouts(paper_id, freeze=True)
        deps.after_publish = lambda job_id,paper_id,path: processing.pipeline().publish_layout_job(job_id,paper_id,path)
    dispatch_event = threading.Event()
    dispatch_stop = threading.Event()
    dispatcher_last_owner: str | None = None

    def find_paper(paper_id: str) -> Paper | None:
        entry = paper_store.get_entry(paper_id)
        if entry:
            return entry.paper
        categories = get_categories()

        def search(node: dict) -> Paper | None:
            category_path = get_category_path(categories, node["id"])
            if category_path:
                for paper in get_papers_in_category(node["id"], category_path):
                    if paper.id == paper_id:
                        return paper
            for child in node.get("children", []):
                found = search(child)
                if found:
                    return found
            return None

        for child in categories.get("children", []):
            found = search(child)
            if found:
                return found
        return None

    def resolve_paper_file(paper: Paper, *, must_exist: bool = True) -> str:
        entry = paper_store.get_entry(paper.id)
        if not entry:
            raise PathSecurityError("paper_category_missing")
        return str(
            verified_paper_path(
                upload_folder,
                entry.category_id,
                paper.filename,
                paper.file_path,
                must_exist=must_exist,
            )
        )

    def config_fingerprint(pdf_path: str, model: str, base_url: str, output_mode: str) -> str:
        digest = hashlib.sha256()
        with open(pdf_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(json.dumps(
            {"model": model, "base_url": base_url, "lang_in": "en", "lang_out": "zh", "output_mode": output_mode},
            sort_keys=True,
        ).encode("utf-8"))
        return digest.hexdigest()

    def _dispatch_for_owner(job: dict) -> bool:
        settings = SettingsDAO.get_setting("agentic_settings", {}) or {}
        config = ((settings.get("llmConfigs") or {}).get("translate") or {})
        model = (config.get("llmModel") or "").strip()
        base_url = (config.get("llmBaseUrl") or "").strip()
        try:
            api_key = credential_store.get("translate") if credential_store else ""
            if outbound_policy:
                outbound_policy.validate(base_url, purpose="ai")
        except Exception as exc:  # noqa: BLE001
            reason = getattr(exc, "reason", "translation_settings_unavailable")
            TranslationJobDAO.update(job["job_id"], "failed", error=reason, error_code=reason)
            TranslationJobDAO.append_event(
                job["job_id"], kind="status", level="error", message=reason,
            )
            return False
        if not model or not base_url or not api_key:
            reason = "translation_settings_not_configured"
            TranslationJobDAO.update(job["job_id"], "failed", error=reason, error_code=reason)
            TranslationJobDAO.append_event(
                job["job_id"], kind="status", level="error", message=reason,
            )
            return False
        if processing:
            pipeline=processing.pipeline()
            with pipeline.store.connection() as db:
                row=db.execute("SELECT snapshot_json FROM processing_layout_jobs WHERE job_id=? AND owner_id=?",(job["job_id"],pipeline.store.owner)).fetchone()
            if row:
                snapshot=json.loads(row[0])
                if snapshot["model"]!=model or snapshot["baseUrl"]!=base_url or snapshot["sourceSha256"]!=file_digest(pipeline.paper_file(job["paper_id"])):
                    TranslationJobDAO.update(job["job_id"],"failed",error="translation_config_or_source_changed",error_code="translation_config_or_source_changed")
                    return False
        if not worker_client.health():
            return False
        try:
            try:
                worker_state = worker_client.get(job["job_id"])
            except TranslationWorkerRejected as exc:
                if exc.status_code != 404:
                    raise
                worker_state = None
            TranslationJobDAO.update(job["job_id"], "dispatching")
            if worker_state is None or worker_state.get("status") in {"paused", "failed"}:
                worker_client.create(job["job_id"], model, base_url, api_key, **({"output_mode": "mono"} if job.get("output_mode") == "mono" else {}))
            TranslationJobDAO.update(
                job["job_id"], "running", increment_attempt=True, heartbeat=True,
            )
            TranslationJobDAO.append_event(
                job["job_id"], kind="status", message="running",
            )
            start_monitor(job["job_id"], job["paper_id"], deps)
            return True
        except TranslationWorkerUnavailable:
            TranslationJobDAO.enqueue(job["job_id"])
            return False
        except TranslationWorkerRejected as exc:
            if exc.reason in {"worker_busy", "job_exists"}:
                TranslationJobDAO.enqueue(job["job_id"])
                return False
            TranslationJobDAO.update(
                job["job_id"], "failed", error=exc.reason, error_code=exc.reason,
            )
            return False
        finally:
            api_key = ""

    def _dispatch_loop() -> None:
        nonlocal dispatcher_last_owner
        TranslationJobDAO.recover_incomplete()
        while not dispatch_stop.is_set():
            if TranslationJobDAO.has_global_running():
                dispatch_event.wait(1.0)
                dispatch_event.clear()
                continue
            job = TranslationJobDAO.next_queued(dispatcher_last_owner)
            if not job:
                dispatch_event.wait(5.0)
                dispatch_event.clear()
                continue
            identity = Identity(job["owner_id"], job["username"], job["role"])
            dispatched = run_as_identity(identity, _dispatch_for_owner, job)
            if dispatched:
                dispatcher_last_owner = job["owner_id"]
            else:
                dispatch_event.wait(5.0)
                dispatch_event.clear()

    deps.on_terminal = dispatch_event.set
    if app.config.get("IPAPER_START_BACKGROUND_TASKS", False):
        threading.Thread(
            target=_dispatch_loop, name="translation-dispatcher", daemon=True
        ).start()

    @app.post("/api/paper/translate")
    def api_translate_paper():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "invalid_request"}), 400
        forbidden = sorted(set(data) - {"paper_id", "output_mode"})
        if forbidden:
            return jsonify({"success": False, "error": "forbidden_agent_overrides", "fields": forbidden}), 400
        paper_id = data.get("paper_id")
        output_mode = data.get("output_mode", "dual")
        if output_mode not in {"mono", "dual"}:
            return jsonify(error="invalid_output_mode"), 400
        if not isinstance(paper_id, str) or not paper_id.strip():
            return jsonify({"success": False, "error": "Missing required parameters"}), 400
        if credential_store is None or outbound_policy is None:
            return jsonify({"success": False, "error": "agentic_security_unavailable"}), 503
        settings = SettingsDAO.get_setting("agentic_settings", {}) or {}
        config = ((settings.get("llmConfigs") or {}).get("translate") or {})
        model = (config.get("llmModel") or "").strip()
        base_url = (config.get("llmBaseUrl") or "").strip()
        try:
            api_key = credential_store.get("translate")
            outbound_policy.validate(base_url, purpose="ai")
        except OutboundPolicyError as exc:
            return jsonify({"success": False, "error": exc.reason}), 400
        except Exception:
            return jsonify({"success": False, "error": "credential_decryption_failed"}), 503
        if not model or not base_url or not api_key:
            return jsonify({"success": False, "error": "translation_settings_not_configured"}), 400
        if TranslationJobDAO.has_active_for_paper(paper_id):
            return jsonify({"success": False, "error": "There is already a translation task running for this paper"}), 400
        paper = find_paper(paper_id)
        if paper is None:
            return jsonify({"success": False, "error": "Paper not found"}), 404
        try:
            pdf_path = resolve_paper_file(paper)
        except PathSecurityError:
            return jsonify({"success": False, "error": "unsafe_stored_path"}), 409

        task_id = str(uuid.uuid4())
        try:
            source_sha = file_digest(pdf_path)
            staged = worker_client.stage_input(task_id, pdf_path)
            if processing and (file_digest(staged) != source_sha or file_digest(pdf_path) != source_sha):
                raise ValueError("source_changed")
            TranslationJobDAO.create(
                task_id, paper_id,
                config_fingerprint=config_fingerprint(pdf_path, model, base_url, output_mode),
                output_mode=output_mode,
                config_snapshot={"model":model,"baseUrl":base_url,"outputMode":output_mode,
                    "sourceSha256": source_sha,
                    "sourceLanguage":"en","targetLanguage":"zh-CN","contractVersion":2} if processing else None,
            )
            with translation_tasks_lock:
                translation_tasks[task_id] = {
                    "paper_id": paper_id,
                    "status": "queued",
                    "progress": 0,
                    "logs": [],
                    "log_lock": threading.Lock(),
                    "start_time": datetime.now(timezone.utc).isoformat(),
                    "result": None,
                }
        except TranslationWorkerUnavailable:
            try:
                worker_client.cleanup(task_id)
            except Exception:
                pass
            return jsonify({"success": False, "error": "translation_worker_unavailable"}), 503
        except TranslationWorkerRejected as exc:
            TranslationJobDAO.update(task_id, "failed", error=exc.reason)
            return jsonify({"success": False, "error": exc.reason}), exc.status_code
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 413
        except Exception:  # noqa: BLE001
            try:
                worker_client.cleanup(task_id)
            except Exception:
                pass
            return jsonify({"success": False, "error": "translation_worker_unavailable"}), 503
        finally:
            api_key = ""

        dispatch_event.set()
        return jsonify({
            "success": True, "message": "Translation task queued",
            "task_id": task_id, "status": "queued",
        }), 202

    @app.get("/api/paper/translate/active")
    def api_get_active_translations():
        jobs = TranslationJobDAO.list_active()
        return jsonify(
            {
                "success": True,
                "tasks": [
                    {
                        "task_id": item["job_id"],
                        "paper_id": item["paper_id"],
                        "status": item["status"],
            "output_mode": item.get("output_mode", "dual"),
                        "start_time": item["created_at"],
                    }
                    for item in jobs
                ],
            }
        )

    def _job_payload(item: dict) -> dict:
        paper = find_paper(item["paper_id"])
        payload = {
            "job_id": item["job_id"],
            "paper_id": item["paper_id"],
            "paper_title": (paper.title or paper.filename) if paper else item["paper_id"],
            "status": item["status"],
            "output_mode": item.get("output_mode", "dual"),
            "progress": int(item.get("progress") or 0),
            "stage": item.get("stage"),
            "stage_progress": int(item.get("stage_progress") or 0),
            "stage_current": int(item.get("stage_current") or 0),
            "stage_total": int(item.get("stage_total") or 0),
            "attempt_count": int(item.get("attempt_count") or 0),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "completed_at": item.get("completed_at"),
            "heartbeat_at": item.get("heartbeat_at"),
            "error_code": item.get("error_code"),
            "recoverable_until": item.get("recoverable_until"),
        }
        payload["queue_position"] = TranslationJobDAO.queue_position(item["job_id"])
        return payload

    @app.get("/api/translations")
    def api_list_translations():
        status = request.args.get("status") or None
        try:
            limit = int(request.args.get("limit", "100"))
        except ValueError:
            return jsonify({"success": False, "error": "invalid_limit"}), 400
        return jsonify({
            "success": True,
            "tasks": [_job_payload(item) for item in TranslationJobDAO.list_jobs(status=status, limit=limit)],
        })

    @app.get("/api/translations/<task_id>")
    def api_get_translation(task_id: str):
        item = TranslationJobDAO.get(task_id)
        if not item:
            return jsonify({"success": False, "error": "task_not_found"}), 404
        payload = _job_payload(item)
        payload["events"] = TranslationJobDAO.list_events(job_id=task_id, limit=500)
        return jsonify({"success": True, "task": payload})

    @app.get("/api/translations/events")
    def api_translation_events():
        try:
            after = int(request.headers.get("Last-Event-ID") or request.args.get("after") or 0)
        except ValueError:
            return jsonify({"success": False, "error": "invalid_event_id"}), 400

        @stream_with_context
        def generate():
            cursor = max(0, after)
            idle_started = time.monotonic()
            while time.monotonic() - idle_started < 55:
                events = TranslationJobDAO.list_events(after_id=cursor, limit=200)
                if events:
                    idle_started = time.monotonic()
                    for event in events:
                        cursor = int(event["id"])
                        yield f"id: {cursor}\nevent: translation\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                else:
                    yield ": heartbeat\n\n"
                time.sleep(1)

        return Response(generate(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })

    def _require_task(task_id: str) -> tuple[dict | None, Any | None]:
        item = TranslationJobDAO.get(task_id)
        if not item:
            return None, (jsonify({"success": False, "error": "task_not_found"}), 404)
        return item, None

    @app.post("/api/translations/<task_id>/pause")
    def api_pause_translation(task_id: str):
        item, failure = _require_task(task_id)
        if failure:
            return failure
        if item["status"] == "queued":
            TranslationJobDAO.update(task_id, "paused", error="paused", error_code="paused")
        elif item["status"] in {"running", "dispatching", "recovering"}:
            try:
                worker_client.pause(task_id)
            except TranslationWorkerUnavailable:
                pass
            TranslationJobDAO.update(task_id, "paused", error="paused", error_code="paused")
        elif item["status"] != "paused":
            return jsonify({"success": False, "error": "task_not_pauseable"}), 409
        TranslationJobDAO.append_event(task_id, kind="status", message="paused")
        dispatch_event.set()
        return jsonify({"success": True, "status": "paused"})

    @app.post("/api/translations/<task_id>/resume")
    def api_resume_translation(task_id: str):
        item, failure = _require_task(task_id)
        if failure:
            return failure
        if item["status"] != "paused":
            return jsonify({"success": False, "error": "task_not_resumable"}), 409
        TranslationJobDAO.enqueue(task_id)
        dispatch_event.set()
        return jsonify({"success": True, "status": "queued"}), 202

    @app.post("/api/translations/<task_id>/retry")
    def api_retry_translation(task_id: str):
        item, failure = _require_task(task_id)
        if failure:
            return failure
        if item["status"] != "failed":
            return jsonify({"success": False, "error": "task_not_retryable"}), 409
        TranslationJobDAO.enqueue(task_id)
        dispatch_event.set()
        return jsonify({"success": True, "status": "queued"}), 202

    @app.patch("/api/translations/<task_id>/queue-position")
    def api_reorder_translation(task_id: str):
        item, failure = _require_task(task_id)
        if failure:
            return failure
        data = request.get_json(silent=True) or {}
        position = data.get("position")
        if not isinstance(position, int) or position < 1:
            return jsonify({"success": False, "error": "invalid_queue_position"}), 400
        queued = [job for job in TranslationJobDAO.list_jobs(status="queued", limit=200)
                  if job["job_id"] != task_id]
        position = min(position, len(queued) + 1)
        queued.insert(position - 1, item)
        base = time.time_ns()
        for index, job in enumerate(queued):
            TranslationJobDAO.set_queue_order(job["job_id"], base + index)
        dispatch_event.set()
        return jsonify({"success": True, "queue_position": position})

    @app.delete("/api/translations/<task_id>")
    def api_delete_translation(task_id: str):
        item, failure = _require_task(task_id)
        if failure:
            return failure
        if item["status"] in {"running", "dispatching", "recovering"}:
            try:
                worker_client.cancel(task_id)
            except TranslationWorkerUnavailable:
                pass
        TranslationJobDAO.update(task_id, "cancelled", error="cancelled", error_code="cancelled")
        TranslationJobDAO.append_event(task_id, kind="status", message="cancelled")
        dispatch_event.set()
        return jsonify({"success": True, "status": "cancelled"})

    @app.get("/api/paper/translate/<task_id>/logs")
    def api_get_translation_logs(task_id: str):
        persisted = TranslationJobDAO.get(task_id)
        if not persisted:
            return jsonify({"success": False, "error": "Task does not exist"}), 404
        with translation_tasks_lock:
            task = translation_tasks.get(task_id)
            if task:
                with task["log_lock"]:
                    return jsonify(
                        {
                            "success": True,
                            "status": task["status"],
                            "progress": max(0, min(100, int(task.get("progress") or 0))),
                            "logs": list(task.get("logs") or []),
                            "start_time": task["start_time"],
                            "result": task.get("result"),
                        }
                    )
        return jsonify(
            {
                "success": True,
                "status": persisted["status"],
                "progress": persisted["progress"],
                "logs": [],
                "start_time": persisted["created_at"],
                "result": (
                    {"success": False, "error": persisted["error"]}
                    if persisted["status"] == "failed"
                    else None
                ),
            }
        )

    @app.post("/api/paper/translate/<task_id>/cancel")
    def api_cancel_translation(task_id: str):
        persisted = TranslationJobDAO.get(task_id)
        if not persisted:
            return jsonify({"success": False, "error": "Task does not exist"}), 404
        if persisted["status"] in {"completed", "failed", "cancelled"}:
            return jsonify({"success": False, "error": "The task has ended and cannot be canceled"}), 400
        state = {"progress": persisted.get("progress", 0)}
        if persisted["status"] in {"running", "dispatching", "recovering"}:
            try:
                state = worker_client.cancel(task_id)
            except TranslationWorkerUnavailable:
                pass
            except TranslationWorkerRejected as exc:
                if exc.status_code != 404:
                    return jsonify({"success": False, "error": exc.reason}), exc.status_code
        TranslationJobDAO.update(task_id, "cancelled", progress=state.get("progress", 0), error="cancelled")
        TranslationJobDAO.append_event(task_id, kind="status", message="cancelled")
        with translation_tasks_lock:
            task = translation_tasks.get(task_id)
            if task:
                task["status"] = "cancelled"
                task["result"] = {"success": False, "error": "Translation canceled"}
        dispatch_event.set()
        return jsonify({"success": True, "message": "Translation task canceled"})

    @app.get("/api/paper/<paper_id>/chinese/file")
    def api_get_chinese_paper_file(paper_id: str):
        paper = find_paper(paper_id)
        if paper is None:
            return jsonify({"error": "Paper not found"}), 404
        try:
            resolve_paper_file(paper, must_exist=False)
            pdf_path = resolve_paper_file(paper)
            assets = paper_asset_paths(upload_folder, pdf_path)
            mode = request.args.get("mode", "dual")
            if mode not in {"mono", "dual"}:
                return jsonify(error="invalid_output_mode"), 400
            chinese_path = assets.chinese_mono if mode == "mono" else assets.chinese_dual
            if "mode" not in request.args and not chinese_path.exists() and assets.chinese_mono.exists():
                chinese_path = assets.chinese_mono
        except PathSecurityError as exc:
            if str(exc) == "missing_path":
                return jsonify({"error": "PDF file not found"}), 404
            return jsonify({"error": "unsafe_stored_path"}), 409
        if not chinese_path.exists():
            return jsonify({"error": "Chinese version file does not exist"}), 404
        return send_file(str(chinese_path), as_attachment=False, mimetype="application/pdf", etag=file_digest(chinese_path))

    if app.config.get("IPAPER_START_BACKGROUND_TASKS", False):
        threading.Thread(target=worker_client.run_cleanup_loop, daemon=True).start()
