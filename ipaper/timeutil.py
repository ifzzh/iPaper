"""Time-zone aware helpers: one place for UTC storage and UTC+8 business dates.

Policy (see `.devnotes/ipaper-ui-phase2-plan.md`, S1):

* Absolute instants are stored and returned as unambiguous UTC values
  (``...Z`` ISO strings or epoch seconds). Sorting and elapsed-time maths stay
  correct.
* Business dates (reading-history day buckets, "today" for Daily arXiv) use the
  product time zone ``Asia/Shanghai`` (UTC+8).
* Legacy naive timestamps were written by a UTC container, so they are read
  back as UTC. They are never rewritten in bulk.
* Bibliographic dates such as ``2026-09-18`` keep date-only semantics.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

APP_TZ_NAME = "Asia/Shanghai"
APP_TZ = ZoneInfo(APP_TZ_NAME)
# arXiv uses US Eastern time for its submission cutoff and announcement times.
UPSTREAM_TZ_NAME = "America/New_York"
UPSTREAM_TZ = ZoneInfo(UPSTREAM_TZ_NAME)
UTC = timezone.utc

DATE_ONLY = "%Y-%m-%d"
APP_DATETIME = "%Y-%m-%d %H:%M:%S"


def now_utc() -> datetime:
    """Current instant as an aware UTC datetime."""
    return datetime.now(UTC)


def now_app() -> datetime:
    """Current instant as an aware UTC+8 datetime."""
    return datetime.now(APP_TZ)


def today_app() -> date:
    """Current business date in UTC+8."""
    return now_app().date()


def utc_iso(value: Optional[datetime] = None) -> str:
    """ISO-8601 UTC string with an explicit ``Z`` suffix."""
    dt = value or now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def app_iso(value: Optional[datetime] = None) -> str:
    """ISO-8601 string in UTC+8 with an explicit ``+08:00`` offset."""
    dt = value or now_app()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(APP_TZ).isoformat(timespec="seconds")


def epoch_seconds(value: Optional[datetime] = None) -> int:
    dt = value or now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def parse_stored(value: Any) -> Optional[datetime]:
    """Parse a stored timestamp without inventing a zone.

    * aware ISO strings (``Z`` or ``+08:00``) keep their instant;
    * naive ISO strings and epoch numbers are read as UTC, matching how the
      earlier UTC container wrote them;
    * date-only strings become midnight UTC (callers that need date semantics
      should use :func:`app_date_str` instead).
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return datetime.fromisoformat(text).replace(tzinfo=UTC)
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def to_app(value: Any) -> Optional[datetime]:
    """Convert any stored value to an aware UTC+8 datetime."""
    dt = parse_stored(value)
    return dt.astimezone(APP_TZ) if dt else None


def format_app(value: Any, fmt: str = APP_DATETIME) -> str:
    """Human-readable UTC+8 text, or ``"—"`` when the value cannot be parsed."""
    dt = to_app(value)
    return dt.strftime(fmt) if dt else "—"


def app_date_str(value: Any) -> str:
    """Business date (UTC+8) for an instant; date-only input is passed through."""
    if isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-":
        try:
            date.fromisoformat(value)
            return value
        except ValueError:
            pass
    dt = to_app(value)
    return dt.strftime(DATE_ONLY) if dt else ""


def day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """UTC instants bounding one UTC+8 calendar day: ``[start, end)``."""
    start = datetime.combine(day, time.min, tzinfo=APP_TZ)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def split_interval_by_app_day(
    start: datetime, end: datetime
) -> list[tuple[str, float]]:
    """Split an interval into ``(UTC+8 date, minutes)`` buckets.

    Zero-length or reversed intervals return an empty list. The result is keyed
    by the business date so a session crossing midnight is credited to both
    days (plan S1.3 / S3).
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    if end <= start:
        return []
    local_start = start.astimezone(APP_TZ)
    local_end = end.astimezone(APP_TZ)
    buckets: dict[str, float] = {}
    cursor = local_start
    while cursor < local_end:
        next_midnight = datetime.combine(
            cursor.date() + timedelta(days=1), time.min, tzinfo=APP_TZ
        )
        chunk_end = min(next_midnight, local_end)
        minutes = (chunk_end - cursor).total_seconds() / 60.0
        key = cursor.strftime(DATE_ONLY)
        buckets[key] = buckets.get(key, 0.0) + minutes
        cursor = chunk_end
    return sorted(buckets.items())


def arxiv_announce_instant(submitted: Optional[datetime] = None) -> datetime:
    """Absolute instant of the arXiv announcement covering a submission.

    arXiv closes its daily submission window at 14:00 US Eastern and announces
    the batch at 20:00 US Eastern on the same business day. Weekends are skipped
    (the official holiday calendar is not modelled). Zone data handles the
    daylight-saving transitions; no fixed offset is added anywhere.
    """
    if submitted is None:
        submitted = now_utc()
    if submitted.tzinfo is None:
        submitted = submitted.replace(tzinfo=UTC)
    eastern = submitted.astimezone(UPSTREAM_TZ)
    cutoff = eastern.replace(hour=14, minute=0, second=0, microsecond=0)
    if eastern >= cutoff:
        cutoff += timedelta(days=1)
    while cutoff.weekday() >= 5:
        cutoff += timedelta(days=1)
    return cutoff.replace(hour=20, minute=0, second=0, microsecond=0)


def redact_for_log(value: Any) -> str:
    """UTC+8 text used by log lines and operator output."""
    return format_app(value)


class AppTimeFormatter:
    """Mixin-compatible formatter that renders logs in UTC+8.

    Used by :func:`install_logging_timezone`; kept here so workers can import it
    without pulling in the web application.
    """

    def formatTime(self, record, datefmt=None):  # noqa: N802 (logging API)
        dt = datetime.fromtimestamp(record.created, APP_TZ)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S%z")