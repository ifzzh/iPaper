"""
Zotero RDF Import route
Process from Zotero The function of importing papers
"""

from __future__ import annotations

from ipaper.environment import getenv as brand_getenv

import json
import io
import os
import re
import shutil
import threading
import time
import uuid
from ipaper.runtime.task_queue import BoundedExecutor, QueueFull, ExecutorShuttingDown
from ipaper.security.identity import current_user_id
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Protocol

import requests
from flask import Flask, Response, jsonify, request
from werkzeug.utils import secure_filename

from ipaper.core.base_paper import Paper
from ipaper.core.paper_store import PaperStore
from ipaper.database.dao.document_job_dao import DocumentJobDAO
from ipaper.document_worker.client import (
    DocumentWorkerClient,
    DocumentWorkerRejected,
    DocumentWorkerUnavailable,
)
from ipaper.document_worker.safety import DocumentLimitError, bounded_copy
from ipaper.security.paths import (
    PathSecurityError,
    ensure_confined_tree,
    paper_asset_paths,
    remove_confined_tree,
    safe_join,
    validate_category_name,
)
from ipaper.tools.basic_tools.upload_paper import (
    fetch_paper_by_arxiv_id_fast,
    search_arxiv_by_title_and_author_fast,
)


class GetCategoriesFn(Protocol):
    def __call__(self) -> Dict[str, Any]: ...


class SaveCategoriesFn(Protocol):
    def __call__(self, categories: Dict[str, Any]) -> None: ...


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


# Import task status storage (support disconnection and reconnection)
import_tasks: Dict[str, Dict[str, Any]] = {}
import_tasks_lock = threading.Lock()

# Currently active import tasksID(Only one import task is allowed globally)
current_import_task_id: Optional[str] = None
_import_workers = BoundedExecutor(max_workers=4, max_queue=4, thread_name_prefix="paper-import")


def _extract_arxiv_id_from_url(url: str) -> Optional[str]:
    """from URL extracted from arXiv ID"""
    patterns = [
        r"arxiv\.org/pdf/([\d.]+(?:v\d+)?)",
        r"arxiv\.org/abs/([\d.]+(?:v\d+)?)",
        r"^([\d.]+(?:v\d+)?)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            arxiv_id = match.group(1)
            return arxiv_id
    return None


def _download_arxiv_pdf(arxiv_id: str) -> Optional[tuple[bytes, str]]:
    """download arXiv PDF(Priority to use export.arxiv.org）"""
    # Try first export.arxiv.org
    pdf_urls = [
        f"https://export.arxiv.org/pdf/{arxiv_id}.pdf",
        f"https://arxiv.org/pdf/{arxiv_id}.pdf",
    ]

    for pdf_url in pdf_urls:
        try:
            print(f"[Import] Removing from arXiv download PDF: {pdf_url}")
            response = requests.get(pdf_url, timeout=60, stream=True)
            response.raise_for_status()
            maximum = int(brand_getenv("IPAPER_MAX_PDF_BYTES", str(100 * 1024 * 1024)))
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > maximum:
                raise DocumentLimitError("upload_too_large")
            output = io.BytesIO()
            total = 0
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > maximum:
                    raise DocumentLimitError("upload_too_large")
                output.write(chunk)
            pdf_content = output.getvalue()
            filename = f"{arxiv_id}.pdf"
            print(f"[Import] Successfully downloaded PDF, size: {len(pdf_content)} bytes")
            return pdf_content, filename
        except (requests.exceptions.RequestException, DocumentLimitError, ValueError) as exc:
            print(f"[Import] from {pdf_url} Download failed: {exc}")
            continue

    print(f"[Import] all URL All downloads failed")
    return None


def _clean_filename(text: Optional[str]) -> Optional[str]:
    """Clean up filenames"""
    if not text:
        return None
    cleaned = text
    cleaned = re.sub(r'[<>:"/\\|?*]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 100:
        cleaned = cleaned[:100] + "..."
    return cleaned or None


def _parse_category_path(category_str: str) -> List[str]:
    """
    parse category String is a list of paths

    For example:
    - "Scene Text Recognition" -> ["Scene Text Recognition"]
    - "Multi Modality/LLaVA" -> ["Multi Modality", "LLaVA"]
    - "A; B/C" -> Only take the first longest path ["B", "C"]
    """
    if not category_str:
        return []

    # If there are semicolons, take the first one (longest path)
    parts = category_str.split(";")
    if parts:
        category_str = parts[0].strip()

    # according to / division
    path_parts = [p.strip() for p in category_str.split("/") if p.strip()]
    return path_parts


def _find_or_create_category(
    categories: Dict[str, Any],
    category_path: List[str],
    save_categories: SaveCategoriesFn,
    create_category_folder: CreateCategoryFolderFn,
) -> Optional[str]:
    """
    Find or create a category, return to category ID

    Args:
        categories: Classification tree data
        category_path: Classification path, such as ["Multi Modality", "LLaVA"]
        save_categories: function to save categories
        create_category_folder: Function to create category folders

    Returns:
        Classification ID, return on failure None
    """
    if not category_path:
        return None

    def find_or_create_in_children(
        children: List[Dict], path: List[str], current_path: List[str]
    ) -> Optional[str]:
        if not path:
            return None

        try:
            target_name = validate_category_name(path[0])
        except PathSecurityError:
            return None
        remaining_path = path[1:]

        # Find an existing category
        for child in children:
            if child.get("name") == target_name:
                if remaining_path:
                    # Continue to search for subcategories
                    if "children" not in child:
                        child["children"] = []
                    return find_or_create_in_children(
                        child["children"], remaining_path, current_path + [target_name]
                    )
                else:
                    # Target category found
                    return child.get("id")

        # Not found, create a new category
        new_id = str(uuid.uuid4())
        new_category = {"id": new_id, "name": target_name, "children": []}
        children.append(new_category)

        # Create folder
        try:
            create_category_folder(new_id)
            print(f"[Import] Create category folder for: {target_name}")
        except Exception as e:
            print(f"[Import] Failed to create category folder: {e}")

        if remaining_path:
            # Continue to create subcategories
            return find_or_create_in_children(
                new_category["children"], remaining_path, current_path + [target_name]
            )
        else:
            return new_id

    # Search starting from the root node
    root_children = categories.get("children", [])
    result = find_or_create_in_children(root_children, category_path, [])

    # Save updated categories
    if result:
        save_categories(categories)

    return result


def _find_or_create_category_under_parent(
    categories: Dict[str, Any],
    parent_category_id: str,
    category_path: List[str],
    save_categories: SaveCategoriesFn,
    create_category_folder: CreateCategoryFolderFn,
) -> Optional[str]:
    """
    Search or create a category in the specified parent directory and return the category ID

    For example:parent_category_id correspond "Project A"，category_path for ["Multi Modality", "LLaVA"]
    will create "Project A/Multi Modality/LLaVA"

    Args:
        categories: Classification tree data
        parent_category_id: parent directoryID
        category_path: Classification path, such as ["Multi Modality", "LLaVA"]
        save_categories: function to save categories
        create_category_folder: Function to create category folders

    Returns:
        Classification ID, return on failure None
    """
    if not category_path:
        # if not category_path, return directly to the parent directoryID
        return parent_category_id

    # Find parent directory node
    def find_node_by_id(
        node: Dict[str, Any], target_id: str
    ) -> Optional[Dict[str, Any]]:
        if node.get("id") == target_id:
            return node
        for child in node.get("children", []):
            result = find_node_by_id(child, target_id)
            if result:
                return result
        return None

    parent_node = find_node_by_id(categories, parent_category_id)
    if not parent_node:
        # The parent directory does not exist, fallback to the root directory
        return _find_or_create_category(
            categories, category_path, save_categories, create_category_folder
        )

    # Get the path of the parent directory (used to create folders)
    def get_path_to_node(
        root: Dict[str, Any], target_id: str, path: List[str] = None
    ) -> Optional[List[str]]:
        if path is None:
            path = []
        if root.get("id") == target_id:
            return path + [root.get("name", "")]
        for child in root.get("children", []):
            result = get_path_to_node(child, target_id, path + [root.get("name", "")])
            if result:
                return result
        return None

    parent_path = get_path_to_node(categories, parent_category_id)
    if not parent_path:
        parent_path = []
    else:
        # Remove "Root" if exists
        parent_path = [p for p in parent_path if p and p != "Root"]

    # Find or create categories under parent directory
    def find_or_create_in_children(
        children: List[Dict], path: List[str], current_folder_path: List[str]
    ) -> Optional[str]:
        if not path:
            return None

        try:
            target_name = validate_category_name(path[0])
        except PathSecurityError:
            return None
        remaining_path = path[1:]

        # Find an existing category
        for child in children:
            if child.get("name") == target_name:
                if remaining_path:
                    # Continue to search for subcategories
                    if "children" not in child:
                        child["children"] = []
                    return find_or_create_in_children(
                        child["children"],
                        remaining_path,
                        current_folder_path + [target_name],
                    )
                else:
                    # Target category found
                    return child.get("id")

        # Not found, create a new category
        new_id = str(uuid.uuid4())
        new_category = {"id": new_id, "name": target_name, "children": []}
        children.append(new_category)

        # Create folder
        try:
            create_category_folder(new_id)
            print(f"[Import] Create category folder for: {target_name}")
        except Exception as e:
            print(f"[Import] Failed to create category folder: {e}")

        if remaining_path:
            # Continue to create subcategories
            return find_or_create_in_children(
                new_category["children"],
                remaining_path,
                current_folder_path + [target_name],
            )
        else:
            return new_id

    # Make sure the parent directory has children list
    if "children" not in parent_node:
        parent_node["children"] = []

    # Search from parent directory
    result = find_or_create_in_children(
        parent_node["children"], category_path, parent_path
    )

    # Save updated categories
    if result:
        save_categories(categories)

    return result


def _get_full_category_path(
    categories: Dict[str, Any],
    category_id: str,
    path: Optional[List[str]] = None,
) -> Optional[List[str]]:
    """Get the full path of the category"""
    if path is None:
        path = ["Root"]

    if categories.get("id") == category_id:
        return path + [categories.get("name", "")]

    for child in categories.get("children", []):
        result = _get_full_category_path(
            child, category_id, path + [categories.get("name", "")]
        )
        if result:
            return result

    return None


def register_import_routes(
    app: Flask,
    *,
    get_categories: GetCategoriesFn,
    save_categories: SaveCategoriesFn,
    get_category_path: GetCategoryPathFn,
    create_category_folder: CreateCategoryFolderFn,
    save_paper_metadata: SavePaperMetadataFn,
    reading_list_file: str,
    paper_store: PaperStore,
    upload_folder: str,
    document_client: DocumentWorkerClient | None = None,
) -> None:
    """Register and import related routes"""
    document_client = document_client or DocumentWorkerClient()

    def _run_document_job(task_id: str, kind: str, stream, *, timeout: float) -> dict:
        document_client.stage(task_id, kind, stream)
        DocumentJobDAO.create(task_id, kind)
        document_client.create(task_id, kind)
        state = document_client.wait(task_id, timeout=timeout)
        DocumentJobDAO.update(
            task_id,
            state["status"],
            progress=int(state.get("progress") or 0),
            error=state.get("error"),
        )
        if state["status"] != "completed":
            raise DocumentWorkerRejected(str(state.get("error") or "document_job_failed"), 422)
        return document_client.result_json(task_id) if kind in {"pdf_inspect", "zotero_rdf"} else {}

    def _promote_validated_pdf(data: bytes, category_id: str, filename: str) -> tuple[str, dict]:
        validation_id = str(uuid.uuid4())
        try:
            inspection = _run_document_job(
                validation_id,
                "pdf_inspect",
                io.BytesIO(data),
                timeout=100,
            )
            category_folder = create_category_folder(category_id)
            clean_name = secure_filename(filename) or f"{validation_id}.pdf"
            target = safe_join(category_folder, clean_name)
            stem, extension = os.path.splitext(clean_name)
            counter = 1
            while target.exists():
                target = safe_join(category_folder, f"{stem}_{counter}{extension}")
                counter += 1
            source = document_client.job_directory(validation_id) / "work" / "input.pdf"
            temporary = target.with_name(f".{target.name}.{validation_id}.tmp")
            with source.open("rb") as reader:
                bounded_copy(reader, temporary, document_client.limits.max_pdf_bytes)
            os.chmod(temporary, 0o660)
            os.replace(temporary, target)
            return str(target), inspection
        finally:
            try:
                document_client.cleanup(validation_id)
            except Exception:
                pass

    def _load_reading_list() -> list[str]:
        try:
            with open(reading_list_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("papers", [])
        except Exception:
            return []

    def _save_reading_list(paper_ids: list[str]) -> None:
        try:
            with open(reading_list_file, "w", encoding="utf-8") as f:
                json.dump({"papers": paper_ids}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _add_to_reading_list(paper_id: str) -> None:
        paper_ids = _load_reading_list()
        if paper_id not in paper_ids:
            paper_ids.append(paper_id)
            _save_reading_list(paper_ids)

    def _filter_already_imported_papers(papers_data: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        # A title or unversioned identifier does not establish file identity.
        # Keep imported versions; the owner-scoped metadata duplicate panel
        # offers verified suggestions after safe admission.
        return list(papers_data), 0

    def _update_task_progress(task_id: str, **kwargs):
        """Update task progress (thread safe)"""
        with import_tasks_lock:
            task = import_tasks.get(task_id)
            if task:
                task.update(kwargs)
                task["last_update"] = datetime.now().isoformat()

    def _import_papers_task(
        task_id: str, papers_data: List[Dict[str, Any]], target_category_id: str = "", topic_ids=None
    ):
        """Background import task

        Args:
            task_id: TaskID
            papers_data: Paper data list
            target_category_id: target directoryID, as the parent directory if specified,Zotero The classification structure will be created under
        """
        global current_import_task_id

        with import_tasks_lock:
            task = import_tasks.get(task_id)
            if not task:
                return

        total = len(papers_data)
        success_count = 0
        failed_count = 0
        skipped_count = 0
        duplicate_count = 0  # Number of repeats
        others_count = 0  # Enter Others quantity

        # If a target directory is specified, obtain its information in advance
        parent_category_id = None
        parent_category_path = None
        print(
            f"[Import] Received target directoryID: '{target_category_id}' (type: {type(target_category_id).__name__})"
        )
        if target_category_id:
            categories = get_categories()
            print(f"[Import] Looking for target directory...")
            parent_category_path = get_category_path(categories, target_category_id)
            print(f"[Import] Find results: {parent_category_path}")
            if parent_category_path:
                parent_category_id = target_category_id
                print(
                    f"[Import] ✅ will be in the directory '{'/'.join(parent_category_path[1:])}' Import under, keep Zotero Classification structure"
                )
            else:
                print(
                    f"[Import] ❌ Target directory does not exist: {target_category_id}, will press in the root directory Zotero Classification import"
                )
        else:
            print("[Import] No target directory specified, will press in the root directory Zotero Classification import")

        for idx, paper_data in enumerate(papers_data):
            # Check if canceled
            with import_tasks_lock:
                task = import_tasks.get(task_id)
                if task and task.get("cancelled", False):
                    print(f"[Import] Import task canceled")
                    _update_task_progress(
                        task_id,
                        status="cancelled",
                        progress=int((idx / total) * 100),
                        current=idx,
                        total=total,
                        message="Import canceled",
                        success_count=success_count,
                        failed_count=failed_count,
                        skipped_count=skipped_count,
                        duplicate_count=duplicate_count,
                        others_count=others_count,
                    )
                    current_import_task_id = None
                    return
            
            try:
                # Update progress status
                _update_task_progress(
                    task_id,
                    status="importing",
                    progress=int((idx / total) * 100),
                    current=idx + 1,
                    total=total,
                    message=f"Processing: {paper_data.get('title', 'Unknown title')[:50]}...",
                    success_count=success_count,
                    failed_count=failed_count,
                    skipped_count=skipped_count,
                    duplicate_count=duplicate_count,
                    others_count=others_count,
                )

                # examine category
                category_str = paper_data.get("extra", {}).get(
                    "category"
                ) or paper_data.get("category")

                # parse category path
                if category_str:
                    category_path = _parse_category_path(category_str)
                else:
                    category_path = []

                # Mark whether to enter Others
                is_others = False

                # if not category or category that is "Others", put in Others Table of contents
                if not category_path or (
                    len(category_path) == 1 and category_path[0].lower() == "others"
                ):
                    category_path = ["Others"]
                    is_others = True
                    print(
                        f"[Import] Uncategorized papers, put in Others: {paper_data.get('title', 'unknown')[:50]}"
                    )

                # Find or create a category
                # If a parent directory is specified, create a classification structure under the parent directory
                categories = get_categories()
                if topic_ids is not None:
                    category_id = "root"
                elif parent_category_id:
                    category_id = _find_or_create_category_under_parent(
                        categories,
                        parent_category_id,
                        category_path,
                        save_categories,
                        create_category_folder,
                    )
                else:
                    category_id = _find_or_create_category(
                        categories,
                        category_path,
                        save_categories,
                        create_category_folder,
                    )

                if not category_id:
                    print(f"[Import] Failed to create category: {category_path}")
                    failed_count += 1
                    continue

                # Get the full path to the category (for paper_store）
                categories = get_categories()  # Retrieve updated categories
                full_category_path = get_category_path(categories, category_id)
                if not full_category_path:
                    full_category_path = ["Root"] + category_path

                # try to start from arXiv Get the paper
                arxiv_id = None
                paper_info = None

                # 1. examine URL Is there any arXiv
                url = paper_data.get("extra", {}).get("url") or paper_data.get(
                    "url", ""
                )
                if "arxiv.org" in url.lower():
                    arxiv_id = _extract_arxiv_id_from_url(url)
                    if arxiv_id:
                        print(f"[Import] from URL extract arXiv ID: {arxiv_id}")
                        paper_info = fetch_paper_by_arxiv_id_fast(arxiv_id)

                # 2. if not arXiv URL, use title+Author search
                if not paper_info:
                    title = paper_data.get("title", "")
                    authors = paper_data.get("authors", "")
                    if title:
                        # Construct a search query
                        search_title = f"{title} {authors}" if authors else title
                        print(f"[Import] Search using titles arXiv: {search_title[:50]}...")

                        if authors:
                            paper_info = search_arxiv_by_title_and_author_fast(title, authors)

                        if paper_info:
                            arxiv_id = paper_info.get("arxiv_id")

                # 3. If still not found, skip
                if not paper_info or not arxiv_id:
                    print(
                        f"[Import] Unable to access from arXiv Get the paper: {paper_data.get('title', 'unknown')[:50]}"
                    )
                    skipped_count += 1
                    continue

                # Preserve different files and versions, even with identical titles.
                # download PDF
                pdf_result = _download_arxiv_pdf(arxiv_id)
                if not pdf_result:
                    print(f"[Import] download PDF fail: {arxiv_id}")
                    failed_count += 1
                    continue

                pdf_content, pdf_filename = pdf_result

                # Use the paper title as the file name
                clean_title = _clean_filename(paper_info.get("title"))
                if clean_title:
                    pdf_filename = f"{clean_title}.pdf"
                file_path, inspection = _promote_validated_pdf(pdf_content, category_id, pdf_filename)
                pdf_filename = os.path.basename(file_path)

                # create Paper object
                paper_id = str(uuid.uuid4())
                # build arxiv_url
                arxiv_url = None
                if arxiv_id:
                    arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"

                new_paper = Paper(
                    id=paper_id,
                    filename=pdf_filename,
                    original_filename=pdf_filename,
                    file_path=file_path,
                    upload_date=datetime.now().isoformat(),
                    title=paper_info.get("title", ""),
                    authors=paper_info.get("authors", ""),
                    arxiv_id=arxiv_id,
                    arxiv_url=arxiv_url,
                    arxiv_published_date=paper_info.get("published_date"),
                    year=paper_info.get("year", ""),
                    abstract=paper_info.get("abstract", ""),
                    summary=paper_info.get("summary", ""),
                    bibtex="",
                    notes=paper_data.get("notes", ""),
                    github=None,  # from Zotero When importing,GitHub is empty but the field exists
                    homepage=None,  # from Zotero When importing,Homepage is empty but the field exists
                    upload_source="zotero_import",
                )

                new_paper.extra.update(_metadata_inspection=inspection, category_id=category_id)
                if topic_ids is not None:
                    new_paper.extra.update(_topic_ids=topic_ids, _topic_paths=[category_path])
                save_paper_metadata(file_path, new_paper)
                # Publish only after the paper and metadata task commit.
                registered_paper = paper_store.upsert(
                    new_paper, category_id=category_id, category_path=full_category_path
                )


                # Note: Imported papers are not added to the to-read list

                success_count += 1
                if is_others:
                    others_count += 1
                print(f"[Import] ✅ Imported successfully: {paper_info.get('title', '')[:50]}")

                # Bibliographic follow-up is persisted by PaperDAO admission.

            except Exception as e:
                print(f"[Import] ❌ Failed to import paper: {e}")
                import traceback

                traceback.print_exc()
                failed_count += 1

        # Import completed
        _update_task_progress(
            task_id,
            status="completed",
            progress=100,
            current=total,
            total=total,
            success_count=success_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            duplicate_count=duplicate_count,
            others_count=others_count,
            message="Import completed",
        )

        # Clear current task mark
        current_import_task_id = None

        print(
            f"[Import] Import completed: success {success_count}, fail {failed_count}, jump over {skipped_count}, repeat {duplicate_count}, Others {others_count}"
        )

    def _enqueue_import(function, task_id, *args):
        global current_import_task_id
        try:
            _import_workers.submit(function, task_id, *args)
            return True
        except (QueueFull, ExecutorShuttingDown):
            with import_tasks_lock:
                import_tasks[task_id].update(status="error", message="import_queue_full")
                if current_import_task_id == task_id:
                    current_import_task_id = None
            for action in (document_client.cancel, document_client.cleanup):
                try:
                    action(task_id)
                except (DocumentWorkerRejected, DocumentWorkerUnavailable, OSError):
                    pass
            DocumentJobDAO.update(task_id, "failed", error="import_queue_full")
            return False

    @app.route("/api/import/zotero", methods=["POST"])
    def api_import_zotero():
        """Queue Zotero RDF validation and parsing in the Document Worker."""
        global current_import_task_id
        if "file" not in request.files:
            return jsonify({"success": False, "error": "No document provided"}), 400
        from ipaper.topics.admission import validated_ids
        from ipaper.topics.store import TopicError
        try:
            topic_ids = validated_ids(request.form.get('topicIds')) if 'topicIds' in request.form else None
        except TopicError as error:
            return jsonify(error=error.code), error.status
        file = request.files["file"]
        if file.filename == "":
            return jsonify({"success": False, "error": "No file selected"}), 400
        if not file.filename.lower().endswith(".rdf"):
            return jsonify({"success": False, "error": "Please upload .rdf format file"}), 400
        target_category_id = request.form.get("target_category_id", "").strip()
        if target_category_id and not get_category_path(get_categories(), target_category_id):
            return jsonify(success=False, error="category_not_found"), 404
        if not document_client.health():
            return jsonify({"success": False, "error": "document_worker_unavailable"}), 503
        if current_import_task_id:
            with import_tasks_lock:
                existing = import_tasks.get(current_import_task_id)
                if existing and existing.get("status") not in {"completed", "error", "cancelled"}:
                    return jsonify({
                        "success": False,
                        "error": "There is an import task in progress",
                        "task_id": current_import_task_id if existing.get("owner_id") == current_user_id() else None,
                    }), 409
        task_id = str(uuid.uuid4())
        try:
            document_client.stage(task_id, "zotero_rdf", file.stream)
            DocumentJobDAO.create(task_id, "zotero_rdf")
            document_client.create(task_id, "zotero_rdf")
        except DocumentLimitError as exc:
            document_client.cleanup(task_id)
            return jsonify({"success": False, "error": exc.reason}), 413
        except DocumentWorkerRejected as exc:
            document_client.cleanup(task_id)
            return jsonify({"success": False, "error": exc.reason}), exc.status_code
        except Exception:
            try:
                document_client.cleanup(task_id)
            except Exception:
                pass
            return jsonify({"success": False, "error": "document_worker_unavailable"}), 503

        current_import_task_id = task_id
        with import_tasks_lock:
            import_tasks[task_id] = {
                "owner_id": current_user_id(),
                "status": "validating", "progress": 0, "current": 0, "total": 0,
                "original_total": 0, "already_imported_count": 0,
                "success_count": 0, "failed_count": 0, "skipped_count": 0,
                "duplicate_count": 0, "others_count": 0,
                "message": "Validating Zotero RDF...",
                "start_time": datetime.now().isoformat(),
                "last_update": datetime.now().isoformat(), "cancelled": False,
            }
        if not _enqueue_import(_validate_rdf_then_import, task_id, target_category_id, topic_ids):
            return jsonify(success=False, error="import_queue_full"), 429
        return jsonify({
            "success": True, "task_id": task_id, "total_papers": 0,
            "message": "RDF queued for security validation",
        }), 202

    def _validate_rdf_then_import(task_id: str, target_category_id: str, topic_ids=None) -> None:
        global current_import_task_id
        try:
            state = document_client.wait(task_id, timeout=120)
            DocumentJobDAO.update(
                task_id, state["status"], progress=int(state.get("progress") or 0),
                error=state.get("error"),
            )
            if state["status"] != "completed":
                raise DocumentWorkerRejected(str(state.get("error") or "rdf_invalid"), 422)
            result = document_client.result_json(task_id)
            papers_data = result.get("papers") if isinstance(result, dict) else None
            if not isinstance(papers_data, list) or not papers_data:
                raise DocumentWorkerRejected("rdf_invalid", 422)
            remaining, imported_count = _filter_already_imported_papers(papers_data)
            if not remaining:
                _update_task_progress(
                    task_id, status="completed", progress=100,
                    original_total=len(papers_data), already_imported_count=imported_count,
                    message="All papers were already imported",
                )
                current_import_task_id = None
                return
            _update_task_progress(
                task_id, status="starting", total=len(remaining),
                original_total=len(papers_data), already_imported_count=imported_count,
                message="RDF validated; importing papers...",
            )
            _import_papers_task(task_id, remaining, target_category_id, topic_ids)
        except (DocumentWorkerRejected, DocumentWorkerUnavailable) as exc:
            reason = getattr(exc, "reason", "document_worker_unavailable")
            DocumentJobDAO.update(task_id, "failed", error=reason)
            _update_task_progress(task_id, status="error", message=reason)
            current_import_task_id = None
        except Exception:
            DocumentJobDAO.update(task_id, "failed", error="rdf_invalid")
            _update_task_progress(task_id, status="error", message="rdf_invalid")
            current_import_task_id = None
        finally:
            try:
                document_client.cleanup(task_id)
            except Exception:
                pass

    @app.route("/api/import/zotero/status")
    def api_import_status():
        """Get the current import task status (for recovery after page refresh)"""
        global current_import_task_id

        if not current_import_task_id:
            return jsonify({"has_task": False})

        with import_tasks_lock:
            task = import_tasks.get(current_import_task_id)
            if task and task.get("owner_id") != current_user_id():
                return jsonify({"has_task": False})
            if not task:
                current_import_task_id = None
                return jsonify({"has_task": False})

            return jsonify(
                {
                    "has_task": True,
                    "task_id": current_import_task_id,
                    "status": task.get("status"),
                    "progress": task.get("progress", 0),
                    "current": task.get("current", 0),
                    "total": task.get("total", 0),
                    "message": task.get("message", ""),
                    "success_count": task.get("success_count", 0),
                    "failed_count": task.get("failed_count", 0),
                    "skipped_count": task.get("skipped_count", 0),
                    "duplicate_count": task.get("duplicate_count", 0),
                    "others_count": task.get("others_count", 0),
                    "original_total": task.get("original_total", task.get("total", 0)),
                    "already_imported_count": task.get("already_imported_count", 0),
                }
            )

    @app.route("/api/import/zotero/progress/<task_id>")
    def api_import_progress(task_id):
        """Get import progress (SSE, read from task status)"""

        with import_tasks_lock:
            task = import_tasks.get(task_id)
            if not task or task.get("owner_id") != current_user_id():
                return jsonify(success=False, error="task_not_found"), 404

        def generate():
            last_status = None

            while True:
                with import_tasks_lock:
                    task = import_tasks.get(task_id)
                    if not task:
                        yield f"data: {json.dumps({'status': 'error', 'message': 'Task does not exist'})}\n\n"
                        return

                    # Build progress data
                    progress_data = {
                        "status": task.get("status"),
                        "progress": task.get("progress", 0),
                        "current": task.get("current", 0),
                        "total": task.get("total", 0),
                        "message": task.get("message", ""),
                        "success_count": task.get("success_count", 0),
                        "failed_count": task.get("failed_count", 0),
                        "skipped_count": task.get("skipped_count", 0),
                        "duplicate_count": task.get("duplicate_count", 0),
                        "others_count": task.get("others_count", 0),
                        "original_total": task.get("original_total", task.get("total", 0)),
                        "already_imported_count": task.get("already_imported_count", 0),
                    }

                # Sent only when status changes
                current_key = (
                    progress_data["status"],
                    progress_data["current"],
                    progress_data["message"],
                )
                if current_key != last_status:
                    yield f"data: {json.dumps(progress_data)}\n\n"
                    last_status = current_key

                # End the flow if completed, on error, or canceled
                if progress_data["status"] in ["completed", "error", "cancelled"]:
                    break

                # Take a short hibernation to avoid CPU overload
                time.sleep(0.5)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/api/import/zotero/cancel/<task_id>", methods=["POST"])
    def api_cancel_import(task_id):
        """Cancel import task"""
        global current_import_task_id

        cancel_document_job = False
        with import_tasks_lock:
            task = import_tasks.get(task_id)
            if not task or task.get("owner_id") != current_user_id():
                return jsonify({"success": False, "error": "Task does not exist"}), 404

            # Check task status
            status = task.get("status")
            if status in ["completed", "error", "cancelled"]:
                return jsonify({"success": False, "error": f"Task completed{status}"}), 400

            # Mark as canceled
            task["cancelled"] = True
            task["status"] = "cancelling"
            task["message"] = "Canceling import..."
            task["last_update"] = datetime.now().isoformat()
            cancel_document_job = status == "validating"

            # If this is the current task, clear the flag
            if current_import_task_id == task_id:
                current_import_task_id = None

        if cancel_document_job:
            try:
                document_client.cancel(task_id)
            except (DocumentWorkerRejected, DocumentWorkerUnavailable):
                pass
        print(f"[Import] Import tasks {task_id} Marked for cancellation")
        return jsonify({"success": True, "message": "Cancellation request sent"})

    @app.route("/api/import/from-export", methods=["POST"])
    def api_import_from_export():
        """Validate a metadata-only iPaper export before importing it."""
        global current_import_task_id
        if "file" not in request.files:
            return jsonify({"success": False, "error": "No document provided"}), 400
        from ipaper.topics.admission import validated_ids
        from ipaper.topics.store import TopicError
        try:
            topic_ids = validated_ids(request.form.get('topicIds')) if 'topicIds' in request.form else None
        except TopicError as error:
            return jsonify(error=error.code), error.status
        file = request.files["file"]
        if not file or file.filename == "":
            return jsonify({"success": False, "error": "No file selected"}), 400
        if not file.filename.lower().endswith(".zip"):
            return jsonify({"success": False, "error": "Only supports ZIP document"}), 400
        if not document_client.health():
            return jsonify({"success": False, "error": "document_worker_unavailable"}), 503
        if current_import_task_id:
            with import_tasks_lock:
                existing = import_tasks.get(current_import_task_id)
                if existing and existing.get("status") not in {"completed", "error", "cancelled"}:
                    return jsonify({
                        "success": False,
                        "error": "There is an import task in progress",
                        "task_id": current_import_task_id if existing.get("owner_id") == current_user_id() else None,
                    }), 409
        task_id = str(uuid.uuid4())
        try:
            document_client.stage(task_id, "metadata_zip", file.stream)
            DocumentJobDAO.create(task_id, "metadata_zip")
            document_client.create(task_id, "metadata_zip")
        except DocumentLimitError as exc:
            document_client.cleanup(task_id)
            return jsonify({"success": False, "error": exc.reason}), 413
        except DocumentWorkerRejected as exc:
            document_client.cleanup(task_id)
            return jsonify({"success": False, "error": exc.reason}), exc.status_code
        except Exception:
            try:
                document_client.cleanup(task_id)
            except Exception:
                pass
            return jsonify({"success": False, "error": "document_worker_unavailable"}), 503

        current_import_task_id = task_id
        with import_tasks_lock:
            import_tasks[task_id] = {
                "owner_id": current_user_id(),
                "status": "validating",
                "progress": 0,
                "current": 0,
                "total": 0,
                "success_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "duplicate_count": 0,
                "others_count": 0,
                "message": "Validating archive...",
                "start_time": datetime.now().isoformat(),
                "last_update": datetime.now().isoformat(),
                "cancelled": False,
            }
        if not _enqueue_import(_validate_export_then_rebuild, task_id, topic_ids):
            return jsonify(success=False, error="import_queue_full"), 429
        return jsonify({
            "success": True,
            "task_id": task_id,
            "total_papers": 0,
            "message": "Archive queued for security validation",
        }), 202

    def _validate_export_then_rebuild(task_id: str, topic_ids=None) -> None:
        global current_import_task_id
        try:
            state = document_client.wait(task_id, timeout=300)
            DocumentJobDAO.update(
                task_id,
                state["status"],
                progress=int(state.get("progress") or 0),
                error=state.get("error"),
            )
            if state["status"] != "completed":
                raise DocumentWorkerRejected(str(state.get("error") or "archive_invalid"), 422)
            output = document_client.output(task_id)
            manifest = document_client.verified_manifest(task_id, "metadata_zip")
            entries = manifest.get("entries", [])
            paper_entries = [
                item for item in entries
                if item.get("path", "").startswith("papers/")
                and os.path.basename(item.get("path", "")) not in {
                    "categories.json", "reading_list.json", "user_settings.json",
                    "reading_history.json", "agentic_settings.json", "daily_arxiv_settings.json",
                }
            ]
            papers_folder = output / "papers"
            if not papers_folder.is_dir():
                raise DocumentWorkerRejected("archive_type_forbidden", 422)
            _update_task_progress(
                task_id,
                status="importing",
                total=len(paper_entries),
                message="Archive validated; importing metadata...",
            )
            _rebuild_papers_from_json(task_id, str(papers_folder), topic_ids)
        except (DocumentWorkerRejected, DocumentWorkerUnavailable) as exc:
            reason = getattr(exc, "reason", "document_worker_unavailable")
            DocumentJobDAO.update(task_id, "failed", error=reason)
            _update_task_progress(task_id, status="error", message=reason)
            current_import_task_id = None
        except Exception:
            DocumentJobDAO.update(task_id, "failed", error="archive_invalid")
            _update_task_progress(task_id, status="error", message="archive_invalid")
            current_import_task_id = None
        finally:
            try:
                document_client.cleanup(task_id)
            except Exception:
                pass

    def _rebuild_papers_from_json(
        task_id: str,
        papers_folder: str,
        topic_ids=None,
    ):
        """Rebuild papers only from Worker-validated metadata JSON files."""
        global current_import_task_id

        def update_progress(
            status=None,
            progress=None,
            current=None,
            total=None,
            message=None,
            success_count=None,
            failed_count=None,
            skipped_count=None,
            duplicate_count=None,
        ):
            """Update task progress"""
            with import_tasks_lock:
                if task_id in import_tasks:
                    task = import_tasks[task_id]
                    if status is not None:
                        task["status"] = status
                    if progress is not None:
                        task["progress"] = progress
                    if current is not None:
                        task["current"] = current
                    if total is not None:
                        task["total"] = total
                    if message is not None:
                        task["message"] = message
                    if success_count is not None:
                        task["success_count"] = success_count
                    if failed_count is not None:
                        task["failed_count"] = failed_count
                    if skipped_count is not None:
                        task["skipped_count"] = skipped_count
                    if duplicate_count is not None:
                        task["duplicate_count"] = duplicate_count
                    task["last_update"] = datetime.now().isoformat()

        success_count = 0
        failed_count = 0
        skipped_count = 0
        duplicate_count = 0

        try:
            json_files = []
            exclude_files = {
                "categories.json",
                "reading_list.json",
                "user_settings.json",
                "reading_history.json",
                "agentic_settings.json",
                "daily_arxiv_settings.json",
            }

            for root, dirs, files in os.walk(papers_folder):
                # Exclude hidden directories
                dirs[:] = [d for d in dirs if not d.startswith(".")]

                for file in files:
                    if file.endswith(".json") and file not in exclude_files:
                        json_path = os.path.join(root, file)
                        json_files.append(json_path)

            total_papers = len(json_files)
            print(f"[Import] turn up {total_papers} papers JSON document")

            update_progress(
                status="importing",
                progress=0,
                current=0,
                total=total_papers,
                message="Start importing papers...",
            )

            for idx, json_path in enumerate(json_files):
                # Check if canceled
                with import_tasks_lock:
                    task = import_tasks.get(task_id)
                    if task and task.get("cancelled", False):
                        print(f"[Import] Import task canceled")
                        update_progress(
                            status="cancelled",
                            progress=int((idx / total_papers) * 100),
                            current=idx,
                            total=total_papers,
                            message="Import canceled",
                            success_count=success_count,
                            failed_count=failed_count,
                            skipped_count=skipped_count,
                            duplicate_count=duplicate_count,
                        )
                        current_import_task_id = None
                        return
                
                try:
                    # read JSON metadata
                    with open(json_path, "r", encoding="utf-8") as f:
                        paper_meta = json.load(f)

                    title = paper_meta.get("title", "")
                    authors = paper_meta.get("authors", "")
                    arxiv_id = paper_meta.get("arxiv_id", "")

                    if not title:
                        print(f"[Import] Skip: Missing title")
                        skipped_count += 1
                        continue

                    # update progress
                    update_progress(
                        status="importing",
                        progress=int((idx / total_papers) * 100),
                        current=idx,
                        total=total_papers,
                        message=f"Importing: {title[:50]}...",
                        success_count=success_count,
                        failed_count=failed_count,
                        skipped_count=skipped_count,
                        duplicate_count=duplicate_count,
                    )

                    paper_dir = os.path.dirname(json_path)
                    rel_dir = os.path.relpath(paper_dir, papers_folder)
                    category_path_parts = rel_dir.split(os.sep) if rel_dir != "." else []
                    category_id = "root"
                    category_path = ["root"]
                    if category_path_parts and topic_ids is None:
                        category_id = _find_or_create_category(
                            get_categories(), category_path_parts, save_categories, create_category_folder
                        )
                        if not category_id:
                            failed_count += 1
                            continue
                        category_path = ["root"] + category_path_parts

                    pdf_content = None
                    pdf_filename = None
                    if arxiv_id:
                        result = _download_arxiv_pdf(arxiv_id)
                        if result:
                            pdf_content, pdf_filename = result
                    elif title and authors:
                        paper_info = search_arxiv_by_title_and_author_fast(title, authors)
                        if paper_info and paper_info.get("arxiv_id"):
                            arxiv_id = paper_info["arxiv_id"]
                            paper_meta["arxiv_id"] = arxiv_id
                            result = _download_arxiv_pdf(arxiv_id)
                            if result:
                                pdf_content, pdf_filename = result
                    if pdf_content is None or pdf_filename is None:
                        failed_count += 1
                        continue
                    clean_title = _clean_filename(title)
                    if clean_title:
                        pdf_filename = f"{clean_title}.pdf"
                    pdf_path, inspection = _promote_validated_pdf(pdf_content, category_id, pdf_filename)
                    actual_filename = os.path.basename(pdf_path)
                    paper_id = str(paper_meta.get("id") or uuid.uuid4())
                    new_paper = Paper(
                        id=paper_id,
                        title=paper_meta.get("title", ""),
                        authors=paper_meta.get("authors", ""),
                        file_path=pdf_path,
                        upload_date=paper_meta.get("upload_date") or datetime.now().isoformat(),
                        filename=actual_filename,
                        original_filename=actual_filename,
                        arxiv_url=paper_meta.get("arxiv_url")
                        or paper_meta.get("url", ""),
                        arxiv_id=paper_meta.get("arxiv_id", ""),
                        arxiv_published_date=paper_meta.get("arxiv_published_date", ""),
                        year=paper_meta.get("year", ""),
                        abstract=paper_meta.get("abstract", ""),
                        summary=paper_meta.get("summary", ""),
                        bibtex=paper_meta.get("bibtex", ""),
                        notes=paper_meta.get("notes", ""),
                        upload_source="export_import",
                        affiliation=paper_meta.get("affiliation", ""),
                        journal=paper_meta.get("journal", ""),
                        subject=paper_meta.get("subject", ""),
                        keywords=paper_meta.get("keywords", ""),
                        starred=paper_meta.get("starred", False),
                        read_time=paper_meta.get("read_time", 0),
                        analysis_view_time=paper_meta.get("analysis_view_time", 0),
                        translation_time=paper_meta.get("translation_time", 0),
                        analysis_time=paper_meta.get("analysis_time", 0),
                    )

                    new_paper.extra.update(_metadata_inspection=inspection, category_id=category_id)
                    if topic_ids is not None:
                        new_paper.extra.update(_topic_ids=topic_ids, _topic_paths=[category_path_parts])
                    save_paper_metadata(pdf_path, new_paper)
                    registered = paper_store.upsert(
                        new_paper, category_id=category_id, category_path=category_path
                    )

                    success_count += 1
                    print(f"[Import] ✅ Imported successfully: {title[:50]}")

                except Exception as e:
                    print(f"[Import] ❌ Processing failed: {json_path}, mistake: {e}")
                    import traceback

                    traceback.print_exc()
                    failed_count += 1

            # Import completed
            update_progress(
                status="completed",
                progress=100,
                current=total_papers,
                total=total_papers,
                message="Import completed",
                success_count=success_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
                duplicate_count=duplicate_count,
            )

            print(
                f"[Import] Import completed: success {success_count}, fail {failed_count}, jump over {skipped_count}, repeat {duplicate_count}"
            )

        except Exception as e:
            print(f"[Import] Import task failed: {e}")
            import traceback

            traceback.print_exc()

            update_progress(
                status="error",
                message=f"Import failed: {str(e)}",
            )

        finally:
            # Clear current task mark
            current_import_task_id = None

    def _import_from_export_task_old(
        task_id: str,
        papers_list: List[Dict[str, Any]],
        extract_dir: str,
        manifest: Dict[str, Any],
    ):
        """Background task: Importing papers generated from the export function"""
        success_count = 0
        failed_count = 0
        skipped_count = 0
        duplicate_count = 0
        others_count = 0
        total = len(papers_list)

        try:
            # Restore classification structure
            exported_categories = manifest.get("categories", {})
            current_categories = get_categories()

            # Merge classification structure (simple append to root directory)
            # TODO: Classifications can be merged more intelligently

            for idx, paper_info in enumerate(papers_list):
                try:
                    _update_task_progress(
                        task_id,
                        status="importing",
                        progress=int((idx / total) * 100),
                        current=idx,
                        total=total,
                        success_count=success_count,
                        failed_count=failed_count,
                        skipped_count=skipped_count,
                        duplicate_count=duplicate_count,
                        others_count=others_count,
                        message=f"Importing: {paper_info['metadata'].get('title', '')[:50]}...",
                    )

                    paper_metadata = paper_info["metadata"]
                    category_path_list = paper_info["category_path"]  # ['CS', 'ML']

                    # Make sure the target category exists
                    full_category_path = ["root"] + category_path_list
                    category_id = None

                    # Traverse the classification path and create non-existing categories
                    current_node = current_categories
                    for cat_name in category_path_list:
                        # Find subcategories
                        found = False
                        for child in current_node.get("children", []):
                            if child.get("name") == cat_name:
                                current_node = child
                                category_id = child.get("id")
                                found = True
                                break

                        # If it does not exist, create a new category
                        if not found:
                            # Create new category
                            try:
                                cat_name = validate_category_name(cat_name)
                            except PathSecurityError:
                                category_id = None
                                break
                            new_cat_id = str(uuid.uuid4())
                            new_category = {
                                "id": new_cat_id,
                                "name": cat_name,
                                "children": [],
                                "isPinned": False,
                            }
                            if "children" not in current_node:
                                current_node["children"] = []
                            current_node["children"].append(new_category)
                            current_node = new_category
                            category_id = new_cat_id

                            # Save classification tree
                            save_categories(current_categories)

                    if not category_id:
                        print(f"[Import] ⚠️ Unable to create classification path: {category_path_list}")
                        failed_count += 1
                        continue

                    # Create category folders
                    category_folder = create_category_folder(category_id)

                    # Check if the same paper already exists
                    paper_id = paper_metadata.get("id")
                    existing_paper = paper_store.get(paper_id) if paper_id else None
                    if existing_paper:
                        print(
                            f"[Import] 📋 The paper already exists, skip: {paper_metadata.get('title', '')[:50]}"
                        )
                        duplicate_count += 1
                        continue

                    # generate new paper_id
                    new_paper_id = str(uuid.uuid4())

                    # copy PDF document
                    pdf_filename = os.path.basename(paper_metadata.get("file_path", ""))
                    if not pdf_filename:
                        pdf_filename = f"{new_paper_id}.pdf"

                    try:
                        zip_pdf_path = str(safe_join(
                            extract_dir,
                            "papers",
                            *category_path_list,
                            pdf_filename,
                            must_exist=True,
                            require_file=True,
                        ))
                    except PathSecurityError:
                        print("[Import] PDF file is missing or outside import root")
                        skipped_count += 1
                        continue

                    # copy PDF to target location
                    dest_pdf_path = str(safe_join(category_folder, pdf_filename))
                    shutil.copy2(zip_pdf_path, dest_pdf_path)
                    destination_assets = paper_asset_paths(
                        category_folder, dest_pdf_path
                    )

                    # Copy Chinese translation (if available)
                    chinese_path = paper_metadata.get("chinese_version_path")
                    if chinese_path:
                        chinese_filename = os.path.basename(chinese_path)
                        try:
                            zip_chinese_path = safe_join(
                                extract_dir,
                                "papers",
                                *category_path_list,
                                chinese_filename,
                                must_exist=True,
                                require_file=True,
                            )
                        except PathSecurityError:
                            zip_chinese_path = None
                        if zip_chinese_path:
                            shutil.copy2(
                                zip_chinese_path, destination_assets.chinese_dual
                            )
                            paper_metadata["chinese_version_path"] = str(
                                destination_assets.chinese_dual
                            )

                    # copy AI Interpretation (if any)
                    analysis_path = paper_metadata.get("analysis_result_path")
                    if analysis_path:
                        analysis_filename = os.path.basename(analysis_path)
                        try:
                            zip_analysis_path = safe_join(
                                extract_dir,
                                "papers",
                                *category_path_list,
                                analysis_filename,
                                must_exist=True,
                                require_file=True,
                            )
                        except PathSecurityError:
                            zip_analysis_path = None
                        if zip_analysis_path:
                            destination_assets.analysis_result.parent.mkdir(
                                parents=True, exist_ok=True
                            )
                            shutil.copy2(
                                zip_analysis_path, destination_assets.analysis_result
                            )
                            paper_metadata["analysis_result_path"] = str(
                                destination_assets.analysis_result
                            )

                            # Copy picture folder
                            images_folder_name = analysis_filename.replace(
                                "_analysis.md", "_images"
                            )
                            zip_images_path = safe_join(
                                extract_dir,
                                "papers",
                                *category_path_list,
                                images_folder_name,
                            )
                            if os.path.exists(zip_images_path) and os.path.isdir(
                                zip_images_path
                            ):
                                zip_images_path = ensure_confined_tree(
                                    extract_dir, zip_images_path
                                )
                                dest_images_path = safe_join(
                                    destination_assets.analysis_result.parent,
                                    images_folder_name,
                                )
                                if os.path.exists(dest_images_path):
                                    remove_confined_tree(
                                        category_folder, dest_images_path
                                    )
                                shutil.copytree(zip_images_path, dest_images_path)

                    # create Paper object
                    new_paper = Paper(
                        id=new_paper_id,
                        title=paper_metadata.get("title", ""),
                        authors=paper_metadata.get("authors", ""),
                        file_path=dest_pdf_path,
                        url=paper_metadata.get("url", ""),
                        arxiv_id=paper_metadata.get("arxiv_id", ""),
                        arxiv_published_date=paper_metadata.get(
                            "arxiv_published_date", ""
                        ),
                        year=paper_metadata.get("year", ""),
                        abstract=paper_metadata.get("abstract", ""),
                        summary=paper_metadata.get("summary", ""),
                        bibtex=paper_metadata.get("bibtex", ""),
                        notes=paper_metadata.get("notes", ""),
                        upload_source="export_import",
                        has_chinese_version=paper_metadata.get(
                            "has_chinese_version", False
                        ),
                        chinese_version_path=paper_metadata.get(
                            "chinese_version_path", ""
                        ),
                        has_analysis_result=paper_metadata.get(
                            "has_analysis_result", False
                        ),
                        analysis_result_path=paper_metadata.get(
                            "analysis_result_path", ""
                        ),
                        read_time=paper_metadata.get("read_time", 0),
                        analysis_view_time=paper_metadata.get("analysis_view_time", 0),
                    )

                    new_paper.extra["category_id"] = category_id
                    save_paper_metadata(dest_pdf_path, new_paper)
                    # Publish after commit.
                    registered_paper = paper_store.upsert(
                        new_paper,
                        category_id=category_id,
                        category_path=full_category_path,
                    )


                    success_count += 1
                    print(
                        f"[Import] ✅ Imported successfully: {paper_metadata.get('title', '')[:50]}"
                    )

                except Exception as e:
                    print(f"[Import] ❌ Failed to import paper: {e}")
                    import traceback

                    traceback.print_exc()
                    failed_count += 1

            # Import completed
            _update_task_progress(
                task_id,
                status="completed",
                progress=100,
                current=total,
                total=total,
                success_count=success_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
                duplicate_count=duplicate_count,
                others_count=others_count,
                message="Import completed",
            )

            # TODO: Restore to-read list, reading history, user settings
            # reading_list = manifest.get("reading_list", [])
            # reading_history = manifest.get("reading_history", {})
            # user_settings = manifest.get("user_settings", {})

        except Exception as e:
            print(f"[Import] ❌ Import task failed: {e}")
            import traceback

            traceback.print_exc()

            update_progress(
                status="error",
                message=f"Import failed: {str(e)}",
            )

        finally:
            # Clear current task mark
            global current_import_task_id
            current_import_task_id = None

            # Clean up temporary directory
            temp_dir = os.path.dirname(extract_dir)
            if os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception as e:
                    print(f"[Import] Failed to clean up temporary directory: {e}")

            print(
                f"[Import] Import completed: success {success_count}, fail {failed_count}, jump over {skipped_count}, repeat {duplicate_count}"
            )
