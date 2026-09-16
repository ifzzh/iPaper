"""Topic relationships use a temporary real SQLite database and no suppliers."""

import sqlite3
import pytest
from ipaper.database.models import SCHEMA_SCRIPT
from ipaper.topics.schema import SCHEMA
from ipaper.topics.store import TopicStore, TopicError, member_ids


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "topics.db"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA_SCRIPT + SCHEMA)
        db.executemany(
            "INSERT INTO papers(id,owner_id,title,file_path) VALUES (?,?,?,?)",
            [(str(i), "a", "Paper", "/papers/original.pdf") for i in range(250)]
            + [("other", "b", "Private", "/papers/private.pdf")],
        )
    return TopicStore(path, "a")


def edit(store, paper, action, topic=None):
    return store.edit_paper(paper, action, store.get(paper)["revision"], topic)


def apply(store, paper, *topics):
    return store.apply(paper, topics, store.get(paper)["revision"], "input")


def ids(state):
    return {t["id"] for t in state["topics"]}


def test_parent_exclusion_child_exception_and_reparent(store):
    parent = store.create("机器人")["id"]
    first = store.create("动作理解", parent)["id"]
    second = store.create("规划", parent)["id"]
    other = store.create("系统")["id"]
    apply(store, "0", first, second, other)
    edit(store, "0", "remove", parent)
    assert ids(apply(store, "0", first, second, other)) == {other}
    edit(store, "0", "add", first)
    assert ids(apply(store, "0", first, second, other)) == {first, other}
    edit(store, "0", "remove", parent)
    store.reparent(first, None, 1)
    assert ids(apply(store, "0", first, second, other)) == {other}


def test_inherited_exclusion_survives_later_child_move(store):
    parent = store.create("研究")["id"]
    edit(store, "0", "remove", parent)
    child = store.create("新子方向", parent)["id"]
    assert not ids(apply(store, "0", child))
    store.reparent(child, None, 1)
    assert not ids(apply(store, "0", child))


def test_clear_blocks_future_topics_except_explicit_add(store):
    first = store.create("第一方向")["id"]
    edit(store, "0", "clear")
    second = store.create("第二方向")["id"]
    assert not ids(apply(store, "0", first, second))
    edit(store, "0", "add", first)
    assert ids(apply(store, "0", first, second)) == {first}
    edit(store, "0", "reset")
    assert ids(apply(store, "0", first, second)) == {first, second}


def test_delete_parent_promotes_and_keeps_children(store):
    root = store.create("研究")["id"]
    child = store.create("方向", root)["id"]
    edit(store, "0", "add", child)
    store.delete(root, 1)
    assert ids(store.get("0")) == {child}
    assert store.get("0")["topics"][0]["parent_id"] is None
    with pytest.raises(TopicError, match="topic_deleted"):
        apply(store, "0", root)
    assert ids(store.get("0")) == {child}


def test_stale_task_and_cross_owner_are_atomic(store):
    first = store.create("研究")["id"]
    old = store.get("0")["revision"]
    edit(store, "0", "add", first)
    with pytest.raises(TopicError, match="revision_conflict"):
        store.apply("0", [], old, "old")
    with pytest.raises(TopicError, match="paper_not_found"):
        store.get("other")
    foreign = TopicStore(store.db_path, "b").create("研究")["id"]
    with pytest.raises(TopicError, match="topic_not_found"):
        apply(store, "0", foreign)
    assert ids(store.get("0")) == {first}


def test_parent_union_deduplicates_250_papers_and_preserves_files(store):
    parent = store.create("研究")["id"]
    left = store.create("左", parent)["id"]
    right = store.create("右", parent)["id"]
    for i in range(250):
        apply(store, str(i), left, right)
    with store.connection() as db:
        assert len(member_ids(db, "a", [parent, left, right])) == 250
        assert {
            r[0]
            for r in db.execute("SELECT file_path FROM papers WHERE owner_id=?", ("a",))
        } == {"/papers/original.pdf"}
    with pytest.raises(TopicError, match="topic_cycle"):
        store.reparent(parent, left, 1)
    # Fresh object uses persisted corrections without relying on in-memory state.
    edit(store, "0", "remove", parent)
    reopened = TopicStore(store.db_path, "a")
    assert not ids(apply(reopened, "0", left, right))


def test_query_combines_topics_tags_and_fixed_selection(store):
    from ipaper.keywords.query import members, selection, selected_ids

    parent = store.create("研究")["id"]
    child = store.create("子方向", parent)["id"]
    for i in range(160):
        apply(store, str(i), child)
    with store.connection(write=True) as db:
        assert len(members(db, "a", {"topicIds": [parent, child]})) == 160
        assert len(members(db, "a", {"unorganized": True})) == 90
        frozen = selection(db, "a", {"topicIds": [parent]})
    edit(store, "200", "add", child)
    with store.connection() as db:
        assert len(members(db, "a", {"topicIds": [parent]})) == 161
        assert len(selected_ids(db, "a", {"selectionId": frozen["id"]})) == 160


def test_merge_preserves_exclusions_and_manual_positive(store):
    source = store.create("来源")["id"]
    target = store.create("目标")["id"]
    edit(store, "0", "remove", target)
    edit(store, "0", "add", source)
    store.merge(source, target, 1, 1)
    assert ids(store.get("0")) == {target}
    assert ids(apply(store, "0", source)) == {target}
    edit(store, "0", "remove", target)
    assert not ids(apply(store, "0", source))


def test_undo_rejects_later_edits_and_can_restore_delete(store):
    node = store.create("研究")["id"]
    edit(store, "0", "add", node)
    store.delete(node, 1)
    with store.connection() as db:
        op = db.execute(
            "SELECT id FROM topic_operations WHERE kind='delete'"
        ).fetchone()[0]
    store.undo(op)
    assert ids(store.get("0")) == {node}
    store.rename(node, "改名", 1)
    with store.connection() as db:
        op = db.execute(
            "SELECT id FROM topic_operations WHERE kind='rename'"
        ).fetchone()[0]
    edit(store, "1", "add", node)
    store.rename(node, "再次改名", 2)
    with pytest.raises(TopicError, match="undo_conflict"):
        store.undo(op)


class SourceReader:
    def __init__(self):
        self.version = "v1"

    def source(self, owner, paper_id):
        return {
            "sections": [{"kind": "title", "text": "Temporal action segmentation"}],
            "inputKey": self.version,
        }

    def shutdown(self):
        pass


def test_durable_local_job_bootstraps_and_reuses_without_suppliers(store):
    from ipaper.topics.service import TopicService

    service = TopicService(store.db_path, None, SourceReader())
    batch = service.create("a", ["0", "1"])
    assert service.create("a", ["0", "1"]) == batch
    with store.connection() as db:
        items = [
            r[0]
            for r in db.execute("SELECT id FROM topic_items WHERE batch_id=?", (batch,))
        ]
    for item in items:
        service.run("a", item)
    with store.connection() as db:
        assert {r[0] for r in db.execute("SELECT status FROM topic_items")} == {
            "completed"
        }
        assert db.execute("SELECT count(*) FROM topic_nodes").fetchone()[0] == 2
    assert len(store.get("0")["topics"]) == 1
    service.shutdown()
    fresh = TopicService(store.db_path, None, SourceReader())
    batch = fresh.create("a", ["0"])
    with store.connection() as db:
        item = db.execute(
            "SELECT id FROM topic_items WHERE batch_id=?", (batch,)
        ).fetchone()[0]
    fresh.run("a", item)
    with store.connection() as db:
        assert (
            db.execute("SELECT status FROM topic_items WHERE id=?", (item,)).fetchone()[
                0
            ]
            == "reused"
        )
    fresh.shutdown()


def test_nonempty_legacy_migration_is_idempotent_and_corrections_survive(store):
    import json
    from ipaper.topics.migration import migrate

    with store.connection(write=True) as db:
        db.execute(
            "INSERT INTO categories(id,name,parent_id,owner_id) VALUES ('old-parent','历史研究','root','a')"
        )
        db.execute(
            "INSERT INTO categories(id,name,parent_id,owner_id) VALUES ('old-child','历史细分','old-parent','a')"
        )
        db.execute(
            "UPDATE papers SET metadata=? WHERE owner_id=? AND id=?",
            (json.dumps({"category_id": "old-child"}), "a", "0"),
        )
        assert migrate(db, "a")["mapped"] == 2
        assert migrate(db, "a")["imported"] == 0
        target = db.execute(
            "SELECT topic_id FROM topic_legacy_map WHERE category_id='old-child'"
        ).fetchone()[0]
    assert ids(store.get("0")) == {target}
    edit(store, "0", "remove", target)
    with store.connection(write=True) as db:
        assert migrate(db, "a")["imported"] == 0
        # Simulated rollback folder edits cannot override a new exclusion.
        db.execute(
            "UPDATE topic_legacy_observed SET category_id=NULL WHERE paper_id='0'"
        )
        assert migrate(db, "a")["imported"] == 0
        assert (
            db.execute("SELECT file_path FROM papers WHERE id='0'").fetchone()[0]
            == "/papers/original.pdf"
        )
    assert not ids(store.get("0"))


def test_moving_existing_automatic_child_into_excluded_branch_hides_it(store):
    parent = store.create("排除分支")["id"]
    child = store.create("自动方向")["id"]
    apply(store, "0", child)
    edit(store, "0", "remove", parent)
    store.reparent(child, parent, 1)
    assert not ids(store.get("0"))
    edit(store, "0", "add", child)
    assert ids(store.get("0")) == {child}


def test_manual_topic_does_not_block_bootstrap_or_resurrect_deleted_definition(store):
    from ipaper.topics.service import TopicService

    manual = store.create("我的待读")["id"]
    canonical = store.create("动作理解与机器人")["id"]
    service = TopicService(store.db_path, None, SourceReader())
    service.bootstrap("a")
    with store.connection() as db:
        from ipaper.topics.store import nodes

        tree = nodes(db, "a")
        assert tree[canonical]["definition_id"] == "embodied"
        assert tree[manual]["definition_id"] is None
        child = next(
            n for n in tree.values() if n["definition_id"] == "action-understanding"
        )
        assert child["parent_id"] == canonical
    store.delete(child["id"], child["revision"])
    service.bootstrap("a")
    assert "action-understanding" not in [
        d["id"] for d in service.propose("a")["definitions"]
    ]
    service.shutdown()


def test_expansion_rejects_stale_preview_and_binding_is_explicit(store):
    from ipaper.topics.service import TopicService
    from ipaper.topics.store import bind_definition

    service = TopicService(store.db_path, None, SourceReader())
    preview = service.propose("a")
    manual = store.create("自定义机器人研究")
    with pytest.raises(TopicError, match="topic_catalog_changed"):
        service.expand("a", preview)
    bind_definition(store, manual["id"], "embodied", manual["revision"])
    service.bootstrap("a")
    with store.connection() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM topic_nodes WHERE definition_id='embodied'"
            ).fetchone()[0]
            == 1
        )
    service.shutdown()


def test_batch_revision_conflict_rolls_back_and_receipt_is_idempotent(store):
    topic = store.create("研究")["id"]
    revisions = {p: store.get(p)["revision"] for p in ["0", "1"]}
    edit(store, "1", "clear")
    with pytest.raises(TopicError, match="revision_conflict"):
        store.edit_batch(["0", "1"], "add", revisions, topic, "request-conflict")
    assert not ids(store.get("0"))
    revisions = {p: store.get(p)["revision"] for p in ["0", "1"]}
    response = store.edit_batch(["0", "1"], "add", revisions, topic, "request-success")
    assert (
        store.edit_batch(["0", "1"], "add", revisions, topic, "request-success")
        == response
    )
    assert store.get("0")["revision"] == revisions["0"] + 1
    with pytest.raises(TopicError, match="request_id_conflict"):
        store.edit_batch(["0", "1"], "clear", revisions, None, "request-success")


def test_admission_preserves_logical_hierarchy_and_explicit_ids(store):
    from ipaper.topics.admission import apply_admission, validated_ids

    selected = store.create("用户选择")["id"]
    with store.connection(write=True) as db:
        before = tuple(
            db.execute("SELECT file_path,metadata FROM papers WHERE id='0'").fetchone()
        )
        apply_admission(
            db,
            "a",
            {
                "id": "0",
                "_topic_ids": [selected],
                "_topic_paths": [["文献集合", "子集合"]],
            },
        )
        assert (
            tuple(
                db.execute(
                    "SELECT file_path,metadata FROM papers WHERE id='0'"
                ).fetchone()
            )
            == before
        )
        assert validated_ids([selected], db=db, owner="a") == [selected]
        with pytest.raises(TopicError):
            validated_ids([selected], db=db, owner="b")
    current = store.get("0")
    assert len(current["topics"]) == 2 and all(t["manual"] for t in current["topics"])
    leaf = next(t for t in current["topics"] if t["name"] == "子集合")
    with store.connection() as db:
        assert (
            db.execute(
                "SELECT name FROM topic_nodes WHERE id=?", (leaf["parent_id"],)
            ).fetchone()[0]
            == "文献集合"
        )


def test_apply_rejects_changed_adopted_content(store):
    from ipaper.keywords.store import observed_signature
    from ipaper.metadata.store import unpack

    topic = store.create("研究")["id"]
    with store.connection() as db:
        signature = observed_signature(
            db, "a", unpack(db.execute("SELECT * FROM papers WHERE id='0'").fetchone())
        )
    with store.connection(write=True) as db:
        db.execute("UPDATE papers SET title='Changed' WHERE id='0'")
    with pytest.raises(TopicError, match="stale"):
        store.apply(
            "0", [topic], store.get("0")["revision"], "old", expected_source=signature
        )
    assert not store.get("0")["topics"]
