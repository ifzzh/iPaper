"""Structured translation budget: larger defaults, deploy ceiling, top-up resume.

All model/cloud work is a local fake; no supplier call is made.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import uuid
from pathlib import Path

import pytest

from ipaper.processing.common import ProcessingError
from ipaper.processing.jobs import (
    DEFAULT_BUDGET,
    DEFAULT_TRANSLATION_BUDGET,
    MAX_TRANSLATION_BUDGET,
    TRANSLATION_KINDS,
    ProcessingJobs,
    budget_ceiling,
    default_budget,
)
from ipaper.processing.translation import request_payload
from tests.test_processing_pipeline import FakeCloud, FakeModel, pipeline  # noqa: F401 (fixture)
from tests.test_processing_store import OTHER, OWNER, store  # noqa: F401 (fixture)


class ReservingModel(FakeModel):
    """A fake supplier that really reserves, like the production path does."""

    calls = 0

    def __init__(self, *args):
        super().__init__(*args)

    def translate(self, units, language, jobs, job_id):
        from ipaper.processing.translation import request_payload as payload

        _, inputs, outputs = payload(units, language)
        attempt = jobs.reserve_attempt(
            job_id, "model", units[0]["id"], input_tokens=inputs, output_tokens=outputs
        )
        answer = super().translate(units, language, jobs, job_id)
        jobs.finish_attempt(job_id, attempt, "completed", {"inputTokens": 11, "outputTokens": 22})
        ReservingModel.calls += 1
        return answer


def publish_scale_structure(store, pipeline, tmp_path, *, translatable=245, empty=26, pages=15):
    """Publish a synthetic parse of the screenshot's magnitude through the real store.

    The original截图 does not include a full structure, so this is explicitly a
    synthetic stand-in: `translatable` blocks with text (one model batch each)
    and `empty` blocks without text, giving 271 blocks / 245 base requests.
    """
    preview = pipeline.preflight("paper")
    parsed = store.new_result(preview["documentId"], "structure", {"normalizer": "synthetic-scale"})
    output = tmp_path / uuid.uuid4().hex
    output.mkdir()
    lines = []
    for index in range(translatable + empty):
        block = {
            "id": f"s{index}",
            "order": index,
            "type": "text",
            "text": "Synthetic block text" if index < translatable else "",
            "caption": "",
            "textHash": hashlib.sha256(f"s{index}".encode()).hexdigest(),
            "source": {"page": 1 + index % pages, "precision": "page"},
        }
        lines.append(json.dumps(block))
    data = ("\n".join(lines) + "\n").encode()
    (output / "blocks.jsonl").write_bytes(data)
    store.publish_structure(
        parsed,
        output,
        {"entries": [{"path": "blocks.jsonl", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]},
    )
    return preview, parsed


# --- A. defaults, kinds and validation -------------------------------------


def test_translation_defaults_are_larger_and_other_kinds_are_unchanged():
    assert default_budget("parse_translate") == {
        "requests": 500,
        "inputTokens": 1_000_000,
        "outputTokens": 500_000,
        "seconds": 14_400,
    }
    assert default_budget("translate") == DEFAULT_TRANSLATION_BUDGET
    assert default_budget("retranslate") == DEFAULT_TRANSLATION_BUDGET
    # BabelDOC, selection, overview, interpretation and the plain parse keep the
    # previous envelope; only the three whole-paper translation kinds moved.
    for kind in ("parse", "overview", "interpretation", "analysis_export", "selection_translate"):
        assert default_budget(kind) == DEFAULT_BUDGET, kind
        assert budget_ceiling(kind) == DEFAULT_BUDGET, kind
    assert "babeldoc_dual" not in TRANSLATION_KINDS


def test_deploy_ceiling_applies_only_to_translation_kinds():
    ceiling = budget_ceiling("parse_translate")
    assert ceiling == MAX_TRANSLATION_BUDGET
    assert ceiling["requests"] == 2_000 and ceiling["seconds"] == 43_200
    assert ProcessingJobs.budget({"requests": 2_000}, kind="translate")["requests"] == 2_000
    with pytest.raises(ProcessingError) as raised:
        ProcessingJobs.budget({"requests": 2_001}, kind="translate")
    assert raised.value.code == "invalid_budget"
    assert raised.value.details["dimension"] == "requests"
    assert raised.value.details["limit"] == 2_000
    # Other kinds cannot be widened through the API at all.
    with pytest.raises(ProcessingError):
        ProcessingJobs.budget({"requests": 500}, kind="overview")


@pytest.mark.parametrize(
    "value",
    [
        {"requests": 0},
        {"requests": -5},
        {"requests": 10.5},
        {"requests": True},
        {"requests": "500"},
        {"unknown": 1},
        {"requests": 500, "extra": 1},
    ],
)
def test_invalid_budget_values_are_rejected(value):
    with pytest.raises(ProcessingError) as raised:
        ProcessingJobs.budget(value, kind="parse_translate")
    assert raised.value.code == "invalid_budget"


def test_deploy_ceiling_is_configurable_and_a_bad_override_fails_loudly(monkeypatch):
    import ipaper.processing.jobs as jobs_module

    monkeypatch.setenv("IPAPER_TRANSLATION_MAX_REQUESTS", "3000")
    reloaded = importlib.reload(jobs_module)
    try:
        assert reloaded.budget_ceiling("translate")["requests"] == 3_000
    finally:
        monkeypatch.delenv("IPAPER_TRANSLATION_MAX_REQUESTS", raising=False)
        importlib.reload(jobs_module)

    monkeypatch.setenv("IPAPER_TRANSLATION_MAX_SECONDS", "two hours")
    with pytest.raises(RuntimeError) as raised:
        importlib.reload(jobs_module)
    assert "IPAPER_TRANSLATION_MAX_SECONDS" in str(raised.value)
    monkeypatch.delenv("IPAPER_TRANSLATION_MAX_SECONDS", raising=False)
    importlib.reload(jobs_module)


# --- A. the screenshot's magnitude -----------------------------------------


def test_screenshot_magnitude_passes_the_new_default_and_was_blocked_at_200(store, pipeline, tmp_path):  # noqa: F811
    preview, parsed = publish_scale_structure(store, pipeline, tmp_path)
    request = {
        "preflightId": preview["preflightId"],
        "kind": "parse_translate",
        "parseResultId": parsed,
    }
    # The old 200-request envelope cannot admit this scope.
    with pytest.raises(ProcessingError) as raised:
        pipeline.create("paper", {**request, "budget": {"requests": 200}})
    assert raised.value.code == "processing_scope_exceeds_budget"
    assert raised.value.details["overage"]["requests"]["required"] > 200
    assert raised.value.details["overage"]["requests"]["limit"] == 200

    # The new default does, and the preview says so without creating a job.
    preview_response = pipeline.create("paper", request, preview=True)
    assert preview_response["exceedsBudget"] is False
    estimate = preview_response["estimate"]
    assert estimate["selectedBlocks"] == 271
    assert estimate["requests"] == 245
    assert estimate["retryAllowance"] == 245
    assert estimate["requestsWithRetryAllowance"] == 490
    # Every base request using its one allowed retry still fits the new default.
    assert estimate["requestsWithRetryAllowance"] <= preview_response["budget"]["requests"]
    assert preview_response["budget"] == DEFAULT_TRANSLATION_BUDGET
    assert preview_response["limits"] == MAX_TRANSLATION_BUDGET
    assert pipeline.jobs.list() == []

    # Submitting under the same envelope is admitted and runs a bounded fake
    # that reserves like production, so a zero-usage run cannot pass this.
    ReservingModel.calls = 0
    pipeline.model_factory = ReservingModel
    job, created = pipeline.create("paper", {**request, "budget": {"requests": 260}})
    assert created
    assert json.loads(job["budget_json"])["requests"] == 260
    pipeline.run(job["id"])
    outcome = pipeline.jobs.get(job["id"])
    assert outcome["status"] == "completed", outcome["error"]
    reserved = json.loads(outcome["usage_json"])["requests"]
    assert reserved == ReservingModel.calls
    assert 200 <= reserved <= 245, reserved
    assert json.loads(outcome["usage_json"])["inputTokens"] > 0


def test_overage_details_name_the_exact_dimension(store, pipeline, tmp_path):  # noqa: F811
    preview, parsed = publish_scale_structure(store, pipeline, tmp_path, translatable=8, empty=0)
    response = pipeline.create(
        "paper",
        {"preflightId": preview["preflightId"], "kind": "parse_translate", "parseResultId": parsed,
         "budget": {"requests": 4, "inputTokens": 10, "outputTokens": 10_000}},
        preview=True,
    )
    assert response["exceedsBudget"] is True
    assert set(response["overage"]) == {"requests", "inputTokens"}
    requests = response["overage"]["requests"]
    assert requests["dimension"] == "requests" and requests["dimensionLabel"] == "模型请求"
    assert requests["required"] == 8 and requests["limit"] == 4
    assert "outputTokens" not in response["overage"]


# --- B. top-up continuation -------------------------------------------------


def stopped_job_with_usage(pipeline, store, monkeypatch, *, requests_used=1):
    """Run a tiny job, stop it mid-way and leave one published block behind."""
    preview = pipeline.preflight("paper")
    job, _ = pipeline.create(
        "paper",
        {"preflightId": preview["preflightId"], "kind": "parse_translate", "budget": {"requests": 2}},
    )

    class CountedModel(FakeModel):
        def translate(self, units, language, jobs, job_id):
            _, inputs, outputs = request_payload(units, language)
            attempt = jobs.reserve_attempt(job_id, "model", units[0]["id"], input_tokens=inputs, output_tokens=outputs)
            answer = super().translate(units, language, jobs, job_id)
            jobs.finish_attempt(job_id, attempt, "completed", {"inputTokens": 11, "outputTokens": 22})
            return answer

    pipeline.model_factory = CountedModel
    original_finish = store.finish_translation
    state = {"stopped": False}

    def stop_once(*args, **kwargs):
        if not kwargs.get("error") and not state["stopped"]:
            state["stopped"] = True
            raise ProcessingError("processing_budget_exceeded")
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(store, "finish_translation", stop_once)
    pipeline.run(job["id"])
    outcome = pipeline.jobs.get(job["id"])
    assert outcome["status"] == "failed" and outcome["error"] == "processing_budget_exceeded"
    return job, outcome


def test_resume_plan_reports_remaining_need_from_checkpoints(store, pipeline, monkeypatch):  # noqa: F811
    job, outcome = stopped_job_with_usage(pipeline, store, monkeypatch)
    usage = json.loads(outcome["usage_json"])
    full = pipeline.estimate(json.loads(outcome["checkpoint_json"])["parseId"])
    plan = pipeline.resume_plan(job["id"])
    assert plan["resumable"] is True
    assert plan["usage"]["requests"] == usage["requests"]
    # The stopped block's units are checkpointed, so the remaining estimate is
    # smaller than the untouched scope and nothing already done is re-charged.
    assert plan["estimate"]["requests"] < full["requests"]
    assert plan["required"]["requests"] == usage["requests"] + plan["estimate"]["requests"]
    assert plan["secondsBasis"]
    assert plan["limits"] == MAX_TRANSLATION_BUDGET


def test_resume_with_a_larger_budget_keeps_usage_and_is_idempotent(store, pipeline, monkeypatch):  # noqa: F811
    job, outcome = stopped_job_with_usage(pipeline, store, monkeypatch)
    usage_before = json.loads(outcome["usage_json"])
    budget_before = json.loads(outcome["budget_json"])

    pipeline.resume(job["id"], {"requests": 500, "inputTokens": 1_000_000, "outputTokens": 500_000, "seconds": 14_400})
    resumed = pipeline.jobs.get(job["id"])
    assert resumed["status"] == "queued"
    assert resumed["error"] is None
    budget_after = json.loads(resumed["budget_json"])
    usage_after = json.loads(resumed["usage_json"])
    assert budget_after["requests"] == 500 and budget_before["requests"] == 2
    # The adjusted budget is the whole-task total: used amounts are preserved.
    assert usage_after == usage_before
    events = [json.loads(row["data_json"]) for row in pipeline.jobs.events(job["id"], 0)]
    kinds = [row["kind"] for row in pipeline.jobs.events(job["id"], 0)]
    assert "budget_adjusted" in kinds
    adjusted = [event for event in events if event.get("after")][-1]
    assert adjusted["before"]["requests"] == 2 and adjusted["after"]["requests"] == 500

    # A duplicate click while the job is queued must not queue it twice.
    with pytest.raises(ProcessingError) as raised:
        pipeline.resume(job["id"], {"requests": 600})
    assert raised.value.code == "job_not_resumable"

    pipeline.run(job["id"])
    final = pipeline.jobs.get(job["id"])
    assert final["status"] == "completed", final["error"]


def test_resume_rejects_below_usage_and_above_the_ceiling(store, pipeline, tmp_path, monkeypatch):  # noqa: F811
    # Two translatable blocks so the job really consumes two requests: the
    # below-usage branch is exercised instead of being skipped.
    preview, _parsed = publish_scale_structure(store, pipeline, tmp_path, translatable=2, empty=0, pages=1)
    job, _ = pipeline.create(
        "paper",
        {"preflightId": preview["preflightId"], "kind": "parse_translate", "budget": {"requests": 2}},
    )
    ReservingModel.calls = 0
    pipeline.model_factory = ReservingModel
    original_finish = store.finish_translation
    state = {"completed": 0}

    def stop_after_two(*args, **kwargs):
        # Let both blocks consume their reservation, then stop the job.
        if not kwargs.get("error"):
            state["completed"] += 1
            # The second block has already reserved; stopping here leaves the
            # job failed with two requests used.
            if state["completed"] >= 2:
                raise ProcessingError("processing_budget_exceeded")
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(store, "finish_translation", stop_after_two)
    pipeline.run(job["id"])
    outcome = pipeline.jobs.get(job["id"])
    usage = json.loads(outcome["usage_json"])
    assert usage["requests"] == 2, usage

    with pytest.raises(ProcessingError) as raised:
        pipeline.resume(job["id"], {"requests": 0})
    assert raised.value.code == "invalid_budget"
    with pytest.raises(ProcessingError) as raised:
        pipeline.resume(job["id"], {"requests": usage["requests"] - 1})
    assert raised.value.code == "budget_below_usage"
    assert raised.value.details["overage"]["requests"]["used"] == usage["requests"]
    with pytest.raises(ProcessingError) as raised:
        pipeline.resume(job["id"], {"requests": 2_001})
    assert raised.value.code == "invalid_budget"
    assert pipeline.jobs.get(job["id"])["status"] == "failed"
    # Equal to the amount already used is accepted (nothing below it is).
    pipeline.resume(job["id"], {"requests": usage["requests"]})
    assert json.loads(pipeline.jobs.get(job["id"])["budget_json"])["requests"] == usage["requests"]
    pipeline.jobs.cancel(job["id"])


def test_resume_without_a_budget_keeps_the_saved_envelope(store, pipeline, monkeypatch):  # noqa: F811
    job, outcome = stopped_job_with_usage(pipeline, store, monkeypatch)
    before = json.loads(outcome["budget_json"])
    pipeline.resume(job["id"])
    assert json.loads(pipeline.jobs.get(job["id"])["budget_json"]) == before


def test_non_translation_kinds_reject_budget_adjustment(store, pipeline):  # noqa: F811
    jobs = ProcessingJobs(store)
    job, _ = jobs.create("paper", "overview", {})
    # The pipeline entry point refuses the kind outright, before any state change.
    for operation in (lambda: pipeline.resume_plan(job["id"]),
                      lambda: pipeline.resume(job["id"], {"requests": 300})):
        with pytest.raises(ProcessingError) as raised:
            operation()
        assert raised.value.code == "unsupported_job_kind_for_budget"
    # And the kind's ceiling still rejects a widened value at validation time.
    with pytest.raises(ProcessingError) as raised:
        ProcessingJobs.budget({"requests": 300}, kind="overview")
    assert raised.value.code == "invalid_budget"
    assert jobs.get(job["id"])["status"] == "queued"


# --- reserved vs supplier-reported usage ------------------------------------


def test_actual_usage_stays_separate_from_the_reservation(store):
    jobs = ProcessingJobs(store)
    job, _ = jobs.create("paper", "translate", {})
    jobs.claim(job["id"])
    first = jobs.reserve_attempt(job["id"], "model", "b1", input_tokens=1_000, output_tokens=2_000)
    jobs.finish_attempt(job["id"], first, "completed", {"inputTokens": 120, "outputTokens": 80})
    second = jobs.reserve_attempt(job["id"], "model", "b2", input_tokens=1_000, output_tokens=2_000)
    jobs.finish_attempt(job["id"], second, "unknown", {})

    reserved = json.loads(jobs.get(job["id"])["usage_json"])
    actual = jobs.actual_usage(job["id"])
    assert reserved["inputTokens"] == 2_000 and reserved["outputTokens"] == 4_000
    assert actual["inputTokens"] == 120 and actual["outputTokens"] == 80
    assert actual["requests"] == 2
    assert actual["reportedAttempts"] == 1 and actual["missingAttempts"] == 1
    assert actual["complete"] is False  # missing reports are never invented as zero


def test_other_owner_cannot_plan_or_resume_the_job(store, pipeline, monkeypatch):  # noqa: F811
    from ipaper.processing.store import ProcessingStore

    job, _ = stopped_job_with_usage(pipeline, store, monkeypatch)
    other = ProcessingStore(store.db_path, store.papers_root, OTHER)
    other_pipeline = type(pipeline)(other, pipeline.profiles, pipeline.credentials, None,
                                    document_client=pipeline.document, cloud_factory=FakeCloud,
                                    model_factory=FakeModel)
    for operation in (lambda: other_pipeline.resume_plan(job["id"]),
                      lambda: other_pipeline.resume(job["id"], {"requests": 500})):
        with pytest.raises(ProcessingError):
            operation()
    assert json.loads(pipeline.jobs.get(job["id"])["budget_json"])["requests"] == 2

# --- regressions ported from the independent 1.12.0 review -----------------


def test_default_admission_respects_a_lower_deployment_ceiling(store, pipeline, tmp_path, monkeypatch):  # noqa: F811
    import ipaper.processing.jobs as module

    monkeypatch.setattr(
        module, "MAX_TRANSLATION_BUDGET", {**module.MAX_TRANSLATION_BUDGET, "requests": 100}
    )
    preview, parsed = publish_scale_structure(store, pipeline, tmp_path, translatable=245, empty=26)
    data = {"kind": "parse_translate", "preflightId": preview["preflightId"], "parseResultId": parsed}
    scope = pipeline.create("paper", data, preview=True)
    # The effective default is the lowered ceiling, never above it.
    assert scope["limits"]["requests"] == 100
    assert scope["budget"]["requests"] == 100
    assert scope["defaultCappedByCeiling"] is True
    # 245 base requests cannot pass a 100 request ceiling, and the error names it.
    with pytest.raises(ProcessingError) as raised:
        pipeline.create("paper", data)
    assert raised.value.code == "processing_scope_exceeds_budget"
    assert raised.value.details["overage"]["requests"]["limit"] == 100
    # A scope inside the ceiling is admitted and stores a budget within it.
    small, _parsed2 = publish_scale_structure(store, pipeline, tmp_path, translatable=40, empty=0)
    job, _ = pipeline.create(
        "paper", {"kind": "parse_translate", "preflightId": small["preflightId"], "parseResultId": _parsed2}
    )
    assert json.loads(job["budget_json"])["requests"] <= 100


def test_zero_and_partial_budgets_are_validated_against_the_ceiling(store):
    import ipaper.processing.jobs as module

    ceiling = dict(module.MAX_TRANSLATION_BUDGET)
    # Every composition path: default, empty dict and partial dict.
    for value in (None, {}, {"requests": 200}):
        result = ProcessingJobs.budget(value, kind="translate")
        assert all(result[key] <= ceiling[key] for key in ("requests", "inputTokens", "outputTokens", "seconds"))


def test_exhausted_time_is_not_sufficient_and_cannot_be_queued(store, pipeline, tmp_path):  # noqa: F811
    # Two blocks, the second interrupted during its model request: the job really
    # has pending work (no checkpoint exists for it).
    preview, parsed = publish_scale_structure(store, pipeline, tmp_path, translatable=2, empty=0, pages=1)

    class StopOnSecond(ReservingModel):
        calls = 0

        def translate(self, units, language, jobs, job_id):
            StopOnSecond.calls += 1
            if StopOnSecond.calls >= 2:
                raise ProcessingError("model_result_unknown")
            return super().translate(units, language, jobs, job_id)

    pipeline.model_factory = StopOnSecond
    job, _ = pipeline.create(
        "paper",
        {"kind": "parse_translate", "preflightId": preview["preflightId"], "parseResultId": parsed,
         "budget": {"requests": 2}},
    )
    pipeline.run(job["id"])
    saved = pipeline.jobs.get(job["id"])
    assert saved["status"] == "interrupted", saved["error"]
    pending = pipeline.resume_plan(job["id"])
    assert pending["remainingWork"] is True
    assert pending["estimate"]["requests"] == 1

    budget = json.loads(saved["budget_json"])
    usage = json.loads(saved["usage_json"])
    usage["seconds"] = budget["seconds"]
    with store.connection(write=True) as db:
        db.execute(
            "UPDATE processing_jobs SET status='interrupted',error='processing_time_budget',usage_json=? WHERE id=?",
            (json.dumps(usage), job["id"]),
        )
    plan = pipeline.resume_plan(job["id"])
    assert plan["sufficient"] is False
    assert plan["time"]["exhausted"] is True
    assert plan["overage"]["seconds"]["required"] == usage["seconds"] + 1
    # Raising only the request allowance must not queue a task that times out again.
    with pytest.raises(ProcessingError) as raised:
        pipeline.resume(job["id"], {"requests": budget["requests"] + 1})
    assert raised.value.code == "budget_time_exhausted"
    assert raised.value.details["minimum"] == usage["seconds"] + 1
    assert pipeline.jobs.get(job["id"])["status"] == "interrupted"
    # Extending the累计 hours is enough on its own.
    pipeline.resume(job["id"], {"seconds": budget["seconds"] + 3600})
    resumed = pipeline.jobs.get(job["id"])
    assert resumed["status"] == "queued"
    assert json.loads(resumed["budget_json"])["seconds"] == budget["seconds"] + 3600
    assert json.loads(resumed["usage_json"])["seconds"] == usage["seconds"]
    pipeline.jobs.cancel(job["id"])


def test_retranslate_resume_plan_matches_the_executor_estimate(store, pipeline):  # noqa: F811
    preview = pipeline.preflight("paper")
    original, _ = pipeline.create("paper", {"preflightId": preview["preflightId"]})
    pipeline.run(original["id"])
    original = pipeline.jobs.get(original["id"])
    translated_id = original["result_id"]
    parse_id = store.result(translated_id)["parse_id"]
    block = store.blocks(translated_id)[0]
    job, _ = pipeline.create(
        "paper",
        {"kind": "retranslate", "preflightId": preview["preflightId"], "parseResultId": parse_id,
         "translationResultId": translated_id, "blockIds": [block["id"]]},
    )
    pipeline.jobs.cancel(job["id"])
    plan = pipeline.resume_plan(job["id"])
    execution = pipeline.estimate(
        parse_id, result_id=translated_id, block_ids=[block["id"]], force=True,
        checkpoint_root=store.artifact_directory(job["id"]),
        request=json.loads(job["request_json"]),
    )
    # The earlier successful translation is not "already done" for an explicit
    # retranslation, so the plan must not hide the request it will spend.
    assert plan["estimate"]["requests"] == execution["requests"] == 1


@pytest.mark.parametrize(
    "response, expected",
    [
        ({"inputTokens": 12, "outputTokens": None}, {"input": True, "output": False}),
        ({"inputTokens": None, "outputTokens": 7}, {"input": False, "output": True}),
        ({"inputTokens": None, "outputTokens": None}, {"input": False, "output": False}),
        ({"inputTokens": "12", "outputTokens": 7.5}, {"input": False, "output": False}),
        ({"inputTokens": 12, "outputTokens": 7}, {"input": True, "output": True}),
    ],
)
def test_actual_usage_completeness_is_per_dimension(store, response, expected):
    jobs = ProcessingJobs(store)
    job, _ = jobs.create("paper", "translate", {})
    jobs.claim(job["id"])
    attempt = jobs.reserve_attempt(job["id"], "model", "unit", input_tokens=100, output_tokens=200)
    jobs.finish_attempt(job["id"], attempt, "completed", response)
    actual = jobs.actual_usage(job["id"])
    assert actual["inputComplete"] is expected["input"]
    assert actual["outputComplete"] is expected["output"]
    assert actual["complete"] is (expected["input"] and expected["output"])
    assert actual["inputMissingAttempts"] == (0 if expected["input"] else 1)
    assert actual["outputMissingAttempts"] == (0 if expected["output"] else 1)
    # Reserved counts are untouched by missing supplier data.
    reserved = json.loads(jobs.get(job["id"])["usage_json"])
    assert reserved["inputTokens"] == 100 and reserved["outputTokens"] == 200


def test_actual_usage_without_requests_is_not_claimed_complete(store):
    jobs = ProcessingJobs(store)
    job, _ = jobs.create("paper", "translate", {})
    actual = jobs.actual_usage(job["id"])
    assert actual["requests"] == 0
    assert actual["complete"] is False
    assert actual["inputComplete"] is False and actual["outputComplete"] is False


def test_in_flight_attempts_count_as_missing_usage(store):
    jobs = ProcessingJobs(store)
    job, _ = jobs.create("paper", "translate", {})
    jobs.claim(job["id"])
    jobs.reserve_attempt(job["id"], "model", "unit", input_tokens=100, output_tokens=200)
    actual = jobs.actual_usage(job["id"])
    assert actual["requests"] == 1
    assert actual["complete"] is False
    assert actual["inputMissingAttempts"] == 1 and actual["outputMissingAttempts"] == 1


def test_exhausted_clock_can_still_finish_publishing_saved_work(store, pipeline):  # noqa: F811
    """No model work left: an exhausted clock must not fail the continuation."""
    preview = pipeline.preflight("paper")
    job, _ = pipeline.create("paper", {"kind": "parse_translate", "preflightId": preview["preflightId"]})
    pipeline.run(job["id"])
    saved = pipeline.jobs.get(job["id"])
    assert saved["status"] == "completed"
    budget = json.loads(saved["budget_json"])
    usage = json.loads(saved["usage_json"])
    usage["seconds"] = budget["seconds"]
    with store.connection(write=True) as db:
        db.execute(
            "UPDATE processing_jobs SET status='interrupted',error='processing_time_budget',usage_json=? WHERE id=?",
            (json.dumps(usage), job["id"]),
        )
    plan = pipeline.resume_plan(job["id"])
    assert plan["remainingWork"] is False
    assert plan["sufficient"] is True  # nothing left to request
    pipeline.resume(job["id"])
    pipeline.run(job["id"])
    outcome = pipeline.jobs.get(job["id"])
    # Previously this failed instantly with processing_time_budget even though no
    # model request was needed.
    assert outcome["status"] == "completed", outcome["error"]
    assert outcome["error"] is None


def test_exhausted_clock_publishes_completed_unit_checkpoints(store, pipeline, tmp_path, monkeypatch):  # noqa: F811
    """R8: units are all checkpointed, the block is unpublished, clock is spent.

    The plan and the executor agree that there is no model work left, so the
    continuation publishes the saved result instead of failing on the clock —
    while a genuine pending request is still refused.
    """
    preview, parsed = publish_scale_structure(store, pipeline, tmp_path, translatable=1, empty=0, pages=1)
    pipeline.model_factory = ReservingModel
    job, _ = pipeline.create(
        "paper",
        {"kind": "parse_translate", "preflightId": preview["preflightId"], "parseResultId": parsed},
    )
    original_finish = store.finish_translation
    state = {"interrupted": False}

    def stop_after_units(*args, **kwargs):
        if not kwargs.get("error") and not state["interrupted"]:
            state["interrupted"] = True
            raise ProcessingError("model_result_unknown")
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(store, "finish_translation", stop_after_units)
    pipeline.run(job["id"])
    stopped = pipeline.jobs.get(job["id"])
    assert stopped["status"] == "interrupted", stopped["error"]
    usage = json.loads(stopped["usage_json"])
    budget = json.loads(stopped["budget_json"])
    usage["seconds"] = budget["seconds"]
    with store.connection(write=True) as db:
        db.execute("UPDATE processing_jobs SET usage_json=? WHERE id=?", (json.dumps(usage), job["id"]))

    plan = pipeline.resume_plan(job["id"])
    assert plan["estimate"]["requests"] == 0
    assert plan["remainingWork"] is False
    assert plan["sufficient"] is True

    pipeline.resume(job["id"], {"requests": budget["requests"] + 1})
    pipeline.run(job["id"])
    finished = pipeline.jobs.get(job["id"])
    assert finished["status"] == "completed", finished["error"]
    # Reused checkpoints: no extra supplier request, and usage was not reset.
    assert json.loads(finished["usage_json"])["requests"] == usage["requests"]
    assert json.loads(finished["usage_json"])["seconds"] >= 3600


def test_pending_work_with_an_exhausted_clock_is_still_refused(store, pipeline, tmp_path):  # noqa: F811
    """R8 guard: the time limit is not removed for real remaining requests."""
    preview, parsed = publish_scale_structure(store, pipeline, tmp_path, translatable=2, empty=0, pages=1)

    class StopOnSecond(ReservingModel):
        calls = 0

        def translate(self, units, language, jobs, job_id):
            StopOnSecond.calls += 1
            if StopOnSecond.calls >= 2:
                raise ProcessingError("model_result_unknown")
            return super().translate(units, language, jobs, job_id)

    pipeline.model_factory = StopOnSecond
    job, _ = pipeline.create(
        "paper",
        {"kind": "parse_translate", "preflightId": preview["preflightId"], "parseResultId": parsed,
         "budget": {"requests": 2}},
    )
    pipeline.run(job["id"])
    stopped = pipeline.jobs.get(job["id"])
    assert stopped["status"] == "interrupted"
    budget = json.loads(stopped["budget_json"])
    usage = json.loads(stopped["usage_json"])
    usage["seconds"] = budget["seconds"]
    with store.connection(write=True) as db:
        db.execute("UPDATE processing_jobs SET usage_json=? WHERE id=?", (json.dumps(usage), job["id"]))
    plan = pipeline.resume_plan(job["id"])
    assert plan["remainingWork"] is True and plan["sufficient"] is False
    with pytest.raises(ProcessingError) as raised:
        pipeline.resume(job["id"], {"requests": budget["requests"] + 1})
    assert raised.value.code == "budget_time_exhausted"
