"""Real Flask/SQLite keyword behavior; no provider, parser or production data."""

import json
import sqlite3
from unittest.mock import patch
import pytest
from ipaper.keywords.common import KeywordError
from ipaper.keywords.store import KeywordStore
from ipaper.keywords.service import KeywordService
from ipaper.keywords.routes import register_keyword_routes
from ipaper.keywords.extract import extract, normalize_phrase
from ipaper.metadata.store import MetadataStore
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.security.identity import Identity, run_as_identity
from tests.workbench_support import make_workbench_fixture


@pytest.fixture
def keyword_app(tmp_path, monkeypatch):
    from ipaper.database import connection

    connection.close_db()
    app, user, other = make_workbench_fixture(tmp_path, monkeypatch, count=3, cold=True)
    service = KeywordService(tmp_path / "ipaper.db", None)
    register_keyword_routes(app, service)
    client = app.test_client()
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "reader_one", "password": "workbench-test-pass"},
        ).status_code
        == 200
    )
    headers = {"X-CSRF-Token": client.get_cookie("paperpilot_csrf").value}
    yield app, service, client, headers, user, other
    service.shutdown()
    connection.close_db()


def edit(client, headers, paper="a-0", **data):
    revision = client.get("/api/paper/" + paper + "/tags").json["revision"]
    response = client.patch(
        "/api/paper/" + paper + "/tags",
        json={"revision": revision, **data},
        headers=headers,
    )
    assert response.status_code == 200, response.json
    return response.json


def run_batch(service, owner, bid):
    with service.store(owner).connection() as db:
        items = [
            r[0]
            for r in db.execute("SELECT id FROM keyword_items WHERE batch_id=?", (bid,))
        ]
    for item in items:
        service.run(item, owner)
    return service.store(owner).batch(bid)


def test_owner_csrf_names_and_revision(keyword_app):
    _, service, c, h, u, o = keyword_app
    assert c.get("/api/paper/b-0/tags").status_code == 404
    assert (
        c.patch(
            "/api/paper/a-0/tags", json={"action": "add", "name": "C++", "revision": 1}
        ).status_code
        == 403
    )
    result = edit(c, h, action="add", name="C++")
    tag = result["tags"][0]
    edit(c, h, action="add", name="C")
    assert len(c.get("/api/paper/a-0/tags").json["tags"]) == 2
    assert (
        c.patch(
            "/api/paper/a-0/tags", json={"action": "clear", "revision": 1}, headers=h
        ).status_code
        == 409
    )
    assert c.get("/api/tags").json["tags"][0]["count"] == 1
    with pytest.raises(KeywordError):
        service.store(o["id"]).edit_tag(tag["id"], {"action": "delete", "revision": 1})
    assert c.get("/api/library/papers").status_code == 200
    assert "file_path" not in c.get("/api/library/papers").json["items"][0]


def test_exclusion_rename_merge_late_generation_and_undo(keyword_app):
    _, service, c, h, u, o = keyword_app
    store = service.store(u["id"])
    first = edit(c, h, action="add", name="KV cache")
    tid = first["tags"][0]["id"]
    edit(c, h, action="remove", tagId=tid)
    head = store.get("a-0")
    store.apply("a-0", [{"name": "KV cache"}], "first", head["revision"], "local", {})
    assert not store.get("a-0")["tags"]
    # Rename keeps the previous spelling bound to the same ID.
    tag = next(t for t in store.tags() if t["id"] == tid)
    store.edit_tag(
        tid, {"action": "rename", "name": "键值缓存", "revision": tag["revision"]}
    )
    head = store.get("a-0")
    store.apply("a-0", [{"name": "KV cache"}], "next", head["revision"], "local", {})
    assert not store.get("a-0")["tags"]
    second = edit(c, h, paper="a-1", action="add", name="Cache alias")["tags"][0]["id"]
    tags = {t["id"]: t for t in store.tags()}
    receipt = store.edit_tag(
        tid,
        {
            "action": "merge",
            "targetId": second,
            "revision": tags[tid]["revision"],
            "targetRevision": tags[second]["revision"],
        },
    )
    head = store.get("a-0")
    store.apply("a-0", [{"name": "KV cache"}], "third", head["revision"], "local", {})
    assert not store.get("a-0")["tags"]
    # A later edit to affected rows prevents destructive undo.
    store.edit_tag(
        second,
        {
            "action": "rename",
            "name": "My cache",
            "revision": next(t for t in store.tags() if t["id"] == second)["revision"],
        },
    )
    with pytest.raises(KeywordError):
        store.undo(receipt["operationId"])
    head = store.get("a-1")
    receipt = store.edit_papers(
        ["a-1"], "remove", tag_id=second, revisions={"a-1": head["revision"]}
    )
    store.undo(receipt["operationId"])
    assert store.get("a-1")["tags"][0]["id"] == second


def test_global_delete_tombstone_and_transaction_failure(keyword_app):
    _, service, c, h, u, o = keyword_app
    store = service.store(u["id"])
    tag = edit(c, h, action="add", name="图神经网络")["tags"][0]
    receipt = store.edit_tag(tag["id"], {"action": "delete", "revision": 1})
    head = store.get("a-0")
    store.apply("a-0", [{"name": "图神经网络"}], "x", head["revision"], "local", {})
    assert not store.get("a-0")["tags"] and store.tags(deleted=True)
    # A suppressed automatic result did not change the deletion's touched rows.
    store.undo(receipt["operationId"])
    assert store.get("a-0")["tags"][0]["id"] == tag["id"]
    with patch.object(
        store, "_touch_paper", side_effect=sqlite3.OperationalError("fixture")
    ):
        with pytest.raises(sqlite3.OperationalError):
            store.edit_papers(
                ["a-1"], "add", name="transaction rollback", revisions={"a-1": 1}
            )
    assert all(t["name"] != "transaction rollback" for t in store.tags())


def test_250_papers_all_any_counts_fixed_selection_and_metadata(keyword_app):
    _, service, c, h, u, o = keyword_app
    store = service.store(u["id"])
    identity = Identity(u["id"], u["username"], u["role"])

    def seed():
        base = PaperDAO.get_paper("a-0")
        for i in range(250):
            PaperDAO.save_paper(
                {
                    **base,
                    "id": "large-" + str(i),
                    "title": "Shared topic " + str(i),
                    "starred": i % 2 == 0,
                }
            )

    run_as_identity(identity, seed)
    ids = ["large-" + str(i) for i in range(250)]
    store.edit_papers(ids, "add", name="第一组")
    store.edit_papers(ids[:150], "add", name="第二组")
    tags = store.tags()
    a = next(t["id"] for t in tags if t["name"] == "第一组")
    b = next(t["id"] for t in tags if t["name"] == "第二组")
    query = {"query": "Shared topic", "tagIds": [a, b], "tagMode": "all"}
    result = c.get(
        "/api/library/papers", query_string={"filter": json.dumps(query)}
    ).json
    assert result["total"] == 150 and len(result["items"]) == 50
    query["tagMode"] = "any"
    result = c.get(
        "/api/library/papers", query_string={"filter": json.dumps(query), "page": 5}
    ).json
    assert result["total"] == 250 and len(result["items"]) == 50
    query["scope"] = "favorites"
    assert (
        c.get("/api/library/papers", query_string={"filter": json.dumps(query)}).json[
            "total"
        ]
        == 125
    )
    fixed = c.post("/api/library/selections", json={"selection": query}, headers=h).json
    run_as_identity(
        identity,
        PaperDAO.save_paper,
        {
            **run_as_identity(identity, PaperDAO.get_paper, "a-0"),
            "id": "later",
            "title": "Shared topic later",
        },
    )
    preview = c.post(
        "/api/keywords/preview", json={"selectionId": fixed["id"]}, headers=h
    ).json
    assert preview["count"] == 125 and "later" not in preview["paperIds"]
    assert set(MetadataStore(store.db_path, store.owner).selection(query)) == set(
        preview["paperIds"]
    )
    other = service.store(o["id"])
    assert other.tags() == []


def test_jobs_auto_off_cache_cancel_deleted_and_metadata_clear(keyword_app):
    _, service, c, h, u, o = keyword_app
    store = service.store(u["id"])
    store.settings({"automatic": False})
    service.pending()
    assert not store.batches()
    bid = store.create(["a-0", "a-1"])
    assert store.create(["a-1", "a-0"]) == bid
    result = run_batch(service, u["id"], bid)
    assert result["counts"].get("completed") == 2, result
    before = store.get("a-0")
    assert before["tags"]
    edit(c, h, action="remove", tagId=before["tags"][0]["id"])
    again = store.create(["a-0"])
    assert run_batch(service, u["id"], again)["counts"] == {"reused": 1}
    assert before["tags"][0]["id"] not in [t["id"] for t in store.get("a-0")["tags"]]
    cancel = store.create(["a-2"])
    store.cancel(cancel)
    assert store.batch(cancel)["counts"] == {"cancelled": 1}
    retry = store.retry(cancel)
    assert retry != cancel
    run_as_identity(
        Identity(u["id"], u["username"], u["role"]), PaperDAO.delete_paper, "a-2"
    )
    assert run_batch(service, u["id"], retry)["counts"] == {"deleted": 1}
    metadata = MetadataStore(store.db_path, store.owner)
    v = metadata.get("a-0")
    metadata.edit("a-0", {"abstract": ""}, v["revision"])
    source = service.source(u["id"], "a-0")
    assert all(s["kind"] != "abstract" for s in source["sections"])


def test_model_cached_format_failure_and_no_automatic_retry(keyword_app, monkeypatch):
    _, service, c, h, u, o = keyword_app
    store = service.store(u["id"])
    calls = []
    monkeypatch.setattr(
        service,
        "configuration",
        lambda owner: (
            {
                "model": "fixture",
                "baseUrl": "https://example.invalid",
                "key": "fixture",
            },
            {"model": "fixture", "revision": 1},
        ),
    )

    def model(profile, messages, limit, deadline):
        calls.append(1)
        return {
            "status": "completed",
            "text": json.dumps(
                [{"name": "多模态推理", "quote": "多模态系统"}], ensure_ascii=False
            ),
        }

    service.model_request = model
    preview = service.prepare(u["id"], ["a-0"], "model")
    bid = service.create(u["id"], ["a-0"], "model", preview["previewKey"])
    assert run_batch(service, u["id"], bid)["counts"] == {"completed": 1}
    preview = service.prepare(u["id"], ["a-0"], "model")
    assert preview["requests"] == 0
    bid = service.create(u["id"], ["a-0"], "model", preview["previewKey"])
    assert run_batch(service, u["id"], bid)["counts"] == {"reused": 1}
    assert len(calls) == 1
    # Configuration revision invalidates the cache, but failure never clears tags.
    monkeypatch.setattr(
        service,
        "configuration",
        lambda owner: (
            {
                "model": "fixture",
                "baseUrl": "https://example.invalid",
                "key": "fixture",
            },
            {"model": "fixture", "revision": 2},
        ),
    )
    service.model_request = lambda *a, **k: {
        "status": "completed",
        "text": '[{"name":"bad","quote":"fabricated"}]',
    }
    preview = service.prepare(u["id"], ["a-0"], "model")
    bid = service.create(u["id"], ["a-0"], "model", preview["previewKey"])
    assert run_batch(service, u["id"], bid)["counts"] == {"failed": 1}
    assert store.get("a-0")["tags"][0]["name"] == "多模态推理"
    with store.connection() as db:
        assert (
            db.execute(
                "SELECT requests FROM keyword_items WHERE batch_id=?", (bid,)
            ).fetchone()[0]
            == 1
        )


def test_unrelated_concepts_punctuation_and_extractive_phrases():
    assert normalize_phrase("RL") == "强化学习"
    assert normalize_phrase("offline RL") == "离线强化学习"
    assert normalize_phrase("C++") == "C++"
    source = {
        "sections": [
            {
                "kind": "title",
                "text": "Quasar spectroscopy with adaptive telescope calibration",
            },
            {
                "kind": "abstract",
                "text": "Adaptive telescope calibration improves quasar spectroscopy. Quasar spectroscopy measures distant emission lines. Telescope calibration uses stable spectral references.",
            },
        ],
        "terms": [],
    }
    result, _ = extract(source)
    assert any("quasar spectroscopy" in x["name"].lower() for x in result)
    assert all(
        x["quote"] in "\n".join(s["text"] for s in source["sections"]) for x in result
    )


def test_thirty_authored_phrase_samples_and_evidence():
    from pathlib import Path

    samples = json.loads(
        (Path(__file__).parent / "fixtures/keywords/samples.json").read_text()
    )
    for case in samples:
        source = {
            "sections": [
                {"kind": "title", "text": case["title"]},
                {"kind": "abstract", "text": case["abstract"]},
            ],
            "terms": [{"text": v, "source": "source"} for v in case["rawKeywords"]],
        }
        tags, stats = extract(source)
        assert normalize_phrase(case["mainTerm"]).casefold() in [
            t["name"].casefold() for t in tags
        ], case["id"]
        assert len(tags) <= 8 and all(len(t["name"]) <= 48 for t in tags)
        assert all(
            t["quote"]
            in "\n".join([case["title"], case["abstract"], *case["rawKeywords"]])
            for t in tags
        )
    assert not extract({"sections": [{"kind": "title", "text": "合成文献验证"}]})[0]


def test_manual_synonym_exclusion_and_automatic_layer_limit(keyword_app):
    _, service, c, h, u, o = keyword_app
    store = service.store(u["id"])
    tag = edit(c, h, action="add", name="RL")["tags"][0]
    edit(c, h, action="remove", tagId=tag["id"])
    head = store.get("a-0")
    store.apply(
        "a-0",
        [{"name": "强化学习"}, {"name": "离线强化学习"}],
        "x",
        head["revision"],
        "local",
        {},
    )
    assert [t["name"] for t in store.get("a-0")["tags"]] == ["离线强化学习"]
    for method in ["local", "model"]:
        head = store.get("a-0")
        store.apply(
            "a-0",
            [{"name": f"{method} term {i}"} for i in range(8)],
            method,
            head["revision"],
            method,
            {},
        )
    assert len(store.get("a-0")["tags"]) == 8


def test_reupgrade_reconciles_old_writes_without_resurrection(keyword_app):
    _, service, c, h, u, o = keyword_app
    store = service.store(u["id"])
    service.reconcile()
    tag = edit(c, h, action="add", name="protected deletion")["tags"][0]
    edit(c, h, action="remove", tagId=tag["id"])
    # Old binary does not call new hooks; emulate its actual DB projection writes.
    with store.connection(True) as db:
        db.execute("DELETE FROM keyword_pending")
        fields = json.loads(
            db.execute(
                "SELECT fields_json FROM bibliography WHERE paper_id=?", ("a-0",)
            ).fetchone()[0]
        )
        fields["title"] = "New title after rollback"
        db.execute(
            "UPDATE bibliography SET fields_json=? WHERE paper_id=?",
            (json.dumps(fields), "a-0"),
        )
        db.execute("DELETE FROM papers WHERE id=?", ("a-1",))
    service.reconcile()
    with store.connection() as db:
        assert db.execute(
            "SELECT 1 FROM keyword_pending WHERE paper_id=?", ("a-0",)
        ).fetchone()
        assert not db.execute(
            "SELECT 1 FROM keyword_links WHERE paper_id=?", ("a-1",)
        ).fetchone()
        assert db.execute(
            "SELECT 1 FROM keyword_exclusions WHERE paper_id=? AND tag_id=?",
            ("a-0", tag["id"]),
        ).fetchone()
    assert not store.get("a-0")["tags"]
    assert (
        service.source(u["id"], "a-0")["sections"][0]["text"]
        == "New title after rollback"
    )


def test_cancel_late_model_response_and_storage_checkpoint(keyword_app, monkeypatch):
    _, service, c, h, u, o = keyword_app
    store = service.store(u["id"])
    monkeypatch.setattr(
        service,
        "configuration",
        lambda owner: ({"model": "fixture"}, {"model": "fixture", "revision": 1}),
    )
    bid = store.create(["a-0"], "model")

    def late(*a, **kw):
        store.cancel(bid)
        return {"status": "completed", "text": '[{"name":"late","quote":"多模态系统"}]'}

    service.model_request = late
    assert run_batch(service, u["id"], bid)["counts"] == {"cancelled": 1}
    assert not store.get("a-0")["tags"]
    # A committed result cannot become failed because writing a later event fails.
    bid = store.create(["a-0"])
    result = run_batch(service, u["id"], bid)
    with store.connection() as db:
        item = db.execute(
            "SELECT id FROM keyword_items WHERE batch_id=?", (bid,)
        ).fetchone()[0]
    service.finish(store, item, "failed", error="keyword_storage_failed")
    assert store.batch(bid)["counts"] == {"completed": 1}


def test_undo_new_tag_cannot_orphan_later_paper_links(keyword_app):
    _, service, c, h, user, _ = keyword_app
    store = service.store(user["id"])
    receipt = store.edit_papers(["a-0"], "add", name="Shared tag")
    tag = store.get("a-0")["tags"][0]
    store.edit_papers(["a-1"], "add", tag_id=tag["id"])
    with pytest.raises(KeywordError, match="tag_undo_conflict"):
        store.undo(receipt["operationId"])
    assert store.get("a-1")["tags"][0]["id"] == tag["id"]


def test_full_queue_still_dispatches_and_daily_admission_is_local(keyword_app):
    _, service, c, h, user, _ = keyword_app
    identity = Identity(user["id"], user["username"], user["role"])

    def seed():
        for i in range(22):
            PaperDAO.save_paper(
                {
                    "id": f"queue-{i}",
                    "title": "Sparse attention for sequence modeling",
                    "file_path": f"/data/papers/{i}.pdf",
                }
            )
        PaperDAO.save_paper(
            {
                "id": "daily-not-admitted",
                "title": "Sparse attention",
                "is_daily": True,
                "file_path": "/data/papers/.daily_arxiv_temp/test.pdf",
            }
        )

    run_as_identity(identity, seed)
    service.pending()
    # A full pending queue must not throw before the dispatcher can run its work.
    service.pending()
    service.dispatch()
    assert service.active
    for future in list(service.active.values()):
        future.result(timeout=15)
    store = service.store(user["id"])
    with store.connection() as db:
        assert not db.execute(
            "SELECT 1 FROM keyword_items WHERE paper_id='daily-not-admitted'"
        ).fetchone()
        assert db.execute(
            "SELECT 1 FROM keyword_items WHERE status='completed'"
        ).fetchone()

    def admit():
        paper = PaperDAO.get_paper("daily-not-admitted")
        paper["file_path"] = "/data/papers/.categories/admitted/daily.pdf"
        PaperDAO.save_paper(paper)

    run_as_identity(identity, admit)
    with store.connection() as db:
        assert db.execute(
            "SELECT 1 FROM keyword_pending WHERE paper_id='daily-not-admitted'"
        ).fetchone()


def test_merged_deleted_filter_redirect_and_model_preview_checkpoint(
    keyword_app, monkeypatch
):
    _, service, c, h, user, _ = keyword_app
    store = service.store(user["id"])
    first = edit(c, h, action="add", name="old concept")["tags"][0]
    target = edit(c, h, paper="a-1", action="add", name="target concept")["tags"][0]
    versions = {t["id"]: t["revision"] for t in store.tags()}
    store.edit_tag(
        first["id"],
        {
            "action": "merge",
            "targetId": target["id"],
            "revision": versions[first["id"]],
            "targetRevision": versions[target["id"]],
        },
    )
    assert store.redirects()[first["id"]] == target["id"]
    current = next(t for t in store.tags() if t["id"] == target["id"])
    store.edit_tag(target["id"], {"action": "delete", "revision": current["revision"]})
    assert store.redirects()[first["id"]] is None
    revision = [1]
    monkeypatch.setattr(
        service,
        "configuration",
        lambda owner: ({"model": "fake"}, {"model": "fake", "revision": revision[0]}),
    )
    preview = service.prepare(user["id"], ["a-0"], "model", "metadata")
    old = service.create(
        user["id"], ["a-0"], "model", preview["previewKey"], "metadata"
    )
    revision[0] = 2
    updated = service.prepare(user["id"], ["a-0"], "model", "metadata")
    new = service.create(
        user["id"], ["a-0"], "model", updated["previewKey"], "metadata"
    )
    assert old != new
    with store.connection() as db:
        saved = {
            r["batch_id"]: json.loads(r["checkpoint_json"])
            for r in db.execute("SELECT * FROM keyword_items")
        }
    assert saved[old]["expectedInput"] == preview["items"][0]["inputKey"]
    assert saved[new]["expectedInput"] == updated["items"][0]["inputKey"]
    assert saved[new]["inputScope"] == "metadata"
    assert "linkedItem" not in saved[new]
    calls = []
    service.model_request = lambda *a, **kw: calls.append(1) or {
        "status": "completed",
        "text": '[{"name":"多模态系统","quote":"多模态系统"}]',
    }
    assert run_batch(service, user["id"], old)["counts"] == {"stale": 1}
    assert calls == []
    assert run_batch(service, user["id"], new)["counts"] == {"completed": 1}
    assert calls == [1]


def test_existing_original_scans_bounded_blocks_without_images():
    from types import SimpleNamespace
    from ipaper.keywords.source import existing_original

    calls = []

    class Store:
        def results(self, paper):
            return [
                {
                    "id": "r",
                    "kind": "structure",
                    "status": "completed",
                    "document_id": "d",
                    "config_json": json.dumps(
                        {"normalizer": "mineru-content-list-v1/1"}
                    ),
                }
            ]

        def document(self, doc):
            return {"sha256": "matching"}

        def blocks(self, result, after, limit):
            calls.append(after)
            return [
                {"order": i, "text": "bounded original text", "image": "never-open.png"}
                for i in range(after + 1, after + 101)
            ]

    source = SimpleNamespace(store=Store(), _coverage=lambda r, d: {"complete": True})
    result = existing_original(source, "paper", "matching", limit=300)
    assert result["coverage"]["keywordTruncated"]
    assert sum(len(u["text"]) for u in result["units"]) == 300
    assert calls == [-1]


def test_adopted_change_during_running_item_is_not_lost(keyword_app, monkeypatch):
    _, service, c, h, user, _ = keyword_app
    owner = user["id"]
    store = service.store(owner)
    # Consume import events and choose one running item deterministically.
    service.pending()
    with store.connection() as db:
        original = db.execute(
            "SELECT id,batch_id FROM keyword_items WHERE paper_id='a-0'"
        ).fetchone()
    real_extract = extract
    changed = []

    def race(source, cancel):
        if not changed:
            changed.append(True)
            metadata = MetadataStore(service.db_path, owner)
            prior = metadata.get("a-0")
            metadata.edit(
                "a-0",
                {"title": "Causal inference for treatment effects"},
                prior["revision"],
            )
            service.pending()
        return real_extract(source, cancel)

    monkeypatch.setattr("ipaper.keywords.service.extract", race)
    service.run(original["id"], owner)
    assert store.batch(original["batch_id"])["counts"] == {"stale": 1}
    with store.connection() as db:
        successor = db.execute(
            "SELECT id FROM keyword_items WHERE paper_id='a-0' AND status='queued'"
        ).fetchone()
    assert successor is not None
    service.run(successor["id"], owner)
    assert store.get("a-0")["status"] == "ready"
    # The final transaction also rejects a metadata change after content checking.
    source = service.source(owner, "a-0")
    revision = store.get("a-0")["revision"]
    metadata = MetadataStore(service.db_path, owner)
    prior = metadata.get("a-0")
    metadata.edit("a-0", {"title": "Another adopted title"}, prior["revision"])
    with pytest.raises(KeywordError, match="keyword_source_changed"):
        store.apply(
            "a-0",
            [{"name": "late"}],
            "x",
            revision,
            "local",
            {},
            expected_source=source["adoptedSignature"],
        )
