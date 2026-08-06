"""Deterministically turn reviewed Coach issues into one bounded problem hypothesis.

The compiler is deliberately read-only.  It does not create a score, persist a
diagnosis, or turn movement evidence into a bodily or peripheral conclusion.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any


_UNSAFE_TEXT = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:users|home|private|tmp|var)/|traceback|secret|token|password)",
    re.IGNORECASE,
)
_FAMILY_PREFIXES = {
    "static_clicking": ("static", "flick", "native_flicking"),
    "dynamic_clicking": ("dynamic",),
    "continuous_tracking": ("tracking", "continuous"),
    "target_switching": ("switch", "target_switching"),
}
_PROFILE_PREFIX = {
    "static_clicking": "static_clicking.",
    "dynamic_clicking": "dynamic_clicking.",
    "continuous_tracking": "continuous_tracking.",
    "target_switching": "target_switching.",
}
_GOAL_TERMS = {
    "terminal_control": ("刹车", "停枪", "过冲", "欠冲", "收尾", "修正"),
    "dynamic_click_confirmation": ("点击", "确认", "动态点击"),
    "target_acquisition_slow": ("起步", "获取目标", "找目标"),
    "speed_matching_and_reading": ("跟枪", "追踪", "速度", "变向", "跟丢"),
    "change_response_and_reacquisition": ("变向", "重新跟上", "重新捕获"),
    "continuous_correction_burden": ("持续跟随", "修正", "抖", "摇摆"),
    "switch_transition_and_arrival": ("切换", "转移", "下一个目标"),
}

# This table only groups reviewed Registry signals and provides stable Chinese
# presentation copy. It does not add a new measurement or causal rule.
_PROBLEM_DEFINITIONS = {
    "terminal_control": {
        "label": "到点后的收尾修正偏多",
        "question": "你更接近哪一种情况：第一次到点已经偏了，还是到点后又反复回拉？",
        "primary": "可能是到点前的减速与停枪时机还没有稳定配合，需要继续验证",
        "alternatives": [
            "也可能是目标大小、距离或本轮节奏变化造成的类似表现",
            "也可能是你刻意放慢，而不是收尾控制本身出了问题",
        ],
    },
    "dynamic_click_confirmation": {
        "label": "跟上移动目标后，点击确认仍不稳定",
        "question": "失误更多发生在目标稳定移动时，还是刚变向或变速之后？",
        "primary": "可能是进入可点击窗口后，确认点击的时机还不稳定，需要继续验证",
        "alternatives": [
            "也可能是目标恰好在点击前变向或变速",
            "也可能是本轮采用了不同的点击节奏",
        ],
    },
    "target_acquisition_slow": {
        "label": "获取移动目标偏慢",
        "question": "你通常是先看丢目标，还是准星已经跟上但会等一下才点击？",
        "primary": "可能是从发现目标到建立稳定跟随的衔接偏慢，需要继续验证",
        "alternatives": [
            "也可能是目标可见窗口或目标大小不同",
            "也可能是你为了准确率主动放慢了点击节奏",
        ],
    },
    "speed_matching_and_reading": {
        "label": "目标速度匹配不够稳定",
        "question": "这种落后或超前在长直线移动中也会持续，还是主要出现在变向、变速之后？",
        "primary": "可能是读取目标速度或方向变化后，自己的速度匹配时机偏晚，需要继续验证",
        "alternatives": [
            "也可能是有意预瞄或固定的准星偏置造成类似轨迹",
            "也可能是可用片段太短或目标条件发生了变化",
        ],
    },
    "change_response_and_reacquisition": {
        "label": "目标变化后重新跟上的代价偏高",
        "question": "目标变化后，你更像是先沿旧方向多走了一段，还是直接跟丢后再重新找回？",
        "primary": "可能是看到变化后，停止旧方向并重新捕获目标的衔接偏慢，需要继续验证",
        "alternatives": [
            "也可能是目标过小、遮挡或画面缺口增加了重新捕获难度",
            "也可能是采集或对齐偏移制造了类似表现",
        ],
    },
    "continuous_correction_burden": {
        "label": "持续跟随需要过多修正",
        "question": "跟随开始变差时，小幅修正能拉回来，还是会连续出现较大的追赶和回拉？",
        "primary": "可能是修正幅度和时机不够稳定，前一次修正又制造了下一次误差，需要继续验证",
        "alternatives": [
            "也可能是目标变化密度更高，正常需要更多修正",
            "也可能是采样或滤波条件变化造成类似波动",
        ],
    },
    "switch_transition_and_arrival": {
        "label": "切换到下一个目标的转移或落点代价偏高",
        "question": "延迟主要出现在离开旧目标前、移动途中，还是到达新目标后的收尾？",
        "primary": "可能是离开旧目标、转移和新目标落点三段中的一段衔接不稳，需要继续验证",
        "alternatives": [
            "也可能是目标间距、数量或大小变化造成的任务差异",
            "也可能是命中反馈延迟改变了离开旧目标的时机",
        ],
    },
}

_SIGNAL_PROBLEMS = {
    ("static_clicking", "reverse_ratio high"): "terminal_control",
    ("static_clicking", "submovement two-stage"): "terminal_control",
    ("static_clicking", "sparc low"): "terminal_control",
    ("dynamic_clicking", "dynamic click error high"): "dynamic_click_confirmation",
    ("dynamic_clicking", "dynamic acquisition slow"): "target_acquisition_slow",
    ("dynamic_clicking", "relative velocity mismatch"): "speed_matching_and_reading",
    ("dynamic_clicking", "post change error high"): "speed_matching_and_reading",
    ("continuous_tracking", "speed mismatch high"): "speed_matching_and_reading",
    ("continuous_tracking", "tracking lag high"): "speed_matching_and_reading",
    ("continuous_tracking", "accel mismatch high"): "change_response_and_reacquisition",
    ("continuous_tracking", "loss count high"): "change_response_and_reacquisition",
    ("continuous_tracking", "off target long"): "change_response_and_reacquisition",
    ("continuous_tracking", "sparc low"): "continuous_correction_burden",
    ("continuous_tracking", "correction burden high"): "continuous_correction_burden",
    ("target_switching", "switch transition slow"): "switch_transition_and_arrival",
    ("target_switching", "switch arrival error high"): "switch_transition_and_arrival",
}


def _safe_text(value: Any, *, maximum: int = 320) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > maximum or "\x00" in text or _UNSAFE_TEXT.search(text):
        return None
    return text


def _safe_ref(value: Any) -> str | None:
    text = _safe_text(value, maximum=192)
    if text is None or ":" not in text or not re.fullmatch(r"[A-Za-z0-9_.:@-]+", text):
        return None
    return text


def _context_family(projection: Mapping[str, Any]) -> str | None:
    candidates: list[str] = []
    scenario = projection.get("scenario")
    if isinstance(scenario, Mapping):
        analyzers = scenario.get("analyzer_refs")
        if isinstance(analyzers, list):
            candidates.extend(item for item in analyzers if isinstance(item, str))
    diagnosis = projection.get("diagnosis")
    meta = diagnosis.get("meta") if isinstance(diagnosis, Mapping) else None
    if isinstance(meta, Mapping) and isinstance(meta.get("summary_type"), str):
        candidates.append(meta["summary_type"])
    joined = " ".join(candidates).casefold()
    for family, prefixes in _FAMILY_PREFIXES.items():
        if any(prefix in joined for prefix in prefixes):
            return family
    return None


def _deterministic_metric_refs(issue: Mapping[str, Any], diagnosis: Mapping[str, Any]) -> list[str]:
    summary = diagnosis.get("summary")
    metric_refs = issue.get("metric_refs")
    if not isinstance(summary, Mapping) or not isinstance(metric_refs, list):
        return []
    valid: list[str] = []
    for raw_ref in metric_refs:
        if not isinstance(raw_ref, str):
            continue
        alternatives = (raw_ref, raw_ref.removeprefix("metric:"), f"metric:{raw_ref}")
        metric = next((summary.get(key) for key in alternatives if isinstance(summary.get(key), Mapping)), None)
        value = metric.get("value") if isinstance(metric, Mapping) else None
        if (
            isinstance(metric, Mapping)
            and metric.get("classification") == "deterministic"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            normalized = raw_ref if raw_ref.startswith("metric:") else f"metric:{raw_ref}"
            if normalized not in valid:
                valid.append(normalized)
    return valid


def _registry_entry(issue: Mapping[str, Any]) -> Mapping[str, Any] | None:
    try:
        from .coach_context import resolve_registry_teaching_entry

        entry = resolve_registry_teaching_entry(issue)
    except (ImportError, KeyError, OSError, TypeError, ValueError):
        return None
    return entry if isinstance(entry, Mapping) else None


def _candidate(
    context: Mapping[str, Any],
    *,
    context_index: int,
    issue_index: int,
) -> dict[str, Any] | None:
    projection = context.get("projection")
    if not isinstance(projection, Mapping):
        return None
    scenario = projection.get("scenario")
    if not isinstance(scenario, Mapping) or scenario.get("support_status") != "supported":
        return None
    diagnosis = projection.get("diagnosis")
    if not isinstance(diagnosis, Mapping):
        return None
    issues = diagnosis.get("issues")
    if not isinstance(issues, list) or not 0 <= issue_index < len(issues):
        return None
    issue = issues[issue_index]
    if not isinstance(issue, Mapping):
        return None
    family = _context_family(projection)
    signal = _safe_text(issue.get("signal"), maximum=120)
    problem_id = (
        _SIGNAL_PROBLEMS.get((family, signal.casefold()))
        if family is not None and signal is not None
        else None
    )
    if problem_id is None:
        return None
    metric_refs = _deterministic_metric_refs(issue, diagnosis)
    entry = _registry_entry(issue)
    context_ref = _safe_ref(context.get("context_ref"))
    analysis_ref = _safe_ref(context.get("analysis_ref"))
    if not metric_refs or entry is None or context_ref is None or analysis_ref is None:
        return None
    priority = issue.get("priority")
    return {
        "family": family,
        "problem_id": problem_id,
        "context_ref": context_ref,
        "analysis_ref": analysis_ref,
        "scenario_profile_ref": _safe_ref(scenario.get("scenario_profile_ref")),
        "context_index": context_index,
        "issue_index": issue_index,
        "signal": signal,
        "metric_refs": metric_refs,
        "observation": _safe_text(issue.get("plain_language_meaning"), maximum=500),
        "priority": priority if isinstance(priority, int) and not isinstance(priority, bool) else 999,
        "issue": issue,
        "entry": entry,
        "teachable": (
            "scenario_prescription" in entry.get("supported_uses", [])
            and isinstance(entry.get("scenario_prescription"), Mapping)
        ),
    }


def _explicit_candidates(bundle: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    contexts = bundle.get("contexts")
    if not isinstance(contexts, list):
        return []
    for context_index, context in enumerate(contexts):
        if not isinstance(context, Mapping) or context.get("kind") != "issue":
            continue
        analysis_ref = _safe_ref(context.get("analysis_ref"))
        target_ref = _safe_ref(context.get("target_ref"))
        if analysis_ref is None or target_ref is None:
            return []
        match = re.fullmatch(re.escape(analysis_ref) + r":issue:(\d+)", target_ref)
        if match is None:
            return []
        candidate = _candidate(context, context_index=context_index, issue_index=int(match.group(1)))
        return [candidate] if candidate is not None else []
    return None


def _all_candidates(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    contexts = bundle.get("contexts")
    if not isinstance(contexts, list):
        return []
    candidates: list[dict[str, Any]] = []
    for context_index, context in enumerate(contexts):
        if not isinstance(context, Mapping):
            continue
        projection = context.get("projection")
        diagnosis = projection.get("diagnosis") if isinstance(projection, Mapping) else None
        issues = diagnosis.get("issues") if isinstance(diagnosis, Mapping) else None
        if not isinstance(issues, list):
            continue
        for issue_index in range(len(issues)):
            candidate = _candidate(context, context_index=context_index, issue_index=issue_index)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _observed_counterevidence(
    candidate: Mapping[str, Any], profile: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(profile, Mapping) or profile.get("schema_version") != "aiming_profile.v1":
        return out
    prefix = _PROFILE_PREFIX[candidate["family"]]
    dimensions = profile.get("dimensions")
    if not isinstance(dimensions, list):
        return out[:2]
    for dimension in dimensions:
        if not isinstance(dimension, Mapping) or not str(dimension.get("dimension_key", "")).startswith(prefix):
            continue
        refs = dimension.get("counterexample_refs")
        if not isinstance(refs, list):
            continue
        safe_refs = [ref for item in refs if (ref := _safe_ref(item)) is not None][:2]
        if safe_refs:
            out.append({"kind": "profile_counterexample", "refs": safe_refs})
            break
    return out[:2]


def _is_comparable_repetition(group: list[dict[str, Any]]) -> bool:
    analysis_refs = {item["analysis_ref"] for item in group}
    scenario_refs = [item["scenario_profile_ref"] for item in group]
    return (
        len(analysis_refs) >= 2
        and all(ref is not None for ref in scenario_refs)
        and len(set(scenario_refs)) == 1
    )


def _goal_matches(candidate: Mapping[str, Any], bundle: Mapping[str, Any]) -> bool:
    intent = bundle.get("learner_context")
    if not isinstance(intent, Mapping):
        return False
    text = " ".join(
        value for key in ("player_problem", "desired_outcome")
        if isinstance(value := intent.get(key), str)
    )
    if not text.strip():
        return False
    return any(term in text for term in _GOAL_TERMS.get(candidate["problem_id"], ()))


def compile_coach_problem(
    bundle: Mapping[str, Any],
    *,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compile one supported functional problem, or return ``None`` fail-closed.

    An explicitly attached issue wins and remains a single issue.  Otherwise,
    only issues from the same family and functional problem are combined.
    """
    if not isinstance(bundle, Mapping) or bundle.get("schema_version") != "coach_turn_context.v1":
        return None
    explicit = _explicit_candidates(bundle)
    candidates = explicit if explicit is not None else _all_candidates(bundle)
    if not candidates:
        return None
    if explicit is not None:
        selected = candidates
    else:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for candidate in candidates:
            groups[(candidate["family"], candidate["problem_id"])].append(candidate)
        selected = min(
            groups.values(),
            key=lambda group: (
                0 if _is_comparable_repetition(group) else 1,
                0 if any(item["teachable"] for item in group) else 1,
                min(item["priority"] for item in group),
                -len({item["signal"] for item in group}),
                0 if any(_goal_matches(item, bundle) for item in group) else 1,
                min(item["context_index"] for item in group),
            ),
        )
    selected = sorted(selected, key=lambda item: (item["priority"], item["context_index"], item["issue_index"]))[:3]
    primary = selected[0]
    definition = _PROBLEM_DEFINITIONS[primary["problem_id"]]
    counterevidence = _observed_counterevidence(primary, profile)
    if _is_comparable_repetition(selected) and not counterevidence:
        evidence_strength = "repeated"
    elif len({item["signal"] for item in selected}) >= 2 or counterevidence:
        evidence_strength = "supported"
    else:
        evidence_strength = "limited"
    return {
        "schema_version": "coach_problem_hypothesis.v1",
        "family": primary["family"],
        "problem_id": primary["problem_id"],
        "problem_label": definition["label"],
        "evidence_strength": evidence_strength,
        "supporting_evidence": [{
            "context_ref": item["context_ref"],
            "analysis_ref": item["analysis_ref"],
            "issue_index": item["issue_index"],
            "signal": item["signal"],
            "metric_refs": item["metric_refs"],
            "observation": item["observation"],
        } for item in selected],
        "counterevidence_status": "observed" if counterevidence else "not_observed",
        "counterevidence": counterevidence,
        "primary_hypothesis": definition["primary"],
        "alternative_hypotheses": list(definition["alternatives"][:2]),
        "discriminator": {"kind": "question", "prompt": definition["question"]},
    }


__all__ = ["compile_coach_problem"]
