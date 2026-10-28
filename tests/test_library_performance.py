"""Batch reads preserve exact adopted-input/owner semantics without N+1 SQL."""
import json
from tests.test_keywords import keyword_app
from ipaper.keywords.organize import saved_summary, saved_summaries
from ipaper.keywords.store import observed_signature
from ipaper.keywords.query import members
from ipaper.metadata.store import MetadataStore

SUMMARY = '这篇论文研究图神经网络在分子性质预测中的应用，通过节点之间的信息传递学习分子表征，并利用对比学习提高模型泛化能力。'

def test_batch_summaries_match_single_reads_and_bound_queries(keyword_app):
    _,service,_,_,user,other=keyword_app
    store=service.store(user['id'])
    with store.connection(True) as db:
        papers=members(db,user['id'],{})
        for p in papers:
            signature=observed_signature(db,user['id'],p)
            db.execute('INSERT INTO library_summaries VALUES(?,?,?,?,?,?,?)',(user['id'],p['id'],SUMMARY,signature,'key','fixture','now'))
        expected={p['id']:saved_summary(db,user['id'],p) for p in papers}
        calls=[];db.set_trace_callback(calls.append)
        assert saved_summaries(db,user['id'],papers)==expected
        assert len(calls)==2
        db.set_trace_callback(None)
        assert all(v==SUMMARY for v in expected.values())
        assert not any(saved_summaries(db,other['id'],papers).values())
    MetadataStore(service.db_path,user['id']).edit('a-0',{'title':'New adopted title'})
    with store.connection() as db:
        papers=members(db,user['id'],{})
        assert saved_summaries(db,user['id'],papers)=={p['id']:saved_summary(db,user['id'],p) for p in papers}
        assert saved_summaries(db,user['id'],papers)['a-0']==''
        assert saved_summaries(db,user['id'],[])=={}

def test_members_skips_daily_before_expensive_unpack(keyword_app,monkeypatch):
    _,service,_,_,user,_=keyword_app
    import ipaper.keywords.query as query
    real=query.unpack;seen=[]
    def unpack(row):seen.append(row['id']);return real(row)
    with service.store(user['id']).connection(True) as db:
        db.execute("UPDATE papers SET is_daily=1,file_path='/data/papers/.users/u/.daily_arxiv_temp/test.pdf' WHERE owner_id=? AND id='a-1'",(user['id'],))
        monkeypatch.setattr(query,'unpack',unpack)
        rows=query.members(db,user['id'],{})
        assert 'a-1' not in seen and len(rows)==2
