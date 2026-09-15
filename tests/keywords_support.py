"""Explicit synthetic keyword supplier, installed only by browser test harness."""

import json
from flask import jsonify


def install(application):
    service = application.extensions["keywords"]
    calls = []
    # Meaningful authored inputs; the generic PDF fixture title is deliberately
    # insufficient to test extraction quality and must not produce filler tags.
    with service.store("fixture").connection() as db:
        owner = db.execute(
            "SELECT id FROM users WHERE username='reader_pdf'"
        ).fetchone()[0]
        ids = [
            r[0] for r in db.execute("SELECT id FROM papers WHERE owner_id=?", (owner,))
        ]
        others = [
            r[0] for r in db.execute("SELECT id FROM users WHERE id!=?", (owner,))
        ]
    for other in others:
        service.store(other).settings({"automatic": False})
    from ipaper.metadata.store import MetadataStore

    metadata = MetadataStore(service.db_path, owner)
    for pid in ids:
        head = metadata.get(pid)
        metadata.edit(
            pid,
            {
                "title": "图神经网络与分子性质预测",
                "abstract": "图神经网络沿分子键传递节点信息，预测分子性质。图神经网络在不同化学骨架划分的数据集上测试。对比学习通过分子图增强获得稳定表征。",
            },
            head["revision"],
        )

    def model(profile, messages, limit, deadline):
        calls.append({"outputLimit": limit, "deadline": deadline})
        text = messages[1]["content"].split(": ", 1)[-1].split("\n")[0]
        return {
            "status": "completed",
            "text": json.dumps(
                [{"name": "合成标签验收", "quote": text[:40]}], ensure_ascii=False
            ),
        }

    service.model_request = model
    # Exercise real queue and validation without credentials or external traffic.
    service.configuration = lambda owner: (
        {
            "model": "synthetic-keywords",
            "baseUrl": "https://example.invalid",
            "key": "fixture-only",
        },
        {"model": "synthetic-keywords", "revision": 1},
    )

    @application.get("/test-keyword-calls")
    def keyword_calls():
        return jsonify(calls=calls, modelRequests=len(calls), mineruRequests=0)

    service.start()
