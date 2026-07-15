from __future__ import annotations

import asyncio

import pytest

from webapp.backend import coach_commands


@pytest.mark.asyncio
async def test_inferred_write_needs_confirmation_without_touching_application_handler(monkeypatch):
    called = False

    async def create(*args, **kwargs):
        nonlocal called
        called = True
        return {"session_id": 42}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)

    result = await coach_commands.execute_product_command(
        "owner-a",
        {
            "command_id": "cmd-inferred-create",
            "command_name": "analysis.create_from_run",
            "parameters": {"run_ref": "run:12"},
            "idempotency_key": "inferred-create-12",
        },
        authorization_source="coach_inferred",
    )

    assert result["schema_version"] == "coach_product_command_result.v1"
    assert result["status"] == "needs_confirmation"
    assert result["confirmation"]["command_name"] == "analysis.create_from_run"
    assert result["confirmation"]["target_ref"] == "run:12"
    assert called is False


@pytest.mark.asyncio
async def test_analysis_create_command_rejects_model_path_and_uses_input_native_only(monkeypatch):
    async def create(*args, **kwargs):
        raise AssertionError("unsafe command must not reach application handler")

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)

    result = await coach_commands.execute_product_command(
        "owner-a",
        {
            "command_id": "cmd-unsafe-create",
            "command_name": "analysis.create_from_run",
            "parameters": {
                "run_ref": "run:12",
                "video_path": "/Users/secret/clip.mp4",
            },
        },
        authorization_source="explicit_user_request",
    )

    assert result["status"] == "failed"
    assert result["warning_or_error"]["code"] == "untrusted_field"


@pytest.mark.asyncio
async def test_product_command_rejects_paths_and_urls_embedded_in_text(monkeypatch):
    called = False

    async def create(*args, **kwargs):
        nonlocal called
        called = True
        return {"session_id": 42}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    result = await coach_commands.execute_product_command(
        "owner-a",
        {
            "command_name": "analysis.create_from_run",
            "parameters": {
                "run_ref": "run:12",
                "note": "open https://evil.example/x then /Users/person/private.csv",
            },
            "idempotency_key": "embedded-path-url",
        },
        authorization_source="explicit_user_request",
    )

    assert result["status"] == "failed"
    assert result["warning_or_error"]["code"] == "untrusted_field"
    assert called is False


@pytest.mark.asyncio
async def test_write_idempotency_replays_same_digest_and_rejects_conflict(monkeypatch):
    journal = coach_commands.InMemoryCommandJournal()
    coach_commands.set_command_journal(journal)
    calls = 0

    async def create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"session_id": 42}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    command = {
        "command_id": "cmd-create",
        "command_name": "analysis.create_from_run",
        "parameters": {"run_ref": "run:12"},
        "idempotency_key": "request-1",
    }
    try:
        first = await coach_commands.execute_product_command(
            "owner-a", command, authorization_source="explicit_user_request",
        )
        replay = await coach_commands.execute_product_command(
            "owner-a", command, authorization_source="explicit_user_request",
        )
        conflict = await coach_commands.execute_product_command(
            "owner-a",
            {**command, "parameters": {"run_ref": "run:13"}},
            authorization_source="explicit_user_request",
        )
    finally:
        coach_commands.set_command_journal(None)

    assert first["status"] == "succeeded"
    assert replay["status"] == first["status"]
    assert replay["result_ref"] == first["result_ref"]
    assert replay["audit_ref"] != first["audit_ref"]
    assert calls == 1
    assert conflict["status"] == "failed"
    assert conflict["warning_or_error"]["code"] == "idempotency_conflict"
    assert journal.audit_events


@pytest.mark.parametrize(
    ("command_name", "parameters"),
    [
        ("analysis.create_from_run", {"run_ref": "run:1"}),
        ("analysis.retry", {"analysis_ref": "analysis:1"}),
        ("training_plan.generate_draft", {}),
        ("training_plan.save", {}),
        ("training_plan.activate", {}),
        ("training_plan.pause", {}),
        ("training_plan.adjust", {}),
    ],
)
@pytest.mark.asyncio
async def test_write_command_without_idempotency_key_fails_before_handler(
    monkeypatch, command_name, parameters,
):
    calls: list[str] = []

    async def create(*args, **kwargs):
        calls.append("analysis.create_from_run")
        return {"session_id": 1, "analysis_ref": "analysis:1"}

    async def retry(*args, **kwargs):
        calls.append("analysis.retry")
        return {"analysis_ref": "analysis:1"}

    async def plan(*args, **kwargs):
        calls.append(command_name)
        return ({}, "plan:test")

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    monkeypatch.setattr(coach_commands, "retry_analysis", retry)
    monkeypatch.setattr(coach_commands, "_execute_training_plan", plan)

    result = await coach_commands.execute_product_command(
        "owner-key-required",
        {
            "command_name": command_name,
            "parameters": parameters,
        },
        authorization_source="explicit_user_request",
    )

    assert result["status"] == "failed"
    assert result["warning_or_error"]["code"] == "idempotency_key_required"
    assert calls == []


@pytest.mark.asyncio
async def test_idempotency_reservation_prevents_replay_after_final_record_crash(monkeypatch):
    class CrashOnFinalRecordJournal(coach_commands.SqliteCommandJournal):
        def __init__(self):
            self.crashed = False

        async def record(
            self,
            owner_id,
            command_name,
            idempotency_key,
            digest,
            result,
        ):
            if result["status"] == "succeeded" and not self.crashed:
                self.crashed = True
                raise RuntimeError("simulated process crash before final record")
            await super().record(
                owner_id,
                command_name,
                idempotency_key,
                digest,
                result,
            )

    journal = CrashOnFinalRecordJournal()
    coach_commands.set_command_journal(journal)
    calls = 0

    async def create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"session_id": 62, "analysis_ref": "analysis:62"}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    command = {
        "command_name": "analysis.create_from_run",
        "parameters": {"run_ref": "run:62"},
        "idempotency_key": "crash-safe-request-62",
    }
    try:
        with pytest.raises(RuntimeError, match="simulated process crash"):
            await coach_commands.execute_product_command(
                "owner-crash-safe",
                {**command, "command_id": "command:first"},
                authorization_source="explicit_user_request",
            )
        coach_commands.set_command_journal(coach_commands.SqliteCommandJournal())
        replay = await coach_commands.execute_product_command(
            "owner-crash-safe",
            {**command, "command_id": "command:retry"},
            authorization_source="explicit_user_request",
        )
    finally:
        coach_commands.set_command_journal(None)

    assert calls == 1
    assert replay["status"] == "unavailable"
    assert replay["warning_or_error"]["code"] == "idempotency_outcome_unknown"


@pytest.mark.asyncio
async def test_concurrent_idempotent_write_executes_side_effect_once_and_audits_each_call(monkeypatch):
    journal = coach_commands.InMemoryCommandJournal()
    coach_commands.set_command_journal(journal)
    calls = 0

    async def create(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"session_id": 52, "analysis_ref": "analysis:52"}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    command = {
        "command_name": "analysis.create_from_run",
        "parameters": {"run_ref": "run:52"},
        "idempotency_key": "concurrent-request-52",
    }
    try:
        first, second = await asyncio.gather(*(
            coach_commands.execute_product_command(
                "owner-concurrent",
                {**command, "command_id": f"command:{index}"},
                authorization_source="explicit_user_request",
            )
            for index in range(2)
        ))
    finally:
        coach_commands.set_command_journal(None)

    assert calls == 1
    assert first["status"] == second["status"] == "succeeded"
    assert first["result_ref"] == second["result_ref"] == "analysis:52"
    assert first["audit_ref"] != second["audit_ref"]
    assert len(journal.audit_events) == 2


@pytest.mark.asyncio
async def test_owner_violation_is_failed_not_unavailable(monkeypatch):
    async def get_session(session_id: int):
        return {"id": session_id, "user_id": "other-owner", "status": "done"}

    monkeypatch.setattr(coach_commands.queue, "get_session", get_session)
    result = await coach_commands.execute_product_command(
        "owner-a",
        {
            "command_id": "cmd-owner",
            "command_name": "analysis.get",
            "parameters": {"analysis_ref": "analysis:7"},
        },
        authorization_source="explicit_user_request",
    )

    assert result["status"] == "failed"
    assert result["warning_or_error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_turn_scoped_bridge_binds_token_and_never_echoes_it(monkeypatch):
    async def list_runs(owner_id: str):
        assert owner_id == "owner-a"
        return [{"id": 4, "source_key": "run-4", "created_at": "now", "updated_at": "now"}]

    monkeypatch.setattr(coach_commands, "list_runs", list_runs)
    bridge = coach_commands.issue_tool_bridge(
        "owner-a",
        thread_id=9,
        user_message_ref="message:11",
        endpoint="http://127.0.0.1:43127/api/coach/tools/execute",
        ttl_seconds=60,
        max_calls=1,
    )
    token = bridge["bearer_token"]

    result = await coach_commands.execute_tool_bridge(
        token,
        {
            "command_id": "cmd-bridge",
            "command_name": "run.list",
            "parameters": {},
        },
    )
    exhausted = await coach_commands.execute_tool_bridge(
        token,
        {
            "command_id": "cmd-bridge-2",
            "command_name": "run.list",
            "parameters": {},
        },
    )

    assert bridge["endpoint"] == "http://127.0.0.1:43127/api/coach/tools/execute"
    assert len(token) >= 43
    assert result["status"] == "succeeded"
    assert token not in repr(result)
    assert exhausted["status"] == "unavailable"
    assert exhausted["warning_or_error"]["code"] == "bridge_unavailable"


@pytest.mark.asyncio
async def test_run_analysis_route_delegates_to_shared_handler(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend import config
    from webapp.backend.app import app

    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "route-token")

    async def create(owner_id: str, run_id: int, **kwargs):
        assert owner_id == config.DESKTOP_LOCAL_PROFILE
        assert run_id == 7
        assert kwargs["input_mode"] == "input_native"
        return {"session_id": 99}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/kovaak-runs/7/analyze",
            headers={
                "X-Aiming-Cookie-Desktop-Token": "route-token",
                "Idempotency-Key": "route-analysis-7",
            },
            json={"input_mode": "input_native"},
        )

    assert response.status_code == 200
    assert response.json() == {"session_id": 99}


@pytest.mark.asyncio
async def test_active_analysis_is_reported_as_unavailable_with_stable_ref(monkeypatch):
    async def active(owner_id: str):
        return {"id": 23, "user_id": owner_id, "status": "queued"}

    monkeypatch.setattr(coach_commands.queue, "get_active_session", active)
    result = await coach_commands.execute_product_command(
        "owner-a",
        {
            "command_id": "cmd-active",
            "command_name": "analysis.create_from_run",
            "parameters": {"run_ref": "run:12"},
            "idempotency_key": "active-create-12",
        },
        authorization_source="explicit_user_request",
    )

    assert result["status"] == "unavailable"
    assert result["result_ref"] == "analysis:23"


@pytest.mark.asyncio
async def test_bridge_http_route_uses_bearer_principal_only(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from webapp.backend.app import app

    async def list_runs(owner_id: str):
        assert owner_id == "owner-bridge"
        return []

    monkeypatch.setattr(coach_commands, "list_runs", list_runs)
    bridge = coach_commands.issue_tool_bridge(
        "owner-bridge",
        thread_id=5,
        user_message_ref="message:5",
        endpoint="http://127.0.0.1:43127/api/coach/tools/execute",
        ttl_seconds=60,
        max_calls=1,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/coach/tools/execute",
            headers={"Authorization": f"Bearer {bridge['bearer_token']}"},
            json={
                "command_id": "cmd-http-bridge",
                "command_name": "run.list",
                "parameters": {},
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert bridge["bearer_token"] not in response.text


@pytest.mark.asyncio
async def test_training_plan_adjust_uses_lazy_owner_scoped_store_and_safe_result(monkeypatch):
    calls: dict[str, object] = {}

    class Store:
        class TrainingPlanError(Exception):
            pass

        class PlanNotFound(TrainingPlanError):
            pass

        class PlanForbidden(TrainingPlanError):
            pass

        class ActivePlanReplacementRequired(TrainingPlanError):
            pass

        async def adjust_plan(self, owner_id, plan_ref, plan_payload, **kwargs):
            calls.update({
                "owner_id": owner_id,
                "plan_ref": plan_ref,
                "plan_payload": plan_payload,
                **kwargs,
            })
            return {
                "plan_id": plan_ref,
                "plan_ref": plan_ref,
                "owner_id": owner_id,
                "status": "saved",
                "version": 2,
                "version_ref": f"{plan_ref}:v2",
                "plan_payload": plan_payload,
                "evidence_refs": kwargs["evidence_refs"],
                "verification_targets": kwargs["verification_targets"],
            }

    async def load_store():
        return Store()

    monkeypatch.setattr(coach_commands, "_training_plan_store", load_store)
    payload = {
        "title": "Correction control",
        "diagnostic_context": {"analysis_refs": ["analysis:7"]},
        "prescriptions": [{"scenario": "1wall", "cue": "Commit once", "purpose": "Reduce correction"}],
    }
    targets = [{
        "metric_ref": "metric:first_shot_correction",
        "expected_direction": "decrease",
        "comparability_requirements": ["same scenario"],
        "retest_guidance": "Repeat the same benchmark.",
        "insufficient_evidence_behavior": "keep current plan",
    }]
    result = await coach_commands.execute_product_command(
        "owner-plan",
        {
            "command_id": "cmd-plan-adjust",
            "command_name": "training_plan.adjust",
                "parameters": {
                "plan_ref": "plan:abc123",
                "plan_payload": payload,
                "adjustment_reason": "Comparable runs still show late correction.",
                "evidence_refs": ["analysis:7", "metric:first_shot_correction"],
                    "verification_targets": targets,
                },
                "idempotency_key": "plan-adjust-abc123-v2",
            },
        authorization_source="explicit_user_request",
    )

    assert result["status"] == "succeeded"
    assert result["result_ref"] == "plan:abc123"
    assert "owner_id" not in result["result"]
    assert calls["owner_id"] == "owner-plan"
    assert calls["verification_targets"] == targets

@pytest.mark.asyncio
async def test_sqlite_journal_persists_audit_and_replays_across_journal_instances(monkeypatch):
    calls = 0

    async def create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"session_id": 88, "analysis_ref": "analysis:88"}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    command = {
        "command_id": "cmd-persisted",
        "command_name": "analysis.create_from_run",
        "parameters": {"run_ref": "run:8"},
        "idempotency_key": "persistent-key-8",
    }
    coach_commands.set_command_journal(coach_commands.SqliteCommandJournal())
    try:
        first = await coach_commands.execute_product_command(
            "owner-persist", command, authorization_source="explicit_user_request", thread_id=7,
        )
        coach_commands.set_command_journal(coach_commands.SqliteCommandJournal())
        replay = await coach_commands.execute_product_command(
            "owner-persist", command, authorization_source="explicit_user_request", thread_id=7,
        )
    finally:
        coach_commands.set_command_journal(None)

    assert first["status"] == "succeeded"
    assert replay["status"] == first["status"]
    assert replay["result_ref"] == first["result_ref"]
    assert replay["audit_ref"] != first["audit_ref"]
    assert calls == 1
    from webapp.backend import db
    conn = await db.get_conn()
    rows = await (await conn.execute(
        "SELECT owner_id, command_name, status, result_ref, result_json, "
        "safe_parameters_summary_json FROM coach_product_commands "
        "WHERE owner_id='owner-persist' ORDER BY audit_id",
    )).fetchall()
    assert len(rows) == 2
    row = rows[0]
    assert row["result_ref"] == "analysis:88"
    stored = row["result_json"] + row["safe_parameters_summary_json"]
    assert "/Users/" not in stored
    assert "raw_trace" not in stored
    assert "credential" not in stored


@pytest.mark.asyncio
async def test_sqlite_journal_stale_lookup_cannot_overwrite_different_digest(monkeypatch):
    from webapp.backend import coach_store

    command_name = "analysis.create_from_run"
    idempotency_key = "stale-lookup-key"
    original_parameters = {"run_ref": "run:12"}
    conflicting_parameters = {"run_ref": "run:13"}
    original_digest = coach_commands._idempotency_digest(
        command_name, original_parameters,
    )
    seeded_result = coach_commands._result(
        "command:original",
        "unavailable",
        warning_or_error=coach_commands._error(
            "idempotency_outcome_unknown",
            "a previous command attempt may have completed",
        ),
    )

    class StaleLookupJournal(coach_commands.SqliteCommandJournal):
        async def lookup(self, owner_id, command_name_arg, idempotency_key_arg):
            prior = await super().lookup(
                owner_id, command_name_arg, idempotency_key_arg,
            )
            assert prior is None
            await coach_store.store_command_idempotency(
                owner_id,
                command_name_arg,
                idempotency_key_arg,
                original_digest,
                seeded_result,
            )
            return None

    calls = 0

    async def create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"session_id": 13, "analysis_ref": "analysis:13"}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    coach_commands.set_command_journal(StaleLookupJournal())
    try:
        conflict = await coach_commands.execute_product_command(
            "owner-stale-lookup",
            {
                "command_name": command_name,
                "parameters": conflicting_parameters,
                "idempotency_key": idempotency_key,
            },
            authorization_source="explicit_user_request",
        )
    finally:
        coach_commands.set_command_journal(None)

    stored = await coach_store.lookup_command_idempotency(
        "owner-stale-lookup", command_name, idempotency_key,
    )
    assert conflict["status"] == "failed"
    assert conflict["warning_or_error"]["code"] == "idempotency_conflict"
    assert calls == 0
    assert stored is not None
    assert stored["digest"] == original_digest
    assert stored["result"]["command_id"] == "command:original"


@pytest.mark.asyncio
async def test_sqlite_journal_stale_lookup_replays_same_digest_reservation(monkeypatch):
    from webapp.backend import coach_store

    owner_id = "owner-same-digest-race"
    command_name = "analysis.create_from_run"
    idempotency_key = "same-digest-race-key"
    parameters = {"run_ref": "run:13"}
    digest = coach_commands._idempotency_digest(command_name, parameters)
    seeded_reservation = coach_commands._result(
        "command:original",
        "unavailable",
        warning_or_error=coach_commands._error(
            "idempotency_outcome_unknown",
            "a previous command attempt may have completed",
        ),
    )

    class StaleLookupJournal(coach_commands.SqliteCommandJournal):
        async def lookup(self, owner_id_arg, command_name_arg, idempotency_key_arg):
            prior = await super().lookup(
                owner_id_arg, command_name_arg, idempotency_key_arg,
            )
            assert prior is None
            await coach_store.store_command_idempotency(
                owner_id_arg,
                command_name_arg,
                idempotency_key_arg,
                digest,
                seeded_reservation,
            )
            return None

    calls = 0

    async def create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"session_id": 13, "analysis_ref": "analysis:13"}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    coach_commands.set_command_journal(StaleLookupJournal())
    try:
        replay = await coach_commands.execute_product_command(
            owner_id,
            {
                "command_name": command_name,
                "parameters": parameters,
                "idempotency_key": idempotency_key,
            },
            authorization_source="explicit_user_request",
        )
    finally:
        coach_commands.set_command_journal(None)

    assert replay["status"] == "unavailable"
    assert replay["warning_or_error"]["code"] == "idempotency_outcome_unknown"
    assert replay["command_id"] != seeded_reservation["command_id"]
    assert replay["audit_ref"] != seeded_reservation["audit_ref"]
    assert calls == 0


@pytest.mark.asyncio
async def test_confirmation_reservation_conflict_rolls_back_consumption(monkeypatch):
    from webapp.backend import coach_store, db

    owner_id = "owner-confirmation-conflict"
    command_name = "analysis.create_from_run"
    idempotency_key = "confirmation-conflict-key"
    original_parameters = {"run_ref": "run:12"}
    conflicting_parameters = {"run_ref": "run:13"}
    original_digest = coach_commands._idempotency_digest(
        command_name, original_parameters,
    )
    seeded_result = coach_commands._result(
        "command:original",
        "unavailable",
        warning_or_error=coach_commands._error(
            "idempotency_outcome_unknown",
            "a previous command attempt may have completed",
        ),
    )
    pending = await coach_commands._create_confirmation(
        owner_id,
        command_name,
        conflicting_parameters,
        coach_commands._risk_for(command_name),
    )

    class StaleLookupJournal(coach_commands.SqliteCommandJournal):
        async def lookup(self, owner_id_arg, command_name_arg, idempotency_key_arg):
            prior = await super().lookup(
                owner_id_arg, command_name_arg, idempotency_key_arg,
            )
            assert prior is None
            await coach_store.store_command_idempotency(
                owner_id_arg,
                command_name_arg,
                idempotency_key_arg,
                original_digest,
                seeded_result,
            )
            return None

    calls = 0

    async def create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"session_id": 13, "analysis_ref": "analysis:13"}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    coach_commands.set_command_journal(StaleLookupJournal())
    try:
        conflict = await coach_commands.execute_product_command(
            owner_id,
            {
                "command_name": command_name,
                "parameters": conflicting_parameters,
                "idempotency_key": idempotency_key,
                "confirmation_ref": pending["confirmation_ref"],
            },
            authorization_source="confirmed",
        )
    finally:
        coach_commands.set_command_journal(None)

    conn = await db.get_conn()
    confirmation = await (await conn.execute(
        "SELECT status FROM coach_command_confirmations WHERE confirmation_ref=?",
        (pending["confirmation_ref"],),
    )).fetchone()
    stored = await coach_store.lookup_command_idempotency(
        owner_id, command_name, idempotency_key,
    )
    assert conflict["status"] == "failed"
    assert conflict["warning_or_error"]["code"] == "idempotency_conflict"
    assert calls == 0
    assert confirmation["status"] == "pending"
    assert stored is not None
    assert stored["digest"] == original_digest


@pytest.mark.asyncio
async def test_persistent_confirmation_is_owner_parameter_and_single_use_bound(monkeypatch):
    calls = 0

    async def create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"session_id": 41, "analysis_ref": "analysis:41"}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    base = {
        "command_id": "cmd-confirm",
        "command_name": "analysis.create_from_run",
        "parameters": {"run_ref": "run:4"},
        "idempotency_key": "confirm-key-4",
    }
    pending = await coach_commands.execute_product_command(
        "owner-confirm", base, authorization_source="coach_inferred", thread_id=3,
    )
    ref = pending["confirmation"]["confirmation_ref"]
    assert pending["status"] == "needs_confirmation"

    wrong_owner = await coach_commands.execute_product_command(
        "other-owner", {**base, "confirmation_ref": ref},
        authorization_source="confirmed", thread_id=8,
    )
    assert wrong_owner["warning_or_error"]["code"] == "invalid_confirmation"

    wrong_ref = await coach_commands.execute_product_command(
        "owner-confirm",
        {**base, "confirmation_ref": "confirmation:missing"},
        authorization_source="confirmed",
        thread_id=3,
    )
    assert wrong_ref["warning_or_error"]["code"] == "invalid_confirmation"

    confirmed = await coach_commands.execute_product_command(
        "owner-confirm", {**base, "confirmation_ref": ref},
        authorization_source="confirmed", thread_id=3,
    )
    assert confirmed["status"] == "succeeded"
    assert calls == 1

    consumed = await coach_commands.execute_product_command(
        "owner-confirm", {**base, "confirmation_ref": ref, "idempotency_key": "second-key"},
        authorization_source="confirmed", thread_id=3,
    )
    assert consumed["warning_or_error"]["code"] == "invalid_confirmation"


@pytest.mark.asyncio
async def test_confirmation_cannot_be_consumed_without_durable_reservation(monkeypatch):
    from webapp.backend import coach_store, db

    calls = 0

    async def create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"session_id": 42, "analysis_ref": "analysis:42"}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    base = {
        "command_name": "analysis.create_from_run",
        "parameters": {"run_ref": "run:42"},
        "idempotency_key": "confirm-reservation-42",
    }
    journal = coach_commands.SqliteCommandJournal()
    coach_commands.set_command_journal(journal)
    try:
        pending = await coach_commands.execute_product_command(
            "owner-confirm-reservation",
            base,
            authorization_source="coach_inferred",
        )
        confirmation_ref = pending["confirmation"]["confirmation_ref"]
        original_store = coach_store._store_command_idempotency_row
        failed = False

        async def fail_reservation_once(
            conn, owner_id, command_name, idempotency_key, parameters_digest, result,
        ):
            nonlocal failed
            error = result.get("warning_or_error") or {}
            if error.get("code") == "idempotency_outcome_unknown" and not failed:
                failed = True
                raise RuntimeError("simulated failure before reservation commit")
            await original_store(
                conn,
                owner_id,
                command_name,
                idempotency_key,
                parameters_digest,
                result,
            )

        monkeypatch.setattr(
            coach_store, "_store_command_idempotency_row", fail_reservation_once,
        )
        with pytest.raises(RuntimeError, match="reservation commit"):
            await coach_commands.execute_product_command(
                "owner-confirm-reservation",
                {**base, "confirmation_ref": confirmation_ref},
                authorization_source="confirmed",
            )

        conn = await db.get_conn()
        confirmation = await (await conn.execute(
            "SELECT status FROM coach_command_confirmations WHERE confirmation_ref=?",
            (confirmation_ref,),
        )).fetchone()
        assert confirmation["status"] == "pending"

        replay = await coach_commands.execute_product_command(
            "owner-confirm-reservation",
            {**base, "confirmation_ref": confirmation_ref},
            authorization_source="confirmed",
        )
    finally:
        coach_commands.set_command_journal(None)

    assert failed is True
    assert replay["status"] == "succeeded"
    assert calls == 1


def _real_plan_payload(cue: str = "Pause, then commit once.") -> dict:
    return {
        "title": "Stabilize first-shot correction",
        "diagnostic_context": {
            "analysis_refs": ["analysis:run-42"],
            "metric_refs": ["metric:first_shot_correction"],
            "knowledge_refs": ["knowledge:stopping_corrections"],
        },
        "prescriptions": [{
            "scenario": "Sixshot",
            "cue": cue,
            "purpose": "Reduce corrective reversal.",
            "target_metric_refs": ["metric:first_shot_correction"],
            "expected_direction": "decrease",
            "source_level": "deterministic_rule",
        }],
    }


def _real_verification_targets() -> list[dict]:
    return [{
        "target_metric": "metric:first_shot_correction",
        "expected_direction": "decrease",
        "comparable_requirements": ["same scenario", "same sensitivity profile"],
        "retest_after": "after three focused sessions",
        "insufficient_evidence_behavior": "keep the plan and collect another comparable run",
    }]


@pytest.mark.asyncio
async def test_real_training_plan_command_lifecycle_versions_confirmation_and_audit():
    from webapp.backend import db, training_plan_store

    owner = "owner-real-plan"

    async def command(name: str, parameters: dict, key: str | None = None, **kwargs):
        envelope = {"command_name": name, "parameters": parameters}
        if key is not None:
            envelope["idempotency_key"] = key
        envelope.update(kwargs.pop("envelope", {}))
        return await coach_commands.execute_product_command(
            owner,
            envelope,
            authorization_source=kwargs.pop("authorization_source", "explicit_user_request"),
            thread_id=21,
        )

    first = await command(
        "training_plan.generate_draft",
        {
            "plan_payload": _real_plan_payload(),
            "evidence_refs": ["analysis:run-42", "metric:first_shot_correction"],
            "verification_targets": _real_verification_targets(),
        },
        "plan-generate-1",
    )
    first_ref = first["result_ref"]
    assert first["status"] == "succeeded"
    assert first["result"]["status"] == "draft"
    assert "owner_id" not in first["result"]

    assert (await command("training_plan.save", {"plan_ref": first_ref}, "plan-save-1"))["result"]["status"] == "saved"
    assert (await command("training_plan.activate", {"plan_ref": first_ref}, "plan-activate-1"))["result"]["status"] == "active"

    second = await command(
        "training_plan.generate_draft",
        {
            "plan_payload": _real_plan_payload("Land once; do not chase the target."),
            "evidence_refs": ["analysis:run-42"],
            "verification_targets": _real_verification_targets(),
        },
        "plan-generate-2",
    )
    second_ref = second["result_ref"]
    await command("training_plan.save", {"plan_ref": second_ref}, "plan-save-2")

    pending = await command(
        "training_plan.activate",
        {"plan_ref": second_ref},
        "plan-activate-2",
    )
    assert pending["status"] == "needs_confirmation"
    confirmation_ref = pending["confirmation"]["confirmation_ref"]

    confirmed = await command(
        "training_plan.activate",
        {"plan_ref": second_ref},
        "plan-activate-2",
        authorization_source="confirmed",
        envelope={"confirmation_ref": confirmation_ref},
    )
    assert confirmed["status"] == "succeeded"
    assert confirmed["result"]["status"] == "active"
    assert (await training_plan_store.get_plan(owner, first_ref))["status"] == "paused"

    adjusted = await command(
        "training_plan.adjust",
        {
            "plan_ref": second_ref,
            "plan_payload": _real_plan_payload("Brake continuously, then settle once."),
            "adjustment_reason": "Comparable runs still show a late corrective reversal.",
            "evidence_refs": ["analysis:run-43", "metric:first_shot_correction"],
            "verification_targets": _real_verification_targets(),
        },
        "plan-adjust-2",
    )
    assert adjusted["result"]["version"] == 2
    assert adjusted["result"]["verification_targets"] == _real_verification_targets()
    assert adjusted["result"]["adjustment_reason"] == "Comparable runs still show a late corrective reversal."

    transitions_before = await training_plan_store.list_transitions(owner, second_ref)
    reviewed = await command("training_plan.review", {"plan_ref": second_ref})
    transitions_after = await training_plan_store.list_transitions(owner, second_ref)
    assert reviewed["result"]["version"] == 2
    assert reviewed["result"]["status"] == "active"
    assert transitions_after == transitions_before

    forbidden = await coach_commands.execute_product_command(
        "other-owner",
        {"command_name": "training_plan.review", "parameters": {"plan_ref": second_ref}},
        authorization_source="explicit_user_request",
    )
    assert forbidden["warning_or_error"]["code"] == "forbidden"

    conn = await db.get_conn()
    activation_audits = await (await conn.execute(
        "SELECT status FROM coach_product_commands "
        "WHERE owner_id=? AND command_name='training_plan.activate' "
        "AND idempotency_key='plan-activate-2' ORDER BY audit_id",
        (owner,),
    )).fetchall()
    assert [row["status"] for row in activation_audits] == ["needs_confirmation", "succeeded"]
    idempotency = await (await conn.execute(
        "SELECT result_json FROM coach_command_idempotency "
        "WHERE owner_id=? AND command_name='training_plan.activate' "
        "AND idempotency_key='plan-activate-2'",
        (owner,),
    )).fetchone()
    assert '"status":"succeeded"' in idempotency["result_json"]


@pytest.mark.asyncio
async def test_bridge_cannot_self_authorize_or_consume_confirmation(monkeypatch):
    calls = 0

    async def create(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"session_id": 71, "analysis_ref": "analysis:71"}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create)
    bridge = coach_commands.issue_tool_bridge(
        "owner-bridge-confirm",
        thread_id=22,
        user_message_ref="message:22",
        endpoint="http://127.0.0.1:43127/api/coach/tools/execute",
        ttl_seconds=60,
        max_calls=3,
    )
    base = {
        "command_name": "analysis.create_from_run",
        "parameters": {"run_ref": "run:71"},
        "idempotency_key": "bridge-confirm-71",
    }
    pending = await coach_commands.execute_tool_bridge(bridge["bearer_token"], base)
    claimed_explicit = await coach_commands.execute_tool_bridge(
        bridge["bearer_token"], {**base, "request_basis": "explicit_user_request"},
    )
    claimed_confirmed = await coach_commands.execute_tool_bridge(
        bridge["bearer_token"], {**base, "confirmation_ref": "confirmation:model-supplied"},
    )

    assert pending["status"] == "needs_confirmation"
    assert "confirmation_ref" not in pending["confirmation"]
    assert claimed_explicit["status"] == "failed"
    assert claimed_explicit["warning_or_error"]["code"] == "untrusted_field"
    assert claimed_confirmed["status"] == "failed"
    assert claimed_confirmed["warning_or_error"]["code"] == "untrusted_field"
    assert calls == 0
    assert bridge["bearer_token"] not in repr(
        [pending, claimed_explicit, claimed_confirmed]
    )
