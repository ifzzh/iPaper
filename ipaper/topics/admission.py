"""Explicit logical topic IDs are never used as storage folder identifiers."""

import json
from ipaper.database.connection import get_db
from ipaper.security.identity import current_user_id
from .store import TopicError, resolve, head


def validated_ids(value, *, db=None, owner=None):
    if value is None:
        return []
    if isinstance(value, str):
        if len(value) > 8192:
            raise TopicError("invalid_topic_selection", 413)
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            raise TopicError("invalid_topic_selection") from None
    if (
        not isinstance(value, list)
        or len(value) > 64
        or any(not isinstance(v, str) or len(v) > 100 for v in value)
    ):
        raise TopicError("invalid_topic_selection")
    db = db if db is not None else get_db()
    owner = owner or current_user_id()
    return list(dict.fromkeys(resolve(db, owner, item)["id"] for item in value))


def apply_admission(db, owner, paper):
    selected = validated_ids(paper.get("_topic_ids", []), db=db, owner=owner)
    from .store import nodes
    from ipaper.keywords.common import label
    from ipaper.metadata.model import stamp
    import uuid

    paths = paper.get("_topic_paths", [])
    if not isinstance(paths, list) or len(paths) > 32:
        raise TopicError("invalid_topic_paths")
    for path in paths:
        if not isinstance(path, list) or len(path) > 12:
            raise TopicError("invalid_topic_paths")
        parent = None
        for raw in path:
            name = label(raw)
            if parent is None and name.casefold() in {
                "root",
                "others",
                "uncategorized",
                "未分类",
            }:
                continue
            tree = nodes(db, owner)
            existing = next(
                (
                    n
                    for n in tree.values()
                    if n["name"].casefold() == name.casefold()
                    and n["parent_id"] == parent
                ),
                None,
            )
            if existing:
                if existing["status"] != "active":
                    break
                parent = existing["id"]
            else:
                if len(tree) >= 1000:
                    raise TopicError("topic_limit", 409)
                tid, now = str(uuid.uuid4()), stamp()
                db.execute(
                    "INSERT INTO topic_nodes(id,owner_id,name,parent_id,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (tid, owner, name, parent, now, now),
                )
                parent = tid
        if parent:
            selected.append(parent)
    if not selected:
        return
    head(db, owner, paper["id"])
    for topic in selected:
        db.execute(
            "INSERT INTO topic_links VALUES (?,?,?,1) ON CONFLICT(owner_id,paper_id,topic_id) DO UPDATE SET manual=1",
            (owner, paper["id"], topic),
        )
    db.execute(
        "UPDATE topic_papers SET revision=revision+1 WHERE owner_id=? AND paper_id=?",
        (owner, paper["id"]),
    )
