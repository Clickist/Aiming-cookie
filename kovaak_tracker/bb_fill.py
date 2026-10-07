"""旁车 bb.json（round_bb.v1）自动化生成：内置半径表 → 本机 .sce → 诚实降级。

"准星×靶子"几何指标（time_in_radius、normalized_click_error）需要靶子真实
半径（cm）。半径真值由 telemetry_signals._build_radius_lookup 从旁车 bb.json
读取；bb 缺失时几何指标诚实降级为 unavailable（FALLBACK_TARGET_RADIUS_CM
只可进通道域，不可作几何依据）。本模块让 worker 在分析时发现 bb 缺失就现场
生成并缓存：

- 内置表：随应用分发的 knowledge/scenarios/bb-radius.v1.json（生成入口
  scripts/export_bb_radius_table.py，用产品自己的 sce_reading 解析器扫描场景
  池），规范化场景名（大小写/空白不敏感）→ 逐出生实例半径；
- 本机 .sce：表未命中时读用户本机场景文件（sce_reading 两级匹配）；
- 双 miss：维持现行诚实降级（不写盘、不抛异常、原因返回给调用方记日志），
  绝不给假数。

round_bb.v1 消费兼容：消费端只读 challenges[].window_t 与
bots[].character.bb.radius（多 bot 半径不一致取中位）。已有 bb.json（含研究
期验证产物）绝不覆盖；写盘原子（tmp + os.replace）。窗口取本局轮帧 t 域的
[t_start, t_end]（清洗器切出的轮范围），保证覆盖全部样本。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import sce_reading

ROUND_BB_SCHEMA_VERSION = "round_bb.v1"
BB_RADIUS_TABLE_SCHEMA_VERSION = "bb_radius_table.v1"

# 内置半径表随应用分发的位置：与 kovaak_tracker.scenario_profiles 的
# knowledge/scenarios 资源根规则同规（AIMING_COOKIE_RESOURCE_ROOT 打包覆盖）。
_RESOURCE_ROOT = os.environ.get("AIMING_COOKIE_RESOURCE_ROOT", "").strip()
RADIUS_TABLE_PATH = (
    Path(_RESOURCE_ROOT) / "knowledge" / "scenarios" / "bb-radius.v1.json"
    if _RESOURCE_ROOT
    else Path(__file__).resolve().parents[1] / "knowledge" / "scenarios" / "bb-radius.v1.json"
)
MAX_TABLE_BYTES = 8 * 1024 * 1024

# 与 sce_reading._norm_name 同规：大小写/空白不敏感的场景名规范化键。
def _normalize_scenario_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def load_radius_table(path: str | Path | None = None) -> dict[str, Any] | None:
    """读内置半径表（fail-open：缺失/超限/损坏/形状不对 → None）。"""
    table_path = Path(path) if path is not None else RADIUS_TABLE_PATH
    try:
        if table_path.stat().st_size > MAX_TABLE_BYTES:
            return None
        data = json.loads(table_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != BB_RADIUS_TABLE_SCHEMA_VERSION:
        return None
    if not isinstance(data.get("entries"), dict):
        return None
    return data


def resolve_target_radii(
    scenario: object,
    *,
    install_dir: str | Any,
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """场景名 → 靶半径（三层）：内置表 → 本机 .sce → unavailable。

    返回 {availability, source, radii_cm, bots, sce_file, sce_path, timescale,
    reason}。availability=unavailable 时 reason 形如
    "table_miss+sce_not_found"（双层都落空的原因链，供日志与报告）。
    """
    if not isinstance(scenario, str) or not scenario.strip():
        return {
            "availability": "unavailable",
            "source": None,
            "radii_cm": [],
            "reason": "invalid_scenario_name",
        }
    payload = table if table is not None else load_radius_table()
    if payload is not None:
        entry = payload["entries"].get(_normalize_scenario_name(scenario))
        if isinstance(entry, dict):
            bots = [b for b in entry.get("bots") or [] if isinstance(b, dict)]
            radii = [
                float(b["radius_cm"])
                for b in bots
                if isinstance(b.get("radius_cm"), (int, float))
            ]
            if radii:
                return {
                    "availability": "available",
                    "source": "builtin-table",
                    "scenario": scenario,
                    "radii_cm": radii,
                    "bots": bots,
                    "sce_file": entry.get("sce_file"),
                    "sce_path": None,
                    "timescale": entry.get("timescale"),
                    "reason": None,
                }
    if install_dir is None:
        reading: dict[str, Any] = {
            "availability": "unavailable",
            "reason": "install_unavailable",
        }
    else:
        reading = sce_reading.resolve_scenario_bounding_radius(
            scenario, install_dir=install_dir,
        )
    if reading.get("availability") == "available":
        return {
            "availability": "available",
            "source": "local-sce",
            "scenario": scenario,
            "radii_cm": [float(r) for r in reading.get("radii_cm") or []],
            "bots": reading.get("bots") or [],
            "sce_file": reading.get("sce_file"),
            "sce_path": reading.get("sce_path"),
            "timescale": reading.get("timescale"),
            "reason": None,
        }
    return {
        "availability": "unavailable",
        "source": None,
        "radii_cm": [],
        "reason": f"table_miss+{reading.get('reason') or 'sce_unavailable'}",
    }


def build_round_bb_document(
    scenario: str,
    resolution: dict[str, Any],
    *,
    round_number: int,
    window_t: tuple[float, float],
) -> dict[str, Any]:
    """解析结果 + 轮窗 → round_bb.v1 文档（单挑战；bots 只含有半径的实例）。"""
    bots = [
        {
            "kind": "bot",
            "profile": bot.get("profile"),
            "character": {
                "name": bot.get("character"),
                "bb": {"type": bot.get("bb_type"), "radius": bot.get("radius_cm")},
            },
        }
        for bot in resolution.get("bots") or []
        if isinstance(bot.get("radius_cm"), (int, float))
    ]
    source = resolution.get("source")
    origin_desc = resolution.get("sce_path") or resolution.get("sce_file") or source
    return {
        "schema_version": ROUND_BB_SCHEMA_VERSION,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "report": f"aiming-cookie bb_fill.v1 ({source}: {origin_desc})",
        "challenges": [
            {
                "scenario": scenario,
                "rounds": [round_number],
                "window_t": [float(window_t[0]), float(window_t[1])],
                "t_domain": "source-relative seconds (= round_NN.jsonl t)",
                "timescale": resolution.get("timescale"),
                "bots": bots,
                "error": None,
                "sce_file": resolution.get("sce_file"),
            },
        ],
    }


def write_round_bb(round_dir: str | Path, document: dict[str, Any]) -> bool:
    """原子写 bb.json；已存在（含研究期验证产物）绝不覆盖。失败不抛。"""
    dst = Path(round_dir) / "bb.json"
    if dst.exists():
        return False
    tmp = dst.with_name("bb.json.tmp")
    try:
        tmp.write_text(
            json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8",
        )
        os.replace(tmp, dst)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def ensure_round_bb(
    round_dir: str | Path,
    scenario: object,
    *,
    install_dir: str | Any,
    round_number: int = 1,
    window_t: tuple[object, object],
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """分析前确保旁车 bb.json 就位（缺则按 表→本机.sce 生成缓存）。

    绝不抛异常、绝不覆盖已有文件；失败返回 status=unavailable/write_failed
    及 reason，调用方记日志后继续正常分析（几何指标按现行语义降级）。
    """
    round_dir = Path(round_dir)
    if (round_dir / "bb.json").exists():
        return {"status": "already_present", "written": False}
    lo, hi = window_t
    if (
        isinstance(lo, bool) or isinstance(hi, bool)
        or not isinstance(lo, (int, float)) or not isinstance(hi, (int, float))
        or hi < lo
    ):
        return {
            "status": "unavailable",
            "written": False,
            "reason": "round_window_unavailable",
        }
    resolution = resolve_target_radii(scenario, install_dir=install_dir, table=table)
    if resolution.get("availability") != "available":
        return {
            "status": "unavailable",
            "written": False,
            "reason": resolution.get("reason"),
        }
    document = build_round_bb_document(
        str(scenario), resolution, round_number=round_number, window_t=(lo, hi),
    )
    if not write_round_bb(round_dir, document):
        return {"status": "write_failed", "written": False, "reason": "bb_write_failed"}
    return {
        "status": "written",
        "written": True,
        "source": resolution.get("source"),
        "scenario": str(scenario),
        "radii_cm": resolution.get("radii_cm"),
        "sce_file": resolution.get("sce_file"),
        "sce_path": resolution.get("sce_path"),
    }


__all__ = [
    "BB_RADIUS_TABLE_SCHEMA_VERSION",
    "RADIUS_TABLE_PATH",
    "ROUND_BB_SCHEMA_VERSION",
    "build_round_bb_document",
    "ensure_round_bb",
    "load_radius_table",
    "resolve_target_radii",
    "write_round_bb",
]
