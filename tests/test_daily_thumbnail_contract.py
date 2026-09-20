"""Daily thumbnail contract: owner isolation, honest pending state, real ETag.

Exercised through the real route with a stubbed manager (no network, no worker,
no model calls). Phase 2, S2.
"""
import json
from pathlib import Path

import pytest

import ipaper.routes.basic_routes.daily_arxiv_route as daily_route
from ipaper.security.identity import current_user_id
from tests.test_workbench import login
from tests.workbench_support import make_workbench_fixture


class FakeManager:
    """Minimal manager: papers per owner, enabled settings, no side effects."""

    def __init__(self, papers_by_owner):
        self._papers = papers_by_owner
        self.calls = []
        # Attributes the route registration touches when wiring callbacks.
        self._get_llm_config = None
        self._get_user_settings = None
        self._scheduler_running = False
        self._scheduler_owner_id = None

    def set_llm_config_callback(self, callback):
        self._get_llm_config = callback

    def get_settings(self):
        return {"enabled": True, "categories": ["cs.DC"]}

    def get_papers_for_date(self, date_str, category=None):
        self.calls.append((current_user_id(), date_str, category))
        return [dict(paper) for paper in self._papers.get(current_user_id(), [])]


def build_app(tmp_path, monkeypatch, papers_by_owner):
    app, one, two = make_workbench_fixture(tmp_path, monkeypatch)
    temp_papers = tmp_path / "daily" / "papers"
    temp_papers.mkdir(parents=True)
    manager = FakeManager(papers_by_owner)
    monkeypatch.setattr(daily_route, "get_manager", lambda *args, **kwargs: manager)
    daily_route.register_daily_arxiv_routes(
        app,
        daily_arxiv_settings_file=str(tmp_path / "daily-settings.json"),
        default_daily_arxiv_settings={},
        temp_papers_dir=str(temp_papers),
        get_categories=lambda: {"id": "root", "name": "Root", "children": []},
        get_category_path=lambda *args: ["Root"],
        create_category_folder=lambda *args: str(tmp_path),
        save_paper_metadata=lambda *args: None,
        reading_list_file=str(tmp_path / "reading-list.json"),
        reading_list_temp_dir=str(tmp_path),
        asset_coordinator=None,
    )
    return app, one, two, manager, temp_papers


def thumbnail(client, date_str, category, arxiv_id, **kwargs):
    return client.get(
        f"/api/daily-arxiv/thumbnail/{date_str}/{category}/{arxiv_id}", **kwargs
    )


def test_pending_preview_is_not_cached_as_a_failure(tmp_path, monkeypatch):
    app, one, _two, manager, _temp = build_app(tmp_path, monkeypatch, {})
    owner = one["id"] if isinstance(one, dict) else one.id
    manager._papers[owner] = [
        {
            "arxiv_id": "2609.19915v1",
            "artifact_status": "retry_wait",
            "thumbnail_path": "",
        }
    ]
    client = app.test_client()
    login(client, "reader_one")
    response = thumbnail(client, "2026-09-18", "cs.DC", "2609.19915v1")
    assert response.status_code == 404
    assert response.json["error"] == "thumbnail_pending"
    assert response.json["artifact_status"] == "retry_wait"
    assert response.headers["Cache-Control"] == "no-store"


def test_settled_preview_failure_is_reported_as_unavailable(tmp_path, monkeypatch):
    app, one, _two, manager, _temp = build_app(tmp_path, monkeypatch, {})
    owner = one["id"] if isinstance(one, dict) else one.id
    manager._papers[owner] = [
        {
            "arxiv_id": "2609.19915v1",
            "artifact_status": "ready",
            "thumbnail_path": "",
            "thumbnail_status": "failed",
            "cover_status": "failed",
        }
    ]
    client = app.test_client()
    login(client, "reader_one")
    response = thumbnail(client, "2026-09-18", "cs.DC", "2609.19915v1")
    assert response.status_code == 404
    # A terminal failure must not look like work in progress.
    assert response.json["error"] == "thumbnail_unavailable"
    assert response.json["cover_status"] == "failed"
    assert response.headers["Cache-Control"] == "no-store"


def test_existing_preview_is_private_and_versioned(tmp_path, monkeypatch):
    app, one, _two, _manager, temp_papers = build_app(tmp_path, monkeypatch, {})
    owner = one["id"] if isinstance(one, dict) else one.id
    cover = temp_papers / "cover-one.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0preview-one\xff\xd9")
    _manager._papers[owner] = [
        {
            "arxiv_id": "2609.10000v1",
            "artifact_status": "ready",
            "thumbnail_path": str(cover),
        }
    ]
    client = app.test_client()
    login(client, "reader_one")
    response = thumbnail(client, "2026-09-18", "cs.DC", "2609.10000v1")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, max-age=300"
    etag = response.headers["ETag"]
    assert "2609.10000v1" in etag and str(cover.stat().st_size) in etag

    not_modified = thumbnail(
        client, "2026-09-18", "cs.DC", "2609.10000v1", headers={"If-None-Match": etag}
    )
    assert not_modified.status_code == 304

    # A repaired file gets a new revision, so the browser refetches it.
    cover.write_bytes(b"\xff\xd8\xff\xe0preview-one-repaired-longer\xff\xd9")
    refreshed = thumbnail(client, "2026-09-18", "cs.DC", "2609.10000v1")
    assert refreshed.status_code == 200
    assert refreshed.headers["ETag"] != etag


def test_users_never_share_a_cached_preview_map(tmp_path, monkeypatch):
    app, one, two, manager, temp_papers = build_app(tmp_path, monkeypatch, {})
    owner_one = one["id"] if isinstance(one, dict) else one.id
    owner_two = two["id"] if isinstance(two, dict) else two.id
    cover_two = temp_papers / "cover-two.jpg"
    cover_two.write_bytes(b"\xff\xd8\xff\xe0preview-two\xff\xd9")
    manager._papers[owner_one] = [
        {"arxiv_id": "2609.20000v1", "artifact_status": "ready", "thumbnail_path": ""}
    ]
    manager._papers[owner_two] = [
        {
            "arxiv_id": "2609.20000v1",
            "artifact_status": "ready",
            "thumbnail_path": str(cover_two),
        }
    ]

    first = app.test_client()
    login(first, "reader_one")
    assert thumbnail(first, "2026-09-18", "cs.DC", "2609.20000v1").status_code == 404

    second = app.test_client()
    login(second, "reader_two")
    second_response = thumbnail(second, "2026-09-18", "cs.DC", "2609.20000v1")
    assert second_response.status_code == 200
    assert second_response.data.endswith(b"\xff\xd9")

    # The first user's cache entry is still theirs: one paper id, two owners,
    # two different answers.
    assert thumbnail(first, "2026-09-18", "cs.DC", "2609.20000v1").status_code == 404


def test_unknown_paper_is_a_plain_404(tmp_path, monkeypatch):
    app, _one, _two, _manager, _temp = build_app(tmp_path, monkeypatch, {})
    client = app.test_client()
    login(client, "reader_one")
    response = thumbnail(client, "2026-09-18", "cs.DC", "2609.99999v1")
    assert response.status_code == 404
    assert response.json["error"] == "Paper not found"


def test_cache_key_includes_the_owner():
    source = Path(daily_route.__file__).read_text(encoding="utf-8")
    assert 'cache_key = f"{owner_id}:{date_str}:{category or \'all\'}"' in source
    assert "_THUMBNAIL_CACHE_TTL_SECONDS" in source
    assert "no-store" in source
    assert "private, max-age=300" in source


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))