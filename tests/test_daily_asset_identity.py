"""Owner-scoped identity lookups used by Daily assets, retries and promotion."""
from __future__ import annotations

import pytest
from contextlib import contextmanager

from ipaper.security.identity import (
    Identity,
    reset_background_identity,
    set_background_identity,
)
from tests.workbench_support import make_workbench_fixture


@contextmanager
def run_as(identity):
    token = set_background_identity(identity)
    try:
        yield
    finally:
        reset_background_identity(token)


def insert_paper(connection, owner, paper_id, arxiv_id, *, is_daily=1, **extra):
    columns = {
        "id": paper_id,
        "title": f"Paper {paper_id}",
        "arxiv_id": arxiv_id,
        "owner_id": owner,
        "is_daily": is_daily,
        "daily_date": extra.get("daily_date", "2026-09-18"),
        "file_path": extra.get("file_path"),
        "thumbnail_path": extra.get("thumbnail_path"),
        "category": extra.get("category", "cs.LG"),
    }
    keys = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    connection.execute(f"INSERT INTO papers ({keys}) VALUES ({marks})", list(columns.values()))


def test_daily_row_is_found_across_versions(tmp_path, monkeypatch):
    from ipaper.database.connection import get_db
    from ipaper.database.dao.paper_dao import PaperDAO

    app, one, two = make_workbench_fixture(tmp_path, monkeypatch)
    with app.app_context():
        with run_as(Identity(one["id"], "reader_one", "admin")):
            db = get_db()
            insert_paper(db, one["id"], "daily_2609.19915v1", "2609.19915")
            insert_paper(db, one["id"], "lib_2609_19915", "2609.19915", is_daily=0, category="Library")
            db.commit()

            daily = PaperDAO.get_daily_paper_by_identity("2609.19915v1")
            assert daily is not None and daily["id"] == "daily_2609.19915v1"

            # The same identity is reachable from every stored form.
            for value in ("2609.19915", "2609.19915v1", "arXiv:2609.19915v2", "2609.19915.pdf"):
                found = PaperDAO.get_paper_by_arxiv_id(value)
                assert found is not None, value
                assert found["id"] in {"daily_2609.19915v1", "lib_2609_19915"}

            # A neighbouring paper is never matched.
            assert PaperDAO.get_paper_by_arxiv_id("2609.19916") is None
            assert PaperDAO.get_daily_paper_by_identity("2609.199150") is None
            assert PaperDAO.get_paper_by_arxiv_id("not-an-id") is None

        # Other owners cannot see this row.
        with run_as(Identity(two["id"], "reader_two", "user")):
            assert PaperDAO.get_daily_paper_by_identity("2609.19915v1") is None
            assert PaperDAO.get_paper_by_arxiv_id("2609.19915") is None


def test_legacy_slash_identity_is_matched_without_touching_others(tmp_path, monkeypatch):
    from ipaper.database.connection import get_db
    from ipaper.database.dao.paper_dao import PaperDAO

    app, one, _two = make_workbench_fixture(tmp_path, monkeypatch)
    with app.app_context():
        with run_as(Identity(one["id"], "reader_one", "admin")):
            db = get_db()
            insert_paper(db, one["id"], "daily_cs0601001", "cs/0601001")
            insert_paper(db, one["id"], "daily_cs0601002", "cs/0601002")
            db.commit()

            found = PaperDAO.get_daily_paper_by_identity("cs/0601001v2")
            assert found is not None and found["id"] == "daily_cs0601001"
            assert PaperDAO.get_daily_paper_by_identity("cs/0601002")["id"] == "daily_cs0601002"
            assert PaperDAO.get_daily_paper_by_identity("cs/0601003") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))