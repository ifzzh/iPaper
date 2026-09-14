#!/usr/bin/env python3
"""Opt-in public bibliographic GET acceptance. Never reads user credentials/PDFs."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ipaper.metadata.schema import SCHEMA
from ipaper.metadata.store import MetadataStore
from ipaper.metadata.providers import BibliographicHTTP, ProviderFailure, PARSERS

CASES=[
 {'provider':'crossref','path':'/works/10.1038%2Fnature14539','params':{},'title':'Deep learning','authors':['Yann LeCun','Yoshua Bengio','Geoffrey Hinton'],'doi':'10.1038/nature14539'},
 {'provider':'arxiv','path':'/api/query','params':{'id_list':'1706.03762v1'},'title':'Attention Is All You Need','authors':['Ashish Vaswani','Noam Shazeer'],'arxiv_id':'1706.03762','arxiv_version':'1'},
 {'provider':'dblp','path':'/search/publ/api','params':{'q':'Attention Is All You Need','format':'json','h':8},'title':'Attention Is All You Need.','authors':['Ashish Vaswani','Noam Shazeer']},
 {'provider':'openreview','path':'/notes','params':{'id':'HkePNpVKPB'},'title':'Adam: A Method for Stochastic Optimization','authors':['Diederik P. Kingma','Jimmy Ba']},
]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    if not args.live:
        print(json.dumps({'mode':'offline','sources':CASES,'requests':0,'instruction':'Use --live for at most four public bibliographic requests, no automatic retries.'}));return
    with tempfile.TemporaryDirectory(prefix='ipaper-metadata-source-') as tmp:
        db=Path(tmp)/'check.db';sqlite3.connect(db).executescript(SCHEMA).close();store=MetadataStore(db,'public-acceptance');calls=[]
        results=[]
        for case in CASES:
            http=BibliographicHTTP(store,lambda:calls.append(case['provider']),lambda:None)
            item={'provider':case['provider'],'expectedTitle':case['title'],'status':'unverified'}
            try:
                data=http.get(case['provider'],case['path'],case['params']);records=PARSERS[case['provider']](data)
                from ipaper.metadata.model import normalized
                record=next((r for r in records if normalized(r['fields'].get('title'))==normalized(case['title'])),None)
                if record:
                    f=record['fields'];names='; '.join(a['name'] for a in f.get('author_list',[]))
                    checks={'title':True,'authors':all(a in names for a in case['authors']),**{k:f.get(k)==case[k] for k in ('doi','arxiv_id','arxiv_version') if k in case}}
                    item.update(status='passed' if all(checks.values()) else 'field_mismatch',checks=checks,fields=f)
                else:item['status']='identity_not_confirmed'
                payload=json.dumps(data,ensure_ascii=False,indent=2).encode();(args.output/(case['provider']+'-response.json')).write_bytes(payload);item['responseSha256']=hashlib.sha256(payload).hexdigest()
                before=len(calls);http.get(case['provider'],case['path'],case['params']);item['cacheAdditionalRequests']=len(calls)-before
            except ProviderFailure as e:item.update(status='blocked',error=e.code)
            results.append(item)
        report={'mode':'live','requests':len(calls),'modelRequests':0,'mineruRequests':0,'ocrRequests':0,'results':results}
        (args.output/'source-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
