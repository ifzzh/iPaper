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

    # The draft can be kept explicitly.
    resolved = a.post(f"/api/paper/{OWNER_PAPER}/reading/note/conflicts/{conflict_id}",
                      json={"choice": "draft"}, headers=headers).json["note"]
    assert resolved["markdown"] == "并发写入" and resolved["conflicts"] == []

    # Inserting an excerpt appends a sourced quote without losing the body.
    annotation = create_annotation(a, token, doc).json["annotation"]
    with_excerpt = a.post(f"/api/paper/{OWNER_PAPER}/reading/note/excerpts",
                          json={"annotationId": annotation["id"], "revision": resolved["revision"]},
                          headers=headers).json["note"]
    assert "> 多行摘录的第二行" in with_excerpt["markdown"]
    assert "来源：第 1 页" in with_excerpt["markdown"]
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
    assert "S1（artifact" in note["markdown"]
    assert note["entries"][0]["kind"] == "answer"

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