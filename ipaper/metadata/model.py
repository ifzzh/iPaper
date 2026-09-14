from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote


class MetadataError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


TEXT_LIMITS = {'title': 2000, 'authors': 20000, 'affiliation': 12000, 'year': 4,
               'journal': 2000, 'abstract': 60000, 'doi': 512, 'arxiv_id': 100,
               'arxiv_version': 12, 'published_date': 40, 'preprint_date': 40,
               'version_date': 40, 'volume': 100, 'issue': 100, 'pages': 100,
               'venue_type': 30, 'source_url': 2048, 'github': 2048, 'homepage': 2048}
FIELDS = set(TEXT_LIMITS) | {'author_list', 'links'}
IDENTITY_FIELDS = {'title','authors','author_list','year','doi','arxiv_id','arxiv_version','source_url'}
LEGACY = {'title':'title','authors':'authors','affiliation':'affiliation','year':'year','journal':'journal',
          'abstract':'abstract','doi':'doi','arxiv_id':'arxiv_id','arxiv_version':'arxiv_version',
          'preprint_date':'arxiv_published_date','source_url':'arxiv_url','github':'github','homepage':'homepage',
          'author_list':'author_list','links':'project_links','published_date':'publication_date',
          'version_date':'arxiv_updated_date','venue_type':'venue_type','volume':'volume','issue':'issue','pages':'pages'}


def safe_url(value):
    if not value:
        return ''
    if not isinstance(value,str) or len(value)>2048 or any(ord(c)<33 or ord(c)==127 for c in value):
        raise MetadataError('invalid_metadata_url')
    try:
        p=urlsplit(value)
        if p.scheme not in {'http','https'} or not p.hostname or p.username is not None or p.password is not None or p.port not in {None,80,443}:
            raise ValueError()
        if '\\' in value:
            raise ValueError()
    except ValueError:
        raise MetadataError('invalid_metadata_url') from None
    return value


def doi(value):
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)', '', value.strip(), flags=re.I)
    value = unquote(value)
    if value and not re.fullmatch(r'10\.\d{4,9}/[^\s<>"{}]+',value,re.I):
        raise MetadataError('invalid_doi')
    return value.lower()


def arxiv(value):
    value=re.sub(r'^arxiv\s*:\s*','',value.strip(),flags=re.I)
    value=re.sub(r'^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/','',value.strip(),flags=re.I)
    value=re.sub(r'\.pdf$','',value,flags=re.I)
    if not value: return '', ''
    m=re.fullmatch(r'((?:\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[A-Z]{2})?/\d{7}))(?:v([1-9]\d{0,3}))?',value)
    if not m: raise MetadataError('invalid_arxiv_id')
    return m[1], m[2] or ''


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC',str(value)).casefold() if c.isalnum())


def validate_patch(data):
    if not isinstance(data,dict) or set(data)-FIELDS or len(encoded(data).encode())>100_000:
        raise MetadataError('invalid_metadata_fields')
    output={}
    for key,value in data.items():
        if key in TEXT_LIMITS:
            if not isinstance(value,str) or len(value)>TEXT_LIMITS[key] or '\x00' in value:
                raise MetadataError('invalid_metadata_field')
            value=value.strip()
            if key in {'source_url','homepage','github'}: value=safe_url(value)
            if key=='doi': value=doi(value)
            if key=='year' and value and not re.fullmatch(r'\d{4}',value): raise MetadataError('invalid_metadata_year')
            if key=='arxiv_version' and value and not re.fullmatch(r'[1-9]\d{0,3}',value): raise MetadataError('invalid_arxiv_id')
            if key in {'published_date','preprint_date','version_date'} and value:
                try:
                    if len(value)==4:datetime(int(value),1,1)
                    elif len(value)==7:datetime.strptime(value,'%Y-%m')
                    else:datetime.fromisoformat(value.replace('Z','+00:00'))
                except ValueError:raise MetadataError('invalid_metadata_date') from None
            if key=='venue_type' and value not in {'','journal','conference','preprint','submission','other'}: raise MetadataError('invalid_venue_type')
        elif key=='author_list':
            if not isinstance(value,list) or len(value)>1000: raise MetadataError('invalid_authors')
            for a in value:
                if not isinstance(a,dict) or set(a)-{'name','kind','given','family','affiliations'} or not isinstance(a.get('name'),str) or not 0<len(a['name'].strip())<=500 or a.get('kind','person') not in {'person','organization'}:
                    raise MetadataError('invalid_authors')
                if any(not isinstance(a.get(k,''),str) or len(a.get(k,''))>500 for k in ('given','family')): raise MetadataError('invalid_authors')
                aff=a.get('affiliations',[])
                if not isinstance(aff,list) or len(aff)>40 or any(not isinstance(x,str) or len(x)>1000 for x in aff): raise MetadataError('invalid_authors')
        else:
            if not isinstance(value,list) or len(value)>30: raise MetadataError('invalid_project_links')
            for link in value:
                if not isinstance(link,dict) or set(link)-{'kind','url','label','source'} or link.get('kind') not in {'project','code','data','source'}:
                    raise MetadataError('invalid_project_links')
                safe_url(link.get('url'))
                if not link.get('url') or any(not isinstance(link.get(k,''),str) or len(link.get(k,''))>2048 for k in ('label','source')): raise MetadataError('invalid_project_links')
        output[key]=value
    if 'arxiv_id' in output:
        base,version=arxiv(output['arxiv_id']); output['arxiv_id']=base
        if version: output['arxiv_version']=version
    if 'authors' in output and 'author_list' not in output:
        output['author_list']=[]
    if 'author_list' in output and 'authors' not in data:
        output['authors']='; '.join(a['name'].strip() for a in output['author_list'])
    return output


def legacy_fields(paper):
    result={key:paper.get(old) or ([] if key in {'author_list','links'} else '') for key,old in LEGACY.items()}
    for key in TEXT_LIMITS:
        if not isinstance(result[key],str): result[key]=str(result[key])
    for key in ('author_list','links'):
        if not isinstance(result[key],list): result[key]=[]
    try:
        result['arxiv_id'], ver=arxiv(result['arxiv_id'])
        if ver: result['arxiv_version']=ver
    except MetadataError: pass
    return result


def legacy_projection(fields):
    return {old:fields.get(key,[] if key in {'author_list','links'} else '') for key,old in LEGACY.items()}


def bibtex(paper_id, fields):
    def escape(text):
        return ''.join({'\\':r'\textbackslash{}','{':r'\{','}':r'\}','%':r'\%','&':r'\&','#':r'\#','_':r'\_','$':r'\$','~':r'\textasciitilde{}','^':r'\textasciicircum{}'}.get(c,c) for c in str(text))
    people=fields.get('author_list') or []
    authors=[]
    for a in people:
        name=(a['family']+', '+a['given']) if a.get('family') and a.get('given') else a['name']
        authors.append('{'+escape(name)+'}' if a.get('kind')=='organization' else escape(name))
    if not authors and fields.get('authors'):
        # Ambiguous legacy names remain a literal string, not invented people.
        authors=['{'+escape(fields['authors'])+'}']
    kind={'journal':'article','conference':'inproceedings'}.get(fields.get('venue_type'),'misc')
    values={'title':fields.get('title'),'author':' and '.join(authors),'year':fields.get('year'),
            'journal' if kind=='article' else 'booktitle' if kind=='inproceedings' else 'howpublished':fields.get('journal'),
            'volume':fields.get('volume'),'number':fields.get('issue'),'pages':fields.get('pages'),
            'doi':fields.get('doi'),'url':fields.get('source_url')}
    if fields.get('arxiv_id'):
        values.update(eprint=fields['arxiv_id']+('v'+fields['arxiv_version'] if fields.get('arxiv_version') else ''),archivePrefix='arXiv')
    lines=[f'@{kind}{{ipaper_{paper_id.replace("-", "")[:12]},']
    for key,value in values.items():
        if value: lines.append('  '+key+' = {'+(value if key=='author' else escape(value))+'},')
    return '\n'.join(lines+['}'])+'\n'
