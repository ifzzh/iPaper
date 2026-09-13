"""Authenticated UI contracts for immutable analysis and offline exports."""

import json
import re
from flask import jsonify, request, send_file
from ipaper.security.paths import safe_join
from .common import ProcessingError, encoded
from .understanding_export import analysis_content


def attach_understanding_routes(bp, service, body):
    @bp.get("/api/paper/<paper_id>/content")
    def content(paper_id):
        return jsonify(service.understanding().files.status(paper_id))

    @bp.get("/api/paper/<paper_id>/understanding")
    def understanding_results(paper_id):
        target = service.understanding()
        rows, heads = target.rows(paper_id)
        status = target.files.status(paper_id)
        return jsonify(
            results=[target.public(r, current_status=status) for r in rows], heads=heads
        )

    @bp.get("/api/paper/<paper_id>/understanding/<result_id>")
    def detail(paper_id, result_id):
        target = service.understanding()
        value, _ = analysis_content(target, paper_id, result_id)
        if result_id == "legacy":
            # Resolve owned historical analysis assets even without the PDF.
            path, text = target.files.legacy(paper_id, analysis=True)
            images, _ = target.files.legacy_images(path, text)
            from .pipeline import file_digest

            for raw, image in images.items():
                name = "images/" + file_digest(image) + image.suffix.lower()
                value["markdown"] = value["markdown"].replace(
                    "](" + name + ")",
                    f"](/api/paper/{paper_id}/understanding-assets/{file_digest(image)})",
                )
            row = {
                "id": "legacy",
                "kind": "interpretation",
                "status": "historical",
                "paperId": paper_id,
                "body": value,
                "stale": False,
            }
        else:
            row = target.public(target.files.row(result_id), include_body=True)
            for value in row["body"].get("sources", {}).values():
                evidence = target.files.resolve(value["sourceId"])
                value.update(
                    page=evidence.get("page"), snapshotId=evidence["snapshotId"]
                )
            snapshot = target.files.row(row["sourceId"])
            for entry in json.loads(snapshot["manifest_json"])["entries"]:
                if entry["path"].startswith("images/"):
                    row["body"]["markdown"] = row["body"]["markdown"].replace(
                        "](" + entry["path"] + ")",
                        f"](/api/understanding/{snapshot['id']}/assets/{entry['sha256']})",
                    )
        return jsonify(result=row)

    @bp.get("/api/paper/<paper_id>/understanding-assets/<sha256>")
    def legacy_understanding_asset(paper_id, sha256):
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ProcessingError("asset_not_found", 404)
        from .pipeline import file_digest

        target = service.understanding()
        _, images = analysis_content(target, paper_id, "legacy")
        image = next((p for p in images.values() if file_digest(p) == sha256), None)
        if not image:
            raise ProcessingError("asset_not_found", 404)
        return send_file(image, conditional=True, max_age=0)

    @bp.get("/api/understanding/<result_id>/assets/<sha256>")
    def understanding_asset(result_id, sha256):
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ProcessingError("asset_not_found", 404)
        target = service.understanding()
        row = target.files.row(result_id)
        entry = next(
            (
                e
                for e in json.loads(row["manifest_json"])["entries"]
                if e["sha256"] == sha256
                and e["path"].lower().endswith((".png", ".jpg", ".jpeg"))
            ),
            None,
        )
        if not entry:
            raise ProcessingError("asset_not_found", 404)
        path = safe_join(
            target.store.artifact_directory(result_id),
            entry["path"],
            must_exist=True,
            require_file=True,
        )
        from .pipeline import file_digest

        if path.stat().st_size != entry["size"] or file_digest(path) != entry["sha256"]:
            raise ProcessingError("content_snapshot_invalid", 409)
        return send_file(path, conditional=True, max_age=0)

    @bp.get("/api/understanding/<result_id>/download")
    def download(result_id):
        target = service.understanding()
        row = target.files.row(result_id)
        if row["kind"] != "analysis_export":
            raise ProcessingError("export_not_found", 404)
        value = target.files.body(result_id)
        return send_file(
            safe_join(
                target.store.artifact_directory(result_id),
                value["file"],
                must_exist=True,
                require_file=True,
            ),
            as_attachment=True,
            download_name=value["filename"],
            max_age=0,
        )

    @bp.route("/api/settings/paper-understanding", methods=["GET", "PUT"])
    def understanding_settings():
        target = service.understanding()
        value = (
            body({"overview", "interpretation"}) if request.method == "PUT" else None
        )
        cfg = target.pipeline.settings().get("llmConfigs", {}).get("interpret", {})
        return jsonify(
            settings=target.settings(value),
            model=cfg.get("llmModel"),
            keyConfigured=target.pipeline.credentials.configured("interpret"),
        )

    @bp.route("/api/paper/<paper_id>/understanding-position", methods=["GET", "PUT"])
    def understanding_position(paper_id):
        target = service.understanding()
        key = "understanding_position_v1:" + paper_id
        with target.store.connection(write=request.method == "PUT") as db:
            target.store.paper_exists(db, paper_id)
            if request.method == "GET":
                row = db.execute(
                    "SELECT value FROM user_settings_v2 WHERE owner_id=? AND key=?",
                    (target.store.owner, key),
                ).fetchone()
                return jsonify(json.loads(row[0]) if row else {})
            update = body({"kind", "resultId", "offset", "sessionId"})
            row = db.execute(
                "SELECT value FROM user_settings_v2 WHERE owner_id=? AND key=?",
                (target.store.owner, key),
            ).fetchone()
            value = json.loads(row[0]) if row else {}
            if "kind" in update:
                kind = update["kind"]
                if (
                    kind not in {"overview", "interpretation", "reader"}
                    or type(update.get("offset")) not in (int, float)
                    or not 0 <= update["offset"] <= 1
                ):
                    raise ProcessingError("invalid_reading_position")
                rid = update.get("resultId")
                if rid == "legacy":
                    if (
                        kind != "interpretation"
                        or not target.files.legacy(paper_id, analysis=True)[0]
                    ):
                        raise ProcessingError("understanding_not_found", 404)
                elif rid:
                    result = target.files.row(rid)
                    if result["paper_id"] != paper_id or result["kind"] != kind:
                        raise ProcessingError("understanding_not_found", 404)
                value.update(kind=kind, resultId=rid, offset=update["offset"])
                value.setdefault("positions", {})[kind] = {
                    "resultId": rid,
                    "offset": update["offset"],
                }
            elif set(update) - {"sessionId"}:
                raise ProcessingError("invalid_reading_position")
            if "sessionId" in update:
                sid = update["sessionId"]
                if sid is not None and (
                    not isinstance(sid, str)
                    or not db.execute(
                        "SELECT 1 FROM chats WHERE owner_id=? AND paper_id=? AND session_id=?",
                        (target.store.owner, paper_id, sid),
                    ).fetchone()
                ):
                    raise ProcessingError("session_not_found", 404)
                value["sessionId"] = sid
            db.execute(
                "INSERT INTO user_settings_v2 VALUES(?,?,?) ON CONFLICT(owner_id,key) DO UPDATE SET value=excluded.value",
                (target.store.owner, key, encoded(value)),
            )
        return jsonify(value)

    @bp.get("/api/paper/chat/turns/<turn_id>")
    def turn(turn_id):
        target = service.understanding()
        with target.store.connection() as db:
            row = db.execute(
                "SELECT * FROM understanding_chat_turns WHERE owner_id=? AND id=?",
                (target.store.owner, turn_id),
            ).fetchone()
            if not row:
                raise ProcessingError("chat_turn_not_found", 404)
            target.store.paper_exists(db, row["paper_id"])
        return jsonify(
            status=row["status"],
            scope=json.loads(row["context_json"]),
            errorCode=row["error"],
            sessionId=row["session_id"],
        )
