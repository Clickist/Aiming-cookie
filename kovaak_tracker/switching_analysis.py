"""Deterministic target-switching analysis over external telemetry truth.

遥测真值动作层 target_switching 分析器。知识库合同（knowledge/coach/
registry.v14.json ``switching.transition-and-arrival@3``）把一次转火拆成
previous_outcome / leave / transition / next_target_acquired / settle 的
事件链（``event.switch_chain``）；本模块从遥测真值逐链计算 registry 指标：

- ``target_switching.transition_time_ms``（``metric:inter_target_transition``）：
  leave（权威击杀 N = 下一目标生命窗 t_end 的前一击杀）→ acquire（准星
  首次进入下一目标到位锥，``ACQUISITION_CONE_DEG``）。cue 信号
  ``switch transition slow``。
- ``target_switching.settle_duration_ms``（``metric:next_target_acquisition``）：
  acquire → settle（持续贴合到击杀的最后接触段起点；中途掉出再进会拉长
  该值）。cue 信号 ``switch arrival error high``。
- ``target_switching.transition_distance_px`` / 行级 ``transition_path_length_px``
  / ``target_switching.path_efficiency``（``metric:distance_conditioned_path``）：
  离开时刻相对误差向量 → 到位时刻相对误差向量的位移，与相对路径长之比。

registry 引用但按其自身口径**不产出数值**的指标：

- ``metric:first_shot_error``：点击沿不可靠（inputs L_down 沿丢失，P1-B
  已知缺陷）→ ``target_switching.first_shot_latency_ms`` 等首发相关记录
  显式 unavailable + limitation。

双输入源融合（合同 ``.zcode/fusion-spec-1005.md`` §二.1，``input_fusion``）：
payload 携带可选 ``press_edges``（遥测 inputs L_down 沿优先 + trace
buttons 沿回填/交叉验证的融合沿序列，adapter 组装）时：

- 点射类局（沿 > 1）逐链出 ``first_shot_latency_ms`` = acquire → 链窗内
  首个融合按压沿，行携带 ``first_shot_press_source`` 来源标注；
- 按住型（沿 <= 1，如 TF180 的 LG 打法）显式 unavailable，limitation
  ``first_shot_press_and_hold_weapon``（数据前提不满足，非数据坏）；
- 交叉验证残差（|telemetry − trace|）与 trace 回填计数进指标 limitation。

无 ``press_edges`` 时保持既有占位语义（P1-B 缺陷口径），逐字段不变。
- ``metric:target_switching.selection_error_ratio``：场景未暴露
  expected-target rule（``switching.selection-observable-only@3``："Selection
  is analyzable only when the scenario exposes an expected-target rule"）
  → 结果级 limitation ``selection evidence unavailable``，不发布选靶论断。

真值来源（worker adapter 组装，见 ``target_switching_telemetry_input.v1``）：
权威击杀 = 目标生命窗 t_end；准星-目标几何 = 遥测 producer 投影的 px 域
样本（虚拟 1920x1080 视口，公式与 CV 时代 ``target_switching_analysis``
逐字节一致，Coach 映射 / history 基线零改动消费）。计算确定性、无机制
推断。
"""

from __future__ import annotations

from math import atan, atan2, degrees, hypot, radians, tan
from typing import Any, Mapping, Sequence

from .target_switching_analysis import (
    _MAX_CANONICAL_TIME_MS,
    _MAX_LOCAL_CONTACT_GAP_MS,
    _metric_record,
    _number,
    _point_at,
    _processed_tables,
    _ref,
    _relative_path_between,
    _segment,
    _time,
    _visual_chain_samples,
    TargetSwitchingAnalysisError,
)

ANALYSIS_VERSION = "target_switching.v1"
SCHEMA_VERSION = "target_switching_analysis.v1"
INPUT_SCHEMA_VERSION = "target_switching_telemetry_input.v1"
# 到位判定锥（deg）：准星-目标角 separation <= 锥角即视为贴合（acquire/
# settle/contact）。定值而非 visible_radius 磁盘：冻结副本普遍缺 bb.json，
# producer 的 30cm 兜底半径自带"不可作几何依据" limitation；定锥保证
# transition_time_ms / settle_duration_ms 跨 run 同口径可比（metric 版本
# 的一部分，改动需升版本）。3.0° 的依据：run 54095 实测死亡近旁最小夹角
# p50=0.87° / p95=2.41° / max=3.72°（对齐验收 tracking_aim 同几何），3°
# 覆盖 96% 击杀且比典型转火角距（中位 ~30°）紧一个数量级。
ACQUISITION_CONE_DEG = 3.0
# 击杀近旁接触窗：末次贴合必须落在击杀前该窗口内（views ~21ms 采样 + 生命
# 窗 ~32ms 量化；口径与对齐验收 death_aim_check 的死亡前 200ms 窗一致）。
KILL_CONTACT_PROXIMITY_MS = 200
# 虚拟视口（producer 投影合同）；px -> 角度的反演与投影同一焦距公式。
_VIEWPORT_WIDTH_PX = 1920
_VIEWPORT_HEIGHT_PX = 1080
# event_bundle.v1 的硬上限（analysis_evidence._MAX_LIST 同值）。
_EVENT_BUNDLE_LIMIT = 512

_CLICK_ANCHOR_STATUSES = {"reliable", "unreliable"}
_PRESS_EDGE_SOURCES = {"telemetry", "trace"}


def _focal_length_px(horizontal_fov_deg: float) -> float:
    return (_VIEWPORT_WIDTH_PX / 2.0) / tan(radians(horizontal_fov_deg) / 2.0)


def _angular_separation_deg(
    sample: Mapping[str, float | int],
    crosshair: Mapping[str, float | int],
    focal_px: float,
) -> float:
    """准星-目标角 separation（deg），与对齐验收 tracking_aim 同一口径：
    dyaw = atan(dx_px/f)、dpitch = atan(dy_px/f)，hypot 平面近似。"""
    dyaw = atan((float(sample["x"]) - float(crosshair["x"])) / focal_px)
    dpitch = atan((float(crosshair["y"]) - float(sample["y"])) / focal_px)
    return degrees(hypot(dyaw, dpitch))


def _inside_states(
    *,
    samples: Sequence[Mapping[str, float | int]],
    crosshair_by_time: Mapping[int, Mapping[str, float | int]],
    start_ms: int,
    end_ms: int,
    focal_px: float,
) -> list[tuple[int, bool]]:
    """(t, inside) on the view sampling clock for one target life window.

    inside = 准星-目标角 separation <= ACQUISITION_CONE_DEG（到位锥）。
    """
    states: list[tuple[int, bool]] = []
    for sample in samples:
        time_ms = int(sample["canonical_time_ms"])
        # 闭窗下界与旧视觉链口径一致（leave 时刻已在锥内 → transition 为 0，
        # 是事实而非异常）。
        if not start_ms <= time_ms <= end_ms:
            continue
        crosshair = crosshair_by_time.get(time_ms)
        if crosshair is None:
            continue
        inside = (
            _angular_separation_deg(sample, crosshair, focal_px)
            <= ACQUISITION_CONE_DEG
        )
        states.append((time_ms, inside))
    return states


def _within_viewport(x: float, y: float) -> bool:
    """px 几何只在探测器可见域（虚拟视口内）有意义：CV 时代视口外的目标
    没有轨迹样本，几何自然不可得；遥测投影的 px 在大偏角处被 tan 放大，
    必须显式守卫（180° 场景目标可距准星 80°+）。"""
    return (
        0.0 <= x <= _VIEWPORT_WIDTH_PX and 0.0 <= y <= _VIEWPORT_HEIGHT_PX
    )


def _contact_runs(states: Sequence[tuple[int, bool]]) -> list[list[int]]:
    """Contiguous inside runs split on >gap between samples (50ms, 同旧管线)."""
    runs: list[list[int]] = []
    current: list[int] = []
    for time_ms, inside in states:
        if not inside:
            if current:
                runs.append(current)
            current = []
            continue
        if current and time_ms - current[-1] > _MAX_LOCAL_CONTACT_GAP_MS:
            runs.append(current)
            current = []
        current.append(time_ms)
    if current:
        runs.append(current)
    return runs


def _authoritative_kills(
    value: Any,
    *,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    """Validate the adapter-supplied authoritative kill series (life t_end)."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise TargetSwitchingAnalysisError("authoritative_kills must be a non-empty list")
    kills: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    seen_indexes: set[int] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {
            "event_ref", "time_ms", "kill_index", "target_track_ref",
            "target_birth_ms",
        }:
            raise TargetSwitchingAnalysisError(f"authoritative_kills[{index}] is invalid")
        event_ref = _ref(raw["event_ref"], f"authoritative_kills[{index}].event_ref")
        time_ms = _time(
            raw["time_ms"], f"authoritative_kills[{index}].time_ms",
            minimum=start_ms, maximum=end_ms,
        )
        kill_index = _time(
            raw["kill_index"], f"authoritative_kills[{index}].kill_index",
            minimum=1, maximum=1_000_000,
        )
        track_ref = _ref(
            raw["target_track_ref"],
            f"authoritative_kills[{index}].target_track_ref",
        )
        birth_ms = _number(
            raw["target_birth_ms"], f"authoritative_kills[{index}].target_birth_ms",
        )
        if int(birth_ms) != birth_ms:
            raise TargetSwitchingAnalysisError(
                f"authoritative_kills[{index}].target_birth_ms is invalid",
            )
        if birth_ms >= time_ms:
            raise TargetSwitchingAnalysisError(
                f"authoritative_kills[{index}] target dies before its birth",
            )
        if event_ref in seen_refs or kill_index in seen_indexes:
            raise TargetSwitchingAnalysisError("authoritative kill is duplicated")
        seen_refs.add(event_ref)
        seen_indexes.add(kill_index)
        kills.append({
            "event_ref": event_ref,
            "time_ms": time_ms,
            "kill_index": kill_index,
            "target_track_ref": track_ref,
            "target_birth_ms": int(birth_ms),
        })
    kills.sort(key=lambda item: (item["time_ms"], item["event_ref"]))
    if any(
        right["kill_index"] <= left["kill_index"]
        for left, right in zip(kills, kills[1:])
    ):
        # 权威击杀的 kill_index 按时间序授予（adapter 合同）；乱序序列视为
        # 被篡改/拼错的输入，fail-closed。
        raise TargetSwitchingAnalysisError("authoritative kill indexes are not time-ordered")
    return kills


def _target_lives(
    value: Any,
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, list[tuple[int, int]]]:
    """track_ref -> [(birth_ms, death_ms)] for candidate visibility at leave."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TargetSwitchingAnalysisError("target_lives must be a list")
    lives: dict[str, list[tuple[int, int]]] = {}
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {"track_ref", "lives"}:
            raise TargetSwitchingAnalysisError(f"target_lives[{index}] is invalid")
        track_ref = _ref(raw["track_ref"], f"target_lives[{index}].track_ref")
        if track_ref in lives:
            raise TargetSwitchingAnalysisError("target_lives track is duplicated")
        raw_lives = raw["lives"]
        if isinstance(raw_lives, (str, bytes)) or not isinstance(raw_lives, Sequence):
            raise TargetSwitchingAnalysisError("target lives must be a list")
        windows: list[tuple[int, int]] = []
        for life_index, life in enumerate(raw_lives):
            if (
                not isinstance(life, Sequence) or isinstance(life, str)
                or len(life) != 2
            ):
                raise TargetSwitchingAnalysisError(
                    f"target_lives[{index}].lives[{life_index}] is invalid",
                )
            birth_ms = _number(
                life[0], f"target_lives[{index}].lives[{life_index}][0]",
            )
            death_ms = _number(
                life[1], f"target_lives[{index}].lives[{life_index}][1]",
            )
            if (
                int(birth_ms) != birth_ms or int(death_ms) != death_ms
                or death_ms <= birth_ms
            ):
                raise TargetSwitchingAnalysisError(
                    f"target_lives[{index}].lives[{life_index}] is invalid",
                )
            windows.append((int(birth_ms), int(death_ms)))
        windows.sort()
        lives[track_ref] = windows
    return lives


def _fused_press_edges(
    value: Any,
    *,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    """Validate the adapter-assembled fused press edge series (input_fusion)."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TargetSwitchingAnalysisError("press_edges must be a list")
    edges: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {
            "time_ms", "source", "matched", "residual_ms",
        }:
            raise TargetSwitchingAnalysisError(f"press_edges[{index}] is invalid")
        time_ms = _time(
            raw["time_ms"], f"press_edges[{index}].time_ms",
            minimum=start_ms, maximum=end_ms - 1,
        )
        source = raw["source"]
        if source not in _PRESS_EDGE_SOURCES:
            raise TargetSwitchingAnalysisError(f"press_edges[{index}].source is invalid")
        matched = raw["matched"]
        if not isinstance(matched, bool):
            raise TargetSwitchingAnalysisError(f"press_edges[{index}].matched is invalid")
        residual = raw["residual_ms"]
        if residual is not None:
            residual = _number(
                residual, f"press_edges[{index}].residual_ms",
            )
            if int(residual) != residual:
                raise TargetSwitchingAnalysisError(
                    f"press_edges[{index}].residual_ms is invalid",
                )
            residual = int(residual)
        if matched != (source == "telemetry" and residual is not None):
            raise TargetSwitchingAnalysisError(
                f"press_edges[{index}] matched/residual pairing is invalid",
            )
        edges.append({
            "time_ms": time_ms,
            "source": source,
            "matched": matched,
            "residual_ms": residual,
        })
    edges.sort(key=lambda edge: (edge["time_ms"], edge["source"]))
    return edges


def _fused_press_validation(value: Any) -> dict[str, Any]:
    """Validate the cross-validation summary accompanying ``press_edges``."""
    if not isinstance(value, Mapping) or set(value) != {
        "matched", "telemetry_only", "trace_only",
        "residual_max_ms", "residual_median_ms",
    }:
        raise TargetSwitchingAnalysisError("press_edge_validation is invalid")
    counts = {}
    for field in ("matched", "telemetry_only", "trace_only"):
        count = _time(
            value[field], f"press_edge_validation.{field}",
            minimum=0, maximum=1_000_000,
        )
        counts[field] = count
    summary: dict[str, Any] = dict(counts)
    residual_max = value["residual_max_ms"]
    if residual_max is not None:
        residual_max = _number(
            residual_max, "press_edge_validation.residual_max_ms",
        )
        if int(residual_max) != residual_max:
            raise TargetSwitchingAnalysisError(
                "press_edge_validation.residual_max_ms is invalid",
            )
    residual_median = value["residual_median_ms"]
    if residual_median is not None:
        residual_median = _number(
            residual_median, "press_edge_validation.residual_median_ms",
        )
    if (counts["matched"] > 0) != (residual_max is not None):
        raise TargetSwitchingAnalysisError(
            "press_edge_validation residual presence is inconsistent",
        )
    summary["residual_max_ms"] = (
        None if residual_max is None else int(residual_max)
    )
    summary["residual_median_ms"] = residual_median
    return summary


def build_switching_chains_from_telemetry_v1(
    *,
    analysis_ref: str,
    canonical_time_window: Mapping[str, Any],
    crosshair_samples: Sequence[Mapping[str, Any]],
    target_tracks: Sequence[Mapping[str, Any]],
    target_lives: Sequence[Mapping[str, Any]],
    authoritative_kills: Sequence[Mapping[str, Any]],
    viewport_fov_deg: float = 103.0,
) -> list[dict[str, Any]]:
    """Pair consecutive authoritative kills into observable switching chains.

    每段链 = 权威击杀 N（leave）→ 权威击杀 N+1（下一受害目标的生命窗终点）。
    acquire/settle 由下一目标生命窗内、views 采样时刻的到位锥接触状态给出：
    acquire = 首个接触 run 起点；settle = 末端（击杀近旁）接触 run 的起点。
    departure 几何要求下一受害目标在 leave 时刻已出生（可插值）；spawn 等待
    段几何不可得，按库口径降级 partial（不臆造 departure 点）。
    """
    analysis_ref = _ref(analysis_ref, "analysis_ref")
    fov = _number(
        viewport_fov_deg, "viewport_fov_deg", minimum=1.0,
    )
    if fov > 179.0:
        raise TargetSwitchingAnalysisError("viewport_fov_deg is invalid")
    focal_px = _focal_length_px(fov)
    if not isinstance(canonical_time_window, Mapping):
        raise TargetSwitchingAnalysisError("canonical_time_window is required")
    start_ms = _time(
        canonical_time_window.get("start_ms"),
        "canonical_time_window.start_ms",
        minimum=0,
        maximum=_MAX_CANONICAL_TIME_MS,
    )
    end_ms = _time(
        canonical_time_window.get("end_ms"),
        "canonical_time_window.end_ms",
        minimum=start_ms + 1,
        maximum=_MAX_CANONICAL_TIME_MS,
    )
    crosshair = _visual_chain_samples(
        crosshair_samples, "crosshair_samples", require_radius=False,
    )
    crosshair_by_time = {
        int(sample["canonical_time_ms"]): sample for sample in crosshair
    }
    if isinstance(target_tracks, (str, bytes)) or not isinstance(target_tracks, Sequence):
        raise TargetSwitchingAnalysisError("target_tracks must be a list")
    tracks: dict[str, list[dict[str, float | int]]] = {}
    for index, raw in enumerate(target_tracks):
        if not isinstance(raw, Mapping):
            raise TargetSwitchingAnalysisError(f"target_tracks[{index}] is invalid")
        track_ref = _ref(raw.get("track_ref"), f"target_tracks[{index}].track_ref")
        if track_ref in tracks or not track_ref.startswith(f"{analysis_ref}:target-track:"):
            raise TargetSwitchingAnalysisError("target telemetry track is invalid")
        tracks[track_ref] = _visual_chain_samples(
            raw.get("samples"), f"target_tracks[{index}].samples", require_radius=True,
        )
    lives = _target_lives(target_lives, start_ms=start_ms, end_ms=end_ms)
    kills = _authoritative_kills(
        authoritative_kills, start_ms=start_ms, end_ms=end_ms,
    )
    unmentioned = {kill["target_track_ref"] for kill in kills} - set(tracks)
    if unmentioned:
        raise TargetSwitchingAnalysisError("authoritative kill references an unknown track")

    chains: list[dict[str, Any]] = []
    for previous, current in zip(kills, kills[1:]):
        leave_time = previous["time_ms"]
        next_death = current["time_ms"]
        if next_death <= leave_time:
            continue
        next_track = current["target_track_ref"]
        birth_ms = current["target_birth_ms"]
        window_start = max(leave_time, birth_ms)
        # 只取下一生命窗内的样本（同 tid 的更早生命不得污染 departure 几何）。
        life_samples = [
            sample for sample in tracks[next_track]
            if birth_ms <= int(sample["canonical_time_ms"]) <= next_death
        ]
        limitations: list[str] = []
        states = _inside_states(
            samples=life_samples,
            crosshair_by_time=crosshair_by_time,
            start_ms=window_start,
            end_ms=next_death,
            focal_px=focal_px,
        )
        runs = _contact_runs(states)
        terminal_runs = [
            run for run in runs
            if next_death - run[-1] <= KILL_CONTACT_PROXIMITY_MS
        ]
        if not runs or not terminal_runs:
            # 击杀近旁无可观察贴合：链不可证（不臆造接触）。
            limitations.append("switch_contact_unobservable")
            chains.append({
                "episode_ref": f"{analysis_ref}:telemetry-switch-chain:{current['kill_index']}",
                "source_refs": sorted([previous["event_ref"], current["event_ref"]]),
                "previous_kill_event_ref": previous["event_ref"],
                "previous_outcome_time_ms": leave_time,
                "leave_time_ms": leave_time,
                "previous_target_track_ref": previous["target_track_ref"],
                "next_kill_event_ref": current["event_ref"],
                "next_outcome_time_ms": next_death,
                "next_target_track_ref": next_track,
                "next_target_birth_ms": birth_ms,
                "candidate_track_refs": [],
                "acquire_time_ms": None,
                "settle_time_ms": None,
                "transition_distance_px": None,
                "transition_direction_deg": None,
                "transition_path_length_px": None,
                "path_efficiency": None,
                "limitations": sorted(set(limitations)),
            })
            continue
        acquire_time = runs[0][0]
        settle_time = terminal_runs[-1][0]
        crosshair_at_leave = _point_at(crosshair, leave_time)
        next_at_leave = (
            _point_at(life_samples, leave_time) if leave_time >= birth_ms else None
        )
        crosshair_at_acquire = _point_at(crosshair, acquire_time)
        next_at_acquire = _point_at(life_samples, acquire_time)
        transition_distance = None
        transition_direction = None
        path_length = None
        path_efficiency = None
        geometry_in_viewport = (
            crosshair_at_leave is not None and crosshair_at_acquire is not None
            and next_at_leave is not None and next_at_acquire is not None
            and _within_viewport(*next_at_leave) and _within_viewport(*next_at_acquire)
            and all(
                _within_viewport(float(sample["x"]), float(sample["y"]))
                for sample in life_samples
                if leave_time < int(sample["canonical_time_ms"]) < acquire_time
            )
        )
        if geometry_in_viewport:
            departure_error = (
                next_at_leave[0] - crosshair_at_leave[0],
                next_at_leave[1] - crosshair_at_leave[1],
            )
            arrival_error = (
                next_at_acquire[0] - crosshair_at_acquire[0],
                next_at_acquire[1] - crosshair_at_acquire[1],
            )
            transition_distance = hypot(
                arrival_error[0] - departure_error[0],
                arrival_error[1] - departure_error[1],
            )
            path_length = _relative_path_between(
                crosshair, life_samples, leave_time, acquire_time,
            )
            transition_direction = (
                degrees(atan2(departure_error[1], departure_error[0]))
                if transition_distance > 0 else None
            )
            path_efficiency = (
                transition_distance / path_length
                if path_length is not None and path_length > 0 else None
            )
        if crosshair_at_leave is not None and next_at_leave is not None \
                and crosshair_at_acquire is not None and next_at_acquire is not None:
            if not geometry_in_viewport:
                limitations.append("transition_geometry_outside_viewport")
            if transition_distance is None or path_length is None:
                limitations.append("transition_geometry_unavailable")
        else:
            limitations.append("transition_geometry_unavailable")
        candidates = sorted(
            track_ref
            for track_ref, windows in lives.items()
            if track_ref != previous["target_track_ref"]
            and any(birth <= leave_time < death for birth, death in windows)
        )
        chains.append({
            "episode_ref": f"{analysis_ref}:telemetry-switch-chain:{current['kill_index']}",
            "source_refs": sorted([previous["event_ref"], current["event_ref"]]),
            "previous_kill_event_ref": previous["event_ref"],
            "previous_outcome_time_ms": leave_time,
            "leave_time_ms": leave_time,
            "previous_target_track_ref": previous["target_track_ref"],
            "next_kill_event_ref": current["event_ref"],
            "next_outcome_time_ms": next_death,
            "next_target_track_ref": next_track,
            "next_target_birth_ms": birth_ms,
            "candidate_track_refs": candidates,
            "acquire_time_ms": acquire_time,
            "settle_time_ms": settle_time,
            "transition_distance_px": transition_distance,
            "transition_direction_deg": transition_direction,
            "transition_path_length_px": path_length,
            "path_efficiency": path_efficiency,
            "limitations": sorted(set(limitations)),
        })
    return chains


def _outcome_only_telemetry_result(
    analysis_ref: str,
    window: Mapping[str, Any],
    comparison: object,
    limitation: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "analysis_ref": analysis_ref,
        "analysis_type": "target_switching",
        "support_status": "outcome_only",
        "processed_rows": [],
        "processed_event_tables": [],
        "metrics": {},
        "evidence_segments": [],
        "comparison": comparison,
        "limitations": [limitation],
        "evidence_extension": {
            "event_bundle": {
                "schema_version": "event_bundle.v1",
                "analysis_ref": analysis_ref,
                "events": [],
                "outcome_associations": [],
            },
            "metric_records": [],
            "evidence_segments": [],
            "processed_event_tables": [],
            "required_outcome_associations": [],
            "required_signal_bundle": None,
            "required_sample_sets": [],
            "required_canonical_time_window": dict(window),
        },
    }


def analyze_target_switching_telemetry_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Analyze one telemetry-truth switching run into the v1 family contract."""
    if not isinstance(payload, Mapping) or payload.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise TargetSwitchingAnalysisError("target switching telemetry input schema is unsupported")
    analysis_ref = _ref(payload.get("analysis_ref"), "analysis_ref")
    window = payload.get("canonical_time_window")
    if not isinstance(window, Mapping):
        raise TargetSwitchingAnalysisError("canonical_time_window is required")
    start_ms = _time(
        window.get("start_ms"), "canonical_time_window.start_ms",
        minimum=0, maximum=_MAX_CANONICAL_TIME_MS,
    )
    end_ms = _time(
        window.get("end_ms"), "canonical_time_window.end_ms",
        minimum=start_ms + 1, maximum=_MAX_CANONICAL_TIME_MS,
    )
    resolution = payload.get("scenario_resolution")
    if not isinstance(resolution, Mapping) or resolution.get("aim_family") != "target_switching":
        raise TargetSwitchingAnalysisError("scenario_resolution must be target_switching")
    quality = payload.get("visual_quality")
    if not isinstance(quality, Mapping):
        raise TargetSwitchingAnalysisError("visual_quality is required")
    click_anchor_status = payload.get("click_anchor_status")
    if click_anchor_status not in _CLICK_ANCHOR_STATUSES:
        raise TargetSwitchingAnalysisError("click_anchor_status is invalid")
    # 融合按压沿（可选；adapter 组装的 input_fusion 产物）。两字段必须成对
    # 出现；出现即接管 first_shot_latency 的出值/按住型语义。
    raw_press_edges = payload.get("press_edges")
    raw_press_validation = payload.get("press_edge_validation")
    if (raw_press_edges is None) != (raw_press_validation is None):
        raise TargetSwitchingAnalysisError("press fusion inputs are incomplete")
    press_edges = (
        _fused_press_edges(raw_press_edges, start_ms=start_ms, end_ms=end_ms)
        if raw_press_edges is not None else None
    )
    press_validation = (
        _fused_press_validation(raw_press_validation)
        if raw_press_validation is not None else None
    )
    # 证据绑定合同（extend_analysis_evidence_with_target_switching_v1）：
    # 结果必须携带 producer 的 signal_bundle / sample_sets 引用，证据提交时
    # 与已入库的视觉证据逐字匹配（篡改 fail-closed）。
    source_signal_bundle = payload.get("source_signal_bundle")
    if not isinstance(source_signal_bundle, Mapping):
        raise TargetSwitchingAnalysisError("source_signal_bundle is required")
    source_sample_sets = payload.get("source_sample_sets")
    if isinstance(source_sample_sets, (str, bytes)) or not isinstance(source_sample_sets, Sequence):
        raise TargetSwitchingAnalysisError("source_sample_sets must be a list")
    viewport_fov_deg = payload.get("viewport_fov_deg", 103.0)
    if not (
        quality.get("status") in {"accepted", "limited"}
        and "target_switching" in (quality.get("enabled_metric_families") or [])
    ):
        return _outcome_only_telemetry_result(
            analysis_ref, window, payload.get("comparison"),
            "target_switching_visual_quality_unavailable",
        )
    chains = build_switching_chains_from_telemetry_v1(
        analysis_ref=analysis_ref,
        canonical_time_window=window,
        crosshair_samples=payload.get("crosshair_samples"),
        target_tracks=payload.get("target_tracks"),
        target_lives=payload.get("target_lives"),
        authoritative_kills=payload.get("authoritative_kills"),
        viewport_fov_deg=viewport_fov_deg,
    )
    if not chains:
        return _outcome_only_telemetry_result(
            analysis_ref, window, payload.get("comparison"),
            "telemetry_switching_no_observable_chain",
        )

    rows: list[dict[str, Any]] = []
    state_groups: dict[str, list[dict[str, Any]]] = {}
    kill_events: dict[str, dict[str, Any]] = {}
    segment_specs: list[dict[str, Any]] = []
    analysis_limitations: set[str] = set()

    def register_kill(ref: str, time_ms: int, actor_ref: str) -> None:
        kill_events.setdefault(ref, {
            "event_id": ref,
            "event_kind": "kill",
            "start_ms": time_ms,
            "end_ms": time_ms,
            "actor_refs": [actor_ref],
            "source_refs": [analysis_ref],
            "confidence": 1.0,
            "attributes": {},
            "limitations": [],
        })

    for episode in chains:
        register_kill(
            episode["previous_kill_event_ref"],
            episode["leave_time_ms"],
            episode["previous_target_track_ref"],
        )
        register_kill(
            episode["next_kill_event_ref"],
            episode["next_outcome_time_ms"],
            episode["next_target_track_ref"],
        )
    for episode in chains:
        full_chain = episode["acquire_time_ms"] is not None
        leave_time = episode["leave_time_ms"]
        acquire_time = episode["acquire_time_ms"]
        settle_time = episode["settle_time_ms"]
        next_death = episode["next_outcome_time_ms"]
        event_ref = episode["episode_ref"].replace(
            ":telemetry-switch-chain:", ":switch-chain:",
        )
        row_limitations = list(episode["limitations"])
        if not full_chain:
            row_limitations.append("switch_contact_unobservable")
        first_shot_event_ref = None
        first_shot_latency = None
        first_shot_source = None
        if press_edges is not None and full_chain:
            # 链窗 [acquire, next_outcome] 内首个融合按压沿（含击杀瞬间）。
            for edge_index, edge in enumerate(press_edges, 1):
                if acquire_time <= edge["time_ms"] <= next_death:
                    first_shot_event_ref = (
                        f"{analysis_ref}:event:fusion-press:{edge_index}"
                    )
                    first_shot_latency = edge["time_ms"] - acquire_time
                    first_shot_source = edge["source"]
                    break
        row = {
            "event_ref": event_ref,
            "row_kind": "switch_chain" if full_chain else "unclassified_discrete_acquisition",
            "start_ms": leave_time,
            "end_ms": settle_time if settle_time is not None else next_death,
            "chain_ref": episode["episode_ref"],
            "classification": (
                "observable_target_switch" if full_chain
                else "unclassified_discrete_acquisition"
            ),
            "previous_outcome_association_ref": None,
            "previous_target_track_ref": episode["previous_target_track_ref"],
            "previous_outcome_time_ms": episode["previous_outcome_time_ms"],
            "leave_time_ms": leave_time,
            "candidate_count": len(episode["candidate_track_refs"]),
            "selection_observation_ref": None,
            "selected_target_track_ref": None,
            "next_target_track_ref": episode["next_target_track_ref"],
            "acquire_time_ms": acquire_time,
            "settle_time_ms": settle_time,
            "transition_time_ms": (
                acquire_time - leave_time if full_chain else None
            ),
            "transition_distance_px": episode["transition_distance_px"],
            "transition_direction_deg": episode["transition_direction_deg"],
            "transition_path_length_px": episode["transition_path_length_px"],
            "path_efficiency": episode["path_efficiency"],
            "settle_duration_ms": (
                settle_time - acquire_time if full_chain else None
            ),
            "first_shot_event_ref": first_shot_event_ref,
            "first_shot_latency_ms": first_shot_latency,
            "first_shot_press_source": first_shot_source,
            "first_damage_event_ref": None,
            "first_damage_latency_ms": None,
            "carry_over_overshoot": None,
            "carry_over_overshoot_observation_ref": None,
            "terminal_correction_observed": None,
            "terminal_correction_observation_ref": None,
            "limitations": sorted(set(row_limitations)),
        }
        rows.append(row)
        analysis_limitations.update(row_limitations)
        if not full_chain:
            continue

        def state(kind: str, time_ms: int, actor_refs: Sequence[str]) -> str:
            state_ref = f"{event_ref}:{kind}"
            state_groups.setdefault(kind, []).append({
                "event_id": state_ref,
                "event_kind": kind,
                "start_ms": time_ms,
                "end_ms": time_ms,
                "actor_refs": list(actor_refs),
                "source_refs": [analysis_ref],
                "confidence": 1.0,
                "attributes": {"row_ref": event_ref},
                "limitations": [],
            })
            return state_ref

        candidates = episode["candidate_track_refs"]
        # 权威击杀事件本身即 previous_outcome 锚（registry observation_refs 的
        # event.previous_outcome），不另发重复状态事件。
        state("leave_previous", leave_time, [episode["previous_target_track_ref"]])
        if candidates:
            state("candidate_visible", leave_time, candidates)
        state("transition", leave_time, [episode["next_target_track_ref"]])
        acquire_ref = state("next_target_acquired", acquire_time, [episode["next_target_track_ref"]])
        settle_ref = state("settle", settle_time, [episode["next_target_track_ref"]])
        segment_specs.extend([
            {
                "id": f"{event_ref}:segment:transition",
                "title": "target_switching.transition",
                "focus": leave_time,
                "events": [
                    event_ref,
                    episode["previous_kill_event_ref"],
                ],
                "metrics": [
                    "metric:target_switching.transition_time_ms@target_switching.transition_time_ms.v1",
                    "metric:target_switching.path_efficiency@target_switching.path_efficiency.v1",
                ],
                "row": row,
            },
            {
                "id": f"{event_ref}:segment:acquisition",
                "title": "target_switching.acquisition",
                "focus": acquire_time,
                "events": [event_ref, acquire_ref],
                "metrics": ["metric:target_switching.transition_time_ms@target_switching.transition_time_ms.v1"],
                "row": row,
            },
            {
                "id": f"{event_ref}:segment:terminal",
                "title": "target_switching.terminal_control",
                "focus": settle_time,
                "events": [event_ref, settle_ref],
                "metrics": ["metric:target_switching.settle_duration_ms@target_switching.settle_duration_ms.v1"],
                "row": row,
            },
        ])

    rows.sort(key=lambda row: (row["start_ms"], row["event_ref"]))
    observable_rows = [row for row in rows if row["row_kind"] == "switch_chain"]
    observable_event_refs = [row["event_ref"] for row in observable_rows]
    segment_refs = [spec["id"] for spec in segment_specs]
    condition_ref = "condition:target_switching:telemetry_truth_chain"
    # registry 口径：无 expected-target rule 时 selection 论断不产出
    # （switching.selection-observable-only@3，信号原文作 limitation）。
    base_limitations = [
        "selection evidence unavailable",
        "acquisition_cone_fixed_default",
        *quality.get("limitations", []),
    ]
    if click_anchor_status == "unreliable":
        base_limitations.append("telemetry_click_anchors_unreliable")

    def values(field: str) -> list[float | None]:
        return [row[field] for row in observable_rows]

    if press_edges is not None:
        # 融合沿接管 first_shot：点射局逐链出值（acquire → 链窗内首沿）；
        # 按住型（沿 <= 1）显式 unavailable + press-and-hold 语义 limitation
        # （数据前提不满足，非数据坏）。残差与回填计数进 limitation（规格
        # §二.1"不一致残差进 limitation、指标记录注明来源"）。
        fusion_limitations = ["first_shot_press_source_fused_telemetry_preferred"]
        if len(press_edges) <= 1:
            fusion_limitations.append("first_shot_press_and_hold_weapon")
        else:
            if press_validation["trace_only"] > 0:
                fusion_limitations.append(
                    f"press_edge_trace_backfilled_{press_validation['trace_only']}",
                )
            if press_validation["residual_max_ms"] is not None:
                fusion_limitations.append(
                    "press_edge_cross_residual_max_"
                    f"{press_validation['residual_max_ms']}ms",
                )
        first_shot_spec: tuple[str, list[Any], tuple[str, ...]] = (
            "ms",
            [row["first_shot_latency_ms"] for row in observable_rows],
            tuple(fusion_limitations),
        )
    else:
        first_shot_spec = (
            "ms",
            [],
            ("telemetry_click_anchors_unreliable",) if click_anchor_status == "unreliable" else ("first_shot_not_observed",),
        )
    metric_specs: dict[str, tuple[str, list[Any], tuple[str, ...]]] = {
        "target_switching.transition_time_ms": (
            "ms", values("transition_time_ms"), (),
        ),
        "target_switching.transition_distance_px": (
            "px", values("transition_distance_px"), (),
        ),
        "target_switching.path_efficiency": (
            "ratio", values("path_efficiency"), (),
        ),
        "target_switching.settle_duration_ms": (
            "ms", values("settle_duration_ms"), (),
        ),
        "target_switching.first_shot_latency_ms": first_shot_spec,
        "target_switching.first_damage_latency_ms": (
            "ms", [], ("first_damage_not_observed",),
        ),
        "target_switching.carry_over_overshoot_ratio": (
            "ratio", [], ("overshoot_direct_observation_unavailable",),
        ),
        "target_switching.terminal_correction_ratio": (
            "ratio", [], ("terminal_correction_direct_observation_unavailable",),
        ),
    }
    metrics = {
        key: _metric_record(
            key,
            metric_values,
            unit=unit,
            analysis_ref=analysis_ref,
            event_refs=observable_event_refs if metric_values else [],
            segment_refs=segment_refs if metric_values else [],
            condition_refs=[condition_ref],
            limitations=[*base_limitations, *extra_limitations],
        )
        for key, (unit, metric_values, extra_limitations) in metric_specs.items()
    }
    from .analysis_evidence import validate_event_bundle_v1, validate_metric_record_v1

    row_events = [{
        "event_id": row["event_ref"],
        "event_kind": row["row_kind"],
        "start_ms": row["start_ms"],
        "end_ms": row["end_ms"],
        "actor_refs": [row["next_target_track_ref"]],
        "source_refs": [analysis_ref],
        "confidence": 1.0,
        "attributes": {
            key: value for key, value in row.items()
            if key not in {"event_ref", "row_kind", "start_ms", "end_ms", "limitations"}
            and value is not None
        },
        "limitations": list(row["limitations"]),
    } for row in rows]
    # event_bundle.v1 硬上限 512：权威击杀 > 链行 > 状态事件（leave →
    # acquire → settle → transition → candidate_visible 整组取舍，与 producer
    # 的优先级截断同思路）；截断记 limitation，行/指标不受影响。
    selected_events: list[dict[str, Any]] = [
        *kill_events.values(),
        *row_events,
    ]
    emitted_ids = {event["event_id"] for event in selected_events}
    for kind in (
        "leave_previous", "next_target_acquired", "settle",
        "transition", "candidate_visible",
    ):
        group = state_groups.get(kind, [])
        if len(selected_events) + len(group) <= _EVENT_BUNDLE_LIMIT:
            selected_events.extend(group)
            emitted_ids.update(event["event_id"] for event in group)
        elif group:
            analysis_limitations.add("switching_state_events_truncated")
    event_bundle = validate_event_bundle_v1({
        "schema_version": "event_bundle.v1",
        "analysis_ref": analysis_ref,
        "events": sorted(
            selected_events,
            key=lambda event: (event["start_ms"], event["event_kind"], event["event_id"]),
        ),
        "outcome_associations": [],
    })
    for metric in metrics.values():
        validate_metric_record_v1(metric)
    evidence_segments = [
        _segment(
            analysis_ref=analysis_ref,
            window=window,
            segment_id=spec["id"],
            title_key=spec["title"],
            start_ms=spec["row"]["start_ms"],
            end_ms=spec["row"]["end_ms"],
            focus_ms=spec["focus"],
            metric_refs=spec["metrics"],
            event_refs=[
                ref for ref in spec["events"] if ref in emitted_ids
            ],
            limitations=list(spec["row"]["limitations"]),
        )
        for spec in segment_specs
    ]
    support_status = (
        "supported"
        if quality.get("status") == "accepted" and len(observable_rows) == len(rows)
        else "partial"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "analysis_ref": analysis_ref,
        "analysis_type": "target_switching",
        "support_status": support_status,
        "processed_rows": rows,
        "processed_event_tables": _processed_tables(analysis_ref, rows),
        "metrics": metrics,
        "evidence_segments": evidence_segments,
        "comparison": payload.get("comparison"),
        "limitations": sorted(set([*base_limitations, *analysis_limitations])),
        "evidence_extension": {
            "event_bundle": event_bundle,
            "metric_records": list(metrics.values()),
            "evidence_segments": evidence_segments,
            "processed_event_tables": _processed_tables(analysis_ref, rows),
            "required_outcome_associations": [],
            "required_signal_bundle": source_signal_bundle,
            "required_sample_sets": list(source_sample_sets),
            "required_canonical_time_window": dict(window),
        },
    }


__all__ = [
    "ACQUISITION_CONE_DEG",
    "ANALYSIS_VERSION",
    "INPUT_SCHEMA_VERSION",
    "KILL_CONTACT_PROXIMITY_MS",
    "SCHEMA_VERSION",
    "TargetSwitchingAnalysisError",
    "analyze_target_switching_telemetry_v1",
    "build_switching_chains_from_telemetry_v1",
]
