from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime
from typing import DefaultDict, Dict, Iterable, List, Optional, Tuple

from ..timeutil import utc_iso
from ipaper.core.base_paper import Paper
from ipaper.security.identity import current_user_id

CategoryPath = Tuple[str, ...]


class PaperEntry:
    __slots__ = ["paper", "category_id", "category_path", "owner_id"]

    def __init__(
        self,
        paper: Paper,
        category_id: str,
        category_path: CategoryPath,
        owner_id: str,
    ) -> None:
        self.paper = paper
        self.category_id = category_id
        self.category_path = category_path
        self.owner_id = owner_id


class PaperStore:
    """Thread-safe in-memory registry mapping paper IDs to Paper instances."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._papers: Dict[tuple[str, str], PaperEntry] = {}
        self._category_index: DefaultDict[tuple[str, str], Dict[str, Paper]] = defaultdict(dict)
        self._category_paths: Dict[tuple[str, str], CategoryPath] = {}
        self._initialized_categories: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # Basic CRUD
    # ------------------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._papers.clear()
            self._category_index.clear()
            self._category_paths.clear()
            self._initialized_categories.clear()

    def _merge_paper(self, existing: Paper, incoming: Paper) -> None:
        """Merge filesystem-loaded paper data into cached paper instance."""
        preserve_fields = {
            "translation_status",
            "translation_task_id",
            "analysis_status",
            "analysis_task_id",
            "status_updated_at",
        }
        for field in incoming.__dataclass_fields__:  # type: ignore[attr-defined]
            if field == "extra" or field in preserve_fields:
                continue
            setattr(existing, field, getattr(incoming, field))
        existing.extra.update(incoming.extra)
        existing._normalize_fields()

    def upsert(
        self,
        paper: Paper,
        *,
        category_id: str,
        category_path: Iterable[str],
    ) -> Paper:
        """Register or update a paper instance and associate it with a category path."""
        path_tuple = tuple(category_path)
        owner_id = current_user_id()
        with self._lock:
            paper_key = (owner_id, paper.id)
            category_key = (owner_id, category_id)
            entry = self._papers.get(paper_key)
            if entry is None:
                paper._normalize_fields()
                entry = PaperEntry(
                    paper=paper,
                    category_id=category_id,
                    category_path=path_tuple,
                    owner_id=owner_id,
                )
                self._papers[paper_key] = entry
            else:
                self._merge_paper(entry.paper, paper)
                entry.category_id = category_id
                entry.category_path = path_tuple
            self._category_index[category_key][paper.id] = entry.paper
            self._category_paths[category_key] = path_tuple
            return entry.paper

    def get(self, paper_id: str) -> Optional[Paper]:
        with self._lock:
            entry = self._papers.get((current_user_id(), paper_id))
            return entry.paper if entry else None

    def get_entry(self, paper_id: str) -> Optional[PaperEntry]:
        with self._lock:
            return self._papers.get((current_user_id(), paper_id))

    def get_by_arxiv_id(self, arxiv_id: str) -> Optional[PaperEntry]:
        """according to arXiv ID Find papers"""
        with self._lock:
            for entry in self._papers.values():
                if entry.owner_id == current_user_id() and entry.paper.arxiv_id == arxiv_id:
                    return entry
            return None

    def remove(self, paper_id: str) -> Optional[Paper]:
        with self._lock:
            owner_id = current_user_id()
            entry = self._papers.pop((owner_id, paper_id), None)
            if not entry:
                return None
            category_key = (owner_id, entry.category_id)
            category_bucket = self._category_index.get(category_key)
            if category_bucket and paper_id in category_bucket:
                category_bucket.pop(paper_id, None)
                if not category_bucket:
                    self._category_index.pop(category_key, None)
            return entry.paper

    # ------------------------------------------------------------------
    # Category-based accessors
    # ------------------------------------------------------------------
    def is_category_initialized(self, category_id: str) -> bool:
        with self._lock:
            return (current_user_id(), category_id) in self._initialized_categories

    def mark_category_initialized(
        self, category_id: str, category_path: Iterable[str]
    ) -> None:
        path_tuple = tuple(category_path)
        with self._lock:
            key = (current_user_id(), category_id)
            self._initialized_categories.add(key)
            self._category_paths[key] = path_tuple

    def list_by_category(self, category_id: str) -> List[Paper]:
        with self._lock:
            bucket = self._category_index.get((current_user_id(), category_id))
            if not bucket:
                return []
            return list(bucket.values())

    def update_category(
        self,
        paper_id: str,
        *,
        category_id: str,
        category_path: Iterable[str],
    ) -> None:
        path_tuple = tuple(category_path)
        with self._lock:
            owner_id = current_user_id()
            entry = self._papers.get((owner_id, paper_id))
            if not entry:
                return
            old_category_id = entry.category_id
            if old_category_id != category_id:
                old_key = (owner_id, old_category_id)
                old_bucket = self._category_index.get(old_key)
                if old_bucket and paper_id in old_bucket:
                    old_bucket.pop(paper_id)
                    if not old_bucket:
                        self._category_index.pop(old_key, None)
                entry.category_id = category_id
            entry.category_path = path_tuple
            category_key = (owner_id, category_id)
            self._category_index[category_key][paper_id] = entry.paper
            self._category_paths[category_key] = path_tuple

    def get_category_path(self, paper_id: str) -> Optional[List[str]]:
        with self._lock:
            entry = self._papers.get((current_user_id(), paper_id))
            if not entry:
                return None
            return list(entry.category_path)

    def get_category_id(self, paper_id: str) -> Optional[str]:
        with self._lock:
            entry = self._papers.get((current_user_id(), paper_id))
            if not entry:
                return None
            return entry.category_id

    def get_category_path_by_id(self, category_id: str) -> Optional[List[str]]:
        with self._lock:
            path = self._category_paths.get((current_user_id(), category_id))
            return list(path) if path else None

    def iter_all(self) -> List[Paper]:
        with self._lock:
            owner_id = current_user_id()
            return [entry.paper for entry in self._papers.values() if entry.owner_id == owner_id]

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------
    def _now_iso(self) -> str:
        return utc_iso()

    def set_translation_status(
        self, paper_id: str, status: str, *, task_id: Optional[str] = None
    ) -> Optional[Paper]:
        with self._lock:
            entry = self._papers.get((current_user_id(), paper_id))
            if not entry:
                return None
            entry.paper.set_translation_status(
                status, task_id=task_id, timestamp=self._now_iso()
            )
            return entry.paper

    def set_analysis_status(
        self, paper_id: str, status: str, *, task_id: Optional[str] = None
    ) -> Optional[Paper]:
        with self._lock:
            entry = self._papers.get((current_user_id(), paper_id))
            if not entry:
                return None
            entry.paper.set_analysis_status(
                status, task_id=task_id, timestamp=self._now_iso()
            )
            return entry.paper


paper_store = PaperStore()
