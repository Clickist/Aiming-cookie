"""Post-migration rewrite of absolute paths stored inside the legacy database.

存储位置迁移（壳侧 storage_location.rs）搬走整个数据根后，历史 SQLite 库
``aiming_cookie.db`` 里以绝对路径落盘的列（如 ``sessions.video_path``、
``kovaak_runs.video_path`` / ``mouse_trace_path``）仍指向旧根，媒体/轨迹引用
全部断链（2026-09-29 真机：40 行）。这里在 Python 后端启动时做一次性、幂等
的整库重写：

- 依据迁移记录 ``storage-migration.json``（sourceRoot/targetRoot/phase）。该
  记录由壳写入并**始终留在默认数据根**（与指针同处），而子进程 ``DATA_ROOT``
  是生效根——自定义位置场景下两者不同，因此候选路径同时探测 DATA_ROOT 与
  平台默认根（Tauri ``app_data_dir`` = ``<平台数据目录>/<identifier>``）。
- 仅当 ``phase == "done"`` 且尚未写过 ``path_rewrite_done`` 标志时执行；
  成功后把标志写回记录，之后启动零开销跳过。
- 对所有用户表的所有 TEXT 列做前缀命中更新，同时处理反斜杠与正斜杠两种
  形态；仅当整值等于旧根、或以「旧根 + 路径分隔符」开头才更新，避免误伤
  共享字符串前缀的同级目录（如 ``D:\\OldRootBackup``）。
- 记录里的根可能带 Windows 扩展前缀（``\\\\?\\\\E:\\Data`` / ``\\\\?\\\\UNC\\...``，
  旧版本 canonicalize 泄漏），重写前先剥成普通形态（与壳侧
  ``strip_extended_prefix`` 同语义，纯字符串处理）。
- 全程 fail-soft：任何异常只进日志，绝不阻塞启动。

会话 JSON 重写（2026-10-02）：会话存储 0813 起是 ``sessions/*.json`` 文件，
``video_path`` / ``trace.path`` / ``external_telemetry.frames_path`` 等字段
同样落着旧根绝对路径，db 重写覆盖不到它们——0928 迁移后全部老会话的视频
挂载断链（``visual_replay_capability`` 判 unavailable，前端显示「本档分析
基于输入数据」）。``rewrite_session_json_paths_for_migration`` 用同一记录、
同一前缀守卫递归重写会话文件里的字符串值，标志是独立的
``session_json_rewrite_done``（不复用 db 标志，已跑过 db 重写的存量迁移
机器也必须能拿到这次修复）；单个文件损坏只跳过不挡批次。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

MIGRATION_FILE_NAME = "storage-migration.json"
DB_FILE_NAME = "aiming_cookie.db"
SESSIONS_DIR_NAME = "sessions"
# 必须与 webapp/frontend/src-tauri/tauri.conf.json 的 identifier 保持一致；
# 默认根由壳的 Tauri app_data_dir 决定，Python 侧只能按同规则推导。
TAURI_APP_IDENTIFIER = "com.aimingcookie.desktop"

MIGRATION_PHASE_DONE = "done"
# db 重写与会话 JSON 重写各自的一次性标志：分开才能让已跑过 db 重写的存量
# 迁移机器（path_rewrite_done 已置位）仍被会话重写兜住。
DB_REWRITE_FLAG = "path_rewrite_done"
SESSION_JSON_REWRITE_FLAG = "session_json_rewrite_done"
# 会话目录下以下划线开头的元数据文件（_counter.json、_deletion_tombstones.json），
# 不是会话记录，不参与重写。
_SESSION_META_PREFIX = "_"
# 迁移记录写回失败时的重试（设置页轮询并发读可能让 Windows 原子替换瞬时冲突，
# 与壳侧 MIGRATION_WRITE_ATTEMPTS 同思路）。
_FLAG_WRITE_ATTEMPTS = 3
_FLAG_WRITE_RETRY_SECONDS = 0.02

# 重写结果状态（只进日志/测试断言，不构成对外契约）。
STATUS_REWRITTEN = "rewritten"
STATUS_NOOP_IDENTICAL_ROOTS = "noop-identical-roots"
STATUS_SKIPPED_NO_RECORD = "skipped-no-record"
STATUS_SKIPPED_PHASE = "skipped-phase"
STATUS_SKIPPED_FLAG_SET = "skipped-flag-set"
STATUS_SKIPPED_DB_MISSING = "skipped-db-missing"
STATUS_SKIPPED_SESSIONS_MISSING = "skipped-sessions-missing"
STATUS_FAILED = "failed"


def strip_extended_prefix(value: str) -> str:
    """剥掉 Windows 扩展路径前缀，语义对齐壳侧 strip_extended_prefix。

    ``\\\\?\\E:\\Data`` → ``E:\\Data``，``\\\\?\\UNC\\server\\share`` →
    ``\\\\server\\share``，其余形态原样返回。剥完超过 260 字符（MAX_PATH）的
    路径保留前缀——此时前缀是功能性的（Win32 处理不了超长路径）。
    """
    if value.startswith("\\\\?\\UNC\\"):
        stripped = "\\\\" + value[len("\\\\?\\UNC\\"):]
    elif value.startswith("\\\\?\\"):
        stripped = value[len("\\\\?\\"):]
    else:
        return value
    if len(stripped) > 260:
        return value
    return stripped


def _platform_default_data_root() -> Path | None:
    """推导壳的默认数据根（Tauri app_data_dir = dirs::data_dir()/identifier）。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        return Path(base) / TAURI_APP_IDENTIFIER if base else None
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / TAURI_APP_IDENTIFIER
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / TAURI_APP_IDENTIFIER


def _candidate_migration_paths(data_root: Path) -> list[Path]:
    """迁移记录的候选路径：生效根优先（无自定义位置时两者同处），默认根兜底。"""
    candidates = [data_root / MIGRATION_FILE_NAME]
    default_root = _platform_default_data_root()
    if default_root is not None and default_root != data_root:
        candidates.append(default_root / MIGRATION_FILE_NAME)
    return candidates


def _read_migration_record(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _write_flag_done(path: Path, record: dict, flag: str = DB_REWRITE_FLAG) -> bool:
    """把重写标志原子写回迁移记录，保留其余字段。"""
    payload = json.dumps({**record, flag: True}, ensure_ascii=False, indent=2)
    tmp_path = path.parent / (path.name + ".tmp")
    for attempt in range(_FLAG_WRITE_ATTEMPTS):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(payload + "\n", encoding="utf-8")
            os.replace(tmp_path, path)
            return True
        except OSError:
            if attempt + 1 < _FLAG_WRITE_ATTEMPTS:
                time.sleep(_FLAG_WRITE_RETRY_SECONDS)
    log.warning("storage path rewrite: failed to persist path_rewrite_done flag into %s", path)
    return False


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _replacement_pairs(source_root: str, target_root: str) -> list[tuple[str, str, str]]:
    """生成 (旧前缀, 新前缀, LIKE 前缀模式) 三元组，覆盖反斜杠与正斜杠两形态。"""
    pairs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for separator in ("\\", "/"):
        source = source_root.replace("\\", separator).replace("/", separator)
        target = target_root.replace("\\", separator).replace("/", separator)
        if (source, target) in seen or source == target:
            continue
        seen.add((source, target))
        pattern = _like_escape(source) + _like_escape(separator) + "%"
        pairs.append((source, target, pattern))
    return pairs


def _user_text_columns(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """所有用户表（排除 sqlite_* 内部表）的所有 TEXT 声明列。"""
    tables = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
    ).fetchall()
    columns: list[tuple[str, str]] = []
    for (table,) in tables:
        table_sql = '"' + table.replace('"', '""') + '"'
        for row in con.execute(f"PRAGMA table_info({table_sql})"):
            name, declared_type = row[1], row[2]
            if declared_type and "TEXT" in str(declared_type).upper():
                columns.append((table, name))
    return columns


def rewrite_paths_for_migration(data_root: Path, migration_path: Path | None = None) -> str:
    """按迁移记录一次性重写库内绝对路径。返回状态字符串，绝不抛异常。"""
    try:
        return _rewrite_paths_for_migration(data_root, migration_path)
    except Exception:
        log.exception("storage path rewrite crashed (non-fatal)")
        return STATUS_FAILED


def _rewrite_paths_for_migration(data_root: Path, migration_path: Path | None) -> str:
    candidates = [migration_path] if migration_path is not None else _candidate_migration_paths(data_root)
    record_path = next((p for p in candidates if p.is_file()), None)
    if record_path is None:
        return STATUS_SKIPPED_NO_RECORD
    record = _read_migration_record(record_path)
    if record is None:
        log.warning("storage path rewrite: unreadable migration record %s", record_path)
        return STATUS_SKIPPED_NO_RECORD
    if record.get(DB_REWRITE_FLAG):
        return STATUS_SKIPPED_FLAG_SET
    if record.get("phase") != MIGRATION_PHASE_DONE:
        return STATUS_SKIPPED_PHASE
    source_root = record.get("sourceRoot")
    target_root = record.get("targetRoot")
    if not isinstance(source_root, str) or not isinstance(target_root, str) or not source_root or not target_root:
        log.warning("storage path rewrite: migration record %s lacks usable roots", record_path)
        return STATUS_SKIPPED_NO_RECORD
    source_root = strip_extended_prefix(source_root)
    target_root = strip_extended_prefix(target_root)
    if os.path.normcase(os.path.normpath(source_root)) == os.path.normcase(os.path.normpath(target_root)):
        # 未真正换根（迁移回默认位置等）：no-op，但仍落标志避免每次启动扫描。
        _write_flag_done(record_path, record)
        return STATUS_NOOP_IDENTICAL_ROOTS

    db_path = data_root / DB_FILE_NAME
    if not db_path.is_file():
        # 库不在生效根时不落标志：等库真正就位的那次启动再重写。
        return STATUS_SKIPPED_DB_MISSING

    pairs = _replacement_pairs(source_root, target_root)
    changed = 0
    con = sqlite3.connect(db_path, timeout=3.0)
    try:
        # 单事务：任一语句失败整体回滚，库不会被写成半旧半新。
        with con:
            for table, column in _user_text_columns(con):
                column_sql = '"' + column.replace('"', '""') + '"'
                table_sql = '"' + table.replace('"', '""') + '"'
                for source, target, pattern in pairs:
                    cursor = con.execute(
                        f"UPDATE {table_sql} SET {column_sql} = REPLACE({column_sql}, ?, ?) "
                        f"WHERE {column_sql} = ? OR {column_sql} LIKE ? ESCAPE '\\'",
                        (source, target, source, pattern),
                    )
                    changed += max(cursor.rowcount, 0)
    finally:
        con.close()
    _write_flag_done(record_path, record)
    log.info(
        "storage path rewrite: %s via %s (%d rows updated, %d column forms)",
        db_path, record_path, changed, len(pairs),
    )
    return STATUS_REWRITTEN


# ── 会话 JSON 重写 ──────────────────────────────────────────────────────────

def _rewrite_path_string(value: str, pairs: list[tuple[str, str]]) -> str:
    """前缀命中重写：整值等于旧根，或以「旧根 + 分隔符」开头才算命中，
    与 db 重写器的 WHERE 守卫同语义（不误伤同级前缀目录）。"""
    for source, target in pairs:
        if value == source:
            return target
        if value.startswith(source + "\\") or value.startswith(source + "/"):
            return target + value[len(source):]
    return value


def _rewrite_json_strings(value: object, pairs: list[tuple[str, str]]) -> int:
    """原位递归重写 JSON 结构里命中旧根前缀的字符串值，返回改写条数。"""
    if isinstance(value, dict):
        changed = 0
        for key, child in value.items():
            if isinstance(child, str):
                rewritten = _rewrite_path_string(child, pairs)
                if rewritten != child:
                    value[key] = rewritten
                    changed += 1
            else:
                changed += _rewrite_json_strings(child, pairs)
        return changed
    if isinstance(value, list):
        changed = 0
        for index, child in enumerate(value):
            if isinstance(child, str):
                rewritten = _rewrite_path_string(child, pairs)
                if rewritten != child:
                    value[index] = rewritten
                    changed += 1
            else:
                changed += _rewrite_json_strings(child, pairs)
        return changed
    return 0


def rewrite_session_json_paths_for_migration(
    data_root: Path,
    migration_path: Path | None = None,
) -> str:
    """按迁移记录一次性重写 sessions/*.json 内的旧根绝对路径。绝不抛异常。

    与 db 重写器同一条记录、同一前缀守卫，但标志独立
    （session_json_rewrite_done）：会话文件不在 db 重写的覆盖范围内，
    0928 迁移后全部老会话的视频挂载因此断链。单文件损坏只跳过并告警，
    不挡其余文件；写回与 file_store.write_json 同格式（indent=2、原子替换）。
    """
    try:
        return _rewrite_session_json_paths_for_migration(data_root, migration_path)
    except Exception:
        log.exception("session json path rewrite crashed (non-fatal)")
        return STATUS_FAILED


def _rewrite_session_json_paths_for_migration(
    data_root: Path,
    migration_path: Path | None,
) -> str:
    candidates = [migration_path] if migration_path is not None else _candidate_migration_paths(data_root)
    record_path = next((p for p in candidates if p.is_file()), None)
    if record_path is None:
        return STATUS_SKIPPED_NO_RECORD
    record = _read_migration_record(record_path)
    if record is None:
        log.warning("session json path rewrite: unreadable migration record %s", record_path)
        return STATUS_SKIPPED_NO_RECORD
    if record.get(SESSION_JSON_REWRITE_FLAG):
        return STATUS_SKIPPED_FLAG_SET
    if record.get("phase") != MIGRATION_PHASE_DONE:
        return STATUS_SKIPPED_PHASE
    source_root = record.get("sourceRoot")
    target_root = record.get("targetRoot")
    if not isinstance(source_root, str) or not isinstance(target_root, str) or not source_root or not target_root:
        log.warning("session json path rewrite: migration record %s lacks usable roots", record_path)
        return STATUS_SKIPPED_NO_RECORD
    source_root = strip_extended_prefix(source_root)
    target_root = strip_extended_prefix(target_root)
    if os.path.normcase(os.path.normpath(source_root)) == os.path.normcase(os.path.normpath(target_root)):
        _write_flag_done(record_path, record, SESSION_JSON_REWRITE_FLAG)
        return STATUS_NOOP_IDENTICAL_ROOTS

    sessions_dir = data_root / SESSIONS_DIR_NAME
    if not sessions_dir.is_dir():
        # 会话目录不在生效根时不落标志：等目录真正就位的那次启动再重写。
        return STATUS_SKIPPED_SESSIONS_MISSING

    # 复用 db 重写器的双分隔符形态对（第三元 LIKE 模式只服务 SQL，此处不用）。
    pairs = [(source, target) for source, target, _ in _replacement_pairs(source_root, target_root)]
    changed_files = 0
    changed_values = 0
    for path in sorted(sessions_dir.glob("*.json")):
        if path.name.startswith(_SESSION_META_PREFIX):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            # 单文件损坏不是本次修复的职责：跳过并告警，不挡其余会话。
            log.warning("session json path rewrite: skipping unreadable %s", path)
            continue
        changed = _rewrite_json_strings(payload, pairs)
        if changed == 0:
            continue
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
        changed_files += 1
        changed_values += changed
    _write_flag_done(record_path, record, SESSION_JSON_REWRITE_FLAG)
    log.info(
        "session json path rewrite: %s via %s (%d files, %d values updated)",
        sessions_dir, record_path, changed_files, changed_values,
    )
    return STATUS_REWRITTEN


def run_startup_path_rewrite() -> str | None:
    """启动钩子：fail-soft 包装，任何异常只进日志，绝不阻塞启动。"""
    try:
        from . import config  # 延迟导入：核心函数可被测试独立使用，不触发 config 副作用

        statuses = {
            "db": rewrite_paths_for_migration(config.DATA_ROOT),
            "session_json": rewrite_session_json_paths_for_migration(config.DATA_ROOT),
        }
        summary = "; ".join(
            f"{name}={status}" for name, status in statuses.items()
            if status != STATUS_SKIPPED_NO_RECORD
        )
        return summary or None
    except Exception:
        log.exception("storage path rewrite failed (non-fatal)")
        return None
