"""One authoritative membership definition for pages, counts and batch selection."""

import json
import time
import uuid
from pathlib import PurePath
from ipaper.metadata.store import in_library, unpack
from ipaper.security.paths import category_storage_id
from .common import KeywordError, encoded


def normalize(query):
    if not isinstance(query, dict) or set(query) - {
        "scope",
        "query",
        "categoryIds",
        "tagIds",
        "tagMode",
        "order",
    }:
        raise KeywordError("invalid_library_query")
    out = {
        "scope": query.get("scope", "all"),
        "query": query.get("query", ""),
        "categoryIds": query.get("categoryIds", []),
        "tagIds": query.get("tagIds", []),
        "tagMode": query.get("tagMode", "all"),
        "order": query.get("order", "recent"),
    }
    if (
        out["scope"] not in {"all", "favorites", "reading", "categories"}
        or out["tagMode"] not in {"all", "any"}
        or out["order"] not in {"recent", "title", "year"}
    ):
        raise KeywordError("invalid_library_query")
    if not isinstance(out["query"], str) or len(out["query"]) > 500:
        raise KeywordError("invalid_library_query")
    for key, maximum in [("categoryIds", 1000), ("tagIds", 64)]:
        if (
            not isinstance(out[key], list)
            or len(out[key]) > maximum
            or any(not isinstance(x, str) or len(x) > 100 for x in out[key])
        ):
            raise KeywordError("invalid_library_query")
        out[key] = list(dict.fromkeys(out[key]))
    return out


def members(db, owner, query, resolve=None):
    q = normalize(query)
    reading = {
        r[0]
        for r in db.execute(
            "SELECT paper_id FROM reading_list WHERE owner_id=?", (owner,)
        )
    }
    tags = set(q["tagIds"])
    if tags:
        if not resolve:
            from .store import resolve_tag

            resolve = lambda tag: resolve_tag(db, owner, tag)
        tags = {resolve(t)["id"] for t in tags}
    links = {}
    if tags:
        for row in db.execute(
            "SELECT paper_id,tag_id FROM keyword_links WHERE owner_id=?", (owner,)
        ):
            links.setdefault(row[0], set()).add(row[1])
    directories = {category_storage_id(c) for c in q["categoryIds"]}
    text = q["query"].strip().casefold()
    result = []
    # Streaming the authoritative rows avoids the historical FTS top-100 cap and
    # keeps Unicode literal matching identical for UI and every batch consumer.
    for row in db.execute("SELECT * FROM papers WHERE owner_id=?", (owner,)):
        p = unpack(row)
        if not in_library(p):
            continue
        if q["scope"] == "favorites" and not p.get("starred"):
            continue
        if q["scope"] == "reading" and p["id"] not in reading:
            continue
        if (
            q["scope"] == "categories"
            and p.get("category_id") not in q["categoryIds"]
            and PurePath(p.get("file_path", "")).parent.name not in directories
        ):
            continue
        if (
            text
            and text
            not in " ".join(
                str(p.get(k) or "")
                for k in ("title", "authors", "abstract", "year", "journal", "doi")
            ).casefold()
        ):
            continue
        present = links.get(p["id"], set())
        if tags and not (tags <= present if q["tagMode"] == "all" else tags & present):
            continue
        result.append(p)
    if q["order"] == "title":
        result.sort(key=lambda p: (str(p.get("title") or "").casefold(), p["id"]))
    elif q["order"] == "year":
        result.sort(key=lambda p: (str(p.get("year") or ""), p["id"]), reverse=True)
    else:
        result.sort(
            key=lambda p: (str(p.get("upload_date") or ""), p["id"]), reverse=True
        )
    return result


def selection(db, owner, query):
    rows = members(db, owner, query)
    if len(rows) > 5000:
        raise KeywordError("keyword_selection_limit", 413)
    ids = [r["id"] for r in rows]
    sid = str(uuid.uuid4())
    db.execute(
        "DELETE FROM library_selections WHERE owner_id=? AND created_at<?",
        (owner, time.time() - 86400),
    )
    if (
        db.execute(
            "SELECT count(*) FROM library_selections WHERE owner_id=?", (owner,)
        ).fetchone()[0]
        >= 100
    ):
        raise KeywordError("keyword_selection_limit", 429)
    db.execute(
        "INSERT INTO library_selections VALUES (?,?,?,?,?)",
        (sid, owner, encoded(ids), encoded(normalize(query)), time.time()),
    )
    return {"id": sid, "count": len(ids), "paperIds": ids}


def selected_ids(db, owner, data):
    if data.get("selectionId"):
        r = db.execute(
            "SELECT * FROM library_selections WHERE id=? AND owner_id=?",
            (data["selectionId"], owner),
        ).fetchone()
        if not r or r["created_at"] < time.time() - 86400:
            raise KeywordError("library_selection_expired", 409)
        ids = json.loads(r["paper_ids_json"])
    elif "selection" in data:
        ids = [p["id"] for p in members(db, owner, data["selection"])]
    else:
        ids = data.get("paperIds")
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) > 5000
        or any(not isinstance(x, str) or len(x) > 100 for x in ids)
    ):
        raise KeywordError("invalid_keyword_selection")
    existing = {
        r[0] for r in db.execute("SELECT id FROM papers WHERE owner_id=?", (owner,))
    }
    if not data.get("selectionId") and set(ids) - existing:
        raise KeywordError("paper_not_found", 404)
    return list(dict.fromkeys(ids))
