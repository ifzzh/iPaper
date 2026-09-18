"""UTC+8 business dates and unambiguous absolute instants (phase 2, S1)."""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from ipaper.timeutil import (
    APP_TZ,
    arxiv_announce_instant,
    UPSTREAM_TZ,
    app_date_str,
    app_iso,
    day_bounds_utc,
    epoch_seconds,
    format_app,
    now_utc,
    parse_stored,
    split_interval_by_app_day,
    today_app,
    to_app,
    utc_iso,
)
from ipaper.tools.basic_tools.daily_arxiv import get_arxiv_announce_date


class AbsoluteInstantTests(unittest.TestCase):
    def test_utc_iso_is_explicit_and_sorts(self):
        value = utc_iso(datetime(2026, 9, 18, 3, 22, 33, tzinfo=timezone.utc))
        self.assertEqual(value, "2026-09-18T03:22:33Z")
        earlier = utc_iso(datetime(2026, 9, 17, 11, 0, 0, tzinfo=timezone.utc))
        self.assertLess(earlier, value)

    def test_app_iso_carries_offset(self):
        self.assertEqual(
            app_iso(datetime(2026, 9, 18, 3, 22, 33, tzinfo=timezone.utc)),
            "2026-09-18T11:22:33+08:00",
        )

    def test_naive_legacy_values_are_read_as_utc(self):
        # The container ran UTC before this change, so a naive value means UTC.
        parsed = parse_stored("2026-09-18T03:22:33")
        self.assertEqual(parsed, datetime(2026, 9, 18, 3, 22, 33, tzinfo=timezone.utc))
        self.assertEqual(format_app("2026-09-18T03:22:33"), "2026-09-18 11:22:33")

    def test_epoch_values_are_absolute(self):
        instant = datetime(2026, 9, 18, 3, 22, 33, tzinfo=timezone.utc)
        self.assertEqual(epoch_seconds(instant), int(instant.timestamp()))
        self.assertEqual(parse_stored(epoch_seconds(instant)), instant)

    def test_date_only_strings_keep_date_semantics(self):
        self.assertEqual(app_date_str("2026-09-18"), "2026-09-18")
        self.assertEqual(format_app(""), "—")
        self.assertIsNone(parse_stored("not-a-date"))

    def test_to_app_converts_with_zone_data(self):
        self.assertEqual(
            to_app(datetime(2026, 9, 17, 16, 0, tzinfo=timezone.utc)).strftime("%Y-%m-%d %H:%M"),
            "2026-09-18 00:00",
        )


class BusinessDateTests(unittest.TestCase):
    def test_today_uses_the_product_zone(self):
        self.assertEqual(today_app().tzinfo if hasattr(today_app(), "tzinfo") else None, None)
        self.assertEqual(today_app(), now_utc().astimezone(APP_TZ).date())

    def test_day_bounds_are_utc_instants(self):
        start, end = day_bounds_utc(date(2026, 9, 18))
        self.assertEqual(start, datetime(2026, 9, 17, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 18, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(end - start, timedelta(days=1))

    def test_interval_splits_at_shanghai_midnight(self):
        # 23:59 -> 00:03 Beijing time spans two business days.
        start = datetime(2026, 9, 17, 15, 59, tzinfo=timezone.utc)
        end = datetime(2026, 9, 17, 16, 3, tzinfo=timezone.utc)
        self.assertEqual(
            split_interval_by_app_day(start, end),
            [("2026-09-17", 1.0), ("2026-09-18", 3.0)],
        )

    def test_interval_splits_across_month_and_year_ends(self):
        start = datetime(2026, 12, 31, 15, 30, tzinfo=timezone.utc)
        end = datetime(2026, 12, 31, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(
            split_interval_by_app_day(start, end),
            [("2026-12-31", 30.0), ("2027-01-01", 30.0)],
        )

    def test_interval_rejects_empty_and_reversed_ranges(self):
        moment = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(split_interval_by_app_day(moment, moment), [])
        self.assertEqual(
            split_interval_by_app_day(moment, moment - timedelta(minutes=1)), []
        )

    def test_interval_inside_one_day_is_not_split(self):
        start = datetime(2026, 9, 18, 1, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 18, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(split_interval_by_app_day(start, end), [("2026-09-18", 30.0)])


class ArxivAnnouncementTests(unittest.TestCase):
    """Announcement batches follow US Eastern rules, reported as UTC+8 dates."""

    def test_before_cutoff_announces_next_beijing_day(self):
        # Monday 10:00 UTC = 06:00 ET, before the 14:00 ET cutoff.
        self.assertEqual(
            get_arxiv_announce_date(datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)).date(),
            date(2026, 9, 15),
        )

    def test_after_cutoff_rolls_one_more_day(self):
        # Monday 20:00 UTC = 16:00 ET, after the cutoff.
        self.assertEqual(
            get_arxiv_announce_date(datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)).date(),
            date(2026, 9, 16),
        )

    def test_friday_after_cutoff_skips_the_weekend(self):
        self.assertEqual(
            get_arxiv_announce_date(datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc)).date(),
            date(2026, 9, 22),
        )

    def test_daylight_saving_transition_is_zone_data_driven(self):
        # US DST starts 2026-03-08; the 2026-03-09 submission is already EDT.
        before = get_arxiv_announce_date(datetime(2026, 3, 6, 10, 0, tzinfo=timezone.utc)).date()
        after = get_arxiv_announce_date(datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)).date()
        self.assertEqual(before, date(2026, 3, 7))
        self.assertEqual(after, date(2026, 3, 10))

    def test_announcement_uses_a_weekday_in_the_upstream_zone(self):
        """The window closes on an Eastern business day; the UTC+8 date may be a weekend morning."""
        for offset in range(0, 14):
            submitted = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc) + timedelta(days=offset)
            instant = arxiv_announce_instant(submitted)
            eastern_day = instant.astimezone(UPSTREAM_TZ)
            self.assertLess(eastern_day.weekday(), 5, f"{submitted} -> {eastern_day}")
            self.assertEqual(eastern_day.hour, 20)
            self.assertEqual(
                get_arxiv_announce_date(submitted).date(),
                instant.astimezone(APP_TZ).date(),
            )

    def test_upstream_zone_is_eastern(self):
        self.assertEqual(str(UPSTREAM_TZ), "America/New_York")


if __name__ == "__main__":
    unittest.main()