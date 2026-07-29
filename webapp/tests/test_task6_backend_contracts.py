from __future__ import annotations

import asyncio
from contextlib import suppress
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from webapp.backend import (
    coach_agent_runs,
    coach_confirmations,
    coach_commands,
    coach_store,
    coach_service,
    config,
    db,
    kovaak_run_store,
    provider_store,
    queue,
)
from webapp.backend.app import app
from webapp.backend.coach_engine import EngineCompleteResult


def _report(*, issue: bool = False) -> dict:
    issues = []
    if issue:
        issues.append({
            "signal": "减速阶段仍有多余修正",
            "severity": "medium",
            "priority": 1,
            "priority_reason": "优先减少末端波动",
            "plain_language_meaning": "接近目标时仍在来回修正",
            "expected_result": "更稳定地停在目标上",
            "claim_level": "deterministic_rule",
            "metric_refs": ["metric:terminal_control@terminal_control.v1"],
            "event_refs": [],
            "limitations": [],
            "primary_evidence_segment_ref": None,
            "supporting_evidence_segment_refs": [],
            "verification": {
                "comparable_requirements": ["same scenario"],
                "success_signals": ["lower correction count"],
                "insufficient_evidence_behavior": "collect another matched run",
            },
            "root_causes": [],
            "prescriptions": [],
        })
    return {
        "diagnosis": {
            "profile": {
                "archetype_id": "decel_jitter",
                "label": "减速抖动型",
                "confidence": 0.8,
                "secondary_tags": [],
            },
            "issues": issues,
            "summary": {},
            "comparison": None,
            "meta": {"cm_per_360": 40.0},
        },
        "figures": {},
        "narration": "测试",
        "notes": [],
    }


async def _seed_done_analysis(owner_id: str, *, issue: bool = False) -> int:
    session_id = await queue.enqueue(owner_id, "", "")
    conn = await db.get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=? WHERE id=?",
        (json.dumps(_report(issue=issue), ensure_ascii=False), session_id),
    )
    await conn.commit()
    return session_id


async def _wait_for_run(
    client: AsyncClient,
    run_ref: str,
    expected: set[str],
) -> dict:
    for _ in range(100):
        response = await client.get(f"/api/coach/agent-runs/{run_ref}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in expected:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError(f"agent run {run_ref} did not reach {sorted(expected)}")


@pytest.mark.asyncio
async def test_v18_schema_contains_task6_contracts() -> None:
    conn = await db.get_conn()
    assert db.TARGET_USER_VERSION >= 18
    tables = {
        row[0]
        for row in await (
            await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    assert {
        "coach_context_refs",
        "coach_agent_runs",
        "coach_agent_run_events",
        "coach_confirmation_requests",
        "coach_confirmation_audits",
        "calibration_profiles",
        "incomplete_capture_deletion_tombstones",
    } <= tables
    message_columns = {
        row[1]
        for row in await (
            await conn.execute("PRAGMA table_info(coach_messages)")
        ).fetchall()
    }
    assert "context_refs_json" in message_columns
    context_columns = {
        row[1]
        for row in await (
            await conn.execute("PRAGMA table_info(coach_context_refs)")
        ).fetchall()
    }
    assert "comparison_projection_json" in context_columns
    command_confirmation_columns = {
        row[1]
        for row in await (
            await conn.execute("PRAGMA table_info(coach_command_confirmations)")
        ).fetchall()
    }
    assert {
        "parameters_json",
        "idempotency_key",
        "thread_id",
        "user_message_ref",
    } <= command_confirmation_columns
    audit_columns = {
        row[1]
        for row in await (
            await conn.execute("PRAGMA table_info(coach_confirmation_audits)")
        ).fetchall()
    }
    assert {"execution_result_json", "audit_state"} <= audit_columns


@pytest.mark.asyncio
async def test_context_attach_detach_deduplicates_and_messages_snapshot_zero_to_many(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = await _seed_done_analysis("owner-a", issue=True)
    second = await _seed_done_analysis("owner-a")
    foreign = await _seed_done_analysis("owner-b")

    async def complete(**kwargs):
        assert len(kwargs["context_bundle"]["contexts"]) == 2
        return {"status": "succeeded", "reply": "已综合两条记录", "tool_events": []}

    monkeypatch.setattr(coach_agent_runs, "execute_turn", complete)
    headers = {"X-User-Id": "owner-a"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers,
    ) as client:
        empty = await client.get("/api/coach/context")
        assert empty.status_code == 200
        assert empty.json() == {"schema_version": "coach_context_list.v1", "contexts": []}

        first_attach = await client.post("/api/coach/context/attach", json={
            "schema_version": "coach_context_attach.v1",
            "kind": "analysis",
            "analysis_ref": f"analysis:{first}",
        })
        duplicate = await client.post("/api/coach/context/attach", json={
            "schema_version": "coach_context_attach.v1",
            "kind": "analysis",
            "analysis_ref": f"analysis:{first}",
        })
        second_attach = await client.post("/api/coach/context/attach", json={
            "schema_version": "coach_context_attach.v1",
            "kind": "analysis",
            "analysis_ref": f"analysis:{second}",
        })
        denied = await client.post("/api/coach/context/attach", json={
            "schema_version": "coach_context_attach.v1",
            "kind": "analysis",
            "analysis_ref": f"analysis:{foreign}",
        })

        assert first_attach.status_code == 200, first_attach.text
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["action"] == "already_attached"
        assert duplicate.json()["context"]["context_ref"] == first_attach.json()["context"]["context_ref"]
        assert second_attach.status_code == 200, second_attach.text
        assert denied.status_code == 404

        created = await client.post("/api/coach/agent-runs", json={
            "schema_version": "coach_agent_run_request.v1",
            "content": "比较这两次训练",
        })
        assert created.status_code == 202, created.text
        completed = await _wait_for_run(client, created.json()["run_ref"], {"succeeded"})
        assert len(completed["contexts"]) == 2
        assert [event["sequence"] for event in completed["events"]] == list(
            range(1, len(completed["events"]) + 1)
        )

        primary = await client.get("/api/coach/primary")
        used = primary.json()["messages"][-2:]
        assert all(len(message["context_refs"]) == 2 for message in used)

        detached = await client.post(
            f"/api/coach/context/{first_attach.json()['context']['context_ref']}/detach"
        )
        repeated = await client.post(
            f"/api/coach/context/{first_attach.json()['context']['context_ref']}/detach"
        )
        assert detached.json()["action"] == "detached"
        assert repeated.json()["action"] == "already_detached"
        assert len((await client.get("/api/coach/context")).json()["contexts"]) == 1

        # Old message snapshots do not change when the active thread context changes.
        old_messages = (await client.get("/api/coach/primary")).json()["messages"][-2:]
        assert all(len(message["context_refs"]) == 2 for message in old_messages)


@pytest.mark.asyncio
async def test_context_rejects_l0_and_deleted_analysis_degrades_old_message_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = await _seed_done_analysis("owner-a")

    async def complete(**_kwargs):
        return {"status": "succeeded", "reply": "已记录", "tool_events": []}

    monkeypatch.setattr(coach_agent_runs, "execute_turn", complete)
    headers = {"X-User-Id": "owner-a"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers,
    ) as client:
        unsafe = await client.post("/api/coach/context/attach", json={
            "schema_version": "coach_context_attach.v1",
            "kind": "metric",
            "analysis_ref": f"analysis:{session_id}",
            "target_ref": "C:\\private\\raw-trace.bin",
            "raw_trace": [{"dx": 1}],
        })
        assert unsafe.status_code in {400, 422}
        assert "private" not in unsafe.text

        attached = await client.post("/api/coach/context/attach", json={
            "schema_version": "coach_context_attach.v1",
            "kind": "analysis",
            "analysis_ref": f"analysis:{session_id}",
        })
        assert attached.status_code == 200
        created = await client.post("/api/coach/agent-runs", json={
            "schema_version": "coach_agent_run_request.v1",
            "content": "保存上下文",
        })
        await _wait_for_run(client, created.json()["run_ref"], {"succeeded"})
        assert (await client.delete(f"/api/sessions/{session_id}")).status_code == 200

        primary = await client.get("/api/coach/primary")
        snapshots = primary.json()["messages"][-2:]
        assert all(message["context_refs"][0]["status"] == "deleted" for message in snapshots)
        assert "raw_trace" not in primary.text
        assert "C:\\" not in primary.text


@pytest.mark.asyncio
async def test_comparison_context_carries_two_independent_safe_projections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = await _seed_done_analysis("owner-a", issue=True)
    second = await _seed_done_analysis("owner-a")

    async def complete(**kwargs):
        contexts = kwargs["context_bundle"]["contexts"]
        assert len(contexts) == 1
        comparison = contexts[0]
        assert comparison["kind"] == "comparison"
        assert comparison["analysis_ref"] == f"analysis:{first}"
        assert comparison["comparison_analysis_ref"] == f"analysis:{second}"
        assert comparison["projection"]["analysis_ref"]["analysis_id"] == f"analysis:{first}"
        assert (
            comparison["comparison_projection"]["analysis_ref"]["analysis_id"]
            == f"analysis:{second}"
        )
        encoded = json.dumps(comparison, ensure_ascii=False)
        assert "raw_trace" not in encoded
        assert "C:\\" not in encoded
        return {"status": "succeeded", "reply": "已比较", "tool_events": []}

    monkeypatch.setattr(coach_agent_runs, "execute_turn", complete)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "owner-a"},
    ) as client:
        attached = await client.post("/api/coach/context/attach", json={
            "schema_version": "coach_context_attach.v1",
            "kind": "comparison",
            "analysis_ref": f"analysis:{first}",
            "comparison_analysis_ref": f"analysis:{second}",
        })
        assert attached.status_code == 200, attached.text

        created = await client.post("/api/coach/agent-runs", json={
            "schema_version": "coach_agent_run_request.v1",
            "content": "比较这两次训练",
            "context_refs": [attached.json()["context"]["context_ref"]],
        })
        assert created.status_code == 202, created.text
        await _wait_for_run(client, created.json()["run_ref"], {"succeeded"})


@pytest.mark.asyncio
async def test_agent_run_stop_waits_for_inflight_message_write_before_cancelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_started = asyncio.Event()
    release_write = asyncio.Event()
    append_cancelled = False
    original_append = coach_store.append_message

    async def delayed_append(thread_id: int, role: str, content: str, **kwargs) -> int:
        nonlocal append_cancelled
        if role == "user":
            write_started.set()
            try:
                await release_write.wait()
            except asyncio.CancelledError:
                append_cancelled = True
                raise
        return await original_append(thread_id, role, content, **kwargs)

    async def unavailable_remote_stop(_run_ref: str) -> bool:
        return False

    monkeypatch.setattr(coach_store, "append_message", delayed_append)
    monkeypatch.setattr(coach_agent_runs.coach_runtime, "stop_pi_coach_turn", unavailable_remote_stop)
    headers = {"X-User-Id": "owner-a"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers,
    ) as client:
        created = await client.post("/api/coach/agent-runs", json={
            "schema_version": "coach_agent_run_request.v1",
            "content": "stop while the message is being stored",
            "context_refs": [],
        })
        assert created.status_code == 202, created.text
        run_ref = created.json()["run_ref"]
        await asyncio.wait_for(write_started.wait(), timeout=1)

        stop_request = asyncio.create_task(
            client.post(f"/api/coach/agent-runs/{run_ref}/stop")
        )
        conn = await db.get_conn()
        for _ in range(100):
            row = await (
                await conn.execute(
                    "SELECT stop_requested FROM coach_agent_runs WHERE run_ref=?",
                    (run_ref,),
                )
            ).fetchone()
            if row is not None and row["stop_requested"] == 1:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("stop request was not persisted")

        release_write.set()
        stopped = await asyncio.wait_for(stop_request, timeout=1)
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == "stopped"
        assert stopped.json()["partial_text"] is None
        assert not append_cancelled
        assert (await client.get(f"/api/coach/agent-runs/{run_ref}")).json()["status"] == "stopped"


@pytest.mark.asyncio
async def test_late_stop_does_not_overwrite_terminal_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_started = asyncio.Event()
    release_execution = asyncio.Event()
    initial_stop_read = asyncio.Event()
    continue_stop = asyncio.Event()

    async def complete_after_release(**_kwargs):
        execution_started.set()
        await release_execution.wait()
        return {"status": "succeeded", "reply": "已完成", "tool_events": []}

    original_get_run = coach_agent_runs.get_run
    stop_get_calls = 0

    async def delayed_stop_read(owner_id: str, run_ref: str):
        nonlocal stop_get_calls
        result = await original_get_run(owner_id, run_ref)
        if stop_get_calls == 0:
            stop_get_calls += 1
            initial_stop_read.set()
            await continue_stop.wait()
        return result

    monkeypatch.setattr(coach_agent_runs, "execute_turn", complete_after_release)
    headers = {"X-User-Id": "owner-a"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers,
    ) as client:
        created = await client.post("/api/coach/agent-runs", json={
            "schema_version": "coach_agent_run_request.v1",
            "content": "完成后再请求停止",
            "context_refs": [],
        })
        assert created.status_code == 202, created.text
        run_ref = created.json()["run_ref"]
        await asyncio.wait_for(execution_started.wait(), timeout=1)
        monkeypatch.setattr(coach_agent_runs, "get_run", delayed_stop_read)

        stop_request = asyncio.create_task(
            client.post(f"/api/coach/agent-runs/{run_ref}/stop")
        )
        await asyncio.wait_for(initial_stop_read.wait(), timeout=1)
        release_execution.set()
        completed = await _wait_for_run(client, run_ref, {"succeeded"})
        assert completed["status"] == "succeeded"
        continue_stop.set()
        late_stop = await asyncio.wait_for(stop_request, timeout=1)
        assert late_stop.status_code == 200, late_stop.text
        assert late_stop.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_stop_stale_current_read_does_not_overwrite_completion_before_task_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = "owner-a"
    run_ref = "agent_run:stale-current-read"
    thread = await coach_store.get_or_create_primary_thread(owner_id)
    conn = await db.get_conn()
    await conn.execute(
        "INSERT INTO coach_agent_runs("
        "run_ref, owner_id, thread_id, attempt, status, phase, content, context_refs_json"
        ") VALUES(?, ?, ?, 1, 'running', 'text_generation', ?, '[]')",
        (run_ref, owner_id, int(thread["id"]), "finish while stop is reading"),
    )
    await conn.commit()

    blocked_task = asyncio.create_task(asyncio.Event().wait())
    coach_agent_runs._tasks[run_ref] = blocked_task
    original_get_run = coach_agent_runs.get_run
    get_calls = 0

    async def stale_current_read(current_owner_id: str, current_run_ref: str):
        nonlocal get_calls
        result = await original_get_run(current_owner_id, current_run_ref)
        get_calls += 1
        if get_calls == 2:
            await conn.execute(
                "UPDATE coach_agent_runs SET status='succeeded', phase='completed', "
                "finished_at=CURRENT_TIMESTAMP WHERE run_ref=?",
                (run_ref,),
            )
            await conn.commit()
            coach_agent_runs._tasks.pop(run_ref).cancel()
        return result

    monkeypatch.setattr(coach_agent_runs, "get_run", stale_current_read)
    try:
        result = await coach_agent_runs.stop_run(owner_id, run_ref)
        assert result is not None
        assert result["status"] == "succeeded"
        actual = await original_get_run(owner_id, run_ref)
        assert actual is not None
        assert actual["status"] == "succeeded"
        assert all(event["code"] != "run_stopped" for event in actual["events"])
    finally:
        coach_agent_runs._tasks.pop(run_ref, None)
        blocked_task.cancel()
        with suppress(asyncio.CancelledError):
            await blocked_task


@pytest.mark.asyncio
async def test_stop_returns_after_bounded_non_provider_write_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_started = asyncio.Event()
    release_write = asyncio.Event()
    append_cancelled = False
    original_append = coach_store.append_message

    async def delayed_append(thread_id: int, role: str, content: str, **kwargs) -> int:
        nonlocal append_cancelled
        if role == "user":
            write_started.set()
            try:
                await release_write.wait()
            except asyncio.CancelledError:
                append_cancelled = True
                raise
        return await original_append(thread_id, role, content, **kwargs)

    async def unavailable_remote_stop(_run_ref: str) -> bool:
        return False

    monkeypatch.setattr(coach_store, "append_message", delayed_append)
    monkeypatch.setattr(coach_agent_runs.coach_runtime, "stop_pi_coach_turn", unavailable_remote_stop)
    monkeypatch.setattr(coach_agent_runs, "_STOP_SETTLE_TIMEOUT_SECONDS", 0.01)
    original_get_run = coach_agent_runs.get_run
    get_calls = 0
    late_get_attempted = asyncio.Event()

    async def block_late_get(owner_id: str, run_ref: str):
        nonlocal get_calls
        get_calls += 1
        if get_calls >= 3:
            late_get_attempted.set()
            await asyncio.Event().wait()
        return await original_get_run(owner_id, run_ref)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-User-Id": "owner-a"},
    ) as client:
        created = await client.post("/api/coach/agent-runs", json={
            "schema_version": "coach_agent_run_request.v1",
            "content": "bound the stop wait around a database write",
            "context_refs": [],
        })
        assert created.status_code == 202, created.text
        run_ref = created.json()["run_ref"]
        await asyncio.wait_for(write_started.wait(), timeout=1)
        monkeypatch.setattr(coach_agent_runs, "get_run", block_late_get)

        pending = await asyncio.wait_for(
            client.post(f"/api/coach/agent-runs/{run_ref}/stop"), timeout=1,
        )
        assert pending.status_code == 200, pending.text
        assert pending.json()["status"] == "running"
        assert not append_cancelled
        assert get_calls == 2
        assert not late_get_attempted.is_set()
        monkeypatch.setattr(coach_agent_runs, "get_run", original_get_run)
        run_task = coach_agent_runs._tasks[run_ref]
        release_write.set()
        assert (await _wait_for_run(client, run_ref, {"stopped"}))["status"] == "stopped"
        await asyncio.wait_for(run_task, timeout=1)
        assert run_ref not in coach_agent_runs._tasks


@pytest.mark.asyncio
async def test_agent_run_stop_failure_domains_retry_and_unsafe_tool_event_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def blocking(**_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(coach_agent_runs, "execute_turn", blocking)
    headers = {"X-User-Id": "owner-a"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers,
    ) as client:
        created = await client.post("/api/coach/agent-runs", json={
            "schema_version": "coach_agent_run_request.v1",
            "content": "请开始生成",
            "context_refs": [],
        })
        assert created.status_code == 202
        await asyncio.wait_for(started.wait(), timeout=1)
        stopped = await client.post(f"/api/coach/agent-runs/{created.json()['run_ref']}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == "stopped"
        assert stopped.json()["partial_text"] is None

        calls = 0

        async def fail_then_succeed(**_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "status": "failed",
                    "reply": "已生成的安全片段",
                    "tool_events": [],
                    "error": {
                        "domain": "network",
                        "code": "provider_unreachable",
                        "message": "模型服务暂不可达",
                        "retryable": True,
                    },
                }
            return {"status": "succeeded", "reply": "重试成功", "tool_events": []}

        monkeypatch.setattr(coach_agent_runs, "execute_turn", fail_then_succeed)
        failed_create = await client.post("/api/coach/agent-runs", json={
            "schema_version": "coach_agent_run_request.v1",
            "content": "失败后重试",
        })
        failed = await _wait_for_run(client, failed_create.json()["run_ref"], {"failed"})
        assert failed["error"]["domain"] == "network"
        assert failed["partial_text"] == "已生成的安全片段"
        retry_responses = await asyncio.gather(
            client.post(f"/api/coach/agent-runs/{failed['run_ref']}/retry"),
            client.post(f"/api/coach/agent-runs/{failed['run_ref']}/retry"),
        )
        assert all(response.status_code == 202 for response in retry_responses), [
            response.text for response in retry_responses
        ]
        retried = retry_responses[0].json()
        duplicate_retry = retry_responses[1].json()
        assert retried["attempt"] == 2
        assert retried["parent_run_ref"] == failed["run_ref"]
        assert duplicate_retry["run_ref"] == retried["run_ref"]
        await _wait_for_run(client, retried["run_ref"], {"succeeded"})
        await asyncio.sleep(0)

        async def unsafe_tool(**_kwargs):
            return {
                "status": "succeeded",
                "reply": "不应保留",
                "tool_events": [{"type": "product_command", "path": "C:\\private\\secret.csv"}],
            }

        monkeypatch.setattr(coach_agent_runs, "execute_turn", unsafe_tool)
        unsafe_create = await client.post("/api/coach/agent-runs", json={
            "schema_version": "coach_agent_run_request.v1",
            "content": "触发不安全事件",
        })
        unsafe_run = await _wait_for_run(client, unsafe_create.json()["run_ref"], {"failed"})
        assert unsafe_run["error"]["domain"] == "permission"
        assert unsafe_run["error"]["code"] == "unsafe_tool_event"
        assert "private" not in json.dumps(unsafe_run, ensure_ascii=False)


@pytest.mark.asyncio
async def test_grounding_failure_never_persists_invalid_assistant_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rejected(**_kwargs):
        return {
            "status": "failed",
            "reply": None,
            "tool_events": [],
            "error": {
                "domain": "model",
                "code": "grounding_violation",
                "message": "Coach response was rejected because it was not grounded",
                "retryable": True,
            },
        }

    monkeypatch.setattr(coach_agent_runs, "execute_turn", rejected)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "grounding-owner"},
    ) as client:
        created = await client.post("/api/coach/agent-runs", json={
            "schema_version": "coach_agent_run_request.v1",
            "content": "比较两次训练",
            "context_refs": [],
        })
        failed = await _wait_for_run(client, created.json()["run_ref"], {"failed"})
        primary = (await client.get("/api/coach/primary")).json()

    assert failed["error"]["code"] == "grounding_violation"
    assert failed["partial_text"] is None
    assert [message["role"] for message in primary["messages"]] == ["user"]


@pytest.mark.asyncio
async def test_confirmation_decisions_are_owner_scoped_audited_and_queries_skip_confirmation() -> None:
    session_id = await _seed_done_analysis("owner-a")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        query = await client.post(
            "/api/coach/confirmations",
            headers={"X-User-Id": "owner-a"},
            json={
                "schema_version": "coach_confirmation_request.v1",
                "action": "navigation",
                "target_ref": f"analysis:{session_id}",
            },
        )
        assert query.status_code == 422

        pending = await client.post(
            "/api/coach/confirmations",
            headers={"X-User-Id": "owner-a"},
            json={
                "schema_version": "coach_confirmation_request.v1",
                "action": "analysis_delete",
                "target_ref": f"analysis:{session_id}",
            },
        )
        assert pending.status_code == 200, pending.text
        body = pending.json()
        assert body["schema_version"] == "coach_confirmation.v1"
        assert body["status"] == "pending"
        assert body["impact"]["code"] == "analysis_becomes_unavailable"
        assert "path" not in pending.text.casefold()

        denied = await client.post(
            f"/api/coach/confirmations/{body['confirmation_ref']}/decision",
            headers={"X-User-Id": "owner-b"},
            json={"schema_version": "coach_confirmation_decision.v1", "decision": "confirm"},
        )
        assert denied.status_code == 404

        rejected = await client.post(
            f"/api/coach/confirmations/{body['confirmation_ref']}/decision",
            headers={"X-User-Id": "owner-a"},
            json={"schema_version": "coach_confirmation_decision.v1", "decision": "reject"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["audit_state"] == "completed"
    assert rejected.json()["audit_ref"].startswith("confirmation_audit:")


@pytest.mark.asyncio
async def test_confirmation_audit_is_pending_before_side_effect_and_reconciles_after_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after the side effect must leave a durable, retryable audit."""
    conn = await db.get_conn()
    await conn.execute(
        "INSERT INTO coach_confirmation_requests(confirmation_ref, owner_id, action, "
        "target_ref, impact_code, impact_message) VALUES(?, ?, 'coach_side_effect', ?, ?, ?)",
        (
            "confirmation:crash-after-effect",
            "owner-a",
            "command:crash-after-effect",
            "coach_inferred_write_executes",
            "Coach side effect",
        ),
    )
    await conn.execute(
        "INSERT INTO coach_command_confirmations("
        "confirmation_ref, owner_id, command_name, parameters_digest, risk, "
        "safe_summary_json, status, expires_at, idempotency_key"
        ") VALUES(?, ?, ?, ?, ?, ?, 'consumed', ?, ?)",
        (
            "confirmation:crash-after-effect",
            "owner-a",
            "analysis.create_from_run",
            "digest",
            "write",
            "{}",
            "2999-01-01T00:00:00+00:00",
            "crash-after-effect-key",
        ),
    )
    await conn.commit()

    calls: list[str] = []

    async def execute_once(_owner_id: str, _confirmation_ref: str) -> dict:
        calls.append("side-effect")
        result = {
            "schema_version": "coach_product_command_result.v1",
            "status": "succeeded",
        }
        await conn.execute(
            "INSERT INTO coach_command_idempotency("
            "owner_id, command_name, idempotency_key, parameters_digest, result_json, latest_audit_ref"
            ") VALUES(?, ?, ?, ?, ?, ?)",
            (
                "owner-a",
                "analysis.create_from_run",
                "crash-after-effect-key",
                "digest",
                json.dumps(result),
                "audit:crash-after-effect",
            ),
        )
        await conn.commit()
        return result

    async def crash_before_completion(*_args, **_kwargs):
        raise RuntimeError("crash after side effect")

    original_complete = coach_confirmations._complete_confirmation_audit
    monkeypatch.setattr(coach_confirmations, "_execute_confirmed_command", execute_once)
    monkeypatch.setattr(coach_confirmations, "_complete_confirmation_audit", crash_before_completion)

    with pytest.raises(RuntimeError, match="crash after side effect"):
        await coach_confirmations.decide_confirmation(
            "owner-a", "confirmation:crash-after-effect", "confirm",
        )

    pending = await (
        await conn.execute(
            "SELECT audit_state, decision FROM coach_confirmation_audits "
            "WHERE confirmation_ref=?",
            ("confirmation:crash-after-effect",),
        )
    ).fetchone()
    assert pending["audit_state"] == "pending"
    assert pending["decision"] == "confirm"
    assert calls == ["side-effect"]

    opposite = await coach_confirmations.decide_confirmation(
        "owner-a", "confirmation:crash-after-effect", "reject",
    )
    assert opposite["status"] == "confirmed"
    assert opposite["audit_state"] == "pending"
    assert opposite["execution"] is None
    assert calls == ["side-effect"]

    monkeypatch.setattr(coach_confirmations, "_complete_confirmation_audit", original_complete)
    reconciled = await coach_confirmations.reconcile_pending_confirmations("owner-a")
    assert reconciled == {"processed": 1, "completed": 1, "failed": 0}
    assert calls == ["side-effect"]

    completed = await (
        await conn.execute(
            "SELECT audit_state, result_status, execution_result_json "
            "FROM coach_confirmation_audits WHERE confirmation_ref=?",
            ("confirmation:crash-after-effect",),
        )
    ).fetchone()
    assert completed["audit_state"] == "completed"
    assert completed["result_status"] == "confirmed"
    assert json.loads(completed["execution_result_json"])["status"] == "succeeded"


@pytest.mark.asyncio
async def test_confirmation_audit_normal_confirm_and_reject_are_complete() -> None:
    conn = await db.get_conn()
    for ref, action in (("confirmation:normal-confirm", "coach_side_effect"), ("confirmation:normal-reject", "analysis_delete")):
        await conn.execute(
            "INSERT INTO coach_confirmation_requests(confirmation_ref, owner_id, action, "
            "target_ref, impact_code, impact_message) VALUES(?, 'owner-a', ?, ?, ?, ?)",
            (ref, action, "analysis:1", "impact", "impact message"),
        )
    await conn.commit()

    async def execute(_owner_id: str, _confirmation_ref: str) -> dict:
        return {"schema_version": "coach_product_command_result.v1", "status": "succeeded"}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(coach_confirmations, "_execute_confirmed_command", execute)
    try:
        confirmed = await coach_confirmations.decide_confirmation(
            "owner-a", "confirmation:normal-confirm", "confirm",
        )
        rejected = await coach_confirmations.decide_confirmation(
            "owner-a", "confirmation:normal-reject", "reject",
        )
    finally:
        monkeypatch.undo()
    assert confirmed["status"] == "confirmed"
    assert rejected["status"] == "rejected"
    assert confirmed["audit_state"] == "completed"
    assert rejected["audit_state"] == "completed"
    rows = await (
        await conn.execute(
            "SELECT decision, audit_state, result_status FROM coach_confirmation_audits "
            "WHERE owner_id='owner-a' ORDER BY confirmation_ref",
        )
    ).fetchall()
    assert [(row["decision"], row["audit_state"], row["result_status"]) for row in rows] == [
        ("confirm", "completed", "confirmed"),
        ("reject", "completed", "rejected"),
    ]


@pytest.mark.asyncio
async def test_confirmation_reconciliation_keeps_unknown_command_outcome_pending() -> None:
    conn = await db.get_conn()
    confirmation_ref = "confirmation:unknown-command-outcome"
    await conn.execute(
        "INSERT INTO coach_confirmation_requests(confirmation_ref, owner_id, action, "
        "target_ref, impact_code, impact_message, status) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (
            confirmation_ref,
            "owner-a",
            "coach_side_effect",
            "command:unknown-command-outcome",
            "coach_inferred_write_executes",
            "impact",
            "confirmed",
        ),
    )
    await conn.execute(
        "INSERT INTO coach_confirmation_audits(audit_ref, confirmation_ref, owner_id, "
        "decision, result_status, audit_state) VALUES(?, ?, ?, 'confirm', 'confirmed', 'pending')",
        ("confirmation_audit:unknown-command-outcome", confirmation_ref, "owner-a"),
    )
    await conn.execute(
        "INSERT INTO coach_command_confirmations("
        "confirmation_ref, owner_id, command_name, parameters_digest, risk, "
        "safe_summary_json, status, expires_at, idempotency_key"
        ") VALUES(?, ?, ?, ?, ?, ?, 'consumed', ?, ?)",
        (
            confirmation_ref,
            "owner-a",
            "analysis.create_from_run",
            "digest",
            "write",
            "{}",
            "2999-01-01T00:00:00+00:00",
            "unknown-command-outcome-key",
        ),
    )
    unknown = {
        "schema_version": "coach_product_command_result.v1",
        "status": "unavailable",
        "warning_or_error": {
            "code": "idempotency_outcome_unknown",
            "message": "inspect current state before retrying",
        },
    }
    await conn.execute(
        "INSERT INTO coach_command_idempotency("
        "owner_id, command_name, idempotency_key, parameters_digest, result_json, latest_audit_ref"
        ") VALUES(?, ?, ?, ?, ?, ?)",
        (
            "owner-a",
            "analysis.create_from_run",
            "unknown-command-outcome-key",
            "digest",
            json.dumps(unknown),
            "audit:unknown-command-outcome",
        ),
    )
    await conn.commit()

    result = await coach_confirmations.reconcile_pending_confirmations("owner-a")
    assert result == {"processed": 1, "completed": 0, "failed": 1}
    row = await (
        await conn.execute(
            "SELECT audit_state FROM coach_confirmation_audits WHERE confirmation_ref=?",
            (confirmation_ref,),
        )
    ).fetchone()
    assert row["audit_state"] == "pending"


@pytest.mark.asyncio
async def test_agent_confirmation_event_executes_canonical_command_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def create_analysis(owner_id: str, run_id: int, **_kwargs):
        assert owner_id == "owner-a"
        calls.append(run_id)
        return {"session_id": 91}

    monkeypatch.setattr(coach_commands, "create_analysis_from_run", create_analysis)
    monkeypatch.setattr(config, "COACH_RUNTIME", "pi")

    async def runtime_profile(_owner_id: str):
        return {"profile_id": 1, "provider_id": "fixture", "model_id": "fixture"}

    monkeypatch.setattr(provider_store, "get_default_runtime_profile", runtime_profile)
    monkeypatch.setattr(provider_store, "runtime_profile_configured", lambda _profile: True)

    async def complete_turn(turn):
        assert turn.tool_bridge is not None
        pending = await coach_commands.execute_tool_bridge(
            turn.tool_bridge["bearer_token"],
            {
                "command_id": "command:task6-confirmation",
                "command_name": "analysis.create_from_run",
                "parameters": {"run_ref": "run:12"},
                "idempotency_key": "task6-confirmation-run-12",
            },
        )
        assert pending["status"] == "needs_confirmation"
        assert "confirmation_ref" not in pending["confirmation"]
        return EngineCompleteResult(
            reply="需要确认后执行",
            notes=[],
            tool_events=[{
                "type": "product_command",
                "command_id": pending["command_id"],
                "command_name": "analysis.create_from_run",
                "status": pending["status"],
                "result_ref": pending.get("result_ref"),
                "audit_ref": pending["audit_ref"],
                "ui_event": pending.get("ui_event"),
                "warning_or_error": pending.get("warning_or_error"),
            }],
        )

    monkeypatch.setattr(coach_service, "complete_turn_async", complete_turn)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1:8765",
        headers={"X-User-Id": "owner-a"},
    ) as client:
        created = await client.post(
            "/api/coach/agent-runs",
            json={
                "schema_version": "coach_agent_run_request.v1",
                "content": "请从 Run 创建分析",
            },
        )
        assert created.status_code == 202, created.text
        completed = await _wait_for_run(client, created.json()["run_ref"], {"succeeded"})
        event = next(item for item in completed["events"] if item["type"] == "confirmation")
        assert event["payload"]["status"] == "needs_confirmation"
        assert event["payload"]["confirmation"]["schema_version"] == "coach_confirmation.v1"
        assert event["payload"]["confirmation"]["audit_state"] is None
        confirmation_ref = event["payload"]["confirmation"]["confirmation_ref"]
        assert calls == []

        denied = await client.post(
            f"/api/coach/confirmations/{confirmation_ref}/decision",
            headers={"X-User-Id": "owner-b"},
            json={"schema_version": "coach_confirmation_decision.v1", "decision": "confirm"},
        )
        assert denied.status_code == 404

        confirmed = await client.post(
            f"/api/coach/confirmations/{confirmation_ref}/decision",
            headers={"X-User-Id": "owner-a"},
            json={"schema_version": "coach_confirmation_decision.v1", "decision": "confirm"},
        )
        repeated = await client.post(
            f"/api/coach/confirmations/{confirmation_ref}/decision",
            headers={"X-User-Id": "owner-a"},
            json={"schema_version": "coach_confirmation_decision.v1", "decision": "confirm"},
        )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["execution"]["status"] == "succeeded"
    assert repeated.json()["execution"] == confirmed.json()["execution"]
    assert calls == [12]


@pytest.mark.asyncio
async def test_calibration_profile_read_save_delete_is_owner_scoped_and_versioned() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        empty = await client.get(
            "/api/calibration-profile", headers={"X-User-Id": "owner-a"},
        )
        assert empty.status_code == 200
        assert empty.json()["schema_version"] == "calibration_profile.v1"
        assert empty.json()["configured"] is False

        saved = await client.put(
            "/api/calibration-profile",
            headers={"X-User-Id": "owner-a"},
            json={
                "schema_version": "calibration_profile_update.v1",
                "cm_per_360": 42.5,
                "fov": 103.0,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["values"] == {"cm_per_360": 42.5, "fov": 103.0}
        assert saved.json()["adoption_priority"] == [
            "stats", "manual_override", "profile_default", "undetermined",
        ]
        assert saved.json()["dpi"] is None
        assert saved.json()["sensitivity"] is None

        foreign = await client.get(
            "/api/calibration-profile", headers={"X-User-Id": "owner-b"},
        )
        assert foreign.json()["configured"] is False

        deleted = await client.delete(
            "/api/calibration-profile", headers={"X-User-Id": "owner-a"},
        )
        repeated = await client.delete(
            "/api/calibration-profile", headers={"X-User-Id": "owner-a"},
        )
        assert deleted.json()["deletion_state"] == "completed"
        assert repeated.json()["deletion_state"] == "already_absent"


@pytest.mark.asyncio
async def test_incomplete_capture_items_are_path_free_and_removal_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "DESKTOP_LAUNCH_TOKEN", "task6-storage-token")
    owner_id = config.DESKTOP_LOCAL_PROFILE
    stats = Path(config.DATA_ROOT) / "user-source.csv"
    stats.write_text("Scenario,Score\nTest,1\n", encoding="utf-8")
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=owner_id,
        source_key="task6-incomplete",
        stats_path=str(stats),
    )
    recovery = Path(config.DATA_ROOT) / "runs" / str(run["id"]) / ".video-partial.recovery"
    recovery.parent.mkdir(parents=True, exist_ok=True)
    recovery.write_bytes(b"recoverable-bytes")

    headers = {"X-Aiming-Cookie-Desktop-Token": "task6-storage-token"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers,
    ) as client:
        listed = await client.get("/api/storage/incomplete")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["schema_version"] == "incomplete_capture_list.v1"
        assert body["total_bytes"] == len(b"recoverable-bytes")
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["reason"] == "interrupted_finalization"
        assert item["removable"] is True
        assert item["impact"]["code"] == "incomplete_recovery_only"
        assert str(Path(config.DATA_ROOT)) not in listed.text
        assert "video-partial" not in listed.text

        removed = await client.delete(f"/api/storage/incomplete/{item['item_ref']}")
        repeated = await client.delete(f"/api/storage/incomplete/{item['item_ref']}")
        assert removed.status_code == 200, removed.text
        assert removed.json()["removal_state"] == "completed"
        assert repeated.json()["removal_state"] == "already_unavailable"
        assert not recovery.exists()
        assert stats.exists()


@pytest.mark.asyncio
async def test_incomplete_capture_cleanup_failure_keeps_tombstone_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = config.DESKTOP_LOCAL_PROFILE
    stats = Path(config.DATA_ROOT) / "task6-recovery-user-source.csv"
    stats.write_text("Scenario,Score\nTest,2\n", encoding="utf-8")
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=owner_id,
        source_key="task6-incomplete-retry",
        stats_path=str(stats),
    )
    recovery = Path(config.DATA_ROOT) / "runs" / str(run["id"]) / ".raw-partial.recovery"
    recovery.parent.mkdir(parents=True, exist_ok=True)
    recovery.write_bytes(b"retryable-recovery")
    item = (await kovaak_run_store.list_incomplete_capture_items(
        owner_id, config.DATA_ROOT,
    ))[0]

    original_unlink = Path.unlink
    failed_once = False

    def fail_recovery_once(path: Path, *args, **kwargs):
        nonlocal failed_once
        if path == recovery and not failed_once:
            failed_once = True
            raise OSError("injected cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_recovery_once)
    denied = await kovaak_run_store.remove_incomplete_capture_item(
        "owner-b", str(item["item_ref"]), config.DATA_ROOT,
    )
    assert denied is None
    assert recovery.exists()

    first = await kovaak_run_store.remove_incomplete_capture_item(
        owner_id, str(item["item_ref"]), config.DATA_ROOT,
    )
    assert first["removal_state"] == "pending_cleanup"
    assert recovery.exists()
    conn = await db.get_conn()
    tombstone = await (
        await conn.execute(
            "SELECT cleanup_state, cleanup_attempts, last_error_code "
            "FROM incomplete_capture_deletion_tombstones WHERE item_ref=?",
            (item["item_ref"],),
        )
    ).fetchone()
    assert tuple(tombstone) == ("failed", 1, "artifact_cleanup_failed")

    recovered = await kovaak_run_store.remove_incomplete_capture_item(
        owner_id, str(item["item_ref"]), config.DATA_ROOT,
    )
    assert recovered["removal_state"] == "completed"
    assert not recovery.exists()
    assert stats.exists()
