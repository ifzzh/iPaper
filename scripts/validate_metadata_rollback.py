#!/usr/bin/env python3
"""Rehearse metadata upgrade / old Web writes / re-upgrade with synthetic data only."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ipaper.database.models import SCHEMA_SCRIPT
from ipaper.metadata.store import MetadataStore

OWNER='00000000-0000-0000-0000-000000000011'
PAPER='00000000-0000-0000-0000-000000000033'
OLD_SCRIPT=r'''
import sys,sqlite3
from ipaper.database.models import SCHEMA_SCRIPT
from ipaper.database import connection
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.security.identity import Identity,run_as_identity
connection.DB_PATH='/check/ipaper.db'
owner,paper,mode=sys.argv[1:]
with sqlite3.connect(connection.DB_PATH) as db:
 db.executescript(SCHEMA_SCRIPT)
 db.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,'unusable','user','active',0,1,1,1)",(owner,'synthetic','synthetic'))
def action():
 if mode=='seed':
  PaperDAO.save_paper({'id':paper,'title':'Original title','authors':'Original group','abstract':'Original abstract','file_path':'/data/papers/stable.pdf','filename':'stable.pdf','notes':'before'})
 else:
  value=PaperDAO.get_paper(paper)
  assert value['abstract']=='' and value['doi']=='10.1000/synthetic'
  value.update(title='Changed using Web 1.4',authors='New old-version author',notes='User data after rollback',read_time=42)
  PaperDAO.save_paper(value)
  PaperDAO.save_paper({'id':'added-after-rollback','title':'Added using Web 1.4','file_path':'/data/papers/second.pdf','filename':'second.pdf'})
run_as_identity(Identity(owner,'synthetic','user'),action)
connection.close_db()
'''

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--old-image',required=True);p.add_argument('--output',required=True);a=p.parse_args()
 dest=Path(a.output).resolve();dest.mkdir(mode=0o700,parents=True,exist_ok=False)
 info=json.loads(subprocess.check_output(['docker','image','inspect',a.old_image]))[0]
 assert info['Config']['Labels']['org.opencontainers.image.version']=='1.4.0'
 def old(mode):
  subprocess.run(['docker','run','--rm','--network','none','--read-only','--tmpfs','/tmp','--user',f'{os.getuid()}:{os.getgid()}',
    '--volume',str(dest)+':/check','--env','IPAPER_DB_PATH=/check/ipaper.db',a.old_image,'python','-c',OLD_SCRIPT,OWNER,PAPER,mode],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
 old('seed');dbpath=dest/'ipaper.db'
 with sqlite3.connect(dbpath) as db:db.executescript(SCHEMA_SCRIPT)
 store=MetadataStore(dbpath,OWNER)
 current=store.get(PAPER)
 store.edit(PAPER,{'abstract':'','doi':'10.1000/synthetic','author_list':[{'name':'Explicit Research Consortium','kind':'organization'}]},current['revision'])
 old('write')
 with sqlite3.connect(dbpath) as db:db.executescript(SCHEMA_SCRIPT)
 store.reconcile();head=store.get(PAPER)
 assert head['fields']['title']=='Changed using Web 1.4'
 assert head['fields']['authors']=='New old-version author' and not head['fields']['author_list']
 assert head['fields']['abstract']=='' and head['provenance']['abstract']['manual']
 assert head['fields']['doi']=='10.1000/synthetic'
 with store.connection() as db:
  current=store.paper(db,PAPER)
  assert current['notes']=='User data after rollback' and current['read_time']==42
  assert current['file_path']=='/data/papers/stable.pdf' and current['filename']=='stable.pdf'
  assert store.paper(db,'added-after-rollback')['title']=='Added using Web 1.4'
  assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 result={'passed':True,'oldImage':a.old_image,'oldImageId':info['Id'],'syntheticOnly':True,'roundTrip':'1.4.0 → 1.5.0 → 1.4.0 writes → 1.5.0','manualClearPreserved':True,'oldVersionEditsPreserved':True,'newPaperPreserved':True,'pathsUnchanged':True,'supplierCalls':0}
 (dest/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
