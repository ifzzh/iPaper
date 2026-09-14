from __future__ import annotations

from ipaper.environment import getenv as brand_getenv

import os
import io
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Protocol

import requests
from flask import Flask, jsonify, request

from ipaper.core.base_paper import Paper
from ipaper.core.paper_store import PaperStore
from ipaper.database.dao.user_data_dao import ReadingListDAO
from ipaper.database.dao.document_job_dao import DocumentJobDAO
from ipaper.document_worker.client import DocumentWorkerClient
from ipaper.document_worker.safety import DocumentLimitError, bounded_copy
from ipaper.security.paths import safe_join




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
            print(f"Removing from arXiv download PDF: {pdf_url}")
            response = requests.get(pdf_url, timeout=30, stream=True)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower():
                print(f"warn: Content-Type no PDF: {content_type}")
            maximum = int(brand_getenv("IPAPER_MAX_PDF_BYTES", str(100 * 1024 * 1024)))
            length = response.headers.get("Content-Length")
            if length and int(length) > maximum:
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
            print(f"Successfully downloaded PDF, size: {len(pdf_content)} bytes")
            return pdf_content, filename
        except (requests.exceptions.RequestException, DocumentLimitError, ValueError) as exc:
            print(f"from {pdf_url} Download failed: {exc}")
            continue

    print(f"all URL All downloads failed")
    return None


def _clean_filename(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = text
    cleaned = re.sub(r'[<>:"/\\|?*]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 100:
        cleaned = cleaned[:100] + "..."
    return cleaned or None


def register_update_from_url_routes(
    app: Flask,
    *,
    get_categories: GetCategoriesFn,
    get_category_path: GetCategoryPathFn,
    create_category_folder: CreateCategoryFolderFn,
    save_paper_metadata: SavePaperMetadataFn,
    reading_list_file: str,
    reading_list_temp_dir: str,
    paper_store: PaperStore,
    document_client: DocumentWorkerClient | None = None,
) -> None:
    document_client = document_client or DocumentWorkerClient()
    def _add_to_reading_list(paper_id: str) -> None:
        ReadingListDAO.add_item(paper_id, datetime.now().isoformat())


    @app.route("/api/upload/arxiv", methods=["POST"])
    def api_upload_arxiv():
        """from arXiv URL Download and import PDF(quick return,DBLP background acquisition)"""
        try:
            data = request.json or {}
            arxiv_url = data.get("arxiv_url", "").strip()
            category_id = data.get("category_id")
            use_temp_dir = data.get("use_temp_dir", False)  # Whether to use the temporary directory of the to-be-read list

            if not arxiv_url:
                return jsonify({"success": False, "error": "Not provided arXiv URL"}), 400

            # If using temp Directory, use directly temp directory path
            if use_temp_dir:
                category_folder = reading_list_temp_dir
                category_path = ["Root", "_ReadingListTemp"]
                category_id = "reading_list_temp"  # Use special ID
            else:
                if not category_id:
                    return jsonify({"success": False, "error": "No category selected"}), 400

                categories = get_categories()
                category_path = get_category_path(categories, category_id)

                if not category_path:
                    return jsonify({"success": False, "error": "Category not found"}), 404

                category_folder = create_category_folder(category_id)

            arxiv_id = _extract_arxiv_id_from_url(arxiv_url)
            if not arxiv_id:
                return (
                    jsonify({"success": False, "error": "Unable to access from URL extracted from arXiv ID"}),
                    400,
                )

            print(f"extracted arXiv ID: {arxiv_id}")

            result = _download_arxiv_pdf(arxiv_id)
            if not result:
                return jsonify({"success": False, "error": "download PDF fail"}), 500

            pdf_content, filename = result
            validation_id = str(uuid.uuid4())
            try:
                document_client.stage(validation_id, "pdf_inspect", io.BytesIO(pdf_content))
                DocumentJobDAO.create(validation_id, "pdf_inspect")
                document_client.create(validation_id, "pdf_inspect")
                state = document_client.wait(validation_id, timeout=100)
                DocumentJobDAO.update(
                    validation_id, state["status"], progress=int(state.get("progress") or 0),
                    error=state.get("error"),
                )
                if state["status"] != "completed":
                    document_client.cleanup(validation_id)
                    return jsonify({"success": False, "error": state.get("error") or "pdf_invalid"}), 422
                inspection = document_client.result_json(validation_id)
                source_pdf = document_client.job_directory(validation_id) / "work" / "input.pdf"
            except Exception:
                try:
                    document_client.cleanup(validation_id)
                except Exception:
                    pass
                return jsonify({"success": False, "error": "document_worker_unavailable"}), 503
            file_path = str(safe_join(category_folder, filename))

            counter = 1
            original_filename = filename
            while os.path.exists(file_path):
                name, ext = os.path.splitext(original_filename)
                filename = f"{name}_{counter}{ext}"
                file_path = str(safe_join(category_folder, filename))
                counter += 1

            temporary = f"{file_path}.{validation_id}.tmp"
            try:
                with source_pdf.open("rb") as reader:
                    bounded_copy(reader, temporary, document_client.limits.max_pdf_bytes)
                os.chmod(temporary, 0o660)
                os.replace(temporary, file_path)
            finally:
                try:
                    document_client.cleanup(validation_id)
                except Exception:
                    pass

            print(f"PDF saved to: {file_path}")

            # Admission is independent of bibliographic network availability.
            # The verified local inspection is retained before staging cleanup;
            # the persistent metadata queue resolves the exact arXiv version.
            embedded = inspection.get("metadata", {})
            metadata = {"title": embedded.get("title") or arxiv_id,
                        "authors": embedded.get("author") or ""}

            paper_id = str(uuid.uuid4())
            # Build arXiv URL(Priority is given to using user-provided URL, otherwise according to arxiv_id build)
            if arxiv_url.startswith("http"):
                final_arxiv_url = arxiv_url
            else:
                final_arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"

            paper_info = {
                "id": paper_id,
                "filename": filename,
                "original_filename": filename,
                "file_path": file_path,
                "upload_date": datetime.now().isoformat(),
                "title": (metadata.get("title") or arxiv_id),
                "authors": metadata.get("authors", ""),
                "arxiv_id": arxiv_id,
                "arxiv_url": metadata.get("arxiv_url")
                or final_arxiv_url,  # priority use metadata in URL, otherwise use the built URL
                "arxiv_published_date": metadata.get("published_date"),
                "affiliation": metadata.get("affiliation", ""),
                "year": metadata.get("year", ""),
                "journal": "",
                "abstract": metadata.get("abstract", ""),
                "summary": metadata.get("summary", ""),
                "bibtex": "",  # Temporarily empty, obtained in the background DBLP post-fill
                "keywords": metadata.get("keywords", ""),
                "subject": metadata.get("subject", ""),
                "notes": "",
                "starred": False,
                "read_time": 0,
                "translation_time": 0,
                "analysis_time": 0,
            }

            paper = Paper.from_dict(paper_info)
            if not paper:
                return jsonify({"success": False, "error": "Failed to create thesis object"}), 500

            # If using temp table of contents, tagged sources
            if use_temp_dir:
                paper.upload_source = "reading_list_url"

            paper.extra["_metadata_inspection"] = inspection
            paper.extra["category_id"] = category_id
            save_paper_metadata(file_path, paper)
            registered_paper = paper_store.upsert(
                paper, category_id=category_id, category_path=category_path
            )
            _add_to_reading_list(registered_paper.id)

            return jsonify({"success": True, "paper": registered_paper.to_dict()})

        except Exception as exc:  # noqa: BLE001
            print(f"from arXiv Import failed: {exc}")
            import traceback

            traceback.print_exc()
            return jsonify({"success": False, "error": f"Import failed: {str(exc)}"}), 500
