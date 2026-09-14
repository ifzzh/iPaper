"""Real PDF/chat routes backed exclusively by synthetic temporary assets and a loopback LLM."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import shutil
import threading
import time

from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.core.base_paper import Paper
from ipaper.database.dao.settings_dao import SettingsDAO
from ipaper.security.identity import Identity, run_as_identity
from ipaper.security.paths import paper_path, paper_asset_paths
from ipaper.security.credentials import generate_settings_key
from ipaper.security.agentic_credentials import AgenticCredentialStore
from ipaper.security.outbound import OutboundPolicy, ValidatedTarget, OutboundPolicyError
from ipaper.routes.agent_routes import agent_chat_route, agent_translate_route
from ipaper.tools.basic_tools.chat_history_manager import ChatHistoryManager
import app as app_module


@contextmanager
def fake_openai():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            if self.path != '/v1/chat/completions' or not 0 < length < 1024 * 1024:
                self.send_error(400); return
            data = json.loads(self.rfile.read(length))
            prompt = data['messages'][-1]['content']
            if prompt == 'startup-failure':
                self.send_error(400); return
            if not data.get('stream', False):
                system = data['messages'][0]['content']
                selection_mode = 'Translate the supplied academic excerpt' in system
                units = [] if selection_mode else json.loads(prompt)
                if selection_mode:
                    content = '合成划词译文：' + prompt
                elif 'Choose evidence for a single-paper' in system:
                    content = {'terms':['experiment','limitations','appendix'], 'unitIds':[units['index'][-1]['id']]}
                elif 'evidence-based academic analysis' in system:
                    if units and 'label' in units[0]:
                        chosen = next((u for u in units if 'controlled experiment' in u['text']),units[0])
                        evidence = [{'label':chosen['label'],'quote':chosen['text'][:90]}]
                    else:
                        evidence = [{'label':label,'quote':value['quote']} for u in units for label,value in u.get('evidence',{}).items()][:12]
                    cite = '['+evidence[0]['label']+']' if evidence else ''
                    markdown = '\n\n'.join('## '+heading+'\n\n这是一份合成验证分析，依据自制论文中的受控实验文字。'+cite for heading in ['背景与问题','核心方法','实验设置','主要发现','结论与局限','继续阅读重点'])
                    markdown += '\n\n$$E = mc^2$$\n\n| 方法 | 得分 |\n| --- | --- |\n| Ours | 95.2 |'
                    if '公众号风格图文长解读' in system:
                        markdown += '\n\n## 方法与实验的详细说明\n\n' + ('这段合成说明用于核对长篇阅读与分页，依据已提供的实验文字；不代表真实论文结论。'+cite+'\n\n')*25
                    markdown += ''.join('\n\n![论文图示，仅引用已提供图片]('+u['image']+')' for u in units if u.get('image'))
                    if 'COMPACT_EVIDENCE_NOTES:' in system:
                        evidence = evidence[:3]
                        markdown = '- 合成验证证据：当前原文记录了研究方法、实验和可确认的局限。'+cite
                    content = {'markdown':markdown,'evidence':evidence}
                else:
                    if not isinstance(units,list):
                        self.send_error(400);return
                    content = {unit['id']: '合成译文：' + unit['text'] for unit in units}
                payload = {'id':'synthetic','object':'chat.completion','created':0,'model':'fixture',
                    'choices':[{'index':0,'message':{'role':'assistant','content':content if selection_mode else json.dumps(content,ensure_ascii=False)},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':100,'completion_tokens':100,'total_tokens':200}}
                encoded=json.dumps(payload,ensure_ascii=False).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded);return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            answer = ['你好，', '这是合成回答。\n', '<img src=x onerror=alert(1)>']
            if any('VERIFIED_SOURCE_EXCERPTS' in m.get('content','') for m in data.get('messages',[])):
                answer = ['合成来源回答：实验结果见 [S1]。虚构编号 [S99] 不应成为来源链接。\n<img src=x onerror=alert(1)>']
            if prompt == 'slow':
                answer = ['开始'] + [' 合成片段'] * 15
            try:
                for index, text in enumerate(answer):
                    event = {'id':'synthetic', 'object':'chat.completion.chunk','created':0,'model':'fixture',
                             'choices':[{'index':0,'delta':{'content':text},'finish_reason':None}]}
                    self.wfile.write(('data: '+json.dumps(event,ensure_ascii=False)+'\n\n').encode())
                    self.wfile.flush()
                    if prompt == 'mid-failure' and index == 0:
                        self.wfile.write(b'data: not-json\n\n'); self.wfile.flush(); return
                    if prompt == 'slow':
                        time.sleep(.1)
                if prompt == 'usage-tail':
                    self.wfile.write(b'data: {"id":"synthetic","choices":[],"usage":{"total_tokens":10}}\n\n')
                self.wfile.write(b'data: {"id":"synthetic","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
                self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def install_reader_fixture(application, root, users, monkeypatch, origin, *, register_routes=True):
    for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        monkeypatch.delenv(name,raising=False)
    root=Path(root); (root/'papers').mkdir(exist_ok=True); store=app_module.paper_store
    application.config['IPAPER_START_BACKGROUND_TASKS']=False
    monkeypatch.setattr(agent_chat_route,'paper_store',store)
    monkeypatch.setattr(agent_chat_route,'chat_history_manager',ChatHistoryManager(store))
    monkeypatch.setattr(agent_translate_route,'paper_store',store)
    key=generate_settings_key(root/'settings.key'); monkeypatch.setenv('IPAPER_SETTINGS_KEY_FILE',str(key)); credentials=AgenticCredentialStore.from_key_file(str(key))
    class LoopbackFixturePolicy(OutboundPolicy):
        # Production policy intentionally forbids loopback. This exact-origin test
        # policy exists only in this fixture and can reach only the ephemeral fake.
        def validate(self, url, *, purpose):
            if purpose != 'ai' or url != origin + '/v1':
                raise OutboundPolicyError('test_origin_only')
            return ValidatedTarget(url, origin, ('127.0.0.1',))
    policy=LoopbackFixturePolicy(public_origins=[],private_origins=[],transfer_origins=[])
    common=dict(get_categories=lambda:{'children':[]},get_category_path=lambda *_:None,
                get_papers_in_category=lambda *_:[],agentic_settings_file='unused',credential_store=credentials,outbound_policy=policy)
    monkeypatch.setattr(app_module, 'AGENTIC_CREDENTIAL_STORE', credentials)
    monkeypatch.setattr(app_module, 'OUTBOUND_POLICY', policy)
    if register_routes:
        agent_chat_route.register_agent_chat_routes(application,**common)
        agent_translate_route.register_agent_translate_routes(application,translation_tasks={},translation_tasks_lock=threading.Lock(),
            save_paper_metadata=lambda *_:None,upload_folder=str(root/'papers'),**common)
    assets=Path(__file__).parent/'fixtures/workbench'
    with application.app_context():
        for user,prefix in zip(users,('a','b','c')):
            def seed():
                credentials.set('interpret','synthetic-test-only')
                SettingsDAO.save_setting('agentic_settings',{'llmConfigs':{'interpret':{'llmBaseUrl':origin+'/v1','llmModel':'fixture'}}})
                for i in range(1 if prefix=='b' else 6):
                    paper=store.get(f'{prefix}-{i}')
                    if not paper and prefix=='c':
                        paper=Paper(id=f'c-{i}',title='合成阅读验证文献',authors='iPaper tests',has_chinese_version=i==0)
                    if not paper: continue
                    target=paper_path(root/'papers','root',f'{prefix}-{i}.pdf',create_parent=True)
                    if i==0: shutil.copyfile(assets/'original.pdf',target)
                    elif i==1: shutil.copyfile(assets/'encrypted.pdf',target)
                    elif i==2: target.write_bytes(b'not a PDF')
                    elif i>=4: shutil.copyfile(assets/'translated.pdf',target)
                    paper.filename=target.name;paper.file_path=str(target)
                    PaperDAO.save_paper(paper.to_dict());store.upsert(paper,category_id='root',category_path=['Root'])
                    if i==0:
                        asset_paths=paper_asset_paths(root/'papers',target)
                        shutil.copyfile(assets/'translated.pdf',asset_paths.chinese_dual)
                        (asset_paths.analysis_directory/"vlm").mkdir(parents=True,exist_ok=True)
                        (asset_paths.analysis_directory/"vlm"/(target.stem+'.md')).write_text('Historical synthetic source for streaming tests.\n\nThe supplied sample describes a controlled experiment; full-document completeness is unknown.')
            run_as_identity(Identity(user['id'],user['username'],user['role']),seed)
