"""Fail-closed retest outcomes derived from two existing Analysis results."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from . import history_trends, queue


_ANALYSIS_REF = re.compile(r"^analysis:([1-9][0-9]*)$")
_METRIC_REF = re.compile(r"^metric:([A-Za-z0-9][A-Za-z0-9._-]{0,159})@[A-Za-z0-9._-]+$")


class RetestDecisionError(Exception):
    pass


class AnalysisUnavailable(RetestDecisionError):
    pass


class AnalysisForbidden(RetestDecisionError):
    pass


class AnalysisInvalid(RetestDecisionError):
    pass


def _analysis_id(value: object) -> int:
    match = _ANALYSIS_REF.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise AnalysisInvalid("analysis_refs must contain stable Analysis references")
    return int(match.group(1))


def _metric_key(value: object) -> str:
    match = _METRIC_REF.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise AnalysisInvalid("expected_metric_ref must identify one versioned metric")
    return match.group(1)


async def _load_result(owner_id: str, analysis_id: int) -> dict[str, Any]:
    session = await queue.get_session(analysis_id)
    if session is None or session.get("status") != "done":
        raise AnalysisUnavailable("analysis is unavailable")
    if session.get("user_id") != owner_id:
        raise AnalysisForbidden("analysis belongs to another owner")
    result = session.get("result")
    if not isinstance(result, dict):
        raise AnalysisUnavailable("analysis result is unavailable")
    return result


async def decide_two_analysis_retest(
    owner_id: str,
    analysis_refs: Sequence[str],
    expected_metric_ref: str,
) -> dict[str, object]:
    """Map ordered ``[baseline, current]`` Analysis refs to a bounded outcome.

    ``history_trends`` remains the sole comparability authority.  No registered
    metric-change policy exists yet, so a non-zero comparable delta is recorded
    as inconclusive rather than as progress or regression.
    """
    if isinstance(analysis_refs, (str, bytes)) or len(analysis_refs) != 2:
        raise AnalysisInvalid("two ordered analysis references are required")
    baseline_id, current_id = (_analysis_id(ref) for ref in analysis_refs)
    if baseline_id == current_id:
        raise AnalysisInvalid("baseline and current analysis references must differ")
    metric_key = _metric_key(expected_metric_ref)
    baseline = await _load_result(owner_id, baseline_id)
    current = await _load_result(owner_id, current_id)
    comparison = history_trends.compare_analysis_results(current, baseline, metric_key)
    if not comparison.get("comparable"):
        reason = comparison.get("reason")
        suffix = reason if isinstance(reason, str) and reason else "unknown"
        return {
            "comparability": "not_comparable",
            "result": "coach_retest_outcome.v1:mixed_or_inconclusive",
            "limitations": [f"analysis_not_comparable:{suffix}"],
        }
    if comparison.get("delta") == 0:
        return {
            "comparability": "comparable",
            "result": "coach_retest_outcome.v1:unchanged",
            "limitations": ["analysis_comparison_equal"],
        }
    return {
        "comparability": "comparable",
        "result": "coach_retest_outcome.v1:mixed_or_inconclusive",
        "limitations": ["metric_change_policy_missing"],
    }
