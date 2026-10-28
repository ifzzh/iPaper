import json
import hashlib
import os
from pathlib import Path
import pytest
from tests.workbench_support import make_workbench_fixture
from tests.workbench_reader_support import fake_openai, install_reader_fixture
from tests.test_workbench import login


@pytest.fixture
def reader(tmp_path,monkeypatch):
    with fake_openai() as origin:
        app,a,b=make_workbench_fixture(tmp_path,monkeypatch,count=4)
        install_reader_fixture(app,tmp_path,(a,b),monkeypatch,origin)
        yield app


def test_private_pdf_range_and_errors(reader):
    a,b=reader.test_client(),reader.test_client()
    for path in ['/api/paper/a-0/file','/api/paper/a-0/chinese/file']:
        r=a.get(path);assert r.status_code==401;assert r.headers['Cache-Control']=='private, no-store'
    csrf=login(a);login(b,'reader_two')
    for suffix in ['file','chinese/file']:
        path=f'/api/paper/a-0/{suffix}'
        full=a.get(path);assert full.status_code==200
        assert full.headers['ETag']=='"'+hashlib.sha256(full.data).hexdigest()+'"'
        head=a.head(path);assert head.status_code==200;assert not head.data
        assert head.headers['Content-Length']==str(len(full.data))
        partial=a.get(path,headers={'Range':'bytes=10-29'})
        assert partial.status_code==206;assert partial.data==full.data[10:30]
        assert partial.headers['Content-Range']==f'bytes 10-29/{len(full.data)}'
        invalid=a.get(path,headers={'Range':f'bytes={len(full.data)+1}-'})
        assert invalid.status_code==416
        for response in [full,head,partial,invalid,b.get(path)]:
            assert response.headers['Cache-Control']=='private, no-store'
            assert 'Cookie' in response.headers['Vary']
        assert b.get(path).status_code==404
    assert a.get('/api/paper/a-3/file').status_code==404
    assert a.get('/api/paper/a-1/chinese/file').status_code==404
    assert a.get('/api/paper/unknown/file').status_code==404
    a.delete('/api/auth/session',headers={'X-CSRF-Token':csrf})
    assert a.get('/api/paper/a-0/file',headers={'Range':'bytes=0-10'}).status_code==401


@pytest.mark.parametrize('suffix,filename',[('file','a-0.pdf'),('chinese/file','a-0.zh.dual.pdf')])
def test_same_size_pdf_replacement_invalidates_head_and_if_match(reader,tmp_path,suffix,filename):
    client=reader.test_client();login(client)
    url='/api/paper/a-0/'+suffix
    before=client.get(url);assert before.status_code==200
    path=next(tmp_path.rglob(filename));info=path.stat()
    changed=before.data[:-1]+bytes([before.data[-1]^1]);path.write_bytes(changed)
    os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
    head=client.head(url);assert head.status_code==200
    assert head.headers['ETag']=='"'+hashlib.sha256(changed).hexdigest()+'"'
    assert head.headers['ETag']!=before.headers['ETag']
    assert client.get(url,headers={'If-Match':before.headers['ETag']}).status_code==412
    response=client.get(url,headers={'If-Match':head.headers['ETag'],'Range':'bytes=0-9'})
    assert response.status_code==206 and response.data==changed[:10]


def test_real_chat_persistence_errors_and_isolation(reader):
    a,b=reader.test_client(),reader.test_client();token=login(a);login(b,'reader_two')
    body={'paper_id':'a-0','messages':[{'role':'user','content':'hello'}]}
    assert a.post('/api/paper/chat',json=body).status_code==403
    response=a.post('/api/paper/chat',json=body,headers={'X-CSRF-Token':token})
    assert response.mimetype=='text/plain'
    header,answer=response.text.split('\n',1);sid=json.loads(header)['session_id']
    assert answer=='你好，这是合成回答。\n<img src=x onerror=alert(1)>'
    path=f'/api/paper/chat/session?paper_id=a-0&session_id={sid}'
    saved=a.get(path).json['session']['messages']
    assert [m['role'] for m in saved]==['user','assistant']
    assert saved[-1]['content']==answer
    assert b.get(path).status_code==404
    assert b.get('/api/paper/chat/sessions?paper_id=a-0').json['sessions']==[]
    for prompt in ['startup-failure','mid-failure']:
        result=a.post('/api/paper/chat',json={'paper_id':'a-0','messages':[{'role':'user','content':prompt}]},headers={'X-CSRF-Token':token})
        assert 'Error: llm_request_failed' in result.text
        sessions=a.get('/api/paper/chat/sessions?paper_id=a-0').json['sessions']
        session=next(s for s in sessions if s['title']==prompt)
        history=a.get(f"/api/paper/chat/session?paper_id=a-0&session_id={session['id']}").json['session']['messages']
        assert len(history)==1 and history[0]['role']=='user'


def test_disconnect_has_no_false_assistant_history(reader):
    c=reader.test_client();token=login(c)
    response=c.post('/api/paper/chat',json={'paper_id':'a-0','messages':[{'role':'user','content':'slow'}]},headers={'X-CSRF-Token':token},buffered=False)
    sid=json.loads(next(response.response))['session_id']
    next(response.response);response.close()
    history=c.get(f'/api/paper/chat/session?paper_id=a-0&session_id={sid}').json['session']['messages']
    assert [m['role'] for m in history]==['user']


def test_usage_only_tail_preserves_successful_answer(reader):
    c=reader.test_client();token=login(c)
    response=c.post('/api/paper/chat',json={'paper_id':'a-0','messages':[{'role':'user','content':'usage-tail'}]},headers={'X-CSRF-Token':token})
    header,answer=response.text.split('\n',1)
    assert 'llm_request_failed' not in answer
    sid=json.loads(header)['session_id']
    history=c.get(f'/api/paper/chat/session?paper_id=a-0&session_id={sid}').json['session']['messages']
    assert history[-1]['role']=='assistant' and history[-1]['content']==answer
