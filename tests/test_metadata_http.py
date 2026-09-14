import json
import sqlite3
from unittest.mock import Mock
import pytest
from ipaper.metadata.schema import SCHEMA
from ipaper.metadata.store import MetadataStore
from ipaper.metadata.providers import BibliographicHTTP, ProviderFailure

class Response:
    def __init__(self,status=200,content=None,headers=None):
        self.status_code=status;self.headers={'Content-Type':'application/json',**(headers or {})};self.content=content or b'{"message":{"items":[]}}'
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def iter_content(self,*a):yield self.content
class Session:
    def __init__(self,response):self.response=response;self.proxies={};self.get=Mock(return_value=response)
    def __enter__(self):return self
    def __exit__(self,*a):pass

@pytest.fixture
def transport(tmp_path):
    path=tmp_path/'test.db';sqlite3.connect(path).executescript(SCHEMA).close();store=MetadataStore(path,'test-owner')
    def make(response):
        session=Session(response);before=Mock();client=BibliographicHTTP(store,before,lambda:None,session_factory=lambda:session);client.policy=Mock()
        return store,client,session,before
    return make

@pytest.mark.parametrize('status,headers,raw,error',[
 (403,{},None,'metadata_access_denied'),(429,{'Retry-After':'15'},None,'metadata_rate_limited'),
 (302,{'Location':'http://127.0.0.1/secret'},None,'metadata_redirect_blocked'),
 (200,{'Content-Type':'text/html'},b'<html>Challenge</html>','metadata_invalid_response'),
 (200,{},b'{invalid','metadata_invalid_response'),(200,{},b'x'*(1024*1024+1),'metadata_response_too_large'),
])
def test_http_does_not_accept_errors_or_follow_redirects(transport,status,headers,raw,error):
    store,http,session,before=transport(Response(status,raw,headers))
    with pytest.raises(ProviderFailure) as caught:http.get('crossref','/works',{'query.bibliographic':'sample'})
    assert caught.value.code==error and before.call_count==1
    assert session.get.call_args.kwargs['allow_redirects'] is False
    assert session.trust_env is False
    with store.connection() as db:assert db.execute('SELECT count(*) FROM metadata_http_cache').fetchone()[0]==0

def test_cache_read_is_zero_calls_owner_isolated_and_pacing_persisted(transport):
    store,http,session,before=transport(Response(content=b'{"message":{"DOI":"10.9999/test","title":["Test"]}}'))
    first=http.get('crossref','/works/test');second=http.get('crossref','/works/test')
    assert first==second and before.call_count==1 and session.get.call_count==1
    with pytest.raises(ProviderFailure) as e:http.get('crossref','/works/other')
    assert e.value.code=='metadata_rate_limited' and before.call_count==1
    other=BibliographicHTTP(MetadataStore(store.db_path,'other'),before,lambda:None,session_factory=lambda:session);other.policy=Mock()
    with pytest.raises(ProviderFailure):other.get('crossref','/works/test')
    with store.connection() as db:assert db.execute('SELECT count(*) FROM metadata_http_cache WHERE owner_id=?',('other',)).fetchone()[0]==0

def test_arxiv_xml_entity_rejected(transport):
    store,http,_,_=transport(Response(headers={'Content-Type':'application/atom+xml'},content=b'<!DOCTYPE test [<!ENTITY x SYSTEM "file:///etc/passwd">]><feed>&x;</feed>'))
    with pytest.raises(ProviderFailure) as e:http.get('arxiv','/api/query',{'id_list':'2401.00001'})
    assert e.value.code=='metadata_invalid_response'

def test_configured_fake_ip_ranges_are_networks_not_characters(transport,monkeypatch):
    store,_,session,before=transport(Response())
    monkeypatch.setenv('IPAPER_AI_PROXY_FAKE_IP_RANGES','198.18.0.0/16, 198.19.0.0/16')
    assert BibliographicHTTP(store,before,lambda:None,session_factory=lambda:session).policy is not None
