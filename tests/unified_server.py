"""Full Flask route graph with temporary synthetic data; never production state.

Run as a separate process: the application factory is process-local. The only
model origin is the existing loopback fake; no real credentials are loaded.
"""
import signal
import os
import tempfile
import json
import shutil
from pathlib import Path

from pytest import MonkeyPatch
from werkzeug.serving import make_server

from tests.workbench_support import make_workbench_fixture
from tests.workbench_reader_support import fake_openai, install_reader_fixture
from tests.test_document_upload_flow import ImmediateDocumentClient
from ipaper.document_worker.safety import DocumentLimits
from ipaper.routes.basic_routes import upload_from_pdf_route
from ipaper.routes.basic_routes import daily_arxiv_route
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.database.dao.translation_job_dao import TranslationJobDAO
from ipaper.security.identity import run_as_identity
from ipaper.database.dao.daily_arxiv_dao import DailyArxivDAO
from ipaper.security.identity import Identity, set_background_identity
from ipaper.database import connection
import app as app_module


if __name__ == "__main__":
    def stop(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with tempfile.TemporaryDirectory(prefix="ipaper-unified-") as directory, MonkeyPatch.context() as patch, fake_openai() as origin:
        fixture, first, second = make_workbench_fixture(directory, patch, count=1000)
        with fixture.app_context():
            _, invite = app_module.AUTH_SERVICE.create_invite(first["id"])
            third = app_module.AUTH_SERVICE.register("reader_pdf", "workbench-test-pass", invite)
            _, invite = app_module.AUTH_SERVICE.create_invite(first['id'])
            app_module.AUTH_SERVICE.register('empty_reader','workbench-test-pass',invite)
            _, invite = app_module.AUTH_SERVICE.create_invite(first['id'])
            reviewer=app_module.AUTH_SERVICE.register('review_admin','workbench-test-pass',invite)
            app_module.AUTH_SERVICE.update_user(first['id'], reviewer['id'], role='admin')
        application = app_module.app
        application.config.update(TESTING=True, IPAPER_START_BACKGROUND_TASKS=False)
        patch.setattr(app_module, "DB_PATH", connection.DB_PATH)
        set_background_identity(Identity(first["id"], first["username"], first["role"]))
        install_reader_fixture(application, directory, (first, second, third), patch, origin, register_routes=False)
        # Library pagination now queries admitted database rows, not a virtual
        # in-memory-only list. Back the remaining synthetic rows with real PDFs;
        # preserve the first six reader fixtures (encrypted/missing/translated).
        def admit_library_fixture():
            from ipaper.security.paths import paper_path
            for i in range(6,1000):
                paper=app_module.paper_store.get(f'a-{i}')
                target=paper_path(Path(directory)/'papers','root',f'a-{i}.pdf',create_parent=True)
                shutil.copyfile(Path(__file__).parent/'fixtures/workbench/translated.pdf',target)
                paper.filename=target.name;paper.file_path=str(target)
                PaperDAO.save_paper(paper.to_dict())
                app_module.paper_store.upsert(paper,category_id='root',category_path=['Root'])
        run_as_identity(Identity(first['id'],first['username'],first['role']),admit_library_fixture)
        with application.app_context():
            def seed_translation_history():
                job_id='00000000-0000-4000-8000-000000000112'
                TranslationJobDAO.create(job_id,'b-0')
                TranslationJobDAO.update(job_id,'completed',progress=100)
                TranslationJobDAO.append_event(job_id,kind='status',message='合成终态日志')
            run_as_identity(Identity(second['id'],second['username'],second['role']),seed_translation_history)
        app_module.init_app(directory + "/papers")
        app_module.init_categories()
        # Browser flow validates Flask upload/promotion against a deterministic
        # worker double. Real worker isolation is covered by its separate suite.
        upload_client=ImmediateDocumentClient(directory+'/upload-jobs')
        upload_client.limits=DocumentLimits(max_pdf_bytes=16*1024*1024)
        if os.getenv('IPAPER_BROWSER_REAL_WORKER') == '1':
            from urllib.parse import urlsplit
            import ipaddress
            worker_url=urlsplit(os.environ['IPAPER_DOCUMENT_WORKER_URL'])
            assert worker_url.scheme=='http' and worker_url.port==7193 and ipaddress.ip_address(worker_url.hostname).is_private
            assert os.environ['IPAPER_DOCUMENT_JOBS_ROOT'].startswith('/tmp/ipaper-unified-document-')
        else:
            register_upload=app_module.register_upload_from_pdf_routes
            patch.setattr(app_module,'register_upload_from_pdf_routes',lambda *args,**kwargs:register_upload(*args,**{**kwargs,'document_client':upload_client}))
        patch.setattr(daily_arxiv_route,'paper_store',app_module.paper_store)
        def no_external_metadata(*_args,**_kwargs):
            raise app_module.QueueFull('synthetic fixture disables outbound metadata')
        patch.setattr(app_module._daily_aux_executor,'submit',no_external_metadata)
        app_module.register_routes()
        from ipaper.routes.basic_routes import import_route
        import_route.import_tasks['synthetic-import-completed']={'owner_id':third['id'],'status':'completed','progress':100,'current':3,'total':3,'success_count':3,'message':'合成导入完成'}
        with application.app_context():
            settings_path=Path(app_module.DAILY_ARXIV_SETTINGS_FILE)
            settings=json.loads(settings_path.read_text())
            settings.update(enabled=True,keywordList=[],topicFilteringEnabled=False,categories=['cs.AI'])
            settings_path.write_text(json.dumps(settings))
            daily_root=Path(app_module.TEMP_PAPERS_DIR)/'2026-09-10'/'cs.AI'
            daily_root.mkdir(parents=True,exist_ok=True)
            pdf=daily_root/'synthetic.pdf'
            shutil.copyfile(Path(__file__).parent/'fixtures/workbench/translated.pdf',pdf)
            paper={'id':'daily-synthetic','arxiv_id':'2609.99999','title':'Daily 合成验收：从发现到阅读','authors':'iPaper tests','abstract':'自制合成样例，不是生产论文。','is_daily':True,'daily_date':'2026-09-10','fetch_date':'2026-09-10','fetch_category':'cs.AI','subject':'cs.AI','categories':['cs.AI'],'file_path':str(pdf),'artifact_status':'ready'}
            PaperDAO.save_paper(paper);DailyArxivDAO.save_candidate(paper)
        if os.getenv('IPAPER_BROWSER_REAL_WORKER') != '1':
            # PDF selections now require actual page-text validation. The
            # ordinary UI suite runs the real Worker runner locally on fixtures.
            from tests.test_processing_pipeline import LocalDocument
            processing=application.extensions['processing']
            original_pipeline=processing.pipeline
            (Path(directory)/'selection-worker').mkdir()
            def local_processing_pipeline(owner=None):
                pipeline=original_pipeline(owner)
                pipeline.document=LocalDocument(jobs_root=Path(directory)/'selection-worker')
                return pipeline
            processing.pipeline=local_processing_pipeline
        if os.getenv("IPAPER_BROWSER_STRUCTURED") == "1":
            from tests.dual_translation_support import install
            install(application, directory, (first, second, third), origin, patch)
        if os.getenv("IPAPER_BROWSER_READING_TOOLS") == "1":
            def install_reading_files():
                for paper_id, name in [('c-0','long-reading.pdf'),('c-1','scanned.pdf'),('c-2','no-outline.pdf')]:
                    paper = PaperDAO.get_paper(paper_id)
                    shutil.copyfile(Path(__file__).parent/'fixtures/reading-tools'/name,paper['file_path'])
            with application.app_context():
                run_as_identity(Identity(third['id'],third['username'],third['role']),install_reading_files)
        if os.getenv("IPAPER_BROWSER_READING_SAMPLE"):
            from tests.reading_sample_support import seed_reading_sample
            with application.app_context():
                print(seed_reading_sample(application,directory,third,os.environ["IPAPER_BROWSER_READING_SAMPLE"]),flush=True)
        if os.getenv("IPAPER_BROWSER_METADATA") == "1":
            from tests.metadata_support import install as install_metadata
            install_metadata(application)
        if os.getenv('IPAPER_BROWSER_KEYWORDS')=='1':
            from tests.keywords_support import install as install_keywords
            install_keywords(application)
        server = make_server("127.0.0.2", 7191, application, threaded=True)
        print("Synthetic unified application ready", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            app_module.shutdown_application()
