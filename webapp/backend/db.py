from __future__ import annotations

import json
import sqlite3
from typing import Optional

import aiosqlite

from .config import DB_PATH

_conn: Optional[aiosqlite.Connection] = None

TARGET_USER_VERSION = 15


async def get_conn() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        _conn = await aiosqlite.connect(DB_PATH)
        _conn.row_factory = aiosqlite.Row
        await _conn.execute("PRAGMA foreign_keys=ON")
        await _conn.execute("PRAGMA journal_mode=WAL")
        await _conn.commit()
    return _conn


async def close_conn() -> None:
    global _conn
    if _conn is not None:
        try:
            await _conn.close()
        except Exception:
            pass
        _conn = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'dev',
    status TEXT NOT NULL DEFAULT 'queued',
    video_path TEXT,
    csv_path TEXT,
    cm_per_360 REAL,
    fov REAL,
    analysis_type TEXT NOT NULL DEFAULT 'flicking',
    input_mode TEXT NOT NULL DEFAULT 'video_fallback',
    kovaak_run_id INTEGER,
    input_snapshot_json TEXT,
    result TEXT,
    error TEXT,
    llm_cost_cny REAL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    worker_id TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_status ON sessions(user_id, status);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    trace_json TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, id);

CREATE TABLE IF NOT EXISTS coach_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'primary',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, kind)
);

CREATE TABLE IF NOT EXISTS coach_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    trace_json TEXT,
    legacy_session_id INTEGER,
    legacy_chat_message_id INTEGER,
    context_json TEXT,
    FOREIGN KEY (thread_id) REFERENCES coach_threads(id)
);
CREATE INDEX IF NOT EXISTS idx_coach_messages_thread
    ON coach_messages(thread_id, id);

CREATE TABLE IF NOT EXISTS coach_analysis_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    analysis_session_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    attached_at TEXT DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    FOREIGN KEY (thread_id) REFERENCES coach_threads(id)
);
CREATE INDEX IF NOT EXISTS idx_coach_refs_thread
    ON coach_analysis_refs(thread_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_refs_thread_session_active
    ON coach_analysis_refs(thread_id, analysis_session_id)
    WHERE status = 'active' AND analysis_session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS kovaak_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'desktop-local',
    source_key TEXT NOT NULL,
    scenario TEXT,
    stats_path TEXT,
    performance_path TEXT,
    mouse_trace_path TEXT,
    trace_state TEXT NOT NULL DEFAULT 'none',
    pending_trace_path TEXT,
    trace_error TEXT,
    capture_session_id TEXT,
    window_start_epoch_ms INTEGER,
    window_end_epoch_ms INTEGER,
    alignment_state TEXT NOT NULL DEFAULT 'unresolved',
    alignment_summary TEXT,
    finalization_state TEXT NOT NULL DEFAULT 'discovered',
    finalization_error TEXT,
    video_path TEXT,
    video_state TEXT NOT NULL DEFAULT 'none',
    pending_video_path TEXT,
    video_request_digest TEXT,
    video_receipt_json TEXT,
    video_summary_json TEXT,
    video_error TEXT,
    stats_summary TEXT,
    performance_summary TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, source_key)
);
CREATE INDEX IF NOT EXISTS idx_kovaak_runs_user_created
    ON kovaak_runs(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS benchmark_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_license_note TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    unit TEXT NOT NULL,
    value REAL NOT NULL,
    observed_at TEXT NOT NULL,
    availability TEXT NOT NULL,
    external_identity_ref TEXT,
    identity_consent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_benchmark_records_user_observed
    ON benchmark_records(user_id, observed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS training_plans (
    plan_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft', 'saved', 'active', 'paused')),
    current_version INTEGER NOT NULL CHECK(current_version >= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_training_plans_owner_updated
    ON training_plans(owner_id, updated_at DESC, plan_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_training_plans_one_active_per_owner
    ON training_plans(owner_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS training_plan_versions (
    plan_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    plan_payload_json TEXT NOT NULL,
    adjustment_reason TEXT,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    verification_targets_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(plan_id, version),
    FOREIGN KEY (plan_id) REFERENCES training_plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_training_plan_versions_plan_version
    ON training_plan_versions(plan_id, version DESC);

CREATE TABLE IF NOT EXISTS training_plan_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    event TEXT NOT NULL CHECK(event IN ('generated', 'saved', 'activated', 'paused', 'adjusted')),
    from_status TEXT,
    to_status TEXT NOT NULL CHECK(to_status IN ('draft', 'saved', 'active', 'paused')),
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id, version) REFERENCES training_plan_versions(plan_id, version)
);
CREATE INDEX IF NOT EXISTS idx_training_plan_transitions_owner_plan
    ON training_plan_transitions(owner_id, plan_id, id);

CREATE TABLE IF NOT EXISTS coach_product_commands (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_ref TEXT NOT NULL UNIQUE,
    command_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    thread_id INTEGER,
    user_message_ref TEXT,
    command_name TEXT NOT NULL,
    risk TEXT NOT NULL,
    authorization_source TEXT NOT NULL,
    idempotency_key TEXT,
    parameters_digest TEXT,
    safe_parameters_summary_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    result_ref TEXT,
    ui_event_json TEXT,
    warning_code TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_coach_product_commands_owner_created
    ON coach_product_commands(owner_id, audit_id DESC);

CREATE TABLE IF NOT EXISTS coach_command_idempotency (
    owner_id TEXT NOT NULL,
    command_name TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    parameters_digest TEXT NOT NULL,
    result_json TEXT NOT NULL,
    latest_audit_ref TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(owner_id, command_name, idempotency_key)
);

CREATE TABLE IF NOT EXISTS coach_command_confirmations (
    confirmation_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    command_name TEXT NOT NULL,
    parameters_digest TEXT NOT NULL,
    risk TEXT NOT NULL,
    safe_summary_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'consumed', 'cancelled')),
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_coach_command_confirmations_owner_status
    ON coach_command_confirmations(owner_id, status, created_at DESC);
"""

_V1_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cm_per_360", "REAL"),
    ("fov", "REAL"),
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("max_attempts", "INTEGER NOT NULL DEFAULT 1"),
    ("worker_id", "TEXT"),
    ("lease_expires_at", "TEXT"),
    ("heartbeat_at", "TEXT"),
    ("started_at", "TEXT"),
    ("finished_at", "TEXT"),
)

_V5_KOVAAK_RUN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("trace_state", "TEXT NOT NULL DEFAULT 'none'"),
    ("pending_trace_path", "TEXT"),
    ("trace_error", "TEXT"),
)

_V14_KOVAAK_RUN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("capture_session_id", "TEXT"),
    ("window_start_epoch_ms", "INTEGER"),
    ("window_end_epoch_ms", "INTEGER"),
    ("alignment_state", "TEXT NOT NULL DEFAULT 'unresolved'"),
    ("alignment_summary", "TEXT"),
    ("finalization_state", "TEXT NOT NULL DEFAULT 'discovered'"),
    ("finalization_error", "TEXT"),
    ("video_path", "TEXT"),
    ("video_state", "TEXT NOT NULL DEFAULT 'none'"),
    ("pending_video_path", "TEXT"),
    ("video_request_digest", "TEXT"),
    ("video_receipt_json", "TEXT"),
    ("video_summary_json", "TEXT"),
    ("video_error", "TEXT"),
)

_V6_ANALYSIS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("analysis_type", "TEXT NOT NULL DEFAULT 'flicking'"),
    ("input_mode", "TEXT NOT NULL DEFAULT 'video_fallback'"),
    ("kovaak_run_id", "INTEGER"),
    ("input_snapshot_json", "TEXT"),
)

_V8_COACH_COLUMNS: tuple[tuple[str, str], ...] = (
    ("context_json", "TEXT"),
)

_V9_PROVIDER_PROFILE_TABLE = """
CREATE TABLE IF NOT EXISTS provider_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('builtin', 'custom_openai_compatible')),
    base_url TEXT,
    model_id TEXT NOT NULL,
    api_key TEXT,
    is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_V9_PROVIDER_DEFAULT_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_profiles_owner_default
    ON provider_profiles(owner_id) WHERE is_default = 1
"""

_V10_PROVIDER_CREDENTIALS_TABLE = """
CREATE TABLE IF NOT EXISTS provider_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    profile_id INTEGER NOT NULL UNIQUE,
    credential_type TEXT NOT NULL,
    credential_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    needs_reauth INTEGER NOT NULL DEFAULT 0 CHECK(needs_reauth IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (profile_id) REFERENCES provider_profiles(id) ON DELETE CASCADE
)
"""

_V10_PROVIDER_CREDENTIALS_OWNER_INDEX = """
CREATE INDEX IF NOT EXISTS idx_provider_credentials_owner_profile
    ON provider_credentials(owner_id, profile_id)
"""


_V11_TRAINING_PLAN_TABLES = """
CREATE TABLE IF NOT EXISTS training_plans (
    plan_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft', 'saved', 'active', 'paused')),
    current_version INTEGER NOT NULL CHECK(current_version >= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS training_plan_versions (
    plan_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    plan_payload_json TEXT NOT NULL,
    adjustment_reason TEXT,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    verification_targets_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(plan_id, version),
    FOREIGN KEY (plan_id) REFERENCES training_plans(plan_id)
);
CREATE TABLE IF NOT EXISTS training_plan_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    event TEXT NOT NULL CHECK(event IN ('generated', 'saved', 'activated', 'paused', 'adjusted')),
    from_status TEXT,
    to_status TEXT NOT NULL CHECK(to_status IN ('draft', 'saved', 'active', 'paused')),
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id, version) REFERENCES training_plan_versions(plan_id, version)
);
CREATE INDEX IF NOT EXISTS idx_training_plans_owner_updated
    ON training_plans(owner_id, updated_at DESC, plan_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_training_plans_one_active_per_owner
    ON training_plans(owner_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_training_plan_versions_plan_version
    ON training_plan_versions(plan_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_training_plan_transitions_owner_plan
    ON training_plan_transitions(owner_id, plan_id, id);
"""


_V12_COACH_COMMAND_TABLES = """
CREATE TABLE IF NOT EXISTS coach_product_commands (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_ref TEXT NOT NULL UNIQUE,
    command_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    thread_id INTEGER,
    user_message_ref TEXT,
    command_name TEXT NOT NULL,
    risk TEXT NOT NULL,
    authorization_source TEXT NOT NULL,
    idempotency_key TEXT,
    parameters_digest TEXT,
    safe_parameters_summary_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    result_ref TEXT,
    ui_event_json TEXT,
    warning_code TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS coach_command_confirmations (
    confirmation_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    command_name TEXT NOT NULL,
    parameters_digest TEXT NOT NULL,
    risk TEXT NOT NULL,
    safe_summary_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'consumed', 'cancelled')),
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_coach_product_commands_owner_created
    ON coach_product_commands(owner_id, audit_id DESC);
DROP INDEX IF EXISTS idx_coach_product_commands_idempotency;
CREATE TABLE IF NOT EXISTS coach_command_idempotency (
    owner_id TEXT NOT NULL,
    command_name TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    parameters_digest TEXT NOT NULL,
    result_json TEXT NOT NULL,
    latest_audit_ref TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(owner_id, command_name, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_coach_command_confirmations_owner_status
    ON coach_command_confirmations(owner_id, status, created_at DESC);
"""


_V13_ANALYSIS_DELETION_TOMBSTONES = """
CREATE TABLE IF NOT EXISTS analysis_deletion_tombstones (
    analysis_session_id INTEGER PRIMARY KEY CHECK(analysis_session_id > 0),
    owner_id TEXT NOT NULL CHECK(TRIM(owner_id) <> ''),
    cleanup_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(cleanup_state IN ('pending', 'failed')),
    cleanup_attempts INTEGER NOT NULL DEFAULT 0 CHECK(cleanup_attempts >= 0),
    last_error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(
        (cleanup_state = 'pending' AND cleanup_attempts = 0
            AND last_error_code IS NULL)
        OR
        (cleanup_state = 'failed' AND cleanup_attempts >= 1
            AND last_error_code IS NOT NULL
            AND last_error_code = 'workspace_cleanup_failed')
    )
);
"""


_V15_RUN_EVIDENCE_DELETION_TOMBSTONES = """
CREATE TABLE IF NOT EXISTS run_evidence_deletion_tombstones (
    run_id INTEGER NOT NULL CHECK(run_id > 0),
    evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('video', 'raw')),
    owner_id TEXT NOT NULL CHECK(TRIM(owner_id) <> ''),
    artifact_relpath TEXT NOT NULL CHECK(TRIM(artifact_relpath) <> ''),
    expected_sha256 TEXT NOT NULL CHECK(LENGTH(expected_sha256) = 64),
    expected_size INTEGER NOT NULL CHECK(expected_size >= 0),
    cleanup_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(cleanup_state IN ('pending', 'failed')),
    cleanup_attempts INTEGER NOT NULL DEFAULT 0 CHECK(cleanup_attempts >= 0),
    last_error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(run_id, evidence_kind),
    FOREIGN KEY (run_id) REFERENCES kovaak_runs(id),
    CHECK(
        (cleanup_state = 'pending' AND cleanup_attempts = 0
            AND last_error_code IS NULL)
        OR
        (cleanup_state = 'failed' AND cleanup_attempts >= 1
            AND last_error_code = 'artifact_cleanup_failed')
    )
);
"""


async def init_schema() -> None:
    conn = await get_conn()
    cur = await conn.execute("PRAGMA user_version")
    row = await cur.fetchone()
    user_version = int(row[0]) if row else 0

    if user_version > TARGET_USER_VERSION:
        raise RuntimeError(
            f"数据库 PRAGMA user_version={user_version} 高于本应用支持的 "
            f"version {TARGET_USER_VERSION}；请升级应用，不得静默降级。"
        )

    await conn.executescript(SCHEMA)

    if user_version >= TARGET_USER_VERSION:
        await _migrate_v3_coach_messages_legacy_id(conn)
        await _migrate_v9_provider_profiles(conn)
        await _migrate_v10_provider_credentials(conn)
        await _migrate_v11_training_plans(conn)
        await _migrate_v12_coach_commands(conn)
        await _migrate_v13_analysis_deletion_tombstones(conn)
        await _migrate_v14_kovaak_run_evidence(conn)
        await _migrate_v15_run_evidence_deletion_tombstones(conn)
        await conn.commit()
        return

    await conn.execute("BEGIN IMMEDIATE")
    try:
        if user_version == 0:
            for col, col_def in _V1_MIGRATION_COLUMNS:
                await _migrate_add_column_if_missing(conn, "sessions", col, col_def)
        if user_version < 3:
            await _migrate_v3_coach_messages_legacy_id(conn)
        if user_version < 5:
            for col, col_def in _V5_KOVAAK_RUN_COLUMNS:
                await _migrate_add_column_if_missing(conn, "kovaak_runs", col, col_def)
        if user_version < 6:
            for col, col_def in _V6_ANALYSIS_COLUMNS:
                await _migrate_add_column_if_missing(conn, "sessions", col, col_def)
        if user_version < 8:
            for col, col_def in _V8_COACH_COLUMNS:
                await _migrate_add_column_if_missing(conn, "coach_messages", col, col_def)
        if user_version < 9:
            await _migrate_v9_provider_profiles(conn)
        if user_version < 10:
            await _migrate_v10_provider_credentials(conn)
        if user_version < 11:
            await _migrate_v11_training_plans(conn)
        if user_version < 12:
            await _migrate_v12_coach_commands(conn)
        if user_version < 13:
            await _migrate_v13_analysis_deletion_tombstones(conn)
        if user_version < 14:
            await _migrate_v14_kovaak_run_evidence(conn)
        if user_version < 15:
            await _migrate_v15_run_evidence_deletion_tombstones(conn)
        await conn.execute(f"PRAGMA user_version = {TARGET_USER_VERSION}")
        await conn.commit()
    except Exception:
        await conn.execute("ROLLBACK")
        raise


async def _migrate_v9_provider_profiles(conn: aiosqlite.Connection) -> None:
    """v8 → v9: owner-scoped local Provider profiles and one default per owner."""
    await conn.execute(_V9_PROVIDER_PROFILE_TABLE)
    await conn.execute(_V9_PROVIDER_DEFAULT_INDEX)


async def _migrate_v10_provider_credentials(conn: aiosqlite.Connection) -> None:
    """v9 → v10: completed generic credentials become the only truth source."""
    await conn.execute(_V10_PROVIDER_CREDENTIALS_TABLE)
    await conn.execute(_V10_PROVIDER_CREDENTIALS_OWNER_INDEX)

    cur = await conn.execute(
        "SELECT id, owner_id, api_key FROM provider_profiles "
        "WHERE api_key IS NOT NULL AND TRIM(api_key) <> '' ORDER BY id"
    )
    for row in await cur.fetchall():
        credential = json.dumps(
            {"type": "api_key", "key": row["api_key"]},
            ensure_ascii=False,
            sort_keys=True,
        )
        await conn.execute(
            "INSERT INTO provider_credentials(owner_id, profile_id, credential_type, "
            "credential_json, revision, needs_reauth) VALUES(?, ?, 'api_key', ?, 1, 0) "
            "ON CONFLICT(profile_id) DO NOTHING",
            (row["owner_id"], row["id"], credential),
        )

    # Compatibility column remains in place for older code/readers, but must
    # never remain a second credential source after backfill.
    await conn.execute("UPDATE provider_profiles SET api_key=NULL WHERE api_key IS NOT NULL")


async def _migrate_v11_training_plans(conn: aiosqlite.Connection) -> None:
    """v10 → v11: immutable owner-scoped Training Plan persistence."""
    await _execute_transactional_script(conn, _V11_TRAINING_PLAN_TABLES)


async def _migrate_v12_coach_commands(conn: aiosqlite.Connection) -> None:
    """v11 → v12: persistent Coach command audit, idempotency and confirmations."""
    await _execute_transactional_script(conn, _V12_COACH_COMMAND_TABLES)


async def _migrate_v13_analysis_deletion_tombstones(
    conn: aiosqlite.Connection,
) -> None:
    """v12 → v13: transient Analysis workspace cleanup tombstones."""
    await _execute_transactional_script(conn, _V13_ANALYSIS_DELETION_TOMBSTONES)


async def _migrate_v14_kovaak_run_evidence(conn: aiosqlite.Connection) -> None:
    """v13 -> v14: Run-owned capture, finalization, and video evidence state."""
    for column, definition in _V14_KOVAAK_RUN_COLUMNS:
        await _migrate_add_column_if_missing(
            conn,
            "kovaak_runs",
            column,
            definition,
        )


async def _migrate_v15_run_evidence_deletion_tombstones(
    conn: aiosqlite.Connection,
) -> None:
    """v14 -> v15: recoverable removal of one Run-owned evidence artifact."""
    await _execute_transactional_script(
        conn, _V15_RUN_EVIDENCE_DELETION_TOMBSTONES,
    )


async def _execute_transactional_script(
    conn: aiosqlite.Connection,
    script: str,
) -> None:
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if not sqlite3.complete_statement(statement):
            continue
        sql = statement.strip()
        statement = ""
        if sql:
            await conn.execute(sql)
    if statement.strip():
        raise ValueError("incomplete migration SQL statement")


async def _migrate_add_column_if_missing(
    conn: aiosqlite.Connection, table: str, col: str, col_def: str,
) -> None:
    """SQLite ALTER TABLE ADD COLUMN(仅当列不存在时)。"""
    assert table.isidentifier(), f"非法 table 名: {table}"
    assert col.isidentifier(), f"非法 col 名: {col}"
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    existing = {row[1] for row in rows}
    if col not in existing:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")


async def _migrate_v3_coach_messages_legacy_id(conn: aiosqlite.Connection) -> None:
    """v2 → v3: legacy_chat_message_id + partial unique index (idempotent)."""
    await _migrate_add_column_if_missing(
        conn, "coach_messages", "legacy_chat_message_id", "INTEGER",
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_messages_legacy_chat_message_id "
        "ON coach_messages(legacy_chat_message_id) "
        "WHERE legacy_chat_message_id IS NOT NULL"
    )


async def save_chat_message(
    session_id: int,
    role: str,
    content: str,
    trace: Optional[list] = None,
) -> int:
    """Append one chat message. ``trace`` is the agent loop's trace list
    (one entry per tool call) — JSON-serialized for observability."""
    conn = await get_conn()
    trace_json = json.dumps(trace, ensure_ascii=False) if trace else None
    cur = await conn.execute(
        "INSERT INTO chat_messages(session_id, role, content, trace_json) "
        "VALUES(?, ?, ?, ?) RETURNING id",
        (session_id, role, content, trace_json),
    )
    row = await cur.fetchone()
    await conn.commit()
    return row["id"]


async def load_chat_history(session_id: int) -> list[dict]:
    """Load chat history ordered by id (chronological).

    Returns list of ``{role, content, created_at, trace}`` dicts. ``trace``
    is parsed from ``trace_json`` when present (else empty list).
    """
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT role, content, created_at, trace_json FROM chat_messages "
        "WHERE session_id=? ORDER BY id",
        (session_id,),
    )
    rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        trace: list = []
        if r["trace_json"]:
            try:
                trace = json.loads(r["trace_json"])
            except (json.JSONDecodeError, TypeError):
                trace = []
        out.append({
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"],
            "trace": trace,
        })
    return out
