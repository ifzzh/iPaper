"""Real Flask session/CSRF and owner boundaries for logical topics."""

import pytest
from tests.workbench_support import make_workbench_fixture
from ipaper.topics.routes import register_topic_routes
from ipaper.topics.service import TopicService


@pytest.fixture
def topic_app(tmp_path, monkeypatch):
    from ipaper.database import connection

    connection.close_db()
    app, user, other = make_workbench_fixture(tmp_path, monkeypatch, cold=True)
    service = TopicService(tmp_path / "ipaper.db", None)
    register_topic_routes(app, service)
    client = app.test_client()
    assert client.get("/api/topics").status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "reader_one", "password": "workbench-test-pass"},
        ).status_code
        == 200
    )
    headers = {"X-CSRF-Token": client.get_cookie("paperpilot_csrf").value}
    yield service, client, headers, user, other
    service.shutdown()
    connection.close_db()


def test_topics_session_csrf_owner_and_revision(topic_app):
    service, client, headers, user, other = topic_app
    assert client.post("/api/topics", json={"name": "研究"}).status_code == 403
    response = client.post("/api/topics", json={"name": "研究"}, headers=headers)
    assert response.status_code == 201
    topic = response.json["id"]
    assert client.get("/api/paper/b-0/topics").status_code == 404
    foreign = service.store(other["id"]).create("研究")["id"]
    assert (
        client.patch(
            "/api/topics/" + foreign,
            json={"action": "delete", "revision": 1},
            headers=headers,
        ).status_code
        == 404
    )
    state = client.get("/api/paper/a-0/topics").json
    data = {"action": "add", "topicId": topic, "revision": state["revision"]}
    assert (
        client.patch("/api/paper/a-0/topics", json=data, headers=headers).status_code
        == 200
    )
    assert (
        client.patch("/api/paper/a-0/topics", json=data, headers=headers).status_code
        == 409
    )
    catalog = client.get("/api/topics")
    assert catalog.json["topics"][0]["count"] == 1
    assert catalog.headers["Cache-Control"] == "private, no-store"


def test_preview_has_zero_requests_and_cancel_is_persistent(topic_app):
    service, client, headers, user, other = topic_app
    data = {"paperIds": ["a-0", "a-1"]}
    preview = client.post("/api/topics/preview", json=data, headers=headers)
    assert preview.status_code == 200
    assert preview.json["requests"] == 0
    task = client.post("/api/topics/jobs", json=data, headers=headers)
    assert task.status_code == 202
    tid = task.json["id"]
    assert client.post("/api/topics/jobs", json=data, headers=headers).json["id"] == tid
    assert client.post(
        "/api/topics/jobs/" + tid + "/cancel", json={}, headers=headers
    ).json["counts"] == {"cancelled": 2}
    with service.store(user["id"]).connection() as db:
        assert db.execute("SELECT count(*) FROM topic_nodes").fetchone()[0] == 0


def test_retired_physical_move_cannot_change_assets(topic_app):
    service, client, headers, user, other = topic_app
    with service.store(user["id"]).connection() as db:
        before = tuple(
            db.execute(
                "SELECT file_path,metadata FROM papers WHERE id='a-0'"
            ).fetchone()
        )
    response = client.put(
        "/api/paper/a-0/move", json={"target_category_id": "root"}, headers=headers
    )
    assert response.status_code == 410
    assert response.json["error"] == "physical_category_move_retired"
    assert (
        client.put(
            "/api/paper/b-0/move", json={"target_category_id": "root"}, headers=headers
        ).status_code
        == 404
    )
    with service.store(user["id"]).connection() as db:
        assert (
            tuple(
                db.execute(
                    "SELECT file_path,metadata FROM papers WHERE id='a-0'"
                ).fetchone()
            )
            == before
        )


def test_topic_navigation_survives_old_client_and_export_is_owned(topic_app):
    service, client, headers, user, other = topic_app
    tid = client.post("/api/topics", json={"name": "定位"}, headers=headers).json["id"]
    state = {"tabs": [], "topicFilter": "topic:" + tid, "topicCollapsed": [tid]}
    assert (
        client.put("/api/workspace/state", json=state, headers=headers).status_code
        == 200
    )
    assert (
        client.put(
            "/api/workspace/state", json={"tabs": [], "theme": "dark"}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.get("/api/workspace/state").json["topicFilter"] == state["topicFilter"]
    )
    foreign = service.store(other["id"]).create("私人")["id"]
    assert (
        client.put(
            "/api/workspace/state",
            json={**state, "topicFilter": "topic:" + foreign},
            headers=headers,
        ).status_code
        == 404
    )
    h = client.get("/api/paper/a-0/topics").json
    client.patch(
        "/api/paper/a-0/topics",
        json={"action": "add", "topicId": tid, "revision": h["revision"]},
        headers=headers,
    )
    export = client.get("/api/topics/" + tid + "/export")
    assert export.status_code == 200 and b"@" in export.data
    assert client.get("/api/topics/" + foreign + "/export").status_code == 404
    assert client.get("/api/topics/" + tid + "/export?format=arxiv").status_code == 200
