from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple

from flask import Flask, jsonify, request, send_file

from ipaper.timeutil import epoch_seconds, now_utc, today_app, utc_iso
from ipaper.core.base_paper import Paper, PaperUpdateError
from ipaper.core.paper_store import PaperStore
from ipaper.database.dao.user_data_dao import ReadingListDAO, ReadingHistoryDAO
from ipaper.database.dao.document_job_dao import DocumentJobDAO
from ipaper.document_worker.client import DocumentWorkerClient
from ipaper.security.paths import (
    PathSecurityError,
    ensure_confined,
    ensure_confined_tree,
    paper_asset_paths,
    paper_path,
    paper_directory,
    verified_paper_path,
)
from ipaper.security.identity import current_user_id
from ipaper.tools.basic_tools.paper_repository import scan_papers_in_directory




class GetCategoriesFn(Protocol):
    def __call__(self) -> Dict[str, Any]: ...


class GetCategoryPathFn(Protocol):
    def __call__(
        self,
        categories: Dict[str, Any],
        category_id: str,
        path: Optional[List[str]] = None,
    ) -> Optional[List[str]]: ...


class FindCategoryNodeFn(Protocol):
    def __call__(
        self,
        categories: Dict[str, Any],
        category_id: str,
    ) -> Optional[Dict[str, Any]]: ...


class GetPapersInCategoryFn(Protocol):
    def __call__(self, category_id: str, category_path: List[str]) -> List[Paper]: ...


class SavePaperMetadataFn(Protocol):
    def __call__(self, pdf_path: str, paper: Paper) -> None: ...


class DeletePaperFilesFn(Protocol):
    def __call__(self, pdf_path: str) -> None: ...


class AddToReadingListFn(Protocol):
    def __call__(self, paper_id: str) -> None: ...


class RemoveFromReadingListFn(Protocol):
    def __call__(self, paper_id: str) -> None: ...


def register_paper_operation_routes(
    app: Flask,
    *,
    get_categories: GetCategoriesFn,
    get_category_path: GetCategoryPathFn,
    find_category_node: FindCategoryNodeFn,
    get_papers_in_category: GetPapersInCategoryFn,
    save_paper_metadata: SavePaperMetadataFn,
    delete_paper_files: DeletePaperFilesFn,
    extract_pdf_metadata: Optional[Any],  # No longer used, reserved for compatibility
    search_arxiv_by_title: Optional[Any],  # No longer used, reserved for compatibility
    reading_list_file: str,
    upload_folder: str,
    paper_store: PaperStore,
    document_client: DocumentWorkerClient | None = None,
) -> None:
    document_client = document_client or DocumentWorkerClient()
    reading_list_scan_mtime_by_owner: dict[str, float] = {}

    def load_reading_list() -> List[str]:
        items = ReadingListDAO.get_list()
        return [item['paper_id'] for item in items]

    def save_reading_list(paper_ids: List[str]) -> None:
        pass

    def add_to_reading_list(paper_id: str) -> None:
        ReadingListDAO.add_item(paper_id, utc_iso())

    def remove_from_reading_list(paper_id: str) -> None:
        ReadingListDAO.remove_item(paper_id)

    def is_in_reading_list(paper_id: str) -> bool:
        return paper_id in load_reading_list()

    def ensure_library_loaded() -> None:
        """Load the current owner's library without a legacy UI warm-up request."""
        categories = get_categories()
        pending = [categories]
        seen: set[str] = set()
        while pending:
            node = pending.pop()
            category_id = node.get("id")
            if category_id and category_id not in seen:
                seen.add(category_id)
                path = get_category_path(categories, category_id)
                if path:
                    get_papers_in_category(category_id, path)
                pending.extend(node.get("children", []))
        # Reading List imports live outside the category tree. Merely load their
        # existing assets; do not create tasks or change Reading List membership.
        get_papers_in_category("reading_list_temp", ["Root", "_ReadingListTemp"])

    def find_paper(paper_id: str) -> Optional[Tuple[Paper, List[str], str]]:
        entry = paper_store.get_entry(paper_id)
        if not entry:
            ensure_library_loaded()
            entry = paper_store.get_entry(paper_id)
        if not entry:
            return None
        return entry.paper, list(entry.category_path), entry.category_id

    def resolve_paper_file(
        paper: Paper,
        category_id: str,
        *,
        must_exist: bool = True,
    ) -> str:
        return str(
            verified_paper_path(
                upload_folder,
                category_id,
                paper.filename,
                paper.file_path,
                must_exist=must_exist,
            )
        )

    def unsafe_stored_path_response():
        return jsonify({"success": False, "error": "unsafe_stored_path"}), 409

    def move_asset_bundle(source_assets, target_assets) -> None:
        """Preflight and move one paper's exact asset set within storage."""
        pairs = (
            (source_assets.pdf, target_assets.pdf),
            (source_assets.metadata, target_assets.metadata),
            (source_assets.chinese_dual, target_assets.chinese_dual),
            (source_assets.chinese_mono, target_assets.chinese_mono),
            (source_assets.translation_log, target_assets.translation_log),
        )
        moves = []
        for source, target in pairs:
            if source.exists():
                safe_source = ensure_confined(
                    upload_folder,
                    source,
                    must_exist=True,
                    require_file=True,
                )
                safe_target = ensure_confined(upload_folder, target)
                if safe_target.exists():
                    raise FileExistsError("paper asset destination already exists")
                moves.append((safe_source, safe_target))

        analysis_move = None
        if source_assets.analysis_directory.exists():
            safe_source = ensure_confined_tree(
                upload_folder,
                source_assets.analysis_directory,
            )
            safe_target = ensure_confined(
                upload_folder,
                target_assets.analysis_directory,
            )
            if safe_target.exists():
                raise FileExistsError("paper analysis destination already exists")
            analysis_move = (safe_source, safe_target)

        for source, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        if analysis_move:
            source, target = analysis_move
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))

    def collect_papers_by_ids(paper_ids: Iterable[str]) -> List[Paper]:
        ordered_ids = list(paper_ids)
        collected: List[Paper] = []
        seen: set[str] = set()
        for pid in ordered_ids:
            if pid in seen:
                continue
            paper = paper_store.get(pid)
            if paper:
                collected.append(paper)
                seen.add(pid)
        return collected

    @app.route("/api/papers/all")
    def api_all_papers():
        """Get all papers, sorted by upload date in descending order"""
        ensure_library_loaded()
        all_papers = paper_store.iter_all()
        # Sort by upload date in descending order (newest first)
        sorted_papers = sorted(
            all_papers, key=lambda p: p.upload_date or "", reverse=True
        )
        return jsonify([paper.to_dict() for paper in sorted_papers])

    @app.route("/api/papers/<category_id>")
    def api_papers(category_id: str):
        categories = get_categories()
        category_path = get_category_path(categories, category_id)

        if not category_path:
            return jsonify({"error": "Category not found"}), 404

        papers = get_papers_in_category(category_id, category_path)
        return jsonify([paper.to_dict() for paper in papers])

    @app.route("/api/papers/<category_id>/recursive")
    def api_papers_recursive(category_id: str):
        """Recursively obtain papers under a category and all its subcategories (for first-level directories/Project）"""
        categories = get_categories()
        category_node = find_category_node(categories, category_id)

        if not category_node:
            return jsonify({"error": "Category not found"}), 404

        def collect_papers_recursive(node: Dict[str, Any]) -> List[Any]:
            """Recursively collect all papers under a category and its subcategories"""
            all_papers = []

            # Get papers in the current category
            node_path = get_category_path(categories, node["id"])
            if node_path:
                node_papers = get_papers_in_category(node["id"], node_path)
                all_papers.extend(node_papers)

            # Recursively process subcategories
            for child in node.get("children", []):
                all_papers.extend(collect_papers_recursive(child))

            return all_papers

        all_papers = collect_papers_recursive(category_node)

        # Sort by upload time (newest first)
        sorted_papers = sorted(
            all_papers, key=lambda p: p.upload_date or "", reverse=True
        )

        return jsonify([paper.to_dict() for paper in sorted_papers])

    @app.route("/api/paper/<paper_id>")
    def api_paper_info(paper_id: str):
        result = find_paper(paper_id)
        if result:
            paper, _, _ = result
            return jsonify(paper.to_dict())
        return jsonify({"error": "Paper not found"}), 404

    @app.route("/api/paper/<paper_id>/move", methods=["PUT"])
    def api_move_paper(paper_id: str):
        from ipaper.database.dao.paper_dao import PaperDAO
        if not PaperDAO.get_paper(paper_id):
            return jsonify(error="paper_not_found"), 404
        return jsonify(error="physical_category_move_retired", message="请使用研究主题加入或移出论文；文件位置保持不变。"), 410

    @app.route("/api/paper/<paper_id>/file")
    def api_get_paper_file(paper_id: str):
        result = find_paper(paper_id)
        if not result:
            return jsonify({"error": "Paper not found"}), 404

        paper, _, category_id = result
        try:
            # Validate confinement/mismatch before classifying an absent file.
            resolve_paper_file(paper, category_id, must_exist=False)
            file_path = resolve_paper_file(paper, category_id)
        except PathSecurityError as exc:
            if str(exc) == "missing_path":
                return jsonify({"error": "PDF file not found"}), 404
            return unsafe_stored_path_response()

        try:
            response = send_file(
                file_path,
                as_attachment=False,
                mimetype="application/pdf",
            )
        except FileNotFoundError:
            return jsonify({"error": "PDF file not found"}), 404
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    @app.route("/api/paper/<paper_id>", methods=["DELETE"])
    def api_delete_paper(paper_id: str):
        try:
            result = find_paper(paper_id)
            if not result:
                return jsonify({"error": "Paper not found"}), 404

            paper, _, category_id = result
            try:
                file_path = resolve_paper_file(paper, category_id)
            except PathSecurityError:
                return unsafe_stored_path_response()
            delete_paper_files(file_path)
            paper_store.remove(paper_id)

            return jsonify(
                {
                    "success": True,
                    "message": "Paper deleted successfully",
                    "paper": paper.to_dict(),
                    "category_id": category_id,
                }
            )

        except Exception as exc:  # noqa: BLE001
            print(f"Failed to delete paper: {exc}")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/paper/<paper_id>", methods=["PUT"])
    def api_update_paper(paper_id: str):
        try:
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify({"error": "invalid_payload"}), 400
            result = find_paper(paper_id)
            if not result:
                return jsonify({"error": "Paper not found"}), 404

            paper, category_path, category_id = result

            # Validate on a detached object. A failed transaction must not alter
            # the live in-memory entry shown by another request.
            checked = Paper.from_dict(paper.to_dict())
            try:
                checked.update_user_fields(data)
            except PaperUpdateError as exc:
                return jsonify(error=exc.reason, fields=list(exc.fields)), 400
            from ipaper.metadata.model import LEGACY, MetadataError
            from ipaper.metadata.store import MetadataStore
            from ipaper.database.connection import DB_PATH
            from ipaper.database.dao.paper_dao import PaperDAO
            metadata = {k:v for k,v in data.items() if k in LEGACY}
            try:
                state = {k:v for k,v in data.items() if k not in LEGACY}
                if metadata:
                    MetadataStore(DB_PATH, current_user_id()).edit(paper_id, metadata,state_changes=state)
                    saved = PaperDAO.get_paper(paper_id)
                else:
                    saved = PaperDAO.patch_state(paper_id,state) if state else PaperDAO.get_paper(paper_id)
            except MetadataError as exc:
                return jsonify(error=exc.code),exc.status
            paper = paper_store.upsert(Paper.from_dict(saved),category_id=category_id,category_path=category_path)

            return jsonify(
                {
                    "success": True,
                    "message": "Paper updated successfully",
                    "paper": paper.to_dict(),
                    "auto_refresh_triggered": False,
                }
            )

        except Exception as exc:  # noqa: BLE001
            print(f"Failed to update paper: {exc}")
            return jsonify({"success": False, "error": "metadata_storage_failed"}), 503

    @app.route("/api/reading-list", methods=["GET"])
    def api_get_reading_list():
        paper_ids = load_reading_list()

        # Auto-sync: scan _ReadingListTemp directory
        # Optimization: Only scan if the directory has been modified
        reading_list_temp_path = str(paper_directory(upload_folder, "reading_list_temp"))
        should_scan = False
        
        if os.path.exists(reading_list_temp_path):
            try:
                # Check directory mtime
                mtime = os.path.getmtime(reading_list_temp_path)
                
                owner_id = current_user_id()
                if mtime > reading_list_scan_mtime_by_owner.get(owner_id, 0):
                    should_scan = True
                    reading_list_scan_mtime_by_owner[owner_id] = mtime
            except Exception:
                # If checking mtime fails, default to scanning (safer)
                should_scan = True

        if should_scan and os.path.exists(reading_list_temp_path):
            # Scan all papers in the directory
            temp_papers = scan_papers_in_directory(
                reading_list_temp_path,
                category_id="reading_list_temp",
                category_path=["Root", "_ReadingListTemp"],
            )

            # Add papers from _ReadingListTemp to the reading list (if not already present)
            for paper in temp_papers:
                if paper.id not in paper_ids:
                    add_to_reading_list(paper.id)
                    paper_ids.append(paper.id)

        # Return all reading list papers
        papers = collect_papers_by_ids(paper_ids)
        return jsonify([paper.to_dict() for paper in papers])

    @app.route("/api/reading-list/<paper_id>/add", methods=["POST"])
    def api_add_to_reading_list(paper_id: str):
        result = find_paper(paper_id)
        if not result:
            return jsonify({"success": False, "error": "Paper not found"}), 404

        add_to_reading_list(paper_id)
        return jsonify({"success": True})

    @app.route("/api/reading-list/<paper_id>/remove", methods=["POST"])
    def api_remove_from_reading_list(paper_id: str):
        if not is_in_reading_list(paper_id):
            return (
                jsonify({"success": False, "error": "Paper not in reading list"}),
                404,
            )

        # Get paper information
        result = find_paper(paper_id)
        if not result:
            return jsonify({"success": False, "error": "Paper not found"}), 404

        paper, category_path, category_id = result

        # Check if it is still in the temporary directory used by the reading list.
        # We require BOTH the category information and the actual file path to match
        # the temp directory, to avoid accidentally deleting papers that have already
        # been moved into a normal category.
        is_temp_category = (
            category_id == "reading_list_temp"
            or (
                category_path
                and len(category_path) > 1
                and category_path[1] == "_ReadingListTemp"
            )
        )
        try:
            verified_file_path = resolve_paper_file(paper, category_id)
        except PathSecurityError:
            return unsafe_stored_path_response()
        is_in_temp_dir = category_id == "reading_list_temp"
        is_in_temp = is_temp_category and is_in_temp_dir

        # Get delete options
        data = request.json or {}
        delete_files = data.get("delete_files", False)

        # If it is in the temporary directory, the user is required to confirm the deletion of the file (regardless of the source)
        if is_in_temp and not delete_files:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Need to confirm deletion",
                        "requires_confirmation": True,
                        "message": "The paper has not been moved to a certain directory. Do you want to delete the paper file?",
                    }
                ),
                200,
            )  # return 200 for front-end processing

        # If file deletion is confirmed, delete the paper and its related files
        # As long as temp Delete files from directory
        if delete_files and is_in_temp:
            delete_paper_files(verified_file_path)
            paper_store.remove(paper_id)
        elif not is_in_temp:
            # if not temp Directory, only removed from the to-read list, files are not deleted
            # The paper remains in its original catalog
            pass

        # Remove from to-read list
        remove_from_reading_list(paper_id)

        # Returns whether the file was deleted (the file is only deleted when it is in the temporary directory and the user confirms the deletion)
        return jsonify({"success": True, "deleted_files": delete_files and is_in_temp})

    @app.route("/api/paper/<paper_id>/read-time", methods=["POST"])
    def api_record_read_time(paper_id: str):
        """Record effective reading time for one visible, focused reader tick.

        The client sends the interval it measured plus a per-tick id. The server
        validates ownership and bounds, splits the interval at UTC+8 midnight so
        a session that crosses midnight is credited to both days, and keeps the
        per-event table and the legacy day aggregate in step. ``tick_id`` makes
        a retried request idempotent.
        """
        try:
            from ipaper.database.dao.settings_dao import SettingsDAO
            from ipaper.timeutil import split_interval_by_app_day

            data = request.json or {}
            tick_id = data.get("tick_id")
            if tick_id is not None and (
                not isinstance(tick_id, str) or not (1 <= len(tick_id) <= 64)
            ):
                return jsonify({"success": False, "error": "invalid_tick_id"}), 400

            seconds = data.get("seconds")
            if seconds is None:
                seconds = data.get("increment", 0)
            if not isinstance(seconds, (int, float)):
                return jsonify({"success": False, "error": "invalid_seconds"}), 400
            seconds = float(seconds)
            # The reader ticks every 30s; anything outside this bound is either a
            # clock problem or an attempt to inflate the meter.
            if seconds <= 0 or seconds > 300:
                return jsonify({"success": False, "error": "invalid_seconds"}), 400

            ended_at = data.get("ended_at")
            if ended_at is None:
                end = now_utc()
            else:
                try:
                    end = datetime.fromtimestamp(float(ended_at), timezone.utc)
                except (TypeError, ValueError, OSError):
                    return jsonify({"success": False, "error": "invalid_ended_at"}), 400
            if end > now_utc() + timedelta(minutes=5):
                return jsonify({"success": False, "error": "invalid_ended_at"}), 400
            start = end - timedelta(seconds=seconds)

            result = find_paper(paper_id)
            if not result:
                return jsonify({"success": False, "error": "Paper not found"}), 404

            paper, category_path, category_id = result

            already_recorded = bool(tick_id) and ReadingHistoryDAO.has_tick(tick_id)

            from ipaper.database.dao.paper_dao import PaperDAO

            if not already_recorded:
                saved = PaperDAO.patch_state(
                    paper_id, {}, increments={"read_time": int(round(seconds))}
                )
                paper = paper_store.upsert(
                    Paper.from_dict(saved),
                    category_id=category_id,
                    category_path=category_path,
                )
                buckets = split_interval_by_app_day(start, end)
                for date_str, minutes in buckets:
                    ReadingHistoryDAO.add_history(
                        date_str,
                        minutes * 60.0,
                        paper_id,
                        int(end.timestamp()),
                        tick_id=tick_id,
                        source="reader",
                    )
                # Keep the legacy day aggregate aligned with the event rows.
                if buckets:
                    history = SettingsDAO.get_setting("reading_history", {}) or {}
                    for date_str, minutes in buckets:
                        entry = history.get(date_str)
                        if isinstance(entry, dict):
                            entry["total"] = float(entry.get("total", 0)) + minutes
                            papers = entry.setdefault("papers", [])
                            if paper_id not in papers:
                                papers.append(paper_id)
                        elif isinstance(entry, (int, float)):
                            history[date_str] = {
                                "total": float(entry) + minutes,
                                "papers": [paper_id],
                            }
                        else:
                            history[date_str] = {"total": minutes, "papers": [paper_id]}
                    SettingsDAO.save_setting("reading_history", history)
            else:
                paper = paper_store.upsert(
                    Paper.from_dict(PaperDAO.get_paper(paper_id)),
                    category_id=category_id,
                    category_path=category_path,
                )

            return jsonify(
                {
                    "success": True,
                    "read_time": paper.read_time,
                    "duplicate": already_recorded,
                }
            )

        except Exception as exc:  # noqa: BLE001
            print(f"Failed to record reading time: {exc}")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/paper/<paper_id>/analysis-view-time", methods=["POST"])
    def api_record_analysis_view_time(paper_id: str):
        """Record AI Interpretation reading time (cumulative increments)"""
        try:
            data = request.json or {}
            # Use incremental mode
            increment = data.get("increment", 0)

            if not isinstance(increment, (int, float)) or increment <= 0:
                return jsonify({"success": True, "analysis_view_time": 0}), 200

            result = find_paper(paper_id)
            if not result:
                return jsonify({"success": False, "error": "Paper not found"}), 404

            paper, category_path, category_id = result

            from ipaper.database.dao.paper_dao import PaperDAO
            saved = PaperDAO.patch_state(paper_id, {}, increments={'analysis_view_time': int(increment)})
            paper = paper_store.upsert(Paper.from_dict(saved), category_id=category_id, category_path=category_path)

            return jsonify(
                {"success": True, "analysis_view_time": paper.analysis_view_time}
            )

        except Exception as exc:  # noqa: BLE001
            print(f"Record interpretation reading time failed: {exc}")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/paper/<paper_id>/refresh-metadata", methods=["POST"])
    def api_refresh_paper_metadata(paper_id: str):
        from ipaper.metadata.model import MetadataError
        service = app.extensions.get("metadata")
        if service is None:
            return jsonify(error="metadata_unavailable"),503
        try:
            task_id=service.store().create([paper_id])
            service.wake.set()
            return jsonify(success=True, task_id=task_id, paper_id=paper_id, status="queued"),202
        except MetadataError as exc:
            return jsonify(error=exc.code),exc.status
