"""Real Flask authentication and durable DB workflow; bibliographic supplier is fake."""
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from ipaper.metadata.routes import register_metadata_routes
from ipaper.metadata.service import MetadataService
from ipaper.metadata.providers import ProviderFailure
from ipaper.metadata.model import MetadataError
from ipaper.metadata.store import MetadataStore
from ipaper.processing.pipeline import file_digest
from ipaper.security.identity import Identity, run_as_identity
from tests.workbench_support import make_workbench_fixture


@pytest.fixture
def metadata_app(tmp_path, monkeypatch):
    from ipaper.database import connection
    connection.close_db()
    app, user, other = make_workbench_fixture(tmp_path, monkeypatch, count=3, cold=True)
    def forbidden(*a, **k):
        raise AssertionError('No processing, models, MinerU or OCR in metadata fixture')
    service = MetadataService(tmp_path/'ipaper.db', SimpleNamespace(pipeline=forbidden))
    register_metadata_routes(app, service)
    def inspect(store, paper_id, check):
        check()
        with store.connection() as db:
            from pathlib import Path
            path=Path(store.paper(db,paper_id)['file_path'])
        return file_digest(path), {'first_page_text':'Learning to read the world: a unified framework for embodied reasoning\nChen Wang, Maya Lee, Alex Kim'}, path
    monkeypatch.setattr(service,'inspect',inspect)
    client=app.test_client()
    response=client.post('/api/auth/login',json={'username':'reader_one','password':'workbench-test-pass'})
    assert response.status_code==200
    token=client.get_cookie('paperpilot_csrf').value
    yield app,service,client,{'X-CSRF-Token':token},user,other
    service.shutdown()
    connection.close_db()


def get_item(service,owner,paper='a-0'):
    with service.store(owner).connection() as db:
        return dict(db.execute('SELECT * FROM metadata_items WHERE owner_id=? AND paper_id=? ORDER BY created_at DESC LIMIT 1',(owner,paper)).fetchone())


def good_response(title):
    return {'result':{'hits':{'hit':[{'info':{'key':'conf/synthetic/2026','title':title,'authors':{'author':['Chen Wang','Maya Lee','Alex Kim']},'venue':'Synthetic Conference','type':'Conference and Workshop Papers','year':'2026'}}]}}}


def test_auth_edit_revision_csrf_bibtex_and_unknown_requests(metadata_app):
    _,service,client,headers,user,other=metadata_app
    assert client.get('/api/paper/b-0/metadata').status_code==404
    assert client.patch('/api/paper/a-0/metadata',json={'revision':1,'fields':{'title':'Attack'}}).status_code==403
    first=client.get('/api/paper/a-0/metadata').json
    response=client.patch('/api/paper/a-0/metadata',json={'revision':first['revision'],'fields':{'title':'A & B 中文','abstract':''}},headers=headers)
    assert response.status_code==200 and response.json['provenance']['abstract']['manual']
    assert client.patch('/api/paper/a-0/metadata',json={'revision':first['revision'],'fields':{'title':'Stale'}},headers=headers).status_code==409
    reference=client.get('/api/paper/a-0/bibtex').json['bibtex']
    download=client.get('/api/paper/a-0/bibtex?download=1')
    assert download.get_data(as_text=True)==reference and 'A \\& B 中文' in reference
    assert download.headers['Cache-Control']=='private, no-store'
    assert client.post('/api/metadata/jobs',json={'paperIds':[{}]},headers=headers).status_code==400
    assert client.post('/api/metadata/jobs',json={'paperIds':['a-0'],'force':{}},headers=headers).status_code==400
    assert client.get('/api/paper/a-0/metadata').json['fields']['abstract']==''
    client.delete('/api/auth/session',headers=headers)
    assert client.get('/api/paper/a-0/metadata').status_code==401


def test_late_job_clear_cancel_delete_and_index_failure(metadata_app,monkeypatch):
    _,service,client,headers,user,_=metadata_app
    store=service.store(user['id']);calls=[]
    title=store.get('a-0')['fields']['title']
    class HTTP:
        def __init__(self,*a):pass
        def get(self,*a,**k):
            calls.append(a)
            store.edit('a-0',{'journal':''})
            return good_response(title)
    service.http_factory=HTTP
    item=get_item(service,user['id']);service.run(item['id'],user['id'])
    assert get_item(service,user['id'])['status']=='stale'
    assert store.get('a-0')['fields']['journal']=='' and len(calls)==1
    batch=store.create(['a-1'],force=True);store.cancel(batch)
    with pytest.raises(MetadataError):store.apply('a-1',{'fields':{'journal':'late'},'provider':'dblp','url':'https://dblp.org/'},1,item_id=get_item(service,user['id'],'a-1')['id'])
    service.search_index=SimpleNamespace(index_paper=Mock(side_effect=RuntimeError('temporary index failure')),remove_paper=Mock())
    service.sync_indexes()
    assert store.get('a-0')['indexPending']
    from ipaper.database.dao.paper_dao import PaperDAO
    run_as_identity(Identity(user['id'],user['username'],user['role']),PaperDAO.delete_paper,'a-0')
    with pytest.raises(MetadataError):store.apply('a-0',{'fields':{'title':'must not resurrect'},'provider':'dblp','url':'https://dblp.org/'},2)


def test_retry_resumes_checkpoint_and_provider_budget(metadata_app):
    _,service,client,headers,user,_=metadata_app
    store=service.store(user['id']);calls=[]
    class HTTP:
        def __init__(self,store,before,check):self.before=before
        def get(self,provider,*a,**k):
            self.before();calls.append(provider)
            raise ProviderFailure('metadata_timeout',retryable=True,delay=1)
    service.http_factory=HTTP
    item=get_item(service,user['id'])
    for _ in range(5):service.run(item['id'],user['id'])
    current=get_item(service,user['id'])
    assert current['status']=='failed' and current['requests']==6 and len(calls)==6
    retried=store.retry(item['batch_id'])
    assert retried!=item['batch_id']
    assert service.store(user['id']).batch(retried)['total']==1


def test_all_pages_selection_and_duplicates_owner_scoped(metadata_app):
    _,service,client,headers,user,_=metadata_app
    from ipaper.database.dao.paper_dao import PaperDAO
    def seed():
        base=PaperDAO.get_paper('a-0')
        for i in range(55):PaperDAO.save_paper({**base,'id':f'bulk-{i}','title':f'Bulk paper {i}','arxiv_id':'2401.00001v1'})
    run_as_identity(Identity(user['id'],user['username'],user['role']),seed)
    response=client.post('/api/metadata/preview',json={'selection':{'scope':'all','query':'Bulk paper'}},headers=headers)
    assert response.status_code==200 and response.json['count']==55
    task=client.post('/api/metadata/jobs',json={'paperIds':response.json['paperIds']},headers=headers).json
    page=client.get('/api/metadata/jobs/'+task['id']).json
    assert page['total']==55 and len(page['items'])==50 and page['next']==50
    assert len(client.get('/api/metadata/jobs/'+task['id']+'?after=50').json['items'])==5
    dup=client.get('/api/paper/bulk-0/duplicates').json['items']
    assert len(dup)==54 and all(d['paperId'].startswith('bulk-') for d in dup)


def test_process_restart_resumes_only_unfinished_readonly_item(metadata_app,tmp_path):
    import subprocess,sys
    _,service,_,_,user,_=metadata_app
    store=service.store(user['id'])
    with store.connection(True) as db:
        db.execute("UPDATE metadata_items SET status='cancelled' WHERE paper_id!='a-0'")
    script=r'''
import sys,os,time,json,sqlite3
from pathlib import Path
from types import SimpleNamespace
from ipaper.database import connection
from ipaper.metadata.service import MetadataService
from ipaper.processing.pipeline import file_digest
from ipaper.security.identity import Identity,run_as_identity
from tests.test_metadata_jobs import good_response
path,owner,mode=sys.argv[1:];connection.DB_PATH=path
service=MetadataService(path,SimpleNamespace())
def inspect(store,paper_id,check):
    with store.connection() as db:
        paper=store.paper(db,paper_id);source=Path(paper['file_path'])
    return file_digest(source),{},source
service.inspect=inspect
class HTTP:
    def __init__(self,store,before,check):self.before=before;self.store=store
    def get(self,*a,**k):
        self.before()
        if mode=='interrupt':os._exit(17)
        return good_response(self.store.get('a-0')['fields']['title'])
service.http_factory=HTTP
service.start()
for i in range(200):
    with sqlite3.connect(path) as db:
        state=db.execute("SELECT status FROM metadata_items WHERE owner_id=? AND paper_id='a-0'",(owner,)).fetchone()[0]
    if state in ('completed','unchanged','failed'):break
    time.sleep(.05)
service.shutdown()
print(state)
'''
    first=subprocess.run([sys.executable,'-c',script,service.db_path,user['id'],'interrupt'],capture_output=True,text=True,timeout=15)
    assert first.returncode==17
    item=get_item(service,user['id']);assert item['status']=='running' and item['requests']==1
    second=subprocess.run([sys.executable,'-c',script,service.db_path,user['id'],'recover'],capture_output=True,text=True,timeout=20)
    assert second.returncode==0,second.stderr
    item=get_item(service,user['id']);assert item['status']=='completed' and item['requests']==2
    assert store.get('a-0')['fields']['journal']=='Synthetic Conference'
    completed_requests=item['requests']
    third=subprocess.run([sys.executable,'-c',script,service.db_path,user['id'],'recover'],capture_output=True,text=True,timeout=20)
    assert third.returncode==0 and get_item(service,user['id'])['requests']==completed_requests


def test_backup_includes_manual_protection_and_queue_evidence(metadata_app,tmp_path):
    from ipaper.processing.maintenance import backup
    _,service,_,_,user,_=metadata_app
    store=service.store(user['id']);store.edit('a-0',{'journal':'','doi':'10.9999/test'})
    report=backup(service.db_path,tmp_path/'papers',tmp_path/'metadata-backup')
    assert report['databaseVerified']
    saved=MetadataStore(tmp_path/'metadata-backup/ipaper.db',user['id']).get('a-0')
    assert saved['fields']['journal']=='' and saved['provenance']['journal']['manual']
    assert saved['fields']['doi']=='10.9999/test' and saved['task']['status']=='queued'
