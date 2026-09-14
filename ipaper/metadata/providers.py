"""Bounded public bibliographic GETs; providers supply candidates, not identity decisions."""
from __future__ import annotations
import json
import re
import time
import threading
from urllib.parse import quote, urlsplit, parse_qs
import requests
import xml.etree.ElementTree as ET
from ipaper.environment import getenv
from ipaper.security.outbound import OutboundPolicy, OutboundPolicyError
from .model import MetadataError, encoded, fingerprint, normalized, doi, arxiv, safe_url

ORIGINS={'arxiv':'https://export.arxiv.org','crossref':'https://api.crossref.org',
         'dblp':'https://dblp.org','openreview':'https://api2.openreview.net','openreview_v1':'https://api.openreview.net'}
_LOCKS={p:threading.Lock() for p in ORIGINS}


class ProviderFailure(MetadataError):
    def __init__(self,code,*,retryable=False,delay=30):
        super().__init__(code,503);self.retryable,self.delay=retryable,min(max(delay,1),86400)


class BibliographicHTTP:
    def __init__(self,store,before_request,check,*,session_factory=None):
        self.store,self.before_request,self.check=store,before_request,check
        self.session_factory=session_factory or requests.Session
        self.policy=OutboundPolicy(public_origins=ORIGINS.values(),private_origins=[],transfer_origins=[],
            proxy_fake_ip_networks=[x.strip() for x in getenv('IPAPER_AI_PROXY_FAKE_IP_RANGES','').split(',') if x.strip()])

    def get(self,provider,path,params=None,*,ttl=30*86400):
        if provider not in ORIGINS or not path.startswith('/') or '?' in path or '#' in path:raise MetadataError('invalid_metadata_provider')
        key=fingerprint([provider,path,params]);now=time.time()
        with self.store.connection() as db:
            cached=db.execute('SELECT data_json FROM metadata_http_cache WHERE owner_id=? AND cache_key=? AND expires_at>?',(self.store.owner,key,now)).fetchone()
        if cached:return json.loads(cached[0])
        with _LOCKS[provider]:
            self.check()
            with self.store.connection() as db:
                limit=db.execute('SELECT next_request FROM metadata_provider_limits WHERE provider=?',(provider,)).fetchone()
            if limit and limit[0]>time.time():raise ProviderFailure('metadata_rate_limited',retryable=True,delay=limit[0]-time.time())
            self.before_request()
            with self.store.connection(True) as db:
                db.execute('INSERT OR REPLACE INTO metadata_provider_limits VALUES (?,?)',(provider,time.time()+(3.1 if provider=='arxiv' else 1.1)))
            url=ORIGINS[provider]+path
            try:
                self.policy.validate(url) # query fields are encoded separately, never interpreted as a destination
                with self.session_factory() as session:
                    session.trust_env=False
                    proxy=getenv('IPAPER_METADATA_PROXY','').strip()
                    if not proxy and provider=='arxiv':
                        from ipaper.tools.basic_tools.arxiv_network import get_arxiv_requests_proxies
                        session.proxies.update(get_arxiv_requests_proxies(url) or {})
                    elif proxy: session.proxies.update({'http':proxy,'https':proxy})
                    with session.get(url,params=params,headers={'User-Agent':'iPaper/1.5 (+https://github.com/ifzzh/iPaper)','Accept':'application/atom+xml' if provider=='arxiv' else 'application/json'},timeout=(5,15),allow_redirects=False,stream=True) as response:
                        if provider=='crossref':
                            try:
                                limit=float(response.headers.get('X-Rate-Limit-Limit',1));interval=float(response.headers.get('X-Rate-Limit-Interval','1s').rstrip('s'))
                                pace=max(1.1,interval/max(1,limit))
                                with self.store.connection(True) as db:db.execute('INSERT OR REPLACE INTO metadata_provider_limits VALUES (?,?)',(provider,time.time()+min(pace,86400)))
                            except ValueError:pass
                        if response.status_code==429:
                            try:delay=float(response.headers.get('Retry-After','60'))
                            except ValueError:delay=60
                            with self.store.connection(True) as db:db.execute('INSERT OR REPLACE INTO metadata_provider_limits VALUES (?,?)',(provider,time.time()+min(max(delay,1),86400)))
                            raise ProviderFailure('metadata_rate_limited',retryable=True,delay=delay)
                        if response.status_code in {401,403}:raise ProviderFailure('metadata_access_denied')
                        if response.status_code>=500:raise ProviderFailure('metadata_provider_unavailable',retryable=True)
                        if 300<=response.status_code<400:raise ProviderFailure('metadata_redirect_blocked')
                        if response.status_code==404:data={'missing':True}
                        elif response.status_code!=200:raise ProviderFailure('metadata_provider_rejected')
                        else:
                            content_type=response.headers.get('Content-Type','').lower()
                            if not any(x in content_type for x in (('xml','atom') if provider=='arxiv' else ('json',))):raise ProviderFailure('metadata_invalid_response')
                            chunks=[];size=0;started=time.monotonic()
                            for chunk in response.iter_content(16384):
                                self.check();size+=len(chunk)
                                if size>1024*1024:raise ProviderFailure('metadata_response_too_large')
                                if time.monotonic()-started>20:raise ProviderFailure('metadata_timeout',retryable=True)
                                chunks.append(chunk)
                            raw=b''.join(chunks)
                            if provider=='arxiv':
                                if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ProviderFailure('metadata_invalid_response')
                                try:
                                    root=ET.fromstring(raw)
                                    ns={'a':'http://www.w3.org/2005/Atom','x':'http://arxiv.org/schemas/atom'}
                                    if root.tag!='{http://www.w3.org/2005/Atom}feed':raise ValueError()
                                    entries=[]
                                    for entry in root.findall('a:entry',ns)[:8]:
                                        item={k:entry.findtext('a:'+k,default='',namespaces=ns) for k in ('id','title','summary','published','updated')}
                                        item.update({"arxiv_"+k:entry.findtext('x:'+k,default='',namespaces=ns) for k in ('doi','journal_ref')})
                                        item['authors']=[{'name':a.findtext('a:name',default='',namespaces=ns),'arxiv_affiliation':a.findtext('x:affiliation',default='',namespaces=ns)} for a in entry.findall('a:author',ns)]
                                        entries.append(item)
                                    data={'entries':entries}
                                except (ET.ParseError,ValueError):raise ProviderFailure('metadata_invalid_response') from None
                            else:
                                try:data=json.loads(raw)
                                except (ValueError,RecursionError):raise ProviderFailure('metadata_invalid_response') from None
                            if not isinstance(data,dict):raise ProviderFailure('metadata_invalid_response')
            except requests.Timeout:raise ProviderFailure('metadata_timeout',retryable=True) from None
            except requests.RequestException:raise ProviderFailure('metadata_network_failed',retryable=True) from None
            except OutboundPolicyError:raise ProviderFailure('metadata_outbound_blocked') from None
            empty=data.get('missing') or data.get('entries')==[] or data.get('notes')==[] or data.get('result',{}).get('hits',{}).get('@total')=='0' or data.get('message',{}).get('items')==[]
            with self.store.connection(True) as db:
                db.execute('DELETE FROM metadata_http_cache WHERE expires_at<?',(time.time(),))
                db.execute('DELETE FROM metadata_http_cache WHERE owner_id=? AND cache_key IN (SELECT cache_key FROM metadata_http_cache WHERE owner_id=? ORDER BY expires_at DESC LIMIT -1 OFFSET 999)',(self.store.owner,self.store.owner))
                db.execute('INSERT OR REPLACE INTO metadata_http_cache VALUES (?,?,?,?,?)',(self.store.owner,key,provider,encoded(data),time.time()+(86400 if empty else ttl)))
            return data


def _plain(value):
    # API fields may contain JATS markup. Never return it as HTML.
    from html import unescape
    return re.sub(r'\s+',' ',unescape(re.sub(r'<[^>]*>',' ',str(value or '')))).strip()


def _first(values):return _plain(values[0]) if isinstance(values,list) and values else _plain(values) if isinstance(values,str) else ''


def _date(value):
    try:return '-'.join(str(n).zfill(2) if i else str(n) for i,n in enumerate(value['date-parts'][0]))
    except (KeyError,TypeError,IndexError):return ''


def record(provider,url,fields,identifier='',*,relations=None):
    from .model import validate_patch
    return {'provider':provider,'url':safe_url(url),'externalId':identifier,'fields':validate_patch(fields),'relations':relations or []}


def crossref_records(data):
    if data.get('missing'):return []
    message=data.get('message',{});items=message.get('items') if isinstance(message,dict) else None
    if items is None:items=[message]
    output=[]
    for item in items[:8]:
        if not isinstance(item,dict) or not item.get('DOI'):continue
        try:
            authors=[]
            for a in item.get('author',[]):
                name=_plain(a.get('name')) or ' '.join(filter(None,(_plain(a.get('given')),_plain(a.get('family')))))
                if name:authors.append({'name':name,'kind':'organization' if a.get('name') and not a.get('family') else 'person','given':_plain(a.get('given')),'family':_plain(a.get('family')),'affiliations':[_plain(x.get('name')) for x in a.get('affiliation',[]) if x.get('name')]})
            date=_date(item.get('published',{})) or _date(item.get('published-online',{})) or _date(item.get('published-print',{}))
            identifier=doi(item['DOI']);venue={'journal-article':'journal','proceedings-article':'conference','posted-content':'preprint'}.get(item.get('type'),'other')
            output.append(record('crossref','https://doi.org/'+identifier,{'doi':identifier,'title':_first(item.get('title')), 'author_list':authors,
                'affiliation':'; '.join(dict.fromkeys(x for a in authors for x in a['affiliations'])),'abstract':_plain(item.get('abstract')),
                'journal':_first(item.get('container-title')),'venue_type':venue,'year':date[:4],'published_date':date if venue in {'journal','conference'} else '',
                'volume':_plain(item.get('volume')),'issue':_plain(item.get('issue')),'pages':_plain(item.get('page')),'source_url':'https://doi.org/'+identifier},identifier,relations=item.get('relation',{})))
        except (MetadataError,TypeError,AttributeError):continue
    return output


def arxiv_records(data):
    output=[]
    for item in data.get('entries',[])[:8]:
        try:
            base,version=arxiv(re.sub(r'^http:', 'https:', item['id']))
            people=[{'name':_plain(a.get('name')),'kind':'person','affiliations':[_plain(a['arxiv_affiliation'])] if a.get('arxiv_affiliation') else []} for a in item.get('authors',[]) if a.get('name')]
            published=str(item.get('published',''));url='https://arxiv.org/abs/'+base+('v'+version if version else '')
            fields={'title':_plain(item.get('title')),'author_list':people,'abstract':_plain(item.get('summary')),
                'arxiv_id':base,'arxiv_version':version,'preprint_date':published,'version_date':str(item.get('updated','')),
                'year':published[:4],'venue_type':'preprint','source_url':url,
                'affiliation':'; '.join(dict.fromkeys(x for a in people for x in a['affiliations']))}
            if item.get('arxiv_doi'):fields['doi']=doi(item['arxiv_doi'])
            output.append(record('arxiv',url,fields,base,relations={'journalReference':_plain(item.get('arxiv_journal_ref'))}))
        except (KeyError,MetadataError,TypeError):continue
    return output


def dblp_records(data):
    try:hits=data['result']['hits'].get('hit',[])
    except (KeyError,TypeError):return []
    if isinstance(hits,dict):hits=[hits]
    output=[]
    for hit in hits[:8]:
        try:
            item=hit['info'];values=item.get('authors',{}).get('author',[])
            if not isinstance(values,list):values=[values]
            names=[a.get('text','') if isinstance(a,dict) else a for a in values]
            fields={'title':_plain(item.get('title')),'author_list':[{'name':_plain(n),'kind':'person'} for n in names if n],
                'year':str(item.get('year','')),'journal':_first(item.get('venue')),'venue_type':{'Conference and Workshop Papers':'conference','Journal Articles':'journal','Informal Publications':'preprint'}.get(item.get('type'),'other'),
                'volume':str(item.get('volume','')),'pages':str(item.get('pages','')),'source_url':'https://dblp.org/rec/'+item['key']}
            if item.get('doi'):fields['doi']=doi(item['doi'])
            output.append(record('dblp',fields['source_url'],fields,item['key']))
        except (KeyError,MetadataError,TypeError,AttributeError):continue
    return output


def openreview_records(data):
    output=[]
    for item in data.get('notes',[])[:8]:
        c=item.get('content',{})
        def get(key):
            value=c.get(key,'');return value.get('value','') if isinstance(value,dict) else value
        # Never treat a review, decision or comment as a publication record.
        if item.get('replyto') or not get('title') or not get('abstract'):continue
        people=get('authors');people=people if isinstance(people,list) else []
        venue=_plain(get('venue'));venueid=_plain(get('venueid'))
        accepted=bool(venueid and re.search(r'\b(?:poster|oral|spotlight|accepted)\b',venue,re.I) and not re.search(r'submission|withdraw|reject|desk',venueid+' '+venue,re.I))
        date=''
        if item.get('pdate') and accepted:
            from datetime import datetime,timezone
            date=datetime.fromtimestamp(item['pdate']/1000,timezone.utc).date().isoformat()
        fields={'title':_plain(get('title')),'abstract':_plain(get('abstract')),'author_list':[{'name':_plain(n),'kind':'person'} for n in people if n and not re.fullmatch(r'anonymous(?: authors?)?',str(n).strip(),re.I)],
                'venue_type':'conference' if accepted else 'submission','journal':venue if accepted else '',
                'year':date[:4],'published_date':date,'source_url':'https://openreview.net/forum?id='+item['id']}
        links=[]
        for key,kind in [('code','code'),('project','project'),('project_page','project'),('data','data')]:
            if get(key):
                try:links.append({'kind':kind,'url':safe_url(str(get(key))),'source':fields['source_url']})
                except MetadataError:pass
        fields['links']=links
        try:output.append(record('openreview',fields['source_url'],fields,item['id']))
        except MetadataError:continue
    return output


def clues(fields,inspection=None,original_title=''):
    text=(inspection or {}).get('first_page_text') or ''
    header=re.split(r'\n\s*(?:references|bibliography|参考文献)\s*\n',text,flags=re.I)[0]
    result={'title':fields.get('title') or original_title,'authors':fields.get('authors',''),'year':fields.get('year',''),
            'doi':fields.get('doi',''),'arxiv_id':fields.get('arxiv_id',''),'arxiv_version':fields.get('arxiv_version',''),
            'header':header,'source_url':fields.get('source_url','')}
    for link in [result['source_url']]:
        p=urlsplit(link);host=(p.hostname or '').lower()
        if host in {'arxiv.org','export.arxiv.org'}:
            try:
                base,version=arxiv(link)
                if not result['arxiv_id'] or result['arxiv_id']==base:
                    result['arxiv_id']=base
                    if version:result['arxiv_version']=version
            except MetadataError:pass
        elif host in {'doi.org','dx.doi.org'}:
            try:result['doi']=doi(link)
            except MetadataError:pass
        elif host=='openreview.net':result['openreview']=(parse_qs(p.query).get('id') or [''])[0]
        elif host in {'dblp.org','dblp.uni-trier.de','dblp.dagstuhl.de'} and p.path.startswith('/rec/'):
            result['dblp_key']=re.sub(r'\.(?:html|bib|xml)$','',p.path[5:])
    if not result['arxiv_version']:
        m=re.search(r'arXiv\s*:\s*(\d{4}\.\d{4,5}(?:v\d+)?)',header,re.I)
        if m:
            base,version=arxiv(m[1])
            if not result['arxiv_id'] or result['arxiv_id']==base:result['arxiv_id'],result['arxiv_version']=base,version
    if not result['doi']:
        # A DOI is only a lookup clue; matching the candidate title in the
        # paper's own header is still mandatory before adopting its fields.
        found=re.findall(r'10\.\d{4,9}/[^\s<>"{}]+',header,re.I)
        if len(set(found))==1:
            try:result['doi']=doi(found[0].rstrip('.,;)'))
            except MetadataError:pass
    return result


def queries(clue):
    output=[]
    if clue.get('openreview'):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',clue['openreview']):return []
        return [('openreview','/notes',{'id':clue['openreview']})]
    if clue.get('arxiv_id'):
        return [('arxiv','/api/query',{'id_list':clue['arxiv_id']+('v'+clue['arxiv_version'] if clue.get('arxiv_version') else '')})]
    if clue.get('doi'):return [('crossref','/works/'+quote(clue['doi'],safe=''),{})]
    if clue.get('dblp_key'):return [('dblp','/search/publ/api',{'q':'key:'+clue['dblp_key'],'format':'json','h':8})]
    title=clue.get('title','')[:500];authors=clue.get('authors','')[:200]
    if len(normalized(title))<8:return []
    # Candidate search follows clues; no fan-out to every available service.
    if re.search(r'computer|learning|neural|agent|network|robot|memory|language|计算|学习|智能',title,re.I):
        output.append(('dblp','/search/publ/api',{'q':title,'format':'json','h':8}))
    output.append(('crossref','/works',{'query.bibliographic':title+' '+authors,'rows':8}))
    return output


PARSERS={'arxiv':arxiv_records,'crossref':crossref_records,'dblp':dblp_records,'openreview':openreview_records,'openreview_v1':openreview_records}


def match(record,clue):
    f=record['fields'];ct=normalized(f.get('title',''));title=normalized(clue.get('title',''));header=normalized(clue.get('header',''))
    title_match=bool(ct and len(ct)>=8 and (ct==title or ct in header))
    if not title_match:return 'candidate'
    if clue.get('arxiv_id') and f.get('arxiv_id')==clue['arxiv_id']:
        if clue.get('arxiv_version') and f.get('arxiv_version')!=clue['arxiv_version']:return 'related_version'
        return 'verified'
    if clue.get('doi') and f.get('doi')==clue['doi']:return 'verified'
    if clue.get('openreview') and record['externalId']==clue['openreview']:return 'verified'
    names=[normalized(a['name']) for a in f.get('author_list',[])];known=normalized(clue.get('authors',''))
    evidence=[n for n in names if n and (n in known or n in header)]
    if not evidence:return 'candidate'
    if clue.get('year') and f.get('year') and f['year']!=clue['year']:return 'related_version'
    if len(evidence)>=min(2,len(names)) and names:return 'verified'
    return 'candidate'


def combine_publication(records):
    """Keep version-specific preprint text; add only verified publication fields."""
    arxivs=[r for r in records if r['provider']=='arxiv']
    if len(arxivs)!=1:return records
    primary=arxivs[0];f=primary['fields']
    related=[r for r in records if r['provider']=='crossref' and f.get('doi') and r['fields'].get('doi')==f['doi']
             and normalized(r['fields'].get('title'))==normalized(f['title'])]
    if len(related)!=1:return records
    publication=related[0];names={normalized(a['name']) for a in f.get('author_list',[])}
    known={normalized(a['name']) for a in publication['fields'].get('author_list',[])}
    if not names or len(names & known)<min(2,len(names),len(known)) or not known:return records
    if publication['fields'].get('venue_type') not in {'journal','conference'}:return records
    merged={**primary,'fields':dict(f),'fieldSources':{},'relations':{**primary.get('relations',{}),'publication':publication['url']}}
    for key in ('journal','venue_type','volume','issue','pages','published_date','year'):
        if publication['fields'].get(key):
            merged['fields'][key]=publication['fields'][key]
            merged['fieldSources'][key]={'provider':'crossref','url':publication['url']}
    return [merged,*[r for r in records if r is not primary and r is not publication]]
