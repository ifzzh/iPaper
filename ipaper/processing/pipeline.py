"""Three-service processing orchestration; untrusted PDFs/archives stay in Worker."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import threading
from pathlib import Path

from ipaper.document_worker.client import DocumentWorkerClient
from ipaper.document_worker.safety import bounded_copy
from ipaper.security.paths import ensure_confined, safe_join, user_storage_root
from .common import ProcessingError, encoded, fingerprint, identifier, now
from .jobs import ProcessingJobs
from .translation import StructuredModel, units_for, assemble, LANGUAGES, request_payload, generation_options
from .cloud import MinerUCloud


# The supported Web runtime is one process. Admission is nonblocking so a PDF
# preflight cannot consume all request threads or fill Worker staging on clicks.
_PREFLIGHT_LOCK = threading.Lock()
_PREFLIGHT_OWNERS = set()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ProcessingPipeline:
    def __init__(self, store, profiles, credentials, policy, *, document_client=None, cloud_factory=MinerUCloud, model_factory=StructuredModel):
        self.store, self.profiles, self.credentials, self.policy = store, profiles, credentials, policy
        self.document = document_client or DocumentWorkerClient()
        self.cloud_factory, self.model_factory = cloud_factory, model_factory
        result_quota, owner_quota = store.quotas()
        self.jobs = ProcessingJobs(store, result_quota=result_quota, owner_quota=owner_quota)

    def paper_file(self, paper_id):
        with self.store.connection() as db:
            self.store.paper_exists(db, paper_id)
            row = db.execute("SELECT file_path FROM papers WHERE id=? AND owner_id=?", (paper_id, self.store.owner)).fetchone()
        path = ensure_confined(self.store.papers_root, row["file_path"], must_exist=True, require_file=True)
        try:
            path.relative_to(user_storage_root(self.store.papers_root, self.store.owner))
        except ValueError:
            raise ProcessingError("unsafe_source_path", 409) from None
        return path

    def settings(self):
        with self.store.connection() as db:
            row = db.execute("SELECT value FROM user_settings_v2 WHERE owner_id=? AND key='agentic_settings'", (self.store.owner,)).fetchone()
        return json.loads(row[0]) if row else {}

    def inspect_document(self, paper_id, source, kind):
        sha = file_digest(source)
        worker_id = identifier()
        try:
            with source.open("rb") as handle:
                self.document.stage(worker_id, "pdf_inspect", handle)
            self.document.create(worker_id, "pdf_inspect")
            outcome = self.document.wait(worker_id, timeout=90)
            if outcome["status"] != "completed":
                raise ProcessingError("layout_pdf_invalid", 422)
            data = self.document.result_json(worker_id)
            if data.get("sha256") != sha or file_digest(source) != sha:
                raise ProcessingError("source_changed", 409)
            return self.store.register_document(paper_id,kind=kind,sha256=sha,size=source.stat().st_size,
                    geometry=data["pages"],file_ref=str(source.relative_to(self.store.papers_root)))
        finally:
            self.document.cleanup(worker_id)

    def register_layouts(self, paper_id, *, freeze=False):
        from ipaper.security.paths import paper_asset_paths
        assets = paper_asset_paths(self.store.papers_root, self.paper_file(paper_id))
        for mode,path in (("dual",assets.chinese_dual),("mono",assets.chinese_mono)):
            if not path.exists():
                continue
            sha = file_digest(path)
            with self.store.connection() as db:
                known = db.execute("""SELECT r.id,d.id AS doc_id,d.file_ref,r.manifest_json FROM processing_results r
                    JOIN processing_documents d ON d.id=r.document_id WHERE r.owner_id=? AND r.paper_id=?
                    AND r.kind=? AND d.sha256=? ORDER BY r.created_at DESC LIMIT 1""",
                    (self.store.owner,paper_id,"babeldoc_"+mode,sha)).fetchone()
            if known:
                result_id = known["id"]
                # A category move updates only the unversioned historical alias;
                # immutable copies remain independent of category directories.
                if not json.loads(known["manifest_json"]).get("entries"):
                    with self.store.connection(write=True) as db:
                        db.execute("UPDATE processing_documents SET file_ref=? WHERE id=?",
                                   (str(path.relative_to(self.store.papers_root)),known["doc_id"]))
            else:
                doc_id = self.inspect_document(paper_id,path,"babeldoc_"+mode)
                result_id = self.store.new_result(doc_id,"babeldoc_"+mode,
                    {"provenance":"historical_config_unknown","outputMode":mode},target_language="zh-CN")
                with self.store.connection(write=True) as db:
                    db.execute("UPDATE processing_results SET status='completed',updated_at=? WHERE id=?",(now(),result_id))
            if freeze:
                self.store.publish_layout(result_id,path)

    def publish_layout_job(self, job_id, paper_id, path):
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM processing_layout_jobs WHERE job_id=? AND owner_id=?",(job_id,self.store.owner)).fetchone()
        if not row:
            self.register_layouts(paper_id,freeze=True)
            return
        if row["result_id"]:
            self.store.result(row["result_id"])
            return
        config = json.loads(row["snapshot_json"])
        source = self.paper_file(paper_id)
        # Never attach a late translation to a replaced original.
        if file_digest(source) != config["sourceSha256"]:
            raise ProcessingError("source_changed",409)
        source_id = self.inspect_document(paper_id,source,"original")
        doc_id = self.inspect_document(paper_id,Path(path),"babeldoc_"+config["outputMode"])
        result_id = self.store.new_result(doc_id,"babeldoc_"+config["outputMode"],
                    {**config,"sourceDocumentId":source_id,"provenance":"verified_job"},
                    source_language="en",target_language="zh-CN")
        self.store.publish_layout(result_id,Path(path))
        with self.store.connection(write=True) as db:
            db.execute("UPDATE processing_layout_jobs SET result_id=? WHERE job_id=? AND owner_id=?",(result_id,job_id,self.store.owner))

    def preflight(self, paper_id):
        with _PREFLIGHT_LOCK:
            if self.store.owner in _PREFLIGHT_OWNERS:
                raise ProcessingError("document_preflight_busy", 409)
            if len(_PREFLIGHT_OWNERS) >= 2:
                raise ProcessingError("document_preflight_busy", 429)
            _PREFLIGHT_OWNERS.add(self.store.owner)
        try:
            return self._preflight(paper_id)
        finally:
            with _PREFLIGHT_LOCK:
                _PREFLIGHT_OWNERS.discard(self.store.owner)

    def _preflight(self, paper_id):
        source = self.paper_file(paper_id)
        sha = file_digest(source)
        with self.store.connection() as db:
            existing = db.execute("""SELECT r.* FROM processing_results r JOIN processing_documents d ON d.id=r.document_id
                  WHERE r.owner_id=? AND r.paper_id=? AND r.kind='document_parts' AND r.status='completed'
                  AND d.sha256=? ORDER BY r.created_at DESC LIMIT 1""", (self.store.owner, paper_id, sha)).fetchone()
        if existing:
            return self._preview(dict(existing))
        # Reserve capacity conservatively before staging. Publication rechecks
        # the actual bytes transactionally against competing admissions.
        with self.store.connection() as db:
            self.store.check_quota(db, self.store.quotas()[0])
        if not self.document.health():
            raise ProcessingError("document_worker_unavailable", 503)
        worker_id = identifier()
        try:
            with source.open("rb") as handle:
                self.document.stage(worker_id, "pdf_split", handle)
            self.document.create(worker_id, "pdf_split")
            state = self.document.wait(worker_id, timeout=300)
            if state["status"] != "completed":
                raise ProcessingError("document_preflight_failed", 422)
            manifest = self.document.verified_manifest(worker_id, "pdf_split")
            output = self.document.output(worker_id)
            result = self.document.result_json(worker_id)
            if result["sha256"] != sha or file_digest(source) != sha:
                raise ProcessingError("source_changed", 409)
            doc_id = self.store.register_document(paper_id, kind="original", sha256=sha, size=source.stat().st_size,
                        geometry=result["pages"], file_ref=str(source.relative_to(self.store.papers_root)))
            result_id = self.store.new_result(doc_id, "document_parts", {"schema": 1, "maxPages": 200})
            self.store.publish_structure(result_id, output, manifest)
            return self._preview(self.store.result(result_id))
        finally:
            self.document.cleanup(worker_id)

    def _preview(self, result):
        data = json.loads(safe_join(self.store.artifact_directory(result["id"]), "result.json", must_exist=True, require_file=True).read_text("utf-8"))
        structures = [r for r in self.store.results(result["paper_id"]) if r["kind"] == "structure" and r["document_id"] == result["document_id"] and r["status"] == "completed" and not json.loads(r["config_json"]).get("internalPart")]
        return {"preflightId": result["id"], "documentId": result["document_id"], "sha256": data["sha256"],
                "pageCount": data["pageCount"], "partCount": len(data["parts"]),
                "parseResultId": structures[0]["id"] if structures else None,
                "model": self.profiles.get(), "budget": self.jobs.budget(),
                "mineruConfigured": bool(self.settings().get("mineruUseApi") and self.credentials and self.credentials.configured("mineru")),
                "estimate": self.estimate(structures[0]["id"]) if structures else None,
                "estimateNote": "解析前无法准确计算块数；开始翻译前再次核对处理范围和预算。"}

    def estimate(self, parse_id, *, result_id=None, pages=None, block_ids=None, target="zh-CN", force=False, checkpoint_root=None, request=None):
        selected = cached = requests = inputs = outputs = 0
        after = -1
        while True:
            blocks = self.store.blocks(result_id or parse_id,after=after,limit=100)
            if not blocks:
                break
            for block in blocks:
                if pages and block["source"].get("page") not in pages or block_ids and block["id"] not in block_ids:
                    continue
                selected += 1
                if not force and block["translation"] and block["translation"]["status"] == "completed":
                    cached += 1
                    continue
                units=units_for(block)
                if checkpoint_root is not None:
                    saved = self._unit_checkpoint(checkpoint_root, result_id, block["id"], request)
                    units = [unit for unit in units if unit["id"] not in saved]
                for offset in range(0,len(units),8):
                    _,input_bound,output_bound=request_payload(units[offset:offset+8],target)
                    requests+=1;inputs+=input_bound;outputs+=output_bound
            after=blocks[-1]["order"]
        return {"selectedBlocks":selected,"cachedBlocks":cached,"requests":requests,"inputTokens":inputs,"outputTokens":outputs,
                "estimation":"保守预留量；实际 token 以供应商返回的用量为准"}

    def create(self, paper_id, data, *, preview=False):
        kind = data.get("kind", "parse_translate")
        if not isinstance(kind,str) or kind not in {"parse","translate","parse_translate","retranslate"}:
            raise ProcessingError("invalid_processing_kind")
        preflight_id = identifier(data.get("preflightId"))
        preflight = self.store.result(preflight_id)
        if preflight["paper_id"] != paper_id or preflight["kind"] != "document_parts":
            raise ProcessingError("invalid_preflight")
        document = self.store.document(preflight["document_id"])
        if file_digest(self.paper_file(paper_id)) != document["sha256"]:
            raise ProcessingError("source_changed", 409)
        source_language, target = data.get("sourceLanguage", "auto"), data.get("targetLanguage", "zh-CN")
        if not isinstance(target,str) or not isinstance(source_language,str) or target not in LANGUAGES or source_language not in {"auto", *LANGUAGES}:
            raise ProcessingError("invalid_language")
        pages = data.get("pages")
        if pages is not None and (not isinstance(pages, list) or not pages or len(pages) > document["page_count"] or any(type(page) is not int or not 1 <= page <= document["page_count"] for page in pages)):
            raise ProcessingError("invalid_page_range")
        parse_id = data.get("parseResultId")
        if parse_id:
            parsed = self.store.result(parse_id)
            if parsed["document_id"] != document["id"] or parsed["kind"] != "structure" or parsed["status"] != "completed":
                raise ProcessingError("parse_version_mismatch", 409)
        profile = self.profiles.get()
        if kind != "parse" and (not profile["revision"] or not profile["keyConfigured"]):
            raise ProcessingError("structured_settings_not_configured")
        if not parse_id and (not self.settings().get("mineruUseApi") or not self.credentials.configured("mineru")):
            raise ProcessingError("mineru_cloud_not_configured")
        blocks = data.get("blockIds")
        if blocks is not None:
            if not parse_id or not isinstance(blocks, list) or not 1 <= len(blocks) <= 1000 or any(not isinstance(v, str) for v in blocks):
                raise ProcessingError("invalid_block_range")
            for block in blocks:
                self.store.block(parse_id, block)
        if kind == "retranslate" and (not blocks or len(blocks) != 1):
            raise ProcessingError("retranslation_requires_one_block")
        config = {"model": profile["model"], "baseUrl": profile["baseUrl"], "revision": profile["revision"],
                  "sourceLanguage": source_language, "targetLanguage": target, "promptVersion": 1, "generationOptions": generation_options(profile["model"])}
        result_id = data.get("translationResultId")
        if result_id:
            translated = self.store.result(result_id)
            if translated["parse_id"] != parse_id or translated["kind"] != "structured_translation" or translated["config_fingerprint"] != fingerprint(config):
                raise ProcessingError("translation_config_changed", 409)
        if not result_id and parse_id and kind != "parse":
            with self.store.connection() as db:
                cached = db.execute("""SELECT id FROM processing_results WHERE owner_id=? AND parse_id=?
                    AND kind='structured_translation' AND config_fingerprint=? ORDER BY created_at DESC LIMIT 1""",
                    (self.store.owner, parse_id, fingerprint(config))).fetchone()
                if cached:
                    result_id = cached["id"]
        # A new explicit retranslation is distinguished by the current visible
        # revision, so repeated clicks return the same job; a subsequent user
        # action after success may legitimately generate another revision.
        estimate = None
        budget = self.jobs.budget(data.get("budget"))
        if parse_id and kind != "parse":
            estimate=self.estimate(parse_id,result_id=result_id,pages=pages,block_ids=blocks,target=target,force=kind=="retranslate")
        exceeds = bool(estimate and any(estimate[key] > budget[key] for key in ("requests", "inputTokens", "outputTokens")))
        if preview:
            return {"estimate": estimate, "budget": budget, "exceedsBudget": exceeds,
                    "parseRequired": not bool(parse_id), "pageCount": document["page_count"],
                    "selectedPages": len(set(pages)) if pages else document["page_count"],
                    "translationResultId": result_id, "model": profile["model"],
                    "targetLanguage": target}
        if exceeds:
            raise ProcessingError("processing_scope_exceeds_budget", 409)
        previous_revision = self.store.translation(result_id, blocks[0]) if kind == "retranslate" and result_id else None
        request = {"preflightId": preflight_id, "parseId": parse_id, "pages": sorted(set(pages)) if pages else None,
                   "blockIds": sorted(set(blocks)) if blocks else None, "config": config,
                   "sourceSha256": document["sha256"], "previousRevision": previous_revision.get("revision") if previous_revision else None}
        return self.jobs.create(paper_id, kind, request, document_id=document["id"], result_id=result_id, budget=data.get("budget"))

    def run(self, job_id):
        if self.jobs.get(job_id)["kind"] in {"overview", "interpretation", "analysis_export"}:
            from .understanding import Understanding
            return Understanding(self).run(job_id)
        if not self.jobs.claim(job_id):
            return
        try:
            job = self.jobs.get(job_id)
            request = json.loads(job["request_json"])
            self._check_source(job["paper_id"], request["sourceSha256"])
            checkpoint = json.loads(job["checkpoint_json"])
            parse_id = checkpoint.get("parseId") or request.get("parseId")
            if not parse_id:
                parse_id = self._parse(job, request, checkpoint)
            if job["kind"] == "parse":
                self.jobs.checkpoint(job_id, "completed", {**checkpoint, "parseId": parse_id}, result_id=parse_id)
                self.jobs.finish(job_id, "completed")
                return
            self._translate(job_id, parse_id, request)
        except ProcessingError as exc:
            status = "cancelled" if exc.code == "processing_cancelled" else "interrupted" if exc.code in {"model_result_unknown", "mineru_response_unknown", "mineru_upload_unknown", "processing_time_budget"} else "failed"
            current_job = self.jobs.get(job_id)
            if current_job["result_id"]:
                with self.store.connection(write=True) as db:
                    db.execute("UPDATE processing_results SET status=?,updated_at=? WHERE id=? AND owner_id=? AND kind='structured_translation'",
                               ("partial", now(), current_job["result_id"], self.store.owner))
            self.jobs.finish(job_id, status, error=exc.code)
        except Exception:
            self.jobs.finish(job_id, "failed", error="processing_failed")

    def _check_source(self, paper_id, sha):
        if file_digest(self.paper_file(paper_id)) != sha:
            raise ProcessingError("source_changed", 409)

    @staticmethod
    def _unit_checkpoint_path(root, result_id, block_id, request):
        return root / (fingerprint([result_id, block_id, request["config"], request["previousRevision"]]) + ".json")

    def _unit_checkpoint(self, root, result_id, block_id, request):
        path = self._unit_checkpoint_path(root, result_id, block_id, request)
        if not path.exists():
            return {}
        if path.is_symlink() or path.stat().st_size > 4*1024**2:
            raise ProcessingError("invalid_translation_checkpoint", 409)
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k,v in data.items()):
            raise ProcessingError("invalid_translation_checkpoint", 409)
        return data

    def _parse(self, job, request, checkpoint):
        job_id = job["id"]
        parts_root = self.store.artifact_directory(request["preflightId"])
        parts_info = json.loads((parts_root / "result.json").read_text("utf-8"))
        scratch = self.store.artifact_directory(job_id)
        scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        cloud = self.cloud_factory(self.credentials.get("mineru"), self.policy)
        part_states = checkpoint.setdefault("parts", {})
        for index, part in enumerate(parts_info["parts"]):
            self.jobs.check(job_id)
            self._check_source(job["paper_id"], request["sourceSha256"])
            state = part_states.setdefault(str(index), {})
            if state.get("resultId"):
                self.store.result(state["resultId"])
                continue
            source = safe_join(parts_root, part["path"], must_exist=True, require_file=True)
            if file_digest(source) != part["sha256"]:
                raise ProcessingError("source_changed", 409)
            if not state.get("batchId"):
                def save_batch(batch):
                    state["batchId"] = batch
                    self.jobs.checkpoint(job_id, "cloud_upload", checkpoint, completed=index, total=len(parts_info["parts"]))
                cloud.submit(source, f"part-{index}", self.jobs, job_id, save_batch)
            archive = scratch / f"part-{index}.zip"
            if not archive.exists():
                cloud.wait_download(state["batchId"], archive, self.jobs, job_id)
            worker_id = identifier()
            try:
                with archive.open("rb") as bundle, source.open("rb") as pdf:
                    self.document.stage_structure(worker_id, bundle, pdf)
                self.document.create(worker_id, "mineru_structure")
                outcome = self.document.wait(worker_id, timeout=300)
                if outcome["status"] != "completed":
                    raise ProcessingError("structure_worker_rejected", 422)
                manifest = self.document.verified_manifest(worker_id, "mineru_structure")
                output = self.document.output(worker_id)
                normalized = self.document.result_json(worker_id)
                if normalized["sourceSha256"] != part["sha256"]:
                    raise ProcessingError("source_changed", 409)
                # Each part is immutable. The merge registers global coordinates
                # against the original document and keeps the part provenance.
                part_result = self.store.new_result(job["document_id"], "structure", {"normalizer": normalized["normalizer"], "part": index, "batchId": state["batchId"], "internalPart": True})
                self.store.publish_structure(part_result, output, manifest, job_id=job_id)
                state["resultId"] = part_result
                self.jobs.checkpoint(job_id, "normalizing", checkpoint, completed=index+1, total=len(parts_info["parts"]))
                archive.unlink()
            finally:
                self.document.cleanup(worker_id)
        return self._merge(job, parts_info, part_states, scratch, checkpoint)

    def _merge(self, job, info, states, scratch, checkpoint):
        merged = scratch / "merged"
        if merged.exists():
            shutil.rmtree(merged)
        merged.mkdir(mode=0o700)
        entries, total, ordinal = [], 0, 0
        # Only already Worker-validated JSON is read here; no PDF parsing or ZIP
        # extraction in Web. Original geometry comes from the PDF split manifest.
        with (merged / "blocks.jsonl").open("wb") as writer:
            for index, part in enumerate(info["parts"]):
                result_id = states[str(index)]["resultId"]
                root = self.store.artifact_directory(result_id)
                after = -1
                while True:
                    blocks = self.store.blocks(result_id, after=after, limit=100)
                    if not blocks:
                        break
                    after = blocks[-1]["order"]
                    for block in blocks:
                        source = block["source"]
                        if "page" in source:
                            local = source["page"] - 1
                            global_index = part["firstPage"] - 1 + local
                            original_geometry = info["pages"][global_index]
                            part_geometry = part["pages"][local]
                            if any(original_geometry.get(key) != part_geometry.get(key) for key in ("mediaBox", "cropBox", "userUnit", "rotation", "pdfToPage")):
                                source["precision"], source["regions"] = "page", []
                            source["page"] = global_index + 1
                            for region in source["regions"]:
                                region["page"] += part["firstPage"] - 1
                        block["id"] = f"p{part['firstPage']:06d}-{block['id']}"
                        block["order"] = ordinal
                        block.pop("translation", None)
                        if block.get("image"):
                            original_image = safe_join(root, block["image"], must_exist=True, require_file=True)
                            image_name = f"images/{index}-{file_digest(original_image)}{original_image.suffix.lower()}"
                            target = safe_join(merged, image_name)
                            if not target.exists():
                                with original_image.open("rb") as handle:
                                    bounded_copy(handle, target, 100*1024**2)
                            block["image"] = image_name
                        writer.write((encoded(block) + "\n").encode())
                        ordinal += 1
        for path in sorted(merged.rglob("*")):
            if path.is_file():
                size = path.stat().st_size
                total += size
                if total > 1024**3:
                    raise ProcessingError("result_quota_exceeded", 413)
                entries.append({"path": path.relative_to(merged).as_posix(), "size": size, "sha256": file_digest(path)})
        result_id = self.store.new_result(job["document_id"], "structure", {"normalizer": "mineru-content-list-v1/1", "modelVersion": "vlm", "parts": [states[str(i)]["resultId"] for i in range(len(info["parts"]))]})
        self.store.publish_structure(result_id, merged, {"kind": "structure_merge", "entries": entries, "ignored": []}, job_id=job["id"])
        checkpoint["parseId"] = result_id
        self.jobs.checkpoint(job["id"], "parsed", checkpoint, result_id=result_id)
        shutil.rmtree(merged)
        return result_id

    def _translate(self, job_id, parse_id, request):
        job = self.jobs.check(job_id)
        config = request["config"]
        profile = self.profiles.get(secret=True)
        if profile["revision"] != config["revision"]:
            raise ProcessingError("translation_config_changed", 409)
        model = self.model_factory(self.policy, profile)
        checkpoint = json.loads(job["checkpoint_json"])
        result_id = job["result_id"]
        if not result_id or self.store.result(result_id)["kind"] != "structured_translation":
            result_id = self.store.new_result(job["document_id"], "structured_translation", config, parse_id=parse_id,
                         source_language=config["sourceLanguage"], target_language=config["targetLanguage"])
            self.jobs.checkpoint(job_id, "translating", {**checkpoint, "parseId": parse_id}, result_id=result_id)
        scratch = self.store.artifact_directory(job_id)
        scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        estimate=self.estimate(parse_id,result_id=result_id,pages=request["pages"],block_ids=request["blockIds"],
                               target=config["targetLanguage"],force=job["kind"]=="retranslate",
                               checkpoint_root=scratch,request=request)
        checkpoint=json.loads(self.jobs.get(job_id)["checkpoint_json"])
        self.jobs.checkpoint(job_id,"translating",{**checkpoint,"estimate":estimate},total=estimate["selectedBlocks"])
        # For a new parse, this is the first reliable estimate. Stop before any
        # model call if its whole selected scope cannot fit the approved budget.
        usage,budget=json.loads(job["usage_json"]),json.loads(job["budget_json"])
        if any(estimate[key]+usage[key]>budget[key] for key in ("requests","inputTokens","outputTokens")):
            raise ProcessingError("processing_scope_exceeds_budget",409)
        failures, completed, after = 0, 0, -1
        while True:
            blocks = self.store.blocks(result_id, after=after, limit=50)
            if not blocks:
                break
            for block in blocks:
                self.jobs.check(job_id)
                if request["pages"] and block["source"].get("page") not in request["pages"]:
                    continue
                if request["blockIds"] and block["id"] not in request["blockIds"]:
                    continue
                if block["translation"] and block["translation"]["status"] == "completed" and job["kind"] != "retranslate":
                    completed += 1
                    continue
                generation, original = self.store.begin_translation(result_id, block["id"])
                units = units_for(original)
                checkpoint_file = self._unit_checkpoint_path(scratch,result_id,block["id"],request)
                translations = self._unit_checkpoint(scratch,result_id,block["id"],request)
                pending = [unit for unit in units if unit["id"] not in translations]
                try:
                    while pending:
                        self._check_source(job["paper_id"],request["sourceSha256"])
                        batch, pending = pending[:8], pending[8:]
                        translations.update(model.translate(batch, config["targetLanguage"], self.jobs, job_id))
                        content = encoded(translations).encode()
                        if len(content) > 4*1024**2:
                            raise ProcessingError("translation_size_limit")
                        bounded_copy(io.BytesIO(content), checkpoint_file, len(content))
                    self.jobs.check(job_id)
                    self.store.finish_translation(result_id, block["id"], generation, assemble(original, units, translations), job_id=job_id)
                    completed += 1
                except ProcessingError as exc:
                    self.store.finish_translation(result_id, block["id"], generation, error=exc.code)
                    if exc.code in {"source_changed", "model_result_unknown", "model_request_rejected", "model_rate_limited", "processing_cancelled", "processing_budget_exceeded", "processing_time_budget"}:
                        raise
                    failures += 1
                checkpoint = json.loads(self.jobs.get(job_id)["checkpoint_json"])
                self.jobs.checkpoint(job_id, "translating", checkpoint, completed=completed,
                                     total=estimate["selectedBlocks"])
            after = blocks[-1]["order"]
        status = "partial" if failures else "completed"
        with self.store.connection(write=True) as db:
            readable=db.execute("SELECT count(*) FROM processing_block_translations WHERE result_id=? AND active_revision IS NOT NULL",(result_id,)).fetchone()[0]
            result_status="completed" if readable==self.store.result(parse_id)["block_count"] and not failures else "partial"
            db.execute("UPDATE processing_results SET status=?,updated_at=? WHERE id=? AND owner_id=?", (result_status, now(), result_id, self.store.owner))
        self.jobs.finish(job_id, status, error="some_blocks_failed" if failures else None)
