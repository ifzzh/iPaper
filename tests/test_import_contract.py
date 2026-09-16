import io
from tests.workbench_support import make_workbench_fixture
from tests.test_workbench import login
import app as app_module
from ipaper.routes.basic_routes import import_route
from ipaper.runtime.task_queue import QueueFull

class Client:
    def __init__(self):self.cancelled=[];self.cleaned=[]
    def health(self):return True
    def stage(self,*args):pass
    def create(self,*args):pass
    def cancel(self,task):self.cancelled.append(task)
    def cleanup(self,task):self.cleaned.append(task)

class Queue:
    def __init__(self):self.calls=[];self.full=False
    def submit(self,*args):
        if self.full:raise QueueFull('full')
        self.calls.append(args)

def fixture(tmp_path,monkeypatch):
    app,_,_=make_workbench_fixture(tmp_path,monkeypatch)
    client=Client();queue=Queue()
    monkeypatch.setattr(import_route,'_import_workers',queue)
    monkeypatch.setattr(import_route,'import_tasks',{})
    monkeypatch.setattr(import_route,'current_import_task_id',None)
    import_route.register_import_routes(app,get_categories=lambda:{'id':'root','name':'Root','children':[]},save_categories=lambda *_:None,get_category_path=lambda *_:['Root'],create_category_folder=lambda *_:str(tmp_path),save_paper_metadata=lambda *_:None,reading_list_file=str(tmp_path/'reading.json'),paper_store=app_module.paper_store,upload_folder=str(tmp_path/'papers'),document_client=client)
    return app,client,queue

def test_import_owner_isolation_and_rdf_target_contract(tmp_path,monkeypatch):
    app,worker,queue=fixture(tmp_path,monkeypatch);one,two=app.test_client(),app.test_client();csrf=login(one);other=login(two,'reader_two')
    result=one.post('/api/import/zotero',data={'file':(io.BytesIO(b'<rdf/>'),'test.rdf'),'target_category_id':'root'},headers={'X-CSRF-Token':csrf});assert result.status_code==202
    ident=result.json['task_id'];assert queue.calls[0][2]=='root'
    assert two.get('/api/import/zotero/status').json=={'has_task':False}
    assert two.get('/api/import/zotero/progress/'+ident).status_code==404
    assert two.post('/api/import/zotero/cancel/'+ident,headers={'X-CSRF-Token':other}).status_code==404
    import_route.import_tasks[ident]['status']='completed'
    stream=one.get('/api/import/zotero/progress/'+ident);assert stream.mimetype=='text/event-stream' and b'"status": "completed"' in stream.data
    assert not worker.cancelled

def test_full_import_queue_reports_failure_and_cleans_worker(tmp_path,monkeypatch):
    app,worker,queue=fixture(tmp_path,monkeypatch);queue.full=True;c=app.test_client();csrf=login(c)
    r=c.post('/api/import/zotero',data={'file':(io.BytesIO(b'<rdf/>'),'test.rdf')},headers={'X-CSRF-Token':csrf})
    assert r.status_code==429 and r.json['error']=='import_queue_full'
    assert len(worker.cancelled)==len(worker.cleaned)==1

def test_import_executor_propagates_submitter_identity():
    import uuid
    from ipaper.security.identity import Identity, current_user_id, run_as_identity
    owner=Identity(str(uuid.uuid4()),'submitter','user')
    future=run_as_identity(owner,lambda:import_route._import_workers.submit(current_user_id))
    assert future.result(timeout=5)==owner.user_id


def test_import_topics_are_validated_before_worker_and_passed_separately(tmp_path,monkeypatch):
    from ipaper.database import connection
    from ipaper.topics.store import TopicStore
    app,worker,queue=fixture(tmp_path,monkeypatch);client=app.test_client();csrf=login(client)
    with app.app_context():
        row=connection.get_db().execute("SELECT id FROM users WHERE username='reader_one'").fetchone()
        topic=TopicStore(connection.DB_PATH,row[0]).create('逻辑主题')['id']
    import json
    denied=client.post('/api/import/zotero',data={'file':(io.BytesIO(b'<rdf/>'),'test.rdf'),'topicIds':json.dumps(['unknown'])},headers={'X-CSRF-Token':csrf})
    assert denied.status_code==404 and not queue.calls
    accepted=client.post('/api/import/zotero',data={'file':(io.BytesIO(b'<rdf/>'),'test.rdf'),'target_category_id':'root','topicIds':json.dumps([topic])},headers={'X-CSRF-Token':csrf})
    assert accepted.status_code==202 and queue.calls[0][2]=='root' and queue.calls[0][3]==[topic]
