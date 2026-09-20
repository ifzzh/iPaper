"""Daily discovery scheduling: one round per owner, honest state, safe pause.

The scheduler is driven with a fake calendar and a fake fetcher so pause/disable
boundaries, duplicates and restarts can be asserted without any network, model or
real crawl.
"""
from __future__ import annotations

import threading
import time
from datetime import date

import pytest

from ipaper.tools.basic_tools.daily_arxiv import DailyArxivManager


class FakeManager(DailyArxivManager):
    def __init__(self, tmp_path, *, enabled=True, dates=("2026-09-18",)):
        super().__init__(str(tmp_path / "temp"), str(tmp_path / "settings.json"))
        self._settings = {
            "enabled": enabled,
            "categories": ["cs.AI"],
            "retentionDays": 3,
            "checkIntervalMinutes": 10,
        }
        self._dates = list(dates)
        self.fetched: list[str] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False

    # --- seams -------------------------------------------------------------
    def get_settings(self):
        return dict(self._settings)

    def get_available_dates(self):
        return []

    def cleanup_old_papers(self, retention_days):
        return 0

    def _get_recent_weekdays(self, days):
        return list(self._dates)

    def fetch_categories_for_date(self, categories, date_str=None, force=False):
        self.fetched.append(date_str)
        if self.block:
            self.started.set()
            self.release.wait(timeout=5)
        return {}


def make(tmp_path, **kwargs):
    return FakeManager(tmp_path, **kwargs)


def test_duplicate_round_is_skipped_not_stacked(tmp_path):
    manager = make(tmp_path)
    manager.block = True
    worker = threading.Thread(target=manager._do_scheduled_fetch)
    worker.start()
    assert manager.started.wait(timeout=5)

    # A second trigger while the round runs must be skipped, not stacked.
    manager._do_scheduled_fetch()
    assert manager.get_fetch_state()["round_skip_reason"] == "round_active"
    assert manager.get_fetch_state()["round_skipped_count"] == 1

    manager.release.set()
    worker.join(timeout=5)
    assert manager.fetched == [manager._dates[0]]
    state = manager.get_fetch_state()
    assert state["round_active"] is False
    assert state["round_result"] == "completed"
    assert state["round_finished_at"]


def test_disabled_and_paused_rounds_never_start(tmp_path):
    manager = make(tmp_path, enabled=False)
    manager._do_scheduled_fetch()
    assert manager.fetched == []
    assert manager.get_fetch_state()["round_skip_reason"] == "disabled"

    manager = make(tmp_path / "b", enabled=True)
    manager.settings_paused = True
    manager.pause_fetching("deploy")
    manager._do_scheduled_fetch()
    assert manager.fetched == []
    assert manager.get_fetch_state()["round_skip_reason"] == "paused"


def test_pause_between_dates_stops_the_round_with_results_kept(tmp_path):
    manager = make(tmp_path, dates=("2026-09-18", "2026-09-17", "2026-09-16"))
    original = manager.fetch_categories_for_date

    def pause_after_first(categories, date_str=None, force=False):
        result = original(categories, date_str=date_str, force=force)
        manager.pause_fetching("deploy")
        return result

    manager.fetch_categories_for_date = pause_after_first  # type: ignore[assignment]
    manager.block = False
    manager._do_scheduled_fetch()
    # The date already running keeps its results; no further date starts.
    assert manager.fetched == ["2026-09-18"]
    assert manager.get_fetch_state()["round_result"] == "completed"


def test_manual_fetch_shares_the_single_round_guard(tmp_path):
    manager = make(tmp_path)
    manager.block = True
    worker = threading.Thread(target=manager._do_scheduled_fetch)
    worker.start()
    assert manager.started.wait(timeout=5)

    outcome = manager.run_manual_fetch(["cs.AI"], "2026-09-18", force=True)
    assert outcome == {"started": False, "reason": "round_active"}

    manager.release.set()
    worker.join(timeout=5)
    outcome = manager.run_manual_fetch(["cs.AI"], "2026-09-18", force=True)
    assert outcome["started"] is True


def test_state_machine_covers_every_reported_state(tmp_path):
    manager = make(tmp_path)
    assert manager.get_fetch_state()["state"] == "stopped"

    manager._scheduler_running = True
    assert manager.get_fetch_state()["state"] == "idle"

    manager._settings["enabled"] = False
    assert manager.get_fetch_state()["state"] == "disabled"

    manager._settings["enabled"] = True
    manager._fetch_round_active = True
    assert manager.get_fetch_state()["state"] == "round_active"

    manager.pause_fetching("deploy")
    assert manager.get_fetch_state()["state"] == "paused_waiting"
    assert manager.get_fetch_state()["pause_reason"] == "deploy"

    manager._fetch_round_active = False
    assert manager.get_fetch_state()["state"] == "paused"

    manager.resume_fetching()
    assert manager.get_fetch_state()["state"] == "idle"
    assert manager.get_fetch_state()["pause_reason"] is None


def test_restart_between_rounds_leaves_a_clean_state(tmp_path):
    manager = make(tmp_path)
    manager._do_scheduled_fetch()
    first = manager.get_fetch_state()
    assert first["round_active"] is False and first["round_result"] == "completed"

    # A restart is a fresh manager: the flag is not persisted, so a new round can
    # start immediately while the previous finished earlier.
    restarted = make(tmp_path)
    restarted._do_scheduled_fetch()
    state = restarted.get_fetch_state()
    assert state["round_active"] is False
    assert state["round_skip_reason"] is None
    assert restarted.fetched == [restarted._dates[0]]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))