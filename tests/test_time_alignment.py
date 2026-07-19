from datetime import datetime, timezone, timedelta

import pytest

from kovaak_tracker.performance_parser import (
    ChallengeProfile,
    PerformanceData,
    PerformanceEvent,
    PerformanceHeader,
)
from kovaak_tracker.time_alignment import TimeAlignmentError, resolve_time_window


LOCAL_TZ = timezone(timedelta(hours=8))


def performance(*, start_ms=1_699_897_600_000, time_limit=60.0, timescale=1.0,
                 bot_max_lives=(), end_kills=0.0, end_damage=0.0, events=()):
    return PerformanceData(
        header=PerformanceHeader(
            scenario_name="scenario",
            challenge_start_utc=start_ms,
            challenge_profile=ChallengeProfile(
                time_limit=time_limit,
                timescale=timescale,
                bot_max_lives=tuple(bot_max_lives),
                end_challenge_after_kills=end_kills,
                end_challenge_after_damage=end_damage,
            ),
        ),
        events=tuple(events),
    )


def test_reconstructs_millisecond_start_from_stats_and_perf_second():
    result = resolve_time_window(
        performance(),
        stats_challenge_start="01:46:40.321",
        local_timezone=LOCAL_TZ,
    )

    assert result.start_ms == 1_699_897_600_321
    assert result.start_source == "stats_challenge_start"
    assert result.end_ms == result.start_ms + 60_000
    assert result.timebase_version == "time_alignment.v2"


def test_timescale_timer_window_without_pause():
    result = resolve_time_window(
        performance(time_limit=60.0, timescale=0.7),
        stats_challenge_start="01:46:40.321",
        local_timezone=LOCAL_TZ,
    )

    assert result.duration_ms == 85_714
    assert result.end_source == "timer_profile"


def test_pause_count_event_fails_closed_before_using_active_event_time():
    with pytest.raises(TimeAlignmentError, match="pause_unsupported"):
        resolve_time_window(
            performance(
                events=(
                    PerformanceEvent(
                        timestamp=11.484201,
                        payload_type="pauseCount",
                        count=1,
                    ),
                ),
            ),
            stats_challenge_start="01:46:40.321",
            performance_event_times_seconds=[59.944450],
            local_timezone=LOCAL_TZ,
        )


def test_coarse_pause_duration_fails_closed():
    with pytest.raises(TimeAlignmentError, match="pause_unsupported"):
        resolve_time_window(
            performance(time_limit=60.0, timescale=0.7),
            stats_challenge_start="01:46:40.321",
            pause_duration_seconds=7.0,
            local_timezone=LOCAL_TZ,
        )


def test_stats_pause_count_fails_closed_without_performance_pause_event():
    with pytest.raises(TimeAlignmentError, match="pause_unsupported"):
        resolve_time_window(
            performance(),
            stats_challenge_start="01:46:40.321",
            pause_count=1,
            local_timezone=LOCAL_TZ,
        )


def test_zero_pause_count_event_preserves_normal_window():
    result = resolve_time_window(
        performance(
            events=(
                PerformanceEvent(
                    timestamp=0.0,
                    payload_type="pauseCount",
                    count=0,
                ),
            ),
        ),
        stats_challenge_start="01:46:40.321",
        local_timezone=LOCAL_TZ,
    )

    assert result.duration_ms == 60_000
    assert result.end_source == "timer_profile"


def test_event_terminated_run_uses_latest_stats_event_over_filename_hint():
    result = resolve_time_window(
        performance(time_limit=1_000.0, bot_max_lives=(5,)),
        stats_challenge_start="01:46:40.321",
        stats_event_times_seconds=[113.944],
        performance_event_times_seconds=[113.934],
        filename_time=datetime(2023, 11, 14, 1, 48, 33, tzinfo=LOCAL_TZ),
        local_timezone=LOCAL_TZ,
    )

    assert result.duration_ms == 113_944
    assert result.end_source == "stats_event"
    assert result.filename_end_hint_ms == 112_679
    assert "filename_time_is_coarse_hint" in result.warnings


def test_cross_midnight_filename_hint_is_resolved_on_next_local_day():
    result = resolve_time_window(
        performance(start_ms=1_702_569_540_000),
        stats_challenge_start="23:59:00.500",
        filename_time="2023.12.15-00.00.03",
        local_timezone=LOCAL_TZ,
    )

    assert result.filename_end_hint_ms == 62_500
    assert "filename_time_is_coarse_hint" in result.warnings


def test_window_is_half_open():
    result = resolve_time_window(
        performance(),
        stats_challenge_start="01:46:40.000",
        local_timezone=LOCAL_TZ,
    )

    assert result.contains(result.start_ms)
    assert not result.contains(result.end_ms)


def test_missing_stats_start_falls_back_explicitly_to_perf_anchor():
    result = resolve_time_window(performance(), local_timezone=LOCAL_TZ)

    assert result.start_ms == 1_699_897_600_000
    assert result.start_source == "performance_challenge_start_utc"
    assert "stats_anchor_missing" in result.warnings


def test_conflicting_stats_start_fails_closed():
    with pytest.raises(TimeAlignmentError, match="anchor_conflict"):
        resolve_time_window(
            performance(),
            stats_challenge_start="01:47:41.321",
            local_timezone=LOCAL_TZ,
        )


def test_missing_duration_fails_closed():
    with pytest.raises(TimeAlignmentError, match="duration_missing"):
        resolve_time_window(
            performance(time_limit=0.0),
            stats_challenge_start="01:46:40.321",
            local_timezone=LOCAL_TZ,
        )
