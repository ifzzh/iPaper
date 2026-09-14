"""Load a hash-checked, one-paper read-only acceptance bundle into a test user.

No account rows, secret envelopes, production DB or remote requests are used.
The bundle is private and deliberately never shipped as a public fixture.
"""

import json
import shutil
from pathlib import Path
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.security.identity import Identity, run_as_identity
from ipaper.security.paths import paper_path, paper_asset_paths, safe_join
from ipaper.processing.pipeline import file_digest
from scripts.promote_processing_acceptance import insert


def seed_reading_sample(application, root, user, sample):
    import app as application_module
    from ipaper.core.base_paper import Paper

    root, sample = Path(root), Path(sample)
    bundle = json.loads((sample / "sample.json").read_text())
    if bundle["format"] != 1:
        raise ValueError("invalid_sample")
    for entry in bundle["files"]:
        path = safe_join(sample, entry["path"], must_exist=True, require_file=True)
        if path.stat().st_size != entry["size"] or file_digest(path) != entry["sha256"]:
            raise ValueError("sample_hash_mismatch")

    def seed():
        pid = bundle["paper"]["id"]
        target = paper_path(root / "papers", "root", pid + ".pdf", create_parent=True)
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
        application_module.paper_store.upsert(
            Paper.from_dict(data), category_id="root", category_path=["Root"]
        )
        store = application.extensions["processing"].pipeline(user["id"]).store
        with store.connection(write=True) as db:
            for original in bundle["documents"]:
                insert(
                    db,
                    "processing_documents",
                    {
                        **original,
                        "owner_id": user["id"],
                        "file_ref": str(target.relative_to(root / "papers")),
                    },
                )
            for original in bundle["results"]:
                insert(db, "processing_results", {**original, "owner_id": user["id"]})
            for row in bundle["blocks"]:
                insert(db, "processing_blocks", row)
            for table, rows in bundle.get("readingTables", {}).items():
                if table not in {
                    "processing_block_translations",
                    "processing_translation_revisions",
                    "understanding_artifacts",
                    "understanding_heads",
                    "understanding_evidence",
                }:
                    raise ValueError("invalid_sample_table")
                for original in rows:
                    row = dict(original)
                    if "owner_id" in row:
                        row["owner_id"] = user["id"]
                    insert(db, table, row)
            copied_roots = set()
            for entry in bundle["files"]:
                parts = Path(entry["path"]).parts
                if parts[0] != "artifacts":
                    continue
                directory = store.artifact_directory(
                    parts[1], create=parts[1] not in copied_roots
                )
                copied_roots.add(parts[1])
                destination = safe_join(directory, *parts[2:])
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(
                    safe_join(
                        sample, entry["path"], must_exist=True, require_file=True
                    ),
                    destination,
                )
                destination.chmod(0o600)
        return {
            "paper": pid,
            "blocks": bundle["blockCount"],
            "translationBlocks": sum(
                bool(r["active_revision"])
                for r in bundle.get("readingTables", {}).get(
                    "processing_block_translations", []
                )
            ),
            "paidRequests": 0,
        }

    return run_as_identity(Identity(user["id"], user["username"], user["role"]), seed)
