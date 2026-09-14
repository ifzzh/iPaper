from __future__ import annotations
import io
import json
import sqlite3
import re
from flask import Blueprint, jsonify, request, send_file
from ipaper.security.identity import current_user_id
from ipaper.processing.pipeline import file_digest
from .model import MetadataError, bibtex, validate_patch


def register_metadata_routes(app,service):
    bp=Blueprint('metadata',__name__);app.extensions['metadata']=service
    @bp.before_request
    def limits():request.max_content_length=128*1024
    @bp.after_request
    def private(response):
        response.headers['Cache-Control']='private, no-store';response.headers['Vary']='Cookie';response.headers['X-Content-Type-Options']='nosniff';return response
    @bp.errorhandler(MetadataError)
    def known(e):return jsonify(error=e.code),e.status
    @bp.errorhandler(sqlite3.Error)
    def db_error(e):return jsonify(error='metadata_storage_failed'),503
    def body(allowed):
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or set(data)-set(allowed):raise MetadataError('invalid_metadata_request')
        return data
    def integer(value,default=0):
        try:return max(0,int(value))
        except (ValueError,TypeError):return default
    def refresh(paper_id):
        from ipaper.database.dao.paper_dao import PaperDAO
        from ipaper.core.base_paper import Paper
        if service.paper_store:
            entry=service.paper_store.get_entry(paper_id)
            if entry:service.paper_store.upsert(Paper.from_dict(PaperDAO.get_paper(paper_id)),category_id=entry.category_id,category_path=entry.category_path)
        service.wake.set()
    @bp.get('/api/paper/<paper_id>/metadata')
    def get(paper_id):return jsonify(service.store().get(paper_id))
    @bp.patch('/api/paper/<paper_id>/metadata')
    def edit(paper_id):
        data=body({'fields','revision'})
        if type(data.get('revision'))!=int:raise MetadataError('metadata_revision_required')
        result=service.store().edit(paper_id,data.get('fields'),data['revision']);refresh(paper_id);return jsonify(result)
    @bp.post('/api/paper/<paper_id>/metadata/adopt')
    def adopt(paper_id):
        data=body({'candidateId','revision','fields'});store=service.store()
        if type(data.get('revision'))!=int or not isinstance(data.get('fields'),list) or any(not isinstance(k,str) for k in data['fields']) or not isinstance(data.get('candidateId'),str):raise MetadataError('invalid_metadata_request')
        with store.connection() as db:
            store.paper(db,paper_id)
            candidate=db.execute('SELECT * FROM bibliography_candidates WHERE id=? AND owner_id=? AND paper_id=?',(data.get('candidateId'),store.owner,paper_id)).fetchone()
        if not candidate:raise MetadataError('metadata_candidate_not_found',404)
        record=json.loads(candidate['record_json'])
        if candidate['sha256']:
            path=service.processing.pipeline(store.owner).paper_file(paper_id)
            if file_digest(path)!=candidate['sha256']:raise MetadataError('metadata_source_changed',409)
        if set(data['fields'])-set(record['fields']):raise MetadataError('invalid_metadata_fields')
        result=store.edit(paper_id,{k:record['fields'][k] for k in data['fields']},data['revision'],reason='candidate_adopted')
        refresh(paper_id);return jsonify(result)
    @bp.post('/api/metadata/preview')
    def preview():
        data=body({'paperIds','selection'});store=service.store();ids=data.get('paperIds') if 'paperIds' in data else store.selection(data.get('selection') or {})
        if not isinstance(ids,list) or len(ids)>5000 or any(not isinstance(i,str) for i in ids):raise MetadataError('invalid_metadata_selection')
        with store.connection() as db:
            from .store import in_library
            for paper_id in ids:
                if not in_library(store.paper(db,paper_id)):raise MetadataError('paper_not_found',404)
        return jsonify(paperIds=list(dict.fromkeys(ids)),count=len(set(ids)),modelRequests=0,mineruRequests=0,maximumBibliographicRequests=8*len(set(ids)))
    @bp.post('/api/metadata/jobs')
    def create():
        data=body({'paperIds','selection','force'});store=service.store()
        if type(data.get('force',False)) is not bool:raise MetadataError('invalid_metadata_request')
        ids=data.get('paperIds') if 'paperIds' in data else store.selection(data.get('selection') or {})
        task=store.create(ids,force=data.get('force',False));service.wake.set();return jsonify(id=task,status='queued'),202
    @bp.get('/api/metadata/jobs')
    def jobs():return jsonify(jobs=service.store().batches(integer(request.args.get('limit'),50)))
    @bp.get('/api/metadata/jobs/<task_id>')
    def job(task_id):return jsonify(service.store().batch(task_id,integer(request.args.get('after')),integer(request.args.get('limit'),50)))
    @bp.post('/api/metadata/jobs/<task_id>/cancel')
    def cancel(task_id):body(set());result=service.store().cancel(task_id);service.wake.set();return jsonify(result)
    @bp.post('/api/metadata/jobs/<task_id>/retry')
    def retry(task_id):body(set());task=service.store().retry(task_id);service.wake.set();return jsonify(id=task),202
    @bp.get('/api/paper/<paper_id>/bibtex')
    def citation(paper_id):
        metadata=service.store().get(paper_id);text=bibtex(paper_id,metadata['fields'])
        if request.args.get('download')=='1':
            title=re.sub(r'[^\w\u4e00-\u9fff .-]','',metadata['fields']['title'])[:100].strip() or '论文'
            return send_file(io.BytesIO(text.encode()),mimetype='application/x-bibtex',as_attachment=True,download_name='iPaper-'+title+'.bib')
        return jsonify(bibtex=text,revision=metadata['revision'])
    @bp.get('/api/paper/<paper_id>/duplicates')
    def duplicates(paper_id):return jsonify(items=service.duplicates(paper_id))
    @bp.post('/api/paper/<paper_id>/duplicates/dismiss')
    def dismiss(paper_id):
        data=body({'paperId','signature'});items=service.duplicates(paper_id)
        chosen=next((x for x in items if x['paperId']==data.get('paperId') and x['signature']==data.get('signature')),None)
        if not chosen:raise MetadataError('metadata_duplicate_changed',409)
        with service.store().connection(True) as db:db.execute('INSERT OR REPLACE INTO metadata_duplicate_dismissals VALUES (?,?,?,?)',(current_user_id(),paper_id,chosen['paperId'],chosen['signature']))
        return jsonify(success=True)
    app.register_blueprint(bp)
