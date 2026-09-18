"""Asset status mapping and repair for Daily arXiv (phase 2, S2)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ipaper.database.db_manager import init_db_schema
from ipaper.tools.basic_tools.daily_arxiv import classify_daily_asset
from ipaper.tools.basic_tools.daily_arxiv_assets import AssetResult, DailyAssetCoordinator


class AssetStatusMappingTests(unittest.TestCase):
    def test_local_pdf_is_ready_even_without_a_preview(self):
        result = classify_daily_asset(
            pdf_downloaded=True, stored_status="retry_wait", thumbnail_exists=False
        )
        self.assertEqual(result["artifact_status"], "ready")
        self.assertFalse(result["thumbnail_ready"])
        self.assertFalse(result["asset_record_inconsistent"])

    def test_ready_record_without_a_file_is_reported_as_missing(self):
        result = classify_daily_asset(
            pdf_downloaded=False, stored_status="ready", thumbnail_exists=False
        )
        self.assertEqual(result["artifact_status"], "missing")
        self.assertTrue(result["asset_record_inconsistent"])

    def test_pending_and_failed_states_pass_through(self):
        for status in ("candidate", "queued", "downloading", "validating", "retry_wait", "failed"):
            result = classify_daily_asset(
                pdf_downloaded=False, stored_status=status, thumbnail_exists=False
            )
            self.assertEqual(result["artifact_status"], status)

    def test_missing_row_defaults_to_pending_not_ready(self):
        result = classify_daily_asset(
            pdf_downloaded=False, stored_status=None, thumbnail_exists=False
        )
        self.assertEqual(result["artifact_status"], "retry_wait")

    def test_preview_flag_is_independent_of_readability(self):
        self.assertTrue(
            classify_daily_asset(
                pdf_downloaded=True, stored_status="ready", thumbnail_exists=True
            )["thumbnail_ready"]
        )
        self.assertFalse(
            classify_daily_asset(
                pdf_downloaded=True, stored_status="ready", thumbnail_exists=False
            )["thumbnail_ready"]
        )


class AssetRepairRequeueTests(unittest.TestCase):
    """A forced enqueue must be able to repair a row that claims ready."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "ipaper.db"
        init_db_schema(str(self.db))

    def tearDown(self):
        self.temp.cleanup()

    def add(self, owner, arxiv_id, status="ready"):
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                """INSERT INTO daily_arxiv_candidates
                   (owner_id,arxiv_id,release_date,artifact_status,retry_count,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (owner, arxiv_id, "2026-09-18", status, 0, "2026-09-18T00:00:00+00:00"),
            )

    def row(self, arxiv_id):
        with sqlite3.connect(self.db) as connection:
            connection.row_factory = sqlite3.Row
            return dict(
                connection.execute(
                    "SELECT * FROM daily_arxiv_candidates WHERE arxiv_id=?", (arxiv_id,)
                ).fetchone()
            )

    def test_plain_enqueue_leaves_a_ready_row_alone(self):
        owner = "00000000-0000-0000-0000-000000000001"
        self.add(owner, "2609.30000v1")
        coordinator = DailyAssetCoordinator(
            str(self.db), lambda *args: AssetResult(True), autostart=False
        )
        coordinator.enqueue(owner, "2609.30000v1")
        self.assertEqual(self.row("2609.30000v1")["artifact_status"], "ready")

    def test_forced_enqueue_requeues_a_ready_row(self):
        owner = "00000000-0000-0000-0000-000000000001"
        self.add(owner, "2609.30001v1")
        coordinator = DailyAssetCoordinator(
            str(self.db), lambda *args: AssetResult(True), autostart=False
        )
        coordinator.enqueue(owner, "2609.30001v1", force=True)
        repaired = self.row("2609.30001v1")
        self.assertEqual(repaired["artifact_status"], "queued")
        self.assertIsNone(repaired["artifact_error_code"])

    def test_forced_enqueue_is_owner_scoped(self):
        owner_one = "00000000-0000-0000-0000-000000000001"
        owner_two = "00000000-0000-0000-0000-000000000002"
        self.add(owner_one, "2609.30002v1")
        self.add(owner_two, "2609.30002v1")
        coordinator = DailyAssetCoordinator(
            str(self.db), lambda *args: AssetResult(True), autostart=False
        )
        coordinator.enqueue(owner_one, "2609.30002v1", force=True)
        with sqlite3.connect(self.db) as connection:
            rows = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT owner_id,artifact_status FROM daily_arxiv_candidates WHERE arxiv_id=?",
                    ("2609.30002v1",),
                )
            }
        self.assertEqual(rows[owner_one], "queued")
        self.assertEqual(rows[owner_two], "ready")


if __name__ == "__main__":
    unittest.main()