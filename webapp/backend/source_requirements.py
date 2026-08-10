"""Fail-closed source requirements for automatic Run analysis selection.

The validator deliberately accepts an analysis snapshot-shaped mapping and
returns only bounded, path-free readiness data.  Raw source payloads remain
local implementation details.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final


MISSING_STATS: Final = "stats_missing"
MISSING_PERFORMANCE: Final = "performance_missing"
MISSING_RAW_INPUT: Final = "raw_input_missing"
MISSING_VIDEO: Final = "video_missing"
MISSING_CANONICAL_WINDOW: Final = "canonical_window_missing"

_SOURCE_KEYS: Final = ("stats", "performance", "raw_input", "video")
_AVAILABILITIES: Final = frozenset(
    {"available", "missing", "unavailable", "invalid", "not_present"}
)
def _availability(value: object) -> str:
    if not isinstance(value, Mapping):
        return "invalid"
    state = value.get("availability")
    return state if isinstance(state, str) and state in _AVAILABILITIES else "invalid"


def _valid_canonical_window(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("schema_version") != "canonical_time_window.v1":
        return False
    start_ms = value.get("start_ms")
    end_ms = value.get("end_ms")
    duration_ms = value.get("duration_ms")
    if any(
        isinstance(item, bool) or not isinstance(item, int)
        for item in (start_ms, end_ms, duration_ms)
    ):
        return False
    if start_ms < 0 or end_ms <= start_ms or duration_ms != end_ms - start_ms:
        return False
    if value.get("window_semantics") != "half_open":
        return False
    if value.get("timebase_version") not in {"time_alignment.v1", "time_alignment.v2"}:
        return False
    for field in ("start_source", "end_source"):
        source = value.get(field)
        if not isinstance(source, str) or not source or len(source) > 80:
            return False
    warnings = value.get("warnings")
    return isinstance(warnings, list) and all(
        isinstance(item, str) and item and len(item) <= 160 for item in warnings
    )


def validate_source_requirements(bundle: Mapping[str, object] | object) -> dict[str, object]:
    """Return bounded source readiness and the highest automatic analysis tier.

    ``bundle`` follows the private snapshot shape: Stats, Performance and
    video live under ``sources``; Raw Input is the top-level ``trace``.  The
    returned mapping is intentionally path-free and stable for API/Coach use.
    """
    sources = bundle.get("sources") if isinstance(bundle, Mapping) else None
    sources = sources if isinstance(sources, Mapping) else {}
    source_values: dict[str, object] = {
        "stats": sources.get("stats"),
        "performance": sources.get("performance"),
        "raw_input": bundle.get("trace") if isinstance(bundle, Mapping) else None,
        "video": sources.get("video"),
    }
    availability = {
        key: _availability(source_values[key]) for key in _SOURCE_KEYS
    }
    canonical = (
        "available"
        if isinstance(bundle, Mapping)
        and _valid_canonical_window(bundle.get("canonical_time_window"))
        else "invalid"
        if isinstance(bundle, Mapping) and bundle.get("canonical_time_window") is not None
        else "missing"
    )
    availability["canonical_window"] = canonical

    code_for = {
        "stats": MISSING_STATS,
        "performance": MISSING_PERFORMANCE,
        "raw_input": MISSING_RAW_INPUT,
        "video": MISSING_VIDEO,
        "canonical_window": MISSING_CANONICAL_WINDOW,
    }
    missing = [
        code_for[key]
        for key in (*_SOURCE_KEYS, "canonical_window")
        if availability[key] != "available"
    ]
    available = lambda *keys: all(availability[key] == "available" for key in keys)
    supported_modes = [
        mode
        for mode, required in (
            ("multimodal", ("stats", "performance", "raw_input", "video", "canonical_window")),
            ("input_native", ("stats", "performance", "raw_input", "canonical_window")),
            ("video_fallback", ("stats", "video")),
        )
        if available(*required)
    ]
    selected_mode = supported_modes[0] if supported_modes else None
    return {
        "ready": selected_mode is not None,
        "missing": missing,
        "availability": availability,
        "supported_modes": supported_modes,
        "selected_mode": selected_mode,
        "summary": {
            "mode": selected_mode,
            "source_count": len(_SOURCE_KEYS),
            "canonical_window": canonical,
        },
    }


__all__ = [
    "MISSING_CANONICAL_WINDOW",
    "MISSING_PERFORMANCE",
    "MISSING_RAW_INPUT",
    "MISSING_STATS",
    "MISSING_VIDEO",
    "validate_source_requirements",
]
