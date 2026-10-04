"""Owner-aware Analysis/History product commands.

After the 2026-08-13 architecture rewrite, Coach reads analysis files directly
via the Node sidecar. These Python functions remain for the desktop Analysis /
History UI routes that still proxy through the backend:

- ``/api/kovaak-runs`` and ``/api/kovaak-runs/{id}`` (run index / detail)
- ``/api/kovaak-runs/{id}/analyze`` (freeze + enqueue one run analysis)
- ``/api/sessions`` and ``/api/sessions/{id}/retry`` (analysis list / retry)

This module deliberately contains no FastAPI types and no Coach proxy imports.
It is the extracted live subset of the old ``coach_commands`` module.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

from . import history_trends, kovaak_run_store, queue
from .contracts import STALE_ANALYSIS_VERSIONS
from .kovaak_run_projection import public_kovaak_run
from .source_requirements import validate_source_requirements
from .workspace import copy_path_to_path, remove_session_workspace, session_dir

RESULT_SCHEMA_VERSION = "coach_product_command_result.v1"

# 按局遥测切窗在途时，给「冻结 → cleaner/merge → watcher 导入 → 配对」一个
# 有界落地窗口（与 mp4/raw-input 在收尾侧阻塞等待的语义对齐，只是这里放
# 在分析创建侧）：无在途切窗的 run 零开销直接过。
_TELEMETRY_CUT_WAIT_SECONDS = 15.0
_TELEMETRY_CUT_POLL_SECONDS = 0.4


async def _wait_for_in_flight_telemetry_cut(run_id: int, owner_id: str) -> None:
    """切窗在途才等；等待 = 切窗事件 + 导入配对就绪，超时放行走旧回退路径。"""
    from . import telemetry_capture_service

    if not telemetry_capture_service.run_cut_pending(run_id):
        return
    log.info("waiting for in-flight telemetry run cut run=%s", run_id)
    wait_started = time.monotonic()

    def _wait_elapsed_ms() -> float:
        return (time.monotonic() - wait_started) * 1000.0

    # wait_run_cut 返回 False=切窗已完成/不存在，True=超时仍在途（仅埋点，
    # 等待流程与返回前一致：都继续进入就绪轮询）。
    cut_settled = await asyncio.to_thread(
        telemetry_capture_service.wait_run_cut, run_id, _TELEMETRY_CUT_WAIT_SECONDS,
    )
    deadline = time.monotonic() + _TELEMETRY_CUT_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            ready = await asyncio.to_thread(
                kovaak_run_store.external_telemetry_ready, run_id, owner_id,
            )
        except Exception:
            log.exception("telemetry readiness poll failed run=%s", run_id)
            log.info(
                "telemetry cut wait done run=%s outcome=poll_error "
                "cut_settled=%s wait_ms=%.0f",
                run_id, cut_settled, _wait_elapsed_ms(),
            )
            return
        if ready:
            log.info(
                "telemetry cut wait done run=%s outcome=ready "
                "cut_settled=%s wait_ms=%.0f",
                run_id, cut_settled, _wait_elapsed_ms(),
            )
            return
        await asyncio.sleep(_TELEMETRY_CUT_POLL_SECONDS)
    log.info(
        "telemetry cut wait done run=%s outcome=timeout_released "
        "cut_settled=%s wait_ms=%.0f",
        run_id, cut_settled, _wait_elapsed_ms(),
    )


class ProductCommandError(Exception):
    """A stable application error that HTTP can map without importing FastAPI."""

    def __init__(
        self, code: str, message: str, *, kind: str = "failed", result_ref: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.kind = kind
        self.result_ref = result_ref


def _safe_run(run: dict[str, Any]) -> dict[str, Any]:
    return public_kovaak_run(run)


def _safe_analysis(session: dict[str, Any]) -> dict[str, Any]:
    error = session.get("error")
    safe_error = None
    if isinstance(error, dict):
        safe_error = {
            key: error[key]
            for key in ("schema_version", "category", "code", "message", "retryable")
            if key in error
        }
    return {
        "analysis_ref": f"analysis:{session['id']}",
        "id": session["id"],
        "status": session.get("status"),
        "analysis_type": session.get("analysis_type", "flicking"),
        "input_mode": session.get("input_mode", "video_fallback"),
        "run_ref": f"run:{session['kovaak_run_id']}" if session.get("kovaak_run_id") else None,
        "created_at": session.get("created_at"),
        "started_at": session.get("started_at"),
        "finished_at": session.get("finished_at"),
        "error": safe_error,
    }


async def list_runs(owner_id: str) -> list[dict[str, Any]]:
    return await kovaak_run_store.list_kovaak_run_summaries(owner_id)


async def get_run(owner_id: str, run_id: int) -> dict[str, Any]:
    run = await kovaak_run_store.get_kovaak_run(run_id, owner_id)
    if run is None:
        any_owner = await kovaak_run_store.get_kovaak_run_any_owner(run_id)
        if any_owner is not None:
            raise ProductCommandError("forbidden", "无权访问此 Run")
        raise ProductCommandError("not_found", "KovaaK run 不存在", kind="unavailable")
    return _public_run_projection(run, owner_id)


def _public_run_projection(run: dict[str, Any], owner_id: str) -> dict[str, Any]:
    """Project one run with its live analysis count (matches list summaries)."""
    projected = dict(run)
    projected["analysis_count"] = kovaak_run_store._get_analysis_count_for_run(
        int(run["id"]), owner_id,
    )
    return _safe_run(projected)


async def list_history(owner_id: str, *, locale: str = "zh-CN") -> list[dict[str, Any]]:
    return await queue.list_sessions(owner_id, locale=locale)


async def history_trend(owner_id: str, metric_key: str) -> dict[str, Any]:
    if not isinstance(metric_key, str) or not metric_key or len(metric_key) > 128:
        raise ProductCommandError("invalid_metric_key", "metric_key is required")
    return await history_trends.recent_trend_for_user(owner_id, metric_key)


async def get_analysis(owner_id: str, analysis_id: int) -> dict[str, Any]:
    session = await queue.get_session(analysis_id)
    if session is None:
        raise ProductCommandError("not_found", "Analysis 不存在", kind="unavailable")
    if session.get("user_id") != owner_id:
        raise ProductCommandError("forbidden", "无权访问此 Analysis")
    return _safe_analysis(session)


def _challenge_button_samples_held(
    snapshot: Mapping[str, Any],
) -> int | None:
    """Count canonical in-window Raw samples with the fire button held.

    The button field is a per-millisecond state bitmask (bit 0 = fire), so a
    sustained hold emits one held sample per ms — exactly the fire-mode signal
    the challenge-shape classifier reads. Any decode/read failure means "no
    Raw signal", never a failed analysis.
    """
    trace = snapshot.get("trace")
    window = snapshot.get("canonical_time_window")
    if not isinstance(trace, Mapping) or not isinstance(window, Mapping):
        return None
    path = trace.get("path")
    start_ms = window.get("start_ms")
    end_ms = window.get("end_ms")
    if (
        not isinstance(path, str)
        or not isinstance(start_ms, int) or isinstance(start_ms, bool)
        or not isinstance(end_ms, int) or isinstance(end_ms, bool)
        or end_ms <= start_ms
    ):
        return None
    from .kovaak_snapshot_codec import decode_mouse_snapshot_bytes

    try:
        points = decode_mouse_snapshot_bytes(Path(path).read_bytes())
    except (OSError, ValueError):
        return None
    held = 0
    for point in points:
        if start_ms <= point["timestamp_ms"] < end_ms and point["buttons"] & 1:
            held += 1
    return held


def _challenge_shape_for_run(
    run: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Bounded, path-free shape facts: Stats kills, challenge duration and,
    when the run has a Raw trace, the in-window held-button sample count."""
    stats_summary = run.get("stats_summary")
    kills = stats_summary.get("kill_count") if isinstance(stats_summary, Mapping) else None
    window = snapshot.get("canonical_time_window")
    duration_ms = window.get("duration_ms") if isinstance(window, Mapping) else None
    if (
        not isinstance(kills, int) or isinstance(kills, bool) or kills < 0
        or not isinstance(duration_ms, int) or isinstance(duration_ms, bool)
        or duration_ms <= 0
    ):
        return None
    shape: dict[str, Any] = {
        "schema_version": "scenario_challenge_shape.v1",
        "kills": kills,
        "duration_ms": duration_ms,
    }
    button_samples_held = _challenge_button_samples_held(snapshot)
    if button_samples_held is not None:
        shape["button_samples_held"] = button_samples_held
    return shape


# ── Coach 判断制第一段：分类证据包（只读，不入队不开跑）────────────────
#
# [2026-10-04] 拍板：Coach 是全产品唯一分析入口，分类改为 Coach 判断制。
# 第一段把场景名字线索、冻结旁车遥测操作特征、Stats/Raw 挑战形状粗分类与
# 字段图例组装成自描述证据包返回给 Coach；知识跟数据走（字段图例随包传输，
# 不进系统提示词）。Coach 看完证据在第二段用 analysis.create_from_run 的
# aim_family 显式发起分析，并用 scenario_memory.set 记住该图。

SCENARIO_EVIDENCE_SCHEMA_VERSION = "scenario_evidence.v1"

SCENARIO_EVIDENCE_FIELD_LEGEND = {
    "name_evidence": (
        "场景名关键词线索：matched_keywords 是命中的家族关键词"
        "（strafe/track→continuous_tracking，switch→target_switching，"
        "pasu→dynamic_clicking）；candidate_family=null 表示名字无任何家族线索"
        "（与「默认落 static_clicking」严格区分，null 不构成投票）；"
        "unique_reviewed_name_match=true 表示官方注册表存在唯一同名场景，"
        "此时 candidate_family 来自该场景的登记家族，可信度最高。"
    ),
    "telemetry_evidence": (
        "冻结旁车遥测的操作特征（null=本局无可用旁车遥测，忽略该层）。"
        "features 数字：hold_frac=开火键按住时间占比（接近 1=持续按住连发，"
        "指向 continuous_tracking；很低=点射，指向点击类）；clicks_per_min="
        "每分钟点击数（高=点击类）；clicks_per_kill=每杀点击数；mean_hold_ms="
        "平均单次按住毫秒；err_p10/err_at_click_p50=准星误差分位数（小=准星"
        "长时间贴住目标，跟踪特征）；err_spike_rate_per_s=误差尖峰频率"
        "（高=频繁大幅修正）；dir_flips_per_s=方向翻转频率（高=往返跟踪）；"
        "omega_p99/omega_frac_20_200=角速度分布（大幅甩动/跟踪占比）；"
        "alive_mean=平均同时存活目标数（>1=并发目标）；"
        "bearing_delta_at_kill_med=击杀时目标方位角（大=多目标间转火）；"
        "ang_radius_med/dist_med=目标角半径/距离中位；moving_time_share="
        "目标运动时间占比；target_speed_p50=目标速度中位（高=移动目标）。"
        "tree_candidate/tree_basis=规则判别树的家族候选与依据（统计性参考）。"
        "official_kills/official_kill_rate_per_s=官方窗击杀数与每秒杀率"
        "（高杀率+低按住=点击；极低杀率+高按住=纯跟踪）。"
    ),
    "shape_evidence": (
        "Stats/Raw 挑战形状粗分类（null=无法计算）：button_samples_held=挑战窗"
        "内开火键按住的毫秒采样数；button_samples_per_kill=每次击杀的按住采样数"
        "（>100=持续按住，指向 tracking；<50=点射，指向 clicking；50-100 为判别"
        "带，shape_class=null 不构成投票）；shape_class=tracking_candidate/"
        "clicking_candidate；basis=判定依据（fire_mode_hold/fire_mode_tap/"
        "zero_kill_sustained_fire/kill_density_fallback）。"
    ),
    "memory": (
        "既往记忆（null=该图从未判过）：confirmed_by=user 表示用户亲自确认过"
        "家族（最高优先，不要覆盖或改判）；confirmed_by=coach 表示 Coach 此前"
        "判定并记住过——同图再次分析直接沿用 current_resolution 的家族，"
        "不重复判断。"
    ),
    "current_resolution": (
        "当前记忆+瀑布应用后的场景解析：classification_source 说明结论来自"
        "哪层（scenario_override=用户确认 > coach_judged=Coach 历史判定 > "
        "local_scenario_definition > telemetry_observed/challenge_shape/"
        "name_heuristic/family_default=自动兜底）。memory 存在时 aim_family "
        "已按记忆固化，直接采用即可。"
    ),
    "how_to_judge": (
        "判断指引：各层证据独立投票，冲突时按证据强度排序（遥测特征与形状 "
        "> 名字关键词；互斥时择强）。判断后：1) 用 analysis.create_from_run "
        "传 aim_family（四家族之一）与 classification_basis（一句话依据）发起"
        "分析；2) 用 scenario_memory.set 传 scenario_hash 与 aim_family、"
        "confirmed_by=\"coach\" 记住该图，下次同图不再重复判断。四家族："
        "static_clicking=静态目标点击；dynamic_clicking=移动目标点击（pasu 等）；"
        "continuous_tracking=持续跟踪（tracking/strafe 类）；target_switching="
        "多目标间快速切换。"
    ),
}


def _scenario_evidence_name_layer(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    from kovaak_tracker.scenario_profiles import name_evidence_for_display_name

    scenario = snapshot.get("scenario")
    if not isinstance(scenario, str):
        return None
    from kovaak_tracker.scenario_profiles import load_registry

    entries = load_registry()["entries"]
    return name_evidence_for_display_name(entries, scenario)


def _scenario_evidence_telemetry_layer(
    snapshot: Mapping[str, Any],
) -> dict[str, Any] | None:
    """冻结旁车遥测证据：features 全量数字 + 判别树原始结论；无源返回 None。"""
    try:
        profile = _observed_profile_for_snapshot(snapshot)
    except (OSError, ValueError):
        return None
    if profile is None:
        return None
    verdict = profile.get("verdict") or {}
    window = profile.get("window") or {}
    official_kills = profile.get("official_kills")
    window_kind = window.get("kind")
    official_span = window.get("official_window_t")
    official_rate = None
    if (
        window_kind == "official_pairing_window"
        and isinstance(official_span, (list, tuple))
        and len(official_span) == 2
        and all(isinstance(item, (int, float)) for item in official_span)
        and official_span[1] > official_span[0]
        and isinstance(official_kills, int)
    ):
        rate = official_kills / (float(official_span[1]) - float(official_span[0]))
        official_rate = round(rate, 4)
    from kovaak_tracker.telemetry_scenario_features import (
        scenario_scoring_penalizes_fire,
    )

    return {
        "window_kind": window_kind,
        "window_duration_s": window.get("duration_s"),
        "official_kills": official_kills,
        "official_kill_rate_per_s": official_rate,
        "features": dict(profile.get("features") or {}),
        "tree_candidate": verdict.get("aim_family"),
        "tree_basis": verdict.get("basis"),
        "scoring_penalizes_fire": scenario_scoring_penalizes_fire(
            snapshot.get("scenario") if isinstance(snapshot.get("scenario"), str) else None,
        ),
    }


def _scenario_evidence_shape_layer(
    run: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, Any] | None:
    shape = _challenge_shape_for_run(run, snapshot)
    if shape is None:
        return None
    from kovaak_tracker.scenario_profiles import classify_challenge_shape_v1

    verdict = classify_challenge_shape_v1(
        shape["kills"],
        shape["duration_ms"],
        shape.get("button_samples_held"),
    )
    return {
        "kills": shape["kills"],
        "duration_ms": shape["duration_ms"],
        "button_samples_held": shape.get("button_samples_held"),
        "button_samples_per_kill": (
            verdict.get("button_samples_per_kill") if verdict else None
        ),
        "shape_class": verdict.get("shape_class") if verdict else None,
        "basis": verdict.get("basis") if verdict else None,
    }


def _scenario_evidence_memory_layer(scenario_hash: object) -> dict[str, Any] | None:
    if not isinstance(scenario_hash, str):
        return None
    override = _load_scenario_overrides().get(scenario_hash)
    if override is None:
        return None
    return {
        "aim_family": override.get("aim_family"),
        "confirmed_by": override.get("confirmed_by", "user"),
        "note": override.get("note"),
        "updated_at": override.get("updated_at"),
    }


def _build_scenario_evidence_payload(
    run: Mapping[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """组装只读证据包：各层证据互相独立，允许同时冲突（Coach 仲裁）。"""
    resolution = snapshot.get("scenario_resolution")
    return {
        "schema_version": SCENARIO_EVIDENCE_SCHEMA_VERSION,
        "run_ref": f"run:{run['id']}",
        "scenario": snapshot.get("scenario"),
        "scenario_hash": (
            resolution.get("scenario_hash")
            if isinstance(resolution, Mapping) else None
        ),
        "name_evidence": _scenario_evidence_name_layer(snapshot),
        "telemetry_evidence": _scenario_evidence_telemetry_layer(snapshot),
        "shape_evidence": _scenario_evidence_shape_layer(run, snapshot),
        "memory": _scenario_evidence_memory_layer(
            resolution.get("scenario_hash") if isinstance(resolution, Mapping) else None,
        ),
        "current_resolution": resolution if isinstance(resolution, Mapping) else None,
        "field_legend": dict(SCENARIO_EVIDENCE_FIELD_LEGEND),
    }


async def build_scenario_evidence(run_id: int, owner_id: str) -> dict[str, Any]:
    """第一段只读端点：对指定 run 组装分类证据包，不入队不开跑。"""
    run = await kovaak_run_store.get_kovaak_run(run_id, owner_id)
    if run is None:
        any_owner = await kovaak_run_store.get_kovaak_run_any_owner(run_id)
        if any_owner is not None:
            raise ProductCommandError("forbidden", "无权访问此 Run")
        raise ProductCommandError("not_found", "KovaaK run 不存在", kind="unavailable")
    try:
        snapshot = await kovaak_run_store.build_analysis_input_snapshot(run_id, owner_id)
    except (LookupError, ValueError) as exc:
        raise ProductCommandError("input_unavailable", str(exc), kind="unavailable") from exc
    # 与 create_analysis_from_run 相同的层序：记忆 > 旁车观测 > 形状。
    # Coach 在证据包里看到的 current_resolution 与随后分析固化的结论一致。
    snapshot = _apply_scenario_override_resolution(snapshot)
    snapshot = await asyncio.to_thread(_apply_telemetry_observed_resolution, snapshot)
    snapshot = _apply_challenge_shape_resolution(run, snapshot)
    return _build_scenario_evidence_payload(run, snapshot)


SCENARIO_OVERRIDES_SCHEMA_VERSION = "scenario_overrides.v1"
SCENARIO_OVERRIDES_MAX_ENTRIES = 5000
SCENARIO_OVERRIDES_MAX_BYTES = 1024 * 1024
_SCENARIO_OVERRIDE_HASH_RE = re.compile(r"[0-9a-f]{32}")
_SCENARIO_OVERRIDE_FAMILIES = {
    "static_clicking", "dynamic_clicking", "continuous_tracking", "target_switching",
}


def _load_scenario_overrides() -> dict[str, dict[str, Any]]:
    """Read user-confirmed scenario family memories from the app-data config dir.

    The file is written by the Coach sidecar (``scenario_memory.set``) after the
    user confirms a scenario's aim family once. Malformed entries are skipped
    individually; a wrong top-level shape means "no memory", never a failed
    analysis.
    """
    from . import config

    path = config.DATA_ROOT / "config" / "scenario-overrides.json"
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return {}
    if len(raw_bytes) > SCENARIO_OVERRIDES_MAX_BYTES:
        return {}
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    entries = raw.get("overrides") if isinstance(raw, Mapping) else None
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema_version") != SCENARIO_OVERRIDES_SCHEMA_VERSION
        or not isinstance(entries, Mapping)
        or len(entries) > SCENARIO_OVERRIDES_MAX_ENTRIES
    ):
        return {}
    overrides: dict[str, dict[str, Any]] = {}
    for scenario_hash, entry in entries.items():
        note = entry.get("note") if isinstance(entry, Mapping) else None
        if (
            not isinstance(scenario_hash, str)
            or not _SCENARIO_OVERRIDE_HASH_RE.fullmatch(scenario_hash)
            or not isinstance(entry, Mapping)
            or set(entry) - {"aim_family", "confirmed_by", "note", "updated_at"}
            or entry.get("aim_family") not in _SCENARIO_OVERRIDE_FAMILIES
            or (
                note is not None
                and (
                    not isinstance(note, str)
                    or len(note) > 200
                    or any(ord(char) < 32 for char in note)
                )
            )
        ):
            continue
        overrides[scenario_hash] = dict(entry)
    return overrides


def _apply_scenario_override_resolution(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Apply the scenario family memory above the heuristic chain.

    [2026-10-04] 读侧层序：用户确认（confirmed_by="user"，scenario_override）
    > Coach 判定（confirmed_by="coach"，coach_judged）> 自动瀑布。一个 hash
    只有一条记忆，写入侧已保证用户确认不被 Coach 覆盖。记忆路由该家族的
    baseline 管线且永不建立场景身份或视觉声明。
    """
    resolution = snapshot.get("scenario_resolution")
    if not isinstance(resolution, Mapping):
        return snapshot
    scenario_hash = resolution.get("scenario_hash")
    if not isinstance(scenario_hash, str):
        return snapshot
    override = _load_scenario_overrides().get(scenario_hash)
    if override is None:
        return snapshot
    coach_confirmed = override.get("confirmed_by") == "coach"
    from kovaak_tracker.scenario_profiles import (
        _FAMILY_BASELINE_LIMITATIONS,
        _family_baseline_resolution,
    )

    next_resolution = _family_baseline_resolution(
        scenario_hash=scenario_hash,
        display_name=resolution.get("display_name"),
        registry_version=resolution["registry_version"],
        manifest_version=resolution["manifest_version"],
        aim_family=override["aim_family"],
        classification_source="coach_judged" if coach_confirmed else "scenario_override",
        classification_confidence="confirmed",
        classification_basis=(
            override.get("note") if coach_confirmed and isinstance(override.get("note"), str) else None
        ),
        target_motion={"model": "unknown", "target_count_model": "unknown"},
        limitations=[
            (
                "coach_judged_is_a_coach_confirmed_family_not_an_identity"
                if coach_confirmed
                else "scenario_override_is_a_user_confirmed_family_not_an_identity"
            ),
            *_FAMILY_BASELINE_LIMITATIONS,
        ],
    )
    next_snapshot = dict(snapshot)
    next_snapshot["scenario_resolution"] = next_resolution
    return next_snapshot


def _apply_challenge_shape_resolution(
    run: Mapping[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Let the Stats-derived challenge shape refine name/default identifications.

    `.sce` structure keeps priority: the shape layer only replaces
    name-keyword or unresolved-default resolutions, and only when it reaches
    a verdict (the middle kill-density band stays undecided).
    """
    resolution = snapshot.get("scenario_resolution")
    if (
        not isinstance(resolution, Mapping)
        or resolution.get("classification_source")
        not in {"name_heuristic", "family_default"}
    ):
        return snapshot
    shape = _challenge_shape_for_run(run, snapshot)
    if shape is None:
        return snapshot
    from kovaak_tracker.scenario_profiles import resolve_scenario_profile

    scenario_hash = resolution.get("scenario_hash")
    scenario = snapshot.get("scenario")
    refined = resolve_scenario_profile(
        scenario_hash if isinstance(scenario_hash, str) else None,
        scenario if isinstance(scenario, str) else None,
        challenge_shape=shape,
    )
    if refined.get("classification_source") != "challenge_shape":
        return snapshot
    next_snapshot = dict(snapshot)
    next_snapshot["scenario_challenge_shape"] = shape
    next_snapshot["scenario_resolution"] = refined
    return next_snapshot


def _observed_profile_for_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    """冻结旁车 -> scenario_observed_profile.v1（worker 遥测同款访问方式）。

    与 worker._build_external_telemetry_visual_result 同缝：external_telemetry
    源可用 + merge_manifest.alignment.accepted 才计算；判定窗用快照的
    canonical_time_window（官方挑战窗）经 s_epoch_of_t0 映射；official kills
    只取 meta.pairing.perf_official.kills。任何缺失返回 None（层让位）。
    """
    source = (snapshot.get("sources") or {}).get("external_telemetry")
    if not isinstance(source, Mapping) or source.get("availability") != "available":
        return None
    external_id = source.get("external_run_id")
    frames_path = source.get("frames_path")
    if (
        not isinstance(external_id, str) or not external_id
        or not isinstance(frames_path, str) or not frames_path
    ):
        return None
    window = snapshot.get("canonical_time_window")
    start_ms = window.get("start_ms") if isinstance(window, Mapping) else None
    end_ms = window.get("end_ms") if isinstance(window, Mapping) else None
    if (
        isinstance(start_ms, bool) or isinstance(end_ms, bool)
        or not isinstance(start_ms, int) or not isinstance(end_ms, int)
        or end_ms <= start_ms
    ):
        return None
    from . import external_telemetry_store as telemetry_store
    from . import file_store
    from kovaak_tracker.telemetry_scenario_features import (
        build_scenario_observed_profile,
    )

    meta = telemetry_store.load_meta(external_id)
    if meta is None:
        return None
    manifest = file_store.read_json(
        telemetry_store.sidecar_path(external_id, "merge_manifest.json"),
    )
    alignment = (
        manifest.get("alignment")
        if isinstance(manifest, dict) and isinstance(manifest.get("alignment"), dict)
        else None
    )
    if alignment is None or alignment.get("accepted") is not True:
        return None
    origin = meta.get("origin") if isinstance(meta.get("origin"), dict) else {}
    try:
        round_number = int(origin.get("round"))
    except (TypeError, ValueError):
        return None
    round_file = str(origin.get("round_file") or "").replace("\\", "/").rsplit("/", 1)[-1]
    file_names = {
        "round": "round.jsonl",
        "views": (
            telemetry_store.sidecar_source_name("views", round_file)
            or f"views_{round_number:02d}.jsonl"
        ),
        "inputs": (
            telemetry_store.sidecar_source_name("inputs", round_file)
            or f"inputs_{round_number:02d}.jsonl"
        ),
    }
    pairing = meta.get("pairing") if isinstance(meta.get("pairing"), dict) else None
    perf_official = (
        pairing.get("perf_official")
        if isinstance(pairing, dict) and isinstance(pairing.get("perf_official"), dict)
        else None
    )
    official_kills = perf_official.get("kills") if perf_official else None
    if isinstance(official_kills, bool) or not isinstance(official_kills, int) or official_kills < 0:
        official_kills = None
    return build_scenario_observed_profile(
        Path(frames_path).parent,
        round_number,
        file_names=file_names,
        official_window_epoch_ms=(start_ms, end_ms),
        official_kills=official_kills,
    )


def _apply_telemetry_observed_resolution(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Let the frozen-sidecar feature tree refine name/default identifications.

    层序：用户确认/Coach 判定记忆在顶层（override 已先行替换），本地 .sce
    保持优先；本层只替换 name_heuristic/family_default，且在 challenge
    shape 之前应用（观测特征是 button_samples 的严格超集）。任何旁车/特征
    异常都静默让位——分类层绝不阻断分析。返回新快照，不就地修改。
    """
    resolution = snapshot.get("scenario_resolution")
    if (
        not isinstance(resolution, Mapping)
        or resolution.get("classification_source")
        not in {"name_heuristic", "family_default"}
    ):
        return snapshot
    try:
        profile = _observed_profile_for_snapshot(snapshot)
    except (OSError, ValueError):
        return snapshot
    if profile is None:
        return snapshot
    from kovaak_tracker.scenario_profiles import resolve_scenario_profile

    scenario_hash = resolution.get("scenario_hash")
    scenario = resolution.get("display_name")
    refined = resolve_scenario_profile(
        scenario_hash if isinstance(scenario_hash, str) else None,
        scenario if isinstance(scenario, str) else None,
        observed_profile=profile,
    )
    if refined.get("classification_source") != "telemetry_observed":
        return snapshot
    next_snapshot = dict(snapshot)
    next_snapshot["scenario_observed_profile"] = profile
    next_snapshot["scenario_resolution"] = refined
    return next_snapshot


async def _run_may_be_reclassified(owner_id: str, run_id: int) -> bool:
    """True when the user-confirmed scenario memory may reclassify a done Run.

    Cheap pre-check that keeps the done-analysis reuse fast path untouched:
    only a run whose Performance scenario hash has an override entry needs the
    fresh snapshot comparison; every other repeat call stays a pure reuse.
    """
    run = await kovaak_run_store.get_kovaak_run(run_id, owner_id)
    if run is None:
        return False
    performance_summary = run.get("performance_summary")
    header = (
        performance_summary.get("header")
        if isinstance(performance_summary, Mapping)
        else None
    )
    scenario_hash = header.get("scenario_hash") if isinstance(header, Mapping) else None
    return (
        isinstance(scenario_hash, str)
        and scenario_hash in _load_scenario_overrides()
    )


def _analysis_type_for_snapshot(snapshot: Mapping[str, Any]) -> str:
    """Keep the persisted request type aligned with the identified dispatch family."""
    resolution = snapshot.get("scenario_resolution")
    if not isinstance(resolution, Mapping):
        return "flicking"
    family = resolution.get("aim_family")
    return {
        "dynamic_clicking": "dynamic_clicking",
        "continuous_tracking": "continuous_tracking",
        "target_switching": "target_switching",
        "static_clicking": (
            "static_clicking"
            if "static_clicking.baseline.v1"
            in (resolution.get("allowed_analyzers") or [])
            else "flicking"
        ),
    }.get(family, "input_kinematics")


def _matches_frozen_copy(
    path: Path,
    fingerprint: object,
    *,
    source: Path | None = None,
) -> bool:
    if not isinstance(fingerprint, Mapping):
        return False
    expected_sha = fingerprint.get("sha256")
    expected_size = fingerprint.get("size")
    expected_mtime_ns = fingerprint.get("mtime_ns")
    if (
        not isinstance(expected_sha, str)
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or isinstance(expected_mtime_ns, bool)
        or not isinstance(expected_mtime_ns, int)
    ):
        return False
    if source is not None:
        try:
            source_stat = source.stat()
        except OSError:
            return False
        if (
            source_stat.st_size != expected_size
            or source_stat.st_mtime_ns != expected_mtime_ns
        ):
            return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return path.stat().st_size == expected_size and digest.hexdigest() == expected_sha


def _matches_frozen_hard_link(
    path: Path,
    source: Path,
    fingerprint: object,
) -> bool:
    if not isinstance(fingerprint, Mapping):
        return False
    expected_sha = fingerprint.get("sha256")
    expected_size = fingerprint.get("size")
    if (
        not isinstance(expected_sha, str)
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
    ):
        return False
    try:
        if path.is_symlink() or not path.is_file() or not path.samefile(source):
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return path.stat().st_size == expected_size and digest.hexdigest() == expected_sha
    except OSError:
        return False


def _freeze_video_source(path: Path) -> dict[str, object]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise ProductCommandError(
            "source_unavailable",
            "Video source unavailable",
            kind="unavailable",
        ) from exc
    before_revision = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_revision = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_revision != after_revision:
        raise ProductCommandError(
            "source_unavailable",
            "Video source revision changed while freezing",
            kind="unavailable",
        )
    return {
        "sha256": digest.hexdigest(),
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
    }


async def create_analysis_from_run(
    owner_id: str,
    run_id: int,
    *,
    input_mode: Literal["multimodal"] = "multimodal",
    allow_parallel: bool = False,
    force: bool = False,
    cm_per_360: float | None = None,
    fov: float | None = None,
    profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
    managed_video_source: Path | None = None,
    managed_video_fingerprint: Mapping[str, object] | None = None,
    locale: str = "zh-CN",
    aim_family: str | None = None,
    classification_basis: str | None = None,
) -> dict[str, Any]:
    """Freeze a Run and enqueue its highest valid automatic evidence tier.

    ``input_mode`` remains an internal compatibility argument for older callers.
    New Run Analysis never trusts it: the frozen snapshot is the sole source
    of the selected tier.

    [fix 2026-10-04] ``force`` 跳过 done 复用门（场景类型修正后的重跑必须
    产出新分析，不能返回缓存旧结果）；run_active 复用门保留——已有在途
    分析时不叠加第二个。

    [2026-10-04] Coach 判断制第二段：``aim_family`` 是 Coach 对本局的显式
    家族判断（四家族白名单）。指定时输入快照的 scenario_resolution 按该
    家族固化（classification_source="coach_specified"、confidence=
    "confirmed"，basis 记录 Coach 给出的依据可选）；用户确认记忆
    （scenario_override）保持最高优先，Coach 指定不覆盖它。未指定时保持
    现状瀑布（名字/遥测线索的既有自动逻辑）作为兜底。
    """
    if aim_family is not None and aim_family not in _SCENARIO_OVERRIDE_FAMILIES:
        raise ProductCommandError(
            "invalid_aim_family",
            "aim_family must be one of " + ", ".join(sorted(_SCENARIO_OVERRIDE_FAMILIES)),
        )
    if classification_basis is not None and (
        not isinstance(classification_basis, str)
        or not classification_basis.strip()
        or len(classification_basis) > 200
        or any(ord(char) < 32 for char in classification_basis)
    ):
        raise ProductCommandError(
            "invalid_classification_basis",
            "classification_basis must be a string of at most 200 characters",
        )
    existing = await queue.get_run_analysis_states(owner_id, run_id)
    # A done analysis is the reusable answer for this Run unless the
    # user-confirmed scenario memory (or an explicit Coach family judgement)
    # may have reclassified it since. Stale algorithm versions are never the
    # answer: a code upgrade changed what the analysis should say (e.g.
    # tracking.generic_visual.v1's inflated in-target metrics), so those
    # rebuild instead of pinning pre-upgrade results.
    completed = next(
        (
            item for item in existing
            if item.get("status") == "done"
            and item.get("analysis_version") not in STALE_ANALYSIS_VERSIONS
        ),
        None,
    )
    reclassified = (
        completed is not None
        and (aim_family is not None or await _run_may_be_reclassified(owner_id, run_id))
    )
    # [fix 2026-10-04] 复用判定 = done 且 force 未设且（非 reclassifiable 或
    # 解析后快照的类型与 completed.analysis_type 相同）。force=True 是用户
    # 纠正场景类型后的显式重跑，必须产出新分析，两条复用路都跳过；类型
    # 一致性由下方 reclassifiable 分支用解析后快照核对（override 会改
    # aim_family，raw snapshot 判型会判错）。无 override 且类型本来就一致的
    # 日常重复调用维持纯复用快路径，不额外构建 snapshot。
    if completed is not None and not force and not reclassified:
        session_id = int(completed["id"])
        return {
            "session_id": session_id,
            "analysis_ref": f"analysis:{session_id}",
            "reused": True,
        }
    run_active = next(
        (item for item in existing if item.get("status") in {"uploading", "queued", "running"}),
        None,
    )
    if run_active is not None:
        session_id = int(run_active["id"])
        return {
            "session_id": session_id,
            "analysis_ref": f"analysis:{session_id}",
            "reused": True,
        }
    active = await queue.get_active_session(owner_id)
    if active is not None and not allow_parallel:
        raise ProductCommandError(
            "active_analysis",
            "已有 Analysis 正在进行",
            kind="unavailable",
            result_ref=f"analysis:{active['id']}",
        )
    run = await kovaak_run_store.get_kovaak_run(run_id, owner_id)
    if run is None:
        any_owner = await kovaak_run_store.get_kovaak_run_any_owner(run_id)
        if any_owner is not None:
            raise ProductCommandError("forbidden", "无权访问此 Run")
        raise ProductCommandError("not_found", "KovaaK run 不存在", kind="unavailable")
    # 快照冻结前给在途的按局遥测切窗一个有界落地窗口（无在途零开销）。
    # 事实核对（仅注释）：分析创建没有 finalization 门——全程不检查
    # run.finalization_state；遥测档（telemetry_multimodal）只要
    # external_telemetry+stats+performance+canonical_window 就绪即可选档，
    # 不要求视频，与 mp4 导出收尾链路解耦。
    await _wait_for_in_flight_telemetry_cut(run_id, owner_id)
    try:
        snapshot = await kovaak_run_store.build_analysis_input_snapshot(run_id, owner_id)
    except (LookupError, ValueError) as exc:
        raise ProductCommandError("input_unavailable", str(exc), kind="unavailable") from exc
    snapshot = _apply_scenario_override_resolution(snapshot)
    # 旁车观测层：旁车 JSONL 读取较重，放线程避免阻塞事件循环。
    snapshot = await asyncio.to_thread(_apply_telemetry_observed_resolution, snapshot)
    snapshot = _apply_challenge_shape_resolution(run, snapshot)
    if aim_family is not None:
        # Coach 判断制第二段：显式家族判断压轴固化（用户确认记忆最高优先，
        # scenario_override 已在记忆层应用时不被覆盖）。
        resolution = snapshot.get("scenario_resolution")
        if not isinstance(resolution, Mapping) or (
            resolution.get("classification_source") != "scenario_override"
        ):
            from kovaak_tracker.scenario_profiles import (
                _FAMILY_BASELINE_LIMITATIONS,
                _family_baseline_resolution,
            )

            snapshot = dict(snapshot)
            snapshot["scenario_resolution"] = _family_baseline_resolution(
                scenario_hash=(
                    resolution.get("scenario_hash")
                    if isinstance(resolution, Mapping) else None
                ),
                display_name=(
                    resolution.get("display_name")
                    if isinstance(resolution, Mapping) else None
                ),
                registry_version=(
                    resolution["registry_version"]
                    if isinstance(resolution, Mapping) else ""
                ),
                manifest_version=(
                    resolution["manifest_version"]
                    if isinstance(resolution, Mapping) else ""
                ),
                aim_family=aim_family,
                classification_source="coach_specified",
                classification_confidence="confirmed",
                classification_basis=classification_basis,
                target_motion={"model": "unknown", "target_count_model": "unknown"},
                limitations=[
                    "coach_specified_is_a_coach_judged_family_not_an_identity",
                    *_FAMILY_BASELINE_LIMITATIONS,
                ],
            )
    if reclassified and not force:
        # [fix 2026-10-04] 补 not force：显式重跑即使类型一致也不复用。
        # The override leaves the done analysis stale only when it changes the
        # dispatch: a same-family confirmation (or an exact reviewed hash
        # keeping priority) keeps the done analysis as this Run's answer.
        completed_session = await queue.get_session(int(completed["id"]))
        if (
            completed_session is not None
            and completed_session.get("analysis_type")
            == _analysis_type_for_snapshot(snapshot)
        ):
            session_id = int(completed["id"])
            return {
                "session_id": session_id,
                "analysis_ref": f"analysis:{session_id}",
                "reused": True,
            }

    run_video = snapshot["sources"].get("video")
    run_video_source = None
    if managed_video_source is None and isinstance(run_video, Mapping):
        run_video_path = run_video.get("path")
        run_video_fingerprint = run_video.get("fingerprint")
        if (
            isinstance(run_video_path, str)
            and Path(run_video_path).is_file()
            and isinstance(run_video_fingerprint, Mapping)
        ):
            run_video_source = Path(run_video_path)
    video_fingerprint = None
    if managed_video_source is not None:
        video_fingerprint = (
            dict(managed_video_fingerprint)
            if isinstance(managed_video_fingerprint, Mapping)
            else await asyncio.to_thread(_freeze_video_source, managed_video_source)
        )
        run_video_fingerprint = (
            run_video.get("fingerprint") if isinstance(run_video, Mapping) else None
        )
        preserves_run_identity = (
            isinstance(run_video, Mapping)
            and isinstance(run_video_fingerprint, Mapping)
            and run_video_fingerprint.get("sha256") == video_fingerprint.get("sha256")
            and run_video_fingerprint.get("size") == video_fingerprint.get("size")
        )
        snapshot["sources"]["video"] = {
            **(dict(run_video) if preserves_run_identity else {}),
            "basename": managed_video_source.name,
            "fingerprint": video_fingerprint,
            "path": str(managed_video_source.resolve()),
            "availability": "available",
            "format_version": "mp4",
        }
    elif run_video_source is not None and isinstance(run_video, Mapping):
        video_fingerprint = dict(run_video["fingerprint"])

    source_gate = validate_source_requirements(snapshot)
    if not source_gate["ready"]:
        missing = ", ".join(str(item) for item in source_gate["missing"])
        raise ProductCommandError(
            "input_unavailable",
            f"required Run sources are unavailable: {missing}",
            kind="unavailable",
        )
    selected_mode = source_gate["selected_mode"]
    if not isinstance(selected_mode, str):  # guarded by ready; keeps the queue contract strict
        raise ProductCommandError("input_unavailable", "Run has no supported analysis tier", kind="unavailable")
    snapshot["source_requirements_version"] = "automatic_quality_tier.v1"

    try:
        session_id = await queue.enqueue(
            owner_id,
            "",
            "",
            cm_per_360=cm_per_360,
            fov=fov,
            profile_default=dict(profile_default) if isinstance(profile_default, Mapping) else None,
            manual_override=dict(manual_override) if isinstance(manual_override, Mapping) else None,
            analysis_type=_analysis_type_for_snapshot(snapshot),
            input_mode=selected_mode,
            kovaak_run_id=run_id,
            input_snapshot=snapshot,
            status="uploading",
            require_no_active=not allow_parallel,
            video_receipt=run.get("video_receipt"),
            locale=locale,
        )
    except queue.ActiveSessionExists as exc:
        active = await queue.get_active_session(owner_id)
        raise ProductCommandError(
            "active_analysis",
            "已有 Analysis 正在进行",
            kind="unavailable",
            result_ref=f"analysis:{active['id']}" if active is not None else None,
        ) from exc
    try:
        managed_video = ""
        managed_csv = ""
        workspace = session_dir(session_id)
        # telemetry_multimodal 与 multimodal 同为视频参与档：工作区视频别名照常冻结。
        uses_video = selected_mode in {"multimodal", "video_fallback", "telemetry_multimodal"}
        if uses_video and managed_video_source is not None:
            video_destination = workspace / "video.mp4"
            try:
                # 源视频整文件复制放线程池：数百 MB 级复制不能占住事件循环。
                await asyncio.to_thread(
                    copy_path_to_path, managed_video_source, video_destination,
                )
            except OSError as exc:
                try:
                    # 全文件 SHA256 放线程池：数百 MB 级校验不能占住事件循环。
                    observed_fingerprint = await asyncio.to_thread(
                        _freeze_video_source, managed_video_source,
                    )
                except ProductCommandError as source_exc:
                    raise source_exc from exc
                if observed_fingerprint != video_fingerprint:
                    raise ProductCommandError(
                        "source_unavailable",
                        "Video source revision changed before managed copy",
                        kind="unavailable",
                    ) from exc
                raise
            if not await asyncio.to_thread(
                _matches_frozen_copy,
                video_destination,
                video_fingerprint,
                source=managed_video_source,
            ):
                raise ProductCommandError(
                    "source_unavailable",
                    "Video source revision changed before managed copy",
                    kind="unavailable",
                )
            managed_video = str(video_destination)
        elif uses_video and run_video_source is not None:
            video_destination = workspace / "video.mp4"
            workspace.mkdir(parents=True, exist_ok=True)
            # Re-analysis of an already-analysed run may retry into a workspace that
            # still holds a prior (partial) freeze; os.link is not idempotent. Reuse an
            # existing matching hard link instead of failing with FileExistsError.
            if not (
                video_destination.exists()
                and await asyncio.to_thread(
                    _matches_frozen_hard_link,
                    video_destination, run_video_source, run_video_fingerprint,
                )
            ):
                if video_destination.exists():
                    video_destination.unlink()
                os.link(run_video_source, video_destination)
            if not await asyncio.to_thread(
                _matches_frozen_hard_link,
                video_destination,
                run_video_source,
                run_video_fingerprint,
            ):
                raise ProductCommandError(
                    "source_unavailable",
                    "Run video revision changed before managed link",
                    kind="unavailable",
                )
            managed_video = str(video_destination)
        if selected_mode == "video_fallback":
            run_stats = snapshot["sources"].get("stats")
            stats_path = run_stats.get("path") if isinstance(run_stats, Mapping) else None
            stats_fingerprint = (
                run_stats.get("fingerprint") if isinstance(run_stats, Mapping) else None
            )
            if not isinstance(stats_path, str) or not isinstance(stats_fingerprint, Mapping):
                raise ProductCommandError(
                    "source_unavailable",
                    "Stats source identity is unavailable",
                    kind="unavailable",
                )
            stats_source = Path(stats_path)
            stats_destination = workspace / "stats.csv"
            try:
                copy_path_to_path(stats_source, stats_destination)
            except OSError as exc:
                try:
                    source_matches = await asyncio.to_thread(
                        _matches_frozen_copy, stats_source, stats_fingerprint,
                    )
                except OSError:
                    source_matches = False
                if not source_matches:
                    raise ProductCommandError(
                        "source_unavailable",
                        "Stats source revision changed before managed copy",
                        kind="unavailable",
                    ) from exc
                raise
            if not await asyncio.to_thread(
                _matches_frozen_copy,
                stats_destination,
                stats_fingerprint,
                source=stats_source,
            ):
                raise ProductCommandError(
                    "source_unavailable",
                    "Stats source revision changed before managed copy",
                    kind="unavailable",
                )
            managed_csv = str(stats_destination)
        await queue.set_session_input_paths(session_id, owner_id, managed_video, managed_csv)
        if not await queue.finish_upload(session_id):
            raise ProductCommandError("upload_state_lost", "分析输入状态已失效，请重新提交", kind="unavailable")
    except ProductCommandError:
        try:
            remove_session_workspace(session_id)
        finally:
            await queue.abort_uploading_session(session_id, owner_id)
        raise
    except Exception as exc:
        try:
            remove_session_workspace(session_id)
        finally:
            await queue.abort_uploading_session(session_id, owner_id)
        log.exception("input snapshot setup failed run_id=%s session_id=%s", run_id, session_id)
        raise ProductCommandError(
            "input_setup_failed",
            "无法建立分析输入快照",
        ) from exc
    return {
        "session_id": session_id,
        "analysis_ref": f"analysis:{session_id}",
        "input_mode": selected_mode,
        "limitations": [
            item for item in source_gate["missing"]
            if isinstance(item, str)
        ],
    }


async def retry_analysis(owner_id: str, analysis_id: int) -> dict[str, Any]:
    await get_analysis(owner_id, analysis_id)
    active = await queue.get_active_session(owner_id)
    if active is not None and int(active["id"]) != analysis_id:
        raise ProductCommandError(
            "active_analysis",
            "已有其它 Analysis 正在进行",
            kind="unavailable",
            result_ref=f"analysis:{active['id']}",
        )
    try:
        updated = await queue.requeue_for_retry(analysis_id)
    except queue.RetryNotAllowed as exc:
        kind = "unavailable" if exc.code in {
            "active_analysis", "not_found", "missing_video", "missing_csv", "missing_snapshot",
        } else "failed"
        raise ProductCommandError(exc.code, exc.message, kind=kind) from exc
    return _safe_analysis(updated)


def _failure_result(
    command_id: str,
    code: str,
    message: str,
    *,
    kind: str = "failed",
    result_ref: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command_id": command_id,
        "status": "unavailable" if kind == "unavailable" else "failed",
        "warning_or_error": {"code": code, "message": message},
    }
    if result_ref is not None:
        out["result_ref"] = result_ref
    return out


async def execute_trusted_analysis_create(
    owner_id: str,
    run_id: int,
    *,
    cm_per_360: float | None,
    fov: float | None,
    profile_default: Mapping[str, object] | None = None,
    manual_override: Mapping[str, object] | None = None,
    managed_video_source: Path | None = None,
    idempotency_key: str | None = None,
    allow_parallel: bool = False,
    force: bool = False,
    locale: str = "zh-CN",
    aim_family: str | None = None,
    classification_basis: str | None = None,
) -> dict[str, Any]:
    """Execute the validated desktop Analysis write and return the canonical result."""
    command_id = "analysis.create_from_run"
    if not isinstance(owner_id, str) or not owner_id.strip():
        return _failure_result(command_id, "invalid_owner", "owner is required")

    video_fingerprint = None
    if managed_video_source is not None:
        try:
            # 全文件 SHA256 放线程池：数百 MB 级校验不能占住事件循环。
            video_fingerprint = await asyncio.to_thread(
                _freeze_video_source, managed_video_source,
            )
        except ProductCommandError as exc:
            return _failure_result(command_id, exc.code, exc.message, kind=exc.kind)
    try:
        created = await create_analysis_from_run(
            owner_id,
            run_id,
            cm_per_360=cm_per_360,
            fov=fov,
            profile_default=profile_default,
            manual_override=manual_override,
            managed_video_source=managed_video_source,
            managed_video_fingerprint=video_fingerprint,
            allow_parallel=allow_parallel,
            force=force,
            locale=locale,
            aim_family=aim_family,
            classification_basis=classification_basis,
        )
    except ProductCommandError as exc:
        return _failure_result(
            command_id,
            exc.code,
            exc.message,
            kind=exc.kind,
            result_ref=exc.result_ref,
        )
    analysis_ref = created.get("analysis_ref") or f"analysis:{created['session_id']}"
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command_id": command_id,
        "status": "succeeded",
        "result_ref": analysis_ref,
        "result": {**created, "analysis_ref": analysis_ref},
    }


async def execute_analysis_retry(
    owner_id: str,
    analysis_id: int,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Re-enqueue a failed Analysis and return the canonical product result."""
    command_id = "analysis.retry"
    if not isinstance(owner_id, str) or not owner_id.strip():
        return _failure_result(command_id, "invalid_owner", "owner is required")
    try:
        retried = await retry_analysis(owner_id, analysis_id)
    except ProductCommandError as exc:
        return _failure_result(
            command_id,
            exc.code,
            exc.message,
            kind=exc.kind,
            result_ref=exc.result_ref,
        )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command_id": command_id,
        "status": "succeeded",
        "result_ref": retried.get("analysis_ref"),
        "result": retried,
    }
