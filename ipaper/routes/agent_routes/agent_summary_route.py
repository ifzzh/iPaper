from __future__ import annotations

import mimetypes
import os
import posixpath
import subprocess
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from flask import jsonify, request, send_file

from ipaper.timeutil import utc_iso
from ipaper.core.base_paper import Paper
from ipaper.core.paper_store import paper_store
from ipaper.database.dao.settings_dao import SettingsDAO
from ipaper.document_worker.client import DocumentWorkerClient
from ipaper.runtime.task_queue import BoundedExecutor, QueueFull
from ipaper.security.agentic_credentials import AgenticCredentialStore
from ipaper.security.identity import current_user_id
from ipaper.security.outbound import OutboundPolicy, OutboundPolicyError
from ipaper.security.paths import (
    PathSecurityError,
    ensure_confined,
    paper_asset_paths,
    safe_join,
    verified_paper_path,
)
from ipaper.tools.agent_tools.summary_pdf import (
    AnalysisDependencies,
    analyze_paper_task,
)
from ipaper.tools.api_test_utils import test_mineru_api

CategoryPath = List[str]
_default_analysis_executor = BoundedExecutor(
    max_workers=1, max_queue=2, thread_name_prefix="analysis"
)


def register_agent_summary_routes(
    app,
    *,
    analysis_tasks: Dict[str, Dict[str, Any]],
    analysis_tasks_lock: threading.Lock,
    get_categories: Callable[[], dict],
    get_category_path: Callable[[dict, str], CategoryPath | None],
    get_papers_in_category: Callable[[str, CategoryPath], List[Paper]],
    save_paper_metadata: Callable[[str, Any], None],
    agentic_settings_file: str,
    upload_folder: str,
    credential_store: AgenticCredentialStore | None = None,
    outbound_policy: OutboundPolicy | None = None,
    document_client: DocumentWorkerClient | None = None,
    task_executor: BoundedExecutor | None = None,
) -> None:
    del agentic_settings_file
    task_executor = task_executor or _default_analysis_executor
    def resolve_paper_file(paper: Paper) -> str:
        entry = paper_store.get_entry(paper.id)
        if not entry:
            raise PathSecurityError("paper_category_missing")
        return str(
            verified_paper_path(
                upload_folder,
                entry.category_id,
                paper.filename,
                paper.file_path,
            )
        )

    @app.route("/api/paper/analyze", methods=["POST"])
    def api_analyze_paper():
        """AI InterpretationPDFpaper - Start background task"""
        if app.extensions.get("processing"):
            from ipaper.processing.common import ProcessingError
            try:
                data=request.get_json(silent=True)
                if not isinstance(data,dict) or set(data)-{"paper_id","ai_language"}:
                    raise ProcessingError("invalid_request")
                target=app.extensions["processing"].understanding()
                state=target.files.status(data.get("paper_id"))
                options={"kind":"interpretation","contentVersion":state["version"]}
                if data.get("ai_language") is not None:options["language"]=data["ai_language"]
                job,reused=target.create(data.get("paper_id"),options)
                app.extensions["processing"].wake.set()
                return jsonify(success=True,task_id=job["id"],reused=not reused)
            except ProcessingError as exc:
                return jsonify(error=exc.code),exc.status
        try:
            data = request.json or {}
            if not isinstance(data, dict):
                return jsonify({"success": False, "error": "invalid_request"}), 400
            forbidden = sorted(set(data) - {"paper_id", "ai_language"})
            if forbidden:
                return jsonify({"success": False, "error": "forbidden_agent_overrides", "fields": forbidden}), 400
            paper_id = data.get("paper_id")
            if not isinstance(paper_id, str) or not paper_id.strip():
                return (
                    jsonify({"success": False, "error": "Missing required parameters"}),
                    400,
                )
            if credential_store is None or outbound_policy is None:
                return jsonify({"success": False, "error": "agentic_security_unavailable"}), 503
            if document_client is None:
                return jsonify({"success": False, "error": "document_worker_unavailable"}), 503

            agentic_settings = SettingsDAO.get_setting("agentic_settings", {}) or {}

            use_api = agentic_settings.get("mineruUseApi", False)
            mineru_config = {
                "useApi": use_api,
                "serverUrl": (
                    agentic_settings.get("mineruServerUrl", "") if not use_api else ""
                ),
                "apiToken": (
                    credential_store.get("mineru") if use_api else ""
                ),
            }

            # Validate based on mode
            if use_api:
                if not mineru_config["apiToken"]:
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": "MinerU API token is required. Please configure it in settings.",
                            }
                        ),
                        400,
                    )
            else:
                if not mineru_config["serverUrl"]:
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": "MinerU Server URL is required. Please configure it in settings.",
                            }
                        ),
                        400,
                    )

            llm_configs = agentic_settings.get("llmConfigs") or {}
            llm_config = llm_configs.get("interpret") or {}
            llm_model = (llm_config.get("llmModel") or "").strip()
            openai_base_url = (llm_config.get("llmBaseUrl") or "").strip()
            openai_api_key = credential_store.get("interpret")
            system_prompt = ""

            if not llm_model:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "interpret_settings_not_configured",
                        }
                    ),
                    400,
                )

            if not openai_base_url or not openai_api_key:
                return jsonify({"success": False, "error": "interpret_settings_not_configured"}), 400
            outbound_policy.validate(openai_base_url, purpose="ai")
            # test MinerU based on mode
            if use_api:
                # Test API token
                from ipaper.tools.api_test_utils import test_mineru_api_token

                mineru_success, mineru_error = test_mineru_api_token(
                    mineru_config["apiToken"]
                )
            else:
                # Test local server
                outbound_policy.validate(mineru_config["serverUrl"], purpose="ai")
                mineru_success, mineru_error = test_mineru_api(
                    mineru_config["serverUrl"], outbound_policy
                )

            if not mineru_success:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"MinerU test failed: {mineru_error}",
                        }
                    ),
                    400,
                )

            with analysis_tasks_lock:
                for task_id, task_info in analysis_tasks.items():
                    if (
                        task_info["paper_id"] == paper_id
                        and task_info["status"] == "running"
                    ):
                        return (
                            jsonify(
                                {
                                    "success": False,
                                    "error": "There is already an interpretation task running for this paper",
                                    "task_id": task_id,
                                }
                            ),
                            400,
                        )

            # First try from paper_store Find papers in（support _ReadingListTemp Table of contents）
            entry = paper_store.get_entry(paper_id)
            if entry:
                paper = entry.paper
                category_path = list(entry.category_path)
            else:
                # if paper_store Not found in , use recursive search of classification tree
                categories = get_categories()

                def search_paper_recursive(node):
                    category_path = get_category_path(categories, node["id"])
                    if category_path:
                        papers = get_papers_in_category(node["id"], category_path)
                        for paper in papers:
                            if paper.id == paper_id:
                                return paper, category_path

                    if "children" in node:
                        for child in node["children"]:
                            result = search_paper_recursive(child)
                            if result:
                                return result

                    return None

                result = None
                for child in categories.get("children", []):
                    result = search_paper_recursive(child)
                    if result:
                        break

                if not result:
                    return jsonify({"success": False, "error": "Paper not found"}), 404

                paper, category_path = result
            try:
                pdf_path = resolve_paper_file(paper)
            except PathSecurityError:
                return (
                    jsonify({"success": False, "error": "unsafe_stored_path"}),
                    409,
                )

            pdf_dir = os.path.dirname(pdf_path)
            pdf_filename = os.path.basename(pdf_path)

            task_id = str(uuid.uuid4())

            with analysis_tasks_lock:
                analysis_tasks[task_id] = {
                    "owner_id": current_user_id(),
                    "paper_id": paper_id,
                    "status": "queued",
                    "step": None,
                    "progress": 0,
                    "logs": [],
                    "log_lock": threading.Lock(),
                    "process": None,
                    "start_time": utc_iso(),
                    "result": None,
                }

            deps = AnalysisDependencies(
                analysis_tasks=analysis_tasks,
                analysis_tasks_lock=analysis_tasks_lock,
                get_categories=get_categories,
                get_category_path=get_category_path,
                get_papers_in_category=get_papers_in_category,
                save_paper_metadata=save_paper_metadata,
                outbound_policy=outbound_policy,
                document_client=document_client,
            )

            ai_language = data.get("ai_language", "zh")
            if ai_language not in {"zh", "en"}:
                ai_language = "zh"

            try:
                future = task_executor.submit(
                    analyze_paper_task,
                    task_id,
                    paper_id,
                    pdf_path,
                    pdf_dir,
                    pdf_filename,
                    mineru_config,
                    llm_model,
                    openai_base_url,
                    openai_api_key,
                    system_prompt,
                    ai_language,
                    deps,
                )
            except QueueFull:
                with analysis_tasks_lock:
                    analysis_tasks.pop(task_id, None)
                response = jsonify(
                    {"success": False, "error": "analysis_queue_full"}
                )
                response.headers["Retry-After"] = "60"
                return response, 429
            with analysis_tasks_lock:
                if task_id in analysis_tasks:
                    analysis_tasks[task_id]["future"] = future

            return jsonify(
                {
                    "success": True,
                    "message": "Interpretation task has started",
                    "task_id": task_id,
                }
            )

        except OutboundPolicyError as exc:
            return jsonify({"success": False, "error": exc.reason}), 400
        except Exception:  # noqa: BLE001
            print("Failed to start interpretation task")
            return (
                jsonify({"success": False, "error": "analysis_start_failed"}),
                500,
            )

    @app.route("/api/paper/analyze/active", methods=["GET"])
    def api_get_active_analysis():
        """Get all ongoing interpretation tasks"""
        if app.extensions.get("processing"):
            tasks=app.extensions["processing"].pipeline().jobs.list()
            return jsonify(success=True,tasks=[{"task_id":j["id"],"paper_id":j["paper_id"],"status":j["status"],"step":j["stage"],"start_time":j["created_at"]} for j in tasks if j["kind"] in {"overview","interpretation"} and j["status"] in {"queued","running","cancelling"}])
        with analysis_tasks_lock:
            active_tasks = []
            for task_id, task_info in analysis_tasks.items():
                if (
                    task_info.get("owner_id") == current_user_id()
                    and task_info["status"] in ["queued", "running"]
                ):
                    active_tasks.append(
                        {
                            "task_id": task_id,
                            "paper_id": task_info["paper_id"],
                            "status": task_info["status"],
                            "step": task_info.get("step"),
                            "start_time": task_info["start_time"],
                        }
                    )
            return jsonify({"success": True, "tasks": active_tasks})

    @app.route("/api/paper/analyze/<task_id>/logs", methods=["GET"])
    def api_get_analysis_logs(task_id):
        """Get the log of the interpretation task"""
        if app.extensions.get("processing"):
            from ipaper.processing.common import ProcessingError
            try:
                jobs=app.extensions["processing"].pipeline().jobs
                job=jobs.get(task_id)
                if job["kind"] not in {"overview","interpretation"}:raise ProcessingError("task_not_found",404)
                return jsonify(success=True,status=job["status"],step=job["stage"],progress=int(job["completed"]*100/max(1,job["total"])),
                               logs=[event["kind"] for event in jobs.events(task_id)],result={"success":job["status"]=="completed","error":job["error"]})
            except ProcessingError as exc:return jsonify(error=exc.code),exc.status
        with analysis_tasks_lock:
            if (
                task_id not in analysis_tasks
                or analysis_tasks[task_id].get("owner_id") != current_user_id()
            ):
                return jsonify({"success": False, "error": "Task does not exist"}), 404

            task_info = analysis_tasks[task_id]
            with task_info["log_lock"]:
                logs = task_info["logs"].copy()
                progress = int(task_info.get("progress") or 0)

            return jsonify(
                {
                    "success": True,
                    "status": task_info["status"],
                    "step": task_info.get("step"),
                    "progress": max(0, min(100, progress)),
                    "logs": logs,
                    "start_time": task_info["start_time"],
                    "result": task_info.get("result"),
                }
            )

    @app.route("/api/paper/analyze/<task_id>/cancel", methods=["POST"])
    def api_cancel_analysis(task_id):
        """Cancel interpretation task"""
        if app.extensions.get("processing"):
            from ipaper.processing.common import ProcessingError
            try:
                jobs=app.extensions["processing"].pipeline().jobs
                if jobs.get(task_id)["kind"] not in {"overview","interpretation"}:raise ProcessingError("task_not_found",404)
                jobs.cancel(task_id)
                return jsonify(success=True)
            except ProcessingError as exc:return jsonify(error=exc.code),exc.status
        with analysis_tasks_lock:
            if (
                task_id not in analysis_tasks
                or analysis_tasks[task_id].get("owner_id") != current_user_id()
            ):
                return jsonify({"success": False, "error": "Task does not exist"}), 404

            task_info = analysis_tasks[task_id]

            if task_info["status"] in ["completed", "failed", "cancelled"]:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "The task has ended and cannot be canceled",
                        }
                    ),
                    400,
                )

            process = task_info.get("process")
            future = task_info.get("future")
            if future is not None:
                future.cancel()
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, 15)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, 9)
                    process.wait()
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed to terminate process: {exc}")

            task_info["status"] = "cancelled"
            task_info["result"] = {"success": False, "error": "Interpretation canceled"}

            return jsonify(
                {"success": True, "message": "Interpretation task has been canceled"}
            )

    @app.route("/api/paper/<paper_id>/analysis/result")
    def api_get_analysis_result(paper_id):
        """Get interpretation result file"""
        if app.extensions.get("processing"):
            from ipaper.processing.common import ProcessingError
            try:
                target=app.extensions["processing"].understanding()
                rows,heads=target.rows(paper_id)
                rid=heads.get("interpretation")
                if rid:
                    body=target.files.body(rid)
                    return jsonify(success=True,content=body["markdown"],resultId=rid,title="论文深度解读")
            except ProcessingError as exc:return jsonify(error=exc.code),exc.status
        # First try from paper_store Find papers in（support _ReadingListTemp Table of contents）
        entry = paper_store.get_entry(paper_id)
        if entry:
            paper = entry.paper
        else:
            # if paper_store Not found in , use recursive search of classification tree
            categories = get_categories()

            def search_paper_recursive(node):
                category_path = get_category_path(categories, node["id"])
                if category_path:
                    papers = get_papers_in_category(node["id"], category_path)
                    for paper in papers:
                        if paper.id == paper_id:
                            return paper, category_path

                if "children" in node:
                    for child in node["children"]:
                        result = search_paper_recursive(child)
                        if result:
                            return result

                return None

            result = None
            for child in categories.get("children", []):
                result = search_paper_recursive(child)
                if result:
                    break

            if not result:
                return jsonify({"error": "Paper not found"}), 404

            paper, _ = result
        try:
            pdf_path = resolve_paper_file(paper)
            result_file = paper_asset_paths(upload_folder, pdf_path).analysis_result
        except PathSecurityError:
            return jsonify({"error": "unsafe_stored_path"}), 409

        if not result_file.exists():
            return jsonify({"error": "Interpretation results file does not exist"}), 404

        try:
            with result_file.open("r", encoding="utf-8") as f:
                content = f.read()
            return jsonify(
                {
                    "success": True,
                    "content": content,
                    "title": paper.title if paper else "Paper Analysis",
                }
            )
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"Failed to read result file: {str(exc)}"}), 500

    @app.route("/api/paper/<paper_id>/analysis/image")
    def api_get_analysis_image(paper_id):
        """Get pictures from interpretation results"""
        # First try from paper_store Find papers in（support _ReadingListTemp Table of contents）
        entry = paper_store.get_entry(paper_id)
        if entry:
            paper = entry.paper
        else:
            # if paper_store Not found in , use recursive search of classification tree
            categories = get_categories()

            def search_paper_recursive(node):
                category_path = get_category_path(categories, node["id"])
                if category_path:
                    papers = get_papers_in_category(node["id"], category_path)
                    for paper in papers:
                        if paper.id == paper_id:
                            return paper, category_path

                if "children" in node:
                    for child in node["children"]:
                        result = search_paper_recursive(child)
                        if result:
                            return result

                return None

            result = None
            for child in categories.get("children", []):
                result = search_paper_recursive(child)
                if result:
                    break

            if not result:
                return jsonify({"error": "Paper not found"}), 404

            paper, _ = result
        try:
            pdf_path = resolve_paper_file(paper)
            analysis_dir = paper_asset_paths(
                upload_folder,
                pdf_path,
            ).analysis_directory
        except PathSecurityError:
            return jsonify({"error": "unsafe_stored_path"}), 409

        image_path = (request.args.get("path") or "").strip()
        if not image_path:
            return jsonify({"error": "Image path not provided"}), 400

        normalized = image_path.replace("\\", "/")
        normalized = posixpath.normpath(normalized).lstrip("/")
        if not normalized or normalized == ".":
            return jsonify({"error": "Invalid image path"}), 400
        parts = [p for p in normalized.split("/") if p]
        if any(p == ".." for p in parts):
            return jsonify({"error": "Invalid image path"}), 400

        try:
            image_file = safe_join(
                analysis_dir,
                normalized,
                must_exist=True,
                require_file=True,
            )
            image_file = ensure_confined(
                upload_folder,
                image_file,
                must_exist=True,
                require_file=True,
            )
        except PathSecurityError:
            return jsonify({"error": "Image file does not exist"}), 404

        mime_type, _ = mimetypes.guess_type(image_file)
        return send_file(
            str(image_file),
            mimetype=mime_type or "application/octet-stream",
        )
