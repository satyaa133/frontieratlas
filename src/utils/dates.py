"""
Date normalization for the freshness pipeline.

Handles:
  - Absolute ISO / RFC dates from meta tags
  - Relative phrases: "2 hours ago", "yesterday", "3d ago"
  - Missing dates -> heuristic fallback (seen-before check, caller-supplied)

Design note: we NEVER fabricate a date. If we truly cannot determine one,
we return None and the caller decides whether the "seen-before" heuristic
(Phase II, "Intelligent Heuristics") applies -- e.g. treat as fresh only if
its dedupe-key wasn't present in the last run's state file.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import dateparser  # pip install dateparser
except ImportError:  # pragma: no cover
    dateparser = None

RELATIVE_PATTERN = re.compile(
    r"(?P<num>\d+)\s*(?P<unit>second|sec|minute|min|hour|hr|day|week|month)s?\s*ago",
    re.IGNORECASE,
)

UNIT_TO_TIMEDELTA = {
    "second": "seconds", "sec": "seconds",
    "minute": "minutes", "min": "minutes",
    "hour": "hours", "hr": "hours",
    "day": "days",
    "week": "weeks",
    "month": "days",  # approximate: handled specially below
}


def _parse_relative(text: str, now: datetime) -> Optional[datetime]:
    m = RELATIVE_PATTERN.search(text.strip().lower())
    if not m:
        if "yesterday" in text.lower():
            return now - timedelta(days=1)
        if "today" in text.lower() or "just now" in text.lower():
            return now
        return None
    num = int(m.group("num"))
    unit = m.group("unit")
    if unit == "month":
        return now - timedelta(days=30 * num)
    kwargs = {UNIT_TO_TIMEDELTA[unit]: num}
    return now - timedelta(**kwargs)


def normalize_date(raw: Optional[str], *, now: Optional[datetime] = None) -> Optional[str]:
    """
    Best-effort normalization of a raw date string into ISO-8601 UTC.
    Returns None if it genuinely cannot be parsed -- caller must then
    fall back to the seen-before heuristic rather than inventing a date.
    """
    if not raw or not raw.strip():
        return None
    now = now or datetime.now(timezone.utc)
    raw = raw.strip()

    # 1. Try relative phrases first (cheap, no deps)
    rel = _parse_relative(raw, now)
    if rel:
        return rel.astimezone(timezone.utc).isoformat()

    # 2. Try native ISO parsing
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass

    # 3. Fall back to dateparser for messy human formats
    if dateparser is not None:
        dt = dateparser.parse(
            raw,
            settings={
                "RELATIVE_BASE": now,
                "TIMEZONE": "UTC",
                "RETURN_AS_TIMEZONE_AWARE": True,
            },
        )
        if dt:
            return dt.astimezone(timezone.utc).isoformat()

    return None


def is_within_last_24h(iso_date: Optional[str], *, now: Optional[datetime] = None) -> bool:
    if not iso_date:
        return False
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = now - dt
        # Allow up to 1h of clock skew for future timestamps, within 24h in past
        return -timedelta(hours=1) <= diff <= timedelta(hours=24)
    except (ValueError, TypeError):
        return False


class SeenBeforeHeuristic:
    """
    Phase II fallback: when a source has no reliable date at all, treat an
    item as "fresh" only if its dedupe key was NOT present in the previous
    run's state. This is loaded/saved as a flat JSON set of keys on disk
    (or swap for Redis in production -- see architecture.pdf).
    """

    def __init__(self, state_path: str):
        self.state_path = state_path
        self._seen: set[str] = self._load()

    def _load(self) -> set[str]:
        import json
        import os

        if not os.path.exists(self.state_path):
            return set()
        with open(self.state_path, "r") as f:
            return set(json.load(f))

    def is_new(self, dedupe_key: str) -> bool:
        return dedupe_key not in self._seen

    def mark_seen(self, dedupe_key: str) -> None:
        self._seen.add(dedupe_key)

    def save(self) -> None:
        import json

        with open(self.state_path, "w") as f:
            json.dump(sorted(self._seen), f)
