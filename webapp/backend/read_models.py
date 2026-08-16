"""Versioned, path-free read models for the formal desktop frontend.

The database and native capture layers keep private paths and diagnostics.  This
module is the single projection boundary used by the HTTP routes and contract
tests, so a read failure cannot be silently represented as an empty collection.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

PRODUCT_STATE_SCHEMA_VERSION = "product_state.v1"
CAPTURE_STATUS_SCHEMA_VERSION = "capture_status.v1"
TASK_LIST_SCHEMA_VERSION = "task_list.v1"
TASK_DETAIL_SCHEMA_VERSION = "task_detail.v1"
ANALYSIS_DATA_SCHEMA_VERSION = "frontend_analysis_data.v1"
ANALYSIS_FAMILY_DATA_SCHEMA_VERSION = "frontend_analysis_family_data.v1"
CURRENT_TRAINING_SCHEMA_VERSION = "current_training.v1"

# Registry entries and stored plan revisions are immutable. Localize only the
# reviewed public projection, keyed by the stable knowledge and scenario refs.
_CURRENT_TRAINING_ZH_CN = {
    "knowledge:static.flicking-terminal-control@3": {
        "scenario_profile_ref": "scenario:static.1wall_6targets_small@1",
        "practice_condition": "保持完全相同的静态场景条件，只测试一个终点控制提示。",
        "cue": "只使用一个动作效果提示：先受控地到达目标，再让点击跟随已经稳定的瞄点。",
        "dose_guardrail": (
            "使用能够清楚判断表现的难度版本，每次只改变一个任务变量；"
            "如果出现不适，或与提示无关的表现质量明显下降，就停止或降低难度。"
        ),
        "review_date": "下一次可比训练后复查。",
    },
    "knowledge:dynamic.click-error-and-acquisition@3": {
        "scenario_profile_ref": "scenario:dynamic.pasu_small_reload@1",
        "practice_condition": "保持完全相同的动态场景条件，每次只改变一个易于辨认的运动变量。",
        "cue": (
            "选择运动清晰可读的目标；先判断它当前的运动并完成捕获，"
            "再进行一次有意识的点击，不要追着分数打。"
        ),
        "dose_guardrail": (
            "先降低一个运动变量并保持结果质量；不要规定统一的训练时长或命中率目标。"
        ),
        "review_date": "下一次可比训练后复查。",
    },
    "knowledge:dynamic.speed-matching-and-reading@3": {
        "scenario_profile_ref": "scenario:dynamic.pasu_small_reload@1",
        "practice_condition": "保持相同的动态场景条件，每次只改变一个运动特征。",
        "cue": (
            "在一段运动清晰可读的长距离横移中，先匹配方向和速度再点击；"
            "确认目标改变运动后，先重新判断新的运动。"
        ),
        "dose_guardrail": (
            "每个训练组只改变速度、变向密度或目标大小中的一项，"
            "并把高难变体保留为压力测试。"
        ),
        "review_date": "下一次可比训练后复查。",
    },
    "knowledge:tracking.predictable-speed-matching@3": {
        "scenario_profile_ref": "scenario:tracking.whj_smooth_strafe_sphere_easy@1",
        "practice_condition": "保持完全相同的可预测运动条件，只测试稳定的速度匹配。",
        "cue": (
            "在运动清晰可读的阶段保持相对速度稳定，使用小幅修正，避免反复大幅追回。"
        ),
        "dose_guardrail": (
            "先使用运动清晰可读的脚本；测试迁移时，只改变速度或运动阶段中的一项。"
        ),
        "review_date": "下一次可比训练后复查。",
    },
    "knowledge:switching.transition-and-arrival@3": {
        "scenario_profile_ref": "scenario:switching.beants_larger@1",
        "practice_condition": (
            "保持完全相同的 beanTS Larger 场景条件；"
            "每个训练组只关注切换移动或到达稳定中的一项。"
        ),
        "cue": (
            "分别练习切换移动和到达：确认上一个目标后离开，直接移动到下一个目标，"
            "并在第一枪前完成稳定。"
        ),
        "dose_guardrail": (
            "每个训练组只改变布局距离、方向或目标数量中的一项，并持续关注结果质量。"
        ),
        "review_date": "下一次可比训练后复查。",
    },
}

_TASK_PHASE_LABELS = {
    "preparing_training_record": "Preparing training record",
    "aligning_input_events": "Aligning input events",
    "computing_kinematics": "Computing movement metrics",
    "analyzing_video": "Analyzing video",
    "generating_diagnostics": "Generating diagnostics",
}
_TASK_STATE_LABELS = {
    "importing": "Importing",
    "queued": "Queued",
    "running": "Running",
    "done": "Done",
    "failed": "Failed",
    "retrying": "Retrying",
}
_FAILURE_DOMAINS = {
    "source_file",
    "alignment",
    "kinematics",
    "video",
    "provider",
    "coach",
    "network",
}
_FORBIDDEN_TEXT = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:Users|home|private|tmp|var)/|Traceback|secret|token|password)",
    re.IGNORECASE,
)
_INTERNAL_REF_TEXT = re.compile(
    r"\b(?:analysis|diagnosis|knowledge|metric|scenario|retest-spec|plan(?:-item)?):"
    r"[A-Za-z0-9:._@+-]+",
    re.IGNORECASE,
)
_PUBLIC_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._@+-]{0,239}$")
_EVENT_KIND = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_PRESENTATION_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_T = TypeVar("_T")


def _error_payload(code: str, message: str) -> dict[str, object]:
    return {
        "code": code,
        "message": message,
        "retryable": False,
    }


def _finite_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return int(round(numeric))


def _bounded_evenly(items: Sequence[_T], maximum: int) -> list[_T]:
    if len(items) <= maximum:
        return list(items)
    return [items[round(index * (len(items) - 1) / (maximum - 1))] for index in range(maximum)]


def _safe_analysis_limitations(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        item
        for item in value
        if isinstance(item, str)
        and 0 < len(item) <= 240
        and not _FORBIDDEN_TEXT.search(item)
    ))


def _safe_presentation_scenario(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    scenario = value.strip()
    if (
        not scenario
        or len(scenario) > 160
        or _FORBIDDEN_TEXT.search(scenario)
        or any(ord(char) < 32 for char in scenario)
    ):
        return None
    return scenario


def _safe_presentation_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not _PRESENTATION_TIMESTAMP.fullmatch(value):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def build_record_presentation_label(
    *,
    scenario: object,
    training_at: object,
    analysis_completed_at: object,
) -> str:
    """Create the sole user-facing identity for a Run or Analysis record."""
    safe_scenario = _safe_presentation_scenario(scenario) or "未命名场景"
    safe_training_at = _safe_presentation_timestamp(training_at) or "训练时间未知"
    safe_analysis_at = _safe_presentation_timestamp(analysis_completed_at) or "分析尚未完成"
    return f"{safe_scenario} | 训练：{safe_training_at} | 分析：{safe_analysis_at}"


def _project_event_rows(
    artifact: Mapping[str, object], *, window_start_ms: int, window_end_ms: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    events_by_ref: dict[str, dict[str, object]] = {}
    bundles = artifact.get("event_bundles")
    for bundle in bundles if isinstance(bundles, list) else []:
        if not isinstance(bundle, Mapping):
            continue
        events = bundle.get("events")
        for raw in events if isinstance(events, list) else []:
            if not isinstance(raw, Mapping):
                continue
            event_ref = raw.get("event_id")
            kind = raw.get("event_kind")
            start_ms = _finite_int(raw.get("start_ms"))
            if (
                not isinstance(event_ref, str)
                or not _PUBLIC_REF.fullmatch(event_ref)
                or not isinstance(kind, str)
                or not _EVENT_KIND.fullmatch(kind)
                or start_ms is None
                or not window_start_ms <= start_ms < window_end_ms
            ):
                continue
            events_by_ref.setdefault(event_ref, {
                "event_ref": event_ref,
                "kind": kind,
                "relative_ms": start_ms - window_start_ms,
            })
    events = sorted(events_by_ref.values(), key=lambda item: (item["relative_ms"], item["event_ref"]))
    counts: dict[str, int] = {}
    for event in events:
        kind = str(event["kind"])
        counts[kind] = counts.get(kind, 0) + 1
    distribution = [
        {"kind": kind, "count": count}
        for kind, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    first_by_kind: dict[str, dict[str, object]] = {}
    for event in events:
        first_by_kind.setdefault(str(event["kind"]), event)
    representatives = list(first_by_kind.values())
    if len(representatives) >= 128:
        return _bounded_evenly(representatives, 128), distribution
    selected_refs = {str(event["event_ref"]) for event in representatives}
    remaining = [event for event in events if str(event["event_ref"]) not in selected_refs]
    markers = representatives + _bounded_evenly(remaining, 128 - len(representatives))
    return sorted(markers, key=lambda item: (item["relative_ms"], item["event_ref"])), distribution


def _sample_values(sample_set: object, *, window_start_ms: int, window_end_ms: int) -> dict[int, float]:
    if not isinstance(sample_set, Mapping):
        return {}
    points = sample_set.get("points")
    if not isinstance(points, list):
        return {}
    values: dict[int, float] = {}
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            continue
        time_ms = _finite_int(point[0])
        value = point[1]
        if (
            time_ms is None
            or not window_start_ms <= time_ms < window_end_ms
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            continue
        values[time_ms] = float(value)
    return values


def _target_relative_error_radius(
    artifact: Mapping[str, object], *, window_start_ms: int, window_end_ms: int,
) -> dict[str, object]:
    sample_sets = artifact.get("sample_sets")
    samples_by_ref = {
        sample.get("sample_set_id"): sample
        for sample in sample_sets if isinstance(sample_sets, list) and isinstance(sample, Mapping)
        if isinstance(sample.get("sample_set_id"), str)
    }
    target_channels: dict[str, dict[str, str]] = {}
    bundles = artifact.get("signal_bundles")
    for bundle in bundles if isinstance(bundles, list) else []:
        if not isinstance(bundle, Mapping):
            continue
        channels = bundle.get("channels")
        for channel in channels if isinstance(channels, list) else []:
            if not isinstance(channel, Mapping):
                continue
            key = channel.get("channel_key")
            sample_ref = channel.get("samples_ref")
            if not isinstance(key, str) or not isinstance(sample_ref, str):
                continue
            match = re.fullmatch(
                r"target\.([A-Za-z0-9._:-]+)\.(position_x|position_y|visible_radius)", key,
            )
            if match:
                target_channels.setdefault(match.group(1), {})[match.group(2)] = sample_ref
            elif key == "crosshair.position_x":
                target_channels.setdefault("__crosshair__", {})["position_x"] = sample_ref
            elif key == "crosshair.position_y":
                target_channels.setdefault("__crosshair__", {})["position_y"] = sample_ref

    crosshair = target_channels.pop("__crosshair__", {})
    required = {"position_x", "position_y", "visible_radius"}
    candidates = [
        channels for channels in target_channels.values()
        if required <= set(channels) and {"position_x", "position_y"} <= set(crosshair)
    ]
    if not candidates:
        return {
            "availability": "unavailable",
            "reason": "target_relative_channels_unavailable",
            "points": [],
        }
    if len(candidates) != 1:
        return {
            "availability": "unavailable",
            "reason": "target_relative_target_ambiguous",
            "points": [],
        }

    target = candidates[0]
    sample_maps = {
        name: _sample_values(samples_by_ref.get(sample_ref), window_start_ms=window_start_ms, window_end_ms=window_end_ms)
        for name, sample_ref in {
            "crosshair_x": crosshair["position_x"],
            "crosshair_y": crosshair["position_y"],
            "target_x": target["position_x"],
            "target_y": target["position_y"],
            "radius": target["visible_radius"],
        }.items()
    }
    common_times = set.intersection(*(set(values) for values in sample_maps.values())) if sample_maps else set()
    points = []
    for time_ms in sorted(common_times):
        radius = sample_maps["radius"][time_ms]
        if radius <= 0:
            continue
        error = math.hypot(
            sample_maps["crosshair_x"][time_ms] - sample_maps["target_x"][time_ms],
            sample_maps["crosshair_y"][time_ms] - sample_maps["target_y"][time_ms],
        ) / radius
        if math.isfinite(error):
            points.append({
                "relative_ms": time_ms - window_start_ms,
                "normalized_error_radius": round(error, 2),
            })
    if not points:
        return {
            "availability": "unavailable",
            "reason": "target_relative_samples_unavailable",
            "points": [],
        }
    return {
        "availability": "available",
        "reason": None,
        "points": _bounded_evenly(points, 120),
    }


def build_frontend_analysis_data_v1(
    *, analysis_ref: str, artifact: Mapping[str, object],
) -> dict[str, object]:
    """Project one validated, owner-bound artifact without coordinate channels."""
    if not _PUBLIC_REF.fullmatch(analysis_ref) or artifact.get("analysis_ref") != analysis_ref:
        raise ValueError("analysis data artifact is bound to another analysis")
    window = artifact.get("canonical_time_window")
    if not isinstance(window, Mapping):
        raise ValueError("analysis data canonical window is unavailable")
    window_start_ms = _finite_int(window.get("start_ms"))
    window_end_ms = _finite_int(window.get("end_ms"))
    if window_start_ms is None or window_end_ms is None or window_end_ms <= window_start_ms:
        raise ValueError("analysis data canonical window is invalid")
    markers, distribution = _project_event_rows(
        artifact,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
    )
    return {
        "schema_version": ANALYSIS_DATA_SCHEMA_VERSION,
        "analysis_ref": analysis_ref,
        "limitations": _safe_analysis_limitations(artifact.get("limitations")),
        "event_markers": markers,
        "event_distribution": distribution,
        "target_relative_error_radius": _target_relative_error_radius(
            artifact,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        ),
    }


def _family_unavailable(
    *, analysis_ref: str, family: str, reason: str, limitations: list[str],
) -> dict[str, object]:
    return {
        "schema_version": ANALYSIS_FAMILY_DATA_SCHEMA_VERSION,
        "analysis_ref": analysis_ref,
        "family": family,
        "availability": "unavailable",
        "reason": reason,
        "limitations": limitations,
        "total_count": 0,
        "next_offset": None,
        "rows": [],
    }


def _family_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _family_timestamp(
    value: object, *, window_start_ms: int, window_end_ms: int,
) -> int | None:
    timestamp = _finite_int(value)
    if timestamp is None or not window_start_ms <= timestamp <= window_end_ms:
        return None
    return timestamp - window_start_ms


def _family_metrics(attributes: Mapping[str, object], keys: Sequence[str]) -> dict[str, float]:
    return {
        key: value
        for key in keys
        if (value := _family_number(attributes.get(key))) is not None
    }


def _family_events(artifact: Mapping[str, object]) -> list[Mapping[str, object]]:
    events = []
    bundles = artifact.get("event_bundles")
    for bundle in bundles if isinstance(bundles, list) else []:
        if not isinstance(bundle, Mapping):
            continue
        raw_events = bundle.get("events")
        for event in raw_events if isinstance(raw_events, list) else []:
            if isinstance(event, Mapping):
                events.append(event)
    return events


def _family_row_limitations(event: Mapping[str, object]) -> list[str]:
    return _safe_analysis_limitations(event.get("limitations"))


def _project_switching_rows(
    events: Sequence[Mapping[str, object]], *, window_start_ms: int, window_end_ms: int,
) -> list[dict[str, object]]:
    rows = []
    metric_keys = (
        "transition_time_ms", "transition_distance_px", "path_efficiency", "settle_duration_ms",
    )
    for event in events:
        if event.get("event_kind") != "switch_chain":
            continue
        attributes = event.get("attributes")
        kill_ms = _family_timestamp(
            event.get("start_ms"), window_start_ms=window_start_ms, window_end_ms=window_end_ms,
        )
        transition_ms = _family_timestamp(
            attributes.get("leave_time_ms") if isinstance(attributes, Mapping) else None,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        acquire_ms = _family_timestamp(
            attributes.get("acquire_time_ms") if isinstance(attributes, Mapping) else None,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        settle_ms = _family_timestamp(
            attributes.get("settle_time_ms") if isinstance(attributes, Mapping) else None,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        if (
            not isinstance(attributes, Mapping)
            or None in {kill_ms, transition_ms, acquire_ms, settle_ms}
            or not kill_ms <= transition_ms <= acquire_ms <= settle_ms
        ):
            continue
        metrics = _family_metrics(attributes, metric_keys)
        if len(metrics) != len(metric_keys):
            continue
        rows.append({
            "kind": "switch_chain",
            "timing": {
                "kill_ms": kill_ms,
                "transition_ms": transition_ms,
                "acquire_ms": acquire_ms,
                "settle_ms": settle_ms,
            },
            "metrics": metrics,
            "limitations": _family_row_limitations(event),
        })
    return rows


def _project_tracking_rows(
    events: Sequence[Mapping[str, object]], *, window_start_ms: int, window_end_ms: int,
) -> list[dict[str, object]]:
    metric_keys_by_kind = {
        "tracking_fixed_window": (
            "target_relative_error_px", "time_in_radius_ratio", "correction_burden", "sparc",
        ),
        "tracking_loss": ("duration_ms",),
        "tracking_reacquisition": ("reacquisition_latency_ms",),
        "tracking_change_response": (
            "observed_change_response_ms", "alignment_latency_ms", "post_change_error_px",
        ),
    }
    rows = []
    for event in events:
        kind = event.get("event_kind")
        metric_keys = metric_keys_by_kind.get(kind) if isinstance(kind, str) else None
        attributes = event.get("attributes")
        start_ms = _family_timestamp(
            event.get("start_ms"), window_start_ms=window_start_ms, window_end_ms=window_end_ms,
        )
        end_ms = _family_timestamp(
            event.get("end_ms"), window_start_ms=window_start_ms, window_end_ms=window_end_ms,
        )
        if (
            metric_keys is None
            or not isinstance(attributes, Mapping)
            or start_ms is None
            or end_ms is None
            or end_ms < start_ms
        ):
            continue
        metrics = _family_metrics(attributes, metric_keys)
        if not metrics:
            continue
        rows.append({
            "kind": kind,
            "timing": {"start_ms": start_ms, "end_ms": end_ms},
            "metrics": metrics,
            "limitations": _family_row_limitations(event),
        })
    return rows


def _project_flicking_rows(
    events: Sequence[Mapping[str, object]], *, window_start_ms: int, window_end_ms: int,
) -> list[dict[str, object]]:
    metric_keys = (
        "accel_duration_ms", "decel_duration_ms", "settle_duration_ms", "peak_speed",
        "path_efficiency", "corrective_count",
    )
    rows = []
    for event in events:
        if event.get("event_kind") != "static_flick":
            continue
        attributes = event.get("attributes")
        start_ms = _family_timestamp(
            event.get("start_ms"), window_start_ms=window_start_ms, window_end_ms=window_end_ms,
        )
        start_absolute_ms = _finite_int(event.get("start_ms"))
        peak_ms = _family_timestamp(
            attributes.get("peak_ms") if isinstance(attributes, Mapping) else None,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        movement_duration_ms = _finite_int(
            attributes.get("movement_duration_ms") if isinstance(attributes, Mapping) else None,
        )
        movement_end_ms = _family_timestamp(
            start_absolute_ms + movement_duration_ms
            if start_absolute_ms is not None and movement_duration_ms is not None else None,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        settle_end_ms = _family_timestamp(
            attributes.get("settle_end_ms") if isinstance(attributes, Mapping) else None,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        if (
            not isinstance(attributes, Mapping)
            or None in {start_ms, peak_ms, movement_end_ms, settle_end_ms}
            or movement_duration_ms is None
            or movement_duration_ms < 0
            or not start_ms <= peak_ms <= movement_end_ms <= settle_end_ms
        ):
            continue
        metrics = _family_metrics(attributes, metric_keys)
        if not metrics:
            continue
        rows.append({
            "kind": "static_flick",
            "timing": {
                "start_ms": start_ms,
                "peak_ms": peak_ms,
                "movement_end_ms": movement_end_ms,
                "settle_end_ms": settle_end_ms,
            },
            "metrics": metrics,
            "limitations": _family_row_limitations(event),
        })
    return rows


def build_frontend_analysis_family_data_v1(
    *,
    analysis_ref: str,
    analysis_type: str,
    analysis_version: str,
    input_mode: str,
    artifact: Mapping[str, object] | None,
    limit: int,
    offset: int,
) -> dict[str, object]:
    """Return the bounded family detail projection for one persisted analysis version."""
    if not _PUBLIC_REF.fullmatch(analysis_ref):
        raise ValueError("analysis family data reference is invalid")
    dispatch = {
        ("target_switching", "target_switching.v1"): ("switching", _project_switching_rows),
        ("continuous_tracking", "continuous_tracking.v1"): ("tracking", _project_tracking_rows),
        ("flicking", "native_flicking.v1"): ("flicking", _project_flicking_rows),
    }
    family_and_projector = dispatch.get((analysis_type, analysis_version))
    if family_and_projector is None or (
        analysis_type == "flicking" and input_mode not in {"input_native", "multimodal"}
    ):
        reason = (
            "family_detail_requires_input_native_flicking"
            if analysis_type == "flicking"
            else "analysis_family_detail_unavailable"
        )
        return _family_unavailable(
            analysis_ref=analysis_ref,
            family="flicking" if analysis_type == "flicking" else "unsupported",
            reason=reason,
            limitations=[],
        )
    family, projector = family_and_projector
    if artifact is None:
        return _family_unavailable(
            analysis_ref=analysis_ref,
            family=family,
            reason="family_detail_artifact_unavailable",
            limitations=[],
        )
    if artifact.get("analysis_ref") != analysis_ref:
        raise ValueError("analysis family data artifact is bound to another analysis")
    window = artifact.get("canonical_time_window")
    if not isinstance(window, Mapping):
        return _family_unavailable(
            analysis_ref=analysis_ref,
            family=family,
            reason="family_detail_window_unavailable",
            limitations=_safe_analysis_limitations(artifact.get("limitations")),
        )
    window_start_ms = _finite_int(window.get("start_ms"))
    window_end_ms = _finite_int(window.get("end_ms"))
    if window_start_ms is None or window_end_ms is None or window_end_ms <= window_start_ms:
        return _family_unavailable(
            analysis_ref=analysis_ref,
            family=family,
            reason="family_detail_window_unavailable",
            limitations=_safe_analysis_limitations(artifact.get("limitations")),
        )
    if limit < 1 or limit > 100 or offset < 0:
        raise ValueError("analysis family data pagination is invalid")
    rows = projector(
        _family_events(artifact),
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
    )
    rows.sort(key=lambda row: min(row["timing"].values()))
    limitations = _safe_analysis_limitations(artifact.get("limitations"))
    if not rows:
        return _family_unavailable(
            analysis_ref=analysis_ref,
            family=family,
            reason="family_detail_rows_unavailable",
            limitations=limitations,
        )
    total_count = len(rows)
    page = rows[offset:offset + limit]
    next_offset = offset + len(page)
    return {
        "schema_version": ANALYSIS_FAMILY_DATA_SCHEMA_VERSION,
        "analysis_ref": analysis_ref,
        "family": family,
        "availability": "available",
        "reason": None,
        "limitations": limitations,
        "total_count": total_count,
        "next_offset": next_offset if next_offset < total_count else None,
        "rows": page,
    }


def build_product_state_v1(
    *,
    onboarding_completed: bool | None = None,
    onboarding_completion_kind: str | None = None,
    has_pending_runs: bool | None = None,
    has_runs: bool | None = None,
    has_analyses: bool | None = None,
    read_error: str | None = None,
) -> dict[str, object]:
    """Build the conditional-start read model.

    ``None`` is intentional in the unavailable shape: it cannot be confused
    with an authoritative empty state by a client.
    """
    if read_error:
        return {
            "schema_version": PRODUCT_STATE_SCHEMA_VERSION,
            "availability": "unavailable",
            "onboarding_completed": None,
            "onboarding_completion_kind": None,
            "has_pending_runs": None,
            "has_runs": None,
            "has_analyses": None,
            "error": _error_payload(read_error, "Product state is unavailable"),
        }
    if onboarding_completed is not True and onboarding_completed is not False:
        raise ValueError("onboarding_completed is required for an available state")
    if onboarding_completion_kind not in {"connected", "legacy", None}:
        raise ValueError("invalid onboarding_completion_kind")
    return {
        "schema_version": PRODUCT_STATE_SCHEMA_VERSION,
        "availability": "available",
        "onboarding_completed": onboarding_completed,
        "onboarding_completion_kind": onboarding_completion_kind,
        "has_pending_runs": bool(has_pending_runs),
        "has_runs": bool(has_runs),
        "has_analyses": bool(has_analyses),
        "error": None,
    }


def _native_status_value(native_status: Mapping[str, object] | None, key: str, default: object = None) -> object:
    if not isinstance(native_status, Mapping):
        return default
    return native_status.get(key, default)


def _source_state(native_status: Mapping[str, object] | None, source: str) -> str | None:
    value = _native_status_value(native_status, source)
    return value.get("state") if isinstance(value, Mapping) else None


def build_capture_status_v1(
    *,
    native_status: Mapping[str, object] | None = None,
    runs: Sequence[Mapping[str, object]] = (),
    read_error: str | None = None,
) -> dict[str, object]:
    """Project native coordinator status plus per-Run evidence attachment."""
    if read_error:
        return {
            "schema_version": CAPTURE_STATUS_SCHEMA_VERSION,
            "availability": "unavailable",
            "platform_supported": None,
            "raw_input_permission": "not_determined",
            "capture_enabled": None,
            "kovaak_process_present": None,
            "replay_buffer_active": None,
            "runtime_health": "unavailable",
            "finalization_state": "unknown",
            "pause_state": "unknown",
            "pause_fail_closed": True,
            "runs": [],
            "error": _error_payload(read_error, "Capture status is unavailable"),
        }

    phase = _native_status_value(native_status, "phase")
    enabled = _native_status_value(native_status, "enabled") is True
    raw_state = _source_state(native_status, "raw")
    video_state = _source_state(native_status, "video")
    available_native = isinstance(native_status, Mapping)
    platform_supported = bool(
        available_native
        and raw_state != "unavailable"
        and video_state != "unavailable"
    )
    explicit_permission = _native_status_value(native_status, "rawInputPermission")
    if explicit_permission not in {"granted", "denied", "not_determined"}:
        explicit_permission = None
    if explicit_permission is not None:
        permission = explicit_permission
    elif enabled:
        permission = "granted"
    else:
        permission = "not_determined"
    if phase in {"error"} or not available_native:
        runtime_health = "unavailable"
    elif phase in {"degraded"} or raw_state == "degraded" or video_state == "degraded":
        runtime_health = "degraded"
    else:
        runtime_health = "healthy"

    public_runs: list[dict[str, object]] = []
    pause_fail_closed = False
    finalization_state = "idle"
    for run in runs:
        run_ref = run.get("run_ref")
        if not isinstance(run_ref, str) or not run_ref:
            continue
        trace_state = run.get("trace_state")
        video_ref = run.get("video_artifact_ref")
        alignment = run.get("alignment")
        error_code = alignment.get("error_code") if isinstance(alignment, Mapping) else None
        if error_code == "pause_unsupported":
            pause_fail_closed = True
        state = run.get("finalization_state")
        if state in {"pending", "capturing", "finalizing"}:
            finalization_state = str(state)
        public_runs.append({
            "run_ref": run_ref,
            "raw_attached": trace_state == "attached",
            "video_attached": isinstance(video_ref, str) and bool(video_ref),
        })

    if phase == "finalizing" or finalization_state == "finalizing":
        finalization_state = "finalizing"
    elif phase in {"capturing", "degraded"} and finalization_state == "idle":
        finalization_state = "capturing"
    return {
        "schema_version": CAPTURE_STATUS_SCHEMA_VERSION,
        "availability": "available",
        "platform_supported": platform_supported,
        "raw_input_permission": permission,
        "capture_enabled": enabled,
        "kovaak_process_present": _native_status_value(
            native_status, "kovaakProcessPresent", False,
        ) is True,
        "replay_buffer_active": enabled and video_state == "capturing",
        "runtime_health": runtime_health,
        "finalization_state": finalization_state,
        "pause_state": "fail_closed" if pause_fail_closed else "clear",
        "pause_fail_closed": pause_fail_closed,
        "runs": public_runs,
        "error": None,
    }


def _decode_json(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
    return value


def _safe_failure(row: Mapping[str, object]) -> dict[str, object] | None:
    raw = _decode_json(row.get("error"))
    if isinstance(raw, Mapping):
        code = raw.get("code") if isinstance(raw.get("code"), str) else "task_failed"
        raw_message = raw.get("message")
        message = raw_message if isinstance(raw_message, str) else "Task failed"
        if _FORBIDDEN_TEXT.search(message):
            message = "Task failed"
        retryable = raw.get("retryable") is True
    elif isinstance(raw, str) and raw:
        code, message, retryable = "task_failed", raw, False
        if _FORBIDDEN_TEXT.search(message):
            message = "Task failed"
    else:
        return None
    domain = row.get("failure_domain")
    if domain not in _FAILURE_DOMAINS:
        domain = "source_file" if code in {"source_unavailable", "missing_snapshot"} else "kinematics"
    return {"domain": domain, "code": code, "message": message, "retryable": retryable}


def _partial_outcome(row: Mapping[str, object]) -> dict[str, object] | None:
    explicit = _decode_json(row.get("partial_outcome_json"))
    if isinstance(explicit, Mapping):
        return {
            "status": "partial",
            "native_preserved": explicit.get("native_preserved") is True,
            "visual_status": str(explicit.get("visual_status") or "unavailable"),
            "reason_code": str(explicit.get("reason_code") or "visual_unavailable"),
        }
    if row.get("status") != "done" or row.get("input_mode") != "multimodal":
        return None
    result = _decode_json(row.get("result"))
    if not isinstance(result, Mapping):
        return None
    evidence = result.get("evidence")
    availability = evidence.get("availability") if isinstance(evidence, Mapping) else None
    video_status = None
    if isinstance(availability, Mapping):
        video_status = availability.get("mp4") or availability.get("video")
    deterministic = result.get("deterministic")
    native_preserved = isinstance(deterministic, Mapping) and deterministic.get("status") in {
        "available", "limited",
    }
    if video_status in {"unavailable", "missing", "not_present"} and native_preserved:
        return {
            "status": "partial",
            "native_preserved": True,
            "visual_status": "unavailable",
            "reason_code": "video_unavailable",
        }
    return None


def _state_for(row: Mapping[str, object]) -> str:
    status = row.get("status")
    if status == "uploading":
        return "importing"
    if status in {"queued", "running", "done", "failed", "retrying"}:
        return str(row["task_state"]) if row.get("task_state") in _TASK_STATE_LABELS else str(status)
    return "failed"


def _phase_for(row: Mapping[str, object], state: str) -> str | None:
    phase = row.get("task_phase")
    if phase in _TASK_PHASE_LABELS:
        if row.get("input_mode") == "input_native" and phase == "analyzing_video":
            return "computing_kinematics"
        return str(phase)
    if state in {"importing", "queued", "retrying"}:
        return "preparing_training_record"
    if state == "running":
        return (
            "computing_kinematics"
            if row.get("input_mode") == "video_fallback"
            else "aligning_input_events"
        )
    return None


def _attempt_projection(row: Mapping[str, object]) -> dict[str, object]:
    state = _state_for(row)
    failure = _safe_failure(row)
    return {
        "attempt_ref": f"analysis:{row.get('id')}",
        "attempt_number": int(row.get("attempt_number") or 1),
        "state": state,
        "state_label": _TASK_STATE_LABELS[state],
        "phase": _phase_for(row, state),
        "failure": failure,
        "partial_outcome": _partial_outcome(row),
        "retryable": bool(failure and failure.get("retryable")),
        "can_delete": state in {"done", "failed"},
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
    }


def build_task_detail_v1(
    row: Mapping[str, object],
    *,
    attempts: Sequence[Mapping[str, object]] = (),
    read_error: str | None = None,
) -> dict[str, object]:
    if read_error:
        return {
            "schema_version": TASK_DETAIL_SCHEMA_VERSION,
            "availability": "unavailable",
            "task_ref": None,
            "error": _error_payload(read_error, "Task is unavailable"),
        }
    state = _state_for(row)
    phase = _phase_for(row, state)
    attempt_rows = list(attempts) or [row]
    attempt_rows.sort(key=lambda item: int(item.get("attempt_number") or 1))
    failure = _safe_failure(row)
    task_ref = row.get("task_group_ref") or f"task:{row.get('id')}"
    training_at = _safe_presentation_timestamp(row.get("training_at"))
    analysis_completed_at = _safe_presentation_timestamp(row.get("finished_at"))
    return {
        "schema_version": TASK_DETAIL_SCHEMA_VERSION,
        "availability": "available",
        "task_ref": task_ref,
        "analysis_ref": f"analysis:{row.get('id')}",
        "state": state,
        "state_label": _TASK_STATE_LABELS[state],
        "phase": phase,
        "phase_label": _TASK_PHASE_LABELS.get(phase) if phase else None,
        "input_mode": row.get("input_mode") or "video_fallback",
        "analysis_type": row.get("analysis_type") or "flicking",
        "run_ref": f"run:{row['kovaak_run_id']}" if row.get("kovaak_run_id") else None,
        "presentation_label": build_record_presentation_label(
            scenario=row.get("scenario"),
            training_at=training_at,
            analysis_completed_at=analysis_completed_at,
        ),
        "training_at": training_at,
        "analysis_completed_at": analysis_completed_at,
        "failure": failure,
        "partial_outcome": _partial_outcome(row),
        "retryable": bool(failure and failure.get("retryable")),
        "can_delete": state in {"done", "failed"},
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "attempt_number": int(row.get("attempt_number") or 1),
        "attempt_history": [_attempt_projection(item) for item in attempt_rows],
        "error": None,
    }


def build_task_list_v1(
    rows: Sequence[Mapping[str, object]] = (),
    *,
    read_error: str | None = None,
) -> dict[str, object]:
    if read_error:
        return {
            "schema_version": TASK_LIST_SCHEMA_VERSION,
            "availability": "unavailable",
            "tasks": [],
            "error": _error_payload(read_error, "Tasks are unavailable"),
        }
    tasks = [build_task_detail_v1(row) for row in rows]
    return {
        "schema_version": TASK_LIST_SCHEMA_VERSION,
        "availability": "available",
        "tasks": tasks,
        "error": None,
    }


def _calibration_value(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and numeric > 0 else None


def resolve_calibration_v1(
    *,
    stats: Mapping[str, object] | None,
    manual_override: Mapping[str, object] | None,
    profile_default: Mapping[str, object] | None,
) -> dict[str, dict[str, object | None]]:
    """Resolve each field independently: Stats > manual > profile > unknown."""
    selected: dict[str, dict[str, object | None]] = {}
    for field in ("cm_per_360", "fov"):
        candidates = (
            ("stats", stats),
            ("manual_override", manual_override),
            ("profile_default", profile_default),
        )
        value = None
        source = "undetermined"
        for candidate_source, candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            candidate_value = _calibration_value(candidate.get(field))
            if candidate_value is not None:
                value = candidate_value
                source = candidate_source
                break
        selected[field] = {"value": value, "source": source}
    return selected


def _safe_current_training_text(value: object, *, maximum: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (
        not text
        or len(text) > maximum
        or _FORBIDDEN_TEXT.search(text)
        or _INTERNAL_REF_TEXT.search(text)
    ):
        return None
    return text


def _current_training_text(item: Mapping[str, object], field: str) -> str | None:
    presentation = _CURRENT_TRAINING_ZH_CN.get(item.get("knowledge_ref"))
    if (
        presentation is not None
        and presentation["scenario_profile_ref"] == item.get("scenario_profile_ref")
    ):
        return _safe_current_training_text(presentation.get(field))
    return _safe_current_training_text(item.get(field))


def _reviewed_scenario(scenario_profile_ref_value: object) -> tuple[str | None, str | None]:
    """Return the safe display name and launch ref for an exact reviewed scenario."""
    if not isinstance(scenario_profile_ref_value, str) or not _PUBLIC_REF.fullmatch(scenario_profile_ref_value):
        return None, None
    try:
        from kovaak_tracker.scenario_profiles import (
            load_launch_manifest,
            load_registry,
            scenario_profile_ref,
        )

        registry = load_registry()
        manifest = load_launch_manifest(registry=registry)
        active_launch_refs = {
            entry["scenario_profile_ref"]
            for entry in manifest["entries"]
            if entry["status"] == "active"
        }
        for profile in registry["entries"]:
            profile_ref = scenario_profile_ref(profile)
            if profile["status"] == "active" and profile_ref == scenario_profile_ref_value:
                display_name = _safe_current_training_text(profile["display_name"])
                return (
                    display_name,
                    profile_ref if profile_ref in active_launch_refs else None,
                )
    except (KeyError, OSError, TypeError, ValueError):
        return None, None
    return None, None


def build_current_training_v1(
    *,
    plan: Mapping[str, object] | None,
    items: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Project the current owner plan with only reviewed public scenario refs."""
    if plan is None:
        return {
            "schema_version": CURRENT_TRAINING_SCHEMA_VERSION,
            "availability": "unavailable",
            "reason": "no_current_plan",
            "plan_status": None,
            "total_item_count": 0,
            "visible_item_count": 0,
            "limitations": [],
            "items": [],
        }

    plan_status = plan.get("status")
    if plan_status not in {"active", "paused"}:
        raise ValueError("current Training Plan must be active or paused")

    status_order = {"active": 0, "planned": 1, "completed": 2, "cancelled": 3}
    ordered_items = sorted(
        (
            item for item in items
            if item.get("status") in status_order
        ),
        key=lambda item: status_order[str(item["status"])],
    )
    projected = []
    for item in ordered_items[:3]:
        display_name, launch_ref = _reviewed_scenario(item.get("scenario_profile_ref"))
        projected.append({
            "display_name": display_name,
            "scenario_profile_ref": launch_ref,
            "scenario_availability": "available" if display_name is not None else "unavailable",
            "status": item["status"],
            "practice_condition": _current_training_text(item, "practice_condition"),
            "cue": _current_training_text(item, "cue"),
            "dose_guardrail": _current_training_text(item, "dose_guardrail"),
            "observation": None,
            "retest": _current_training_text(item, "review_date"),
        })
    limitations = []
    if plan_status == "paused":
        limitations.append("plan_paused")
    if len(ordered_items) > len(projected):
        limitations.append("items_limited_to_three")
    return {
        "schema_version": CURRENT_TRAINING_SCHEMA_VERSION,
        "availability": "available",
        "reason": None,
        "plan_status": plan_status,
        "total_item_count": len(ordered_items),
        "visible_item_count": len(projected),
        "limitations": limitations,
        "items": projected,
    }


__all__ = [
    "ANALYSIS_DATA_SCHEMA_VERSION",
    "CURRENT_TRAINING_SCHEMA_VERSION",
    "CAPTURE_STATUS_SCHEMA_VERSION",
    "PRODUCT_STATE_SCHEMA_VERSION",
    "TASK_DETAIL_SCHEMA_VERSION",
    "TASK_LIST_SCHEMA_VERSION",
    "build_frontend_analysis_family_data_v1",
    "build_capture_status_v1",
    "build_frontend_analysis_data_v1",
    "build_current_training_v1",
    "build_product_state_v1",
    "build_task_detail_v1",
    "build_task_list_v1",
    "resolve_calibration_v1",
]
