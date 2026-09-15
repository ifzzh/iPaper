#!/usr/bin/env python3
"""Synthetic 1.5.0 rollback and real-process local-task recovery rehearsal."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ipaper.database.models import SCHEMA_SCRIPT
from ipaper.keywords.service import KeywordService
from ipaper.metadata.store import MetadataStore

OWNER = "00000000-0000-0000-0000-000000000011"
PAPER = "00000000-0000-0000-0000-000000000033"
OLD = r"""
import sys,sqlite3
from ipaper.database.models import SCHEMA_SCRIPT
from ipaper.database import connection
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.metadata.store import MetadataStore
from ipaper.security.identity import Identity,run_as_identity
connection.DB_PATH='/check/ipaper.db';owner,paper,mode=sys.argv[1:]
with sqlite3.connect(connection.DB_PATH) as db:
 db.executescript(SCHEMA_SCRIPT)
 db.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,'unusable','user','active',0,1,1,1)",(owner,'synthetic','synthetic'))
def action():
 if mode=='seed':
  for pid in (paper,'remove-during-rollback'):
   PaperDAO.save_paper({'id':pid,'title':'Quasar spectroscopy and telescope calibration','abstract':'Quasar spectroscopy measures redshift. Telescope calibration removes wavelength drift.','file_path':'/data/papers/stable.pdf','filename':'stable.pdf','notes':'before'})
 else:
  store=MetadataStore(connection.DB_PATH,owner);head=store.get(paper)
  store.edit(paper,{'title':'Spectral calibration after rollback','abstract':''},head['revision'])
  value=PaperDAO.get_paper(paper);value.update(notes='Preserve new user data',read_time=42);PaperDAO.save_paper(value)
  PaperDAO.save_paper({'id':'added-after-rollback','title':'Adaptive telescope calibration','abstract':'Adaptive telescope calibration corrects wavelength drift.','file_path':'/data/papers/new.pdf','filename':'new.pdf'})
  PaperDAO.delete_paper('remove-during-rollback')
run_as_identity(Identity(owner,'synthetic','user'),action);connection.close_db()
"""
CHILD = r"""
import sys,time
from pathlib import Path
from ipaper.keywords.service import KeywordService
import ipaper.keywords.service as module
path,owner,bid,pause=sys.argv[1:]
if pause!='-':
 original=module.extract
 def paused(*a,**kw):
  Path(pause).write_text('started');time.sleep(120);return original(*a,**kw)
 module.extract=paused
service=KeywordService(path,None);service.start()
try:
 deadline=time.monotonic()+50
 while time.monotonic()<deadline:
  result=service.store(owner).batch(bid)
  if result['status']!='running':print(result['status']);break
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
    assert info["Config"]["Labels"]["org.opencontainers.image.version"] == "1.5.0"

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
            timeout=40,
        )

    old("seed")
    path = dest / "ipaper.db"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA_SCRIPT)
    service = KeywordService(path, None)
    service.reconcile()
    store = service.store(OWNER)
    store.edit_papers([PAPER], "add", name="Quasar spectroscopy")
    removed = store.get(PAPER)["tags"][0]["id"]
    store.edit_papers([PAPER], "remove", tag_id=removed)
    store.edit_papers([PAPER], "add", name="组会候选")
    bid = store.create([PAPER])
    marker = dest / "started"
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(path), OWNER, bid, str(marker)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 25
        while not marker.exists() and time.monotonic() < deadline:
            assert proc.poll() is None
            time.sleep(0.1)
        assert marker.exists(), "worker_never_started"
    finally:
        proc.kill()
        proc.wait(timeout=5)
    subprocess.run(
        [sys.executable, "-c", CHILD, str(path), OWNER, bid, "-"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    assert store.batch(bid)["counts"] == {"completed": 1}
    assert removed not in [t["id"] for t in store.get(PAPER)["tags"]]
    with store.connection(True) as db:
        db.execute("DELETE FROM keyword_pending")
    old("write")
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA_SCRIPT)
    service.reconcile()
    MetadataStore(path, OWNER).reconcile()
    with store.connection() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        row = store.paper(db, PAPER)
        assert (
            row["title"] == "Spectral calibration after rollback"
            and row["abstract"] == ""
        )
        assert row["notes"] == "Preserve new user data" and row["read_time"] == 42
        assert (
            row["file_path"] == "/data/papers/stable.pdf"
            and row["filename"] == "stable.pdf"
        )
        assert (
            store.paper(db, "added-after-rollback")["title"]
            == "Adaptive telescope calibration"
        )
        assert not db.execute(
            "SELECT 1 FROM keyword_links WHERE paper_id='remove-during-rollback'"
        ).fetchone()
        assert db.execute(
            "SELECT 1 FROM keyword_exclusions WHERE paper_id=? AND tag_id=?",
            (PAPER, removed),
        ).fetchone()
        assert db.execute(
            "SELECT 1 FROM keyword_pending WHERE paper_id='added-after-rollback'"
        ).fetchone()
    assert any(
        t["name"] == "组会候选" and t["manual"] for t in store.get(PAPER)["tags"]
    )
    result = {
        "passed": True,
        "oldImage": a.old_image,
        "oldImageId": info["Id"],
        "roundTrip": "1.5.0 → 1.6.0 → 1.5.0 writes → 1.6.0",
        "actualProcessKillAndRecovery": True,
        "manualExclusionPreserved": True,
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
