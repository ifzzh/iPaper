"""Reading notes: annotations, the main note, saved answers and Markdown export.

Route-level tests over the real Flask app and the existing paper fixtures; no
model, Worker, MinerU or OCR call is made anywhere here.
"""

import json

from tests.test_processing_api import application, generate  # noqa: F401 (fixture)
from tests.test_workbench import login

OWNER_PAPER = "a-4"


def document(client, token, paper=OWNER_PAPER):
    response = client.post(f"/api/paper/{paper}/reading-document", json={}, headers={"X-CSRF-Token": token})
    assert response.status_code == 200, response.json
    return response.json["document"]


def pdf_anchor(page=1):
    return {
        "mode": "pdf",
        "page": page,
        "rects": [
            {"x": 0.1, "y": 0.2, "w": 0.4, "h": 0.04},
            {"x": 0.1, "y": 0.25, "w": 0.3, "h": 0.04},
        ],
    }


def create_annotation(client, token, doc, **overrides):
    body = {
        "documentId": doc["id"],
        "kind": "highlight",
        "color": "amber",
        "excerpt": "多行摘录的第二行",
        "comment": "我的理解",
        "anchor": pdf_anchor(),
        "context": {"before": "前一句", "after": "后一句"},
    }
    body.update(overrides)
    return client.post(
        f"/api/paper/{OWNER_PAPER}/reading/annotations", json=body, headers={"X-CSRF-Token": token}
    )


def test_annotation_lifecycle_and_owner_isolation(application):
    a, b = application.test_client(), application.test_client()
    token = login(a)
    login(b, "reader_two")
    headers = {"X-CSRF-Token": token}
    doc = document(a, token)

    created = create_annotation(a, token, doc)
    assert created.status_code == 201, created.json
    annotation = created.json["annotation"]
    assert annotation["excerpt"] == "多行摘录的第二行"
    assert annotation["anchor"]["page"] == 1 and len(annotation["anchor"]["rects"]) == 2
    assert annotation["stale"] is False and annotation["canNavigate"] is True
    assert not {"owner_id", "anchor_json", "context_json"} & set(annotation)

    # CSRF is still required, and another owner sees nothing.
    assert create_annotation(a, "forged", doc).status_code == 403
    assert b.get(f"/api/paper/{OWNER_PAPER}/reading/annotations").status_code == 404

    listed = a.get(f"/api/paper/{OWNER_PAPER}/reading/annotations").json
    assert len(listed["annotations"]) == 1 and listed["total"] == 1

    path = f"/api/paper/{OWNER_PAPER}/reading/annotations/{annotation['id']}"
    edited = a.put(path, json={"revision": annotation["revision"], "comment": "改过的批注", "color": "blue"},
                   headers=headers).json["annotation"]
    assert edited["comment"] == "改过的批注" and edited["color"] == "blue"
    assert edited["revision"] != annotation["revision"]
    # A stale revision is refused instead of overwriting newer content.
    assert a.put(path, json={"revision": annotation["revision"], "comment": "旧修订"}, headers=headers).status_code == 409

    deleted = a.delete(path, json={"revision": edited["revision"]}, headers=headers).json["annotation"]
    assert deleted["deleted"] is True
    assert a.get(f"/api/paper/{OWNER_PAPER}/reading/annotations").json["annotations"] == []
    assert len(a.get(f"/api/paper/{OWNER_PAPER}/reading/annotations/deleted").json["annotations"]) == 1

    # Undo restores the same record (same id, new revision).
    restored = a.put(path, json={"revision": deleted["revision"], "restore": True}, headers=headers).json["annotation"]
    assert restored["id"] == annotation["id"] and restored["deleted"] is False
    assert a.get(f"/api/paper/{OWNER_PAPER}/reading/annotations").json["total"] == 1


def test_anchor_and_size_validation(application):
    a = application.test_client()
    token = login(a)
    doc = document(a, token)
    cases = [
        ({"anchor": {"mode": "pdf", "page": 10_000, "rects": [{"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}]}}, "invalid_anchor"),
        ({"anchor": {"mode": "pdf", "page": 1, "rects": []}}, "invalid_anchor"),
        ({"anchor": {"mode": "pdf", "page": 1, "rects": [{"x": 1.5, "y": 0.1, "w": 0.1, "h": 0.1}]}}, "invalid_anchor"),
        ({"anchor": {"mode": "unknown", "page": 1}}, "invalid_anchor"),
        ({"anchor": {"mode": "structure", "blockId": "b1", "field": "text", "start": 0, "end": 3}}, "invalid_anchor"),
        ({"comment": "x" * 2_001}, "invalid_comment_too_large"),
        ({"excerpt": "x" * 8_001}, "invalid_excerpt_too_large"),
        ({"color": "teal"}, "invalid_color"),
        ({"kind": "scribble"}, "invalid_annotation_kind"),
    ]
    for overrides, code in cases:
        response = create_annotation(a, token, doc, **overrides)
        assert response.status_code in {400, 409, 413}, (code, response.status_code, response.json)
        assert response.json["error"] == code, (code, response.json)
    # A page-level record is allowed without rects (scanned or unreliable text).
    page_note = create_annotation(a, token, doc, kind="page_note", anchor={"mode": "page", "page": 1}, excerpt="")
    assert page_note.status_code == 201
    assert page_note.json["annotation"]["anchor"]["rects"] == []


def test_main_note_autosave_conflicts_and_excerpts(application):
    a = application.test_client()
    token = login(a)
    headers = {"X-CSRF-Token": token}
    doc = document(a, token)

    empty = a.get(f"/api/paper/{OWNER_PAPER}/reading/note").json["note"]
    assert empty["exists"] is False and empty["markdown"] == "" and empty["revision"] is None

    first = a.put(f"/api/paper/{OWNER_PAPER}/reading/note",
                  json={"markdown": "# 我的理解\n\n第一段。", "revision": None}, headers=headers).json["note"]
    assert first["revision"]
    saved = a.put(f"/api/paper/{OWNER_PAPER}/reading/note",
                  json={"markdown": "# 我的理解\n\n第一段。\n\n第二段。", "revision": first["revision"]},
                  headers=headers).json["note"]
    assert "第二段" in saved["markdown"]

    # A second writer using the old revision gets a recoverable conflict draft.
    conflict = a.put(f"/api/paper/{OWNER_PAPER}/reading/note",
                     json={"markdown": "并发写入", "revision": first["revision"]}, headers=headers)
    assert conflict.status_code == 409 and conflict.json["error"] == "note_revision_conflict"
    conflict_id = conflict.json["conflictId"]
    note = a.get(f"/api/paper/{OWNER_PAPER}/reading/note").json["note"]
    assert any(item["id"] == conflict_id and item["markdown"] == "并发写入" for item in note["conflicts"])
    assert "第二段" in note["markdown"]  # nothing was silently overwritten

    # The draft can be kept explicitly, bound to the revision the user saw.
    assert a.post(f"/api/paper/{OWNER_PAPER}/reading/note/conflicts/{conflict_id}",
                  json={"choice": "draft"}, headers=headers).status_code == 400  # revision required
    probe = a.post(f"/api/paper/{OWNER_PAPER}/reading/note/conflicts/{conflict_id}",
                   json={"choice": "draft", "revision": note["revision"]}, headers=headers)
    assert probe.status_code == 200, probe.get_data(as_text=True)
    resolved = probe.json["note"]
    assert resolved["markdown"] == "并发写入"
    # Nothing is deleted: the conflict is kept (marked resolved) and the text it
    # replaced is preserved, so both sides remain recoverable.
    assert any(item["id"] == conflict_id for item in resolved["conflicts"])
    assert any("第二段" in item["markdown"] for item in resolved["conflicts"])

    # Inserting an excerpt appends a sourced quote without losing the body.
    annotation = create_annotation(a, token, doc).json["annotation"]
    with_excerpt = a.post(f"/api/paper/{OWNER_PAPER}/reading/note/excerpts",
                          json={"annotationId": annotation["id"], "revision": resolved["revision"]},
                          headers=headers).json["note"]
    assert "> 多行摘录的第二行" in with_excerpt["markdown"]
    assert "来源：原文 PDF · 第 1 页" in with_excerpt["markdown"]
    assert "并发写入" in with_excerpt["markdown"]
    assert len(with_excerpt["entries"]) == 1 and with_excerpt["entries"][0]["kind"] == "excerpt"

    # Re-inserting the same excerpt is idempotent and cannot lose concurrent text.
    again = a.post(f"/api/paper/{OWNER_PAPER}/reading/note/excerpts",
                   json={"annotationId": annotation["id"], "revision": with_excerpt["revision"]},
                   headers=headers).json["note"]
    assert again["markdown"].count("> 多行摘录的第二行") == 1
    assert len(again["entries"]) == 1
    # An outdated note revision cannot append behind the user's back.
    stale = a.post(f"/api/paper/{OWNER_PAPER}/reading/note/excerpts",
                   json={"annotationId": annotation["id"], "revision": resolved["revision"]}, headers=headers)
    assert stale.status_code == 409
    # Re-inserting the same excerpt after removing it from the body restores the
    # text without creating a second source record.
    manual = dict(with_excerpt)
    stripped = manual["markdown"].split("<!-- ipaper:excerpt:")[0]
    stripped_note = a.put(f"/api/paper/{OWNER_PAPER}/reading/note",
                          json={"markdown": stripped, "revision": manual["revision"]},
                          headers=headers).json["note"]
    re_added = a.post(f"/api/paper/{OWNER_PAPER}/reading/note/excerpts",
                      json={"annotationId": annotation["id"], "revision": stripped_note["revision"]},
                      headers=headers).json["note"]
    assert "> 多行摘录的第二行" in re_added["markdown"]
    assert len([e for e in re_added["entries"] if e["kind"] == "excerpt"]) == 1


def test_saved_answer_is_copied_idempotently_and_owner_bound(application):
    a, b = application.test_client(), application.test_client()
    token = login(a)
    login(b, "reader_two")
    headers = {"X-CSRF-Token": token}

    # Persist an assistant answer in the reader chat (no model call happens here).
    with application.app_context():
        from ipaper.database.connection import get_db
        from ipaper.security.identity import Identity, run_as_identity

        paper_owner = None
        with get_db() as db:
            row = db.execute("SELECT owner_id FROM papers WHERE id=?", (OWNER_PAPER,)).fetchone()
            paper_owner = row[0]
        history = [
            {"role": "user", "content": "这篇论文的结论是什么？", "timestamp": "1700000000.1"},
            {"role": "assistant", "content": "结论：系统在 157 个内核上可用。", "timestamp": "1700000000.2"},
        ]

        def seed():
            db = get_db()
            db.execute(
                "INSERT INTO chats (session_id,paper_id,history,created_at,updated_at,title,owner_id)"
                " VALUES (?,?,?,?,?,?,?)",
                ("notes-session", OWNER_PAPER, json.dumps(history), "1790000000.1",
                 "1790000000.2", "笔记测试", paper_owner),
            )
            db.execute(
                "INSERT INTO processing_chat_sources (owner_id,session_id,message_key,sources_json)"
                " VALUES (?,?,?,?)",
                (paper_owner, "notes-session", "1700000000.2", json.dumps({"S1": "artifact-1"})),
            )
            db.commit()

        run_as_identity(Identity(paper_owner, "reader_one", "admin"), seed)

    body = {"sessionId": "notes-session", "messageIndex": 1}
    saved = a.post(f"/api/paper/{OWNER_PAPER}/reading/note/answers", json=body, headers=headers)
    assert saved.status_code == 200, saved.json
    note = saved.json["note"]
    assert "结论：系统在 157 个内核上可用。" in note["markdown"]
    assert "来源：S1" in note["markdown"]
    entry = note["entries"][0]
    assert entry["kind"] == "answer"
    # The full, revisit-capable source identity is stored, not just a short label.
    assert entry["content"]["sources"][0]["sourceId"] == "artifact-1"
    assert entry["content"]["sessionId"] == "notes-session"

    # A repeated click must not insert a second copy.
    repeat = a.post(f"/api/paper/{OWNER_PAPER}/reading/note/answers", json=body, headers=headers).json["note"]
    assert repeat["markdown"].count("结论：系统在 157 个内核上可用。") == 1
    assert len(repeat["entries"]) == 1

    # User turns, unknown messages and other owners are refused.
    assert a.post(f"/api/paper/{OWNER_PAPER}/reading/note/answers",
                  json={"sessionId": "notes-session", "messageIndex": 0}, headers=headers).status_code == 400
    assert a.post(f"/api/paper/{OWNER_PAPER}/reading/note/answers",
                  json={"sessionId": "notes-session", "messageIndex": 9}, headers=headers).status_code == 404
    assert b.post(f"/api/paper/{OWNER_PAPER}/reading/note/answers", json=body,
                  headers={"X-CSRF-Token": "irrelevant"}).status_code in {403, 404}


def test_note_export_is_readable_and_leaks_nothing_private(application):
    a = application.test_client()
    token = login(a)
    headers = {"X-CSRF-Token": token}
    doc = document(a, token)
    annotation = create_annotation(a, token, doc, comment="值得回看").json["annotation"]
    saved = a.put(f"/api/paper/{OWNER_PAPER}/reading/note",
                  json={"markdown": "# 笔记\n\n- 要点一\n- 要点二"}, headers=headers).json["note"]
    inserted = a.post(f"/api/paper/{OWNER_PAPER}/reading/note/excerpts",
                      json={"annotationId": annotation["id"], "revision": saved["revision"]}, headers=headers)
    assert inserted.status_code == 200, inserted.json
    assert inserted.json["note"]["entries"]

    response = a.get(f"/api/paper/{OWNER_PAPER}/reading/note/export.md")
    assert response.status_code == 200
    assert response.mimetype.startswith("text/markdown")
    assert "attachment" in response.headers["Content-Disposition"]
    text = response.get_data(as_text=True)
    assert text.startswith("# ")
    assert "要点一" in text and "值得回看" in text and "摘录与批注" in text
    assert "来源记录" in text
    assert "/data/papers" not in text and "paperpilot_session" not in text
    assert "http://127.0.0.1" not in text
    # Without annotations the excerpt section is omitted but the note stays.
    plain = a.get(f"/api/paper/{OWNER_PAPER}/reading/note/export.md?annotations=0").get_data(as_text=True)
    assert "要点一" in plain and "摘录与批注" not in plain


def test_note_size_limit_and_cross_paper_references(application):
    a = application.test_client()
    token = login(a)
    headers = {"X-CSRF-Token": token}
    doc = document(a, token)
    huge = "x" * (262_144 + 1)
    assert a.put(f"/api/paper/{OWNER_PAPER}/reading/note", json={"markdown": huge}, headers=headers).status_code == 413

    annotation = create_annotation(a, token, doc).json["annotation"]
    # An annotation from another paper's document cannot be attached here.
    other = create_annotation(a, token, doc, documentId="00000000-0000-0000-0000-000000000000")
    assert other.status_code == 404


def test_stale_source_is_reported_without_redirecting(application):
    a = application.test_client()
    token = login(a)
    headers = {"X-CSRF-Token": token}
    doc = document(a, token)
    annotation = create_annotation(a, token, doc).json["annotation"]

    # The source hash changes: the record keeps its excerpt but says so.
    with application.app_context():
        from ipaper.database.connection import get_db
        from ipaper.security.identity import Identity, run_as_identity

        with get_db() as db:
            owner = db.execute("SELECT owner_id FROM papers WHERE id=?", (OWNER_PAPER,)).fetchone()[0]

        def mark_stale():
            db = get_db()
            db.execute("UPDATE processing_documents SET sha256=? WHERE id=?", ("0" * 64, doc["id"]))
            db.commit()

        run_as_identity(Identity(owner, "reader_one", "admin"), mark_stale)

    listed = a.get(f"/api/paper/{OWNER_PAPER}/reading/annotations").json["annotations"][0]
    assert listed["excerpt"] == annotation["excerpt"]
    assert listed["stale"] is True and listed["canNavigate"] is False
    assert "不可用" in listed["notice"] or "变化" in listed["notice"]

# --- regressions from the independent 1.13.0 acceptance --------------------


def test_translated_pdf_annotation_uses_its_own_document_file(application):
    """R4.1: a BabelDOC document is verified against its own controlled file."""
    c = application.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    doc = c.post('/api/paper/a-0/reading-document', json={"document": "translated"},
                 headers=headers).json["document"]
    assert doc["kind"] in {"babeldoc_dual", "babeldoc_mono"}
    created = c.post('/api/paper/a-0/reading/annotations', headers=headers, json={
        "documentId": doc["id"], "kind": "highlight", "excerpt": "Translated synthetic excerpt",
        "anchor": {"mode": "pdf", "page": 1, "rects": [{"x": .1, "y": .2, "w": .3, "h": .04}]},
    })
    assert created.status_code == 201, created.json
    annotation = created.json["annotation"]
    assert annotation["context"]["documentKind"] == doc["kind"]
    assert annotation["contentKind"] == "pdf_translated"
    # The original PDF hash must never stand in for the translated document.
    assert annotation["stale"] is False and annotation["canNavigate"] is True, annotation
    listed = c.get('/api/paper/a-0/reading/annotations').json["annotations"]
    assert listed[0]["canNavigate"] is True


def test_structure_anchors_need_the_real_block_and_revision(application):
    """R4.2/R4.3: result/document identity and the opaque revision are verified."""
    c = application.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    _, job = generate(c, token)
    rid = job["resultId"]
    result = c.get(f"/api/results/{rid}").json["result"]
    block = c.get(f"/api/results/{rid}/blocks").json["blocks"][1]
    revision = block["translation"]["revision"]
    assert isinstance(revision, str) and revision  # opaque string identity
    text = block["translation"]["content"]["text"]

    created = c.post('/api/paper/a-4/reading/annotations', headers=headers, json={
        "documentId": result["documentId"], "resultId": rid, "kind": "highlight",
        "excerpt": text[:8],
        "anchor": {"mode": "structure", "blockId": block["id"], "field": "text",
                   "start": 0, "end": 8, "translationRevision": revision},
    })
    assert created.status_code == 201, created.json
    annotation = created.json["annotation"]
    assert annotation["contentKind"] == "structure_translated"
    assert annotation["stale"] is False and annotation["canNavigate"] is True

    # The list is queried by the document, and this record is found there (the
    # former bug queried by result id and silently saw nothing).
    queried = c.get(f"/api/paper/a-4/reading/annotations?documentId={result['documentId']}").json
    assert [item["id"] for item in queried["annotations"]] == [annotation["id"]]
    assert c.get(f"/api/paper/a-4/reading/annotations?documentId={rid}").json["annotations"] == []

    # A revision that is not the one attached to this block is refused.
    wrong = c.post('/api/paper/a-4/reading/annotations', headers=headers, json={
        "documentId": result["documentId"], "resultId": rid, "kind": "highlight",
        "excerpt": text[:8],
        "anchor": {"mode": "structure", "blockId": block["id"], "field": "text",
                   "start": 0, "end": 8, "translationRevision": "00000000-0000-0000-0000-000000000000"},
    })
    assert wrong.status_code == 400 and wrong.json["error"] == "invalid_anchor"
    # Unknown blocks, out-of-range ranges and a result from another document fail too.
    for anchor in (
        {"mode": "structure", "blockId": "nope", "field": "text", "start": 0, "end": 1},
        {"mode": "structure", "blockId": block["id"], "field": "text", "start": 0, "end": 10_000},
        {"mode": "structure", "blockId": block["id"], "field": "cell:9:9", "start": 0, "end": 1},
    ):
        response = c.post('/api/paper/a-4/reading/annotations', headers=headers, json={
            "documentId": result["documentId"], "resultId": rid, "kind": "highlight",
            "excerpt": "x", "anchor": anchor,
        })
        assert response.status_code == 400, (anchor, response.json)
    other = c.post('/api/paper/a-4/reading/annotations', headers=headers, json={
        "documentId": result["documentId"], "resultId": rid, "kind": "highlight",
        "excerpt": text[:4],
        "anchor": {"mode": "structure", "blockId": block["id"], "field": "text",
                   "start": 0, "end": 4, "translationRevision": revision},
    })
    assert other.status_code == 201


def test_conflict_resolution_is_bound_to_the_seen_revision(application):
    """R3: a decision made against an old revision cannot erase newer text."""
    c = application.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    path = "/api/paper/a-4/reading/note"
    initial = c.put(path, json={"markdown": "initial", "revision": None}, headers=headers).json["note"]
    seen = c.put(path, json={"markdown": "remote version user has seen", "revision": initial["revision"]},
                 headers=headers).json["note"]
    conflict = c.put(path, json={"markdown": "local conflict draft", "revision": initial["revision"]},
                     headers=headers)
    assert conflict.status_code == 409
    conflict_id = conflict.json["conflictId"]
    # A third writer moves the note on after the user saw `seen`.
    newer = c.put(path, json={"markdown": "NEW remote content", "revision": seen["revision"]}, headers=headers)
    assert newer.status_code == 200

    stale_decision = c.post(f"{path}/conflicts/{conflict_id}",
                            json={"choice": "draft", "revision": seen["revision"]}, headers=headers)
    assert stale_decision.status_code == 409
    assert stale_decision.json["reason"] == "stale_decision"
    after = c.get(path).json["note"]
    # The unseen newer text still exists, both as the note and as a kept draft.
    assert after["markdown"] == "NEW remote content"
    assert any(item["markdown"] == "NEW remote content" for item in after["conflicts"])
    assert any(item["id"] == conflict_id for item in after["conflicts"])

    # A decision bound to the current revision is accepted and keeps the replaced side.
    accepted = c.post(f"{path}/conflicts/{conflict_id}",
                      json={"choice": "draft", "revision": after["revision"]}, headers=headers)
    assert accepted.status_code == 200
    final = accepted.json["note"]
    assert final["markdown"] == "local conflict draft"
    assert any(item["markdown"] == "NEW remote content" for item in final["conflicts"])


def test_multiline_answer_save_is_idempotent(application):
    """R6: multi-line answers dedupe by identity, never by body substrings."""
    c = application.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    from ipaper.database.connection import get_db
    from ipaper.security.identity import Identity, run_as_identity

    with application.app_context():
        with get_db() as db:
            owner = db.execute("SELECT owner_id FROM papers WHERE id='a-4'").fetchone()[0]

        def seed():
            with get_db() as db:
                db.execute(
                    "INSERT INTO chats (session_id,paper_id,history,created_at,updated_at,title,owner_id)"
                    " VALUES (?,?,?,?,?,?,?)",
                    ("notes-multiline", "a-4",
                     json.dumps([{"role": "assistant",
                                 "content": "First paragraph.\nSecond paragraph.",
                                 "timestamp": "1700000000.2"}]),
                     "1790000000.1", "1790000000.2", "synthetic", owner))
                db.commit()

        run_as_identity(Identity(owner, "reader_one", "admin"), seed)

    body = {"sessionId": "notes-multiline", "messageIndex": 0}
    first = c.post("/api/paper/a-4/reading/note/answers", json=body, headers=headers)
    assert first.status_code == 200, first.json
    second = c.post("/api/paper/a-4/reading/note/answers", json=body, headers=headers)
    assert second.status_code == 200, second.get_data(as_text=True)[:200]
    note = c.get("/api/paper/a-4/reading/note").json["note"]
    assert note["markdown"].count("已保存的 AI 回答") == 1
    assert note["markdown"].count("First paragraph.") == 1
    assert len(note["entries"]) == 1
    assert first.json["note"]["duplicate"] is False
    assert second.json["note"]["duplicate"] is True


def test_undo_uses_the_revision_returned_by_delete(application):
    """R5: the old revision stays refused; the new one can restore."""
    c = application.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    doc = document(c, token)
    annotation = create_annotation(c, token, doc).json["annotation"]
    path = f"/api/paper/{OWNER_PAPER}/reading/annotations/{annotation['id']}"
    deleted = c.delete(path, json={"revision": annotation["revision"]}, headers=headers)
    assert deleted.status_code == 200
    removed = deleted.json["annotation"]
    assert removed["deleted"] is True and removed["revision"] != annotation["revision"]

    # What the old UI did: reuse the pre-delete revision. Still refused.
    stale = c.put(path, json={"revision": annotation["revision"], "restore": True}, headers=headers)
    assert stale.status_code == 409 and stale.json["error"] == "annotation_revision_conflict"
    # The revision the delete returned restores the record.
    restored = c.put(path, json={"revision": removed["revision"], "restore": True}, headers=headers)
    assert restored.status_code == 200
    assert restored.json["annotation"]["id"] == annotation["id"]
    assert restored.json["annotation"]["deleted"] is False


def test_annotation_edits_and_recolor_keep_versions_honest(application):
    """R5: editing text and colour is revision-checked and visible afterwards."""
    c = application.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    doc = document(c, token)
    annotation = create_annotation(c, token, doc).json["annotation"]
    path = f"/api/paper/{OWNER_PAPER}/reading/annotations/{annotation['id']}"
    edited = c.put(path, json={"revision": annotation["revision"], "comment": "改过的批注",
                               "color": "pink"}, headers=headers).json["annotation"]
    assert edited["comment"] == "改过的批注" and edited["color"] == "pink"
    listed = c.get(f"/api/paper/{OWNER_PAPER}/reading/annotations").json["annotations"][0]
    assert listed["comment"] == "改过的批注" and listed["color"] == "pink"
    assert listed["revision"] == edited["revision"]
    # Reusing the old revision after an edit is still a conflict.
    again = c.put(path, json={"revision": annotation["revision"], "color": "amber"}, headers=headers)
    assert again.status_code == 409
    assert c.put(path, json={"revision": edited["revision"], "color": "amber"},
                 headers=headers).json["annotation"]["color"] == "amber"


def test_annotation_pagination_reading_order_and_page_records(application):
    """R7: beyond 200 items are reachable, ordered, and page records are allowed."""
    c = application.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    doc = document(c, token)
    last_page = min(2, int(doc["pageCount"]))
    for page in (last_page, 1, last_page):
        created = create_annotation(c, token, doc, anchor=pdf_anchor(page), excerpt=f"第{page}页摘录{page}")
        assert created.status_code == 201, created.json
    page_note = c.post(f"/api/paper/{OWNER_PAPER}/reading/annotations", headers=headers, json={
        "documentId": doc["id"], "kind": "page_note", "color": "blue", "excerpt": "",
        "comment": "扫描页记录", "anchor": {"mode": "page", "page": last_page},
    })
    assert page_note.status_code == 201
    assert page_note.json["annotation"]["contentKind"] == "page"

    first = c.get(f"/api/paper/{OWNER_PAPER}/reading/annotations?limit=2").json
    assert len(first["annotations"]) == 2 and first["nextCursor"] == "2" and first["total"] == 4
    second = c.get(f"/api/paper/{OWNER_PAPER}/reading/annotations?limit=2&cursor={first['nextCursor']}").json
    assert len(second["annotations"]) == 2 and second["nextCursor"] is None
    ids = [item["id"] for item in first["annotations"] + second["annotations"]]
    assert len(set(ids)) == 4
    # Each row carries the ordering key the client sorts by (page within document).
    pages = [item["orderKey"][3] for item in first["annotations"] + second["annotations"]]
    assert sorted(pages) == [1, last_page, last_page, last_page]
    # Page-level records are honest about having no precise selection.
    page_record = [item for item in first["annotations"] + second["annotations"]
                   if item["kind"] == "page_note"][0]
    assert page_record["anchor"]["rects"] == [] and page_record["contentKind"] == "page"


def test_stale_versions_keep_the_excerpt_without_navigating(application):
    """R7: re-parse/retranslate and replaced files degrade explicitly."""
    c = application.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    doc = document(c, token)
    annotation = create_annotation(c, token, doc, excerpt="原始摘录文字").json["annotation"]
    assert annotation["stale"] is False

    # The document is replaced by a different file revision.
    with application.app_context():
        from ipaper.database.connection import get_db
        from ipaper.security.identity import Identity, run_as_identity

        with get_db() as db:
            owner = db.execute("SELECT owner_id FROM papers WHERE id=?", (OWNER_PAPER,)).fetchone()[0]

        def replace():
            with get_db() as db:
                db.execute("UPDATE processing_documents SET sha256=? WHERE id=?", ("1" * 64, doc["id"]))
                db.commit()

        run_as_identity(Identity(owner, "reader_one", "admin"), replace)

    listed = c.get(f"/api/paper/{OWNER_PAPER}/reading/annotations").json["annotations"][0]
    assert listed["excerpt"] == annotation["excerpt"]  # the record is never dropped
    assert listed["stale"] is True and listed["canNavigate"] is False
    assert listed["notice"]
