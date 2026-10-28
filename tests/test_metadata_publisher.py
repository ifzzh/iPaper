import json
from unittest.mock import Mock
import pytest
from ipaper.metadata.providers import clues, queries, match, BibliographicHTTP, ProviderFailure
from ipaper.metadata.publisher import usenix_payload, usenix_records, usenix_path
from tests.test_metadata_http import transport, Response

TITLE='Rajomon: Decentralized and Coordinated Overload Control for Latency-Sensitive Microservices'
COVER='This paper is included in the Proceedings of NSDI 25.\n'+TITLE+'\nJiali Xing, Akis Giannoukos\nhttps://www.usenix.org/conference/nsdi25/presentation/xing\n'
HTML='''<meta name="citation_title" content="Rajomon: Decentralized and Coordinated Overload Control for {Latency-Sensitive} Microservices">
<meta name="citation_author" content="Jiali Xing"><meta name="citation_author" content="Akis Giannoukos">
<meta name="citation_publication_date" content="2025"><meta name="citation_conference_title" content="NSDI 25">
<meta name="citation_firstpage" content="21"><meta name="citation_lastpage" content="36">
<div class="field-name-field-paper-description"><div><p>We present RAJOMON, a market-based overload control system for large microservice graphs.</p></div></div>'''


def test_cover_publisher_url_beats_filename_and_requires_identity():
    clue=clues({'title':'nsdi25-xing'},{'first_page_text':COVER})
    assert queries(clue)==[('usenix','/conference/nsdi25/presentation/xing',{})]
    records=usenix_records({**usenix_payload(HTML),'url':'https://www.usenix.org/conference/nsdi25/presentation/xing'})
    paper=records[0]
    assert paper['fields']['title']==TITLE and paper['fields']['pages']=='21--36'
    assert paper['fields']['abstract'].startswith('We present RAJOMON')
    assert match(paper,clue)=='verified'
    assert match(paper,{'title':'nsdi25-xing','header':'Another paper by Jiali Xing'})!='verified'
    assert match(paper,{'title':TITLE,'authors':'Someone else'})!='verified'


@pytest.mark.parametrize('url',[
 'https://www.usenix.org.evil.invalid/conference/nsdi25/presentation/xing',
 'http://www.usenix.org/conference/nsdi25/presentation/xing',
 'https://user@www.usenix.org/conference/nsdi25/presentation/xing',
 'https://www.usenix.org:443/conference/nsdi25/presentation/xing',
 'https://www.usenix.org/conference/nsdi25/presentation/xing?redirect=http://127.0.0.1',
])
def test_publisher_url_never_expands_outbound_targets(url):
    assert usenix_path(url) is None
    assert 'usenix' not in clues({'title':'paper','source_url':url})


def test_embedded_title_can_replace_filename_lookup_but_not_identity_evidence():
    clue=clues({'title':'download-123'}, {'metadata':{'title':'Reliable network systems'},'first_page_text':'Reliable network systems\nAda Example\nAbstract'})
    assert clue['title']=='Reliable network systems'
    misleading=clues({'title':'download-123'}, {'metadata':{'title':'Unrelated title'},'first_page_text':'Actual title\nAda Example'})
    assert misleading['title']=='download-123'


def test_public_html_get_is_bounded_cached_and_never_redirects(transport):
    store,http,session,before=transport(Response(content=HTML.encode(),headers={'Content-Type':'text/html; charset=utf-8'}))
    first=http.get('usenix','/conference/nsdi25/presentation/xing')
    second=http.get('usenix','/conference/nsdi25/presentation/xing')
    assert first==second and before.call_count==1
    assert session.get.call_args.kwargs['headers']['Accept']=='text/html'
    assert session.get.call_args.kwargs['allow_redirects'] is False
    assert len(usenix_records(first))==1


def test_publisher_challenge_is_not_cached_as_a_missing_paper(transport):
    store,http,session,before=transport(Response(content=b'<html>Challenge</html>',headers={'Content-Type':'text/html'}))
    with pytest.raises(ProviderFailure) as error:
        http.get('usenix','/conference/nsdi25/presentation/xing')
    assert error.value.code=='metadata_invalid_response'
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM metadata_http_cache').fetchone()[0]==0
