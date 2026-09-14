from __future__ import annotations
import json
import logging
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from ipaper.security.paths import PathSecurityError
from ipaper.security.identity import Identity, current_user_id, run_as_identity
from .model import MetadataError, stamp, encoded, fingerprint, normalized
from .store import MetadataStore, TERMINAL, cache_inspection, ensure_head, in_library, unpack
from .providers import BibliographicHTTP, ProviderFailure, clues, queries, PARSERS, match, combine_publication


class MetadataService:
    def __init__(self,db_path,processing,paper_store=None,search_index=None,*,http_factory=BibliographicHTTP):
        self.db_path,self.processing,self.paper_store,self.search_index=str(db_path),processing,paper_store,search_index
        self.http_factory=http_factory;self.stop=threading.Event();self.wake=threading.Event();self.thread=None
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='bibliography')
        self.active={}
    def store(self,owner=None):return MetadataStore(self.db_path,owner or current_user_id())

    def start(self):
        if self.thread:return
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE metadata_items SET status='queued',stage='recovering',updated_at=? WHERE status='running'",(stamp(),))
            owners=[r[0] for r in db.execute('SELECT DISTINCT owner_id FROM bibliography')]
        for owner in owners:self.store(owner).reconcile()
        self.thread=threading.Thread(target=self._loop,name='metadata-dispatch',daemon=True);self.thread.start()
    def shutdown(self):
        self.stop.set();self.wake.set()
        if self.thread:self.thread.join(timeout=2)
        self.pool.shutdown(wait=False,cancel_futures=True)
    def _loop(self):
        while not self.stop.is_set():
            try:self.dispatch();self.sync_indexes()
            except Exception:logging.getLogger(__name__).warning('metadata_dispatch_failed')
            self.wake.wait(.5);self.wake.clear()
    def dispatch(self):
        for item,future in list(self.active.items()):
            if future.done():
                if not future.cancelled() and future.exception():
                    # Keep admission blocked if recording failure is itself
                    # unavailable, then retry the terminal write next tick.
                    with sqlite3.connect(self.db_path) as db:
                        db.execute("UPDATE metadata_items SET status='failed',stage='failed',error='metadata_storage_failed',updated_at=? WHERE id=? AND status='running'",(stamp(),item))
                    logging.getLogger(__name__).warning('metadata_job_interrupted')
                del self.active[item]
        with sqlite3.connect(self.db_path) as db:
            db.row_factory=sqlite3.Row
            rows=db.execute("""SELECT i.*,u.username,u.role FROM metadata_items i JOIN users u ON u.id=i.owner_id
                JOIN metadata_batches b ON b.id=i.batch_id WHERE i.status IN ('queued','waiting') AND i.stage!='linked'
                AND i.next_run<=? AND b.cancel_requested=0 AND u.status='active'
                AND NOT EXISTS (SELECT 1 FROM metadata_items r WHERE r.owner_id=i.owner_id AND r.status='running')
                ORDER BY CASE b.kind WHEN 'manual' THEN 0 ELSE 1 END,i.created_at LIMIT 20""",(time.time(),)).fetchall()
        busy=set()
        for row in rows:
            if len(self.active)>=2 or self.stop.is_set():break
            if row['owner_id'] in busy or row['id'] in self.active:continue
            busy.add(row['owner_id']);identity=Identity(row['owner_id'],row['username'],row['role'])
            self.active[row['id']]=self.pool.submit(run_as_identity,identity,self.run,row['id'],row['owner_id'])
        self.sync_linked()
    def sync_linked(self):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory=sqlite3.Row
            for r in db.execute("SELECT * FROM metadata_items WHERE stage='linked' AND status='waiting'").fetchall():
                target=json.loads(r['checkpoint_json']).get('linkedItem');source=db.execute('SELECT * FROM metadata_items WHERE id=? AND owner_id=?',(target,r['owner_id'])).fetchone()
                if source and source['status'] in TERMINAL:db.execute('UPDATE metadata_items SET status=?,stage=?,error=?,updated_at=? WHERE id=?',(source['status'],'reused',source['error'],stamp(),r['id']))
    def check(self,store,item_id):
        with store.connection() as db:
            row=db.execute('SELECT i.*,b.cancel_requested,u.status AS user_status FROM metadata_items i JOIN metadata_batches b ON b.id=i.batch_id JOIN users u ON u.id=i.owner_id WHERE i.id=? AND i.owner_id=?',(item_id,store.owner)).fetchone()
            if not row:raise MetadataError('metadata_task_not_found',404)
            store.paper(db,row['paper_id'])
        if row['cancel_requested'] or row['user_status']!='active':raise MetadataError('metadata_cancelled',409)
        if self.stop.is_set():raise MetadataError('metadata_interrupted',409)
        return dict(row)
    def event(self,store,item_id,kind,data):
        with store.connection(True) as db:
            db.execute('INSERT INTO metadata_events(item_id,owner_id,kind,data_json,created_at) VALUES (?,?,?,?,?)',(item_id,store.owner,kind,encoded(data),stamp()))
    def finish(self,store,item_id,status,*,error=None,checkpoint=None,delay=0):
        with store.connection(True) as db:
            db.execute('UPDATE metadata_items SET status=?,stage=?,error=?,updated_at=?,next_run=? WHERE id=? AND owner_id=?',(status,status,error,stamp(),time.time()+delay,item_id,store.owner))
            if checkpoint is not None:db.execute('UPDATE metadata_items SET checkpoint_json=? WHERE id=?',(encoded(checkpoint),item_id))
        self.event(store,item_id,status,{'error':error} if error else {})

    def inspect(self,store,paper_id,check):
        from ipaper.processing.pipeline import file_digest
        pipeline=self.processing.pipeline(store.owner);path=pipeline.paper_file(paper_id);sha=file_digest(path);check()
        with store.connection() as db:
            row=db.execute('SELECT data_json FROM bibliography_inspections WHERE owner_id=? AND paper_id=? AND sha256=?',(store.owner,paper_id,sha)).fetchone()
        if row:return sha,json.loads(row[0]),path
        # Reuse the parser's source content when available; translations do not
        # limit the available original text. Only page-one blocks are identity clues.
        with pipeline.store.connection() as db:
            result=db.execute("""SELECT r.id,d.page_count FROM processing_results r JOIN processing_documents d ON d.id=r.document_id
                WHERE r.owner_id=? AND r.paper_id=? AND d.kind='original' AND d.sha256=? AND r.kind='structure' AND r.status='completed'
                AND json_extract(r.config_json,'$.internalPart') IS NOT 1 ORDER BY r.created_at DESC LIMIT 1""",(store.owner,paper_id,sha)).fetchone()
        data=None
        if result:
            blocks=pipeline.store.blocks(result['id'],limit=100)
            text=[]
            for block in blocks:
                source=block.get('source',{});regions=source.get('regions',[])
                pages=[x.get('page') for x in regions]
                if source.get('page') is not None:pages.append(source['page'])
                if 1 in pages:
                    content=block.get('content',{});text.append(content.get('text',''))
            if text:data={'sha256':sha,'page_count':result['page_count'],'first_page_text':'\n'.join(text)[:65536],'metadata':{},'source':'structure'}
        if not data:
            worker=pipeline.document;job=str(uuid.uuid4())
            try:
                with path.open('rb') as handle:worker.stage(job,'pdf_inspect',handle)
                worker.create(job,'pdf_inspect');outcome=worker.wait(job,timeout=90);check()
                if outcome['status']!='completed':raise MetadataError('metadata_document_unavailable',503)
                data=worker.result_json(job)
                if data.get('sha256')!=sha:raise MetadataError('metadata_source_changed',409)
            except MetadataError:raise
            except Exception:raise MetadataError('metadata_document_unavailable',503) from None
            finally:
                try:worker.cleanup(job)
                except Exception:logging.getLogger(__name__).warning('metadata_inspection_cleanup_pending')
        if file_digest(path)!=sha:raise MetadataError('metadata_source_changed',409)
        with store.connection(True) as db:store.paper(db,paper_id);cache_inspection(db,store.owner,paper_id,data)
        return sha,data,path

    def run(self,item_id,owner):
        store=self.store(owner);checkpoint={}
        try:
            with store.connection(True) as db:
                row=db.execute('SELECT * FROM metadata_items WHERE id=? AND owner_id=?',(item_id,owner)).fetchone()
                if not row or row['status'] not in {'queued','waiting'}:return
                if db.execute("SELECT 1 FROM metadata_items WHERE owner_id=? AND status='running'",(owner,)).fetchone():return
                db.execute("UPDATE metadata_items SET status='running',stage='identifying',updated_at=? WHERE id=?",(stamp(),item_id))
                checkpoint=json.loads(row['checkpoint_json'])
            paper_id=row['paper_id'];check=lambda:self.check(store,item_id);check()
            current=store.get(paper_id)
            if current['revision']!=row['input_revision']:raise MetadataError('metadata_revision_conflict',409)
            try:sha,inspection,path=self.inspect(store,paper_id,check)
            except PathSecurityError as e:
                if e.reason!='missing_path':raise MetadataError('metadata_document_unavailable',409) from None
                sha,inspection,path='',{},None
            except FileNotFoundError:
                sha,inspection,path='',{},None
            if checkpoint.get('sha256') and checkpoint['sha256']!=sha:raise MetadataError('metadata_source_changed',409)
            clue=clues(current['fields'],inspection,current['originalTitle'])
            schedule=checkpoint.get('schedule') or queries(clue)
            records=checkpoint.get('records',[]);index=checkpoint.get('index',0);attempts=checkpoint.get('attempts',{})
            checkpoint.update(schedule=schedule,records=records,index=index,attempts=attempts,sha256=sha)
            def before():
                check()
                with store.connection(True) as db:
                    r=db.execute('SELECT requests FROM metadata_items WHERE id=?',(item_id,)).fetchone()
                    if r[0]>=8:raise MetadataError('metadata_request_budget',409)
                    db.execute('UPDATE metadata_items SET requests=requests+1 WHERE id=?',(item_id,))
            http=self.http_factory(store,before,check)
            while index<len(schedule):
                provider,url,params=schedule[index];check()
                try:
                    data=http.get(provider,url,params,ttl=86400 if provider=='arxiv' and not clue.get('arxiv_version') else 30*86400)
                except ProviderFailure as e:
                    # Local provider pacing did not consume a request. Remote
                    # transient errors get at most two retries within eight GETs.
                    retry_count=attempts.get(str(index),0)
                    if e.code=='metadata_rate_limited' or e.retryable and retry_count<2:
                        if e.code!='metadata_rate_limited':attempts[str(index)]=retry_count+1
                        if check()['requests']>=8:raise MetadataError('metadata_request_budget',409)
                        self.finish(store,item_id,'waiting',error=e.code,checkpoint=checkpoint,delay=e.delay*(2**retry_count));return
                    checkpoint.setdefault('providerFailures',[]).append({'provider':provider,'error':e.code})
                    self.event(store,item_id,'provider_unavailable',{'provider':provider,'error':e.code})
                    if index+1<len(schedule) or any(match(r,clue)=='verified' for r in records):
                        index+=1;checkpoint['index']=index
                        continue
                    raise
                candidates=PARSERS[provider](data)
                if provider=='arxiv' and not clue.get('arxiv_version'):
                    for record in candidates:
                        record['relations']['onlineVersion']=record['fields'].pop('arxiv_version','')
                        record['relations']['onlineUpdated']=record['fields'].pop('version_date','')
                records.extend(candidates)
                if provider=='arxiv':
                    from urllib.parse import quote
                    linked=[r for r in candidates if match(r,clue)=='verified' and r['fields'].get('doi')]
                    if len(linked)==1 and not any(q[0]=='crossref' for q in schedule):
                        schedule.append(['crossref','/works/'+quote(linked[0]['fields']['doi'],safe=''),{}])
                # API v1 is a known protocol fallback only after a genuine miss.
                if provider=='openreview' and not candidates and (data.get('missing') or data.get('notes')==[]):schedule.append(['openreview_v1','/notes',params])
                index+=1;checkpoint['index']=index
                with store.connection(True) as db:db.execute('UPDATE metadata_items SET checkpoint_json=? WHERE id=?',(encoded(checkpoint),item_id))
                if any(match(r,clue)=='verified' for r in records) and not (provider=='arxiv' and index<len(schedule)):break
            check()
            if path:
                from ipaper.processing.pipeline import file_digest
                if file_digest(path)!=sha:raise MetadataError('metadata_source_changed',409)
            verified=[]
            linked=[r for r in records if r['provider']=='arxiv' and match(r,clue)=='verified' and r['fields'].get('doi')]
            if len(linked)==1:clue={**clue,'doi':linked[0]['fields']['doi']}
            with store.connection(True) as db:
                store.paper(db,paper_id)
                db.execute('DELETE FROM bibliography_candidates WHERE owner_id=? AND paper_id=?',(owner,paper_id))
                for record in records[:20]:
                    verdict=match(record,clue)
                    if verdict=='verified':verified.append(record)
                    db.execute('INSERT INTO bibliography_candidates VALUES (?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),owner,paper_id,row['input_revision'],sha,encoded(record),verdict,stamp()))
            # Deduplicate identical provider records, never choose a winner by rank.
            verified=combine_publication(verified)
            unique={fingerprint([r['fields'].get('doi'),r['fields'].get('arxiv_id'),r['externalId']]):r for r in verified}
            if len(unique)==1:
                check();changes=store.apply(paper_id,next(iter(unique.values())),row['input_revision'],sha=sha,item_id=item_id)
                self.publish_memory(store,paper_id)
                self.finish(store,item_id,'completed' if changes else 'unchanged')
                self.event(store,item_id,'fields_saved',{'fields':changes})
            else:
                if not records and checkpoint.get('providerFailures'):raise MetadataError(checkpoint['providerFailures'][-1]['error'],503)
                self.finish(store,item_id,'needs_review' if records else 'not_found')
        except MetadataError as e:
            status={'metadata_cancelled':'cancelled','paper_not_found':'deleted','metadata_revision_conflict':'stale','metadata_source_changed':'stale','metadata_interrupted':'queued'}.get(e.code,'failed')
            self.finish(store,item_id,status,error=e.code,checkpoint=checkpoint)
        except Exception as e:
            logging.getLogger(__name__).warning('metadata_processing_failed type=%s',type(e).__name__)
            self.finish(store,item_id,'failed',error='metadata_processing_failed',checkpoint=checkpoint)

    def publish_memory(self,store,paper_id):
        if self.paper_store is None:return
        from ipaper.core.base_paper import Paper
        with store.connection() as db:
            data=store.paper(db,paper_id)
            user=db.execute('SELECT username,role FROM users WHERE id=?',(store.owner,)).fetchone()
        def publish():
            entry=self.paper_store.get_entry(paper_id)
            if entry:self.paper_store.upsert(Paper.from_dict(data),category_id=entry.category_id,category_path=entry.category_path)
        run_as_identity(Identity(store.owner,user['username'],user['role']),publish)

    def sync_indexes(self):
        if self.search_index is None:return
        with sqlite3.connect(self.db_path) as db:
            db.row_factory=sqlite3.Row
            rows=db.execute('SELECT e.*,u.username,u.role FROM metadata_index_events e JOIN users u ON u.id=e.owner_id WHERE u.status=\'active\' LIMIT 20').fetchall()
        for row in rows:
            def sync():
                from ipaper.core.base_paper import Paper
                from ipaper.database.dao.paper_dao import PaperDAO
                data=PaperDAO.get_paper(row['paper_id'])
                if data:
                    paper=Paper.from_dict(data);entry=self.paper_store.get_entry(paper.id) if self.paper_store else None
                    if entry:self.paper_store.upsert(paper,category_id=entry.category_id,category_path=entry.category_path)
                    self.search_index.index_paper(paper,entry.category_id if entry else data.get('category_id'))
                else:
                    self.search_index.remove_paper(row['paper_id'])
                    if self.paper_store:self.paper_store.remove(row['paper_id'])
                with sqlite3.connect(self.db_path) as db:db.execute('DELETE FROM metadata_index_events WHERE owner_id=? AND paper_id=? AND revision=?',(row['owner_id'],row['paper_id'],row['revision']))
            try:run_as_identity(Identity(row['owner_id'],row['username'],row['role']),sync)
            except Exception:
                with sqlite3.connect(self.db_path) as db:db.execute('UPDATE metadata_index_events SET attempts=attempts+1 WHERE owner_id=? AND paper_id=?',(row['owner_id'],row['paper_id']))

    def duplicates(self,paper_id):
        store=self.store();current=store.get(paper_id);f=current['fields'];out=[]
        with store.connection() as db:
            inspected=db.execute('SELECT sha256 FROM bibliography_inspections WHERE owner_id=? AND paper_id=? ORDER BY created_at DESC LIMIT 1',(store.owner,paper_id)).fetchone()
            docs=db.execute("SELECT sha256 FROM processing_documents WHERE owner_id=? AND paper_id=? AND kind='original' ORDER BY created_at DESC LIMIT 1",(store.owner,paper_id)).fetchone()
            sha=inspected[0] if inspected else docs[0] if docs else ''
            for row in db.execute('SELECT * FROM papers WHERE owner_id=? AND id!=?',(store.owner,paper_id)):
                p=unpack(row)
                if not in_library(p):continue
                head=db.execute('SELECT fields_json FROM bibliography WHERE owner_id=? AND paper_id=?',(store.owner,p['id'])).fetchone()
                from .model import legacy_fields
                other=json.loads(head[0]) if head else legacy_fields(p);kind=''
                if sha and db.execute("SELECT 1 FROM bibliography_inspections WHERE owner_id=? AND paper_id=? AND sha256=? UNION ALL SELECT 1 FROM processing_documents WHERE owner_id=? AND paper_id=? AND kind='original' AND sha256=?",(store.owner,p['id'],sha,store.owner,p['id'],sha)).fetchone():kind='same_file'
                elif f.get('arxiv_id') and f['arxiv_id']==other.get('arxiv_id'):kind='same_paper' if f.get('arxiv_version') and f.get('arxiv_version')==other.get('arxiv_version') else 'related_version'
                elif f.get('doi') and f['doi']==other.get('doi'):kind='same_paper'
                elif len(normalized(f.get('title')))>8 and normalized(f['title'])==normalized(other.get('title')) and f.get('authors') and normalized(f['authors'])==normalized(other.get('authors')):kind='possible_duplicate'
                if not kind:continue
                sig=fingerprint([kind,f,other,sha]);dismissed=db.execute('SELECT signature FROM metadata_duplicate_dismissals WHERE owner_id=? AND paper_id=? AND other_id=?',(store.owner,paper_id,p['id'])).fetchone()
                if dismissed and dismissed[0]==sig:continue
                out.append({'paperId':p['id'],'title':other.get('title') or p.get('title'),'kind':kind,'signature':sig})
        return out[:100]
