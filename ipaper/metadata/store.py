from __future__ import annotations
import json
import sqlite3
import uuid
from contextlib import contextmanager
from .model import MetadataError, FIELDS, IDENTITY_FIELDS, LEGACY, stamp, encoded, fingerprint, legacy_fields, legacy_projection, validate_patch

TERMINAL={'completed','unchanged','needs_review','not_found','failed','cancelled','stale','deleted'}


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='bibliography'").fetchone())


def unpack(row):
    from ipaper.database.dao.paper_dao import PaperDAO
    return PaperDAO._row_to_dict(row)


def in_library(paper):
    path=str(paper.get('file_path') or '')
    return bool(path and '/.daily_arxiv_temp/' not in path and (not paper.get('is_daily') or '/.categories/' in path or '/_ReadingListTemp/' in path))


def _save_head(db, owner, paper_id, fields, provenance, revision, original_title, reason):
    now=stamp(); projection=legacy_projection(fields)
    db.execute('INSERT OR REPLACE INTO bibliography VALUES (?,?,?,?,?,?,?,?)',
        (owner,paper_id,revision,encoded(fields),encoded(provenance),encoded(projection),original_title,now))
    db.execute('INSERT OR REPLACE INTO bibliography_revisions VALUES (?,?,?,?,?,?,?)',
        (owner,paper_id,revision,encoded(fields),encoded(provenance),reason,now))
    db.execute('INSERT INTO metadata_index_events(owner_id,paper_id,revision) VALUES (?,?,?) ON CONFLICT(owner_id,paper_id) DO UPDATE SET revision=excluded.revision,attempts=0', (owner,paper_id,revision))


def ensure_head(db, owner, paper, *, initial=False):
    row=db.execute('SELECT * FROM bibliography WHERE owner_id=? AND paper_id=?',(owner,paper['id'])).fetchone()
    if row: return dict(row)
    fields=legacy_fields(paper); now=stamp()
    source='import' if initial else 'legacy'
    provenance={k:{'source':source,'manual':False,'revision':1,'at':now} for k,v in fields.items() if v}
    if initial and paper.get('_metadata_inspection'):
        for k in ('title','authors'):
            provenance[k]={'source':'pdf_embedded' if paper['_metadata_inspection'].get('metadata',{}).get('title' if k=='title' else 'author') else 'filename','manual':False,'revision':1,'at':now}
    _save_head(db,owner,paper['id'],fields,provenance,1,paper.get('title',''),'import' if initial else 'legacy_registration')
    return dict(db.execute('SELECT * FROM bibliography WHERE owner_id=? AND paper_id=?',(owner,paper['id'])).fetchone())


def prepare_save(db, owner, incoming, previous):
    """Called inside the paper transaction; background full-object saves cannot touch bibliography."""
    if not exists(db): return incoming
    if previous:
        old=unpack(previous)
        head=ensure_head(db,owner,old)
        fields=json.loads(head['fields_json'])
        merged={**old,**incoming,**legacy_projection(fields)}
        # Concurrent objects may include an older accumulated duration.
        for key in ('read_time','analysis_view_time','translation_time','analysis_time'):
            merged[key]=max(int(old.get(key) or 0),int(incoming.get(key) or 0))
        return merged
    return incoming


def after_save(db, owner, paper, *, new):
    if not exists(db): return
    head=ensure_head(db,owner,paper,initial=new)
    inspection=paper.get('_metadata_inspection')
    if inspection and isinstance(inspection,dict): cache_inspection(db,owner,paper['id'],inspection)
    db.execute('INSERT INTO metadata_index_events(owner_id,paper_id,revision) VALUES (?,?,?) ON CONFLICT(owner_id,paper_id) DO UPDATE SET revision=excluded.revision,attempts=0',(owner,paper['id'],head['revision']))
    if new and in_library(paper): enqueue(db,owner,[paper['id']],kind='import')


def cache_inspection(db,owner,paper_id,data):
    sha=data.get('sha256','')
    if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha): return
    compact={k:data.get(k) for k in ('sha256','first_page_text','metadata','page_count')}
    if len(encoded(compact).encode())>300_000: raise MetadataError('inspection_limit',413)
    db.execute('INSERT OR REPLACE INTO bibliography_inspections VALUES (?,?,?,?,?)',(owner,paper_id,sha,encoded(compact),stamp()))


def enqueue(db,owner,paper_ids,*,kind='manual',force=False):
    if not isinstance(paper_ids,list) or not paper_ids or len(paper_ids)>5000 or any(not isinstance(v,str) or len(v)>100 for v in paper_ids): raise MetadataError('invalid_metadata_selection')
    ids=list(dict.fromkeys(paper_ids)); rows=[]
    for paper_id in ids:
        row=db.execute('SELECT * FROM papers WHERE owner_id=? AND id=?',(owner,paper_id)).fetchone()
        if not row or not in_library(unpack(row)): raise MetadataError('paper_not_found',404)
        head=ensure_head(db,owner,unpack(row))
        rows.append((paper_id,head['revision']))
    key=fingerprint(rows)
    previous=db.execute('SELECT * FROM metadata_batches WHERE owner_id=? AND selection_key=? AND cancel_requested=0 ORDER BY created_at DESC LIMIT 1',(owner,key)).fetchone()
    if previous and not force: return previous['id']
    batch=str(uuid.uuid4()); now=stamp()
    db.execute('INSERT INTO metadata_batches VALUES (?,?,?,?,0,?)',(batch,owner,kind,key,now))
    for paper_id,revision in rows:
        # One pending item per owner/paper even if batch selections overlap.
        prior=db.execute("SELECT id FROM metadata_items WHERE owner_id=? AND paper_id=? AND status IN ('queued','running','waiting')",(owner,paper_id)).fetchone()
        item=str(uuid.uuid4())
        db.execute('INSERT INTO metadata_items(id,batch_id,owner_id,paper_id,input_revision,status,stage,checkpoint_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (item,batch,owner,paper_id,revision,'waiting' if prior else 'queued','linked' if prior else 'queued',encoded({'linkedItem':prior['id']}) if prior else '{}',now,now))
    return batch


def legacy_write(db,owner,paper_id,fields,*,state_changes=None):
    from ipaper.database.dao.paper_dao import PaperDAO
    row=db.execute('SELECT * FROM papers WHERE owner_id=? AND id=?',(owner,paper_id)).fetchone()
    if not row: raise MetadataError('paper_not_found',404)
    paper=unpack(row); paper.update(state_changes or {});paper.update(legacy_projection(fields)); converted=PaperDAO._dict_to_row(paper)
    # This updates no file, task or reading identity: values originate from the
    # just-read row, under the same IMMEDIATE transaction.
    db.execute('UPDATE papers SET '+','.join(k+'=?' for k in converted if k!='id')+' WHERE owner_id=? AND id=?',
        [v for k,v in converted.items() if k!='id']+[owner,paper_id])


class MetadataStore:
    def __init__(self,db_path,owner): self.db_path,self.owner=str(db_path),owner
    @contextmanager
    def connection(self,write=False):
        db=sqlite3.connect(self.db_path,timeout=20);db.row_factory=sqlite3.Row
        try:
            if write: db.execute('BEGIN IMMEDIATE')
            yield db
            if write:db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def paper(self,db,paper_id):
        row=db.execute('SELECT * FROM papers WHERE id=? AND owner_id=?',(paper_id,self.owner)).fetchone()
        if not row:raise MetadataError('paper_not_found',404)
        return unpack(row)

    def get(self,paper_id):
        with self.connection() as db:
            paper=self.paper(db,paper_id)
            head=db.execute('SELECT * FROM bibliography WHERE owner_id=? AND paper_id=?',(self.owner,paper_id)).fetchone()
            fields=json.loads(head['fields_json']) if head else legacy_fields(paper)
            provenance=json.loads(head['provenance_json']) if head else {k:{'source':'legacy','manual':False,'revision':1} for k,v in fields.items() if v}
            candidates=[{'id':r['id'],'revision':r['input_revision'],'verdict':r['verdict'],**json.loads(r['record_json'])} for r in db.execute('SELECT * FROM bibliography_candidates WHERE owner_id=? AND paper_id=? ORDER BY created_at DESC LIMIT 20',(self.owner,paper_id))]
            item=db.execute('SELECT id,batch_id,status,stage,error,updated_at FROM metadata_items WHERE owner_id=? AND paper_id=? ORDER BY created_at DESC LIMIT 1',(self.owner,paper_id)).fetchone()
            sync=db.execute('SELECT 1 FROM metadata_index_events WHERE owner_id=? AND paper_id=?',(self.owner,paper_id)).fetchone()
            return {'paperId':paper_id,'revision':head['revision'] if head else 1,'fields':fields,'provenance':provenance,'candidates':candidates,'task':dict(item) if item else None,'indexPending':bool(sync),'originalTitle':head['original_title'] if head else paper.get('title','')}

    def edit(self,paper_id,patch,revision=None,*,reason='manual',state_changes=None):
        patch=validate_patch(patch)
        if state_changes and set(state_changes)&set(LEGACY.values()):raise MetadataError('invalid_metadata_fields')
        with self.connection(True) as db:
            head=ensure_head(db,self.owner,self.paper(db,paper_id))
            if revision is not None and (type(revision)!=int or head['revision']!=revision):raise MetadataError('metadata_revision_conflict',409)
            fields=json.loads(head['fields_json']); provenance=json.loads(head['provenance_json']);new=head['revision']+1
            for key,value in patch.items():
                fields[key]=value; provenance[key]={'source':'manual','manual':True,'revision':new,'at':stamp()}
            legacy_write(db,self.owner,paper_id,fields,state_changes=state_changes)
            _save_head(db,self.owner,paper_id,fields,provenance,new,head['original_title'],reason)
        return self.get(paper_id)

    def apply(self,paper_id,record,revision,*,sha='',item_id=None):
        patch=validate_patch(record['fields']);changes=[]
        with self.connection(True) as db:
            if item_id:
                task=db.execute('SELECT b.cancel_requested FROM metadata_items i JOIN metadata_batches b ON b.id=i.batch_id WHERE i.id=? AND i.owner_id=?',(item_id,self.owner)).fetchone()
                if not task or task[0]:raise MetadataError('metadata_cancelled',409)
            head=ensure_head(db,self.owner,self.paper(db,paper_id))
            if head['revision']!=revision:raise MetadataError('metadata_revision_conflict',409)
            fields=json.loads(head['fields_json']);provenance=json.loads(head['provenance_json']);new=revision+1
            for key,value in patch.items():
                origin=provenance.get(key,{})
                if not value or origin.get('manual'):continue
                if fields.get(key) and origin.get('source') not in {'filename','pdf_embedded'}:continue
                if fields.get(key)==value:continue
                fields[key]=value;changes.append(key)
                source=record.get('fieldSources',{}).get(key,record)
                provenance[key]={'source':source['provider'],'url':source['url'],'manual':False,'revision':new,'at':stamp(),'sha256':sha}
            if changes:
                legacy_write(db,self.owner,paper_id,fields)
                _save_head(db,self.owner,paper_id,fields,provenance,new,head['original_title'],'enrichment')
        return changes

    def create(self,paper_ids,*,force=False):
        with self.connection(True) as db:return enqueue(db,self.owner,paper_ids,force=force)

    def selection(self,query):
        if not isinstance(query,dict) or set(query)-{'scope','query','categoryIds'}:raise MetadataError('invalid_metadata_selection')
        with self.connection() as db:
            rows=[unpack(r) for r in db.execute('SELECT * FROM papers WHERE owner_id=?',(self.owner,))]
            reading={r[0] for r in db.execute('SELECT paper_id FROM reading_list WHERE owner_id=?',(self.owner,))}
        scope=query.get('scope','all'); text=str(query.get('query','')).strip().casefold()
        category_ids=query.get('categoryIds',[])
        if scope not in {'all','favorites','reading','categories'}:raise MetadataError('invalid_metadata_selection')
        if not isinstance(category_ids,list) or len(category_ids)>1000 or any(not isinstance(v,str) for v in category_ids):raise MetadataError('invalid_metadata_selection')
        result=[]
        for p in rows:
            if not in_library(p):continue
            if scope=='favorites' and not p.get('starred'):continue
            if scope=='reading' and p['id'] not in reading:continue
            if scope=='categories':
                from ipaper.security.paths import category_storage_id
                from pathlib import PurePath
                # Historic rows may predate the explicit category field. The
                # existing immutable directory mapping is authoritative.
                parent=PurePath(p.get('file_path','')).parent.name
                if p.get('category_id') not in category_ids and parent not in {category_storage_id(c) for c in category_ids}:continue
            if text and text not in ' '.join(str(p.get(k) or '') for k in ('title','authors','abstract','year','journal','doi')).casefold():continue
            result.append(p['id'])
        return sorted(result)

    def batches(self,limit=50):
        with self.connection() as db:
            ids=[r[0] for r in db.execute('SELECT id FROM metadata_batches WHERE owner_id=? ORDER BY created_at DESC LIMIT ?',(self.owner,min(limit,200)))]
        return [self.batch(x,limit=0) for x in ids]

    def batch(self,batch_id,after=0,limit=50):
        with self.connection() as db:
            b=db.execute('SELECT * FROM metadata_batches WHERE id=? AND owner_id=?',(batch_id,self.owner)).fetchone()
            if not b:raise MetadataError('metadata_task_not_found',404)
            counts={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM metadata_items WHERE batch_id=? GROUP BY status',(batch_id,))}
            items=[dict(r) for r in db.execute('SELECT id,paper_id,status,stage,error,requests,updated_at FROM metadata_items WHERE batch_id=? ORDER BY created_at,id LIMIT ? OFFSET ?',(batch_id,min(limit,100),max(0,after)))]
            events=[dict(r) for r in db.execute('SELECT sequence,kind,data_json,created_at FROM metadata_events WHERE owner_id=? AND item_id IN (SELECT id FROM metadata_items WHERE batch_id=?) ORDER BY sequence DESC LIMIT 50',(self.owner,batch_id))] if limit else []
        active=sum(n for s,n in counts.items() if s not in TERMINAL)
        return {'id':batch_id,'kind':'metadata','created_at':b['created_at'],'counts':counts,'total':sum(counts.values()),'completed':sum(counts.values())-active,'status':'running' if active else 'cancelled' if b['cancel_requested'] else 'partial' if any(counts.get(s) for s in ('failed','stale')) else 'completed','items':items,'events':events,'next':after+len(items) if limit and after+len(items)<sum(counts.values()) else None}

    def cancel(self,batch_id):
        self.batch(batch_id,limit=0)
        with self.connection(True) as db:
            db.execute('UPDATE metadata_batches SET cancel_requested=1 WHERE id=? AND owner_id=?',(batch_id,self.owner))
            db.execute("UPDATE metadata_items SET status='cancelled',stage='cancelled',updated_at=? WHERE batch_id=? AND status IN ('queued','waiting')",(stamp(),batch_id))
        return self.batch(batch_id)

    def retry(self,batch_id):
        self.batch(batch_id,limit=0)
        with self.connection() as db:
            ids=[r[0] for r in db.execute("SELECT paper_id FROM metadata_items WHERE batch_id=? AND status IN ('failed','stale','cancelled')",(batch_id,))]
        if not ids:raise MetadataError('metadata_nothing_to_retry',409)
        return self.create(ids,force=True)

    def reconcile(self):
        """On re-upgrade preserve changes made by the older Web's compatible fields."""
        with self.connection(True) as db:
            for row in db.execute('SELECT * FROM bibliography WHERE owner_id=?',(self.owner,)).fetchall():
                paper_row=db.execute('SELECT * FROM papers WHERE owner_id=? AND id=?',(self.owner,row['paper_id'])).fetchone()
                if not paper_row:continue
                fields=json.loads(row['fields_json']);p=json.loads(row['provenance_json']); projection=json.loads(row['projection_json']);paper=unpack(paper_row);changed=False
                for key,old in LEGACY.items():
                    # Missing extra fields from an older whole-object write are
                    # not intentional clears: only fields that existed on 1.4.
                    if key not in {'title','authors','abstract','affiliation','year','journal','source_url','arxiv_id','github','homepage','preprint_date'}:continue
                    value=paper.get(old) or ''
                    if value!=(projection.get(old) or ''):
                        if key=='authors':fields['author_list']=[];p['author_list']={'source':'legacy_edit','manual':True,'revision':row['revision']+1,'at':stamp()}
                        fields[key]=value;p[key]={'source':'legacy_edit','manual':True,'revision':row['revision']+1,'at':stamp()};changed=True
                if changed:_save_head(db,self.owner,row['paper_id'],fields,p,row['revision']+1,row['original_title'],'rollback_reconciliation')
                legacy_write(db,self.owner,row['paper_id'],fields)
