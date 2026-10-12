"""Analysis artifacts 总量上限自动清理（[ac P0-5 2026-10-11] 点点拍板 Steam 方案）.

三层清理机制的层 2 + 层 3：

- 层 2（总量上限）：分析产物目录（``{DATA_ROOT}/sessions/<id>/`` 工作区）总
  占用默认上限 **10GB**；超限从最旧的分析产物开始删，直到回落到上限或没有
  可删产物。用户可在设置调整上限或整体关闭。触发点：应用启动对账一次 +
  设置保存即刻对账（不挂在分析完成路径上——后台任务与测试/关停生命周期
  竞态的教训，保持触发点可预测）。
- 层 3（明示留痕）：自动清理绝不静默——设置里可见可关；每次触发把「删了哪
  些、各释放多少」写日志并落 ``diagnostics/analysis-cleanup.json`` 快照供
  设置页展示。

红线（PRD :285 / ARCHITECTURE :242 合同）：上限清理**只删 Analysis-owned
workspace**，绝不触碰 Run 录像 MP4、Raw trace 等用户证据——那些只有用户
手动动作（按条目移除 / 删训练记录）才能动。进行中（uploading/queued/
running）的分析工作区永不清理。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, file_store
from .workspace import remove_session_workspace, workspace_size_bytes

log = logging.getLogger(__name__)

_SETTINGS_PATH = "config/analysis-cleanup.json"
_LAST_RUN_PATH = "diagnostics/analysis-cleanup.json"

SETTINGS_SCHEMA_VERSION = "analysis_cleanup_settings.v1"
LAST_RUN_SCHEMA_VERSION = "analysis_cleanup_run.v1"

# 默认 10GB（点点拍板）；可调边界 1GB–1TB——下限防用户误设 0 变相全删，
# 上限防 int 溢出式荒谬值。
DEFAULT_LIMIT_BYTES = 10 * 1024**3
MIN_LIMIT_BYTES = 1 * 1024**3
MAX_LIMIT_BYTES = 1 * 1024**3 * 1024

# 只清已知终态；未知状态保守跳过（宁可留待下次，不误删未知生命周期）。
_TERMINAL_STATUSES = frozenset({"done", "failed"})
_ACTIVE_STATUSES = frozenset({"uploading", "queued", "running"})


def get_cleanup_settings() -> dict[str, object]:
    """读取清理设置；缺失/损坏回默认（enabled=True，10GB）——默认开启是
    拍板语义（无界增长是审计伤口），损坏配置不能让上限失效。"""
    try:
        data = file_store.read_json(_SETTINGS_PATH)
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        return {"enabled": True, "limit_bytes": DEFAULT_LIMIT_BYTES}
    enabled = data.get("enabled")
    limit = data.get("limit_bytes")
    return {
        "enabled": True if not isinstance(enabled, bool) else enabled,
        "limit_bytes": (
            limit
            if isinstance(limit, int) and not isinstance(limit, bool)
            and MIN_LIMIT_BYTES <= limit <= MAX_LIMIT_BYTES
            else DEFAULT_LIMIT_BYTES
        ),
    }


def save_cleanup_settings(*, enabled: bool, limit_bytes: int) -> dict[str, object]:
    """校验并持久化清理设置；返回规范化的设置（供响应投影）。"""
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    if (
        not isinstance(limit_bytes, int)
        or isinstance(limit_bytes, bool)
        or not MIN_LIMIT_BYTES <= limit_bytes <= MAX_LIMIT_BYTES
    ):
        raise ValueError(
            f"limit_bytes must be an int between {MIN_LIMIT_BYTES} and {MAX_LIMIT_BYTES}"
        )
    file_store.write_json(_SETTINGS_PATH, {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "enabled": enabled,
        "limit_bytes": limit_bytes,
    })
    return {"enabled": enabled, "limit_bytes": limit_bytes}


def _session_record(session_id: int) -> dict | None:
    try:
        record = file_store.read_json(f"sessions/{session_id}.json")
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _workspace_candidates() -> list[dict[str, object]]:
    """扫描 sessions 根，产出可清理候选（终态工作区 + 孤立工作区）。

    活动工作区不在候选里；未知状态同样保守跳过。孤立工作区（记录已不存在）
    是级联/对账失败的遗留物，无用户可见入口，按无主产物参与最旧优先。
    """
    candidates: list[dict[str, object]] = []
    sessions_root = Path(config.DATA_ROOT) / "sessions"
    try:
        entries = list(sessions_root.iterdir())
    except OSError:
        return candidates
    for directory in entries:
        if not directory.is_dir():
            continue
        try:
            session_id = int(directory.name)
        except ValueError:
            continue
        record = _session_record(session_id)
        if record is None:
            try:
                timestamp = directory.stat().st_mtime
            except OSError:
                continue
            candidates.append({
                "session_id": session_id,
                "orphan": True,
                "status": None,
                "created_at": timestamp,
            })
            continue
        status = record.get("status")
        if status not in _TERMINAL_STATUSES:
            # 活动与未知状态一律跳过（含 active 集合之外的未知值）。
            continue
        created_at = record.get("created_at")
        candidates.append({
            "session_id": session_id,
            "orphan": False,
            "status": status,
            "created_at": _parse_created_at(created_at),
        })
    return candidates


def _parse_created_at(raw: object) -> float:
    """session created_at（ISO 字符串）→ epoch 秒；解析失败回退 0（按最旧处理）。"""
    if not isinstance(raw, str) or not raw:
        return 0.0
    try:
        text = raw.strip().rstrip("Z").replace("T", " ")
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        return parsed.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def enforce_analysis_artifacts_cap(
    *, settings: dict[str, object] | None = None,
) -> dict[str, object]:
    """执行总量上限清理（同步、纯本地 IO；调用方决定放线程还是事件循环）。

    返回 last-run 快照（与落盘的 ``diagnostics/analysis-cleanup.json`` 同构）。
    关闭时直接返回不落盘（保留上次触发的快照供设置页展示）。
    """
    resolved = settings if settings is not None else get_cleanup_settings()
    enabled = resolved.get("enabled") is True
    limit = resolved.get("limit_bytes")
    limit_bytes = limit if isinstance(limit, int) else DEFAULT_LIMIT_BYTES
    if not enabled:
        return {"triggered": False, "reason": "disabled"}

    candidates = _workspace_candidates()
    sessions_root = Path(config.DATA_ROOT) / "sessions"
    sizes: dict[int, int] = {}
    total = 0
    active_guard: set[int] = set()
    if sessions_root.is_dir():
        for directory in sessions_root.iterdir():
            if not directory.is_dir():
                continue
            try:
                session_id = int(directory.name)
            except ValueError:
                continue
            record = _session_record(session_id)
            if record is not None and (record.get("status") or "") in _ACTIVE_STATUSES:
                active_guard.add(session_id)
            try:
                size = workspace_size_bytes(session_id)
            except OSError:
                size = 0
            sizes[session_id] = size
            total += size

    removed: list[dict[str, object]] = []
    skipped_active = 0
    error: str | None = None
    candidates.sort(key=lambda item: (float(item["created_at"]), int(item["session_id"])))
    for candidate in candidates:
        if total <= limit_bytes:
            break
        session_id = int(candidate["session_id"])
        # 移除前重查记录（防扫描间隙并发出现的新分析被误删）：记录存在且非
        # 终态 → 跳过；记录缺 → 按扫描时的孤立候选清理。
        record = _session_record(session_id)
        if record is not None and (record.get("status") or "") not in _TERMINAL_STATUSES:
            skipped_active += 1
            continue
        size = sizes.get(session_id, 0)
        try:
            removed_workspace = remove_session_workspace(session_id)
        except OSError as exc:
            error = f"remove_failed: session={session_id}: {exc}"
            log.warning("analysis artifacts cap removal failed session=%s: %s", session_id, exc)
            continue
        if removed_workspace:
            total -= size
            removed.append({
                "session_id": session_id,
                "orphan": bool(candidate["orphan"]),
                "bytes": size,
            })
            # 层 3 留痕：每删一个产物一条日志（删了哪个、释放多少）。
            log.info(
                "analysis artifacts cap removed session=%s orphan=%s bytes=%d "
                "total_before_remaining=%d limit=%d",
                session_id, bool(candidate["orphan"]), size, total, limit_bytes,
            )

    snapshot: dict[str, object] = {
        "schema_version": LAST_RUN_SCHEMA_VERSION,
        "finished_epoch_s": time.time(),
        "triggered": bool(removed),
        "enabled": enabled,
        "limit_bytes": limit_bytes,
        "total_after_bytes": total,
        "removed": removed,
        # 进行中的工作区不在可清理集里（红线）；这里报数让「为什么不删」可回答。
        "skipped_active": len(active_guard) + skipped_active,
        "error": error,
    }
    try:
        file_store.write_json(_LAST_RUN_PATH, snapshot)
    except OSError:
        log.exception("analysis cleanup snapshot write failed")
    if removed:
        log.info(
            "analysis artifacts cap enforcement done removed=%d reclaimed=%d "
            "total_after=%d limit=%d",
            len(removed), sum(int(item["bytes"]) for item in removed),
            total, limit_bytes,
        )
    return snapshot


def get_last_run() -> dict[str, object] | None:
    try:
        data = file_store.read_json(_LAST_RUN_PATH)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
