"""Asset execution: reuse local PDFs, generate previews, keep states honest.

The processor is exercised with a fake Document Worker and a manager subclass
that never touches the network, so each stage can be asserted on its own.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from ipaper.security.identity import Identity, reset_background_identity, set_background_identity
from tests.workbench_support import make_workbench_fixture


@contextmanager
def run_as(identity):
    token = set_background_identity(identity)
    try:
        yield
    finally:
        reset_background_identity(token)


class FakeLimits:
    max_pdf_bytes = 50 * 1024 * 1024
    max_thumbnail_bytes = 10 * 1024 * 1024


class FakeDocumentClient:
    """Minimal stand-in: stages bytes, then writes a PDF and (optionally) a cover."""

    def __init__(self, root: Path, *, emit_thumbnail: bool = True):
        self.root = root
        self.jobs_root = root
        self.limits = FakeLimits()
        self.emit_thumbnail = emit_thumbnail
        self.jobs: list[str] = []

    def job_directory(self, job_id):
        return self.root / job_id

    def stage(self, job_id, kind, stream):
        job = self.job_directory(job_id)
        (job / "work").mkdir(parents=True, exist_ok=True)
        target = job / "work" / ("input.pdf" if kind == "pdf_inspect" else "input.bin")
        with open(target, "wb") as destination:
            shutil.copyfileobj(stream, destination)
        return target

    def create(self, job_id, kind):
        self.jobs.append(job_id)
        return {"job_id": job_id, "kind": kind}

    def wait(self, job_id, *, timeout=100):
        return {"status": "completed", "progress": 100, "error": None}

    def result_json(self, job_id):
        output = self.job_directory(job_id) / "work" / "output"
        output.mkdir(parents=True, exist_ok=True)
        (output / "result.json").write_text(
            json.dumps({"first_page_text": "preview"}), encoding="utf-8"
        )
        if self.emit_thumbnail:
            (output / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff\xe0cover\xff\xd9")
        return {"first_page_text": "preview"}

    def output(self, job_id):
        return self.job_directory(job_id) / "work" / "output"

    def cleanup(self, job_id):
        shutil.rmtree(self.job_directory(job_id), ignore_errors=True)


class FakeManager:
    """Manager subclass that records downloads instead of performing them."""

    def __init__(self, base_dir: Path, document_client):
        from ipaper.tools.basic_tools.daily_arxiv import DailyArxivManager

        self._real = DailyArxivManager(str(base_dir), str(base_dir / "settings.json"))
        self._real.set_document_client(document_client)
        self.download_calls: list[str] = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    def process(self, arxiv_id, stage_callback):
        def downloader(existing, destination):
            self.download_calls.append(arxiv_id)
            if self.download_fails_with:
                return self.download_fails_with
            Path(destination).write_bytes(b"%PDF-1.4 downloaded")
            return None

        self._real._download_daily_pdf = downloader  # type: ignore[assignment]
        return self._real.process_paper_asset(arxiv_id, stage_callback)

    download_fails_with: str | None = None


def insert_paper(connection, owner, paper_id, arxiv_id, *, is_daily=1, **extra):
    columns = {
        "id": paper_id,
        "title": f"Paper {paper_id}",
        "arxiv_id": arxiv_id,
        "owner_id": owner,
        "is_daily": is_daily,
        "daily_date": extra.get("daily_date", "2026-09-18"),
        "category": extra.get("category", "cs.LG"),
        "file_path": extra.get("file_path"),
        "thumbnail_path": extra.get("thumbnail_path"),
    }
    connection.execute(
        f"INSERT INTO papers ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        list(columns.values()),
    )


def make_fixture(tmp_path, monkeypatch, *, emit_thumbnail=True):
    app, one, two = make_workbench_fixture(tmp_path, monkeypatch)
    daily_root = tmp_path / "daily" / ".daily_arxiv_temp"
    daily_root.mkdir(parents=True)
    client = FakeDocumentClient(tmp_path / "jobs", emit_thumbnail=emit_thumbnail)
    manager = FakeManager(daily_root, client)
    manager.download_fails_with = None
    return app, one, two, manager, client, daily_root


def progress_recorder():
    stages = []
    return stages, lambda stage, job=None: stages.append(stage)


def test_reuses_a_local_pdf_and_only_generates_the_cover(tmp_path, monkeypatch):
    app, one, _two, manager, _client, daily_root = make_fixture(tmp_path, monkeypatch)
    with app.app_context():
        with run_as(Identity(one["id"], "reader_one", "admin")):
            from ipaper.database.connection import get_db
            from ipaper.database.dao.paper_dao import PaperDAO

            target_dir = daily_root / "2026-09-18" / "cs_LG"
            target_dir.mkdir(parents=True)
            local_pdf = target_dir / "2609.19915v1.pdf"
            local_pdf.write_bytes(b"%PDF-1.4 already here")

            db = get_db()
            insert_paper(db, one["id"], "daily_2609.19915v1", "2609.19915")
            db.commit()

            stages, callback = progress_recorder()
            result = manager.process("2609.19915v1", callback)

            assert result.success is True
            assert result.thumbnail_status == "ready"
            assert manager.download_calls == []  # never re-downloaded
            assert "downloading" not in stages  # only validation/preview ran

            paper = PaperDAO.get_daily_paper_by_identity("2609.19915v1")
            assert paper["file_path"] == str(local_pdf)
            assert paper["thumbnail_path"] == str(target_dir / "2609.19915v1_thumbnail.jpg")
            assert Path(paper["thumbnail_path"]).read_bytes().startswith(b"\xff\xd8")
            assert paper["artifact_status"] == "ready"


def test_reuses_an_already_library_pdf_copy(tmp_path, monkeypatch):
    app, one, _two, manager, _client, daily_root = make_fixture(tmp_path, monkeypatch)
    with app.app_context():
        with run_as(Identity(one["id"], "reader_one", "admin")):
            from ipaper.database.connection import get_db
            from ipaper.database.dao.paper_dao import PaperDAO

            # Library files live under the owner's root, next to the Daily temp dir.
            library_dir = daily_root.parent / "paper-category"
            library_dir.mkdir(parents=True)
            library_pdf = library_dir / "the-paper.pdf"
            library_pdf.write_bytes(b"%PDF-1.4 library copy")
            before = library_pdf.read_bytes()

            db = get_db()
            insert_paper(db, one["id"], "daily_2609.19915v1", "2609.19915")
            insert_paper(
                db, one["id"], "lib_2609_19915", "2609.19915v1",
                is_daily=0, file_path=str(library_pdf), category="Library",
            )
            db.commit()

            result = manager.process("2609.19915v1", lambda stage, job=None: None)
            assert result.success is True
            assert manager.download_calls == []

            paper = PaperDAO.get_daily_paper_by_identity("2609.19915v1")
            assert paper["file_path"] != str(library_pdf)  # a Daily-owned copy
            assert Path(paper["file_path"]).read_bytes() == before
            assert library_pdf.read_bytes() == before  # original untouched


def test_cover_failure_keeps_the_pdf_readable(tmp_path, monkeypatch):
    app, one, _two, manager, _client, daily_root = make_fixture(
        tmp_path, monkeypatch, emit_thumbnail=False
    )
    with app.app_context():
        with run_as(Identity(one["id"], "reader_one", "admin")):
            from ipaper.database.connection import get_db
            from ipaper.database.dao.paper_dao import PaperDAO

            target_dir = daily_root / "2026-09-18" / "cs_LG"
            target_dir.mkdir(parents=True)
            (target_dir / "2609.19915v1.pdf").write_bytes(b"%PDF-1.4 local")

            db = get_db()
            insert_paper(db, one["id"], "daily_2609.19915v1", "2609.19915")
            db.commit()

            result = manager.process("2609.19915v1", lambda stage, job=None: None)
            # Readable, but the preview is reported separately.
            assert result.success is True
            assert result.thumbnail_status == "failed"

            paper = PaperDAO.get_daily_paper_by_identity("2609.19915v1")
            assert paper["artifact_status"] == "ready"
            assert paper["file_path"]
            assert not paper.get("thumbnail_path")
            assert paper["thumbnail_status"] == "failed"
            assert paper["thumbnail_error_code"] == "thumbnail_failed"


def test_downloads_only_when_no_local_pdf_exists(tmp_path, monkeypatch):
    app, one, _two, manager, client, _daily_root = make_fixture(tmp_path, monkeypatch)
    with app.app_context():
        with run_as(Identity(one["id"], "reader_one", "admin")):
            from ipaper.database.connection import get_db
            from ipaper.database.dao.paper_dao import PaperDAO

            db = get_db()
            insert_paper(db, one["id"], "daily_2609.19915v1", "2609.19915")
            db.commit()

            stages, callback = progress_recorder()
            result = manager.process("2609.19915v1", callback)

            assert result.success is True
            assert manager.download_calls == ["2609.19915v1"]
            assert stages == ["downloading", "validating"]
            assert len(client.jobs) == 1
            paper = PaperDAO.get_daily_paper_by_identity("2609.19915v1")
            assert Path(paper["file_path"]).read_bytes().startswith(b"%PDF")
            assert Path(paper["thumbnail_path"]).exists()


def test_thumbnail_retry_without_a_pdf_reports_pdf_missing(tmp_path, monkeypatch):
    app, one, _two, manager, _client, _daily_root = make_fixture(tmp_path, monkeypatch)
    with app.app_context():
        with run_as(Identity(one["id"], "reader_one", "admin")):
            from ipaper.database.connection import get_db
            from ipaper.database.dao.paper_dao import PaperDAO

            db = get_db()
            insert_paper(db, one["id"], "daily_2609.19915v1", "2609.19915")
            db.execute(
                "INSERT INTO daily_arxiv_candidates"
                " (owner_id,arxiv_id,release_date,artifact_status,retry_count,updated_at,requested_stage)"
                " VALUES (?,?,?,?,?,?,?)",
                (one["id"], "2609.19915v1", "2026-09-18", "queued", 0,
                 "2026-09-18T00:00:00+00:00", "thumbnail"),
            )
            db.commit()

            result = manager.process("2609.19915v1", lambda stage, job=None: None)
            assert result.success is False
            assert result.error_code == "pdf_missing"
            assert manager.download_calls == []
            paper = PaperDAO.get_daily_paper_by_identity("2609.19915v1")
            assert not paper.get("file_path")


def test_download_failure_is_reported_with_a_stable_code(tmp_path, monkeypatch):
    app, one, _two, manager, _client, _daily_root = make_fixture(tmp_path, monkeypatch)
    manager.download_fails_with = "pdf_http_429"
    with app.app_context():
        with run_as(Identity(one["id"], "reader_one", "admin")):
            from ipaper.database.connection import get_db

            db = get_db()
            insert_paper(db, one["id"], "daily_2609.19915v1", "2609.19915")
            db.commit()

            result = manager.process("2609.19915v1", lambda stage, job=None: None)
            assert result.success is False
            assert result.error_code == "pdf_http_429"


def test_another_owners_local_pdf_is_never_reused(tmp_path, monkeypatch):
    app, one, two, manager, _client, daily_root = make_fixture(tmp_path, monkeypatch)
    with app.app_context():
        with run_as(Identity(two["id"], "reader_two", "user")):
            from ipaper.database.connection import get_db

            other_dir = tmp_path / "other-owner"
            other_dir.mkdir(parents=True)
            other_pdf = other_dir / "other.pdf"
            other_pdf.write_bytes(b"%PDF-1.4 other owner")
            db = get_db()
            insert_paper(db, two["id"], "lib_other", "2609.19915", is_daily=0,
                         file_path=str(other_pdf), category="Library")
            db.commit()

        with run_as(Identity(one["id"], "reader_one", "admin")):
            from ipaper.database.connection import get_db

            db = get_db()
            insert_paper(db, one["id"], "daily_2609.19915v1", "2609.19915")
            db.commit()

            result = manager.process("2609.19915v1", lambda stage, job=None: None)
            # The other owner's file is invisible, so this one downloads instead.
            assert manager.download_calls == ["2609.19915v1"]
            assert result.success is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))