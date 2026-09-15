"""Bounded, read-only access to existing original content; no snapshots or assets."""

import hashlib
import json
import time


def existing_original(understanding, paper_id, sha, limit=200000):
    store = understanding.store
    choices = []
    for row in store.results(paper_id):
        config = json.loads(row["config_json"])
        if (
            row["kind"] != "structure"
            or row["status"] not in {"completed", "partial"}
            or config.get("internalPart")
            or config.get("normalizer") != "mineru-content-list-v1/1"
        ):
            continue
        doc = store.document(row["document_id"])
        if doc["sha256"] == sha:
            choices.append((row, understanding._coverage(row, doc)))
    choices.sort(key=lambda item: not item[1]["complete"])
    if choices:
        row, coverage = choices[0]
        units, after, consumed, truncated = [], -1, 0, False
        deadline = time.monotonic() + 10
        while consumed < limit and time.monotonic() < deadline:
            blocks = store.blocks(row["id"], after=after, limit=100)
            if not blocks:
                break
            for block in blocks:
                text = block.get("text") or ""
                if block.get("table"):
                    text += "\n" + "\n".join(
                        " | ".join(c["text"] for c in cells) for cells in block["table"]
                    )
                if block.get("caption"):
                    text += "\n" + block["caption"]
                remaining = limit - consumed
                units.append({"text": text[:remaining]})
                consumed += min(len(text), remaining)
                after = block["order"]
                if (
                    len(text) > remaining
                    or consumed >= limit
                    or time.monotonic() >= deadline
                ):
                    truncated = True
                    break
            if truncated:
                break
        if time.monotonic() >= deadline:
            truncated = True
        return {
            "units": units,
            "parseId": row["id"],
            "coverage": {
                **coverage,
                "keywordCharacters": consumed,
                "keywordTruncated": truncated,
            },
        }
    # Legacy reader enforces its existing 64 MiB ceiling and confined owner paths.
    # No image inspection, image copy or new content snapshot is performed.
    path, text = understanding.legacy(paper_id)
    return {
        "units": [{"text": text[:limit]}] if path else [],
        "legacyHash": hashlib.sha256(text.encode()).hexdigest() if path else None,
        "coverage": {
            "complete": False,
            "reason": "legacy_coverage_unknown" if path else "no_content",
            "keywordCharacters": min(len(text), limit),
            "keywordTruncated": len(text) > limit,
        },
    }
