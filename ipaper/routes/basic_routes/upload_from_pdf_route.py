from __future__ import annotations

import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Protocol

from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

from ipaper.timeutil import utc_iso
from ipaper.core.base_paper import Paper
from ipaper.core.paper_store import PaperStore
from ipaper.database.dao.user_data_dao import ReadingListDAO
from ipaper.database.dao.document_job_dao import DocumentJobDAO
from ipaper.document_worker.client import (
    DocumentWorkerClient,
    DocumentWorkerRejected,
    DocumentWorkerUnavailable,
)
from ipaper.document_worker.safety import DocumentLimitError, bounded_copy
from ipaper.security.identity import current_identity, run_as_identity
from ipaper.security.paths import safe_join


_document_monitors = ThreadPoolExecutor(max_workers=2, thread_name_prefix="document-monitor")


class GetCategoriesFn(Protocol):
    def __call__(self) -> Dict[str, Any]: ...


class GetCategoryPathFn(Protocol):
    def __call__(
        self,
        categories: Dict[str, Any],
        category_id: str,
        path: Optional[list[str]] = None,
    ) -> Optional[list[str]]: ...


class CreateCategoryFolderFn(Protocol):
    def __call__(self, category_id: str) -> str: ...


class SavePaperMetadataFn(Protocol):
    def __call__(self, pdf_path: str, paper: Paper) -> None: ...


def _clean_filename(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = text
    cleaned = re.sub(r'[<>:"/\\|?*]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 100:
        cleaned = cleaned[:100] + "..."
    return cleaned or None


def register_upload_from_pdf_routes(
    app: Flask,
    *,
    get_categories: GetCategoriesFn,
    get_category_path: GetCategoryPathFn,
    create_category_folder: CreateCategoryFolderFn,
    save_paper_metadata: SavePaperMetadataFn,
    reading_list_file: str,
    paper_store: PaperStore,
    document_client: DocumentWorkerClient | None = None,
) -> None:
    document_client = document_client or DocumentWorkerClient()
    def _add_to_reading_list(paper_id: str) -> None:
        ReadingListDAO.add_item(paper_id, utc_iso())

    def _complete_pdf_job(
        task_id: str,
        original_filename: str,
        category_id: str,
        category_path: list[str],
        category_folder: str,
        topic_ids: list[str],
    ) -> None:
        try:
            while True:
                state = document_client.get(task_id)
                if state["status"] in {"completed", "failed", "cancelled"}:
                    break
                DocumentJobDAO.update(
                    task_id,
                    state["status"],
                    progress=max(0, min(99, int(state.get("progress") or 0))),
                    error=state.get("error"),
                )
                threading.Event().wait(0.5)
            if state["status"] != "completed":
                DocumentJobDAO.update(
                    task_id,
                    state["status"],
                    progress=max(0, min(100, int(state.get("progress") or 0))),
                    error=state.get("error"),
                )
                return
            result = document_client.result_json(task_id)
            metadata = result.get("metadata") if isinstance(result, dict) else {}
            if not isinstance(metadata, dict):
                metadata = {}
            title = _clean_filename(str(metadata.get("title") or ""))
            base_title = title or os.path.splitext(original_filename)[0]
            filename = secure_filename(f"{base_title}.pdf") or f"{uuid.uuid4()}.pdf"
            target = safe_join(category_folder, filename)
            counter = 1
            while target.exists():
                stem, extension = os.path.splitext(filename)
                target = safe_join(category_folder, f"{stem}_{counter}{extension}")
                counter += 1
            source = document_client.job_directory(task_id) / "work" / "input.pdf"
            temporary = target.with_name(f".{target.name}.{task_id}.tmp")
            with source.open("rb") as reader:
                bounded_copy(reader, temporary, document_client.limits.max_pdf_bytes)
            os.chmod(temporary, 0o660)
            os.replace(temporary, target)
            paper_id = str(uuid.uuid4())
            paper = Paper.from_dict({
                "id": paper_id,
                "filename": target.name,
                "original_filename": original_filename,
                "file_path": str(target),
                "upload_date": utc_iso(),
                "title": title or os.path.splitext(original_filename)[0],
                "authors": str(metadata.get("author") or "")[:4096],
                "subject": str(metadata.get("subject") or "")[:4096],
                "keywords": str(metadata.get("keywords") or "")[:4096],
                "notes": "",
                "starred": False,
            })
            paper.extra["_metadata_inspection"] = result
            paper.extra["category_id"] = category_id
            paper.extra["_topic_ids"] = topic_ids
            save_paper_metadata(str(target), paper)
            registered = paper_store.upsert(
                paper, category_id=category_id, category_path=category_path
            )
            _add_to_reading_list(registered.id)
            DocumentJobDAO.update(task_id, "completed", progress=100, paper_id=registered.id)
            document_client.cleanup(task_id)
        except DocumentWorkerUnavailable:
            DocumentJobDAO.update(task_id, "failed", error="document_worker_unavailable")
        except Exception:
            DocumentJobDAO.update(task_id, "failed", error="document_processing_failed")

    @app.route("/api/upload", methods=["POST"])
    def api_upload():
        if "file" not in request.files:
            return jsonify({"success": False, "error": "No file provided"})

        file = request.files["file"]
        category_id = request.form.get("category_id")
        from ipaper.topics.admission import validated_ids
        from ipaper.topics.store import TopicError
        try:
            topic_ids = validated_ids(request.form.get("topicIds"))
        except TopicError as error:
            return jsonify(error=error.code), error.status

        if file.filename == "":
            return jsonify({"success": False, "error": "No file selected"})

        if not file.filename.lower().endswith(".pdf"):
            return jsonify({"success": False, "error": "Only PDF files are allowed"})

        if not document_client.health():
            return jsonify({"success": False, "error": "document_worker_unavailable"}), 503

        categories = get_categories()

        # Special handling: To-be-read listcategory_id
        if category_id == "reading_list_temp":
            category_path = ["Root", "_ReadingListTemp"]
        else:
            category_path = get_category_path(categories, category_id)
            if not category_path:
                return jsonify({"success": False, "error": "Category not found"})

        category_folder = create_category_folder(category_id)
        original_filename = file.filename
        if not secure_filename(original_filename):
            return jsonify({"success": False, "error": "Invalid file name"}), 400
        task_id = str(uuid.uuid4())
        try:
            document_client.stage(task_id, "pdf_inspect", file.stream)
            DocumentJobDAO.create(task_id, "pdf_inspect")
            document_client.create(task_id, "pdf_inspect")
        except DocumentLimitError as exc:
            try:
                document_client.cleanup(task_id)
            except Exception:
                pass
            return jsonify({"success": False, "error": exc.reason}), 413
        except DocumentWorkerRejected as exc:
            return jsonify({"success": False, "error": exc.reason}), exc.status_code
        except Exception:
            try:
                document_client.cleanup(task_id)
            except Exception:
                pass
            return jsonify({"success": False, "error": "document_worker_unavailable"}), 503
        identity = current_identity()
        _document_monitors.submit(
            run_as_identity,
            identity,
            _complete_pdf_job,
            task_id,
            original_filename,
            category_id,
            category_path,
            category_folder,
            topic_ids,
        )
        return jsonify({"success": True, "task_id": task_id, "status": "queued"}), 202

    @app.get("/api/upload/<task_id>")
    def api_upload_status(task_id: str):
        job = DocumentJobDAO.get(task_id)
        if not job or job.get("kind") != "pdf_inspect":
            return jsonify({"success": False, "error": "Task does not exist"}), 404
        payload = {
            "success": True,
            "task_id": task_id,
            "status": job["status"],
            "progress": job["progress"],
            "error": job["error"],
        }
        if job.get("paper_id"):
            paper = paper_store.get(job["paper_id"])
            if paper:
                payload["paper"] = paper.to_dict()
        return jsonify(payload)

    @app.delete("/api/upload/<task_id>")
    def api_cancel_upload(task_id: str):
        job = DocumentJobDAO.get(task_id)
        if not job or job.get("kind") != "pdf_inspect":
            return jsonify({"success": False, "error": "Task does not exist"}), 404
        if job["status"] in {"completed", "failed", "cancelled"}:
            return jsonify({"success": True, "status": job["status"]})
        try:
            document_client.cancel(task_id)
            DocumentJobDAO.update(task_id, "cancelled", error="cancelled")
            document_client.cleanup(task_id)
        except DocumentWorkerUnavailable:
            return jsonify({"success": False, "error": "document_worker_unavailable"}), 503
        return jsonify({"success": True, "status": "cancelled"})

    for interrupted in DocumentJobDAO.list_active():
        if interrupted.get("kind") == "pdf_inspect":
            DocumentJobDAO.update(interrupted["job_id"], "failed", error="interrupted")
