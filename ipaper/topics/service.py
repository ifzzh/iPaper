"""Local durable topic queue. No network or model calls in automatic work."""

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from ipaper.metadata.model import fingerprint, stamp
from ipaper.metadata.store import in_library, unpack
from ipaper.keywords.service import KeywordService
from ipaper.security.identity import Identity, current_user_id, run_as_identity
from .definitions import VERSION, BY_ID, classify, proposed_directions
from .store import TopicStore, TopicError, nodes, head

TERMINAL = {
    "completed",
    "reused",
    "unorganized",
    "failed",
    "cancelled",
    "stale",
    "deleted",
}


class TopicService:
    def __init__(self, db_path, processing, source_reader=None):
        self.db_path = str(db_path)
        self.processing = processing
        self.reader = source_reader or KeywordService(db_path, processing)
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="topics")
        self.active = {}
        self.thread = None

    def store(self, owner=None):
        return TopicStore(self.db_path, owner or current_user_id())

    def source(self, owner, paper_id):
        # Reuse only the adopted/original read path, never keyword extraction or
        # its pending queue. This reader makes no provider requests.
        return self.reader.source(owner, paper_id)

    def create(self, owner, paper_ids):
        if (
            not isinstance(paper_ids, list)
            or not 1 <= len(paper_ids) <= 5000
            or any(not isinstance(p, str) or len(p) > 100 for p in paper_ids)
        ):
            raise TopicError("invalid_topic_selection")
        ids = list(dict.fromkeys(paper_ids))
        key = fingerprint(ids)
        store = self.store(owner)
        with store.connection(write=True) as db:
            for paper in ids:
                head(db, owner, paper)
            prior = db.execute(
                "SELECT b.id FROM topic_batches b WHERE b.owner_id=? AND b.selection_key=? AND b.cancel_requested=0 AND EXISTS (SELECT 1 FROM topic_items i WHERE i.batch_id=b.id AND i.status IN ('queued','running'))",
                (owner, key),
            ).fetchone()
            if prior:
                return prior[0]
            if (
                db.execute(
                    "SELECT count(DISTINCT batch_id) FROM topic_items WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= 20
            ):
                raise TopicError("topic_queue_full", 429)
            batch, now = str(uuid.uuid4()), stamp()
            db.execute(
                "INSERT INTO topic_batches VALUES (?,?,?,0,?)", (batch, owner, key, now)
            )
            for paper in ids:
                db.execute(
                    "INSERT INTO topic_items(id,owner_id,batch_id,paper_id,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), owner, batch, paper, now, now),
                )
        self.wake.set()
        return batch

    def check(self, owner, item_id):
        if self.stop.is_set():
            raise TopicError("interrupted")
        with self.store(owner).connection() as db:
            row = db.execute(
                "SELECT i.*,b.cancel_requested FROM topic_items i JOIN topic_batches b ON b.id=i.batch_id AND b.owner_id=i.owner_id WHERE i.owner_id=? AND i.id=?",
                (owner, item_id),
            ).fetchone()
            if not row:
                raise TopicError("task_not_found", 404)
            if row["cancel_requested"]:
                raise TopicError("cancelled")
            paper = db.execute(
                "SELECT * FROM papers WHERE owner_id=? AND id=?",
                (owner, row["paper_id"]),
            ).fetchone()
            if not paper or not in_library(unpack(paper)):
                raise TopicError("deleted")
            user = db.execute(
                "SELECT status FROM users WHERE id=?", (owner,)
            ).fetchone()
            if user and user[0] != "active":
                raise TopicError("cancelled")
            return dict(row)

    def propose(self, owner, check=lambda: None):
        store = self.store(owner)
        with store.connection() as db:
            tree = nodes(db, owner)
            ids = [
                p["id"]
                for row in db.execute(
                    "SELECT * FROM papers WHERE owner_id=? ORDER BY id", (owner,)
                )
                if in_library(p := unpack(row))
            ]
        if len(ids) > 5000:
            raise TopicError("topic_bootstrap_budget", 409)
        samples, deadline = [], time.monotonic() + 30
        for paper in ids:
            check()
            if time.monotonic() > deadline:
                raise TopicError("topic_bootstrap_budget", 409)
            samples.append((paper, self.source(owner, paper)["sections"]))
        # A deleted definition is deliberately not proposed again. Renaming a
        # bound topic also never creates another copy of that direction.
        known = {n["definition_id"] for n in tree.values() if n["definition_id"]}
        proposed = proposed_directions(samples, known)
        return {
            "definitions": [
                {"id": d.id, "name": d.name, "parent": d.parent} for d in proposed
            ],
            "paperCount": len(ids),
            "revision": fingerprint(tree),
            "requests": 0,
        }

    def expand(self, owner, preview, check=lambda: None):
        store = self.store(owner)
        with store.connection(write=True) as db:
            tree = nodes(db, owner)
            if fingerprint(tree) != preview["revision"]:
                raise TopicError("topic_catalog_changed", 409)
            mapped = {
                n["definition_id"]: n["id"]
                for n in tree.values()
                if n["status"] == "active" and n["definition_id"]
            }
            for proposal in preview["definitions"]:
                check()
                definition = BY_ID[proposal["id"]]
                parent = mapped.get(definition.parent)
                # Exact canonical-name migration preserves the user's topic ID;
                # other manual names are untouched and can be bound explicitly.
                existing = next(
                    (
                        n
                        for n in tree.values()
                        if n["status"] == "active"
                        and not n["definition_id"]
                        and n["name"] == definition.name
                        and n["parent_id"] == parent
                    ),
                    None,
                )
                tid, now = (existing["id"] if existing else str(uuid.uuid4())), stamp()
                if existing:
                    db.execute(
                        "UPDATE topic_nodes SET definition_id=?,revision=revision+1,updated_at=? WHERE owner_id=? AND id=?",
                        (definition.id, now, owner, tid),
                    )
                else:
                    db.execute(
                        "INSERT INTO topic_nodes(id,owner_id,name,parent_id,definition_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                        (tid, owner, definition.name, parent, definition.id, now, now),
                    )
                mapped[definition.id] = tid
            if mapped:
                db.execute(
                    "INSERT INTO topic_catalog_state VALUES (?,?,?) ON CONFLICT(owner_id) DO UPDATE SET definition_version=excluded.definition_version",
                    (owner, stamp(), VERSION),
                )

    def bootstrap(self, owner, check=lambda: None):
        with self.store(owner).connection() as db:
            if db.execute(
                "SELECT 1 FROM topic_catalog_state WHERE owner_id=?", (owner,)
            ).fetchone():
                return
        self.expand(owner, self.propose(owner, check), check)

    def run(self, owner, item_id):
        store = self.store(owner)
        try:
            item = self.check(owner, item_id)
            with store.connection(write=True) as db:
                if db.execute(
                    "SELECT 1 FROM topic_items WHERE owner_id=? AND status='running' AND id<>?",
                    (owner, item_id),
                ).fetchone():
                    return
                if (
                    db.execute(
                        "UPDATE topic_items SET status='running',updated_at=? WHERE owner_id=? AND id=? AND status='queued'",
                        (stamp(), owner, item_id),
                    ).rowcount
                    != 1
                ):
                    return
            check = lambda: self.check(owner, item_id)
            self.bootstrap(owner, check)
            state = store.get(item["paper_id"])
            source = self.source(owner, item["paper_id"])
            with store.connection() as db:
                tree = nodes(db, owner)
            mapping = {
                n["definition_id"]: n["id"]
                for n in tree.values()
                if n["status"] == "active" and n["definition_id"] in BY_ID
            }
            taxonomy = sorted(
                (n["id"], n["definition_id"], n["parent_id"], n["status"])
                for n in tree.values()
            )
            key = fingerprint([source["inputKey"], VERSION, taxonomy])
            if state["input_key"] == key:
                check()
                self.finish(owner, item_id, "reused")
                return
            chosen = classify(source["sections"], mapping, check)
            # If a specific definition was not activated, classify() can select
            # the supported broad parent because only bound nodes are allowed.
            check()
            if self.source(owner, item["paper_id"])["inputKey"] != source["inputKey"]:
                raise TopicError("stale")
            with store.connection() as db:
                current = sorted(
                    (n["id"], n["definition_id"], n["parent_id"], n["status"])
                    for n in nodes(db, owner).values()
                )
            if current != taxonomy:
                raise TopicError("stale")
            result = store.apply(
                item["paper_id"],
                [mapping[d] for d in chosen],
                state["revision"],
                key,
                taxonomy=taxonomy,
                item_id=item_id,
                expected_source=source.get("adoptedSignature"),
            )
            self.finish(
                owner, item_id, "completed" if result["topics"] else "unorganized"
            )
        except Exception as error:
            code = getattr(error, "code", "topic_processing_failed")
            status = (
                code
                if code in {"cancelled", "deleted", "stale"}
                else "queued" if code == "interrupted" else "failed"
            )
            self.finish(owner, item_id, status, code)

    def finish(self, owner, item_id, status, error=None):
        with self.store(owner).connection(write=True) as db:
            previous = db.execute(
                "SELECT status FROM topic_items WHERE owner_id=? AND id=?",
                (owner, item_id),
            ).fetchone()
            if (
                previous
                and previous[0] in {"completed", "unorganized", "reused"}
                and status not in {"completed", "unorganized", "reused"}
            ):
                return
            db.execute(
                "UPDATE topic_items SET status=?,error=?,updated_at=? WHERE owner_id=? AND id=?",
                (status, error, stamp(), owner, item_id),
            )
            db.execute(
                "INSERT INTO topic_events(owner_id,item_id,kind,created_at) VALUES (?,?,?,?)",
                (owner, item_id, status, stamp()),
            )

    def settings(self, owner, data=None):
        with self.store(owner).connection(write=data is not None) as db:
            if data is not None:
                if (
                    not isinstance(data, dict)
                    or set(data) != {"automatic"}
                    or type(data["automatic"]) is not bool
                ):
                    raise TopicError("invalid_topic_settings")
                db.execute(
                    "INSERT INTO user_settings_v2 VALUES (?,'topics_v1',?) ON CONFLICT(owner_id,key) DO UPDATE SET value=excluded.value",
                    (owner, json.dumps(data)),
                )
            row = db.execute(
                "SELECT value FROM user_settings_v2 WHERE owner_id=? AND key='topics_v1'",
                (owner,),
            ).fetchone()
            return json.loads(row[0]) if row else {"automatic": True}

    def pending(self):
        with self.store("__scheduler__").connection() as db:
            events = list(
                db.execute(
                    "SELECT owner_id,paper_id,updated_at FROM topic_pending ORDER BY updated_at LIMIT 20"
                )
            )
        for owner, paper_id, generation in events:
            if self.stop.is_set():
                return
            with self.store(owner).connection() as db:
                paper = db.execute(
                    "SELECT * FROM papers WHERE owner_id=? AND id=?", (owner, paper_id)
                ).fetchone()
            with self.store(owner).connection() as db:
                active = db.execute(
                    "SELECT 1 FROM topic_items WHERE owner_id=? AND paper_id=? AND status IN ('queued','running')",
                    (owner, paper_id),
                ).fetchone()
            if active:
                # Keep a newer adopted-content event until the in-flight task
                # commits or goes stale, rather than losing it to deduplication.
                continue
            if (
                paper
                and in_library(unpack(paper))
                and self.settings(owner)["automatic"]
            ):
                try:
                    self.create(owner, [paper_id])
                except TopicError as error:
                    if error.code == "topic_queue_full":
                        break
                    if error.code != "paper_not_found":
                        raise
            with self.store(owner).connection(write=True) as db:
                db.execute(
                    "DELETE FROM topic_pending WHERE owner_id=? AND paper_id=? AND updated_at=?",
                    (owner, paper_id, generation),
                )

    def reconcile(self):
        from ipaper.keywords.store import observed_signature
        from .store import queue_change

        with self.store("__scheduler__").connection(write=True) as db:
            for (owner,) in list(db.execute("SELECT id FROM users")):
                initialized = db.execute(
                    "SELECT 1 FROM user_settings_v2 WHERE owner_id=? AND key='topics_observed_v1'",
                    (owner,),
                ).fetchone()
                observed = {
                    r[0]: r[1]
                    for r in db.execute(
                        "SELECT paper_id,signature FROM topic_observed WHERE owner_id=?",
                        (owner,),
                    )
                }
                current = set()
                for row in list(
                    db.execute("SELECT * FROM papers WHERE owner_id=?", (owner,))
                ):
                    paper = unpack(row)
                    if not in_library(paper):
                        continue
                    current.add(paper["id"])
                    signature = observed_signature(db, owner, paper)
                    if initialized and observed.get(paper["id"]) != signature:
                        queue_change(db, owner, paper["id"])
                    db.execute(
                        "INSERT INTO topic_observed VALUES (?,?,?) ON CONFLICT(owner_id,paper_id) DO UPDATE SET signature=excluded.signature",
                        (owner, paper["id"], signature),
                    )
                for table in (
                    "topic_links",
                    "topic_exclusions",
                    "topic_papers",
                    "topic_pending",
                    "topic_legacy_observed",
                    "topic_observed",
                ):
                    for (paper_id,) in list(
                        db.execute(
                            "SELECT DISTINCT paper_id FROM "
                            + table
                            + " WHERE owner_id=?",
                            (owner,),
                        )
                    ):
                        if paper_id not in current:
                            db.execute(
                                "DELETE FROM "
                                + table
                                + " WHERE owner_id=? AND paper_id=?",
                                (owner, paper_id),
                            )
                            db.execute(
                                "UPDATE topic_items SET status='deleted' WHERE owner_id=? AND paper_id=? AND status IN ('queued','running')",
                                (owner, paper_id),
                            )
                db.execute(
                    "INSERT OR IGNORE INTO user_settings_v2 VALUES (?,'topics_observed_v1','true')",
                    (owner,),
                )

    def start(self):
        if self.thread:
            return
        self.reconcile()
        with self.store("__scheduler__").connection(write=True) as db:
            db.execute("UPDATE topic_items SET status='queued' WHERE status='running'")
        from .migration import migrate

        with self.store("__scheduler__").connection(write=True) as db:
            for row in list(db.execute("SELECT id FROM users")):
                migrate(db, row[0])
        self.thread = threading.Thread(
            target=self.loop, daemon=True, name="topic-coordinator"
        )
        self.thread.start()

    def loop(self):
        while not self.stop.is_set():
            try:
                self.pending()
                self.active = {
                    key: future
                    for key, future in self.active.items()
                    if not future.done()
                }
                with self.store("__scheduler__").connection() as db:
                    rows = db.execute(
                        "SELECT i.owner_id,i.id FROM topic_items i JOIN topic_batches b ON b.id=i.batch_id WHERE i.status='queued' AND b.cancel_requested=0 ORDER BY i.created_at,i.id LIMIT 100"
                    ).fetchall()
                owners = {key[0] for key in self.active}
                for owner, item in rows:
                    if len(self.active) >= 2:
                        break
                    if owner in owners:
                        continue
                    owners.add(owner)
                    self.active[(owner, item)] = self.pool.submit(
                        run_as_identity,
                        Identity(owner, "topics", "user"),
                        self.run,
                        owner,
                        item,
                    )
            except Exception:
                logging.getLogger(__name__).exception("Topic scheduler failed")
            self.wake.wait(1)
            self.wake.clear()

    def shutdown(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.reader.shutdown()
