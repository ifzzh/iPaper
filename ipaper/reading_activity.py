"""Reading-activity aggregation in UTC+8 (phase 2, S3).

Pure helpers so the merge rules can be tested without Flask or a browser. The
route only gathers rows; every decision about what may be claimed lives here.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional

from .timeutil import APP_TZ_NAME


def _legacy_entry(entry: Any) -> tuple[float, List[str]]:
    if isinstance(entry, dict):
        return float(entry.get("total") or 0), list(entry.get("papers") or [])
    if isinstance(entry, (int, float)):
        return float(entry), []
    return 0.0, []


def build_activity_days(
    *,
    start: date,
    end: date,
    today: date,
    table_rows: Iterable[Dict[str, Any]],
    legacy_totals: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Merge the per-event rows with the legacy day aggregate.

    A day's minutes are the **larger** of the two stores, never their sum: both
    are written by the same reader tick, so summing would double count, while a
    historical day that only exists in one store stays visible. ``duration`` in
    the event rows is seconds.

    ``papersKnown`` is false when only a legacy total exists, so the UI can show
    minutes without inventing a paper count.
    """
    rows = {row["date"]: row for row in table_rows if row.get("date")}
    days: List[Dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        row = rows.get(key)
        table_minutes = round(float((row or {}).get("seconds") or 0) / 60.0, 1)
        legacy_minutes, legacy_papers = _legacy_entry(legacy_totals.get(key))
        minutes = max(table_minutes, legacy_minutes)

        attributed = int((row or {}).get("attributed_rows") or 0)
        if attributed:
            papers = int((row or {}).get("papers") or 0)
            papers_known = True
            legacy_only = False
        elif legacy_papers:
            papers = len(set(legacy_papers))
            papers_known = True
            legacy_only = False
        else:
            papers = 0
            papers_known = False
            legacy_only = minutes > 0

        days.append(
            {
                "date": key,
                "minutes": round(minutes, 1),
                "papers": papers,
                "papersKnown": papers_known,
                "legacy": legacy_only,
                "future": cursor > today,
                "today": cursor == today,
            }
        )
        cursor += timedelta(days=1)
    return days


def summarize(days: Iterable[Dict[str, Any]], today: date) -> Dict[str, Any]:
    """Total, current-week and active-day figures for the legend/summary row."""
    week_start = today - timedelta(days=today.weekday())
    week_key = week_start.isoformat()
    today_key = today.isoformat()
    total = 0.0
    week = 0.0
    active = 0
    for day in days:
        minutes = float(day.get("minutes") or 0)
        total += minutes
        if week_key <= day["date"] <= today_key:
            week += minutes
        if minutes > 0:
            active += 1
    return {
        "totalMinutes": round(total, 1),
        "weekMinutes": round(week, 1),
        "readingDays": active,
    }


def activity_range(today: date, weeks: int) -> tuple[date, date]:
    """Two-endpoint calendar range: whole weeks ending on the current Sunday."""
    weeks = max(1, min(int(weeks), 53))
    start = today - timedelta(days=today.weekday() + (weeks - 1) * 7)
    end = start + timedelta(days=weeks * 7 - 1)
    return start, end


def activity_payload(
    *,
    today: date,
    weeks: int,
    table_rows: Iterable[Dict[str, Any]],
    legacy_totals: Dict[str, Any],
) -> Dict[str, Any]:
    start, end = activity_range(today, weeks)
    days = build_activity_days(
        start=start,
        end=end,
        today=today,
        table_rows=table_rows,
        legacy_totals=legacy_totals,
    )
    return {
        "timezone": APP_TZ_NAME,
        "range": {"start": start.isoformat(), "end": end.isoformat(), "weeks": weeks},
        "summary": summarize(days, today),
        "days": days,
    }


def level_for_minutes(minutes: float, thresholds: Optional[List[float]] = None) -> int:
    """Intensity level 0..4 used by the heat map; 15-minute steps by default."""
    steps = thresholds or [0.1, 15, 30, 60]
    if minutes < steps[0]:
        return 0
    level = 1
    for index, threshold in enumerate(steps[1:], start=1):
        if minutes >= threshold:
            level = index + 1
    return min(level, 4)