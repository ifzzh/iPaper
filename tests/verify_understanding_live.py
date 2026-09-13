"""One explicit single-paper acceptance package; defaults to no paid calls.

The caller provides a prepared single-paper bundle and a fresh isolated root.
Real interpret credentials are read ONLY from stdin in --live mode, held in
memory and passed over the existing controlled model pipes. No production DB,
account row or encryption master key is copied. A receipt forbids reruns.
"""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace
import uuid

from pytest import MonkeyPatch
from ipaper.database import connection
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.database.dao.settings_dao import SettingsDAO
from ipaper.security.identity import Identity, run_as_identity
from ipaper.security.paths import paper_path, paper_asset_paths, safe_join
from ipaper.security.outbound import OutboundPolicy
from ipaper.processing.pipeline import file_digest
from ipaper.processing.service import ProcessingService
from ipaper.processing.routes import register_processing_routes
from ipaper.processing.common import encoded, ProcessingError
from tests.workbench_support import make_workbench_fixture
from tests.workbench_reader_support import install_reader_fixture, fake_openai
from tests.test_workbench import login
from scripts.promote_processing_acceptance import insert


def live_preflight():
    """Construct the actual SDK transport before spending any request budget.

    This performs no HTTP request and uses no real credential. In particular,
    an unsupported inherited SOCKS proxy must fail before any receipt/task.
    The operator can explicitly select its supported HTTP proxy; this function
    never changes proxy variables or production outbound policy.
    """
    from openai import OpenAI, DefaultHttpxClient

    with OpenAI(
        api_key="offline-preflight",
        base_url="https://example.com/v1",
        max_retries=0,
        http_client=DefaultHttpxClient(follow_redirects=False),
    ):
        pass


def record_analysis_response(response, path, *, live):
    diagnostic = {
        k: response[k]
        for k in (
            "status",
            "text",
            "error",
            "httpStatus",
            "inputTokens",
            "outputTokens",
            "finishReason",
            "errorKind",
        )
        if k in response
    }
    path.write_text(encoded(diagnostic))
    path.chmod(0o600)
    # Stop this acceptance package at the first unsuccessful response. The
    # production partial-result scheduler may continue other independent chunks;
    # a narrowly authorized acceptance must not silently spend that whole budget.
    if live and (response.get("status") != "completed" or response.get("error")):
        raise ProcessingError("acceptance_response_failed", 502)
    return response


def run_acceptance(root, sample, config, *, live=False):
    if live:
        live_preflight()
    import app as application_module
    from ipaper.processing import understanding as analysis, understanding_chat as chat

    root, sample = Path(root).resolve(), Path(sample).resolve()
    if root.exists():
        raise ValueError("acceptance_root_already_used")
    bundle = json.loads((sample / "sample.json").read_text())
    if bundle["format"] != 1 or not bundle["coverage"]["complete"]:
        raise ValueError("invalid_sample")
    for entry in bundle["files"]:
        path = safe_join(sample, entry["path"], must_exist=True, require_file=True)
        if path.stat().st_size != entry["size"] or file_digest(path) != entry["sha256"]:
            raise ValueError("sample_hash_mismatch")
    if live and (
        bundle["paper"]["id"] != config["paperId"]
        or bundle["paper"]["owner_id"] != config["ownerId"]
        or bundle["documents"][0]["sha256"] != config["sourceHash"]
    ):
        raise ValueError("approved_paper_mismatch")
    root.mkdir(mode=0o700, parents=True)
    receipt = {
        k: config.get(k)
        for k in ("paperId", "ownerId", "sourceHash", "credentialRevision")
    }
    receipt.update(
        live=live,
        createdAt=time.time(),
        requests={"overview": 6, "interpretation": 5, "local": 1, "paper": 2},
        inputBudget=345000,
        outputBudget=36864,
        timeoutSeconds=120,
        automaticRetry=False,
    )
    (root / "request-receipt.json").write_text(encoded(receipt))
    (root / "request-receipt.json").chmod(0o600)
    usage = {"requests": 0, "inputTokensUpper": 0, "outputTokensUpper": 0, "phases": {}}
    phase = None
    service = None
    limits = {
        "overview": (6, 190000, 14336),
        "interpretation": (5, 110000, 20480),
        "local": (1, 10000, 512),
        "paper": (2, 35000, 1536),
    }

    def charge(messages, output):
        if phase not in limits:
            raise ProcessingError("acceptance_scope_closed", 409)
        current = usage["phases"].setdefault(
            phase, {"requests": 0, "inputTokensUpper": 0, "outputTokensUpper": 0}
        )
        cost = len(encoded(messages).encode()) + 1024
        bound = limits[phase]
        if (
            current["requests"] + 1 > bound[0]
            or current["inputTokensUpper"] + cost > bound[1]
            or current["outputTokensUpper"] + output > bound[2]
        ):
            raise ProcessingError("acceptance_budget_exceeded", 409)
        for target in (current, usage):
            target["requests"] += 1
            target["inputTokensUpper"] += cost
            target["outputTokensUpper"] += output
        (root / "usage.json").write_text(encoded(usage))
        (root / "usage.json").chmod(0o600)

    proxy = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
    }
    connection.close_db()
    with MonkeyPatch.context() as patch:
        app, a, b = make_workbench_fixture(root, patch, count=1)
        # The generated local key encrypts only fixture credentials. Real
        # credentials below bypass storage and never enter this key file.
        install_reader_fixture(
            app, root, (a, b), patch, "http://127.0.0.1:1", register_routes=False
        )
        if live:
            for k, v in proxy.items():
                patch.setenv(k, v)
        policy = OutboundPolicy(
            public_origins=config.get("publicOrigins", []),
            private_origins=[],
            transfer_origins=[],
            proxy_fake_ip_networks=config.get("fakeIpRanges", []),
        )
        if not live:

            class OfflinePolicy:
                def validate(self, url, **_):
                    if url != config["profile"]["baseUrl"]:
                        raise AssertionError("offline_origin_only")

            policy = OfflinePolicy()

        class Credentials:
            def get(self, name):
                if name != "interpret":
                    raise AssertionError("no_parser_or_translation_credential")
                return config["profile"]["key"]

            def configured(self, name):
                return name == "interpret"

        service = ProcessingService(
            connection.DB_PATH,
            root / "papers",
            root / "settings.key",
            Credentials(),
            policy,
        )
        register_processing_routes(app, service)
        from ipaper.routes.agent_routes.agent_chat_route import (
            register_agent_chat_routes,
        )

        register_agent_chat_routes(
            app,
            get_categories=lambda: {"children": []},
            get_category_path=lambda *_: None,
            get_papers_in_category=lambda *_: [],
            agentic_settings_file="unused",
            credential_store=Credentials(),
            outbound_policy=policy,
        )
        pid = bundle["paper"]["id"]

        def seed():
            target = paper_path(
                root / "papers", "root", pid + ".pdf", create_parent=True
            )
            shutil.copyfile(sample / "input/original.pdf", target)
            translated = sample / "input/translated.pdf"
            if translated.exists():
                shutil.copyfile(
                    translated, paper_asset_paths(root / "papers", target).chinese_dual
                )
            data = {k: v for k, v in bundle["paper"].items() if k != "owner_id"}
            data.update(
                file_path=str(target),
                filename=target.name,
                has_chinese_version=translated.exists(),
            )
            PaperDAO.save_paper(data)
            from ipaper.core.base_paper import Paper

            application_module.paper_store.upsert(
                Paper.from_dict(data), category_id="root", category_path=["Root"]
            )
            settings = {
                "llmConfigs": {
                    "interpret": {
                        "llmModel": config["profile"]["model"],
                        "llmBaseUrl": config["profile"]["baseUrl"],
                    }
                }
            }
            SettingsDAO.save_setting("agentic_settings", settings)
            store = service.pipeline(a["id"]).store
            with store.connection(write=True) as db:
                db.execute(
                    "UPDATE agentic_secrets_v2 SET updated_at=? WHERE owner_id=? AND name='interpret'",
                    (config.get("credentialRevision"), a["id"]),
                )
                for original in bundle["documents"]:
                    row = {
                        **original,
                        "owner_id": a["id"],
                        "file_ref": str(target.relative_to(root / "papers")),
                    }
                    insert(db, "processing_documents", row)
                for original in bundle["results"]:
                    row = {**original, "owner_id": a["id"]}
                    insert(db, "processing_results", row)
                    directory = store.artifact_directory(row["id"], create=True)
                    for entry in json.loads(row["manifest_json"])["entries"]:
                        dest = safe_join(directory, entry["path"])
                        dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                        shutil.copyfile(
                            safe_join(
                                sample,
                                "artifacts",
                                row["id"],
                                entry["path"],
                                must_exist=True,
                                require_file=True,
                            ),
                            dest,
                        )
                        dest.chmod(0o600)
                for row in bundle["blocks"]:
                    insert(db, "processing_blocks", row)

        run_as_identity(Identity(a["id"], a["username"], a["role"]), seed)
        raw_analysis = analysis.process_request
        raw_helper = chat.process_request
        raw_stream = chat.stream_request

        def guarded_analysis(profile, messages, output):
            charge(messages, output)
            response = raw_analysis(profile, messages, output)
            path = root / f'response-{usage["requests"]}.json'
            response = record_analysis_response(response, path, live=live)
            # Acceptance never retries, including a clearly rejected 429.
            if response.get("status") == "rejected":
                response = {**response, "status": "failed"}
            return response

        def guarded_helper(profile, messages, output):
            charge(messages, output)
            return raw_helper(profile, messages, output)

        def guarded_stream(profile, messages, output):
            charge(messages, 512)
            yield from raw_stream(profile, messages, 512)

        patch.setattr(analysis, "process_request", guarded_analysis)
        patch.setattr(chat, "process_request", guarded_helper)
        patch.setattr(chat, "stream_request", guarded_stream)
        service.start()
        client = app.test_client()
        token = login(client)
        headers = {"X-CSRF-Token": token}
        jobs = []
        turns = []
        try:
            state = client.get(f"/api/paper/{pid}/content").json
            if (
                not state["coverage"]["complete"]
                or state["blockCount"] != bundle["blockCount"]
            ):
                raise ValueError("source_coverage_mismatch")

            def wait_job(job):
                deadline = time.monotonic() + 1800
                while (
                    job["status"] in ("queued", "running", "cancelling")
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.1)
                    job = client.get("/api/processing/jobs/" + job["id"]).json["job"]
                if job["status"] != "completed":
                    raise ValueError("acceptance_task_failed")
                return job

            for kind in ("overview", "interpretation"):
                phase = kind
                data = {
                    "kind": kind,
                    "contentVersion": state["version"],
                    "budget": {
                        "requests": limits[kind][0],
                        "inputTokens": limits[kind][1],
                        "outputTokens": limits[kind][2],
                    },
                }
                estimate = client.post(
                    f"/api/paper/{pid}/processing/estimate", json=data, headers=headers
                )
                (root / (kind + "-preflight.json")).write_text(encoded(estimate.json))
                if estimate.status_code != 200 or estimate.json["exceedsBudget"]:
                    raise ValueError("acceptance_preflight_budget")
                result = client.post(
                    f"/api/paper/{pid}/processing/jobs", json=data, headers=headers
                )
                if result.status_code not in (200, 202):
                    raise ValueError("acceptance_admission_failed")
                job = wait_job(result.json["job"])
                jobs.append(job)
                value = client.get(
                    f"/api/paper/{pid}/understanding/" + job["resultId"]
                ).json["result"]
                if not value["body"]["sources"]:
                    raise ValueError("acceptance_evidence_missing")
                (root / (kind + ".json")).write_text(encoded(value))
            source_id = next(iter(value["body"]["sources"].values()))["sourceId"]
            question = "论文报告了哪些实验结果和局限？请区分已提供的证据与无法确认的内容，并引用来源编号。回答控制在150字以内。"
            for mode in ("local", "paper"):
                phase = mode
                rid = str(uuid.uuid4())
                data = {
                    "paper_id": pid,
                    "messages": [{"role": "user", "content": question}],
                    "scope": mode,
                    "request_id": rid,
                    "content_version": state["version"],
                }
                if mode == "local":
                    data["source_ids"] = [source_id]
                response = client.post("/api/paper/chat", json=data, headers=headers)
                if response.status_code != 200:
                    raise ValueError("acceptance_chat_rejected")
                wire = response.text
                (root / (mode + "-stream.json")).write_text(
                    encoded(
                        {
                            "httpStatus": response.status_code,
                            "contentType": response.content_type,
                            "body": wire[:65536],
                        }
                    )
                )
                header, answer = wire.split("\n", 1)
                session = json.loads(header)["session_id"]
                history = client.get(
                    f"/api/paper/chat/session?paper_id={pid}&session_id={session}"
                ).json["session"]["messages"]
                turn = client.get("/api/paper/chat/turns/" + rid).json
                (root / (mode + "-turn.json")).write_text(encoded(turn))
                if (
                    turn["status"] != "completed"
                    or not answer.strip()
                    or history[-1]["content"] != answer
                    or not history[-1].get("sources")
                ):
                    raise ValueError("acceptance_chat_not_saved")
                evidence = {
                    s["label"]: client.get("/api/sources/" + s["sourceId"]).json[
                        "source"
                    ]
                    for s in history[-1]["sources"]
                }
                (root / (mode + "-chat.json")).write_text(
                    encoded({"history": history, "turn": turn, "evidence": evidence})
                )
                turns.append({"id": rid, "sessionId": session, "mode": mode})
            phase = None
            # Reading cached analyses and exporting must spend no requests.
            before = usage["requests"]
            for job in jobs:
                client.get(f"/api/paper/{pid}/understanding/" + job["resultId"])
                exported = client.post(
                    f"/api/paper/{pid}/processing/jobs",
                    json={
                        "kind": "analysis_export",
                        "analysisResultId": job["resultId"],
                        "format": "zip",
                    },
                    headers=headers,
                )
                export = wait_job(exported.json["job"])
                (root / (job["kind"] + ".zip")).write_bytes(
                    client.get(
                        "/api/understanding/" + export["resultId"] + "/download"
                    ).data
                )
            if usage["requests"] != before:
                raise ValueError("unexpected_cached_request")
            result = {
                "passed": True,
                "live": live,
                "ownerId": a["id"],
                "paperId": pid,
                "sourceHash": bundle["documents"][0]["sha256"],
                "parseId": state["parseId"],
                "blocks": state["blockCount"],
                "unitCount": state["unitCount"],
                "jobs": jobs,
                "turns": turns,
                "usage": usage,
                "mineruRequests": 0,
            }
            (root / "result.json").write_text(encoded(result))
            return result
        finally:
            phase = None
            service.shutdown()
            connection.close_db()
            for path in root.rglob("*"):
                if path.is_file():
                    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--sample", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    if args.live:
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise ValueError("configuration_size_limit")
        result = run_acceptance(args.root, args.sample, json.loads(raw), live=True)
    else:
        with fake_openai() as origin:
            bundle = json.loads((Path(args.sample) / "sample.json").read_text())
            config = {
                "paperId": bundle["paper"]["id"],
                "ownerId": bundle["paper"]["owner_id"],
                "sourceHash": bundle["documents"][0]["sha256"],
                "credentialRevision": "offline-fixture",
                "profile": {
                    "model": "fixture",
                    "baseUrl": origin + "/v1",
                    "key": "offline-only",
                },
            }
            result = run_acceptance(args.root, args.sample, config)
    print(
        encoded(
            {
                "passed": result["passed"],
                "live": result["live"],
                "blocks": result["blocks"],
                "usage": result["usage"],
                "mineruRequests": 0,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            encoded(
                {
                    "passed": False,
                    "errorType": type(error).__name__,
                    "automaticRetry": False,
                }
            )
        )
        raise SystemExit(1)
