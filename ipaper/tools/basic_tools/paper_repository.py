from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Iterable, List, Optional

from ipaper.core.base_paper import Paper
from ipaper.core.paper_store import paper_store
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.security.paths import (
    PathSecurityError,
    ensure_confined,
    paper_asset_paths,
    paper_directory,
    remove_confined_tree,
)


def get_paper_json_path(pdf_path: str) -> str:
    return os.path.splitext(pdf_path)[0] + ".json"


def save_paper_metadata(
    pdf_path: str,
    paper_data,
    *,
    upload_root: Optional[str] = None,
) -> None:
    if upload_root is not None:
        pdf_path = str(ensure_confined(upload_root, pdf_path))
    if isinstance(paper_data, Paper):
        paper = paper_data
    else:
        paper = Paper.from_dict(paper_data) if paper_data else None
    
    if paper:
        # Ensure file_path is set
        if not paper.file_path:
            paper.file_path = pdf_path
        
        payload = paper.to_dict()
        inspection = paper.extra.pop("_metadata_inspection", None)
        if inspection:
            payload["_metadata_inspection"] = inspection
        saved = PaperDAO.save_paper(payload)
        if saved:
            paper.update_from_dict(saved)

        # Optional: Delete legacy JSON if it exists to avoid confusion?
        # json_path = get_paper_json_path(pdf_path)
        # if os.path.exists(json_path):
        #    os.remove(json_path)


def load_paper_metadata(pdf_path: str) -> Optional[Paper]:
    # 1. Try loading from DB
    try:
        data = PaperDAO.get_paper_by_path(pdf_path)
        if data:
            return Paper.from_dict(data)
    except Exception as exc:
        print(f"Failed to load from DB: {exc}")

    # 2. Fallback to JSON file (Legacy support & Migration)
    json_path = get_paper_json_path(pdf_path)
    try:
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                paper = Paper.from_dict(json.load(f))
                # Auto-migrate to DB
                if paper:
                    paper.sync_filesystem(pdf_path, os.path.basename(pdf_path))
                    save_paper_metadata(pdf_path, paper)
                return paper
    except Exception as exc:
        print(f"Failed to load article metadata from JSON: {exc}")
    return None


def delete_paper_files(upload_root: str, pdf_path: str) -> None:
    assets = paper_asset_paths(upload_root, pdf_path)

    for path in (
        assets.pdf,
        assets.metadata,
        assets.chinese_dual,
        assets.chinese_mono,
        assets.translation_log,
    ):
        if path.exists():
            confined = ensure_confined(
                upload_root,
                path,
                must_exist=True,
                require_file=True,
            )
            confined.unlink()
            print(f"Deleted paper asset: {confined.name}")

    if assets.analysis_directory.exists():
        remove_confined_tree(upload_root, assets.analysis_directory)
        print("Deleted exact paper analysis directory")
            
    # Delete from DB
    try:
        # We need the ID. Try to get it from DB first.
        data = PaperDAO.get_paper_by_path(str(assets.pdf))
        if data and data.get('id'):
            PaperDAO.delete_paper(data['id'])
    except Exception as e:
        print(f"Failed to delete paper from DB: {e}")


def scan_papers_in_directory(
    directory_path: str,
    *,
    category_id: str,
    category_path: Iterable[str],
) -> List[Paper]:
    category_path_list = list(category_path)
    papers: List[Paper] = []
    if not os.path.exists(directory_path):
        paper_store.mark_category_initialized(category_id, category_path_list)
        return papers

    for filename in os.listdir(directory_path):
        if not filename.lower().endswith(".pdf"):
            continue

        if filename.endswith(".zh.dual.pdf") or filename.endswith(".zh.mono.pdf"):
            continue

        try:
            pdf_path = str(
                ensure_confined(
                    directory_path,
                    os.path.join(directory_path, filename),
                    must_exist=True,
                    require_file=True,
                )
            )
        except PathSecurityError:
            print("Skipped unsafe PDF entry while scanning category storage")
            continue
        paper = load_paper_metadata(pdf_path)

        if paper:
            paper.sync_filesystem(pdf_path, filename)
        else:
            paper = Paper.create_default(
                filename=filename,
                file_path=pdf_path,
                original_filename=filename,
                upload_date=datetime.fromtimestamp(
                    os.path.getctime(pdf_path)
                ).isoformat(),
            )

        paper.mark_starred(getattr(paper, "starred", False))

        assets = paper_asset_paths(directory_path, pdf_path)
        dual_file = assets.chinese_dual
        paper.mark_chinese_version(str(dual_file) if dual_file.exists() else str(assets.chinese_mono) if assets.chinese_mono.exists() else None)
        if not paper.has_chinese_version:
            paper.use_chinese_version = False

        analysis_result_path = (
            str(assets.analysis_result) if assets.analysis_result.exists() else None
        )
        paper.mark_analysis_result(analysis_result_path)

        paper.extra["category_id"] = category_id
        save_paper_metadata(pdf_path, paper, upload_root=directory_path)
        registered = paper_store.upsert(
            paper, category_id=category_id, category_path=category_path_list
        )
        papers.append(registered)

    # An upsert (including a concurrent upload) is not proof of a full scan.
    # Publish completeness only after every existing file has been considered.
    paper_store.mark_category_initialized(category_id, category_path_list)
    return papers


def refresh_paper_status(paper: Paper, upload_root: str) -> None:
    """Check the file system and update the translation and interpretation status of the paper"""
    if not paper.file_path or not os.path.exists(paper.file_path):
        return
    
    try:
        assets = paper_asset_paths(upload_root, paper.file_path)
    except PathSecurityError:
        return
    
    # Check translation files
    paper.mark_chinese_version(
        str(assets.chinese_dual) if assets.chinese_dual.exists() else None
    )
    
    # Check interpretation results
    analysis_result_path = (
        str(assets.analysis_result) if assets.analysis_result.exists() else None
    )
    paper.mark_analysis_result(analysis_result_path)


def get_papers_in_category(
    upload_folder: str, category_id: str, category_path: List[str]
) -> List[Paper]:
    if not category_path:
        return []
    if paper_store.is_category_initialized(category_id):
        papers = paper_store.list_by_category(category_id)
        # Refresh status of each paper (check file system)
        for paper in papers:
            old_has_chinese = paper.has_chinese_version
            old_has_analysis = paper.has_analysis_result
            refresh_paper_status(paper, upload_folder)
            # If the status changes, save to JSON document
            if (paper.has_chinese_version != old_has_chinese or 
                paper.has_analysis_result != old_has_analysis):
                if paper.file_path and os.path.exists(paper.file_path):
                    save_paper_metadata(
                        paper.file_path,
                        paper,
                        upload_root=upload_folder,
                    )
        return papers
    directory_path = str(paper_directory(upload_folder, category_id))
    return scan_papers_in_directory(
        directory_path, category_id=category_id, category_path=category_path
    )
