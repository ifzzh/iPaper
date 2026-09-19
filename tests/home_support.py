"""Independent dashboard accounts and synthetic files for browser acceptance.

These rows exist only in unified_server's temporary database. They keep home
tests from changing the library/topic/history fixtures used by other specs.
"""
import shutil
from pathlib import Path

from ipaper.core.base_paper import Paper
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.security.identity import Identity, run_as_identity
from ipaper.security.paths import paper_path, paper_asset_paths


def install(application, directory, admin):
    import app as app_module

    with application.app_context():
        service = app_module.AUTH_SERVICE
        _, invitation = service.create_invite(admin["id"])
        user = service.register("home_reader", "workbench-test-pass", invitation)
        _, invitation = service.create_invite(admin["id"])
        service.register("home_empty", "workbench-test-pass", invitation)

        def seed():
            titles = [
                "AutoSci: A Memory-Centric Agentic System for the Full Scientific Research Lifecycle",
                "Memory Compression for High-Fanout Agent Sandboxes",
                "MicroIntent: Intent-Based Placement Strategy for Microservice Application in the Compute Continuum Using LLMs",
            ]
            assets = Path(__file__).parent / "fixtures/workbench"
            for index in range(11):
                target = paper_path(Path(directory) / "papers", "root", f"h-{index}.pdf", create_parent=True)
                shutil.copyfile(assets / ("original.pdf" if index == 0 else "translated.pdf"), target)
                paper = Paper(
                    id=f"h-{index}", title=titles[index] if index < 3 else f"隔离样例 {index + 1} · 多模态学习与可靠推理",
                    authors="隔离验收样例 · 非正式数据", year="2026",
                    filename=target.name, file_path=str(target),
                    has_chinese_version=index == 0,
                )
                PaperDAO.save_paper(paper.to_dict())
                app_module.paper_store.upsert(paper, category_id="root", category_path=["Root"])
                if index == 0:
                    paths = paper_asset_paths(Path(directory) / "papers", target)
                    paths.chinese_dual.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(assets / "translated.pdf", paths.chinese_dual)

        run_as_identity(Identity(user["id"], user["username"], user["role"]), seed)
