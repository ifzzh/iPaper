"""Owner/version-validated references. Model text never supplies a file path."""
from __future__ import annotations
import json
import re
import time
from .common import ProcessingError, encoded, identifier, now
from .pipeline import file_digest


class Sources:
    def __init__(self, store, current_file):
        self.store, self.current_file = store, current_file

    def create_block_selection(self, paper_id, result_id, block_id, *, start, end, revision_id=None, field="text"):
        result = self.store.result(result_id)
        if result["paper_id"] != paper_id:
            raise ProcessingError("source_not_found", 404)
        block = self.store.block(result_id, block_id)
        cell = re.fullmatch(r"cell:(\d{1,3}):(\d{1,3})",field) if isinstance(field,str) else None
        if not isinstance(field,str) or field not in {"text", "caption"} and not cell:
            raise ProcessingError("invalid_selection_field")
        content = block
        if revision_id:
            translation = self.store.translation(result_id, block_id)
            if not translation or translation.get("revision") != revision_id:
                raise ProcessingError("translation_revision_changed", 409)
            content = translation["content"]
        if cell:
            try:
                text = content["table"][int(cell[1])][int(cell[2])]["text"]
            except (TypeError,IndexError,KeyError):
                raise ProcessingError("invalid_selection_field") from None
        else:
            text = content.get(field) or ""
        # JavaScript string offsets are UTF-16 code units. Use that convention
        # explicitly, rejecting a selection that bisects a surrogate pair.
        if type(start) is not int or type(end) is not int or not 0 <= start <= end or end - start > 6000:
            raise ProcessingError("invalid_selection_range")
        utf16 = text.encode("utf-16-le")
        if end * 2 > len(utf16):
            raise ProcessingError("invalid_selection_range")
        try:
            selected = utf16[start*2:end*2].decode("utf-16-le")
        except UnicodeError:
            raise ProcessingError("invalid_selection_range") from None
        source_id = identifier()
        value = {"text": selected, "field": field, "start": start, "end": end,
                 "offsetConvention": "utf16", "source": block["source"]}
        with self.store.connection(write=True) as db:
            self.store.paper_exists(db, paper_id)
            db.execute("INSERT INTO processing_sources VALUES (?,?,?,?,?,?,?,?,?)",
                       (source_id, self.store.owner, paper_id, result["document_id"], result_id, block_id, revision_id, encoded(value), now()))
        return self.resolve(source_id)

    def create_pdf_selection(self, pipeline, paper_id, *, page, text, document_id=None, translated=False):
        from ipaper.security.paths import paper_asset_paths, ensure_confined
        if not isinstance(text, str) or not text.strip() or not 0 < len(text) <= 6000 or type(page) is not int or page < 1:
            raise ProcessingError("invalid_selection_range")
        source = pipeline.paper_file(paper_id)
        kind = "original"
        if document_id:
            doc = self.store.document(document_id)
            if doc["paper_id"] != paper_id:
                raise ProcessingError("source_not_found", 404)
            source = ensure_confined(self.store.papers_root, doc["file_ref"], must_exist=True, require_file=True)
            kind = doc["kind"]
        elif translated:
            assets = paper_asset_paths(self.store.papers_root, source)
            source = assets.chinese_dual if assets.chinese_dual.exists() else assets.chinese_mono
            kind = "babeldoc_dual" if source == assets.chinese_dual else "babeldoc_mono"
        worker_id = identifier()
        try:
            with source.open("rb") as handle:
                pipeline.document.stage_page_text(worker_id, handle, page)
            pipeline.document.create(worker_id, "pdf_page_text")
            outcome = pipeline.document.wait(worker_id, timeout=90)
            if outcome["status"] != "completed":
                raise ProcessingError("pdf_selection_unverified", 422)
            data = pipeline.document.result_json(worker_id)
            normalize = lambda value: re.sub(r"\s+", "", value)
            if normalize(text) not in normalize(data["text"]):
                raise ProcessingError("pdf_selection_unverified", 409)
            if file_digest(source) != data["sha256"]:
                raise ProcessingError("source_expired", 409)
            if document_id and doc["sha256"] != data["sha256"]:
                raise ProcessingError("source_expired", 409)
            doc_id = self.store.register_document(paper_id, kind=kind, sha256=data["sha256"], size=source.stat().st_size,
                    geometry=data["pages"], file_ref=str(source.relative_to(self.store.papers_root)))
            source_id = identifier()
            selection = {"text": text, "source": {"page": page, "precision": "page", "regions": []}, "verifiedText": True}
            with self.store.connection(write=True) as db:
                db.execute("INSERT INTO processing_sources VALUES (?,?,?,?,NULL,NULL,NULL,?,?)",(source_id,self.store.owner,paper_id,doc_id,encoded(selection),now()))
            return self.resolve(source_id)
        finally:
            pipeline.document.cleanup(worker_id)

    def resolve(self, source_id):
        with self.store.connection() as db:
            row = self.store._owned(db, "processing_sources", source_id)
            self.store.paper_exists(db, row["paper_id"])
        doc = self.store.document(row["document_id"])
        selection = json.loads(row["selection_json"])
        source = selection["source"]
        try:
            if doc["kind"] == "original":
                path = self.current_file(row["paper_id"])
            else:
                from ipaper.security.paths import ensure_confined
                path = ensure_confined(self.store.papers_root, doc["file_ref"], must_exist=True, require_file=True)
            current_hash = file_digest(path)
        except (OSError, ValueError):
            current_hash = None
        stale = current_hash != doc["sha256"]
        return {"id": row["id"], "paperId": row["paper_id"], "documentId": doc["id"],
                "documentHash": doc["sha256"], "resultId": row["result_id"], "blockId": row["block_id"],
                "revisionId": row["revision_id"], "text": selection["text"], "stale": stale,
                "page": source.get("page"), "precision": "none" if stale else source["precision"],
                "regions": [] if stale else source["regions"],
                "document": "original" if doc["kind"] == "original" else "translated", "canNavigate": not stale and source.get("precision") in {"page", "region"}}

    def context(self, paper_id, source_ids):
        if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 8 or any(not isinstance(v,str) for v in source_ids) or len(set(source_ids)) != len(source_ids):
            raise ProcessingError("invalid_sources")
        texts, mapping = [], {}
        count = 0
        for number, source_id in enumerate(source_ids, 1):
            source = self.resolve(source_id)
            if source["paperId"] != paper_id:
                raise ProcessingError("source_not_found", 404)
            if source["stale"]:
                raise ProcessingError("source_expired", 409)
            if not source["text"].strip():
                raise ProcessingError("empty_source_context")
            count += len(source["text"])
            if count > 12000:
                raise ProcessingError("source_context_limit", 413)
            label = f"S{number}"
            texts.append(f"[{label}] 第 {source['page'] or '未知'} 页\n{source['text']}")
            mapping[label] = source["id"]
        # No search index: include at most the immediately adjacent blocks of
        # the selected version. Every excerpt admitted gets its own source ID.
        resolved=[self.resolve(source_id) for source_id in source_ids]
        seen={(s["resultId"],s["blockId"]) for s in resolved}
        for source_id in source_ids:
            source=self.resolve(source_id)
            if not source["resultId"] or not source["blockId"]:
                continue
            current=self.store.block(source["resultId"],source["blockId"])
            for neighbor in self.store.blocks(source["resultId"],after=max(-1,current["order"]-2),limit=3):
                if len(mapping) >= 8:
                    break
                pair=(source["resultId"],neighbor["id"])
                text=neighbor.get("text","")
                if neighbor["id"]==current["id"] or pair in seen or not text or count+len(text)>12000 or len(text.encode("utf-16-le"))>12000:
                    continue
                seen.add(pair)
                reference=self.create_block_selection(paper_id,source["resultId"],neighbor["id"],start=0,end=len(text.encode("utf-16-le"))//2)
                label=f"S{len(mapping)+1}"
                texts.append(f"[{label}] 相邻原文，第 {reference['page'] or '未知'} 页\n{text}")
                mapping[label]=reference["id"]
                count+=len(text)
        return "\n\n".join(texts), mapping

    def save_answer(self,paper_id,session_id,content,mapping):
        """Commit the assistant text and its trusted reference map together."""
        with self.store.connection(write=True) as db:
            self.store.paper_exists(db,paper_id)
            row=db.execute("SELECT history FROM chats WHERE owner_id=? AND paper_id=? AND session_id=?",(self.store.owner,paper_id,session_id)).fetchone()
            if not row:
                raise ProcessingError("session_not_found",404)
            stamp=time.time()
            history=json.loads(row["history"] or "[]")
            history.append({"role":"assistant","content":content,"timestamp":stamp})
            db.execute("UPDATE chats SET history=?,updated_at=? WHERE session_id=? AND owner_id=?",(encoded(history),stamp,session_id,self.store.owner))
            db.execute("INSERT INTO processing_chat_sources VALUES (?,?,?,?)",(self.store.owner,session_id,str(stamp),encoded(mapping)))

    def save_chat_mapping(self, session_id, message_key, mapping):
        with self.store.connection(write=True) as db:
            if not db.execute("SELECT 1 FROM chats WHERE session_id=? AND owner_id=?", (session_id, self.store.owner)).fetchone():
                raise ProcessingError("session_not_found", 404)
            db.execute("INSERT OR REPLACE INTO processing_chat_sources VALUES (?,?,?,?)", (self.store.owner, session_id, str(message_key), encoded(mapping)))

    def attach_history(self, session_id, messages):
        with self.store.connection() as db:
            rows = db.execute("SELECT message_key,sources_json FROM processing_chat_sources WHERE owner_id=? AND session_id=?", (self.store.owner, session_id)).fetchall()
        lookup = {row["message_key"]: json.loads(row["sources_json"]) for row in rows}
        for message in messages:
            mapping = lookup.get(str(message.get("timestamp")))
            if mapping:
                labels = set(re.findall(r"\[(S[1-9][0-9]{0,2})\]", message["content"]))
                message["sources"] = [{"label": label, "sourceId": mapping[label]} for label in sorted(labels & mapping.keys())]
        with self.store.connection() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='understanding_chat_turns'").fetchone():
                turns=db.execute("SELECT message_key,context_json,status FROM understanding_chat_turns WHERE owner_id=? AND session_id=? AND message_key IS NOT NULL",(self.store.owner,session_id)).fetchall()
                scopes={row["message_key"]:json.loads(row["context_json"]) for row in turns}
                for message in messages:
                    if str(message.get("timestamp")) in scopes:message["scope"]=scopes[str(message["timestamp"])]
        return messages
