"""Reading tools against real Flask, owner isolation and isolated fake suppliers."""

import json
import time
from tests.test_processing_api import application, generate
from tests.test_workbench import login


def wait(client, job):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = client.get("/api/processing/jobs/" + job["id"]).json["job"]
        if job["status"] not in {"queued", "running", "cancelling"}:
            return job
        time.sleep(0.03)
    raise AssertionError(job)


def test_navigation_bookmarks_revision_and_owner(application):
    a, b = application.test_client(), application.test_client()
    token = login(a)
    login(b, "reader_two")
    headers = {"X-CSRF-Token": token}
    _, job = generate(a, token)
    rid = job["resultId"]
    nav = a.get(f"/api/results/{rid}/navigation").json
    assert nav["items"] and nav["items"][0]["page"] == 1
    assert b.get(f"/api/results/{rid}/navigation").status_code == 404
    identity = a.post("/api/paper/a-4/reading-document", json={}, headers=headers)
    assert identity.status_code == 200, identity.json
    document = identity.json["document"]
    value = {
        "documentId": document["id"],
        "name": "后部实验",
        "location": {"page": 2, "offset": 0.3},
    }
    assert a.post("/api/paper/a-4/bookmarks", json=value).status_code == 403
    first = a.post("/api/paper/a-4/bookmarks", json=value, headers=headers).json[
        "bookmark"
    ]
    again = a.post("/api/paper/a-4/bookmarks", json=value, headers=headers).json[
        "bookmark"
    ]
    assert first == again and first["canNavigate"]
    assert not {"owner_id", "location_json"} & set(first)
    path = "/api/paper/a-4/bookmarks/" + first["id"]
    updated = a.put(
        path, json={"revision": first["revision"], "name": "实验结果"}, headers=headers
    ).json["bookmark"]
    assert updated["name"] == "实验结果" and updated["revision"] != first["revision"]
    assert (
        a.delete(
            path, json={"revision": first["revision"]}, headers=headers
        ).status_code
        == 409
    )
    assert b.get("/api/paper/a-4/bookmarks").status_code == 404
    assert a.get("/api/paper/a-4/bookmarks").json["bookmarks"] == [updated]
    assert (
        a.delete(
            path, json={"revision": updated["revision"]}, headers=headers
        ).status_code
        == 200
    )
    assert a.get("/api/paper/a-4/bookmarks").json["bookmarks"] == []


def test_result_search_full_scope_and_no_generation(application):
    a = application.test_client()
    token = login(a)
    headers = {"X-CSRF-Token": token}
    _, job = generate(a, token, pages=[1])
    rid = job["resultId"]
    before = a.get("/api/processing/jobs").json
    response = a.post(
        f"/api/results/{rid}/search",
        headers=headers,
        json={"query": "controlled experiment", "scope": "both"},
    )
    assert response.status_code == 200, response.json
    value = response.json
    assert value["complete"] and value["totalBlocks"] == 8
    assert any(m["page"] == 2 and m["display"] == "original" for m in value["matches"])
    assert not any(
        m["page"] == 2 and m["display"] == "translated" for m in value["matches"]
    )
    assert value["translatedBlocks"] < value["totalBlocks"]
    assert before == a.get("/api/processing/jobs").json


def test_explicit_selection_single_request_cache_and_retry(application, monkeypatch):
    a, b = application.test_client(), application.test_client()
    token = login(a)
    login(b, "reader_two")
    headers = {"X-CSRF-Token": token}
    doc = a.post("/api/paper/a-4/reading-document", json={}, headers=headers).json[
        "document"
    ]
    calls = []

    def model(profile, messages, limit, **kwargs):
        calls.append((messages, limit, kwargs))
        return {"status": "completed", "text": "受控选区译文"}

    monkeypatch.setattr("ipaper.processing.selection.process_request", model)
    value = {
        "text": "Synthetic reader validation",
        "documentId": doc["id"],
        "targetLanguage": "zh-CN",
    }
    path = "/api/paper/a-4/selection-translation"
    assert a.post(path, json=value).status_code == 403
    assert a.post(path + "/preview", json=value, headers=headers).json["cached"] is None
    assert calls == []
    response = a.post(path, json=value, headers=headers)
    assert response.status_code == 200, response.json
    job = wait(a, response.json["job"])
    assert job["status"] == "completed", job
    assert len(calls) == 1 and calls[0][1] == 2048
    cached = a.post(path, json=value, headers=headers).json
    assert cached["cached"] and cached["translation"]["translation"] == "受控选区译文"
    assert len(calls) == 1
    assert b.get("/api/selection-translations/" + job["resultId"]).status_code == 404
    assert (
        a.post(path, json={**value, "text": "x" * 2001}, headers=headers).status_code
        == 413
    )
    assert (
        a.post(path, json={**value, "sourceId": "bad"}, headers=headers).status_code
        == 400
    )
    # Model failure cannot overwrite a completed selection, let alone a block.
    monkeypatch.setattr(
        "ipaper.processing.selection.process_request",
        lambda *a, **k: {"status": "unknown"},
    )
    failed = wait(
        a,
        a.post(path, json={**value, "targetLanguage": "en"}, headers=headers).json[
            "job"
        ],
    )
    assert failed["status"] == "interrupted"
    repeated = a.post(
        path, json={**value, "targetLanguage": "en"}, headers=headers
    ).json["job"]
    assert repeated["id"] == failed["id"]
    assert (
        a.post(
            "/api/processing/jobs/" + failed["id"] + "/resume", headers=headers, json={}
        ).status_code
        == 409
    )
    monkeypatch.setattr("ipaper.processing.selection.process_request", model)
    retried = wait(
        a,
        a.post(
            path,
            json={**value, "targetLanguage": "en", "retryJobId": failed["id"]},
            headers=headers,
        ).json["job"],
    )
    assert retried["status"] == "completed" and retried["id"] != failed["id"]


def test_cancelled_selection_does_not_publish_late_supplier_result(
    application, monkeypatch
):
    import threading
    from ipaper.processing.maintenance import backup

    client = application.test_client()
    token = login(client)
    headers = {"X-CSRF-Token": token}
    doc = client.post("/api/paper/a-4/reading-document", json={}, headers=headers).json[
        "document"
    ]
    started = threading.Event()
    release = threading.Event()
    calls = []

    def model(*args, **kwargs):
        calls.append(1)
        started.set()
        release.wait(5)
        return {"status": "completed", "text": "too late"}

    monkeypatch.setattr("ipaper.processing.selection.process_request", model)
    value = {"text": "one explicit selection", "documentId": doc["id"]}
    path = "/api/paper/a-4/selection-translation"
    job = client.post(path, headers=headers, json=value).json["job"]
    assert started.wait(5)
    repeated = client.post(path, headers=headers, json=value).json["job"]
    assert repeated["id"] == job["id"]
    assert (
        client.post(
            "/api/processing/jobs/" + job["id"] + "/cancel", headers=headers, json={}
        ).status_code
        == 200
    )
    release.set()
    job = wait(client, job)
    assert job["status"] == "cancelled" and job["resultId"] is None and len(calls) == 1
    assert (
        client.post(path + "/preview", headers=headers, json=value).json["cached"]
        is None
    )


def test_search_cursors_revision_and_bookmark_expiry(
    application, monkeypatch, tmp_path
):
    import hashlib
    import uuid
    from ipaper.security.identity import Identity, run_as_identity

    client = application.test_client()
    token = login(client)
    headers = {"X-CSRF-Token": token}
    doc = client.post("/api/paper/a-4/reading-document", json={}, headers=headers).json[
        "document"
    ]
    user = client.get("/api/auth/session").json["user"]

    def seed():
        pipeline = application.extensions["processing"].pipeline(user["id"])
        store = pipeline.store
        parsed = store.new_result(doc["id"], "structure", {"normalizer": "fixture"})
        root = tmp_path / "long-blocks"
        root.mkdir()
        blocks = [
            {
                "id": "b" + str(n),
                "order": n,
                "type": "text",
                "level": 1 if n in {0, 101} else None,
                "text": "RepeatEvidence " * 120 if n == 101 else f"Paragraph {n}",
                "textHash": hashlib.sha256(str(n).encode()).hexdigest(),
                "source": {
                    "page": 2 if n > 100 else 1,
                    "precision": "page",
                    "regions": [],
                },
            }
            for n in range(237)
        ]
        payload = "".join(json.dumps(b) + "\n" for b in blocks).encode()
        (root / "blocks.jsonl").write_bytes(payload)
        store.publish_structure(
            parsed,
            root,
            {
                "entries": [
                    {
                        "path": "blocks.jsonl",
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ]
            },
        )
        translated = store.new_result(
            doc["id"],
            "structured_translation",
            {"model": "fixture", "revision": "1"},
            parse_id=parsed,
        )
        return pipeline, translated

    pipeline, rid = run_as_identity(
        Identity(user["id"], user["username"], user["role"]), seed
    )
    query = {"query": "RepeatEvidence", "scope": "both"}
    cursor = None
    matches = []
    while True:
        value = client.post(
            f"/api/results/{rid}/search",
            headers=headers,
            json={**query, "cursor": cursor},
        ).json
        matches.extend(value["matches"])
        cursor = value["cursor"]
        if not cursor:
            break
    assert len(matches) == 120 and all(m["order"] == 101 for m in matches)
    first = client.post(f"/api/results/{rid}/search", headers=headers, json=query).json
    assert first["cursor"]
    generation, _ = pipeline.store.begin_translation(rid, "b101")
    pipeline.store.finish_translation(
        rid, "b101", generation, {"text": "new translated text"}
    )
    assert (
        client.post(
            f"/api/results/{rid}/search",
            headers=headers,
            json={**query, "cursor": first["cursor"]},
        ).status_code
        == 409
    )
    bookmark = client.post(
        "/api/paper/a-4/bookmarks",
        headers=headers,
        json={
            "name": "versioned",
            "documentId": doc["id"],
            "location": {"page": 1, "offset": 0.2},
        },
    ).json["bookmark"]
    pipeline.paper_file("a-4").write_bytes(b"%PDF-changed-synthetic")
    listed = client.get("/api/paper/a-4/bookmarks").json["bookmarks"]
    assert (
        listed[0]["id"] == bookmark["id"]
        and not listed[0]["canNavigate"]
        and listed[0]["stale"]
    )


def test_selection_cache_backup_expiry_and_same_source_dedup(
    application, monkeypatch, tmp_path
):
    from ipaper.processing.maintenance import backup, cleanup
    from ipaper.security.identity import Identity, run_as_identity

    client = application.test_client()
    token = login(client)
    headers = {"X-CSRF-Token": token}
    doc = client.post("/api/paper/a-4/reading-document", headers=headers, json={}).json[
        "document"
    ]
    calls = []

    def model(*args, **kwargs):
        calls.append(1)
        return {"status": "completed", "text": "缓存结果 " + str(len(calls))}

    monkeypatch.setattr("ipaper.processing.selection.process_request", model)
    payload = {"text": "Synthetic reader validation", "documentId": doc["id"]}
    path = "/api/paper/a-4/selection-translation"
    first = wait(client, client.post(path, headers=headers, json=payload).json["job"])
    assert first["status"] == "completed"
    assert len(calls) == 1
    client.post(
        "/api/paper/a-4/bookmarks",
        headers=headers,
        json={"documentId": doc["id"], "name": "备份书签", "location": {"page": 1}},
    )
    user = client.get("/api/auth/session").json["user"]
    pipeline = application.extensions["processing"].pipeline(user["id"])
    evidence = backup(
        pipeline.store.db_path,
        pipeline.store.papers_root,
        tmp_path / "consistent-backup",
    )
    assert evidence["databaseVerified"] and evidence["immutableFiles"] >= 1
    with pipeline.store.connection(write=True) as db:
        db.execute(
            "UPDATE reading_selection_cache SET expires_at='2000-01-01T00:00:00+00:00' WHERE owner_id=?",
            (user["id"],),
        )
    assert (
        client.get("/api/selection-translations/" + first["resultId"]).status_code
        == 404
    )
    second = wait(client, client.post(path, headers=headers, json=payload).json["job"])
    assert (
        second["status"] == "completed"
        and second["resultId"] != first["resultId"]
        and len(calls) == 2
    )
    assert (
        client.post(path + "/preview", headers=headers, json=payload).json["cached"][
            "translation"
        ]
        == "缓存结果 2"
    )
    with pipeline.store.connection(write=True) as db:
        db.execute(
            "UPDATE reading_selection_cache SET expires_at='2000-01-01T00:00:00+00:00' WHERE owner_id=?",
            (user["id"],),
        )
    cleanup(pipeline.store)
    assert not pipeline.store.artifact_directory(second["resultId"]).exists()


def test_reading_routes_default_deny_and_csrf_matrix(application):
    anonymous = application.test_client()
    client = application.test_client()
    token = login(client)
    routes = [
        ("POST", "/api/paper/a-4/reading-document", {}),
        ("GET", "/api/paper/a-4/bookmarks", None),
        ("POST", "/api/paper/a-4/bookmarks", {}),
        ("PUT", "/api/paper/a-4/bookmarks/00000000-0000-4000-8000-000000000001", {}),
        ("DELETE", "/api/paper/a-4/bookmarks/00000000-0000-4000-8000-000000000001", {}),
        (
            "POST",
            "/api/results/00000000-0000-4000-8000-000000000001/search",
            {"query": "test"},
        ),
        ("GET", "/api/results/00000000-0000-4000-8000-000000000001/navigation", None),
        ("POST", "/api/paper/a-4/selection-translation/preview", {}),
        ("POST", "/api/paper/a-4/selection-translation", {}),
        (
            "GET",
            "/api/selection-translations/00000000-0000-4000-8000-000000000001",
            None,
        ),
    ]
    for method, path, body in routes:
        assert anonymous.open(path, method=method, json=body).status_code == 401, path
        if method != "GET":
            assert client.open(path, method=method, json=body).status_code == 403, path


def test_selection_configuration_source_and_restart_boundaries(
    application, monkeypatch
):
    from ipaper.security.identity import Identity, run_as_identity

    client = application.test_client()
    token = login(client)
    headers = {"X-CSRF-Token": token}
    doc = client.post("/api/paper/a-4/reading-document", json={}, headers=headers).json[
        "document"
    ]
    user = client.get("/api/auth/session").json["user"]
    pipeline = application.extensions["processing"].pipeline(user["id"])
    calls = []
    monkeypatch.setattr(
        "ipaper.processing.selection.process_request",
        lambda *a, **k: (
            calls.append(1) or {"status": "completed", "text": "temporary translation"}
        ),
    )
    value = {
        "text": "Synthetic reader validation",
        "documentId": doc["id"],
        "budget": {
            "requests": 1,
            "inputTokens": 2000,
            "outputTokens": 512,
            "seconds": 120,
        },
    }
    path = "/api/paper/a-4/selection-translation"
    first = wait(client, client.post(path, headers=headers, json=value).json["job"])
    assert first["status"] == "completed" and first["usage"]["requests"] == 1
    assert (
        client.post(
            path, headers=headers, json={**value, "budget": {"requests": 2}}
        ).status_code
        == 400
    )
    assert (
        client.post(
            path, headers=headers, json={**value, "budget": {"inputTokens": 1}}
        ).status_code
        == 413
    )
    source = client.post(
        "/api/paper/a-4/pdf-sources",
        headers=headers,
        json={
            "document": "original",
            "documentId": doc["id"],
            "page": 1,
            "text": value["text"],
        },
    )
    assert source.status_code == 200, source.json
    assert (
        client.post(
            path + "/preview",
            headers=headers,
            json={
                **value,
                "sourceId": source.json["source"]["id"],
                "text": "forged selection",
            },
        ).status_code
        == 409
    )

    # Alter only non-sensitive model identity; the prior cache remains readable
    # but cannot answer a request using a different effective configuration.
    def change_profile():
        profile = pipeline.profiles.get(secret=True)
        pipeline.profiles.save(profile["model"] + "-changed", profile["baseUrl"])

    run_as_identity(
        Identity(user["id"], user["username"], user["role"]), change_profile
    )
    assert (
        client.post(path + "/preview", headers=headers, json=value).json["cached"]
        is None
    )
    assert (
        client.get("/api/selection-translations/" + first["resultId"]).status_code
        == 200
    )
    assert len(calls) == 1
    # Restart recovery marks a dispatched selection unknown, never re-runs it.
    spec, _ = (
        __import__("ipaper.processing.selection", fromlist=["SelectionTranslation"])
        .SelectionTranslation(pipeline)
        .prepare("a-4", value)
    )
    job, _ = pipeline.jobs.create(
        "a-4",
        "selection_translate",
        spec,
        document_id=doc["id"],
        budget=spec["budget"],
        reservation=32768,
    )
    pipeline.jobs.claim(job["id"])
    pipeline.jobs.recover()
    assert pipeline.jobs.get(job["id"])["status"] == "interrupted"
    assert (
        client.post(
            "/api/processing/jobs/" + job["id"] + "/resume", headers=headers, json={}
        ).status_code
        == 409
    )
    assert len(calls) == 1


def test_paper_delete_cleans_only_owned_bookmarks(application):
    from ipaper.database.dao.paper_dao import PaperDAO
    from ipaper.security.identity import Identity,run_as_identity
    client=application.test_client();token=login(client);headers={'X-CSRF-Token':token}
    user=client.get('/api/auth/session').json['user']
    doc=client.post('/api/paper/a-4/reading-document',headers=headers,json={}).json['document']
    assert client.post('/api/paper/a-4/bookmarks',headers=headers,json={'documentId':doc['id'],'name':'temporary','location':{'page':1}}).status_code==200
    with application.app_context():
        run_as_identity(Identity(user['id'],user['username'],user['role']),lambda:PaperDAO.delete_paper('a-4'))
    store=application.extensions['processing'].pipeline(user['id']).store
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM reading_bookmarks WHERE owner_id=?',(user['id'],)).fetchone()[0]==0
    assert client.get('/api/paper/a-4/bookmarks').status_code==404
