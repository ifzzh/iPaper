"""Explicit one-package acceptance. Default is offline; real secrets enter stdin only.

Run against copies of the approved PDF and an isolated Document Worker. The
fixture database has its own key; real provider credentials are NEVER saved in
it. request-receipt.json is created exclusively before any provider submission.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace


def main(test_dependencies=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--original', type=Path)
    parser.add_argument('--translated', type=Path)
    parser.add_argument('--document-origin')
    parser.add_argument('--document-staging', type=Path)
    parser.add_argument('--document-token', type=Path)
    parser.add_argument('--serve-seconds', type=int, default=0)
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({'offline':True,'requests':0,'maxCloudCreates':1,'maxModelRequests':10,
                          'translation':{'pages':[1], 'maxTextBlocks':6,'requests':8,'inputTokens':20000,'outputTokens':12000},
                          'retranslation':{'requests':1,'inputTokens':5000,'outputTokens':4096},
                          'chat':{'requests':1,'maxOutputTokens':512,'timeoutSeconds':120}}))
        return
    from pytest import MonkeyPatch
    from tests.workbench_support import make_workbench_fixture
    from tests.workbench_reader_support import install_reader_fixture
    from tests.test_workbench import login
    from ipaper.database import connection
    from ipaper.database.dao.paper_dao import PaperDAO
    from ipaper.database.dao.settings_dao import SettingsDAO
    from ipaper.security.identity import Identity, run_as_identity
    from ipaper.security.paths import paper_path, paper_asset_paths
    from ipaper.security.outbound import OutboundPolicy
    from ipaper.processing.pipeline import ProcessingPipeline, file_digest
    from ipaper.processing.service import ProcessingService
    from ipaper.processing.routes import register_processing_routes
    from ipaper.processing.translation import request_payload, units_for, generation_options
    from ipaper.document_worker.client import DocumentWorkerClient
    from ipaper.routes.agent_routes import agent_chat_route
    from openai import OpenAI, DefaultHttpxClient
    import app as app_module
    import shutil
    import threading
    from werkzeug.serving import make_server

    os.umask(0o077)
    args.root.mkdir(parents=True, exist_ok=True)
    receipt = args.root/'request-receipt.json'
    assert not receipt.exists() and not (args.root/'ipaper.db').exists(), 'acceptance_already_started_no_retry'
    config=json.loads(sys.stdin.buffer.read(65537))
    assert config['paperId'] and config['ownerId'] and config['sourceSha256']==file_digest(args.original)
    assert config['expectedPages']==22
    with receipt.open('x') as f:
        json.dump({k:config[k] for k in ('paperId','ownerId','sourceSha256','expectedPages','translateCredentialRevision')},f)
        f.flush();os.fsync(f.fileno())
    policy=OutboundPolicy(public_origins=config['publicOrigins'], private_origins=[],
        transfer_origins=config['transferOrigins'], proxy_fake_ip_networks=config.get('fakeIpRanges',[]))
    if test_dependencies: policy=test_dependencies['policy']
    proxy={k:v for k,v in os.environ.items() if k.upper() in ('HTTP_PROXY','HTTPS_PROXY','NO_PROXY')}
    counters={'cloudCreates':0,'chatRequests':0}
    def stage(name):
        # Closed, non-sensitive progress survives a rejected/partial real run;
        # never serialize an exception, request headers or the provider config.
        (args.root/'stage.json').write_text(json.dumps({'stage':name,**counters}))
    stage('local_preflight')
    server=None
    with MonkeyPatch.context() as patch:
        app,a,b=make_workbench_fixture(args.root,patch,count=1)
        install_reader_fixture(app,args.root,(a,b),patch,'http://127.0.0.1:1',register_routes=False)
        for k,v in proxy.items():patch.setenv(k,v)
        service=ProcessingService(connection.DB_PATH,args.root/'papers',args.root/'settings.key',
                                   app_module.AGENTIC_CREDENTIAL_STORE,policy)
        register_processing_routes(app,service)
        document=DocumentWorkerClient(base_url=args.document_origin,token_file=args.document_token,jobs_root=args.document_staging)
        if test_dependencies: document=test_dependencies['document']
        assert document.health(), 'isolated_worker_not_ready'
        class Profiles:
            def get(self, *, secret=False):
                return {**{k:config['translation'][k] for k in ('model','baseUrl','revision')},'keyConfigured':True,
                        **({'key':config['translation']['key']} if secret else {})}
        class Credentials:
            def get(self,name): return config['mineruKey'] if name=='mineru' else config['chat']['key'] if name=='interpret' else ''
            def configured(self,name): return bool(config['mineruKey']) if name=='mineru' else bool(config['chat']['key']) if name=='interpret' else False
        from ipaper.processing.cloud import MinerUCloud
        Cloud=(test_dependencies or {}).get('cloud',MinerUCloud)
        class OneCloud(Cloud):
            def submit(self,*a,**kw):
                if counters['cloudCreates']: raise RuntimeError('duplicate_cloud_create_forbidden')
                counters['cloudCreates']+=1
                return super().submit(*a,**kw)
        agent_chat_route.register_agent_chat_routes(app,get_categories=lambda:{'children':[]},get_category_path=lambda *_:None,
            get_papers_in_category=lambda *_:[],agentic_settings_file='unused',credential_store=Credentials(),outbound_policy=policy)
        original_pipeline=service.pipeline
        def pipeline(owner=None):
            current=original_pipeline(owner)
            return ProcessingPipeline(current.store,Profiles(),Credentials(),policy,document_client=document,cloud_factory=OneCloud)
        service.pipeline=pipeline
        def seed():
            current=PaperDAO.get_paper('a-0');target=paper_path(args.root/'papers','root','autosci.pdf',create_parent=True)
            shutil.copyfile(args.original,target)
            if args.translated:shutil.copyfile(args.translated,paper_asset_paths(args.root/'papers',target).chinese_dual)
            current.update(title=config['title'],file_path=str(target),filename=target.name,has_chinese_version=bool(args.translated))
            PaperDAO.save_paper(current)
            from ipaper.core.base_paper import Paper
            app_module.paper_store.upsert(Paper.from_dict(current),category_id='root',category_path=['Root'])
            SettingsDAO.save_setting('agentic_settings',{'mineruUseApi':True,'llmConfigs':{'interpret':{'llmModel':config['chat']['model'],'llmBaseUrl':config['chat']['baseUrl']}}})
        run_as_identity(Identity(a['id'],a['username'],a['role']),seed)
        from ipaper.processing import translation as transport
        original_request=transport.process_request
        observed_requests=[]
        def observe(*arguments,**keywords):
            # Only the closed response is retained privately. Profile/key and
            # request headers are never serialized into evidence.
            value=original_request(*arguments,**keywords)
            observed_requests.append(value)
            (args.root/f'model-response-{len(observed_requests)}.json').write_text(json.dumps(value,ensure_ascii=False))
            return value
        patch.setattr(transport,'process_request',observe)
        service.start()
        client=app.test_client();csrf=login(client);headers={'X-CSRF-Token':csrf}
        def request(path,data):
            response=client.post(path,json=data,headers=headers)
            assert response.status_code in (200,202),(path,response.status_code,response.json)
            return response.json
        def await_job(value):
            end=time.monotonic()+2100
            while time.monotonic()<end:
                result=client.get('/api/processing/jobs/'+value['job']['id']).json['job']
                if result['status'] not in ('queued','running','cancelling'):
                    (args.root/(result['id']+'.json')).write_text(json.dumps(result,ensure_ascii=False))
                    assert result['status']=='completed',result.get('error')
                    return result
                time.sleep(1)
            raise RuntimeError('acceptance_timeout_no_resubmit')
        preview=request('/api/paper/a-0/processing/preview',{})
        assert preview['pageCount']==22 and preview['partCount']==1
        stage('parsing')
        parsed=await_job(request('/api/paper/a-0/processing/jobs',{'kind':'parse','preflightId':preview['preflightId']}))
        parse_id=parsed['resultId']
        all_blocks=[];cursor=-1
        while True:
            batch=client.get(f'/api/results/{parse_id}/blocks?after={cursor}&limit=100').json['blocks']
            if not batch:break
            all_blocks.extend(batch);cursor=batch[-1]['order']
        chosen=[];usage={'requests':0,'inputTokens':0,'outputTokens':0}
        for block in all_blocks:
            if block['source'].get('page')!=1 or block['type'] not in ('text','title') or not block.get('text','').strip():continue
            units=units_for(block)
            bounds=[request_payload(units[n:n+8],'zh-CN') for n in range(0,len(units),8)]
            extra={'requests':len(bounds),'inputTokens':sum(v[1] for v in bounds),'outputTokens':sum(v[2] for v in bounds)}
            if any(usage[k]+extra[k]>limit for k,limit in [('requests',8),('inputTokens',20000),('outputTokens',12000)]):continue
            chosen.append(block['id']);usage={k:usage[k]+extra[k] for k in usage}
            if len(chosen)==6:break
        assert chosen,'no_text_blocks_in_approved_scope'
        data={'preflightId':preview['preflightId'],'parseResultId':parse_id,'kind':'translate','blockIds':chosen,
              'budget':{'requests':8,'inputTokens':20000,'outputTokens':12000}}
        scope=request('/api/paper/a-0/processing/estimate',data)
        (args.root/'scope.json').write_text(json.dumps(scope,ensure_ascii=False))
        stage('translating')
        translated=await_job(request('/api/paper/a-0/processing/jobs',data))
        result_id=translated['resultId']
        retry_id=next((block['id'] for block in all_blocks if block['id'] in chosen and len(units_for(block))<=8
                       and request_payload(units_for(block),'zh-CN')[1]<=5000
                       and request_payload(units_for(block),'zh-CN')[2]<=4096),None)
        assert retry_id,'no_approved_retranslation_block'
        stage('retranslating')
        retranslated=await_job(request('/api/paper/a-0/processing/jobs',{**data,'kind':'retranslate','translationResultId':result_id,
                    'blockIds':[retry_id],'budget':{'requests':1,'inputTokens':5000,'outputTokens':4096}}))
        block=client.get(f'/api/results/{result_id}/blocks/{retry_id}').json['block']
        text=block['text'];source=request('/api/paper/a-0/sources',{'resultId':result_id,'blockId':retry_id,'start':0,
                    'end':min(len(text.encode('utf-16-le'))//2,1200)})['source']
        from ipaper.processing import understanding_chat
        from ipaper.processing.chat_transport import stream_request
        def bounded_chat(profile,messages,output):
            assert counters['chatRequests']==0,'duplicate_chat_forbidden'
            assert len(json.dumps(messages,ensure_ascii=False).encode())+256<=5000,'chat_input_budget_exceeded'
            counters['chatRequests']+=1;policy.validate(config['chat']['baseUrl'],purpose='ai')
            yield from stream_request(profile,messages,512,deadline=120)
        patch.setattr(understanding_chat,'stream_request',bounded_chat)
        import signal
        previous_alarm=signal.signal(signal.SIGALRM,lambda *_: (_ for _ in ()).throw(TimeoutError('chat_deadline')))
        signal.alarm(120)
        stage('source_chat')
        try:
            reply=client.post('/api/paper/chat',json={'paper_id':'a-0','messages':[{'role':'user','content':'请简要解释引用内容，并使用提供的来源编号标明依据。'}],
                             'source_ids':[source['id']]},headers=headers)
            content=reply.text
            (args.root/'chat-response.json').write_text(json.dumps({'status':reply.status_code,
                'contentType':reply.content_type,'stream':content[:65536]},ensure_ascii=False))
        finally:
            signal.alarm(0);signal.signal(signal.SIGALRM,previous_alarm)
        header,answer=content.split('\n',1);session_id=json.loads(header)['session_id']
        history=client.get('/api/paper/chat/session?paper_id=a-0&session_id='+session_id).json['session']['messages']
        (args.root/'chat-history.json').write_text(json.dumps(history,ensure_ascii=False))
        assert answer.strip() and history[-1]['content']==answer and history[-1].get('sources'),'answer_not_persisted_or_missing_valid_source'
        fresh=app.test_client();login(fresh)
        assert fresh.get('/api/paper/chat/session?paper_id=a-0&session_id='+session_id).json['session']['messages']==history
        cache=request('/api/paper/a-0/processing/estimate',{**data,'translationResultId':result_id})
        assert cache['estimate']['requests']==0
        report={'passed':True,**counters,'sourceHash':preview['sha256'],'pages':22,'parsedBlocks':len(all_blocks),
                'translatedBlocks':chosen,'resultId':result_id,'parseId':parse_id,'ownerId':a['id'],'paperId':'a-0',
                'model':config['translation']['model'],'jobs':[parsed,translated,retranslated],'sessionId':session_id,
                'history':history,'source':source,'cacheRequests':0}
        (args.root/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        stage('completed')
        print(json.dumps({'liveAcceptance':True,'parsedBlocks':len(all_blocks),'translatedBlocks':len(chosen),'requests':counters}),flush=True)
        if args.serve_seconds:
            server=make_server('127.0.0.3',7191,app,threaded=True)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            print('ISOLATED_READING_READY',flush=True)
            time.sleep(min(args.serve_seconds,3600))
            server.shutdown()
        service.shutdown();connection.close_db()

if __name__=='__main__':
    try:main()
    except Exception as error:
        # Do not stringify upstream exceptions: they may contain signed URLs.
        import traceback
        frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                for f in traceback.extract_tb(error.__traceback__)]
        print(json.dumps({'acceptanceFailed':True,'errorType':type(error).__name__,'automaticRetry':False,'frames':frames}))
        raise SystemExit(1)
