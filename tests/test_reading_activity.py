"""Reading activity aggregation rules (phase 2, S3)."""
import unittest
from datetime import date

from ipaper.reading_activity import (
    activity_payload,
    activity_range,
    build_activity_days,
    level_for_minutes,
)


class ActivityMergeTests(unittest.TestCase):
    def days(self, table_rows, legacy, start=date(2026, 9, 14), end=date(2026, 9, 20)):
        return build_activity_days(
            start=start,
            end=end,
            today=date(2026, 9, 18),
            table_rows=table_rows,
            legacy_totals=legacy,
        )

    def by_date(self, days):
        return {day["date"]: day for day in days}

    def test_event_rows_win_when_they_are_larger(self):
        days = self.by_date(
            self.days(
                [{"date": "2026-09-15", "seconds": 1800, "papers": 2, "attributed_rows": 60}],
                {"2026-09-15": {"total": 10, "papers": []}},
            )
        )
        self.assertEqual(days["2026-09-15"]["minutes"], 30.0)
        self.assertEqual(days["2026-09-15"]["papers"], 2)
        self.assertTrue(days["2026-09-15"]["papersKnown"])
        self.assertFalse(days["2026-09-15"]["legacy"])

    def test_stores_are_never_summed(self):
        days = self.by_date(
            self.days(
                [{"date": "2026-09-15", "seconds": 600, "papers": 1, "attributed_rows": 20}],
                {"2026-09-15": {"total": 40, "papers": []}},
            )
        )
        # 10 event minutes + 40 legacy minutes would be 50 if they were summed.
        self.assertEqual(days["2026-09-15"]["minutes"], 40.0)

    def test_legacy_only_day_reports_minutes_without_guessing_papers(self):
        days = self.by_date(self.days([], {"2026-09-16": 8}))
        entry = days["2026-09-16"]
        self.assertEqual(entry["minutes"], 8.0)
        self.assertEqual(entry["papers"], 0)
        self.assertFalse(entry["papersKnown"])
        self.assertTrue(entry["legacy"])

    def test_legacy_day_with_papers_is_attributed(self):
        days = self.by_date(
            self.days([], {"2026-09-16": {"total": 8, "papers": ["p1", "p1", "p2"]}})
        )
        entry = days["2026-09-16"]
        self.assertEqual(entry["papers"], 2)
        self.assertTrue(entry["papersKnown"])
        self.assertFalse(entry["legacy"])

    def test_empty_days_are_zero_not_legacy(self):
        entry = self.by_date(self.days([], {}))["2026-09-17"]
        self.assertEqual(entry["minutes"], 0)
        self.assertFalse(entry["legacy"])
        self.assertFalse(entry["papersKnown"])

    def test_future_and_today_are_flagged(self):
        days = self.by_date(self.days([], {}))
        self.assertTrue(days["2026-09-18"]["today"])
        self.assertFalse(days["2026-09-18"]["future"])
        self.assertTrue(days["2026-09-19"]["future"])
        self.assertFalse(days["2026-09-15"]["future"])

    def test_calendar_range_starts_on_monday_and_covers_whole_weeks(self):
        start, end = activity_range(date(2026, 9, 18), 12)  # Friday
        self.assertEqual(start.weekday(), 0)
        self.assertEqual((end - start).days + 1, 12 * 7)
        self.assertLessEqual(start, date(2026, 9, 18))
        self.assertGreaterEqual(end, date(2026, 9, 18))

    def test_range_is_clamped(self):
        start, end = activity_range(date(2026, 9, 18), 999)
        self.assertEqual((end - start).days + 1, 53 * 7)
        start, end = activity_range(date(2026, 9, 18), 0)
        self.assertEqual((end - start).days + 1, 7)

    def test_payload_summary_counts_week_and_active_days(self):
        payload = activity_payload(
            today=date(2026, 9, 18),  # Friday, week starts 2026-09-14
            weeks=2,
            table_rows=[
                {"date": "2026-09-15", "seconds": 900, "papers": 1, "attributed_rows": 30},
                {"date": "2026-09-09", "seconds": 1800, "papers": 2, "attributed_rows": 60},
            ],
            legacy_totals={"2026-09-16": {"total": 5, "papers": []}},
        )
        self.assertEqual(payload["summary"]["totalMinutes"], 50.0)
        self.assertEqual(payload["summary"]["weekMinutes"], 20.0)
        self.assertEqual(payload["summary"]["readingDays"], 3)
        self.assertEqual(payload["timezone"], "Asia/Shanghai")

    def test_legacy_number_and_dict_shapes_both_work(self):
        days = self.by_date(self.days([], {"2026-09-15": 4, "2026-09-16": {"total": 6}}))
        self.assertEqual(days["2026-09-15"]["minutes"], 4.0)
        self.assertEqual(days["2026-09-16"]["minutes"], 6.0)


class HeatLevelTests(unittest.TestCase):
    def test_zero_and_tiny_values_are_level_zero(self):
        self.assertEqual(level_for_minutes(0), 0)
        self.assertEqual(level_for_minutes(0.05), 0)

    def test_levels_increase_with_minutes(self):
        self.assertEqual(level_for_minutes(1), 1)
        self.assertEqual(level_for_minutes(15), 2)
        self.assertEqual(level_for_minutes(30), 3)
        self.assertEqual(level_for_minutes(90), 4)

    def test_level_is_capped(self):
        self.assertEqual(level_for_minutes(100000), 4)


if __name__ == "__main__":
    unittest.main()