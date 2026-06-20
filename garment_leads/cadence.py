"""Polite-pacing helpers for the authenticated scraper.

Encapsulates:
- random jitter between requests (min/max seconds)
- requests-per-minute cap with sleep-until-next-window enforcement
- exponential backoff sequence on HTTP 429

All functions are pure (time.sleep is injected) so tests can run instantly.
"""

from __future__ import annotations

import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

DEFAULT_BACKOFF_SEQUENCE: Final[tuple[int, ...]] = (5, 15, 45)


@dataclass
class CadenceConfig:
    """Pacing knobs (also settable via env in config.py)."""

    min_delay_seconds: float = 2.0
    max_delay_seconds: float = 8.0
    max_requests_per_minute: int = 8
    max_retries: int = 3

    def __post_init__(self) -> None:
        if self.min_delay_seconds < 0:
            raise ValueError("min_delay_seconds must be >= 0")
        if self.max_delay_seconds < self.min_delay_seconds:
            raise ValueError("max_delay_seconds must be >= min_delay_seconds")
        if self.max_requests_per_minute < 1:
            raise ValueError("max_requests_per_minute must be >= 1")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")


@dataclass
class Pacer:
    """Tracks recent request timestamps and computes sleeps.

    Uses a 60-second sliding window of (timestamp, slept_seconds) pairs.
    """

    config: CadenceConfig
    sleep_fn: Callable[[float], None] = time.sleep
    rand_fn: Callable[[float, float], float] = random.uniform
    now_fn: Callable[[], float] = time.time
    _window: deque[float] = field(default_factory=deque)
    observed_delays: list[float] = field(default_factory=list)

    def _prune_window(self, now: float) -> None:
        cutoff = now - 60.0
        while self._window and self._window[0] < cutoff:
            self._window.popleft()

    def jitter_seconds(self) -> float:
        """Random delay between min and max (inclusive)."""

        delay = self.rand_fn(self.config.min_delay_seconds, self.config.max_delay_seconds)
        self.observed_delays.append(delay)
        return delay

    def seconds_to_next_slot(self, now: float | None = None) -> float:
        """How long to sleep before the next request slot opens.

        Returns 0.0 if we're under the per-minute cap. Otherwise returns the
        number of seconds until the oldest request in the window falls off.
        """

        current = now if now is not None else self.now_fn()
        self._prune_window(current)
        if len(self._window) < self.config.max_requests_per_minute:
            return 0.0
        oldest = self._window[0]
        return max(0.0, (oldest + 60.0) - current)

    def before_request(self, now: float | None = None) -> float:
        """Sleep as needed before the next request. Returns seconds slept.

        Adds a random jitter first, then waits for a free per-minute slot if
        we've exceeded the cap.
        """

        current = now if now is not None else self.now_fn()
        self._prune_window(current)
        # Per-minute slot wait (only if cap hit)
        slot_wait = self.seconds_to_next_slot(current)
        # Random jitter
        jitter = self.jitter_seconds()
        # Don't double-sleep if slot_wait already covers jitter
        sleep_for = max(slot_wait, jitter) if slot_wait > 0 else jitter
        if sleep_for > 0:
            self.sleep_fn(sleep_for)
        self._window.append(self.now_fn())
        return sleep_for

    def backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff for HTTP 429 responses.

        attempt=0 -> first retry delay, attempt=1 -> second, etc.
        Returns 0 if attempt >= max_retries.
        """

        if attempt >= self.config.max_retries:
            return 0.0
        if attempt < 0:
            return 0.0
        # Use the default sequence but cap at max_retries
        idx = min(attempt, len(DEFAULT_BACKOFF_SEQUENCE) - 1)
        return float(DEFAULT_BACKOFF_SEQUENCE[idx])

    def observed_avg_delay(self) -> float:
        """Average jitter we picked across this pacer's lifetime."""

        if not self.observed_delays:
            return 0.0
        return sum(self.observed_delays) / len(self.observed_delays)