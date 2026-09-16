#!/usr/bin/env python3
"""Synthetic old-image rollback, new writes and actual process recovery."""
import argparse, json, os, sqlite3, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_keywords_rollback import OLD, OWNER, PAPER
from ipaper.database.models import SCHEMA_SCRIPT
from ipaper.topics.service import TopicService
from ipaper.metadata.store import MetadataStore

CHILD = r"""
import sys,time,sqlite3
from pathlib import Path
from ipaper.topics.service import TopicService
path,owner,batch,pause=sys.argv[1:]
service=TopicService(path,None)
if pause!='-':
 original=service.source
 def paused(*a,**kw):
  Path(pause).write_text('started');time.sleep(120);return original(*a,**kw)
 service.source=paused
service.start()
try:
 deadline=time.monotonic()+60
 while time.monotonic()<deadline:
  with sqlite3.connect(path) as db:
   active=db.execute("SELECT count(*) FROM topic_items WHERE batch_id=? AND status IN ('queued','running')",(batch,)).fetchone()[0]
  if not active:break
  time.sleep(.1)
 else:raise RuntimeError('recovery_timeout')
finally:service.shutdown()
"""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-image", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    dest = Path(a.output).resolve()
    dest.mkdir(mode=0o700, parents=True, exist_ok=False)
    info = json.loads(
        subprocess.check_output(["docker", "image", "inspect", a.old_image])
    )[0]
    assert info["Config"]["Labels"]["org.opencontainers.image.version"] == "1.6.0"

    def old(mode):
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--volume",
                str(dest) + ":/check",
                "--env",
                "IPAPER_DB_PATH=/check/ipaper.db",
                a.old_image,
                "python",
                "-c",
                OLD,
                OWNER,
                PAPER,
                mode,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    old("seed")
    path = dest / "ipaper.db"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA_SCRIPT)
    service = TopicService(path, None)
    service.reconcile()
    store = service.store(OWNER)
    root = store.create("保留方向")["id"]
    child = store.create("受保护子方向", root)["id"]
    store.edit_paper(PAPER, "add", store.get(PAPER)["revision"], child)
    store.edit_paper(PAPER, "remove", store.get(PAPER)["revision"], root)
    manual = store.create("人工主题")["id"]
    store.edit_paper(PAPER, "add", store.get(PAPER)["revision"], manual)
    store.edit_paper(
        "remove-during-rollback",
        "add",
        store.get("remove-during-rollback")["revision"],
        manual,
    )
    batch = service.create(OWNER, [PAPER])
    marker = dest / "started"
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(path), OWNER, batch, str(marker)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline:
            assert proc.poll() is None
            time.sleep(0.1)
        assert marker.exists()
    finally:
        proc.kill()
        proc.wait(timeout=5)
    subprocess.run(
        [sys.executable, "-c", CHILD, str(path), OWNER, batch, "-"],
        check=True,
        capture_output=True,
        timeout=75,
    )
    with store.connection() as db:
        assert (
            db.execute(
                "SELECT status FROM topic_items WHERE batch_id=?", (batch,)
            ).fetchone()[0]
            == "completed"
        )
    old("write")
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA_SCRIPT)
    service.reconcile()
    MetadataStore(path, OWNER).reconcile()
    with store.connection() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        paper = store.paper(db, PAPER)
        assert (
            paper["title"] == "Spectral calibration after rollback"
            and paper["abstract"] == ""
            and paper["notes"] == "Preserve new user data"
            and paper["read_time"] == 42
        )
        assert paper["file_path"] == "/data/papers/stable.pdf"
        assert db.execute(
            "SELECT 1 FROM topic_exclusions WHERE owner_id=? AND paper_id=? AND topic_id=?",
            (OWNER, PAPER, child),
        ).fetchone()
        assert not db.execute(
            "SELECT 1 FROM topic_links WHERE paper_id='remove-during-rollback'"
        ).fetchone()
        assert db.execute(
            "SELECT 1 FROM topic_pending WHERE paper_id='added-after-rollback'"
        ).fetchone()
    assert any(t["id"] == manual and t["manual"] for t in store.get(PAPER)["topics"])
    result = {
        "passed": True,
        "oldImage": a.old_image,
        "oldImageId": info["Id"],
        "roundTrip": "1.6.0 → topics → 1.6.0 writes → topics",
        "actualProcessKillAndRecovery": True,
        "manualBranchExclusionPreserved": True,
        "newAndEditedPapersPreserved": True,
        "deletedPaperNotResurrected": True,
        "pathsUnchanged": True,
        "providerCalls": 0,
        "syntheticOnly": True,
    }
    (dest / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    service.shutdown()


if __name__ == "__main__":
    main()
