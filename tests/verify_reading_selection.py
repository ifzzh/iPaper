"""One explicit selection acceptance; defaults to the loopback fake supplier.

Live mode reads a server profile through stdin only, never from env/argv/files.
A used root cannot be reused. No parsing, whole-block translation or chat occurs.
"""

import argparse
import json
import os
from pathlib import Path
import time
from pytest import MonkeyPatch
from tests.workbench_support import make_workbench_fixture
from tests.workbench_reader_support import fake_openai, install_reader_fixture
from tests.reading_sample_support import seed_reading_sample
from tests.test_workbench import login
from ipaper.database import connection
from ipaper.processing.service import ProcessingService
from ipaper.processing.routes import register_processing_routes
from ipaper.processing.common import encoded, ProcessingError
from ipaper.security.outbound import OutboundPolicy
import app as app_module


def run(root, sample, config=None):
    from ipaper.processing import selection

    live = config is not None
    root = Path(root).resolve()
    sample = Path(sample).resolve()
    if root.exists():
        raise ValueError("acceptance_root_already_used")
    root.mkdir(parents=True, mode=0o700)
    receipt = {
        "live": live,
        "mineruRequests": 0,
        "maxRequests": 1,
        "inputBudget": 2000,
        "outputBudget": 512,
        "timeoutSeconds": 120,
        "automaticRetry": False,
        "requests": 0,
    }

    def record(name, value):
        path = root / name
        path.write_text(encoded(value))
        path.chmod(0o600)

    record("receipt.json", receipt)
    connection.close_db()
    with MonkeyPatch.context() as patch, fake_openai() as origin:
        app, a, b = make_workbench_fixture(root, patch, count=1)
        install_reader_fixture(app, root, (a, b), patch, origin, register_routes=False)
        policy = app_module.OUTBOUND_POLICY
        if live:
            policy = OutboundPolicy(
                public_origins=config["publicOrigins"],
                private_origins=[],
                transfer_origins=[],
                proxy_fake_ip_networks=config.get("fakeIpRanges", []),
            )
        service = ProcessingService(
            connection.DB_PATH,
            root / "papers",
            root / "settings.key",
            app_module.AGENTIC_CREDENTIAL_STORE,
            policy,
        )
        register_processing_routes(app, service)
        seed_reading_sample(app, root, a, sample)
        bundle = json.loads((sample / "sample.json").read_text())
        pid = bundle["paper"]["id"]
        doc = bundle["documents"][0]
        if live:
            assert (
                config["paperId"] == pid
                and config["sourceHash"] == doc["sha256"]
                and config["ownerId"] == bundle["paper"]["owner_id"]
            )
        profile = (
            config["profile"]
            if live
            else {
                "model": "fixture",
                "baseUrl": origin + "/v1",
                "key": "synthetic-selection-only",
                "revision": "synthetic",
            }
        )

        class MemoryProfile:
            def get(self, *, secret=False):
                return {
                    k: v
                    for k, v in {**profile, "keyConfigured": True}.items()
                    if secret or k != "key"
                }

        original = service.pipeline

        def pipeline(owner=None):
            value = original(owner)
            value.profiles = MemoryProfile()
            return value

        service.pipeline = pipeline
        raw = selection.process_request

        def guarded(profile, messages, output_limit, *, deadline=120):
            assert receipt["requests"] == 0 and output_limit <= 512 and deadline <= 120
            assert len(messages[-1]["content"]) <= 300
            receipt["requests"] += 1
            record("receipt.json", receipt)
            response = raw(profile, messages, output_limit, deadline=deadline)
            record(
                "supplier-result.json",
                {
                    k: response[k]
                    for k in (
                        "status",
                        "error",
                        "errorKind",
                        "text",
                        "httpStatus",
                        "inputTokens",
                        "outputTokens",
                        "finishReason",
                    )
                    if k in response
                },
            )
            return response

        patch.setattr(selection, "process_request", guarded)
        service.start()
        try:
            client = app.test_client()
            token = login(client)
            headers = {"X-CSRF-Token": token}
            parsed = next(
                r
                for r in bundle["results"]
                if r["kind"] == "structure"
                and not json.loads(r["config_json"]).get("internalPart")
            )
            blocks = client.get("/api/results/" + parsed["id"] + "/blocks").json[
                "blocks"
            ]
            block = next(
                b for b in blocks if b["type"] == "text" and len(b["text"]) > 200
            )
            text = block["text"][:200]
            source = client.post(
                "/api/paper/" + pid + "/sources",
                headers=headers,
                json={
                    "resultId": parsed["id"],
                    "blockId": block["id"],
                    "start": 0,
                    "end": len(text.encode("utf-16-le")) // 2,
                },
            )
            assert source.status_code == 200
            value = {
                "text": text,
                "documentId": doc["id"],
                "resultId": parsed["id"],
                "sourceId": source.json["source"]["id"],
                "targetLanguage": "zh-CN",
                "budget": {
                    "requests": 1,
                    "inputTokens": 2000,
                    "outputTokens": 512,
                    "seconds": 120,
                },
            }
            path = "/api/paper/" + pid + "/selection-translation"
            preview = client.post(path + "/preview", headers=headers, json=value)
            assert preview.status_code == 200 and receipt["requests"] == 0
            record(
                "preflight.json",
                {
                    **preview.json,
                    "paperId": pid,
                    "blockId": block["id"],
                    "selectionText": text,
                },
            )
            response = client.post(path, headers=headers, json=value)
            assert response.status_code == 200
            job = response.json["job"]
            deadline = time.monotonic() + 130
            while time.monotonic() < deadline:
                job = client.get("/api/processing/jobs/" + job["id"]).json["job"]
                if job["status"] not in {"queued", "running", "cancelling"}:
                    break
                time.sleep(0.2)
            record("job.json", job)
            if job["status"] != "completed":
                raise ProcessingError(job.get("error") or "acceptance_failed")
            first = client.get("/api/selection-translations/" + job["resultId"])
            assert first.status_code == 200
            again = client.post(path, headers=headers, json=value)
            assert (
                again.status_code == 200
                and again.json["cached"]
                and receipt["requests"] == 1
            )
            assert (
                first.json["translation"]["translation"]
                == again.json["translation"]["translation"]
            )
            foreign = app.test_client()
            login(foreign, "reader_two")
            assert (
                foreign.get(
                    "/api/selection-translations/" + job["resultId"]
                ).status_code
                == 404
            )
            record("translation.json", first.json["translation"])
            receipt.update(
                passed=True,
                cachedReadRequests=0,
                ownerIsolation=True,
                model=profile["model"],
                paperId=pid,
                characters=len(text),
            )
            record("receipt.json", receipt)
            return receipt
        finally:
            service.shutdown()
            connection.close_db()


def main():
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(sys.stdin.buffer.read(32769)) if args.live else None
    try:
        print(encoded(run(args.root, args.sample, config)))
    except Exception as exc:
        # Closed diagnostics only: no credential, URL, request or provider body.
        print(
            encoded({"passed": False, "code": getattr(exc, "code", type(exc).__name__)})
        )
        if not args.live:
            import traceback

            traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
