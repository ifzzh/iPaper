"""Read legacy storage identities into logical relationships, without file I/O."""

from pathlib import PurePath
import uuid
from ipaper.metadata.model import stamp
from ipaper.metadata.store import in_library, unpack
from ipaper.security.paths import category_storage_id
from .store import nodes, ancestors, head


def migrate(db, owner):
    """Caller owns the transaction; repeated/re-upgrade runs preserve corrections."""
    legacy = {
        r["id"]: dict(r)
        for r in db.execute(
            "SELECT * FROM categories WHERE owner_id=? AND id<>'root'", (owner,)
        )
        if r["name"] not in {"未分类", "Uncategorized"}
    }
    mapping = {
        r[0]: r[1]
        for r in db.execute(
            "SELECT category_id,topic_id FROM topic_legacy_map WHERE owner_id=?",
            (owner,),
        )
    }
    pending = set(legacy) - set(mapping)
    while pending:
        ready = sorted(
            key for key in pending if legacy[key]["parent_id"] not in pending
        )
        if not ready:
            # Corrupt historic cycles are not imported as a cyclic topic tree.
            # A later explicit repair of legacy data can retry the remaining map.
            break
        for key in ready:
            old, tid, now = legacy[key], str(uuid.uuid4()), stamp()
            name = old.get("display_name") or old["name"]
            db.execute(
                "INSERT INTO topic_nodes(id,owner_id,name,parent_id,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (tid, owner, name, mapping.get(old["parent_id"]), now, now),
            )
            db.execute("INSERT INTO topic_legacy_map VALUES (?,?,?)", (owner, key, tid))
            mapping[key] = tid
            pending.remove(key)
    tree = nodes(db, owner)
    directories = {category_storage_id(key): key for key in mapping}
    imported = 0
    for row in db.execute("SELECT * FROM papers WHERE owner_id=?", (owner,)):
        paper = unpack(row)
        if not in_library(paper):
            continue
        category = paper.get("category_id")
        if category not in mapping:
            category = directories.get(
                PurePath(paper.get("file_path") or "").parent.name
            )
        previous = db.execute(
            "SELECT category_id FROM topic_legacy_observed WHERE owner_id=? AND paper_id=?",
            (owner, paper["id"]),
        ).fetchone()
        if previous and previous[0] == category:
            continue
        topic = mapping.get(category)
        if topic and tree.get(topic, {}).get("status") == "active":
            state = head(db, owner, paper["id"])
            excluded = {
                r[0]
                for r in db.execute(
                    "SELECT topic_id FROM topic_exclusions WHERE owner_id=? AND paper_id=?",
                    (owner, paper["id"]),
                )
            }
            if not state["auto_blocked"] and not ancestors(tree, topic) & excluded:
                db.execute(
                    "INSERT OR IGNORE INTO topic_links VALUES (?,?,?,1)",
                    (owner, paper["id"], topic),
                )
                imported += 1
        # Do not remove relationships when old physical folders change: logical
        # topics can span folders, and newer user corrections remain authoritative.
        db.execute(
            "INSERT INTO topic_legacy_observed VALUES (?,?,?) ON CONFLICT(owner_id,paper_id) DO UPDATE SET category_id=excluded.category_id",
            (owner, paper["id"], category),
        )
    return {
        "mapped": len(mapping),
        "imported": imported,
        "invalidHierarchy": len(pending),
    }
