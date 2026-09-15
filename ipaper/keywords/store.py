import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from ipaper.metadata.store import MetadataStore, in_library, unpack
from .common import KeywordError, encoded, fingerprint, label, name_key, stamp, TERMINAL


TABLE_KEYS = {
    "keyword_tags": ("id",),
    "keyword_names": ("owner_id", "name_key"),
    "keyword_links": ("owner_id", "paper_id", "tag_id"),
    "keyword_exclusions": ("owner_id", "paper_id", "tag_id"),
    "keyword_papers": ("owner_id", "paper_id"),
}


def exists(db):
    return bool(
        db.execute("SELECT 1 FROM sqlite_master WHERE name='keyword_tags'").fetchone()
    )


def queue_change(db, owner, paper_id):
    if exists(db):
        db.execute(
            "INSERT INTO keyword_pending VALUES (?,?,?) ON CONFLICT(owner_id,paper_id) DO UPDATE SET updated_at=excluded.updated_at",
            (owner, paper_id, stamp()),
        )


def observed_signature(db, owner, paper):
    """Only adopted input, never mutable reading counters or candidate records."""
    head = db.execute(
        "SELECT fields_json FROM bibliography WHERE owner_id=? AND paper_id=?",
        (owner, paper["id"]),
    ).fetchone()
    from ipaper.metadata.model import legacy_fields

    fields = json.loads(head[0]) if head else legacy_fields(paper)
    return fingerprint(
        [fields, paper.get("keywords"), paper.get("file_path"), paper.get("is_daily")]
    )


def resolve_tag(db, owner, tag_id, *, deleted=False):
    seen = set()
    while tag_id and tag_id not in seen:
        seen.add(tag_id)
        r = db.execute(
            "SELECT * FROM keyword_tags WHERE owner_id=? AND id=?", (owner, tag_id)
        ).fetchone()
        if not r:
            break
        if r["status"] == "merged":
            tag_id = r["merged_into"]
            continue
        if r["status"] == "deleted" and not deleted:
            raise KeywordError("tag_deleted", 409)
        return dict(r)
    raise KeywordError("tag_not_found", 404)


def ensure_paper(db, owner, paper_id):
    row = db.execute(
        "SELECT * FROM papers WHERE owner_id=? AND id=?", (owner, paper_id)
    ).fetchone()
    if not row or not in_library(unpack(row)):
        raise KeywordError("paper_not_found", 404)
    db.execute(
        "INSERT OR IGNORE INTO keyword_papers(owner_id,paper_id,updated_at) VALUES (?,?,?)",
        (owner, paper_id, stamp()),
    )
    return dict(
        db.execute(
            "SELECT * FROM keyword_papers WHERE owner_id=? AND paper_id=?",
            (owner, paper_id),
        ).fetchone()
    )


def find_name(db, owner, value):
    row = db.execute(
        "SELECT * FROM keyword_names WHERE owner_id=? AND name_key=?",
        (owner, name_key(value)),
    ).fetchone()
    if not row:
        return None
    if row["blocked"]:
        raise KeywordError("tag_name_retired", 409)
    return resolve_tag(db, owner, row["tag_id"])


def create_tag(db, owner, value):
    value = label(value)
    old = find_name(db, owner, value)
    if old:
        return old
    if (
        db.execute(
            "SELECT count(*) FROM keyword_tags WHERE owner_id=? AND status='active'",
            (owner,),
        ).fetchone()[0]
        >= 5000
    ):
        raise KeywordError("tag_catalog_limit", 409)
    tid = str(uuid.uuid4())
    now = stamp()
    db.execute(
        "INSERT INTO keyword_tags VALUES (?,?,?,?,?,?,?,?)",
        (tid, owner, value, "active", None, 1, now, now),
    )
    db.execute(
        "INSERT INTO keyword_names VALUES (?,?,?,?,0)",
        (owner, name_key(value), value, tid),
    )
    from .extract import CONCEPTS, ALIASES

    canonical = ALIASES.get(name_key(value))
    if canonical:
        for concept, variants in CONCEPTS:
            if concept == canonical:
                for alias in [concept, *variants]:
                    # Existing human alias decisions and tombstones win.
                    db.execute(
                        "INSERT OR IGNORE INTO keyword_names VALUES (?,?,?,?,0)",
                        (owner, name_key(alias), alias, tid),
                    )
    return resolve_tag(db, owner, tid)


class KeywordStore(MetadataStore):
    def paper(self, db, paper_id):
        row = db.execute(
            "SELECT * FROM papers WHERE owner_id=? AND id=?", (self.owner, paper_id)
        ).fetchone()
        if not row or not in_library(unpack(row)):
            raise KeywordError("paper_not_found", 404)
        return unpack(row)

    def settings(self, value=None):
        with self.connection(value is not None) as db:
            if value is not None:
                if (
                    not isinstance(value, dict)
                    or set(value) != {"automatic"}
                    or type(value["automatic"]) is not bool
                ):
                    raise KeywordError("invalid_keyword_settings")
                db.execute(
                    "INSERT INTO user_settings_v2 VALUES (?,'keywords_v1',?) ON CONFLICT(owner_id,key) DO UPDATE SET value=excluded.value",
                    (self.owner, encoded(value)),
                )
            r = db.execute(
                "SELECT value FROM user_settings_v2 WHERE owner_id=? AND key='keywords_v1'",
                (self.owner,),
            ).fetchone()
        return json.loads(r[0]) if r else {"automatic": True}

    def redirects(self):
        with self.connection() as db:
            rows = db.execute(
                "SELECT id FROM keyword_tags WHERE owner_id=? AND status!='active'",
                (self.owner,),
            ).fetchall()
            result = {}
            for row in rows:
                target = resolve_tag(db, self.owner, row["id"], deleted=True)
                result[row["id"]] = (
                    target["id"] if target["status"] == "active" else None
                )
            return result

    def tags(self, query="", deleted=False):
        from collections import defaultdict, Counter
        from .query import members

        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM keyword_tags WHERE owner_id=? AND status=? ORDER BY name,id",
                (self.owner, "deleted" if deleted else "active"),
            ).fetchall()
            aliases = defaultdict(set)
            resolved = {}
            for name in db.execute(
                "SELECT * FROM keyword_names WHERE owner_id=? AND blocked=0",
                (self.owner,),
            ):
                tid = name["tag_id"]
                if tid not in resolved:
                    resolved[tid] = resolve_tag(db, self.owner, tid, deleted=True)["id"]
                aliases[resolved[tid]].add(name["label"])
            admitted = {p["id"] for p in members(db, self.owner, {})}
            counts = Counter(
                r[0]
                for r in db.execute(
                    "SELECT tag_id,paper_id FROM keyword_links WHERE owner_id=?",
                    (self.owner,),
                )
                if r[1] in admitted
            )
            return [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "revision": r["revision"],
                    "status": r["status"],
                    "aliases": sorted(aliases[r["id"]] - {r["name"]}),
                    "count": counts[r["id"]],
                }
                for r in rows
                if query.casefold()
                in " ".join([r["name"], *aliases[r["id"]]]).casefold()
            ]

    def get(self, paper_id):
        with self.connection() as db:
            self.paper(db, paper_id)
            row = db.execute(
                "SELECT * FROM keyword_papers WHERE owner_id=? AND paper_id=?",
                (self.owner, paper_id),
            ).fetchone()
            tags = [
                {"id": r["id"], "name": r["name"], "manual": bool(r["manual"])}
                for r in db.execute(
                    "SELECT t.id,t.name,l.manual FROM keyword_links l JOIN keyword_tags t ON t.id=l.tag_id AND t.owner_id=l.owner_id WHERE l.owner_id=? AND l.paper_id=? AND t.status='active' ORDER BY l.manual DESC,t.name,t.id",
                    (self.owner, paper_id),
                )
            ]
            task = db.execute(
                "SELECT id,batch_id,status,error FROM keyword_items WHERE owner_id=? AND paper_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (self.owner, paper_id),
            ).fetchone()
            exclusions = db.execute(
                "SELECT count(*) FROM keyword_exclusions WHERE owner_id=? AND paper_id=?",
                (self.owner, paper_id),
            ).fetchone()[0]
        return {
            "paperId": paper_id,
            "revision": row["revision"] if row else 1,
            "tags": tags,
            "status": row["status"] if row else "pending",
            "excludedCount": exclusions,
            "task": dict(task) if task else None,
        }

    def _snapshot(self, db, *, paper_ids=None, tag_ids=None):
        result = {}
        size = 0
        for table, keys in TABLE_KEYS.items():
            where = "owner_id=?"
            args = [self.owner]
            if table in {"keyword_links", "keyword_exclusions", "keyword_papers"}:
                if paper_ids is not None:
                    where += " AND paper_id IN (SELECT value FROM json_each(?))"
                    args.append(encoded(paper_ids))
                elif tag_ids is not None:
                    if table == "keyword_papers":
                        result[table] = {}
                        continue
                    where += " AND tag_id IN (SELECT value FROM json_each(?))"
                    args.append(encoded(tag_ids))
            rows = {}
            for r in db.execute(f"SELECT * FROM {table} WHERE {where}", args):
                item = dict(r)
                size += len(encoded(item).encode())
                if size > 32 * 1024**2:
                    raise KeywordError("tag_operation_limit", 413)
                rows[encoded([r[k] for k in keys])] = item
            result[table] = rows
        return result

    @contextmanager
    def operation(self, kind, *, paper_ids=None, tag_ids=None):
        with self.connection(True) as db:
            if tag_ids is not None:
                tag_ids = [
                    resolve_tag(db, self.owner, t, deleted=True)["id"]
                    for t in tag_ids
                    if t
                ]
            scope = {"paper_ids": paper_ids, "tag_ids": tag_ids}
            before = self._snapshot(db, **scope)
            receipt = {}
            yield db, receipt
            after = self._snapshot(db, **scope)
            changes = []
            for table in TABLE_KEYS:
                for key in sorted(set(before[table]) | set(after[table])):
                    a, b = before[table].get(key), after[table].get(key)
                    if a != b:
                        changes.append(
                            {
                                "table": table,
                                "key": json.loads(key),
                                "before": a,
                                "after": b,
                            }
                        )
            if changes:
                payload = encoded(changes)
                if len(payload.encode()) > 8 * 1024**2:
                    raise KeywordError("tag_operation_limit", 413)
                oid = str(uuid.uuid4())
                receipt["operationId"] = oid
                db.execute(
                    "INSERT INTO keyword_operations VALUES (?,?,?,?,0,?)",
                    (oid, self.owner, kind, payload, stamp()),
                )
            db.execute(
                "DELETE FROM keyword_operations WHERE owner_id=? AND created_at<?",
                (
                    self.owner,
                    (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
                ),
            )

    def _touch_paper(self, db, paper_id):
        db.execute(
            "UPDATE keyword_papers SET revision=revision+1,updated_at=? WHERE owner_id=? AND paper_id=?",
            (stamp(), self.owner, paper_id),
        )

    def edit_papers(self, paper_ids, action, *, name=None, tag_id=None, revisions=None):
        if action not in {"add", "remove", "clear", "confirm", "reset"}:
            raise KeywordError("invalid_tag_action")
        with self.operation("paper_" + action, paper_ids=paper_ids) as (db, receipt):
            if action == "add":
                tag = (
                    create_tag(db, self.owner, name)
                    if name is not None
                    else resolve_tag(db, self.owner, tag_id)
                )
            elif action in {"remove", "confirm"}:
                tag = resolve_tag(db, self.owner, tag_id)
            for pid in paper_ids:
                # Selections retain deleted IDs for reporting, never recreate them.
                if not db.execute(
                    "SELECT 1 FROM papers WHERE owner_id=? AND id=?", (self.owner, pid)
                ).fetchone():
                    continue
                head = ensure_paper(db, self.owner, pid)
                if revisions is not None and revisions.get(pid) != head["revision"]:
                    raise KeywordError("tag_revision_conflict", 409)
                if action == "add":
                    n = db.execute(
                        "SELECT count(*) FROM keyword_links WHERE owner_id=? AND paper_id=?",
                        (self.owner, pid),
                    ).fetchone()[0]
                    if (
                        n >= 128
                        and not db.execute(
                            "SELECT 1 FROM keyword_links WHERE owner_id=? AND paper_id=? AND tag_id=?",
                            (self.owner, pid, tag["id"]),
                        ).fetchone()
                    ):
                        raise KeywordError("paper_tag_limit", 409)
                    db.execute(
                        "DELETE FROM keyword_exclusions WHERE owner_id=? AND paper_id=? AND tag_id=?",
                        (self.owner, pid, tag["id"]),
                    )
                    db.execute(
                        "INSERT INTO keyword_links VALUES (?,?,?,1,'manual','{}') ON CONFLICT(owner_id,paper_id,tag_id) DO UPDATE SET manual=1,method='manual'",
                        (self.owner, pid, tag["id"]),
                    )
                elif action in {"remove", "clear"}:
                    ids = (
                        [
                            r[0]
                            for r in db.execute(
                                "SELECT tag_id FROM keyword_links WHERE owner_id=? AND paper_id=?",
                                (self.owner, pid),
                            )
                        ]
                        if action == "clear"
                        else [tag["id"]]
                    )
                    for tid in ids:
                        db.execute(
                            "INSERT OR IGNORE INTO keyword_exclusions VALUES (?,?,?)",
                            (self.owner, pid, tid),
                        )
                        db.execute(
                            "DELETE FROM keyword_links WHERE owner_id=? AND paper_id=? AND tag_id=?",
                            (self.owner, pid, tid),
                        )
                elif action == "confirm":
                    db.execute(
                        "UPDATE keyword_links SET manual=1 WHERE owner_id=? AND paper_id=? AND tag_id=?",
                        (self.owner, pid, tag["id"]),
                    )
                else:
                    db.execute(
                        "DELETE FROM keyword_exclusions WHERE owner_id=? AND paper_id=?",
                        (self.owner, pid),
                    )
                    db.execute(
                        "UPDATE keyword_papers SET input_key='' WHERE owner_id=? AND paper_id=?",
                        (self.owner, pid),
                    )
                self._touch_paper(db, pid)
        return receipt

    def edit_tag(self, tag_id, data):
        action = data.get("action")
        if action not in {"rename", "aliases", "merge", "delete", "restore"}:
            raise KeywordError("invalid_tag_action")
        with self.operation(
            action,
            tag_ids=(
                [tag_id, data.get("targetId")] if action in {"merge", "delete"} else []
            ),
        ) as (db, receipt):
            tag = resolve_tag(db, self.owner, tag_id, deleted=action == "restore")
            if (
                type(data.get("revision")) is not int
                or data["revision"] != tag["revision"]
            ):
                raise KeywordError("tag_revision_conflict", 409)
            if action == "rename":
                value = label(data.get("name"))
                other = find_name(db, self.owner, value)
                if other and other["id"] != tag["id"]:
                    raise KeywordError("tag_name_conflict", 409)
                db.execute(
                    "INSERT OR REPLACE INTO keyword_names VALUES (?,?,?,?,0)",
                    (self.owner, name_key(value), value, tag["id"]),
                )
                db.execute(
                    "UPDATE keyword_tags SET name=? WHERE id=?", (value, tag["id"])
                )
            elif action == "aliases":
                aliases = data.get("aliases")
                if not isinstance(aliases, list) or len(aliases) > 32:
                    raise KeywordError("invalid_tag_aliases")
                values = {name_key(v): label(v) for v in aliases}
                values[name_key(tag["name"])] = tag["name"]
                for key, value in values.items():
                    existing = db.execute(
                        "SELECT * FROM keyword_names WHERE owner_id=? AND name_key=?",
                        (self.owner, key),
                    ).fetchone()
                    if (
                        existing
                        and resolve_tag(
                            db, self.owner, existing["tag_id"], deleted=True
                        )["id"]
                        != tag["id"]
                    ):
                        raise KeywordError("tag_name_conflict", 409)
                    db.execute(
                        "INSERT OR REPLACE INTO keyword_names VALUES (?,?,?,?,0)",
                        (self.owner, key, value, tag["id"]),
                    )
                for r in db.execute(
                    "SELECT name_key,tag_id FROM keyword_names WHERE owner_id=?",
                    (self.owner,),
                ).fetchall():
                    if (
                        resolve_tag(db, self.owner, r[1], deleted=True)["id"]
                        == tag["id"]
                        and r[0] not in values
                    ):
                        db.execute(
                            "UPDATE keyword_names SET blocked=1 WHERE owner_id=? AND name_key=?",
                            (self.owner, r[0]),
                        )
            elif action == "merge":
                target = resolve_tag(db, self.owner, data.get("targetId"))
                if target["id"] == tag["id"]:
                    raise KeywordError("invalid_tag_action")
                if data.get("targetRevision") != target["revision"]:
                    raise KeywordError("tag_revision_conflict", 409)
                for r in db.execute(
                    "SELECT * FROM keyword_links WHERE owner_id=? AND tag_id=?",
                    (self.owner, tag["id"]),
                ).fetchall():
                    db.execute(
                        "INSERT INTO keyword_links VALUES (?,?,?,?,?,?) ON CONFLICT(owner_id,paper_id,tag_id) DO UPDATE SET manual=max(manual,excluded.manual)",
                        (
                            self.owner,
                            r["paper_id"],
                            target["id"],
                            r["manual"],
                            r["method"],
                            r["evidence_json"],
                        ),
                    )
                db.execute(
                    "INSERT OR IGNORE INTO keyword_exclusions SELECT owner_id,paper_id,? FROM keyword_exclusions WHERE owner_id=? AND tag_id=?",
                    (target["id"], self.owner, tag["id"]),
                )
                db.execute(
                    "DELETE FROM keyword_links WHERE owner_id=? AND tag_id=?",
                    (self.owner, tag["id"]),
                )
                db.execute(
                    "DELETE FROM keyword_links WHERE owner_id=? AND tag_id=? AND manual=0 AND paper_id IN (SELECT paper_id FROM keyword_exclusions WHERE owner_id=? AND tag_id=?)",
                    (self.owner, target["id"], self.owner, target["id"]),
                )
                db.execute(
                    "UPDATE keyword_tags SET status='merged',merged_into=? WHERE id=?",
                    (target["id"], tag["id"]),
                )
                db.execute(
                    "UPDATE keyword_tags SET revision=revision+1,updated_at=? WHERE id=?",
                    (stamp(), target["id"]),
                )
                receipt["targetId"] = target["id"]
            elif action == "delete":
                db.execute(
                    "UPDATE keyword_tags SET status='deleted' WHERE id=?", (tag["id"],)
                )
                db.execute(
                    "INSERT OR IGNORE INTO keyword_exclusions SELECT owner_id,paper_id,tag_id FROM keyword_links WHERE owner_id=? AND tag_id=?",
                    (self.owner, tag["id"]),
                )
                db.execute(
                    "DELETE FROM keyword_links WHERE owner_id=? AND tag_id=?",
                    (self.owner, tag["id"]),
                )
            else:
                db.execute(
                    "UPDATE keyword_tags SET status='active' WHERE id=?", (tag["id"],)
                )
            db.execute(
                "UPDATE keyword_tags SET revision=revision+1,updated_at=? WHERE id=?",
                (stamp(), tag["id"]),
            )
        return receipt

    def undo(self, operation_id):
        with self.connection(True) as db:
            row = db.execute(
                "SELECT * FROM keyword_operations WHERE owner_id=? AND id=?",
                (self.owner, operation_id),
            ).fetchone()
            if (
                not row
                or row["undone"]
                or row["created_at"]
                < (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            ):
                raise KeywordError("tag_undo_unavailable", 409)
            changes = json.loads(row["changes_json"])
            changed_keys = {(c["table"], encoded(c["key"])) for c in changes}
            for c in changes:
                table = c["table"]
                where = " AND ".join(k + "=?" for k in TABLE_KEYS[table])
                current = db.execute(
                    f"SELECT * FROM {table} WHERE {where}", c["key"]
                ).fetchone()
                if (dict(current) if current else None) != c["after"]:
                    raise KeywordError("tag_undo_conflict", 409)
                if table == "keyword_tags" and c["before"] is None:
                    # Undoing an initial add must not orphan a later association
                    # created for another paper while the tag itself stayed equal.
                    for related in (
                        "keyword_links",
                        "keyword_exclusions",
                        "keyword_names",
                    ):
                        for dependent in db.execute(
                            f"SELECT * FROM {related} WHERE owner_id=? AND tag_id=?",
                            (self.owner, c["after"]["id"]),
                        ):
                            key = encoded([dependent[k] for k in TABLE_KEYS[related]])
                            if (related, key) not in changed_keys:
                                raise KeywordError("tag_undo_conflict", 409)
                old = c["before"]
                if (
                    old
                    and old.get("paper_id")
                    and not db.execute(
                        "SELECT 1 FROM papers WHERE owner_id=? AND id=?",
                        (self.owner, old["paper_id"]),
                    ).fetchone()
                ):
                    raise KeywordError("tag_undo_conflict", 409)
            for c in reversed(changes):
                table = c["table"]
                where = " AND ".join(k + "=?" for k in TABLE_KEYS[table])
                db.execute(f"DELETE FROM {table} WHERE {where}", c["key"])
                if c["before"]:
                    r = c["before"]
                    db.execute(
                        f'INSERT INTO {table} ({",".join(r)}) VALUES ({",".join("?" for _ in r)})',
                        list(r.values()),
                    )
            db.execute(
                "UPDATE keyword_operations SET undone=1 WHERE id=?", (operation_id,)
            )
        return {"success": True}

    def operations(self):
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,kind,undone,created_at FROM keyword_operations WHERE owner_id=? AND created_at>=? ORDER BY created_at DESC LIMIT 50",
                    (
                        self.owner,
                        (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
                    ),
                )
            ]

    def create(
        self,
        paper_ids,
        method="local",
        *,
        force=False,
        inputs=None,
        input_scope="available",
        generation=None,
    ):
        if method not in {"local", "model"}:
            raise KeywordError("invalid_keyword_method")
        if not isinstance(paper_ids, list) or not paper_ids or len(paper_ids) > 5000:
            raise KeywordError("invalid_keyword_selection")
        ids = sorted(set(paper_ids))
        key = fingerprint([method, ids, inputs or {}, generation])
        with self.connection(True) as db:
            old = db.execute(
                "SELECT b.id FROM keyword_batches b WHERE b.owner_id=? AND b.selection_key=? AND b.cancel_requested=0 AND EXISTS(SELECT 1 FROM keyword_items i WHERE i.batch_id=b.id AND i.status IN ('queued','running')) ORDER BY b.created_at DESC LIMIT 1",
                (self.owner, key),
            ).fetchone()
            if old:
                return old[0]
            if (
                db.execute(
                    "SELECT count(*) FROM keyword_batches b WHERE EXISTS(SELECT 1 FROM keyword_items i WHERE i.batch_id=b.id AND i.status IN ('queued','running'))"
                ).fetchone()[0]
                >= 20
            ):
                raise KeywordError("keyword_queue_full", 429)
            bid = str(uuid.uuid4())
            now = stamp()
            db.execute(
                "INSERT INTO keyword_batches VALUES (?,?,?,?,0,?)",
                (bid, self.owner, method, key, now),
            )
            for pid in ids:
                present = db.execute(
                    "SELECT * FROM papers WHERE owner_id=? AND id=?", (self.owner, pid)
                ).fetchone()
                if present and not in_library(unpack(present)):
                    raise KeywordError("paper_not_found", 404)
                status = "queued" if present else "deleted"
                prior = db.execute(
                    "SELECT i.id,i.checkpoint_json FROM keyword_items i JOIN keyword_batches b ON b.id=i.batch_id WHERE i.owner_id=? AND i.paper_id=? AND b.method=? AND i.status IN ('queued','running') AND b.cancel_requested=0 ORDER BY i.created_at LIMIT 1",
                    (self.owner, pid, method),
                ).fetchone()
                checkpoint = {"force": force}
                if inputs:
                    checkpoint.update(expectedInput=inputs[pid], inputScope=input_scope)
                if prior and (
                    not inputs
                    or json.loads(prior[1]).get("expectedInput") == inputs[pid]
                ):
                    checkpoint["linkedItem"] = prior[0]
                db.execute(
                    "INSERT INTO keyword_items(id,batch_id,owner_id,paper_id,status,checkpoint_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        bid,
                        self.owner,
                        pid,
                        status,
                        encoded(checkpoint),
                        now,
                        now,
                    ),
                )
        return bid

    def batches(self):
        with self.connection() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM keyword_batches WHERE owner_id=? ORDER BY created_at DESC LIMIT 100",
                    (self.owner,),
                )
            ]
        return [self.batch(i, limit=0) for i in ids]

    def batch(self, batch_id, after=0, limit=50):
        with self.connection() as db:
            b = db.execute(
                "SELECT * FROM keyword_batches WHERE owner_id=? AND id=?",
                (self.owner, batch_id),
            ).fetchone()
            if not b:
                raise KeywordError("keyword_task_not_found", 404)
            counts = {
                r[0]: r[1]
                for r in db.execute(
                    "SELECT status,count(*) FROM keyword_items WHERE batch_id=? AND owner_id=? GROUP BY status",
                    (batch_id, self.owner),
                )
            }
            items = [
                dict(r)
                for r in db.execute(
                    "SELECT i.id,i.paper_id,i.status,i.requests,i.error,i.updated_at,p.title FROM keyword_items i LEFT JOIN papers p ON p.id=i.paper_id AND p.owner_id=i.owner_id WHERE i.owner_id=? AND i.batch_id=? ORDER BY i.created_at,i.id LIMIT ? OFFSET ?",
                    (self.owner, batch_id, min(100, max(0, limit)), max(0, after)),
                )
            ]
            events = (
                [
                    dict(r)
                    for r in db.execute(
                        "SELECT kind,data_json,created_at FROM keyword_events WHERE owner_id=? AND item_id IN (SELECT id FROM keyword_items WHERE batch_id=?) ORDER BY sequence DESC LIMIT 50",
                        (self.owner, batch_id),
                    )
                ]
                if limit
                else []
            )
        total = sum(counts.values())
        active = sum(n for s, n in counts.items() if s not in TERMINAL)
        return {
            "id": batch_id,
            "method": b["method"],
            "kind": "keywords",
            "created_at": b["created_at"],
            "counts": counts,
            "total": total,
            "completed": total - active,
            "status": (
                "running"
                if active
                else (
                    "cancelled"
                    if b["cancel_requested"]
                    else (
                        "partial"
                        if any(
                            counts.get(s) for s in ("failed", "stale", "interrupted")
                        )
                        else "completed"
                    )
                )
            ),
            "items": items,
            "events": events,
            "next": (
                after + len(items) if limit and after + len(items) < total else None
            ),
        }

    def cancel(self, batch_id):
        self.batch(batch_id, limit=0)
        with self.connection(True) as db:
            db.execute(
                "UPDATE keyword_batches SET cancel_requested=1 WHERE owner_id=? AND id=?",
                (self.owner, batch_id),
            )
            db.execute(
                "UPDATE keyword_items SET status='cancelled',updated_at=? WHERE owner_id=? AND batch_id=? AND status='queued'",
                (stamp(), self.owner, batch_id),
            )
        return self.batch(batch_id)

    def retry(self, batch_id):
        b = self.batch(batch_id, limit=0)
        with self.connection() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT paper_id FROM keyword_items WHERE owner_id=? AND batch_id=? AND status IN ('failed','stale','interrupted','cancelled')",
                    (self.owner, batch_id),
                )
            ]
        if not ids:
            raise KeywordError("keyword_nothing_to_retry", 409)
        return self.create(ids, b["method"], force=True)

    def apply(
        self,
        paper_id,
        candidates,
        input_key,
        revision,
        method,
        result,
        *,
        item_id=None,
        expected_source=None,
    ):
        with self.connection(True) as db:
            if item_id:
                item = db.execute(
                    "SELECT b.cancel_requested,i.status FROM keyword_items i JOIN keyword_batches b ON b.id=i.batch_id WHERE i.id=? AND i.owner_id=?",
                    (item_id, self.owner),
                ).fetchone()
                if not item or item["cancel_requested"] or item["status"] != "running":
                    raise KeywordError("keyword_cancelled", 409)
            head = ensure_paper(db, self.owner, paper_id)
            if head["revision"] != revision:
                raise KeywordError("tag_revision_conflict", 409)
            if expected_source is not None:
                paper = self.paper(db, paper_id)
                if observed_signature(db, self.owner, paper) != expected_source:
                    raise KeywordError("keyword_source_changed", 409)
            chosen = []
            for candidate in candidates:
                try:
                    tag = create_tag(db, self.owner, candidate["name"])
                except KeywordError as e:
                    if e.code in {"tag_deleted", "tag_name_retired"}:
                        continue
                    raise
                if db.execute(
                    "SELECT 1 FROM keyword_exclusions WHERE owner_id=? AND paper_id=? AND tag_id=?",
                    (self.owner, paper_id, tag["id"]),
                ).fetchone():
                    continue
                chosen.append((tag["id"], candidate))
            # Local and explicit model enhancement are revisions of one automatic
            # layer (at most eight labels), alongside protected manual relations.
            if candidates:
                db.execute(
                    "DELETE FROM keyword_links WHERE owner_id=? AND paper_id=? AND manual=0",
                    (self.owner, paper_id),
                )
            n = db.execute(
                "SELECT count(*) FROM keyword_links WHERE owner_id=? AND paper_id=?",
                (self.owner, paper_id),
            ).fetchone()[0]
            for tid, candidate in chosen[: max(0, 128 - n)]:
                db.execute(
                    "INSERT INTO keyword_links VALUES (?,?,?,0,?,?) ON CONFLICT(owner_id,paper_id,tag_id) DO NOTHING",
                    (self.owner, paper_id, tid, method, encoded(candidate)),
                )
            db.execute(
                "UPDATE keyword_papers SET input_key=?,status=?,result_json=?,revision=revision+1,updated_at=? WHERE owner_id=? AND paper_id=?",
                (
                    input_key,
                    "ready" if candidates else "needs_content",
                    encoded(result),
                    stamp(),
                    self.owner,
                    paper_id,
                ),
            )
            if item_id:
                db.execute(
                    "UPDATE keyword_items SET status=?,result_json=?,updated_at=? WHERE owner_id=? AND id=?",
                    (
                        "completed" if candidates else "needs_content",
                        encoded(result),
                        stamp(),
                        self.owner,
                        item_id,
                    ),
                )
