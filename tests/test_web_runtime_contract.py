import json
import threading
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class WebTaskQueueContractTests(unittest.TestCase):
    def test_executor_rejects_work_beyond_running_and_queued_capacity(self):
        from ipaper.runtime.task_queue import BoundedExecutor, QueueFull

        release = threading.Event()
        started = threading.Event()

        def blocked():
            started.set()
            release.wait(2)

        executor = BoundedExecutor(max_workers=1, max_queue=1, thread_name_prefix="test")
        first = executor.submit(blocked)
        self.assertTrue(started.wait(1))
        second = executor.submit(lambda: "queued")

        with self.assertRaises(QueueFull):
            executor.submit(lambda: "overflow")

        release.set()
        first.result(timeout=2)
        self.assertEqual(second.result(timeout=2), "queued")
        executor.shutdown(wait=True, cancel_futures=True)

    def test_shutdown_rejects_new_work_and_cancels_queued_work(self):
        from ipaper.runtime.task_queue import BoundedExecutor, ExecutorShuttingDown

        release = threading.Event()
        executor = BoundedExecutor(max_workers=1, max_queue=1, thread_name_prefix="test")
        running = executor.submit(lambda: release.wait(2))
        queued = executor.submit(lambda: "must-not-run")

        executor.shutdown(wait=False, cancel_futures=True)
        with self.assertRaises(ExecutorShuttingDown):
            executor.submit(lambda: None)

        release.set()
        running.result(timeout=2)
        for _ in range(100):
            if queued.cancelled():
                break
            time.sleep(0.01)
        self.assertTrue(queued.cancelled())

    def test_export_returns_stable_429_when_queue_is_full(self):
        from flask import Flask

        from ipaper.routes.basic_routes import export_route
        from ipaper.runtime.task_queue import QueueFull

        class FullExecutor:
            def submit(self, *_args, **_kwargs):
                raise QueueFull("full")

        export_route.export_tasks.clear()
        application = Flask(__name__)
        export_route.register_export_routes(
            application,
            papers_dir="/managed",
            task_executor=FullExecutor(),
        )
        response = application.test_client().post("/api/export/start")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["error"], "export_queue_full")
        self.assertEqual(response.headers["Retry-After"], "60")

    def test_request_routes_do_not_spawn_unbounded_threads(self):
        route_files = [
            "ipaper/routes/agent_routes/agent_summary_route.py",
            "ipaper/routes/basic_routes/export_route.py",
            "ipaper/routes/basic_routes/daily_arxiv_route.py",
            "ipaper/routes/basic_routes/settings_route.py",
        ]
        for path in route_files:
            with self.subTest(path=path):
                source = Path(path).read_text(encoding="utf-8")
                self.assertNotIn("threading.Thread", source)


class ApplicationFactoryContractTests(unittest.TestCase):
    def test_gunicorn_is_single_process_with_eight_threads(self):
        config = Path("gunicorn.conf.py").read_text(encoding="utf-8")
        self.assertIn('worker_class = "gthread"', config)
        self.assertIn("workers = 1", config)
        self.assertIn("threads = 8", config)
        self.assertIn("timeout = 300", config)
        self.assertIn("control_socket_disable = True", config)
        self.assertIn("preflight_environment()", config)
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn('"gunicorn", "--config", "gunicorn.conf.py"', dockerfile)

    def test_runtime_does_not_log_managed_storage_paths(self):
        source = Path("app.py").read_text(encoding="utf-8")
        for label in (
            "Paper directory:",
            "SQLite database:",
            "Category configuration (file):",
            "Daily arXiv settings (file):",
            "Avatar directory:",
            "Daily arXiv temporary directory:",
            "Search index database:",
        ):
            with self.subTest(label=label):
                self.assertNotIn(label, source)

    def test_compose_allows_the_full_gunicorn_graceful_window(self):
        compose = Path("docker-compose.yaml").read_text(encoding="utf-8")
        self.assertIn("stop_grace_period: 35s", compose)

    def test_release_component_matrix_matches_version_surfaces(self):
        version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]["version"]
        self.assertEqual(version, "1.9.0")
        lock = Path("uv.lock").read_text(encoding="utf-8")
        self.assertIn('name = "ipaper"\nversion = "1.9.0"', lock)
        matrix = json.loads(
            Path("docker/release-components.json").read_text(encoding="utf-8")
        )
        self.assertEqual(matrix["release"], version)
        self.assertEqual(matrix["web"]["tag"], "1.9.0")
        self.assertEqual(matrix["translation_worker"]["tag"], "1.2.0")
        self.assertFalse(matrix["translation_worker"]["publish"])
        self.assertEqual(
            matrix["document_worker"]["tag"], "1.2.0"
        )
        self.assertFalse(matrix["document_worker"]["publish"])
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG APP_VERSION=1.9.0", dockerfile)
        self.assertIn("ARG TRANSLATION_WORKER_VERSION=1.2.0", dockerfile)
        self.assertIn("ARG DOCUMENT_WORKER_VERSION=1.2.0", dockerfile)
        compose = Path("docker-compose.yaml").read_text(encoding="utf-8")
        repositories = {
            "web": "ifzzh520/ipaper",
            "translation_worker": "ifzzh520/ipaper-translation-worker",
            "document_worker": "ifzzh520/ipaper-document-worker",
        }
        for component, repository in repositories.items():
            self.assertEqual(matrix[component]["repository"], repository)
            tag = matrix[component]["tag"]
            with self.subTest(tag=tag):
                self.assertIn(f"image: {repository}:{tag}", compose)
                digest = matrix[component].get("digest")
                if digest:
                    self.assertIn(f"{tag}@{digest}", compose)

    def test_master_preflight_validates_state_and_closes_sqlite_connection(self):
        from ipaper.runtime import preflight

        store = Mock()
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ",
            {
                "IPAPER_PAPERS_DIR": str(Path(temporary) / "papers"),
                "IPAPER_SETTINGS_KEY_FILE": str(Path(temporary) / "settings.key"),
            },
            clear=False,
        ), patch.object(preflight.AuthConfig, "from_environ"), patch.object(
            preflight, "init_db_schema"
        ) as init_schema, patch.object(
            preflight, "assert_no_plaintext_credentials"
        ), patch.object(
            preflight.AgenticCredentialStore,
            "from_key_file",
            return_value=store,
        ), patch.object(
            preflight.StructuredCredentialCipher, "from_file"
        ) as structured_cipher, patch.object(
            preflight.DynamicOutboundPolicy, "from_environ"
        ), patch.object(
            preflight.LocalAuthService, "has_active_admin", return_value=True
        ), patch.object(
            preflight, "assert_tenant_migrated"
        ) as storage_check, patch.object(
            preflight, "close_db"
        ) as close_db:
            preflight.preflight_environment()

        init_schema.assert_called_once_with(preflight.DB_PATH)
        store.validate_all.assert_called_once_with()
        structured_cipher.return_value.validate_all.assert_called_once_with(preflight.DB_PATH)
        storage_check.assert_called_once()
        close_db.assert_called_once_with()

    def test_factory_initializes_once_for_one_paper_root(self):
        import app as app_module

        original = (
            app_module._application_initialized,
            app_module._application_papers_dir,
            app_module._shutdown_registered,
        )
        with tempfile.TemporaryDirectory() as temporary:
            paper_root = str(Path(temporary) / "papers")
            try:
                app_module._application_initialized = False
                app_module._application_papers_dir = None
                with patch.object(app_module, "_initialize_application") as initialize:
                    first = app_module.create_app(paper_root)
                    second = app_module.create_app(paper_root)
                    self.assertIs(first, second)
                    initialize.assert_called_once_with(str(Path(paper_root).resolve()))
                    with self.assertRaisesRegex(RuntimeError, "another paper root"):
                        app_module.create_app(str(Path(temporary) / "other"))
            finally:
                (
                    app_module._application_initialized,
                    app_module._application_papers_dir,
                    app_module._shutdown_registered,
                ) = original

    def test_database_initialization_failure_is_not_ignored(self):
        import app as app_module

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            app_module, "init_db_schema", side_effect=OSError("read only")
        ):
            with self.assertRaisesRegex(OSError, "read only"):
                app_module.init_app(str(Path(temporary) / "papers"))


if __name__ == "__main__":
    unittest.main()
