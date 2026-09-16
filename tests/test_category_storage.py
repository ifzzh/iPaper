import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from flask import Flask

from ipaper.routes.basic_routes.category_tree_route import (
    register_category_routes,
)
from ipaper.security.paths import category_directory
from ipaper.tools.basic_tools.category_manager import (
    find_category_node,
    get_category_path,
)


class _PaperStoreStub:
    def list_by_category(self, category_id):
        return []

    def remove(self, paper_id):
        return None


class TestCategoryStorage(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.categories = {
            "id": "root",
            "name": "Root",
            "children": [
                {
                    "id": "category-a",
                    "name": "Category A",
                    "children": [],
                },
                {
                    "id": "category-b",
                    "name": "Category B",
                    "children": [],
                },
            ],
        }
        self.saved = []
        app = Flask(__name__)
        register_category_routes(
            app,
            get_categories=lambda: self.categories,
            save_categories=self._save_categories,
            find_category_node=find_category_node,
            get_category_path=get_category_path,
            get_papers_in_category=lambda category_id, path: [],
            add_pdf_counts_to_categories=lambda categories, callback: categories,
            get_category_pdf_count=lambda categories, category_id, callback: 0,
            paper_store=_PaperStoreStub(),
            upload_folder=str(self.root),
        )
        self.client = app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def _save_categories(self, categories):
        self.saved.append(deepcopy(categories))

    def test_rejects_dangerous_create_and_rename_names(self):
        for name in ("..", "parent/child", "parent\\child", "CON", "bad\x00name"):
            with self.subTest(name=name):
                create_response = self.client.post(
                    "/api/categories",
                    json={"parent_id": "root", "name": name},
                )
                rename_response = self.client.put(
                    "/api/categories/category-a",
                    json={"name": name},
                )
                self.assertEqual(create_response.status_code, 400)
                self.assertEqual(rename_response.status_code, 400)

    def test_valid_name_is_trimmed_before_storage(self):
        response = self.client.post(
            "/api/categories",
            json={"parent_id": "root", "name": "  New Category  "},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["category"]["name"], "New Category")

    def test_retired_delete_keeps_all_assets_and_tree(self):
        target = category_directory(self.root, "category-a", create=True)
        sibling = category_directory(self.root, "category-b", create=True)
        (target / "paper.pdf").write_bytes(b"target")
        (sibling / "paper.pdf").write_bytes(b"sibling")

        response = self.client.delete("/api/categories/category-a")

        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.get_json()["error"], "physical_category_delete_retired")
        self.assertEqual((target / "paper.pdf").read_bytes(), b"target")
        self.assertEqual((sibling / "paper.pdf").read_bytes(), b"sibling")
        self.assertEqual(self.saved, [])

    def test_delete_rejects_symlink_without_mutating_tree(self):
        original = deepcopy(self.categories)
        target = category_directory(self.root, "category-a", create=True)
        outside = self.root.parent / "outside-category-test.txt"
        outside.write_text("keep")
        (target / "link").symlink_to(outside)
        try:
            response = self.client.delete("/api/categories/category-a")
            self.assertEqual(response.status_code, 410)
            self.assertEqual(self.categories, original)
            self.assertTrue(outside.exists())
        finally:
            outside.unlink(missing_ok=True)

    def test_moving_parent_changes_only_category_metadata(self):
        target = category_directory(self.root, "category-a", create=True)
        (target / "paper.pdf").write_bytes(b"paper")

        response = self.client.put(
            "/api/categories/category-a/move",
            json={"target_parent_id": "category-b"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue((target / "paper.pdf").exists())
        parent = find_category_node(self.categories, "category-b")
        self.assertEqual(parent["children"][0]["id"], "category-a")


if __name__ == "__main__":
    unittest.main()
