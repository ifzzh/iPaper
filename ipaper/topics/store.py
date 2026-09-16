"""Transactional logical membership. This module never accesses asset files."""

import uuid
import json
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from ipaper.metadata.store import MetadataStore, in_library, unpack
from ipaper.metadata.model import stamp
from ipaper.keywords.common import label


class TopicError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def nodes(db, owner):
    return {
        r["id"]: dict(r)
        for r in db.execute("SELECT * FROM topic_nodes WHERE owner_id=?", (owner,))
    }


def resolve(db, owner, topic_id):
    tree = nodes(db, owner)
    seen = set()
    while topic_id in tree and topic_id not in seen:
        seen.add(topic_id)
        node = tree[topic_id]
        if node["status"] == "merged":
            topic_id = node["merged_into"]
            continue
        if node["status"] == "active":
            return node
        raise TopicError("topic_deleted", 409)
    raise TopicError("topic_not_found", 404)


def descendants(tree, topic_id):
    found, pending = set(), [topic_id]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        pending.extend(
            n["id"]
            for n in tree.values()
            if n["parent_id"] == current and n["status"] == "active"
        )
    return found


def ancestors(tree, topic_id):
    found = set()
    while topic_id in tree and topic_id not in found:
        found.add(topic_id)
        topic_id = tree[topic_id]["parent_id"]
    return found


def member_ids(db, owner, topic_ids):
    tree, selected = nodes(db, owner), set()
    for topic in topic_ids:
        selected.update(descendants(tree, resolve(db, owner, topic)["id"]))
    return {
        r[0]
        for r in db.execute(
            "SELECT paper_id,topic_id FROM topic_links WHERE owner_id=?", (owner,)
        )
        if r[1] in selected and tree.get(r[1], {}).get("status") == "active"
    }


def head(db, owner, paper_id):
    paper = db.execute(
        "SELECT * FROM papers WHERE owner_id=? AND id=?", (owner, paper_id)
    ).fetchone()
    if not paper or not in_library(unpack(paper)):
        raise TopicError("paper_not_found", 404)
    db.execute(
        "INSERT OR IGNORE INTO topic_papers(owner_id,paper_id) VALUES (?,?)",
        (owner, paper_id),
    )
    return dict(
        db.execute(
            "SELECT * FROM topic_papers WHERE owner_id=? AND paper_id=?",
            (owner, paper_id),
        ).fetchone()
    )


def revision(row, expected):
    if type(expected) is not int or row["revision"] != expected:
        raise TopicError("revision_conflict", 409)


class TopicStore(MetadataStore):
    def create(self, name, parent_id=None, definition_id=None):
        name = label(name)
        with self.operation("create") as db:
            if parent_id:
                parent_id = resolve(db, self.owner, parent_id)["id"]
            if any(
                n["status"] == "active"
                and n["parent_id"] == parent_id
                and n["name"].casefold() == name.casefold()
                for n in nodes(db, self.owner).values()
            ):
                raise TopicError("topic_name_conflict", 409)
            if definition_id and any(
                n["status"] == "active" and n["definition_id"] == definition_id
                for n in nodes(db, self.owner).values()
            ):
                raise TopicError("topic_definition_conflict", 409)
            if len(nodes(db, self.owner)) >= 1000:
                raise TopicError("topic_limit", 409)
            topic_id, now = str(uuid.uuid4()), stamp()
            db.execute(
                "INSERT INTO topic_nodes(id,owner_id,name,parent_id,definition_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (topic_id, self.owner, name, parent_id, definition_id, now, now),
            )
            return resolve(db, self.owner, topic_id)

    def get(self, paper_id):
        with self.connection(write=True) as db:
            state = head(db, self.owner, paper_id)
            tree = nodes(db, self.owner)
            state["topics"] = [
                dict(tree[r["topic_id"]], manual=bool(r["manual"]))
                for r in db.execute(
                    "SELECT * FROM topic_links WHERE owner_id=? AND paper_id=?",
                    (self.owner, paper_id),
                )
                if tree.get(r["topic_id"], {}).get("status") == "active"
            ]
            return state

    def edit_paper(self, paper_id, action, expected, topic_id=None):
        with self.operation("edit_paper") as db:
            self._edit_paper(db, paper_id, action, expected, topic_id)
        return self.get(paper_id)

    def _edit_paper(self, db, paper_id, action, expected, topic_id=None):
        if action not in {"add", "confirm", "remove", "clear", "reset"}:
            raise TopicError("invalid_topic_action")
        state = head(db, self.owner, paper_id)
        revision(state, expected)
        tree = nodes(db, self.owner)
        if action in {"add", "confirm", "remove"}:
            topic_id = resolve(db, self.owner, topic_id)["id"]
        if action in {"add", "confirm"}:
            # Explicit positive association is the sole exception to an
            # ancestor exclusion; siblings stay blocked automatically.
            db.execute(
                "INSERT INTO topic_links VALUES (?,?,?,1) ON CONFLICT(owner_id,paper_id,topic_id) DO UPDATE SET manual=1",
                (self.owner, paper_id, topic_id),
            )
        elif action == "remove":
            for target in descendants(tree, topic_id):
                db.execute(
                    "INSERT OR IGNORE INTO topic_exclusions VALUES (?,?,?)",
                    (self.owner, paper_id, target),
                )
                db.execute(
                    "DELETE FROM topic_links WHERE owner_id=? AND paper_id=? AND topic_id=?",
                    (self.owner, paper_id, target),
                )
        elif action == "clear":
            db.execute(
                "DELETE FROM topic_links WHERE owner_id=? AND paper_id=?",
                (self.owner, paper_id),
            )
            db.execute(
                "UPDATE topic_papers SET auto_blocked=1 WHERE owner_id=? AND paper_id=?",
                (self.owner, paper_id),
            )
        else:
            db.execute(
                "DELETE FROM topic_exclusions WHERE owner_id=? AND paper_id=?",
                (self.owner, paper_id),
            )
            db.execute(
                "UPDATE topic_papers SET auto_blocked=0,input_key='' WHERE owner_id=? AND paper_id=?",
                (self.owner, paper_id),
            )
        db.execute(
            "UPDATE topic_papers SET revision=revision+1 WHERE owner_id=? AND paper_id=?",
            (self.owner, paper_id),
        )

    def edit_batch(self, paper_ids, action, revisions, topic_id, request_id):
        from ipaper.metadata.model import fingerprint

        if (
            not isinstance(request_id, str)
            or not 8 <= len(request_id) <= 100
            or not isinstance(revisions, dict)
        ):
            raise TopicError("invalid_topic_request")
        if (
            not isinstance(paper_ids, list)
            or not 1 <= len(paper_ids) <= 5000
            or len(set(paper_ids)) != len(paper_ids)
        ):
            raise TopicError("invalid_topic_selection")
        payload_key = fingerprint([paper_ids, action, revisions, topic_id])
        with self.operation("batch_edit") as db:
            prior = db.execute(
                "SELECT * FROM topic_mutation_receipts WHERE owner_id=? AND request_id=?",
                (self.owner, request_id),
            ).fetchone()
            if prior:
                if prior["payload_key"] != payload_key:
                    raise TopicError("request_id_conflict", 409)
                return json.loads(prior["result_json"])
            for paper_id in paper_ids:
                self._edit_paper(
                    db, paper_id, action, revisions.get(paper_id), topic_id
                )
            result = {"count": len(paper_ids), "requestId": request_id}
            db.execute(
                "INSERT INTO topic_mutation_receipts VALUES (?,?,?,?,?)",
                (self.owner, request_id, payload_key, json.dumps(result), stamp()),
            )
            return result

    def apply(
        self,
        paper_id,
        topic_ids,
        expected,
        input_key,
        *,
        taxonomy=None,
        item_id=None,
        expected_source=None,
    ):
        if len(topic_ids) > 3:
            raise TopicError("automatic_topic_limit")
        with self.connection(write=True) as db:
            state = head(db, self.owner, paper_id)
            revision(state, expected)
            if expected_source is not None:
                from ipaper.keywords.store import observed_signature
                from ipaper.metadata.store import unpack

                paper = unpack(
                    db.execute(
                        "SELECT * FROM papers WHERE id=? AND owner_id=?",
                        (paper_id, self.owner),
                    ).fetchone()
                )
                if observed_signature(db, self.owner, paper) != expected_source:
                    raise TopicError("stale", 409)
            tree = nodes(db, self.owner)
            if taxonomy is not None:
                current = sorted(
                    (n["id"], n["definition_id"], n["parent_id"], n["status"])
                    for n in tree.values()
                )
                if current != taxonomy:
                    raise TopicError("stale", 409)
            if item_id is not None:
                item = db.execute(
                    "SELECT i.status,b.cancel_requested FROM topic_items i JOIN topic_batches b ON b.id=i.batch_id AND b.owner_id=i.owner_id WHERE i.owner_id=? AND i.id=? AND i.paper_id=?",
                    (self.owner, item_id, paper_id),
                ).fetchone()
                if not item or item["status"] != "running" or item["cancel_requested"]:
                    raise TopicError("cancelled", 409)
            chosen = {resolve(db, self.owner, t)["id"] for t in topic_ids}
            excluded = {
                r[0]
                for r in db.execute(
                    "SELECT topic_id FROM topic_exclusions WHERE owner_id=? AND paper_id=?",
                    (self.owner, paper_id),
                )
            }
            db.execute(
                "DELETE FROM topic_links WHERE owner_id=? AND paper_id=? AND manual=0",
                (self.owner, paper_id),
            )
            if not state["auto_blocked"]:
                for topic in chosen:
                    if not ancestors(tree, topic) & excluded:
                        db.execute(
                            "INSERT OR IGNORE INTO topic_links VALUES (?,?,?,0)",
                            (self.owner, paper_id, topic),
                        )
            db.execute(
                "UPDATE topic_papers SET revision=revision+1,input_key=? WHERE owner_id=? AND paper_id=?",
                (input_key, self.owner, paper_id),
            )
            if item_id is not None:
                any_link = db.execute(
                    "SELECT 1 FROM topic_links l JOIN topic_nodes n ON n.id=l.topic_id AND n.owner_id=l.owner_id WHERE l.owner_id=? AND l.paper_id=? AND n.status='active'",
                    (self.owner, paper_id),
                ).fetchone()
                db.execute(
                    "UPDATE topic_items SET status=?,input_key=?,updated_at=? WHERE owner_id=? AND id=?",
                    (
                        "completed" if any_link else "unorganized",
                        input_key,
                        stamp(),
                        self.owner,
                        item_id,
                    ),
                )
        return self.get(paper_id)

    def reparent(self, topic_id, parent_id, expected):
        with self.operation("reparent") as db:
            node = resolve(db, self.owner, topic_id)
            revision(node, expected)
            tree = nodes(db, self.owner)
            if parent_id:
                parent_id = resolve(db, self.owner, parent_id)["id"]
                if parent_id in descendants(tree, node["id"]):
                    raise TopicError("topic_cycle", 409)
            if any(
                n["id"] != node["id"]
                and n["status"] == "active"
                and n["parent_id"] == parent_id
                and n["name"].casefold() == node["name"].casefold()
                for n in tree.values()
            ):
                raise TopicError("topic_name_conflict", 409)
            self._preserve_exclusions(db, tree, node["id"])
            db.execute(
                "UPDATE topic_nodes SET parent_id=?,revision=revision+1,updated_at=? WHERE owner_id=? AND id=?",
                (parent_id, stamp(), self.owner, node["id"]),
            )
            self._prune_automatic(db)

    def _preserve_exclusions(self, db, tree, topic_id):
        # Freeze inherited exclusions before a subtree leaves its branch.
        for ancestor in ancestors(tree, topic_id):
            papers = [
                r[0]
                for r in db.execute(
                    "SELECT paper_id FROM topic_exclusions WHERE owner_id=? AND topic_id=?",
                    (self.owner, ancestor),
                )
            ]
            for paper in papers:
                for target in descendants(tree, topic_id):
                    db.execute(
                        "INSERT OR IGNORE INTO topic_exclusions VALUES (?,?,?)",
                        (self.owner, paper, target),
                    )

    def delete(self, topic_id, expected):
        with self.operation("delete") as db:
            node = resolve(db, self.owner, topic_id)
            revision(node, expected)
            tree = nodes(db, self.owner)
            children = [
                n
                for n in tree.values()
                if n["status"] == "active" and n["parent_id"] == node["id"]
            ]
            siblings = {
                n["name"].casefold()
                for n in tree.values()
                if n["status"] == "active"
                and n["parent_id"] == node["parent_id"]
                and n["id"] != node["id"]
            }
            if any(n["name"].casefold() in siblings for n in children):
                raise TopicError("topic_name_conflict", 409)
            self._preserve_exclusions(db, tree, node["id"])
            db.execute(
                "INSERT OR IGNORE INTO topic_exclusions SELECT owner_id,paper_id,topic_id FROM topic_links WHERE owner_id=? AND topic_id=?",
                (self.owner, node["id"]),
            )
            db.execute(
                "DELETE FROM topic_links WHERE owner_id=? AND topic_id=?",
                (self.owner, node["id"]),
            )
            db.execute(
                "UPDATE topic_nodes SET parent_id=?,revision=revision+1,updated_at=? WHERE owner_id=? AND parent_id=?",
                (node["parent_id"], stamp(), self.owner, node["id"]),
            )
            db.execute(
                "UPDATE topic_nodes SET status='deleted',revision=revision+1,updated_at=? WHERE owner_id=? AND id=?",
                (stamp(), self.owner, node["id"]),
            )

    def _snapshot(self, db):
        tables = (
            "topic_nodes",
            "topic_links",
            "topic_exclusions",
            "topic_papers",
            "topic_legacy_map",
        )
        result, size = {}, 0
        for table in tables:
            rows = []
            for row in db.execute(
                "SELECT * FROM " + table + " WHERE owner_id=? ORDER BY rowid",
                (self.owner,),
            ):
                value = dict(row)
                size += len(json.dumps(value, ensure_ascii=False).encode())
                if size > 8 * 1024 * 1024:
                    raise TopicError("topic_operation_limit", 413)
                rows.append(value)
            result[table] = rows
        return result

    @contextmanager
    def operation(self, kind):
        with self.connection(write=True) as db:
            before = self._snapshot(db)
            yield db
            after = self._snapshot(db)
            if before != after:
                db.execute(
                    "INSERT INTO topic_operations VALUES (?,?,?,?,?,0,?)",
                    (
                        str(uuid.uuid4()),
                        self.owner,
                        kind,
                        json.dumps(before, ensure_ascii=False),
                        json.dumps(after, ensure_ascii=False),
                        stamp(),
                    ),
                )
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            db.execute(
                "DELETE FROM topic_operations WHERE owner_id=? AND created_at<?",
                (self.owner, cutoff),
            )

    def undo(self, operation_id):
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM topic_operations WHERE owner_id=? AND id=?",
                (self.owner, operation_id),
            ).fetchone()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            if not row or row["undone"] or row["created_at"] < cutoff:
                raise TopicError("operation_unavailable", 409)
            before, after = json.loads(row["before_json"]), json.loads(
                row["after_json"]
            )
            keys = {
                "topic_nodes": ("id",),
                "topic_links": ("paper_id", "topic_id"),
                "topic_exclusions": ("paper_id", "topic_id"),
                "topic_papers": ("paper_id",),
                "topic_legacy_map": ("category_id",),
            }
            changes = []
            current = self._snapshot(db)
            for table, columns in keys.items():
                index = lambda rows: {tuple(r[k] for k in columns): r for r in rows}
                previous, expected, actual = (
                    index(before[table]),
                    index(after[table]),
                    index(current[table]),
                )
                for key in previous.keys() | expected.keys():
                    if previous.get(key) == expected.get(key):
                        continue
                    if actual.get(key) != expected.get(key):
                        raise TopicError("undo_conflict", 409)
                    value = previous.get(key)
                    if (
                        value
                        and "paper_id" in value
                        and not db.execute(
                            "SELECT 1 FROM papers WHERE owner_id=? AND id=?",
                            (self.owner, value["paper_id"]),
                        ).fetchone()
                    ):
                        raise TopicError("undo_deleted_paper", 409)
                    changes.append((table, columns, key, value))
            removed_nodes = {
                key[0]
                for table, _, key, value in changes
                if table == "topic_nodes" and value is None
            }
            changing_links = {(table, key) for table, _, key, _ in changes}
            for tid in removed_nodes:
                for table in ("topic_links", "topic_exclusions"):
                    for item in current[table]:
                        if (
                            item["topic_id"] == tid
                            and (table, (item["paper_id"], tid)) not in changing_links
                        ):
                            raise TopicError("undo_conflict", 409)
                if any(
                    n["parent_id"] == tid and n["id"] not in removed_nodes
                    for n in current["topic_nodes"]
                ):
                    raise TopicError("undo_conflict", 409)
            # Remove changed rows first so restoring a merge cannot violate the
            # one-active-definition index. Unrelated new work stays untouched.
            for table, columns, key, value in changes:
                clause = " AND ".join(column + "=?" for column in columns)
                db.execute(
                    "DELETE FROM " + table + " WHERE owner_id=? AND " + clause,
                    (self.owner, *key),
                )
            for table, columns, key, value in changes:
                if value:
                    db.execute(
                        "INSERT INTO "
                        + table
                        + "("
                        + ",".join(value)
                        + ") VALUES ("
                        + ",".join("?" for _ in value)
                        + ")",
                        tuple(value.values()),
                    )
            db.execute(
                "UPDATE topic_operations SET undone=1 WHERE id=? AND owner_id=?",
                (operation_id, self.owner),
            )

    def rename(self, topic_id, name, expected):
        name = label(name)
        with self.operation("rename") as db:
            node = resolve(db, self.owner, topic_id)
            revision(node, expected)
            if any(
                n["id"] != node["id"]
                and n["status"] == "active"
                and n["parent_id"] == node["parent_id"]
                and n["name"].casefold() == name.casefold()
                for n in nodes(db, self.owner).values()
            ):
                raise TopicError("topic_name_conflict", 409)
            db.execute(
                "UPDATE topic_nodes SET name=?,revision=revision+1,updated_at=? WHERE id=? AND owner_id=?",
                (name, stamp(), node["id"], self.owner),
            )

    def merge(self, source_id, target_id, expected, target_revision):
        with self.operation("merge") as db:
            source = resolve(db, self.owner, source_id)
            target = resolve(db, self.owner, target_id)
            revision(source, expected)
            revision(target, target_revision)
            tree = nodes(db, self.owner)
            if source["id"] in ancestors(tree, target["id"]) or target[
                "id"
            ] in ancestors(tree, source["id"]):
                raise TopicError("topic_merge_cycle", 409)
            children = [
                n
                for n in tree.values()
                if n["status"] == "active" and n["parent_id"] == source["id"]
            ]
            siblings = {
                n["name"].casefold()
                for n in tree.values()
                if n["status"] == "active"
                and n["parent_id"] == target["id"]
                and n["id"] != source["id"]
            }
            if any(n["name"].casefold() in siblings for n in children):
                raise TopicError("topic_name_conflict", 409)
            self._preserve_exclusions(db, tree, source["id"])
            for row in list(
                db.execute(
                    "SELECT * FROM topic_links WHERE owner_id=? AND topic_id=?",
                    (self.owner, source["id"]),
                )
            ):
                db.execute(
                    "INSERT INTO topic_links VALUES (?,?,?,?) ON CONFLICT(owner_id,paper_id,topic_id) DO UPDATE SET manual=MAX(manual,excluded.manual)",
                    (self.owner, row["paper_id"], target["id"], row["manual"]),
                )
            db.execute(
                "INSERT OR IGNORE INTO topic_exclusions SELECT owner_id,paper_id,? FROM topic_exclusions WHERE owner_id=? AND topic_id=?",
                (target["id"], self.owner, source["id"]),
            )
            db.execute(
                "DELETE FROM topic_links WHERE owner_id=? AND topic_id=?",
                (self.owner, source["id"]),
            )
            db.execute(
                "DELETE FROM topic_links WHERE owner_id=? AND topic_id=? AND manual=0 AND paper_id IN (SELECT paper_id FROM topic_exclusions WHERE owner_id=? AND topic_id=?)",
                (self.owner, target["id"], self.owner, target["id"]),
            )
            db.execute(
                "UPDATE topic_nodes SET parent_id=?,revision=revision+1,updated_at=? WHERE owner_id=? AND parent_id=?",
                (target["id"], stamp(), self.owner, source["id"]),
            )
            db.execute(
                "UPDATE topic_nodes SET status='merged',merged_into=?,revision=revision+1,updated_at=? WHERE owner_id=? AND id=?",
                (target["id"], stamp(), self.owner, source["id"]),
            )
            db.execute(
                "UPDATE topic_nodes SET revision=revision+1,updated_at=? WHERE owner_id=? AND id=?",
                (stamp(), self.owner, target["id"]),
            )
            self._prune_automatic(db)

    def _prune_automatic(self, db):
        tree = nodes(db, self.owner)
        exclusions = {}
        for paper, topic in db.execute(
            "SELECT paper_id,topic_id FROM topic_exclusions WHERE owner_id=?",
            (self.owner,),
        ):
            exclusions.setdefault(paper, set()).add(topic)
        removed = set()
        for paper, topic in list(
            db.execute(
                "SELECT paper_id,topic_id FROM topic_links WHERE owner_id=? AND manual=0",
                (self.owner,),
            )
        ):
            if ancestors(tree, topic) & exclusions.get(paper, set()):
                db.execute(
                    "DELETE FROM topic_links WHERE owner_id=? AND paper_id=? AND topic_id=? AND manual=0",
                    (self.owner, paper, topic),
                )
                removed.add(paper)
        for paper in removed:
            db.execute(
                "UPDATE topic_papers SET revision=revision+1 WHERE owner_id=? AND paper_id=?",
                (self.owner, paper),
            )

    def restore(self, topic_id, expected):
        with self.operation("restore") as db:
            tree = nodes(db, self.owner)
            node = tree.get(topic_id)
            if not node:
                raise TopicError("topic_not_found", 404)
            revision(node, expected)
            if node["status"] != "deleted":
                raise TopicError("revision_conflict", 409)
            parent = (
                node["parent_id"]
                if tree.get(node["parent_id"], {}).get("status") == "active"
                else None
            )
            if any(
                n["status"] == "active"
                and n["parent_id"] == parent
                and n["name"].casefold() == node["name"].casefold()
                for n in tree.values()
            ):
                raise TopicError("topic_name_conflict", 409)
            if node["definition_id"] and any(
                n["status"] == "active" and n["definition_id"] == node["definition_id"]
                for n in tree.values()
            ):
                raise TopicError("topic_definition_in_use", 409)
            db.execute(
                "UPDATE topic_nodes SET status='active',parent_id=?,revision=revision+1,updated_at=? WHERE owner_id=? AND id=?",
                (parent, stamp(), self.owner, topic_id),
            )

    def order(self, topic_id, position, expected):
        if type(position) is not int or not 0 <= position <= 1000:
            raise TopicError("invalid_topic_order")
        with self.operation("order") as db:
            node = resolve(db, self.owner, topic_id)
            revision(node, expected)
            db.execute(
                "UPDATE topic_nodes SET sort_order=?,revision=revision+1,updated_at=? WHERE owner_id=? AND id=?",
                (position, stamp(), self.owner, node["id"]),
            )


def queue_change(db, owner, paper_id):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='topic_pending'").fetchone():
        db.execute(
            "INSERT INTO topic_pending VALUES (?,?,?) ON CONFLICT(owner_id,paper_id) DO UPDATE SET updated_at=excluded.updated_at",
            (owner, paper_id, stamp()),
        )


# Binding is an explicit semantic edit; names and sort order are not definitions.
def bind_definition(store, topic_id, definition_id, expected):
    from .definitions import BY_ID

    if definition_id is not None and definition_id not in BY_ID:
        raise TopicError("invalid_topic_definition")
    with store.operation("bind") as db:
        node = resolve(db, store.owner, topic_id)
        revision(node, expected)
        if (
            definition_id
            and db.execute(
                "SELECT 1 FROM topic_nodes WHERE owner_id=? AND definition_id=? AND id<>? AND status='active'",
                (store.owner, definition_id, node["id"]),
            ).fetchone()
        ):
            raise TopicError("topic_definition_in_use", 409)
        db.execute(
            "UPDATE topic_nodes SET definition_id=?,revision=revision+1,updated_at=? WHERE owner_id=? AND id=?",
            (definition_id, stamp(), store.owner, node["id"]),
        )
