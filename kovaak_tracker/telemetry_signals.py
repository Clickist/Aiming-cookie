"""External telemetry producer: world-truth sidecars projected as a CV-shaped
``visual_result``.

外部遥测 producer（SIDECARS 合同，见 FPSAimTrainer analysis/external/SIDECARS.md）：
读取真值旁车目录（round_NN / views_NN / inputs_NN / bb.json / merge_manifest.json），
把角度域真值投影到虚拟 1920x1080 视口（px 域），产出与 CV 路径
``visual_signals._preprocess_visual_signals_with_global_identity_v1`` 完全同形的
``visual_result``，使五个分析器 / 证据 / 前端 / coach 零改动消费。

投影数学（已实证链路，参照 merge_channels.kill_click_check 与 SIDECARS §4）：
- 目标方向角：yaw_t = atan2(dy, dx)、pitch_t = atan2(dz, hypot(dx, dy))；
- 角差：dyaw = wrap(yaw_t - cam_yaw 到 ±pi)、dpitch = pitch_t - cam_pitch
  （UE4 约定：views.rot = [pitch, yaw, roll]，度）；
- 视口：游戏渲染为 rectilinear/针孔投影；KovaaK fov 为水平 fov
  （UE POV.FOV），单焦距 f = (W/2)/tan(hfov/2)（103° 时 f≈763.6），
  px_x = W/2 + f*tan(dyaw)，px_y = H/2 - f*tan(dpitch)，准心恒 (W/2, H/2)；
- 目标像素半径：角半径 atan(bb_radius_cm / 视点-目标距离) * f；
- 视锥门（producer 循环）：前方但出视锥（|dyaw| > hfov/2 + 角半径 或
  |dpitch| > vfov/2 + 角半径，vfov/2 = atan((H/W)·tan(hfov/2))）的样本在
  tan 爆炸区，直接丢弃不产样本，run 级记 target_outside_frustum_skipped。

fov 用该轮 views 流的稳健值（中位；views 有少量 fov 撕裂帧），全轮统一投影；
量化取整后编入 visual quality profile ref（不同 fov 的 px/rad 尺度不可比）。

时间域：旁车 t 为源文件相对秒；本切片不查 KovaaKRun，canonical ms 由入参
canonical_window 直接给定：canonical_ms = window_start + (t - t_origin)*1000。
t_origin：源目录入口取 rounds_index 该轮 t_start；冻结入口优先取本局 meta 的
origin_t（[fix 2026-10-05e] worker 用对齐回执锚换算的帧域局窗起点，producer
只消费不推导），缺失回退 t_start（frozen_round_meta 透传，不读共享索引）、
再回退轮帧首帧 t。

文件名布局：默认消费源目录（round_NN / views_NN / inputs_NN）；Aiming Cookie
侧的冻结副本（{DATA_ROOT}/external/ext-<id>/）把轮帧固定重命名为 round.jsonl，
views/inputs 保留源文件名，故入口提供 analysis_ref / file_names 显式覆盖，
供 worker 侧按 ExternalTelemetryRun 精确映射，不改投影数学。
"""

from __future__ import annotations

import bisect
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path

# 产物形状与版本对齐 visual_signals.py（producer 身份可区分，形状合同不变）。
from .visual_signals import VISUAL_SIGNAL_SCHEMA_VERSION
from .analysis_evidence import validate_event_bundle_v1, validate_signal_bundle_v1

TELEMETRY_PRODUCER_ID = "external_telemetry"
TELEMETRY_PRODUCER_VERSION = "telemetry_signals.v1"
TELEMETRY_TIME_MAPPING_VERSION = "telemetry_time_mapping.v1"
TELEMETRY_TIMEBASE_VERSION = "telemetry_sidecar_t.v1"

VIEWPORT_WIDTH_PX = 1920
VIEWPORT_HEIGHT_PX = 1080
# bb.json 缺失（如 verify0831 会话）或挑战窗未覆盖时的兜底半径（cm）。
# 纯粹为让 visible_radius 通道可计算，必须记 limitation 提示不可作几何依据。
FALLBACK_TARGET_RADIUS_CM = 30.0
# 与 visual_signals 一致的合同约束。
_EVENT_BUNDLE_LIMIT = 512
_MAX_SAMPLE_GAP_MS = 100
# 轮帧插值只在同一条生命窗内做；边界放宽 50ms 吸收 cleaner 的量化误差。
_LIFE_FRAME_EPSILON_S = 0.05
ALL_METRIC_FAMILIES = ("dynamic_clicking", "tracking", "switching")
# 这些 limitation 触发与 CV 相同的 fail-closed 门槛：全 family 停用。
_DISABLE_ALL_LIMITATIONS = frozenset({
    "visual_frame_gap",
    "visual_event_budget_exceeded",
})


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    return records


def _focal_length_px(horizontal_fov_deg: float) -> float:
    """rectilinear 针孔焦距：f = (W/2)/tan(hfov/2)。

    KovaaK fov 为水平 fov（UE POV.FOV）；焦距对横竖轴相同（同一相机），
    px = f*tan(角度)，比线性角度投影更贴近游戏实际渲染（中心区线性值
    系统性偏大 ~15%）。"""
    return (VIEWPORT_WIDTH_PX / 2.0) / math.tan(math.radians(horizontal_fov_deg) / 2.0)


def _wrap_pi(angle_rad: float) -> float:
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def _target_angles(
    target: tuple[float, float, float],
    camera_pos: Sequence[float],
) -> tuple[float, float]:
    """目标相对相机的 (yaw, pitch)，弧度，UE4 约定（rot[0]=pitch, rot[1]=yaw）。"""
    dx = target[0] - camera_pos[0]
    dy = target[1] - camera_pos[1]
    dz = target[2] - camera_pos[2]
    return math.atan2(dy, dx), math.atan2(dz, math.hypot(dx, dy))


def _is_in_front(
    target: tuple[float, float, float],
    camera_pos: Sequence[float],
    rot: Sequence[float],
) -> bool:
    """目标是否在相机前方（前向点积 > 0），避免绕 ±yaw wrap 投影出镜像坐标。"""
    dx = target[0] - camera_pos[0]
    dy = target[1] - camera_pos[1]
    dz = target[2] - camera_pos[2]
    pitch = math.radians(float(rot[0]))
    yaw = math.radians(float(rot[1]))
    forward = (
        math.cos(pitch) * math.cos(yaw),
        math.cos(pitch) * math.sin(yaw),
        math.sin(pitch),
    )
    return (dx * forward[0] + dy * forward[1] + dz * forward[2]) > 0.0


def project_world_to_viewport(
    *,
    target: tuple[float, float, float],
    camera_pos: Sequence[float],
    rot: Sequence[float],
    horizontal_fov_deg: float,
    target_radius_cm: float,
) -> tuple[float, float, float]:
    """世界坐标 -> 虚拟视口 (px_x, px_y, px_radius)。目标在相机后方时抛 ValueError。"""
    if not _is_in_front(target, camera_pos, rot):
        raise ValueError("target is behind the camera")
    focal_px = _focal_length_px(horizontal_fov_deg)
    yaw_target, pitch_target = _target_angles(target, camera_pos)
    dyaw = _wrap_pi(yaw_target - math.radians(float(rot[1])))
    dpitch = pitch_target - math.radians(float(rot[0]))
    px_x = VIEWPORT_WIDTH_PX / 2.0 + focal_px * math.tan(dyaw)
    px_y = VIEWPORT_HEIGHT_PX / 2.0 - focal_px * math.tan(dpitch)
    distance = math.dist(target, tuple(float(item) for item in camera_pos))
    angular_radius = math.atan2(target_radius_cm, distance) if distance > 0 else math.pi / 2
    return px_x, px_y, angular_radius * focal_px


def _is_in_view_frustum(
    *,
    target: tuple[float, float, float],
    camera_pos: Sequence[float],
    rot: Sequence[float],
    horizontal_fov_deg: float,
    target_radius_cm: float,
) -> bool:
    """角度域视锥判定（双轴 + 目标包围圆，与 px 投影同一组角量）。

    可见 ⇔ |dyaw| ≤ hfov/2 + angular_radius 且 |dpitch| ≤ vfov/2 + angular_radius，
    其中 vfov/2 = atan((H/W)·tan(hfov/2))。angular_radius 为目标角半径：这是
    包围球-视锥相交测试（游戏引擎 culling 标准做法），中心出界但圆盘与视口
    相交 = 部分可见 = 保留。阈值判定天然挡掉 px=f·tan(θ) 在 |θ|→90° 的爆炸
    样本；pitch 侧无 wrap（dpitch ∈ ±180°），超界 dpitch 同样被阈值拦下。
    """
    yaw_target, pitch_target = _target_angles(target, camera_pos)
    dyaw = _wrap_pi(yaw_target - math.radians(float(rot[1])))
    dpitch = pitch_target - math.radians(float(rot[0]))
    distance = math.dist(target, tuple(float(item) for item in camera_pos))
    angular_radius = math.atan2(target_radius_cm, distance) if distance > 0 else math.pi / 2
    hfov_half = math.radians(horizontal_fov_deg) / 2.0
    vfov_half = math.atan(
        (VIEWPORT_HEIGHT_PX / VIEWPORT_WIDTH_PX) * math.tan(hfov_half)
    )
    return (
        abs(dyaw) <= hfov_half + angular_radius
        and abs(dpitch) <= vfov_half + angular_radius
    )


def _load_round_target_positions(
    round_dir: Path,
    round_number: int,
    frames_name: str | None = None,
) -> tuple[dict[int, list[tuple[float, float, float, float]]], float | None]:
    """round_NN.jsonl -> addr -> 按时间排序的 [(t, x, y, z)]；附带首帧 t。"""
    positions: dict[int, list[tuple[float, float, float, float]]] = {}
    first_t: float | None = None
    for record in _read_jsonl(
        round_dir / (frames_name or f"round_{round_number:02d}.jsonl")
    ):
        if record.get("ev") != "frame":
            continue
        try:
            frame_t = float(record["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if first_t is None or frame_t < first_t:
            first_t = frame_t
        for entry in record.get("targets") or []:
            if not isinstance(entry, Sequence) or isinstance(entry, str) or len(entry) < 4:
                continue
            try:
                addr = int(entry[0])
                point = (frame_t, float(entry[1]), float(entry[2]), float(entry[3]))
            except (TypeError, ValueError):
                continue
            positions.setdefault(addr, []).append(point)
    for points in positions.values():
        points.sort()
    return positions, first_t


def _load_views(
    round_dir: Path,
    round_number: int,
    views_name: str | None = None,
) -> list[tuple[float, tuple[float, ...], tuple[float, ...], float | None]]:
    """views_NN.jsonl -> 按时间排序的 [(t, pos, rot, fov)]。"""
    frames: list[tuple[float, tuple[float, ...], tuple[float, ...], float | None]] = []
    for record in _read_jsonl(
        round_dir / (views_name or f"views_{round_number:02d}.jsonl")
    ):
        try:
            frame_t = float(record["t"])
            pos = tuple(float(item) for item in record["pos"])
            rot = tuple(float(item) for item in record["rot"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(pos) != 3 or len(rot) != 3:
            continue
        raw_fov = record.get("fov")
        fov = float(raw_fov) if isinstance(raw_fov, (int, float)) and math.isfinite(float(raw_fov)) else None
        frames.append((frame_t, pos, rot, fov))
    frames.sort(key=lambda item: item[0])
    return frames


def _load_click_times(
    round_dir: Path,
    round_number: int,
    inputs_name: str | None = None,
) -> list[float]:
    """inputs_NN.jsonl 的 L_down 逐发沿时刻（源 t 域，秒），升序。"""
    clicks: list[float] = []
    for record in _read_jsonl(
        round_dir / (inputs_name or f"inputs_{round_number:02d}.jsonl")
    ):
        buttons = record.get("btn")
        if not isinstance(buttons, Sequence) or isinstance(buttons, str):
            continue
        if not any(button == "L_down" for button in buttons):
            continue
        try:
            clicks.append(float(record["t"]))
        except (KeyError, TypeError, ValueError):
            continue
    clicks.sort()
    return clicks


def _load_round_index_entry(
    round_dir: Path,
    manifest: dict | None,
    round_number: int,
) -> tuple[dict | None, list[str]]:
    """定位 rounds_index.json 并取该轮条目；找不到返回 (None, limitations)。

    final_0831 的 rounds_index 在旁车目录上一级；verify0831 在会话目录。
    merge_manifest 的 rounds_index/round_dir 字段是绝对路径，按 basename 匹配。
    basename 不匹配即 fail-closed 返回 None：共享索引是活文件（后续导入会
    覆盖），按 (round, file) 兜底"唯一命中"会把新局身份误认成旧局——该兜底
    已退役，冻结路径的身份由 frozen_round_meta 透传（见 build_telemetry_
    visual_result）。
    """
    limitations: list[str] = []
    candidates: list[Path] = []
    if isinstance(manifest, Mapping):
        index_path = manifest.get("rounds_index")
        if isinstance(index_path, str) and index_path:
            candidates.append(Path(index_path))
    candidates.append(round_dir / "rounds_index.json")
    candidates.append(round_dir.parent / "rounds_index.json")
    index: dict | None = None
    for candidate in candidates:
        loaded = _read_json(candidate)
        if isinstance(loaded, dict):
            index = loaded
            break
    if index is None:
        limitations.append("rounds_index_missing_addr_dedup_identity")
        return None, limitations
    round_dir_name = round_dir.name
    for source in index.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        source_outdir = source.get("outdir")
        source_name = source.get("source")
        matches = (
            (isinstance(source_outdir, str) and Path(source_outdir).name == round_dir_name)
            or (isinstance(source_name, str) and Path(source_name).name == f"{round_dir_name}.jsonl")
        )
        if not matches:
            continue
        for entry in source.get("rounds") or []:
            if isinstance(entry, Mapping) and entry.get("round") == round_number:
                return entry, limitations
    limitations.append("rounds_index_missing_addr_dedup_identity")
    return None, limitations


def _frozen_round_meta_entry(frozen_round_meta: Mapping) -> dict:
    """冻结本局 meta（targets/t_start/origin_t）-> 等价 rounds_index 该轮条目。

    共享 rounds_index 是活文件，后续导入会覆盖它；(round, file) 命中会把
    新局身份误认成旧局，因此冻结路径只认入库时随本局保存的身份：
    addr_hex 按 16 进制解析；条目/生命窗解析失败跳过（与 index 条目同款
    容错）；t_start 缺失时不写入，由调用方回退轮帧首帧 t。
    [fix 2026-10-05e] origin_t（帧域局窗起点，worker 用对齐回执锚换算）优先
    于 t_start 作 canonical 映射原点——t_start 是采集窗起点带局前垫，直接
    当原点会把 canonical 窗整体偏早（run 54095 实测偏早 7.88s：丢局末击杀、
    混入局前自然收尾段误判 kill）。缺失时回退 t_start 现行为。
    """
    entry: dict = {"targets": []}
    raw_targets = frozen_round_meta.get("targets")
    if isinstance(raw_targets, list):
        for item in raw_targets:
            if not isinstance(item, Mapping):
                continue
            try:
                tid = int(item["tid"])
                addr = int(str(item["addr_hex"]), 16)
            except (KeyError, TypeError, ValueError):
                continue
            lives: list[dict] = []
            raw_lives = item.get("lives")
            if isinstance(raw_lives, list):
                for life in raw_lives:
                    if not isinstance(life, Mapping):
                        continue
                    try:
                        lives.append({
                            "t_start": float(life["t_start"]),
                            "t_end": float(life["t_end"]),
                        })
                    except (KeyError, TypeError, ValueError):
                        continue
            entry["targets"].append({"tid": tid, "addr": addr, "lives": lives})
    for key in ("origin_t", "t_start"):
        try:
            entry[key] = float(frozen_round_meta[key])
            break
        except (KeyError, TypeError, ValueError):
            continue
    return entry


def _build_radius_lookup(
    round_dir: Path,
) -> tuple[list[tuple[float, float, float]], float, list[str]]:
    """bb.json -> ([(lo_t, hi_t, radius_cm)], fallback_radius_cm, limitations)。

    bb.challenges[].window_t 为轮 t 域；bots 半径不一致时取中位并记 limitation。
    """
    limitations: list[str] = []
    bb = _read_json(round_dir / "bb.json")
    windows: list[tuple[float, float, float]] = []
    if not isinstance(bb, dict):
        limitations.append("bb_missing_default_radius")
        return windows, FALLBACK_TARGET_RADIUS_CM, limitations
    all_radii: list[float] = []
    for challenge in bb.get("challenges") or []:
        if not isinstance(challenge, Mapping):
            continue
        window = challenge.get("window_t")
        bots = challenge.get("bots")
        if (
            not isinstance(window, Sequence)
            or isinstance(window, str)
            or len(window) != 2
            or not isinstance(bots, Sequence)
            or not bots
        ):
            continue
        radii: list[float] = []
        for bot in bots:
            try:
                radii.append(float(bot["character"]["bb"]["radius"]))
            except (KeyError, TypeError, ValueError):
                continue
        if not radii:
            continue
        if len(set(radii)) > 1:
            limitations.append("bb_radius_mixed_within_challenge_median")
        radius = float(statistics.median(radii))
        all_radii.append(radius)
        try:
            windows.append((float(window[0]), float(window[1]), radius))
        except (TypeError, ValueError):
            continue
    windows.sort()
    fallback = float(statistics.median(all_radii)) if all_radii else FALLBACK_TARGET_RADIUS_CM
    return windows, fallback, limitations


def _radius_at(windows: Sequence[tuple[float, float, float]], fallback: float, t: float) -> float:
    for lo, hi, radius in windows:
        if lo <= t <= hi:
            return radius
    return fallback


def _interpolate_in_life(
    points: Sequence[tuple[float, float, float, float]],
    life_start: float,
    life_end: float,
    t: float,
) -> tuple[float, float, float] | None:
    """在一条生命窗内的轮帧之间线性插值目标位置；窗外（死亡间隙）返回 None。"""
    lo = bisect.bisect_left(points, (life_start - _LIFE_FRAME_EPSILON_S,))
    hi = bisect.bisect_right(points, (life_end + _LIFE_FRAME_EPSILON_S, math.inf))
    if lo >= hi:
        return None
    index = bisect.bisect_right(points, (t, math.inf, math.inf, math.inf), lo, hi) - 1
    if index < lo:
        first = points[lo]
        return first[1], first[2], first[3]
    if index >= hi - 1:
        last = points[index]
        return last[1], last[2], last[3]
    left, right = points[index], points[index + 1]
    span = right[0] - left[0]
    weight = (t - left[0]) / span if span > 0 else 0.0
    return (
        left[1] + (right[1] - left[1]) * weight,
        left[2] + (right[2] - left[2]) * weight,
        left[3] + (right[3] - left[3]) * weight,
    )


def build_telemetry_visual_result(
    round_dir: Path,
    round_number: int,
    *,
    canonical_window: tuple[float, float],
    fov_fallback: float = 103.0,
    analysis_ref: str | None = None,
    file_names: Mapping[str, str] | None = None,
    frozen_round_meta: Mapping | None = None,
) -> dict:
    """真值旁车目录 -> 与 CV 路径同形的 visual_result（px 域，虚拟 1920x1080）。

    analysis_ref / file_names：Aiming Cookie worker 侧冻结副本入口——
    analysis_ref 绑定到 analysis:{job_id}（family adapter 与证据提交按它校验），
    file_names 显式映射冻结文件名（round.jsonl / views_NN.jsonl / inputs_NN.jsonl）。
    frozen_round_meta：冻结路径的本局身份（入库 meta 的 targets/t_start 透传）。
    传入时不读任何 rounds_index——共享索引是活文件（后续导入会覆盖），(round,
    file) 命中会把新局身份误认成旧局；targets 缺失走整轮兜底，绝不回读共享
    索引。缺省值保持源目录布局与派生 ref，纯源目录消费方不受影响。
    """
    round_dir = Path(round_dir)
    window_start = int(canonical_window[0])
    window_end = int(canonical_window[1])
    if window_start < 0 or window_end <= window_start:
        raise ValueError("canonical_window must be a bounded half-open range")
    if not 1.0 <= fov_fallback <= 179.0:
        raise ValueError("fov_fallback is out of range")
    name_overrides = {
        key: value
        for key, value in (file_names or {}).items()
        if isinstance(value, str) and value
    }
    frames_name = name_overrides.get("round", f"round_{round_number:02d}.jsonl")
    views_name = name_overrides.get("views", f"views_{round_number:02d}.jsonl")
    inputs_name = name_overrides.get("inputs", f"inputs_{round_number:02d}.jsonl")
    # 路径不进 ref（stable ref 禁分隔符），只取目录 basename + 轮号，保证确定性。
    analysis_ref = analysis_ref or f"analysis:telemetry:{round_dir.name}:round:{round_number:02d}"

    limitations: list[str] = []

    def add_limitation(code: str) -> None:
        if code not in limitations:
            limitations.append(code)

    manifest = _read_json(round_dir / "merge_manifest.json")
    if frozen_round_meta is not None:
        # 冻结路径：身份只来自本局 meta，不调用 _load_round_index_entry，也不
        # 读 manifest 声明的共享 rounds_index 绝对路径（活文件，会被新局覆盖）。
        index_entry = _frozen_round_meta_entry(frozen_round_meta)
    else:
        index_entry, index_limitations = _load_round_index_entry(
            round_dir, manifest, round_number,
        )
        limitations.extend(index_limitations)
    radius_windows, fallback_radius, bb_limitations = _build_radius_lookup(round_dir)
    limitations.extend(bb_limitations)

    positions, first_frame_t = _load_round_target_positions(
        round_dir, round_number, frames_name,
    )
    views = _load_views(round_dir, round_number, views_name)
    click_times = _load_click_times(round_dir, round_number, inputs_name)
    if not views:
        raise ValueError("telemetry sidecar views are unavailable")
    if not positions:
        raise ValueError("telemetry sidecar round frames are unavailable")

    # tid -> (addr, lives)。tid 优先取 index_entry（源目录入口=共享索引条目，
    # 冻结入口=本局 meta）的 targets[].tid/addr 映射；无身份时按地址首次
    # 出现顺序去重编号，整轮视作一条生命。
    if index_entry is not None:
        targets: dict[int, dict] = {}
        for target in index_entry.get("targets") or []:
            if not isinstance(target, Mapping):
                continue
            try:
                tid = int(target["tid"])
                addr = int(target["addr"])
            except (KeyError, TypeError, ValueError):
                continue
            lives = []
            for life in target.get("lives") or []:
                try:
                    lives.append((float(life["t_start"]), float(life["t_end"])))
                except (KeyError, TypeError, ValueError):
                    continue
            targets[tid] = {"addr": addr, "lives": lives}
        if not targets and frozen_round_meta is None:
            add_limitation("rounds_index_targets_unreadable_addr_dedup_identity")
    else:
        targets = {}
    if not targets:
        add_limitation("lives_unavailable_whole_round_window")
        next_tid = 0
        for addr, points in sorted(positions.items()):
            if not points:
                continue
            targets[next_tid] = {
                "addr": addr,
                "lives": [(points[0][0], points[-1][0])],
            }
            next_tid += 1
    if not targets:
        raise ValueError("telemetry sidecar targets are unavailable")

    origin_t: float | None = None
    if index_entry is not None:
        # [fix 2026-10-05e] 冻结路径的锚校正 origin_t（worker 用对齐回执锚
        # 换算的帧域局窗起点）优先；源目录入口的 rounds_index 条目只有
        # t_start（无 origin_t），保持现行为。
        for key in ("origin_t", "t_start"):
            try:
                origin_t = float(index_entry[key])
                break
            except (KeyError, TypeError, ValueError):
                continue
    if origin_t is None:
        origin_t = first_frame_t
    if origin_t is None:
        raise ValueError("telemetry sidecar round time origin is unavailable")

    def to_canonical_ms(source_t: float) -> int:
        return window_start + int(round((source_t - origin_t) * 1000.0))

    fovs = [fov for _, _, _, fov in views if fov is not None]
    selector_fov = float(statistics.median(fovs)) if fovs else float(fov_fallback)

    # 采样时钟 = view 帧；目标世界位置在该生命窗内的轮帧间插值。
    crosshair_samples: list[dict] = []
    track_samples: dict[int, list[dict]] = {tid: [] for tid in sorted(targets)}
    view_sample_count = 0
    previous_canonical: int | None = None
    for frame_t, pos, rot, _frame_fov in views:
        canonical_ms = to_canonical_ms(frame_t)
        if not window_start <= canonical_ms < window_end:
            add_limitation("frame_pts_outside_canonical_window")
            continue
        if previous_canonical is not None and canonical_ms <= previous_canonical:
            add_limitation("non_monotonic_frame_pts")
            continue
        if (
            previous_canonical is not None
            and canonical_ms - previous_canonical > _MAX_SAMPLE_GAP_MS
        ):
            add_limitation("visual_frame_gap")
        previous_canonical = canonical_ms
        view_sample_count += 1
        crosshair_samples.append({
            "canonical_time_ms": canonical_ms,
            "x": float(VIEWPORT_WIDTH_PX / 2.0),
            "y": float(VIEWPORT_HEIGHT_PX / 2.0),
            "confidence": 1.0,
        })
        # fov 用全轮稳健值（中位，撕裂帧不逐帧跟随）：同一轮内像素尺度必须一致。
        radius_cm = _radius_at(radius_windows, fallback_radius, frame_t)
        for tid in sorted(targets):
            target = targets[tid]
            world = None
            for life_start, life_end in target["lives"]:
                if life_start <= frame_t <= life_end:
                    world = _interpolate_in_life(
                        positions.get(target["addr"], ()),
                        life_start,
                        life_end,
                        frame_t,
                    )
                    break
            if world is None:
                # 死亡间隙或该 addr 无帧：目标不存在，不产生样本（非遮挡）。
                continue
            if not _is_in_front(world, pos, rot):
                add_limitation("target_behind_camera_skipped")
                continue
            if not _is_in_view_frustum(
                target=world,
                camera_pos=pos,
                rot=rot,
                horizontal_fov_deg=selector_fov,
                target_radius_cm=radius_cm,
            ):
                # 前方但出视锥（tan 爆炸区）：画外样本丢弃不产样本，与死亡
                # 间隙同型；run 级记 limitation，不静默。
                add_limitation("target_outside_frustum_skipped")
                continue
            try:
                px_x, px_y, px_radius = project_world_to_viewport(
                    target=world,
                    camera_pos=pos,
                    rot=rot,
                    horizontal_fov_deg=selector_fov,
                    target_radius_cm=radius_cm,
                )
            except ValueError:
                add_limitation("target_behind_camera_skipped")
                continue
            track_samples[tid].append({
                "canonical_time_ms": canonical_ms,
                "x": px_x,
                "y": px_y,
                "visible_radius": px_radius,
                "confidence": 1.0,
                "measurement_source": "telemetry_world_projection",
            })
    if view_sample_count == 0:
        raise ValueError("telemetry sidecar views fall outside the canonical window")

    # 事件：kill=生命窗 t_end；target_change_point=生命窗出生；shot=L_down 沿。
    # event_bundle.v1 硬上限 512，按 kill > 出生 > shot 的优先级保留（CV 同款
    # limitation 字符串），保证击杀与变点在 mega-round 里尽量不被截断。
    def build_event(kind: str, time_ms: int, actor_refs: list[str], attributes: dict) -> dict:
        return {
            "event_id": "",
            "event_kind": kind,
            "start_ms": time_ms,
            "end_ms": time_ms,
            "actor_refs": actor_refs,
            "source_refs": [analysis_ref],
            "confidence": 1.0,
            "attributes": attributes,
            "limitations": [],
        }

    kill_events: list[dict] = []
    birth_events: list[dict] = []
    for tid in sorted(targets):
        track_ref = f"{analysis_ref}:target-track:{tid}"
        for life_start, life_end in sorted(targets[tid]["lives"]):
            birth_ms = to_canonical_ms(life_start)
            death_ms = to_canonical_ms(life_end)
            if window_start <= birth_ms < window_end:
                birth_events.append(build_event(
                    "target_change_point", birth_ms, [track_ref],
                    {"change_kind": "life_birth"},
                ))
            if window_start <= death_ms < window_end:
                kill_events.append(build_event("kill", death_ms, [track_ref], {}))
    shot_events = [
        build_event("shot", to_canonical_ms(t), [], {})
        for t in click_times
        if window_start <= to_canonical_ms(t) < window_end
    ]
    selected: list[dict] = []
    for group in (kill_events, birth_events, shot_events):
        group.sort(key=lambda event: event["start_ms"])
        selected.extend(group)
    if len(selected) > _EVENT_BUNDLE_LIMIT:
        add_limitation("visual_event_budget_exceeded")
        priority = {id(event): rank for rank, group in enumerate(
            (kill_events, birth_events, shot_events)
        ) for event in group}
        selected.sort(key=lambda event: (event["start_ms"], priority[id(event)]))
        selected = selected[:_EVENT_BUNDLE_LIMIT]
    selected.sort(key=lambda event: event["start_ms"])
    events = []
    for index, event in enumerate(selected, 1):
        event["event_id"] = f"{analysis_ref}:event:telemetry:{index}"
        events.append(event)
    events.sort(key=lambda item: (item["start_ms"], item["event_id"]))

    # ---- 质量与 profile ----
    disable_all = any(code in limitations for code in _DISABLE_ALL_LIMITATIONS)
    quality = {
        "status": "limited" if disable_all or limitations else "accepted",
        "enabled_metric_families": [] if disable_all else list(ALL_METRIC_FAMILIES),
        "limitations": list(limitations),
    }

    scenario_hash = f"external-telemetry:{round_dir.name}"
    selector = {
        "schema_version": "visual_runtime_selector.v1",
        "scenario_hash": scenario_hash,
        "resolution": [VIEWPORT_WIDTH_PX, VIEWPORT_HEIGHT_PX],
        "canonical_video_mapping_version": TELEMETRY_TIME_MAPPING_VERSION,
        "fov": selector_fov,
    }
    profile = build_telemetry_quality_profile_v2(selector=selector)

    # ---- signal_bundle / sample_sets（exact-keys 对齐 analysis_evidence）----
    sample_sets: list[dict] = []
    channels: list[dict] = []

    def add_channel(channel_key: str, points: list[list[float]], coverage: float) -> None:
        sample_ref = f"{analysis_ref}:samples:{channel_key.replace('.', '-')}"
        sample_sets.append({
            "sample_set_id": sample_ref,
            "channel_key": channel_key,
            "unit": "px",
            "points": points,
        })
        channels.append({
            "channel_key": channel_key,
            "source_refs": [analysis_ref],
            "coordinate_space": "capture_coordinates",
            "unit": "px",
            "sample_rate_semantics": "source_pts_irregular",
            "samples_ref": sample_ref,
            "coverage": min(1.0, coverage),
            "confidence_summary": 1.0,
            "transform_version": TELEMETRY_PRODUCER_VERSION,
            "limitations": list(quality["limitations"]),
        })

    add_channel(
        "crosshair.position_x",
        [[s["canonical_time_ms"], s["x"]] for s in crosshair_samples],
        len(crosshair_samples) / view_sample_count,
    )
    add_channel(
        "crosshair.position_y",
        [[s["canonical_time_ms"], s["y"]] for s in crosshair_samples],
        len(crosshair_samples) / view_sample_count,
    )
    track_summaries: list[dict] = []
    ordered_tids = sorted(track_samples)
    for tid in ordered_tids:
        samples = track_samples[tid]
        add_channel(
            f"target.{tid}.position_x",
            [[s["canonical_time_ms"], s["x"]] for s in samples],
            len(samples) / view_sample_count,
        )
        add_channel(
            f"target.{tid}.position_y",
            [[s["canonical_time_ms"], s["y"]] for s in samples],
            len(samples) / view_sample_count,
        )
        add_channel(
            f"target.{tid}.visible_radius",
            [[s["canonical_time_ms"], s["visible_radius"]] for s in samples],
            len(samples) / view_sample_count,
        )
        track_summaries.append({
            "track_ref": f"{analysis_ref}:target-track:{tid}",
            "identity_source": "telemetry_addr_identity",
            "visible_radius_px": (
                float(statistics.median(s["visible_radius"] for s in samples))
                if samples
                else 0.0
            ),
            "sample_count": len(samples),
            "coverage": min(1.0, len(samples) / view_sample_count),
            "limitations": list(bb_limitations),
        })

    signal_bundle = {
        "schema_version": "signal_bundle.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window_ref": f"{analysis_ref}:canonical-window",
        "visual_quality_profile_ref": profile["profile_ref"],
        "observed_visual_domain": selector,
        "channels": channels,
    }
    event_bundle = {
        "schema_version": "event_bundle.v1",
        "analysis_ref": analysis_ref,
        "events": events,
        "outcome_associations": [],
    }
    validate_signal_bundle_v1(signal_bundle)
    validate_event_bundle_v1(event_bundle)

    completeness = (
        "partial"
        if any(code in limitations for code in (
            "visual_frame_gap",
            "frame_pts_outside_canonical_window",
            "non_monotonic_frame_pts",
            "visual_event_budget_exceeded",
        ))
        else "complete"
    )
    window = {
        "schema_version": "canonical_time_window.v1",
        "start_ms": window_start,
        "end_ms": window_end,
        "duration_ms": window_end - window_start,
        "window_semantics": "half_open",
        "timebase_version": TELEMETRY_TIMEBASE_VERSION,
        "start_source": "telemetry_canonical_window_argument",
        "end_source": "telemetry_canonical_window_argument",
        "warnings": [],
    }
    return {
        "schema_version": VISUAL_SIGNAL_SCHEMA_VERSION,
        "analysis_ref": analysis_ref,
        "canonical_time_window": window,
        "video_time_mapping": {
            "schema_version": TELEMETRY_TIME_MAPPING_VERSION,
            "source_pts_origin_ms": origin_t * 1000.0,
            "canonical_origin_ms": window_start,
            "mapping_method": "telemetry_sidecar_t_domain",
            "timebase_version": TELEMETRY_TIMEBASE_VERSION,
        },
        "visual_quality_profile_ref": profile["profile_ref"],
        "visual_runtime_selector": selector,
        "quality": quality,
        "completeness": completeness,
        "track_summaries": track_summaries,
        "signal_bundle": signal_bundle,
        "event_bundle": event_bundle,
        "sample_sets": sample_sets,
        "local_samples": {
            "crosshair.position": crosshair_samples,
            **{
                f"target.{tid}.position": track_samples[tid]
                for tid in ordered_tids
            },
        },
        "safe_summary": {
            "schema_version": "visual_signal_summary.v1",
            "status": "available",
            "producer_version": TELEMETRY_PRODUCER_VERSION,
            "quality_status": quality["status"],
            "enabled_metric_families": list(quality["enabled_metric_families"]),
            "track_count": len(track_summaries),
            "observation_count": view_sample_count,
            "target_coverage": (
                sum(len(track_samples[tid]) for tid in ordered_tids)
                / (view_sample_count * max(len(ordered_tids), 1))
            ),
            "crosshair_coverage": len(crosshair_samples) / view_sample_count,
            "completeness": completeness,
            "event_counts": {
                kind: sum(1 for event in events if event["event_kind"] == kind)
                for kind in sorted({event["event_kind"] for event in events})
            },
            "limitations": list(quality["limitations"]),
        },
        "limitations": list(quality["limitations"]),
    }


def build_telemetry_quality_profile_v2(*, selector: dict) -> dict:
    """v2 形状的最小 visual quality profile（producer_id=external_telemetry）。

    CV 视频检测已退役、世界投影是唯一正源：px 域真值由该投影定义，检测对
    真值的误差按构造为零，无 producer 级降级披露（status=accepted）；单局
    数据质量问题（画外/间隙等）只记运行时 quality 的 limitations，不进
    profile，避免 runtime/profile 口径不一。
    profile_ref 编入量化 fov（四舍五入取整防抖动）：px/rad 尺度随 fov 变化，
    不同 fov 的 run 在 history_trends 的 ref 精确匹配下不可比。
    """
    from .visual_signals import build_visual_quality_profile_v2

    profile = build_visual_quality_profile_v2(
        producer_id=TELEMETRY_PRODUCER_ID,
        producer_version=TELEMETRY_PRODUCER_VERSION,
        annotation_set_ref="annotation-set:external-telemetry-sidecars.v1",
        annotation_protocol_version="telemetry_geometry_protocol.v1",
        coordinate_space="capture_pixels",
        calibration_context={
            "detector_config_ref": "detector-config:telemetry-world-projection.v1",
            "hud_mask_version": None,
            "annotated_map_or_background_labels": ["external-telemetry-sidecar"],
            "annotated_target_appearance_labels": ["world-projected-target"],
        },
        validated_selectors=[selector],
        required_selector_keys_by_metric_family={
            family: [
                "scenario_hash",
                "resolution",
                "canonical_video_mapping_version",
            ]
            for family in ALL_METRIC_FAMILIES
        },
        required_quality_fields_by_metric_family={
            family: [
                "center_error_median_px",
                "center_error_p95_px",
                "radius_or_hitbox_error_px",
                "minimum_coverage",
            ]
            for family in ALL_METRIC_FAMILIES
        },
        compatibility_predicate_version="visual_runtime_compatibility.v2",
        acceptance_thresholds={
            "center_error_median_px": 4.0,
            "center_error_p95_px": 7.0,
            "radius_or_hitbox_error_px": 2.0,
            "false_positive_rate": 0.05,
            "identity_switch_rate": 0.01,
            "occlusion_reentry_accuracy": 0.95,
            "minimum_coverage": 0.95,
        },
        validation_results={
            "center_error_median_px": 0.0,
            "center_error_p95_px": 0.0,
            "radius_or_hitbox_error_px": 0.0,
            "false_positive_rate": 0.0,
            "identity_switch_rate": 0.0,
            "occlusion_reentry_accuracy": 1.0,
            "minimum_coverage": 1.0,
        },
        validated_metric_families=list(ALL_METRIC_FAMILIES),
        status="accepted",
        limitations=[],
    )
    fov = selector.get("fov")
    if isinstance(fov, (int, float)) and not isinstance(fov, bool) and math.isfinite(float(fov)):
        profile["profile_ref"] = f"{profile['profile_ref']}:fov{int(round(float(fov)))}"
    return profile
