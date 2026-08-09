import json

import pytest

from webapp.backend.source_requirements import (
    MISSING_CANONICAL_WINDOW,
    MISSING_PERFORMANCE,
    MISSING_RAW_INPUT,
    MISSING_STATS,
    MISSING_VIDEO,
    validate_source_requirements,
)


def _window() -> dict:
    return {
        "schema_version": "canonical_time_window.v1",
        "timebase_version": "time_alignment.v2",
        "start_ms": 1000,
        "end_ms": 2000,
        "duration_ms": 1000,
        "start_source": "performance_challenge_start",
        "end_source": "stats_challenge_end",
        "warnings": [],
        "window_semantics": "half_open",
    }


def _source(*, kind: str) -> dict:
    return {
        "availability": "available",
        "artifact_ref": f"run:42:{kind}:stable",
        "path": f"C:/private/{kind}.source",
        "private_payload": {"raw": "must not cross the boundary"},
    }


def _complete_snapshot() -> dict:
    return {
        "schema_version": "analysis_input_snapshot.v3",
        "sources": {
            "stats": _source(kind="stats"),
            "performance": _source(kind="performance"),
            "video": _source(kind="video"),
        },
        "trace": _source(kind="raw_input"),
        "canonical_time_window": _window(),
    }


def test_complete_bundle_is_ready_and_has_bounded_public_summary():
    result = validate_source_requirements(_complete_snapshot())

    assert result == {
        "ready": True,
        "missing": [],
        "availability": {
            "stats": "available",
            "performance": "available",
            "raw_input": "available",
            "video": "available",
            "canonical_window": "available",
        },
        "summary": {
            "mode": "multimodal",
            "source_count": 4,
            "canonical_window": "available",
        },
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert "C:/private" not in serialized
    assert "must not cross" not in serialized


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("stats", MISSING_STATS),
        ("performance", MISSING_PERFORMANCE),
        ("trace", MISSING_RAW_INPUT),
        ("video", MISSING_VIDEO),
        ("canonical_time_window", MISSING_CANONICAL_WINDOW),
    ],
)
def test_missing_required_source_returns_stable_code(field: str, code: str):
    snapshot = _complete_snapshot()
    if field == "trace":
        snapshot.pop("trace")
    else:
        snapshot["sources"].pop(field, None)
        if field == "canonical_time_window":
            snapshot.pop(field)

    result = validate_source_requirements(snapshot)

    assert result["ready"] is False
    assert result["missing"] == [code]
    assert result["summary"] == {
        "mode": "multimodal",
        "source_count": 4,
        "canonical_window": "available" if field != "canonical_time_window" else "missing",
    }


def test_unavailable_and_malformed_sources_fail_closed_without_echoing_values():
    snapshot = _complete_snapshot()
    snapshot["sources"]["performance"] = {
        "availability": "unavailable",
        "path": "C:/private/performance.perf",
    }
    snapshot["sources"]["video"] = "raw video payload"
    snapshot["canonical_time_window"] = {"start_ms": 2, "end_ms": 1}

    result = validate_source_requirements(snapshot)

    assert result["ready"] is False
    assert result["missing"] == [
        MISSING_PERFORMANCE,
        MISSING_VIDEO,
        MISSING_CANONICAL_WINDOW,
    ]
    assert result["availability"] == {
        "stats": "available",
        "performance": "unavailable",
        "raw_input": "available",
        "video": "invalid",
        "canonical_window": "invalid",
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert "C:/private" not in serialized
    assert "raw video payload" not in serialized


def test_unknown_fields_do_not_become_requirements_or_public_output():
    snapshot = _complete_snapshot()
    snapshot["sources"]["untrusted_extra"] = {
        "availability": "available",
        "path": "C:/private/extra.bin",
    }

    result = validate_source_requirements(snapshot)

    assert result["ready"] is True
    assert result["summary"]["source_count"] == 4
    assert "untrusted_extra" not in json.dumps(result, ensure_ascii=False)
