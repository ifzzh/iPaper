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

    def test_ready_row_with_files_is_not_requeued_by_a_duplicate_request(self):
        owner = "00000000-0000-0000-0000-000000000001"
        self.add(owner, "2609.30001v1")
        coordinator = DailyAssetCoordinator(
            str(self.db), lambda *args: AssetResult(True), autostart=False
        )
        coordinator.enqueue(owner, "2609.30001v1", force=True)
        row = self.row("2609.30001v1")
        # Ready and nothing missing: no new work, no reset of the attempt counter.
        self.assertEqual(row["artifact_status"], "ready")
        self.assertIsNone(row["requested_stage"])

    def test_thumbnail_stage_requeues_a_ready_row_without_a_cover(self):
        owner = "00000000-0000-0000-0000-000000000001"
        self.add(owner, "2609.30003v1")
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "UPDATE daily_arxiv_candidates SET thumbnail_status='failed' WHERE arxiv_id=?",
                ("2609.30003v1",),
            )
        coordinator = DailyAssetCoordinator(
            str(self.db), lambda *args: AssetResult(True), autostart=False
        )
        coordinator.enqueue(owner, "2609.30003v1", stage="thumbnail")
        row = self.row("2609.30003v1")
        self.assertEqual(row["artifact_status"], "queued")
        self.assertEqual(row["requested_stage"], "thumbnail")

    def test_explicit_pdf_retry_requeues_a_ready_row(self):
        owner = "00000000-0000-0000-0000-000000000001"
        self.add(owner, "2609.30004v1")
        coordinator = DailyAssetCoordinator(
            str(self.db), lambda *args: AssetResult(True), autostart=False
        )
        coordinator.enqueue(owner, "2609.30004v1", force=True, stage="pdf")
        row = self.row("2609.30004v1")
        self.assertEqual(row["artifact_status"], "queued")
        self.assertEqual(row["requested_stage"], "pdf")

    def test_forced_enqueue_is_owner_scoped(self):
        owner_one = "00000000-0000-0000-0000-000000000001"
        owner_two = "00000000-0000-0000-0000-000000000002"
        self.add(owner_one, "2609.30002v1")
        self.add(owner_two, "2609.30002v1")
        coordinator = DailyAssetCoordinator(
            str(self.db), lambda *args: AssetResult(True), autostart=False
        )
        coordinator.enqueue(owner_one, "2609.30002v1", force=True, stage="pdf")
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


class VersionTolerantCandidateLookupTests(unittest.TestCase):
    """A paper keeps the bare arXiv id while the candidate keeps `vN`."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "ipaper.db"
        init_db_schema(str(self.db))
        self.owner = "00000000-0000-0000-0000-000000000001"
        self.other = "00000000-0000-0000-0000-000000000002"
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                """INSERT INTO daily_arxiv_candidates
                   (owner_id,arxiv_id,release_date,artifact_status,retry_count,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (self.owner, "2609.19915v1", "2026-09-18", "ready", 0,
                 "2026-09-18T00:00:00+00:00"),
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_normaliser_keeps_the_category_prefix(self):
        from ipaper.tools.basic_tools.daily_arxiv import normalize_arxiv_id

        self.assertEqual(normalize_arxiv_id("2609.19915v1"), "2609.19915")
        self.assertEqual(normalize_arxiv_id("2609.19915"), "2609.19915")
        self.assertEqual(normalize_arxiv_id("cs/0601001v2"), "cs/0601001")
        self.assertEqual(normalize_arxiv_id(None), "")

    def test_sql_predicate_matches_both_forms(self):
        with sqlite3.connect(self.db) as connection:
            predicate = (
                "(arxiv_id=? OR arxiv_id LIKE ? || 'v%' OR arxiv_id LIKE '%/' || ?"
                " OR arxiv_id LIKE '%/' || ? || 'v%')"
            )
            for candidate in ("2609.19915", "2609.19915v1"):
                found = connection.execute(
                    f"SELECT arxiv_id FROM daily_arxiv_candidates WHERE owner_id=? AND {predicate}",
                    (self.owner, candidate, candidate, candidate, candidate),
                ).fetchone()
                self.assertEqual(found[0], "2609.19915v1", candidate)

    def test_repair_addresses_the_versioned_row(self):
        with sqlite3.connect(self.db) as connection:
            predicate = (
                "(arxiv_id=? OR arxiv_id LIKE ? || 'v%' OR arxiv_id LIKE '%/' || ?"
                " OR arxiv_id LIKE '%/' || ? || 'v%')"
            )
            connection.execute(
                f"""UPDATE daily_arxiv_candidates
                    SET artifact_status='retry_wait', artifact_error_code='asset_file_missing'
                    WHERE owner_id=? AND {predicate} AND artifact_status='ready'""",
                (self.owner, "2609.19915", "2609.19915", "2609.19915", "2609.19915"),
            )
            row = connection.execute(
                "SELECT artifact_status,artifact_error_code FROM daily_arxiv_candidates"
            ).fetchone()
            self.assertEqual(row, ("retry_wait", "asset_file_missing"))

    def test_other_owner_is_not_matched(self):
        with sqlite3.connect(self.db) as connection:
            predicate = (
                "(arxiv_id=? OR arxiv_id LIKE ? || 'v%' OR arxiv_id LIKE '%/' || ?"
                " OR arxiv_id LIKE '%/' || ? || 'v%')"
            )
            found = connection.execute(
                f"SELECT 1 FROM daily_arxiv_candidates WHERE owner_id=? AND {predicate}",
                (self.other, "2609.19915", "2609.19915", "2609.19915", "2609.19915"),
            ).fetchone()
            self.assertIsNone(found)


def test_dao_lookup_and_repair_ignore_the_version_suffix(tmp_path, monkeypatch):
    """The DAO itself must resolve both arXiv id forms, owner scoped."""
    from contextlib import contextmanager

    from ipaper.database.connection import get_db
    from ipaper.database.dao.daily_arxiv_dao import DailyArxivDAO
    from ipaper.security.identity import (
        Identity,
        reset_background_identity,
        set_background_identity,
    )
    from tests.workbench_support import make_workbench_fixture

    @contextmanager
    def run_as(identity):
        token = set_background_identity(identity)
        try:
            yield
        finally:
            reset_background_identity(token)

    app, one, two = make_workbench_fixture(tmp_path, monkeypatch)
    with app.app_context():
        with run_as(Identity(one["id"], "reader_one", "admin")):
            db = get_db()
            db.execute(
                """INSERT INTO daily_arxiv_candidates
                   (owner_id,arxiv_id,release_date,artifact_status,retry_count,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (one["id"], "2609.19915v1", "2026-09-18", "ready", 0,
                 "2026-09-18T00:00:00+00:00"),
            )
            db.commit()
            candidate = DailyArxivDAO.get_candidate("2609.19915")
            assert candidate is not None
            assert candidate["arxiv_id"] == "2609.19915v1"
            assert DailyArxivDAO.mark_asset_file_missing("2609.19915") is True
            repaired = DailyArxivDAO.get_candidate("2609.19915")
            assert repaired["artifact_status"] == "retry_wait"
            assert repaired["artifact_error_code"] == "asset_file_missing"
        with run_as(Identity(two["id"], "reader_two", "user")):
            assert DailyArxivDAO.get_candidate("2609.19915") is None


if __name__ == "__main__":
    unittest.main()