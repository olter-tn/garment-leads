"""Facebook timestamp parsing using dateparser.

Handles FB's relative timestamps ("2 hrs ago", "Yesterday at 5:00 PM",
"il y a 3 h", "منذ ساعتين") and absolute timestamps from abbr[@title]
attributes.

Adapted from FBScrapeIdeas/scraper/timestamp_parser.py but with:
- Proper type hints
- Returns None instead of raising on parse failure (caller handles)
- UTC timezone always
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import dateparser

logger = logging.getLogger(__name__)


def parse_fb_timestamp(timestamp_str: str) -> datetime | None:
    """Parse a Facebook timestamp string into a UTC datetime.

    Args:
        timestamp_str: e.g. "2 hrs ago", "Yesterday at 5:00 PM",
                       "il y a 3 h", "منذ ساعتين", "January 15 at 3:00 PM"

    Returns:
        timezone-aware datetime in UTC, or None if parsing fails.
    """
    if not timestamp_str or not timestamp_str.strip():
        return None

    try:
        parsed = dateparser.parse(
            timestamp_str.strip(),
            settings={
                "TIMEZONE": "UTC",
                "RETURN_AS_TIMEZONE_AWARE": True,
                "RELATIVE_BASE": datetime.now(UTC),
            },
        )
        if not parsed:
            logger.debug("dateparser returned None for: %s", timestamp_str)
            return None
        return parsed
    except Exception as exc:
        logger.warning("Timestamp parsing error for %r: %s", timestamp_str, exc)
        return None


def to_unix(ts: datetime | None) -> int | None:
    """Convert a datetime to unix timestamp, or None."""
    if ts is None:
        return None
    return int(ts.timestamp())