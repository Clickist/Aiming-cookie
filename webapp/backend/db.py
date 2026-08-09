from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Awaitable, Callable, Optional, TypeVar

import aiosqlite

from .config import DB_PATH

_Result = TypeVar("_Result")


class _TransactionGate:
    """Serialize access while one task owns the shared SQLite transaction."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()
        self._owner: Optional[asyncio.Task] = None

    async def run(self, operation: Callable[[], Awaitable[_Result]]) -> _Result:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("database access requires an asyncio task")
        acquired = False
        if self._owner is not task:
            await self._lock.acquire()
            acquired = True
            self._owner = task
            task.add_done_callback(self._rollback_abandoned_transaction)
        try:
            result = await operation()
        except BaseException:
            if acquired and not self._conn.in_transaction:
                self._release(task)
            raise
        if self._owner is task and not self._conn.in_transaction:
            self._release(task)
        return result

    def _rollback_abandoned_transaction(self, task: asyncio.Task) -> None:
        if self._owner is task and not task.get_loop().is_closed():
            task.get_loop().create_task(self._finish_abandoned_transaction(task))

    async def _finish_abandoned_transaction(self, task: asyncio.Task) -> None:
        if self._owner is not task:
            return
        try:
            try:
                if self._conn.in_transaction:
                    await self._conn.rollback()
            except ValueError:
                # Fixture/runtime shutdown may close the raw connection first.
                pass
        finally:
            self._release(task)

    def _release(self, task: asyncio.Task) -> None:
        if self._owner is task:
            self._owner = None
            self._lock.release()

    async def close(self) -> None:
        if self._conn.in_transaction:
            await self._conn.rollback()
        await self._conn.close()
        if self._owner is not None:
            self._owner = None
            if self._lock.locked():
                self._lock.release()


class _GatedConnection:
    """Expose the existing aiosqlite API with shared transaction ownership."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._gate = _TransactionGate(conn)

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        self._conn.row_factory = value

    @property
    def in_transaction(self) -> bool:
        return self._conn.in_transaction

    async def execute(self, sql: str, parameters=None):
        return await self._gate.run(lambda: self._conn.execute(sql, parameters))

    async def executemany(self, sql: str, parameters):
        return await self._gate.run(lambda: self._conn.executemany(sql, parameters))

    async def executescript(self, sql_script: str):
        return await self._gate.run(lambda: self._conn.executescript(sql_script))

    async def commit(self) -> None:
        await self._gate.run(self._conn.commit)

    async def rollback(self) -> None:
        await self._gate.run(self._conn.rollback)

    async def close(self) -> None:
        await self._gate.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


_conn: Optional[_GatedConnection] = None

TARGET_USER_VERSION = 26


async def get_conn() -> _GatedConnection:
    global _conn
    if _conn is None:
        _conn = _GatedConnection(await aiosqlite.connect(DB_PATH))
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
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    task_group_ref TEXT,
    parent_session_id INTEGER,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    task_state TEXT,
    task_phase TEXT,
    failure_domain TEXT,
    partial_outcome_json TEXT,
    calibration_request_json TEXT,
    calibration_snapshot_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_status ON sessions(user_id, status);

CREATE TABLE IF NOT EXISTS product_state (
    owner_id TEXT PRIMARY KEY,
    onboarding_completed INTEGER NOT NULL DEFAULT 0 CHECK(onboarding_completed IN (0, 1)),
    onboarding_completion_kind TEXT CHECK(
        onboarding_completion_kind IN ('connected', 'skipped', 'legacy')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived', 'deleted')),
    deleted_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS kovaak_connections (
    owner_id TEXT PRIMARY KEY,
    steam_id TEXT NOT NULL CHECK(LENGTH(steam_id) = 17 AND steam_id NOT GLOB '*[^0-9]*'),
    connected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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

_V17_SESSION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("task_group_ref", "TEXT"),
    ("parent_session_id", "INTEGER"),
    ("attempt_number", "INTEGER NOT NULL DEFAULT 1"),
    ("task_state", "TEXT"),
    ("task_phase", "TEXT"),
    ("failure_domain", "TEXT"),
    ("partial_outcome_json", "TEXT"),
    ("calibration_request_json", "TEXT"),
    ("calibration_snapshot_json", "TEXT"),
)

_V17_PRODUCT_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS product_state (
    owner_id TEXT PRIMARY KEY,
    onboarding_completed INTEGER NOT NULL DEFAULT 0 CHECK(onboarding_completed IN (0, 1)),
    onboarding_completion_kind TEXT CHECK(
        onboarding_completion_kind IN ('connected', 'skipped', 'legacy')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_V18_TASK6_CONTRACTS = """
CREATE TABLE IF NOT EXISTS coach_context_refs (
    context_ref TEXT PRIMARY KEY,
    thread_id INTEGER NOT NULL,
    dedupe_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN (
        'analysis', 'issue', 'time_range', 'metric', 'evidence_segment', 'comparison'
    )),
    analysis_session_id INTEGER NOT NULL CHECK(analysis_session_id > 0),
    comparison_session_id INTEGER,
    target_ref TEXT,
    start_ms REAL,
    end_ms REAL,
    label TEXT NOT NULL,
    projection_json TEXT NOT NULL,
    comparison_projection_json TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'detached', 'deleted')),
    attached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    detached_at TEXT,
    deleted_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(thread_id, dedupe_key),
    FOREIGN KEY(thread_id) REFERENCES coach_threads(id),
    CHECK(comparison_session_id IS NULL OR comparison_session_id > 0),
    CHECK(start_ms IS NULL OR start_ms >= 0),
    CHECK(end_ms IS NULL OR (start_ms IS NOT NULL AND end_ms >= start_ms))
);
CREATE INDEX IF NOT EXISTS idx_coach_context_refs_thread_status
    ON coach_context_refs(thread_id, status, attached_at, context_ref);

CREATE TABLE IF NOT EXISTS coach_agent_runs (
    run_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    thread_id INTEGER NOT NULL,
    parent_run_ref TEXT,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
    status TEXT NOT NULL CHECK(status IN (
        'queued', 'running', 'succeeded', 'failed', 'stopped'
    )),
    phase TEXT NOT NULL CHECK(phase IN (
        'queued', 'text_generation', 'tool_execution', 'completed'
    )),
    content TEXT NOT NULL,
    user_message_id INTEGER,
    initiator TEXT NOT NULL DEFAULT 'user' CHECK(initiator IN ('user', 'system')),
    trigger_ref TEXT,
    context_refs_json TEXT NOT NULL DEFAULT '[]',
    partial_text TEXT,
    error_json TEXT,
    teaching_session_ref TEXT,
    teaching_state_version INTEGER,
    teaching_contract_json TEXT,
    stop_requested INTEGER NOT NULL DEFAULT 0 CHECK(stop_requested IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(thread_id) REFERENCES coach_threads(id),
    CHECK(
        (initiator='user' AND trigger_ref IS NULL)
        OR (initiator='system' AND trigger_ref IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_coach_agent_runs_owner_created
    ON coach_agent_runs(owner_id, created_at DESC, run_ref);
CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_agent_runs_active_teaching_session
    ON coach_agent_runs(teaching_session_ref)
    WHERE teaching_session_ref IS NOT NULL AND status IN ('queued', 'running');
CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_agent_runs_owner_system_trigger
    ON coach_agent_runs(owner_id, trigger_ref)
    WHERE initiator='system' AND trigger_ref IS NOT NULL;

CREATE TABLE IF NOT EXISTS teaching_sessions (
    session_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    thread_id INTEGER NOT NULL UNIQUE,
    state_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
    active_run_ref TEXT,
    pending_confirmation_ref TEXT,
    pause_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_id, thread_id),
    FOREIGN KEY(thread_id) REFERENCES coach_threads(id),
    FOREIGN KEY(active_run_ref) REFERENCES coach_agent_runs(run_ref)
);
CREATE INDEX IF NOT EXISTS idx_teaching_sessions_owner_thread
    ON teaching_sessions(owner_id, thread_id);

CREATE TABLE IF NOT EXISTS coach_agent_run_events (
    event_ref TEXT PRIMARY KEY,
    run_ref TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'status', 'phase', 'tool', 'text', 'confirmation', 'guidance', 'error'
    )),
    phase TEXT NOT NULL CHECK(phase IN (
        'queued', 'text_generation', 'tool_execution', 'completed'
    )),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_ref, sequence),
    FOREIGN KEY(run_ref) REFERENCES coach_agent_runs(run_ref)
);

CREATE TABLE IF NOT EXISTS coach_confirmation_requests (
    confirmation_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    impact_code TEXT NOT NULL,
    impact_message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'confirmed', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_coach_confirmation_requests_owner_status
    ON coach_confirmation_requests(owner_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS coach_confirmation_audits (
    audit_ref TEXT PRIMARY KEY,
    confirmation_ref TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('confirm', 'reject')),
    result_status TEXT NOT NULL CHECK(result_status IN ('confirmed', 'rejected')),
    execution_result_json TEXT,
    audit_state TEXT NOT NULL DEFAULT 'completed'
        CHECK(audit_state IN ('pending', 'completed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(confirmation_ref),
    FOREIGN KEY(confirmation_ref) REFERENCES coach_confirmation_requests(confirmation_ref)
);

CREATE TABLE IF NOT EXISTS calibration_profiles (
    owner_id TEXT PRIMARY KEY,
    cm_per_360 REAL,
    fov REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(cm_per_360 IS NULL OR (cm_per_360 > 0 AND cm_per_360 <= 1000)),
    CHECK(fov IS NULL OR (fov > 0 AND fov <= 180)),
    CHECK(cm_per_360 IS NOT NULL OR fov IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS incomplete_capture_deletion_tombstones (
    item_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    run_id INTEGER NOT NULL CHECK(run_id > 0),
    artifact_relpath TEXT NOT NULL CHECK(TRIM(artifact_relpath) <> ''),
    expected_sha256 TEXT NOT NULL CHECK(LENGTH(expected_sha256) = 64),
    expected_size INTEGER NOT NULL CHECK(expected_size >= 0),
    cleanup_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(cleanup_state IN ('pending', 'failed', 'completed')),
    cleanup_attempts INTEGER NOT NULL DEFAULT 0 CHECK(cleanup_attempts >= 0),
    last_error_code TEXT,
    reclaimed_bytes INTEGER NOT NULL DEFAULT 0 CHECK(reclaimed_bytes >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(run_id) REFERENCES kovaak_runs(id),
    CHECK(
        (cleanup_state = 'pending' AND cleanup_attempts = 0
            AND last_error_code IS NULL)
        OR
        (cleanup_state = 'failed' AND cleanup_attempts >= 1
            AND last_error_code = 'artifact_cleanup_failed')
        OR
        (cleanup_state = 'completed' AND cleanup_attempts >= 1
            AND last_error_code IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_incomplete_capture_tombstones_owner_state
    ON incomplete_capture_deletion_tombstones(owner_id, cleanup_state, created_at);
"""

_V9_PROVIDER_PROFILE_TABLE = """
CREATE TABLE IF NOT EXISTS provider_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN (
        'builtin', 'custom_openai_compatible', 'custom_anthropic_compatible'
    )),
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
        await _migrate_v16_profile_plan_loop(conn)
        await _migrate_v17_capability_contracts(conn)
        await _migrate_v18_task6_contracts(conn)
        await _migrate_v19_stale_failure_timestamps(conn)
        await _migrate_v20_teaching_sessions(conn)
        await _migrate_v21_kovaak_connections(conn)
        await _migrate_v22_provider_profile_kinds(conn)
        await _migrate_v23_coach_system_triggers(conn)
        await _migrate_v24_provider_model_capabilities(conn)
        await _migrate_v25_coach_thread_lifecycle(conn)
        await _migrate_v26_guidance_events(conn)
        await conn.commit()
        return

    foreign_keys_disabled = False
    if user_version < 26:
        foreign_keys = await (await conn.execute("PRAGMA foreign_keys")).fetchone()
        if foreign_keys and foreign_keys[0]:
            await conn.execute("PRAGMA foreign_keys = OFF")
            foreign_keys_disabled = True
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
        if user_version < 16:
            await _migrate_v16_profile_plan_loop(conn)
        if user_version < 17:
            await _migrate_v17_capability_contracts(conn)
        if user_version < 18:
            await _migrate_v18_task6_contracts(conn)
        if user_version < 19:
            await _migrate_v19_stale_failure_timestamps(conn)
        if user_version < 20:
            await _migrate_v20_teaching_sessions(conn)
        if user_version < 21:
            await _migrate_v21_kovaak_connections(conn)
        if user_version < 22:
            await _migrate_v22_provider_profile_kinds(conn)
        if user_version < 23:
            await _migrate_v23_coach_system_triggers(conn)
        if user_version < 24:
            await _migrate_v24_provider_model_capabilities(conn)
        if user_version < 25:
            await _migrate_v25_coach_thread_lifecycle(conn)
        if user_version < 26:
            await _migrate_v26_guidance_events(conn)
        await conn.execute(f"PRAGMA user_version = {TARGET_USER_VERSION}")
        await conn.commit()
    except Exception:
        await conn.execute("ROLLBACK")
        raise
    finally:
        if foreign_keys_disabled:
            await conn.execute("PRAGMA foreign_keys = ON")


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


_V16_PROFILE_PLAN_LOOP = """
CREATE TABLE IF NOT EXISTS profile_contributions (
    owner_id TEXT NOT NULL CHECK(TRIM(owner_id) <> ''),
    analysis_ref TEXT NOT NULL CHECK(TRIM(analysis_ref) <> ''),
    contribution_ref TEXT NOT NULL UNIQUE,
    current_revision INTEGER NOT NULL CHECK(current_revision >= 1),
    status TEXT NOT NULL CHECK(status IN ('active', 'invalidated')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(owner_id, analysis_ref)
);
CREATE INDEX IF NOT EXISTS idx_profile_contributions_owner_status
    ON profile_contributions(owner_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS profile_contribution_revisions (
    owner_id TEXT NOT NULL,
    analysis_ref TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL CHECK(LENGTH(payload_digest) = 64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(owner_id, analysis_ref, revision),
    FOREIGN KEY(owner_id, analysis_ref)
        REFERENCES profile_contributions(owner_id, analysis_ref)
);

CREATE TABLE IF NOT EXISTS profile_contribution_tombstones (
    tombstone_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL CHECK(TRIM(owner_id) <> ''),
    analysis_ref TEXT NOT NULL CHECK(TRIM(analysis_ref) <> ''),
    invalidated_revision INTEGER NOT NULL CHECK(invalidated_revision >= 1),
    reason TEXT NOT NULL CHECK(TRIM(reason) <> ''),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_id, analysis_ref, invalidated_revision)
);
CREATE INDEX IF NOT EXISTS idx_profile_tombstones_owner_created
    ON profile_contribution_tombstones(owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS aiming_profile_state (
    owner_id TEXT PRIMARY KEY CHECK(TRIM(owner_id) <> ''),
    rebuild_state TEXT NOT NULL DEFAULT 'clean'
        CHECK(rebuild_state IN ('clean', 'pending')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiming_profile_dimensions (
    owner_id TEXT NOT NULL,
    dimension_key TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('exact_scenario', 'cross_scenario_normalized')),
    scope_ref TEXT NOT NULL,
    projection_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(owner_id, dimension_key, scope, scope_ref),
    FOREIGN KEY(owner_id) REFERENCES aiming_profile_state(owner_id)
);
CREATE INDEX IF NOT EXISTS idx_aiming_profile_dimensions_owner
    ON aiming_profile_dimensions(owner_id, dimension_key);

CREATE TABLE IF NOT EXISTS training_plan_items (
    item_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL CHECK(plan_version >= 1),
    item_revision INTEGER NOT NULL DEFAULT 1 CHECK(item_revision >= 1),
    status TEXT NOT NULL CHECK(status IN ('planned', 'active', 'completed', 'cancelled')),
    item_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_id, item_ref),
    FOREIGN KEY(plan_id, plan_version)
        REFERENCES training_plan_versions(plan_id, version)
);
CREATE INDEX IF NOT EXISTS idx_training_plan_items_owner_plan
    ON training_plan_items(owner_id, plan_id, plan_version, item_ref);

CREATE TABLE IF NOT EXISTS training_plan_item_statuses (
    status_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    item_ref TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL CHECK(to_status IN ('planned', 'active', 'completed', 'cancelled')),
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(item_ref) REFERENCES training_plan_items(item_ref)
);
CREATE INDEX IF NOT EXISTS idx_training_plan_item_statuses_owner_item
    ON training_plan_item_statuses(owner_id, item_ref, created_at);

CREATE TABLE IF NOT EXISTS training_plan_executions (
    execution_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    item_ref TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    item_revision INTEGER NOT NULL,
    scenario_ref TEXT NOT NULL,
    run_refs_json TEXT NOT NULL,
    planned_dose_json TEXT NOT NULL,
    completed_dose_json TEXT NOT NULL,
    completion_status TEXT NOT NULL CHECK(completion_status IN ('completed', 'partial', 'skipped')),
    user_feedback TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(item_ref) REFERENCES training_plan_items(item_ref)
);
CREATE INDEX IF NOT EXISTS idx_training_plan_executions_owner_item
    ON training_plan_executions(owner_id, item_ref, created_at DESC);

CREATE TABLE IF NOT EXISTS training_plan_retests (
    retest_ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    item_ref TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    item_revision INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('matched', 'near_transfer')),
    expected_metric_ref TEXT NOT NULL,
    expected_direction TEXT NOT NULL,
    analysis_refs_json TEXT NOT NULL,
    comparability TEXT NOT NULL CHECK(comparability IN ('comparable', 'not_comparable', 'unavailable')),
    result TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(item_ref) REFERENCES training_plan_items(item_ref)
);
CREATE INDEX IF NOT EXISTS idx_training_plan_retests_owner_item
    ON training_plan_retests(owner_id, item_ref, created_at DESC);
"""


async def _migrate_v16_profile_plan_loop(conn: aiosqlite.Connection) -> None:
    """v15 -> v16: durable aiming profile and Training Plan execution facts."""
    await _execute_transactional_script(conn, _V16_PROFILE_PLAN_LOOP)


async def _migrate_v17_capability_contracts(conn: aiosqlite.Connection) -> None:
    """v16 -> v17: product state, task attempts and calibration snapshots."""
    for column, definition in _V17_SESSION_COLUMNS:
        await _migrate_add_column_if_missing(conn, "sessions", column, definition)
    await _execute_transactional_script(conn, _V17_PRODUCT_STATE_TABLE)
    await conn.execute(
        "UPDATE sessions SET task_group_ref=COALESCE(task_group_ref, 'analysis:' || id), "
        "attempt_number=COALESCE(attempt_number, 1), "
        "task_state=COALESCE(task_state, CASE status "
        "WHEN 'uploading' THEN 'importing' WHEN 'queued' THEN 'queued' "
        "WHEN 'running' THEN 'running' WHEN 'done' THEN 'done' "
        "WHEN 'succeeded' THEN 'done' WHEN 'failed' THEN 'failed' ELSE status END)"
    )


async def _migrate_v18_task6_contracts(conn: aiosqlite.Connection) -> None:
    """v17 -> v18: Coach run/context, calibration and incomplete cleanup."""
    await _migrate_add_column_if_missing(
        conn, "coach_messages", "context_refs_json", "TEXT",
    )
    await _execute_transactional_script(conn, _V18_TASK6_CONTRACTS)
    await _migrate_add_column_if_missing(
        conn, "coach_context_refs", "comparison_projection_json", "TEXT",
    )
    for column, definition in (
        ("parameters_json", "TEXT"),
        ("idempotency_key", "TEXT"),
        ("thread_id", "INTEGER"),
        ("user_message_ref", "TEXT"),
    ):
        await _migrate_add_column_if_missing(
            conn, "coach_command_confirmations", column, definition,
        )
    await _migrate_add_column_if_missing(
        conn, "coach_confirmation_audits", "execution_result_json", "TEXT",
    )
    await _migrate_add_column_if_missing(
        conn, "coach_confirmation_audits", "audit_state", "TEXT NOT NULL DEFAULT 'completed'",
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_coach_confirmation_audits_pending "
        "ON coach_confirmation_audits(owner_id, audit_state, created_at)"
    )


async def _migrate_v19_stale_failure_timestamps(conn: aiosqlite.Connection) -> None:
    """Repair only future timestamps written by injected stale-job clocks."""
    columns = {
        row[1]
        for row in await (await conn.execute("PRAGMA table_info(sessions)")).fetchall()
    }
    if not {"status", "error", "finished_at", "updated_at"}.issubset(columns):
        return
    await conn.execute(
        "UPDATE sessions SET finished_at=NULL, updated_at=CURRENT_TIMESTAMP "
        "WHERE status='failed' AND json_valid(error) "
        "AND json_extract(error, '$.code')='stale_lease_exhausted' "
        "AND finished_at IS NOT NULL AND updated_at=finished_at "
        "AND finished_at > datetime('now', '+1 day')"
    )


async def _migrate_v20_teaching_sessions(conn: aiosqlite.Connection) -> None:
    """v19 -> v20: persistent owner/thread-scoped teaching state."""
    for column, definition in (
        ("user_message_id", "INTEGER"),
        ("teaching_session_ref", "TEXT"),
        ("teaching_state_version", "INTEGER"),
        ("teaching_contract_json", "TEXT"),
    ):
        await _migrate_add_column_if_missing(conn, "coach_agent_runs", column, definition)
    await _execute_transactional_script(conn, """
        CREATE TABLE IF NOT EXISTS teaching_sessions (
            session_ref TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            thread_id INTEGER NOT NULL UNIQUE,
            state_json TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
            active_run_ref TEXT,
            pending_confirmation_ref TEXT,
            pause_reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_id, thread_id),
            FOREIGN KEY(thread_id) REFERENCES coach_threads(id),
            FOREIGN KEY(active_run_ref) REFERENCES coach_agent_runs(run_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_teaching_sessions_owner_thread
            ON teaching_sessions(owner_id, thread_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_agent_runs_active_teaching_session
            ON coach_agent_runs(teaching_session_ref)
            WHERE teaching_session_ref IS NOT NULL AND status IN ('queued', 'running');
    """)


async def _migrate_v21_kovaak_connections(conn: aiosqlite.Connection) -> None:
    """v20 -> v21: one local public KovaaK identity per owner."""
    await _execute_transactional_script(conn, """
        CREATE TABLE IF NOT EXISTS kovaak_connections (
            owner_id TEXT PRIMARY KEY,
            steam_id TEXT NOT NULL CHECK(LENGTH(steam_id) = 17 AND steam_id NOT GLOB '*[^0-9]*'),
            connected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)


async def _migrate_v22_provider_profile_kinds(conn: aiosqlite.Connection) -> None:
    """v21 -> v22: allow custom Anthropic-compatible Provider profiles."""
    cur = await conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='provider_profiles'"
    )
    row = await cur.fetchone()
    if row is None or "custom_anthropic_compatible" in (row[0] or ""):
        return
    await _execute_transactional_script(conn, """
        CREATE TABLE provider_profiles_v22 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id TEXT NOT NULL,
            name TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN (
                'builtin', 'custom_openai_compatible', 'custom_anthropic_compatible'
            )),
            base_url TEXT,
            model_id TEXT NOT NULL,
            api_key TEXT,
            is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO provider_profiles_v22(
            id, owner_id, name, provider_id, kind, base_url, model_id, api_key,
            is_default, created_at, updated_at
        ) SELECT
            id, owner_id, name, provider_id, kind, base_url, model_id, api_key,
            is_default, created_at, updated_at
        FROM provider_profiles;
        DROP TABLE provider_profiles;
        ALTER TABLE provider_profiles_v22 RENAME TO provider_profiles;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_profiles_owner_default
            ON provider_profiles(owner_id) WHERE is_default = 1;
    """)


async def _migrate_v23_coach_system_triggers(conn: aiosqlite.Connection) -> None:
    """v22 -> v23: explicit, idempotent system-initiated Coach runs."""
    row = await (await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='coach_agent_runs'"
    )).fetchone()
    if row is None:
        return
    await _migrate_add_column_if_missing(
        conn,
        "coach_agent_runs",
        "initiator",
        "TEXT NOT NULL DEFAULT 'user' CHECK(initiator IN ('user', 'system'))",
    )
    await _migrate_add_column_if_missing(
        conn,
        "coach_agent_runs",
        "trigger_ref",
        "TEXT",
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_agent_runs_owner_system_trigger "
        "ON coach_agent_runs(owner_id, trigger_ref) "
        "WHERE initiator='system' AND trigger_ref IS NOT NULL"
    )


async def _migrate_v24_provider_model_capabilities(conn: aiosqlite.Connection) -> None:
    """v23 -> v24: retain custom Provider limits returned by discovery."""
    row = await (await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_profiles'"
    )).fetchone()
    if row is None:
        return
    await _migrate_add_column_if_missing(
        conn, "provider_profiles", "context_window", "INTEGER",
    )
    await _migrate_add_column_if_missing(
        conn, "provider_profiles", "max_tokens", "INTEGER",
    )


async def _migrate_v25_coach_thread_lifecycle(conn: aiosqlite.Connection) -> None:
    """v24 -> v25: multiple owner-scoped Coach sessions with soft lifecycle."""
    cur = await conn.execute("PRAGMA table_info(coach_threads)")
    columns = {row[1] for row in await cur.fetchall()}
    required = {"title", "status", "deleted_at"}
    if required.issubset(columns):
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_coach_threads_owner_status "
            "ON coach_threads(user_id, status, updated_at DESC, id DESC)"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_threads_owner_primary "
            "ON coach_threads(user_id) WHERE kind = 'primary'"
        )
        return

    # The original table had UNIQUE(user_id, kind), which prevents more than
    # one conversation. Rebuild it while preserving every existing row/id.
    await _execute_transactional_script(conn, """
        CREATE TABLE coach_threads_v25 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'primary',
            title TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'archived', 'deleted')),
            deleted_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO coach_threads_v25(
            id, user_id, kind, title, status, deleted_at, created_at, updated_at
        )
        SELECT id, user_id, kind, NULL, 'active', NULL, created_at, updated_at
        FROM coach_threads;
        DROP TABLE coach_threads;
        ALTER TABLE coach_threads_v25 RENAME TO coach_threads;
        CREATE INDEX IF NOT EXISTS idx_coach_threads_owner_status
            ON coach_threads(user_id, status, updated_at DESC, id DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_threads_owner_primary
            ON coach_threads(user_id) WHERE kind = 'primary';
        """)


async def _migrate_v26_guidance_events(conn: aiosqlite.Connection) -> None:
    """v25 -> v26: permit bounded guidance events in Coach run history."""
    row = await (await conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='coach_agent_run_events'"
    )).fetchone()
    if row is None or not isinstance(row[0], str) or "'guidance'" in row[0]:
        return
    await _execute_transactional_script(conn, """
        CREATE TABLE coach_agent_run_events_v26 (
            event_ref TEXT PRIMARY KEY,
            run_ref TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK(sequence >= 1),
            event_type TEXT NOT NULL CHECK(event_type IN (
                'status', 'phase', 'tool', 'text', 'confirmation', 'guidance', 'error'
            )),
            phase TEXT NOT NULL CHECK(phase IN (
                'queued', 'text_generation', 'tool_execution', 'completed'
            )),
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(run_ref, sequence),
            FOREIGN KEY(run_ref) REFERENCES coach_agent_runs(run_ref)
        );
        INSERT INTO coach_agent_run_events_v26
        SELECT event_ref, run_ref, sequence, event_type, phase, code, message, payload_json, created_at
        FROM coach_agent_run_events;
        DROP TABLE coach_agent_run_events;
        ALTER TABLE coach_agent_run_events_v26 RENAME TO coach_agent_run_events;
    """)


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
