import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.dates import is_within_last_24h, normalize_date  # noqa: E402

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def test_relative_hours_ago():
    result = normalize_date("2 hours ago", now=NOW)
    expected = (NOW - timedelta(hours=2)).isoformat()
    assert result == expected


def test_relative_days_ago():
    result = normalize_date("3 days ago", now=NOW)
    expected = (NOW - timedelta(days=3)).isoformat()
    assert result == expected


def test_yesterday():
    result = normalize_date("Yesterday", now=NOW)
    expected = (NOW - timedelta(days=1)).isoformat()
    assert result == expected


def test_iso_passthrough():
    result = normalize_date("2026-08-26T10:00:00Z", now=NOW)
    assert result == "2026-08-26T10:00:00+00:00"


def test_missing_date_returns_none():
    assert normalize_date(None) is None
    assert normalize_date("") is None


def test_freshness_window():
    fresh = (NOW - timedelta(hours=5)).isoformat()
    stale = (NOW - timedelta(hours=30)).isoformat()
    assert is_within_last_24h(fresh, now=NOW) is True
    assert is_within_last_24h(stale, now=NOW) is False
    assert is_within_last_24h(None, now=NOW) is False


if __name__ == "__main__":
    test_relative_hours_ago()
    test_relative_days_ago()
    test_yesterday()
    test_iso_passthrough()
    test_missing_date_returns_none()
    test_freshness_window()
    print("All date normalization tests passed.")
