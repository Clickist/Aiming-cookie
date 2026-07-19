"""Resolve the canonical Challenge-relative time window for a KovaaK Run.

The product timeline is epoch milliseconds with a Challenge-relative duration.
Stats supplies the precise millisecond fraction when available; Performance
supplies the date/second identity.  This module does not expose QPC or video
timestamps: those clocks are correlated at the capture boundary.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Iterable

from .performance_parser import PerformanceData, PerformanceHeader


class TimeAlignmentError(ValueError):
    """Raised when alignment evidence is missing or contradictory."""


@dataclass(frozen=True)
class TimeWindow:
    """A half-open epoch-millisecond window ``[start_ms, end_ms)``."""

    start_ms: int
    end_ms: int
    duration_ms: int
    start_source: str
    end_source: str
    filename_end_hint_ms: int | None = None
    timebase_version: str = "time_alignment.v2"
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def anchor_source(self) -> str:
        """Backward-compatible name for the start provenance."""
        return self.start_source

    @property
    def termination_source(self) -> str:
        """Backward-compatible name for the end provenance."""
        return self.end_source

    def contains(self, timestamp_ms: int | float) -> bool:
        return self.start_ms <= timestamp_ms < self.end_ms


_FILENAME_RE = re.compile(r"(?P<date>\d{4}\.\d{2}\.\d{2})-(?P<time>\d{2}\.\d{2}\.\d{2})")


def resolve_time_window(
    performance: PerformanceData | PerformanceHeader,
    *,
    stats_challenge_start: datetime | str | None = None,
    stats_challenge_start_epoch_ms: int | None = None,
    stats_event_times_seconds: Iterable[float] = (),
    performance_event_times_seconds: Iterable[float] = (),
    pause_count: int | float | str | None = None,
    pause_duration_seconds: float = 0.0,
    filename_time: datetime | str | None = None,
    local_timezone: tzinfo = timezone.utc,
) -> TimeWindow:
    """Resolve start/end anchors and provenance for one Challenge.

    A Stats event is preferred over a Performance event when both are present.
    A parsed pause event or non-zero pause duration is fail-closed in v1: the
    active-game event clock cannot provide a canonical wall-time end.  If no
    pause evidence exists, the timer profile is used as
    ``time_limit / timescale``.
    """
    header = performance.header if isinstance(performance, PerformanceData) else performance
    reject_pause_evidence(performance, pause_duration_seconds, pause_count=pause_count)
    perf_start_value = _get(header, "challenge_start_utc")
    perf_start_ms = int(perf_start_value or 0)
    if perf_start_ms <= 0:
        raise TimeAlignmentError("missing Performance challenge_start_utc")

    start_ms, start_source, warnings = _resolve_start(
        perf_start_ms,
        stats_challenge_start=stats_challenge_start,
        stats_challenge_start_epoch_ms=stats_challenge_start_epoch_ms,
        local_timezone=local_timezone,
    )

    stats_events = _valid_event_seconds(stats_event_times_seconds)
    perf_events = _valid_event_seconds(performance_event_times_seconds)

    if stats_events:
        duration_ms = _round_ms(max(stats_events) * 1000)
        end_source = "stats_event"
    elif perf_events:
        duration_ms = _round_ms(max(perf_events) * 1000)
        end_source = "performance_event"
    else:
        duration_ms = _timer_duration_ms(header, 0.0)
        end_source = "timer_profile"

    if duration_ms <= 0:
        raise TimeAlignmentError("duration_missing: resolved Challenge duration must be positive")

    filename_hint_ms = _filename_hint_ms(filename_time, start_ms, local_timezone)
    if filename_hint_ms is not None:
        warnings.append("filename_time_is_coarse_hint")

    return TimeWindow(
        start_ms=start_ms,
        end_ms=start_ms + duration_ms,
        duration_ms=duration_ms,
        start_source=start_source,
        end_source=end_source,
        filename_end_hint_ms=filename_hint_ms,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def resolve_time_alignment(*args, **kwargs) -> TimeWindow:
    """Compatibility alias for the pre-v2 resolver name."""
    return resolve_time_window(*args, **kwargs)


def _resolve_start(
    perf_start_ms: int,
    *,
    stats_challenge_start: datetime | str | None,
    stats_challenge_start_epoch_ms: int | None,
    local_timezone: tzinfo,
) -> tuple[int, str, list[str]]:
    warnings: list[str] = []
    if stats_challenge_start is None and stats_challenge_start_epoch_ms is None:
        warnings.append("stats_anchor_missing")
        return perf_start_ms, "performance_challenge_start_utc", warnings

    if stats_challenge_start_epoch_ms is not None:
        precise_ms = int(stats_challenge_start_epoch_ms)
    else:
        precise_ms = _stats_value_to_epoch_ms(stats_challenge_start, perf_start_ms, local_timezone)
    if precise_ms <= 0:
        raise TimeAlignmentError("anchor_conflict: invalid Stats Challenge Start")
    if precise_ms // 1000 != perf_start_ms // 1000:
        raise TimeAlignmentError("anchor_conflict: Stats and Performance Challenge Start disagree")
    return precise_ms, "stats_challenge_start", warnings


def _stats_value_to_epoch_ms(value: datetime | str, perf_start_ms: int, local_timezone: tzinfo) -> int:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f", "%H:%M:%S.%f"):
            try:
                parsed = datetime.strptime(text, fmt)
                if fmt == "%H:%M:%S.%f":
                    day = datetime.fromtimestamp(perf_start_ms / 1000, local_timezone).date()
                    parsed = datetime.combine(day, parsed.time())
                break
            except ValueError:
                continue
        if parsed is None:
            raise TimeAlignmentError(f"anchor_conflict: invalid Stats Challenge Start {value!r}")
    else:
        raise TimeAlignmentError("anchor_conflict: Stats Challenge Start must be datetime or string")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_timezone)
    return _round_ms(parsed.timestamp() * 1000)


def _timer_duration_ms(header: PerformanceHeader, pause_duration_seconds: float) -> int:
    if pause_duration_seconds < 0:
        raise TimeAlignmentError("duration_missing: pause_duration_seconds cannot be negative")
    profile = _get(header, "challenge_profile")
    time_limit = float(_get(profile, "time_limit") or 0.0)
    if time_limit <= 0:
        raise TimeAlignmentError("duration_missing: missing or non-positive Performance time_limit")
    timescale = float(_get(profile, "timescale") or 1.0)
    if timescale <= 0:
        raise TimeAlignmentError("duration_missing: invalid Performance timescale")
    return _round_ms((time_limit / timescale + pause_duration_seconds) * 1000)


def reject_pause_evidence(
    performance: object,
    pause_duration_seconds: float,
    *,
    pause_count: int | float | str | None = None,
) -> None:
    try:
        duration = float(pause_duration_seconds)
    except (TypeError, ValueError) as exc:
        raise TimeAlignmentError("pause_unsupported: invalid pause duration evidence") from exc
    if not math.isfinite(duration) or duration != 0.0:
        raise TimeAlignmentError(
            "pause_unsupported: v1 does not resolve non-zero pause duration evidence"
        )
    if pause_count is not None:
        try:
            count = float(pause_count)
        except (TypeError, ValueError) as exc:
            raise TimeAlignmentError("pause_unsupported: invalid Stats pause count") from exc
        if not math.isfinite(count) or count < 0 or count != int(count):
            raise TimeAlignmentError("pause_unsupported: invalid Stats pause count")
        if count > 0:
            raise TimeAlignmentError("pause_unsupported: Stats Pause Count > 0")

    for event in _get(performance, "events", ()) or ():
        if _get(event, "payload_type") != "pauseCount":
            continue
        raw_count = _get(event, "count")
        if raw_count is None:
            raw_count = _get(event, "value")
        if raw_count is None:
            continue
        try:
            count = float(raw_count)
        except (TypeError, ValueError) as exc:
            raise TimeAlignmentError("pause_unsupported: invalid pauseCount evidence") from exc
        if not math.isfinite(count) or count < 0 or count != int(count):
            raise TimeAlignmentError("pause_unsupported: invalid pauseCount evidence")
        if count > 0:
            raise TimeAlignmentError("pause_unsupported: pauseCount > 0")


def _valid_event_seconds(values: Iterable[float]) -> list[float]:
    result: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number >= 0:
            result.append(number)
    return result


def _get(value: object, key: str, default: object | None = None) -> object | None:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _filename_hint_ms(value: datetime | str | None, start_ms: int, local_timezone: tzinfo) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        match = _FILENAME_RE.search(value)
        if not match:
            raise TimeAlignmentError(f"invalid filename timestamp: {value!r}")
        parsed = datetime.strptime(
            f"{match.group('date')}-{match.group('time')}", "%Y.%m.%d-%H.%M.%S"
        )
    else:
        raise TimeAlignmentError("invalid filename timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_timezone)
    hint = _round_ms(parsed.timestamp() * 1000) - start_ms
    if hint < 0:
        hint += 24 * 60 * 60 * 1000
    if hint < 0:
        raise TimeAlignmentError("filename timestamp precedes Challenge start")
    return hint


def _round_ms(value: float) -> int:
    return int(round(value))


__all__ = [
    "TimeAlignmentError",
    "TimeWindow",
    "reject_pause_evidence",
    "resolve_time_alignment",
    "resolve_time_window",
]
