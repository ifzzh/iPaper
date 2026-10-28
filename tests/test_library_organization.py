import json
from unittest.mock import Mock
import pytest
from tests.test_keywords import keyword_app, run_batch
from ipaper.metadata.store import MetadataStore
from ipaper.keywords.organize import saved_summary
from ipaper.keywords.store import observed_signature
from ipaper.topics.service import TopicService
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.security.identity import Identity, run_as_identity

SUMMARY = '这篇论文研究图神经网络在分子性质预测中的应用，通过沿分子键传递节点信息来建立分子表征，并使用对比学习改善不同化学骨架上的泛化能力。提供的摘要没有报告具体实验数值，因此这里不补充未提供的结论。'
ABSTRACT = 'We study graph neural networks for molecular property prediction. Contrastive learning improves molecular representations across scaffold splits. ' * 2


@pytest.fixture
def organized(keyword_app):
    app, service, client, headers, user, other = keyword_app
    store=MetadataStore(service.db_path,user['id'])
    store.edit('a-0',{'title':'Graph neural networks for molecules','abstract':ABSTRACT})
    service.configuration=lambda owner: ({'model':'fixture'}, {'model':'fixture','revision':1})
    service.topics=TopicService(service.db_path,None,source_reader=service)
    calls=[]
    def model(profile,messages,limit,deadline):
        calls.append(messages)
        if '"summary"' in messages[0]['content']:
            return {'status':'completed','text':json.dumps({'summary':SUMMARY})}
        # The summary must already be durable before any tag request starts.
        with service.store(user['id']).connection() as db:
            assert saved_summary(db,user['id'],service.store(user['id']).paper(db,'a-0'))==SUMMARY
        return {'status':'completed','text':json.dumps([{'name':'图神经网络','quote':'graph neural networks'}])}
    service.model_request=model
    yield app,service,client,headers,user,other,calls
    service.topics.shutdown()


def start(service,owner):
    preview=service.prepare(owner,['a-0'],'organize')
    bid=service.create(owner,['a-0'],'organize',preview['previewKey'])
    return preview,bid


def test_summary_then_grounded_tags_then_topics_and_cached_repeat(organized):
    _,s,c,h,u,o,calls=organized
    preview,bid=start(s,u['id'])
    assert preview['requests']==2
    result=run_batch(s,u['id'],bid)
    assert result['counts']=={'completed':1},result
    assert len(calls)==2
    listed=c.get('/api/library/papers').json['items']
    paper=next(p for p in listed if p['id']=='a-0')
    assert paper['summary']==SUMMARY and any(t['name']=='图神经网络' for t in paper['tags'])
    item=json.loads(result['items'][0]['result_json'])
    with s.store(u['id']).connection() as db:
        topic=db.execute('SELECT id FROM topic_items WHERE owner_id=? AND batch_id=?',(u['id'],item['topicBatch'])).fetchone()[0]
    s.topics.run(u['id'],topic)
    with s.store(u['id']).connection() as db:
        assert db.execute('SELECT status FROM topic_items WHERE id=?',(topic,)).fetchone()[0] in {'completed','reused','unorganized'}
    second,next_id=start(s,u['id'])
    assert second['requests']==0
    assert run_batch(s,u['id'],next_id)['counts']=={'completed':1}
    assert len(calls)==2


@pytest.mark.parametrize('response,error',[
 ({'status':'unknown'},'model_result_unknown'),
 ({'status':'completed','text':'{"summary":"English abstract only"}'},'organization_summary_invalid'),
 ({'status':'completed','text':'not JSON'},'model_output_invalid')])
def test_invalid_or_unknown_summary_never_runs_tags(organized,response,error):
    _,s,c,h,u,o,_=organized
    s.model_request=Mock(return_value=response)
    _,bid=start(s,u['id']);result=run_batch(s,u['id'],bid)
    assert result['items'][0]['error']==error
    assert s.model_request.call_count==1
    assert c.get('/api/paper/a-0/tags').json['tags']==[]
    with s.store(u['id']).connection() as db:
        assert db.execute('SELECT count(*) FROM library_summaries').fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM topic_items WHERE status IN ('completed','reused')").fetchone()[0]==0


def test_tag_failure_keeps_summary_retry_reuses_it(organized):
    _,s,c,h,u,o,calls=organized
    real=s.model_request
    def fail_tags(*args,**kwargs):
        if '"summary"' in args[1][0]['content']:return real(*args,**kwargs)
        return {'status':'unknown'}
    s.model_request=fail_tags
    _,bid=start(s,u['id']);failed=run_batch(s,u['id'],bid)
    assert failed['items'][0]['error']=='model_result_unknown'
    assert next(p for p in c.get('/api/library/papers').json['items'] if p['id']=='a-0')['summary']==SUMMARY
    s.model_request=real
    preview,retry=start(s,u['id'])
    assert preview['requests']==1
    assert run_batch(s,u['id'],retry)['counts']=={'completed':1}
    assert len(calls)==2 # one summary and one successful tag request


def test_preview_stale_owner_cancel_and_content_guards(organized):
    _,s,c,h,u,o,calls=organized
    assert c.post('/api/keywords/preview',json={'paperIds':['b-0'],'method':'organize'},headers=h).status_code==404
    preview=s.prepare(u['id'],['a-0'],'organize')
    MetadataStore(s.db_path,u['id']).edit('a-0',{'abstract':'A changed abstract '*12})
    assert c.post('/api/keywords/jobs',json={'paperIds':['a-0'],'method':'organize','previewKey':preview['previewKey']},headers=h).status_code==409
    _,bid=start(s,u['id']);s.store(u['id']).cancel(bid);run_batch(s,u['id'],bid)
    assert not calls
    MetadataStore(s.db_path,u['id']).edit('a-0',{'abstract':''})
    preview=s.prepare(u['id'],['a-0'],'organize')
    assert preview['items'][0]['needsContent']
    assert c.post('/api/keywords/jobs',json={'paperIds':['a-0'],'method':'organize','previewKey':preview['previewKey']},headers=h).status_code==409


def test_human_labels_and_exclusions_survive_and_stale_summary_hidden(organized):
    _,s,c,h,u,o,calls=organized
    store=s.store(u['id']);store.edit_papers(['a-0'],'add',name='组会必读')
    _,bid=start(s,u['id']);run_batch(s,u['id'],bid)
    assert any(t['name']=='组会必读' for t in c.get('/api/paper/a-0/tags').json['tags'])
    MetadataStore(s.db_path,u['id']).edit('a-0',{'abstract':'New evidence '*20})
    assert not next(p for p in c.get('/api/library/papers').json['items'] if p['id']=='a-0')['summary']


def test_auto_is_explicit_and_owner_scoped(organized):
    _,s,c,h,u,o,calls=organized
    assert c.get('/api/organization/settings').json=={'automatic':False}
    assert c.put('/api/organization/settings',json={'automatic':True}).status_code==403
    assert c.put('/api/organization/settings',json={'automatic':1},headers=h).status_code==400
    assert c.put('/api/organization/settings',json={'automatic':True},headers=h).status_code==200
    assert s.organization_settings(o['id'])=={'automatic':False}
    assert calls==[] # enabling and previewing never calls the model


def test_topic_job_waits_for_summary_and_tags(organized):
    _,s,c,h,u,o,calls=organized
    _,bid=start(s,u['id'])
    topics=s.topics.create(u['id'],['a-0'])
    with s.store(u['id']).connection() as db:
        row=db.execute('SELECT id FROM topic_items WHERE batch_id=?',(topics,)).fetchone()[0]
    s.topics.run(u['id'],row)
    with s.store(u['id']).connection() as db:
        assert db.execute('SELECT status FROM topic_items WHERE id=?',(row,)).fetchone()[0]=='queued'
    assert calls==[]


def test_preview_is_readonly_and_view_preference_persists(organized):
    _,s,c,h,u,o,calls=organized
    before=c.get('/api/keywords/jobs').json
    assert c.post('/api/keywords/preview',json={'paperIds':['a-0'],'method':'organize'},headers=h).status_code==200
    assert c.get('/api/keywords/jobs').json==before and calls==[]
    state=c.get('/api/workspace/state').json
    assert c.put('/api/workspace/state',json={**state,'libraryView':'cards'},headers=h).status_code==200
    assert c.get('/api/workspace/state').json['libraryView']=='cards'
    with s.store(u['id']).connection() as db:
        legacy=json.loads(db.execute("SELECT value FROM user_settings_v2 WHERE owner_id=? AND key='workspace_v1'",(u['id'],)).fetchone()[0])
        assert 'libraryView' not in legacy # old Web must still accept its state envelope
    assert c.put('/api/workspace/state',json={**state,'libraryView':'malformed'},headers=h).status_code==400


def test_daily_summary_reused_without_summary_request(organized):
    _,s,c,h,u,o,calls=organized
    def update():
        paper=PaperDAO.get_paper('a-0');paper['summary']=SUMMARY;paper['is_daily']=True
        PaperDAO.save_paper(paper)
    run_as_identity(Identity(u['id'],u['username'],u['role']),update)
    preview,bid=start(s,u['id'])
    assert preview['requests']==1 and preview['items'][0]['hasSummary']
    assert run_batch(s,u['id'],bid)['counts']=={'completed':1}
    assert len(calls)==1 and '"summary"' not in calls[0][0]['content']


def test_auto_waits_for_metadata_and_queues_same_summary_first_flow(organized):
    _,s,c,h,u,o,calls=organized
    s.organization_settings(u['id'],{'automatic':True})
    with s.store(u['id']).connection(True) as db:
        db.execute("UPDATE metadata_items SET status='running' WHERE owner_id=? AND paper_id='a-0'",(u['id'],))
    s.pending()
    with s.store(u['id']).connection(True) as db:
        assert not db.execute("SELECT 1 FROM keyword_items i JOIN keyword_batches b ON b.id=i.batch_id WHERE i.owner_id=? AND i.paper_id='a-0' AND b.method='organize'",(u['id'],)).fetchone()
        db.execute("UPDATE metadata_items SET status='completed' WHERE owner_id=? AND paper_id='a-0'",(u['id'],))
    s.pending()
    with s.store(u['id']).connection() as db:
        assert db.execute("SELECT 1 FROM keyword_items i JOIN keyword_batches b ON b.id=i.batch_id WHERE i.owner_id=? AND i.paper_id='a-0' AND b.method='organize'",(u['id'],)).fetchone()
    assert calls==[] # creating queued work is not a model request


def test_topic_queue_capacity_checked_before_any_model_cost(organized):
    _,s,c,h,u,o,calls=organized
    from ipaper.topics.store import TopicError
    s.topics.create=Mock(side_effect=TopicError('topic_queue_full',429))
    _,bid=start(s,u['id']);result=run_batch(s,u['id'],bid)
    assert result['items'][0]['error']=='topic_queue_full'
    assert calls==[] and result['items'][0]['requests']==0


def test_generated_summary_not_accepted_as_original_keyword_quote(organized):
    _,s,c,h,u,o,calls=organized
    real=s.model_request
    def model(profile,messages,limit,deadline):
        if '"summary"' in messages[0]['content']:return real(profile,messages,limit,deadline)
        return {'status':'completed','text':json.dumps([{'name':'未核实标签','quote':SUMMARY[:20]}])}
    s.model_request=model
    _,bid=start(s,u['id']);result=run_batch(s,u['id'],bid)
    assert result['items'][0]['error']=='keyword_evidence_invalid'
    assert c.get('/api/paper/a-0/tags').json['tags']==[]


def test_edit_during_generation_does_not_publish_stale_summary(organized):
    _,s,c,h,u,o,calls=organized
    real=s.model_request
    def model(*args,**kwargs):
        result=real(*args,**kwargs)
        MetadataStore(s.db_path,u['id']).edit('a-0',{'abstract':'Changed while generating '*12})
        return result
    s.model_request=model
    _,bid=start(s,u['id']);result=run_batch(s,u['id'],bid)
    assert result['items'][0]['error']=='keyword_source_changed' and len(calls)==1
    with s.store(u['id']).connection() as db:
        assert db.execute('SELECT count(*) FROM library_summaries').fetchone()[0]==0


def test_deleting_paper_removes_its_summary_only(organized):
    _,s,c,h,u,o,calls=organized
    _,bid=start(s,u['id']);run_batch(s,u['id'],bid)
    run_as_identity(Identity(u['id'],u['username'],u['role']),PaperDAO.delete_paper,'a-0')
    with s.store(u['id']).connection() as db:
        assert db.execute('SELECT count(*) FROM library_summaries').fetchone()[0]==0


def test_overlapping_batches_do_not_repeat_an_unknown_attempt(organized):
    _,s,c,h,u,o,calls=organized
    s.model_request=Mock(return_value={'status':'unknown'})
    _,first=start(s,u['id'])
    preview=s.prepare(u['id'],['a-0','a-1'],'organize')
    second=s.create(u['id'],['a-0','a-1'],'organize',preview['previewKey'])
    assert run_batch(s,u['id'],first)['counts']=={'interrupted':1}
    with s.store(u['id']).connection() as db:
        child=db.execute("SELECT id FROM keyword_items WHERE batch_id=? AND paper_id='a-0'",(second,)).fetchone()[0]
    s.run(child,u['id'])
    with s.store(u['id']).connection() as db:
        row=db.execute('SELECT status,error,requests FROM keyword_items WHERE id=?',(child,)).fetchone()
        assert tuple(row)==('interrupted','model_result_unknown',0)
    assert s.model_request.call_count==1
