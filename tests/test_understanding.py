"""Single-paper acceptance uses synthetic text and a strictly local supplier."""

import json
import time
import zipfile
import io
from pathlib import Path

import pytest
from tests.test_processing_api import application, generate
from tests.test_workbench import login
from ipaper.processing.common import ProcessingError


@pytest.fixture
def understanding_app(application, monkeypatch):
    calls = []

    def model(profile, messages, output):
        units = json.loads(messages[-1]["content"])
        # Both direct generation and reduction must return literal evidence.
        if units and "label" in units[0]:
            unit = next(
                (u for u in units if "controlled experiment" in u["text"]), units[0]
            )
            evidence = [{"label": unit["label"], "quote": unit["text"][:50]}]
            markdown = (
                "## 核心方法\n\n论文报告受控实验 ["
                + unit["label"]
                + "].\n\n$$E=mc^2$$\n\n| 方法 | 得分 |\n|---|---|\n| Ours | 95.2 |\n\n虚构引用 [S99999]。"
            )
        else:
            evidence = []
            markdown = "## 背景与问题\n\n合成概览。"
        calls.append({"messages": messages, "output": output})
        return {
            "status": "completed",
            "text": json.dumps({"markdown": markdown, "evidence": evidence}),
        }

    monkeypatch.setattr("ipaper.processing.understanding.process_request", model)
    application.understanding_calls = calls
    return application


def wait(client, job):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get("/api/processing/jobs/" + job["id"]).json["job"]
        if job["status"] not in ("queued", "running", "cancelling"):
            return job
        time.sleep(0.03)
    raise AssertionError(job)


def start(client, token, kind="overview", **extra):
    state = client.get("/api/paper/a-4/content")
    assert state.status_code == 200, state.json
    response = client.post(
        "/api/paper/a-4/processing/jobs",
        headers={"X-CSRF-Token": token},
        json={"kind": kind, "contentVersion": state.json["version"], **extra},
    )
    assert response.status_code in (200, 202), response.json
    return wait(client, response.json["job"])


def test_complete_parse_is_reused_for_independent_analysis_and_export(
    understanding_app, monkeypatch
):
    c = understanding_app.test_client()
    token = login(c)
    _, parsed = generate(c, token, kind="parse")
    state = c.get("/api/paper/a-4/content")
    assert state.json["coverage"]["complete"], state.json
    assert state.json["blockCount"] == 8

    # Existing source needs neither a new parser task nor translation results.
    def forbid(*a, **k):
        raise AssertionError("must not create another MinerU parse")

    monkeypatch.setattr("tests.dual_translation_support.SyntheticCloud.submit", forbid)
    overview = start(c, token)
    assert overview["status"] == "completed", overview
    deep = start(c, token, "interpretation")
    assert deep["status"] == "completed", deep
    results = c.get("/api/paper/a-4/understanding").json
    assert set(results["heads"]) == {"overview", "interpretation"}
    value = c.get("/api/paper/a-4/understanding/" + deep["resultId"]).json["result"]
    assert value["body"]["sources"]
    assert "[S99999]" not in value["body"]["markdown"]
    source = next(iter(value["body"]["sources"].values()))["sourceId"]
    assert c.get("/api/sources/" + source).json["source"]["canNavigate"]
    n = len(understanding_app.understanding_calls)
    same = start(c, token, "interpretation")
    assert same["id"] == deep["id"]
    assert len(understanding_app.understanding_calls) == n
    other = understanding_app.test_client()
    login(other, "reader_two")
    for path in [
        "/api/paper/a-4/content",
        "/api/paper/a-4/understanding/" + deep["resultId"],
        "/api/sources/" + source,
    ]:
        assert other.get(path).status_code == 404
    exp = c.post(
        "/api/paper/a-4/processing/jobs",
        headers={"X-CSRF-Token": token},
        json={
            "kind": "analysis_export",
            "analysisResultId": deep["resultId"],
            "format": "zip",
        },
    )
    assert exp.status_code == 202, exp.json
    export = wait(c, exp.json["job"])
    assert export["status"] == "completed", export
    download = c.get("/api/understanding/" + export["resultId"] + "/download")
    assert download.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(download.data))
    md = next(n for n in archive.namelist() if n.endswith(".md"))
    content = archive.read(md).decode()
    assert "来源摘录" in content and "E=mc^2" in content and "| Ours |" in content
    assert "/data/" not in content and "apiKey" not in content
    assert len(understanding_app.understanding_calls) == n


def test_failed_update_preserves_previous_analysis(understanding_app, monkeypatch):
    c = understanding_app.test_client()
    token = login(c)
    generate(c, token, kind="parse")
    good = start(c, token)
    monkeypatch.setattr(
        "ipaper.processing.understanding.process_request",
        lambda *a, **k: {"status": "unknown"},
    )
    failed = start(c, token, previousResultId=good["resultId"])
    assert failed["status"] == "interrupted", failed
    assert (
        c.get("/api/paper/a-4/understanding").json["heads"]["overview"]
        == good["resultId"]
    )
    assert c.get("/api/paper/a-4/understanding/" + good["resultId"]).status_code == 200


def test_legacy_and_missing_content_are_honest_and_csrf_protected(understanding_app):
    c = understanding_app.test_client()
    token = login(c)
    state = c.get("/api/paper/a-0/content")
    assert state.status_code == 200
    if state.json["available"]:
        assert not state.json["coverage"]["complete"]
    request = {"kind": "overview", "contentVersion": state.json["version"]}
    assert c.post("/api/paper/a-0/processing/jobs", json=request).status_code == 403
    denied = c.post(
        "/api/paper/a-0/processing/jobs", headers={"X-CSRF-Token": token}, json=request
    )
    assert denied.status_code == 409
    assert not understanding_app.understanding_calls
    cfg = c.get("/api/settings/paper-understanding")
    assert cfg.status_code == 200 and set(cfg.json["settings"]) == {
        "overview",
        "interpretation",
    }
    assert "key" not in cfg.json and "baseUrl" not in cfg.json


def legacy_files(application, paper="a-4"):
    service = application.extensions["processing"]
    import sqlite3

    with sqlite3.connect(service.db_path) as db:
        path, owner = db.execute(
            "SELECT file_path,owner_id FROM papers WHERE id=?", (paper,)
        ).fetchone()
    source = Path(path)
    directory = source.parent / "outputs" / source.stem / "vlm"
    directory.mkdir(parents=True, exist_ok=True)
    return source, directory, owner


def test_long_whole_paper_qa_finds_tail_and_deduplicates(application, monkeypatch):
    source, directory, owner = legacy_files(application)
    text = "\n\n".join(
        f"Background paragraph {i}. " + ("Earlier general background. " * 50)
        for i in range(45)
    )
    tail = "Appendix limitations: experiments excluded the ZEPHYR benchmark and trials above 1000 frames."
    (directory / (source.stem + ".md")).write_text(text + "\n\n" + tail)
    calls = []

    def select(profile, messages, output):
        calls.append(("select", messages))
        assert "ZEPHYR" in messages[-1]["content"]
        return {
            "status": "completed",
            "text": json.dumps(
                {"terms": ["ZEPHYR", "excluded", "limitations"], "unitIds": []}
            ),
        }

    def answer(profile, messages, output):
        calls.append(("answer", messages))
        assert tail in messages[0]["content"]
        import re

        entries = re.findall(
            r"\[(S\d+)\] 第 [^\n]+\n(.*?)(?=\n\n\[S\d+\]|\n</VERIFIED_SOURCE_EXCERPTS>)",
            messages[0]["content"],
            re.S,
        )
        label = next(label for label, text in entries if tail in text)
        yield f"附录说明实验排除了 ZEPHYR 基准 [{label}]。"

    monkeypatch.setattr("ipaper.processing.understanding_chat.process_request", select)
    monkeypatch.setattr("ipaper.processing.understanding_chat.stream_request", answer)
    c = application.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    state = c.get("/api/paper/a-4/content").json
    import uuid

    payload = {
        "paper_id": "a-4",
        "messages": [{"role": "user", "content": "附录中实验排除了什么？"}],
        "scope": "paper",
        "allow_partial": True,
        "content_version": state["version"],
        "request_id": str(uuid.uuid4()),
    }
    response = c.post("/api/paper/chat", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    header, answer_text = response.text.split("\n", 1)
    assert "ZEPHYR" in answer_text
    duplicate = c.post("/api/paper/chat", json=payload, headers=headers)
    assert duplicate.text == response.text
    assert [kind for kind, _ in calls] == ["select", "answer"]
    changed = c.post(
        "/api/paper/chat",
        json={**payload, "messages": [{"role": "user", "content": "different"}]},
        headers=headers,
    )
    assert changed.status_code == 409
    sid = json.loads(header)["session_id"]
    history = c.get(f"/api/paper/chat/session?paper_id=a-4&session_id={sid}").json[
        "session"
    ]["messages"]
    assert len(history) == 2 and history[-1]["scope"]["selectionLimited"]
    assert not history[-1]["scope"]["searchCoverage"]["complete"]
    assert history[-1]["sources"]
    evidence = c.get("/api/sources/" + history[-1]["sources"][0]["sourceId"]).json[
        "source"
    ]
    assert (
        tail in evidence["text"]
        and not evidence["canNavigate"]
        and evidence["page"] is None
    )
    other = application.test_client()
    login(other, "reader_two")
    assert (
        other.get("/api/paper/chat/turns/" + payload["request_id"]).status_code == 404
    )
    turn = c.get("/api/paper/chat/turns/" + payload["request_id"]).json
    assert turn["status"] == "completed"


def test_legacy_image_export_and_analysis_survive_missing_original(understanding_app):
    source, directory, owner = legacy_files(understanding_app)
    import base64

    image = directory / "figure.png"
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
    )
    (directory / "result.md").write_text(
        "# Historical analysis\n\n![figure](figure.png)\n\n$$x^2$$\n\n![bad](https://unapproved.test/image.png)"
    )
    (directory / (source.stem + ".md")).write_text(
        "Legacy source paragraph.\n\n![figure](figure.png)"
    )
    source.unlink()  # isolated synthetic file only
    c = understanding_app.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    state = c.get("/api/paper/a-4/content")
    assert state.status_code == 200 and state.json["kind"] == "legacy_markdown"
    detail = c.get("/api/paper/a-4/understanding/legacy")
    assert (
        detail.status_code == 200
        and "Historical analysis" in detail.json["result"]["body"]["markdown"]
    )
    import re

    image_url = re.search(
        r"\]\((/api/paper/[^)]+)\)", detail.json["result"]["body"]["markdown"]
    )[1]
    assert c.get(image_url).data == image.read_bytes()
    created = c.post(
        "/api/paper/a-4/processing/jobs",
        headers=headers,
        json={"kind": "analysis_export", "analysisResultId": "legacy", "format": "zip"},
    )
    assert created.status_code == 202, created.json
    done = wait(c, created.json["job"])
    assert done["status"] == "completed", done
    zipped = c.get("/api/understanding/" + done["resultId"] + "/download")
    archive = zipfile.ZipFile(io.BytesIO(zipped.data))
    assert len([n for n in archive.namelist() if n.startswith("images/")]) == 1
    md = archive.read(next(n for n in archive.namelist() if n.endswith(".md"))).decode()
    assert "图片缺失说明" in md and "/api/" not in md
    assert r"\![bad]" in md
    assert not understanding_app.understanding_calls


def test_backup_contains_new_content_and_results(understanding_app, tmp_path):
    c = understanding_app.test_client()
    token = login(c)
    generate(c, token, kind="parse")
    job = start(c, token)
    from ipaper.processing.maintenance import backup

    service = understanding_app.extensions["processing"]
    result = backup(
        service.db_path, service.papers_root, tmp_path / "understanding-backup"
    )
    assert result["immutableFiles"] >= 6
    import sqlite3

    with sqlite3.connect(tmp_path / "understanding-backup" / "ipaper.db") as db:
        row = db.execute(
            "SELECT manifest_json,owner_id FROM understanding_artifacts WHERE id=?",
            (job["resultId"],),
        ).fetchone()
    for entry in json.loads(row[0])["entries"]:
        assert (
            tmp_path
            / "understanding-backup/artifacts/.users"
            / row[1]
            / ".artifacts"
            / job["resultId"]
            / entry["path"]
        ).is_file()


def test_cancel_resume_keeps_successful_chunks_and_never_publishes_late_result(
    application, monkeypatch
):
    source, directory, owner = legacy_files(application)
    (directory / (source.stem + ".md")).write_text(
        "\n\n".join(
            f"Section {i}: " + ("Original experiment evidence. " * 45)
            for i in range(35)
        )
    )
    service = application.extensions["processing"]
    seen = []
    cancelled = False

    def model(profile, messages, output):
        nonlocal cancelled
        units = json.loads(messages[-1]["content"])
        seen.append(units[0]["id"])
        if len(seen) == 2 and not cancelled:
            cancelled = True
            import sqlite3

            with sqlite3.connect(service.db_path) as db:
                db.execute(
                    "UPDATE processing_jobs SET cancel_requested=1,status='cancelling' WHERE owner_id=? AND kind='interpretation' AND status='running'",
                    (owner,),
                )
        return {
            "status": "completed",
            "text": json.dumps(
                {
                    "markdown": "## Experiments\n\nOriginal evidence ["
                    + units[0]["label"]
                    + "].",
                    "evidence": [
                        {"label": units[0]["label"], "quote": units[0]["text"][:60]}
                    ],
                }
            ),
        }

    monkeypatch.setattr("ipaper.processing.understanding.process_request", model)
    c = application.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    first = start(c, token, "interpretation", allowPartial=True)
    assert first["status"] == "cancelled", first
    assert not c.get("/api/paper/a-4/understanding").json["heads"]
    assert (
        c.post(
            "/api/processing/jobs/" + first["id"] + "/resume", headers=headers, json={}
        ).status_code
        == 200
    )
    done = wait(c, first)
    assert done["status"] == "completed", done
    assert len(set(seen)) == len(
        seen
    )  # Successful in-flight chunk is saved, then cancellation is observed.
    body = c.get("/api/paper/a-4/understanding/" + done["resultId"]).json["result"][
        "body"
    ]
    assert body["coveredChunks"] == body["totalChunks"] > 2
    assert (
        c.get("/api/paper/a-4/understanding/" + done["resultId"]).json["result"][
            "status"
        ]
        == "partial"
    )  # Legacy completeness stays unknown.
    (directory / (source.stem + ".md")).write_text("Changed source text.")
    changed = c.get("/api/paper/a-4/understanding/" + done["resultId"]).json["result"]
    assert changed["contentChanged"] and changed["body"]["markdown"]


def test_analysis_positions_and_chat_choices_are_owned_and_independent(
    understanding_app,
):
    c = understanding_app.test_client()
    token = login(c)
    headers = {"X-CSRF-Token": token}
    generate(c, token, kind="parse")
    overview = start(c, token)
    deep = start(c, token, "interpretation")
    path = "/api/paper/a-4/understanding-position"
    assert (
        c.put(
            path,
            json={"kind": "overview", "resultId": overview["resultId"], "offset": 0.4},
        ).status_code
        == 403
    )
    for kind, job, offset in [
        ("overview", overview, 0.4),
        ("interpretation", deep, 0.8),
    ]:
        assert (
            c.put(
                path,
                headers=headers,
                json={"kind": kind, "resultId": job["resultId"], "offset": offset},
            ).status_code
            == 200
        )
    saved = c.get(path).json
    assert (
        saved["positions"]["overview"]["offset"] == 0.4
        and saved["positions"]["interpretation"]["offset"] == 0.8
    )
    assert (
        c.put(
            path,
            headers=headers,
            json={"kind": "overview", "resultId": deep["resultId"], "offset": 0.4},
        ).status_code
        == 404
    )
    assert (
        c.put(path, headers=headers, json={"sessionId": "not-owned"}).status_code == 404
    )
    other = understanding_app.test_client()
    other_token = login(other, "reader_two")
    assert other.get(path).status_code == 404
    assert (
        other.put(
            path, headers={"X-CSRF-Token": other_token}, json={"sessionId": None}
        ).status_code
        == 404
    )


def test_one_paper_bundle_has_no_accounts_or_credentials(understanding_app, tmp_path):
    from scripts.prepare_understanding_sample import prepare

    c = understanding_app.test_client()
    token = login(c)
    generate(c, token, kind="parse")
    service = understanding_app.extensions["processing"]
    report = prepare(service.db_path, service.papers_root, "a-4", tmp_path / "sample")
    assert report["blocks"] == 8 and report["paidRequests"] == 0
    bundle = json.loads((tmp_path / "sample/sample.json").read_text())
    assert set(bundle) == {
        "format",
        "paper",
        "documents",
        "results",
        "blocks",
        "coverage",
        "blockCount",
        "files",
    }
    assert not list((tmp_path / "sample").rglob("*.db")) and not list(
        (tmp_path / "sample").rglob("*.key")
    )
    for entry in bundle["files"]:
        from ipaper.processing.pipeline import file_digest

        assert file_digest(tmp_path / "sample" / entry["path"]) == entry["sha256"]


def test_whole_acceptance_harness_uses_only_existing_parse_and_fake_model(
    application, tmp_path
):
    from scripts.prepare_understanding_sample import prepare
    from tests.verify_understanding_live import run_acceptance
    from tests.workbench_reader_support import fake_openai

    c = application.test_client()
    token = login(c)
    generate(c, token, kind="parse")
    service = application.extensions["processing"]
    sample = tmp_path / "sample"
    prepare(service.db_path, service.papers_root, "a-4", sample)
    service.shutdown()
    with fake_openai() as origin:
        result = run_acceptance(
            tmp_path / "isolated-understanding",
            sample,
            {
                "profile": {
                    "model": "fixture",
                    "baseUrl": origin + "/v1",
                    "key": "offline-only",
                },
                "credentialRevision": "synthetic",
                "ownerId": json.loads((sample / "sample.json").read_text())["paper"][
                    "owner_id"
                ],
                "paperId": "a-4",
                "sourceHash": json.loads((sample / "sample.json").read_text())[
                    "documents"
                ][0]["sha256"],
            },
        )
    assert result["passed"] and not result["live"] and result["mineruRequests"] == 0
    assert result["blocks"] == 8 and set(result["usage"]["phases"]) == {
        "overview",
        "interpretation",
        "local",
        "paper",
    }
    assert result["usage"]["phases"]["local"]["requests"] == 1
    assert (tmp_path / "isolated-understanding/overview.zip").is_file()
    from scripts.promote_understanding_acceptance import promote
    from ipaper.processing.store import ProcessingStore
    import sqlite3

    owner = json.loads((sample / "sample.json").read_text())["paper"]["owner_id"]
    target = ProcessingStore(service.db_path, service.papers_root, owner)
    with target.connection(write=True) as db:
        cfg = json.loads(
            db.execute(
                "SELECT value FROM user_settings_v2 WHERE owner_id=? AND key='agentic_settings'",
                (owner,),
            ).fetchone()[0]
        )
        cfg["llmConfigs"]["interpret"] = {
            "llmModel": "fixture",
            "llmBaseUrl": origin + "/v1",
        }
        db.execute(
            "UPDATE user_settings_v2 SET value=? WHERE owner_id=? AND key='agentic_settings'",
            (json.dumps(cfg), owner),
        )
        db.execute(
            "UPDATE agentic_secrets_v2 SET updated_at='synthetic' WHERE owner_id=? AND name='interpret'",
            (owner,),
        )
        original_chats = db.execute("SELECT count(*) FROM chats").fetchone()[0]
    with pytest.raises(ValueError, match="verified_real_acceptance_required"):
        promote(tmp_path / "isolated-understanding", target, "a-4")
    checked = promote(
        tmp_path / "isolated-understanding", target, "a-4", require_live=False
    )
    assert not checked["apply"]
    done = promote(
        tmp_path / "isolated-understanding",
        target,
        "a-4",
        apply=True,
        require_live=False,
    )
    assert done["chats"] == 2 and not done["credentialsCopied"]
    with target.connection() as db:
        assert (
            db.execute("SELECT count(*) FROM chats").fetchone()[0] == original_chats + 2
        )
        assert (
            db.execute(
                "SELECT count(*) FROM understanding_heads WHERE owner_id=?", (owner,)
            ).fetchone()[0]
            == 2
        )
    with pytest.raises(ValueError, match="target_analysis_already_exists"):
        promote(
            tmp_path / "isolated-understanding",
            target,
            "a-4",
            apply=True,
            require_live=False,
        )


def test_reading_positions_do_not_exhaust_generation_limit(application):
    import app as module

    module._rate_limiter.clear()
    client = application.test_client()
    token = login(client)
    headers = {"X-CSRF-Token": token}
    for _ in range(65):
        response = client.put(
            "/api/paper/a-4/understanding-position",
            json={"kind": "overview", "resultId": None, "offset": 0.25},
            headers=headers,
        )
        assert response.status_code == 200, response.json
    # Budget preflight remains usable after ordinary reading activity.
    assert (
        client.post(
            "/api/paper/a-4/processing/estimate",
            json={"kind": "overview"},
            headers=headers,
        ).status_code
        != 429
    )
    module._rate_limiter.clear()
