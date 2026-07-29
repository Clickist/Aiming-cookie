from __future__ import annotations

import importlib
import json
import os
import subprocess
from unittest.mock import MagicMock, patch

import httpx
import pytest

from kovaak_tracker.coach.diagnosis import (
    CoachDiagnosis,
    DiagnosisIssue,
    ProfileMatch,
    RootCause,
)
from webapp.backend import coach_runtime, config
from webapp.backend.coach_runtime import (
    CoachRuntimeError,
    diagnosis_to_analysis_summary,
    ProviderUnconfiguredError,
    run_pi_coach_turn as _run_pi_coach_turn,
)

_RUNTIME_PROFILE = {
    "provider_id": "test-provider",
    "provider_name": "Test Provider",
    "kind": "custom_openai_compatible",
    "base_url": "https://provider.test/v1",
    "model_id": "test-model",
    "api_key": "runtime-secret-key",
}


def run_pi_coach_turn(**kwargs):
    kwargs.setdefault("user_id", "runtime-user")
    kwargs.setdefault("profile", _RUNTIME_PROFILE)
    return _run_pi_coach_turn(**kwargs)


def _ok_response(reply: str = "教练回复") -> dict:
    return {
        "schema_version": "coach_runtime_turn.v1",
        "ok": True,
        "reply": reply,
        "error": None,
        "notes": [],
    }


def _ok_stdout(reply: str = "教练回复") -> str:
    return json.dumps(_ok_response(reply), ensure_ascii=False)


def _mock_httpx_post_success(reply: str = "Pi 说：练减速段"):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = _ok_response(reply)
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    return patch("webapp.backend.coach_runtime.httpx.Client", return_value=mock_cm)


def test_run_pi_coach_turn_http_success():
    messages = [{"role": "user", "content": "你好"}]
    with _mock_httpx_post_success("Pi 说：练减速段"):
        reply = run_pi_coach_turn(
            messages=messages,
            analysis_summary=None,
        )
    assert reply == "Pi 说：练减速段"


def test_turn_response_rejects_mismatched_run_id():
    response = _ok_response("wrong turn")
    response["run_id"] = "agent_run:other"

    with pytest.raises(CoachRuntimeError, match="run_id") as exc_info:
        coach_runtime._validate_turn_response(
            response,
            expected_schema="coach_runtime_turn.v1",
            expected_run_id="agent_run:expected",
        )

    assert exc_info.value.error_code == "turn_mismatch"


def test_run_pi_coach_turn_http_success_payload():
    messages = [{"role": "user", "content": "你好"}]
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _ok_response()
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        run_pi_coach_turn(messages=messages, analysis_summary=None, timeout_s=45)

        mock_client_cls.assert_called_once_with(timeout=45)
        call_kw = mock_client.post.call_args
        assert call_kw[0][0] == f"{config.COACH_SIDECAR_URL.rstrip('/')}/v1/turn"
        stdin_payload = call_kw[1]["json"]
        assert stdin_payload["schema_version"] == "coach_runtime_turn.v1"
        assert stdin_payload["messages"] == messages
        assert stdin_payload["analysis_summary"] is None
        assert "run_id" in stdin_payload
        assert stdin_payload["user_id"] == "runtime-user"
        assert stdin_payload["model"] == {
            **{key: value for key, value in _RUNTIME_PROFILE.items() if key != "api_key"},
            "credential": {"type": "api_key", "key": "runtime-secret-key"},
        }
        assert "runtime-secret-key" not in json.dumps(stdin_payload["messages"])
        assert "runtime-secret-key" not in str(stdin_payload["analysis_summary"])


def test_build_turn_request_keeps_exact_canonical_analysis_summary():
    from webapp.backend.coach_context import serialize_coach_diagnostic_context

    context = {
        "schema_version": "coach_diagnostic_context.v1",
        "analysis_ref": {
            "analysis_id": "analysis:42",
            "analysis_result_version": "analysis_result.v2",
            "analysis_type": "flicking",
            "input_mode": "input_native",
        },
        "diagnosis": {
            "profile": {},
            "issues": [],
            "summary": {
                "distance": {
                    "value": 12.0,
                    "unit": "raw_counts",
                    "classification": "deterministic",
                },
            },
            "comparison": None,
            "meta": {},
        },
        "evidence_summary": {
            "availability": {"raw_input": "available"},
            "alignment": {"status": "aligned"},
        },
        "warnings": [],
    }
    canonical_wire = serialize_coach_diagnostic_context(context)
    assert canonical_wire is not None

    request, _ = coach_runtime._build_turn_request(
        schema_version=coach_runtime.COACH_RUNTIME_TURN_SCHEMA_V1,
        user_id="runtime-user",
        profile=_RUNTIME_PROFILE,
        messages=[{"role": "user", "content": "hello"}],
        analysis_summary=canonical_wire,
        system_prompt=None,
    )

    assert request["analysis_summary"] == canonical_wire


def _teaching_turn() -> dict:
    return {
        "schema_version": "coach_teaching_turn.v1",
        "session_ref": "teaching_session:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "session_version": 3,
        "phase": "await_teach_back",
        "observation": "减速段有重复修正。",
        "primary_candidate": "reading",
        "alternatives": [],
        "cue": "看到目标减速时，让鼠标也开始减速。",
        "changed_variable": "注意点",
        "active_item_ref": None,
        "prepared_plan_ref": None,
        "prepared_item": None,
        "next_recommendation": None,
        "question_kind": "teach_back",
        "question": "这组练习的注意点是什么？",
        "allowed_command": None,
        "confirmation_intent": "none",
        "retest": {
            "intent": "immediate_matched",
            "comparability_required": True,
            "comparability": "unresolved",
            "revision_decision": None,
        },
        "ratio_sources": [],
        "approved_dose": None,
    }


def test_build_turn_request_propagates_bounded_teaching_turn_only_for_v1():
    teaching_turn = _teaching_turn()
    request, _ = coach_runtime._build_turn_request(
        schema_version=coach_runtime.COACH_RUNTIME_TURN_SCHEMA_V1,
        user_id="runtime-user",
        profile=_RUNTIME_PROFILE,
        messages=[{"role": "user", "content": "hello"}],
        analysis_summary=None,
        system_prompt=None,
        teaching_turn=teaching_turn,
    )

    assert request["teaching_turn"] == teaching_turn

    legacy_request, _ = coach_runtime._build_turn_request(
        schema_version=coach_runtime.COACH_RUNTIME_TURN_SCHEMA_V0,
        user_id=None,
        profile=None,
        messages=[{"role": "user", "content": "hello"}],
        analysis_summary=None,
        system_prompt=None,
    )
    assert "teaching_turn" not in legacy_request


def test_build_turn_request_accepts_complete_prepared_training_item():
    teaching_turn = {
        **_teaching_turn(),
        "phase": "practice_ready",
        "prepared_plan_ref": "plan:guided-loop",
        "prepared_item": {
            "diagnosis_ref": "diagnosis:tracking-error@1",
            "knowledge_ref": "knowledge:speed-matching@1",
            "scenario_profile_ref": "scenario:tracking.smoothbot@1",
            "baseline_metric_ref": "metric:tracking-error@v1",
            "expected_direction": "lower_better",
            "practice_condition": "Repeat the reviewed tracking scenario.",
            "cue": "看到目标减速时，让自己的移动也开始减速。",
            "dose_guardrail": "先保持原场景，只改变这个注意点。",
            "matched_retest_ref": "retest-spec:tracking-matched@1",
            "near_transfer_retest_ref": "retest-spec:tracking-transfer@1",
            "review_date": "after the next comparable practice run",
        },
        "allowed_command": "training_plan.item.add",
    }

    request, _ = coach_runtime._build_turn_request(
        schema_version=coach_runtime.COACH_RUNTIME_TURN_SCHEMA_V1,
        user_id="runtime-user",
        profile=_RUNTIME_PROFILE,
        messages=[{"role": "user", "content": "hello"}],
        analysis_summary=None,
        system_prompt=None,
        teaching_turn=teaching_turn,
    )

    assert request["teaching_turn"] == teaching_turn


@pytest.mark.parametrize(
    "teaching_turn",
    [
        {**_teaching_turn(), "unexpected": "value"},
        {**_teaching_turn(), "cue": {"raw_trace": "mouse deltas"}},
        {**_teaching_turn(), "cue": {"file_path": "relative.txt"}},
        {**_teaching_turn(), "cue": {"item_payload": "opaque data"}},
        {**_teaching_turn(), "cue": {"api_key": "runtime-secret-key"}},
        {**_teaching_turn(), "cue": {"text": r"C:\\Users\\point\\private.txt"}},
        {**_teaching_turn(), "cue": {"text": "x" * 4_001}},
    ],
)
def test_build_turn_request_rejects_unsafe_teaching_turn(teaching_turn):
    with pytest.raises(CoachRuntimeError, match="teaching_turn"):
        coach_runtime._build_turn_request(
            schema_version=coach_runtime.COACH_RUNTIME_TURN_SCHEMA_V1,
            user_id="runtime-user",
            profile=_RUNTIME_PROFILE,
            messages=[{"role": "user", "content": "hello"}],
            analysis_summary=None,
            system_prompt=None,
            teaching_turn=teaching_turn,
        )


@pytest.mark.asyncio
async def test_run_chat_turn_propagates_teaching_turn_to_engine(monkeypatch):
    from webapp.backend import coach_service, coach_store
    from webapp.backend.coach_engine import EngineCompleteResult

    thread = await coach_store.get_or_create_primary_thread("teaching-turn-owner")
    captured: dict = {}

    async def default_profile(owner_id: str):
        assert owner_id == "teaching-turn-owner"
        return {"credential": {"type": "api_key", "key": "runtime-key"}}

    async def complete(turn):
        captured["teaching_turn"] = turn.teaching_turn
        return EngineCompleteResult(reply="ok", notes=[], tool_events=[])

    monkeypatch.setattr(config, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(coach_service.provider_store, "get_default_runtime_profile", default_profile)
    monkeypatch.setattr(coach_service.provider_store, "runtime_profile_configured", lambda _: True)
    monkeypatch.setattr(coach_service, "complete_turn_async", complete)

    result = await coach_service.run_chat_turn(
        x_user_id="teaching-turn-owner",
        thread_id=int(thread["id"]),
        prior_messages=[],
        user_msg_to_store="开始这一组练习",
        diagnosis=None,
        legacy_session_id=None,
        cost_session_id=None,
        teaching_turn=_teaching_turn(),
    )

    assert result.reply == "ok"
    assert captured["teaching_turn"] == _teaching_turn()


@pytest.mark.asyncio
async def test_pi_engine_forwards_teaching_turn_to_async_runtime(monkeypatch):
    from webapp.backend import coach_engine
    from webapp.backend.coach_runtime import PiCoachTurnResult

    captured: dict = {}

    async def complete(**kwargs):
        captured.update(kwargs)
        return PiCoachTurnResult(reply="ok", notes=[], tool_events=[])

    monkeypatch.setattr(coach_runtime, "run_pi_coach_turn_async", complete)
    result = await coach_engine.PiCoachEngine().complete_async(coach_engine.CoachTurn(
        prior_messages=[],
        user_message="继续",
        user_id="teaching-turn-owner",
        provider_profile=_RUNTIME_PROFILE,
        teaching_turn=_teaching_turn(),
        run_ref="agent_run:42",
    ))

    assert result.reply == "ok"
    assert captured["teaching_turn"] == _teaching_turn()


def test_build_turn_request_reprojects_canonical_looking_analysis_summary():
    from webapp.backend.coach_context import (
        coerce_coach_diagnostic_context,
        serialize_coach_diagnostic_context,
    )

    poisoned = {
        "schema_version": "coach_diagnostic_context.v1",
        "analysis_ref": {
            "analysis_id": "analysis:42",
            "analysis_result_version": "analysis_result.v2",
            "analysis_type": "flicking",
            "input_mode": "input_native",
        },
        "diagnosis": {
            "profile": {
                "label": r"C:\Users\point\private\trace.csv",
            },
            "issues": [],
            "summary": {},
            "comparison": None,
            "meta": {},
            "raw_trace": "runtime-raw-trace-sentinel",
            "payload": "runtime-payload-sentinel",
        },
        "evidence_summary": {
            "availability": {"raw_input": "available"},
            "alignment": {"status": "aligned"},
            "benchmark": "runtime-benchmark-sentinel",
        },
        "warnings": [],
        "api_key": "sk-runtime-analysis-summary-secret",
    }
    poisoned_wire = json.dumps(poisoned, ensure_ascii=False)
    expected = serialize_coach_diagnostic_context(
        coerce_coach_diagnostic_context(poisoned)
    )
    assert expected is not None

    request, _ = coach_runtime._build_turn_request(
        schema_version=coach_runtime.COACH_RUNTIME_TURN_SCHEMA_V1,
        user_id="runtime-user",
        profile=_RUNTIME_PROFILE,
        messages=[{"role": "user", "content": "hello"}],
        analysis_summary=poisoned_wire,
        system_prompt=None,
    )

    assert request["analysis_summary"] == expected
    assert request["analysis_summary"] != poisoned_wire
    for sentinel in (
        r"C:\Users\point\private\trace.csv",
        "runtime-raw-trace-sentinel",
        "runtime-payload-sentinel",
        "runtime-benchmark-sentinel",
        "sk-runtime-analysis-summary-secret",
    ):
        assert sentinel not in request["analysis_summary"]


@pytest.mark.asyncio
async def test_direct_coach_turn_persists_no_steam_identity(monkeypatch):
    from webapp.backend import coach_commands, coach_service, coach_store
    from webapp.backend.coach_engine import EngineCompleteResult

    steam_id = "76561199033719938"
    profile_url = f"https://steamcommunity.com/profiles/{steam_id}/"
    thread = await coach_store.get_or_create_primary_thread("direct-steam-owner")
    captured: dict = {}
    original_issue = coach_commands.issue_tool_bridge

    async def default_profile(_owner_id: str):
        return {"credential": {"type": "api_key", "key": "runtime-key"}}

    def issue(*args, **kwargs):
        captured["profile_refs"] = kwargs.get("temporary_profile_refs")
        bridge = original_issue(*args, **kwargs)
        captured["bearer_token"] = bridge["bearer_token"]
        return bridge

    async def complete(turn):
        captured["user_message"] = turn.user_message
        return EngineCompleteResult(reply=f"临时结果来自 {profile_url}", notes=[], tool_events=[])

    monkeypatch.setattr(config, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(coach_service.provider_store, "get_default_runtime_profile", default_profile)
    monkeypatch.setattr(coach_service.provider_store, "runtime_profile_configured", lambda _: True)
    monkeypatch.setattr(coach_service.coach_commands, "issue_tool_bridge", issue)
    monkeypatch.setattr(coach_service, "complete_turn_async", complete)

    result = await coach_service.run_chat_turn(
        x_user_id="direct-steam-owner",
        thread_id=int(thread["id"]),
        prior_messages=[],
        user_msg_to_store=f"帮我查 {profile_url}",
        diagnosis=None,
        legacy_session_id=None,
        cost_session_id=None,
        tool_bridge_endpoint="http://127.0.0.1:43127/api/coach/tools/execute",
    )

    messages = await coach_store.load_messages(int(thread["id"]))
    persisted = json.dumps({"messages": messages, "result": result.assistant_content}, ensure_ascii=False)
    assert steam_id not in persisted
    assert profile_url not in persisted
    assert captured["user_message"] == "帮我查 steam_profile:1"
    assert captured["profile_refs"] == {"steam_profile:1": steam_id}
    released = await coach_commands.execute_tool_bridge(
        captured["bearer_token"],
        {"command_name": "kovaak_scores.lookup", "parameters": {"profile_ref": "steam_profile:1"}},
    )
    assert released["status"] == "unavailable"


@pytest.mark.asyncio
async def test_v2_context_seeds_only_its_reachable_analysis_ref_for_tool_bridge(monkeypatch):
    from webapp.backend import coach_commands, coach_service, coach_store
    from webapp.backend.coach_engine import EngineCompleteResult

    context = {
        "schema_version": "coach_diagnostic_context.v2",
        "analysis_ref": {
            "analysis_id": "analysis:42",
            "analysis_result_version": "analysis_result.v2",
            "analysis_type": "flicking",
            "input_mode": "input_native",
        },
        "scenario": {
            "scenario_profile_ref": None,
            "analyzer_refs": [],
            "support_status": "supported",
            "limitations": [],
        },
        "run_facts": {"mode": "unavailable", "limitations": []},
        "diagnosis": {
            "profile": {}, "issues": [], "summary": {},
            "comparison": None, "meta": {},
        },
        "evidence_summary": {
            "availability": {}, "alignment": {}, "segment_refs": [],
        },
        "trends": [],
        "training": {"active_plan_ref": None, "recent_retest_ref": None},
        "limitations": [],
    }
    thread = await coach_store.get_or_create_primary_thread("bridge-context-owner")
    captured: dict = {}
    original_issue = coach_commands.issue_tool_bridge

    async def default_profile(owner_id: str):
        assert owner_id == "bridge-context-owner"
        return {"credential": {"type": "api_key", "key": "runtime-key"}}

    def issue(*args, **kwargs):
        captured["reachable_refs"] = kwargs.get("reachable_refs")
        return original_issue(*args, **kwargs)

    async def complete(turn):
        captured["context"] = turn.diagnostic_context
        return EngineCompleteResult(reply="ok", notes=[], tool_events=[])

    monkeypatch.setattr(config, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(coach_service.provider_store, "get_default_runtime_profile", default_profile)
    monkeypatch.setattr(coach_service.provider_store, "runtime_profile_configured", lambda _: True)
    monkeypatch.setattr(coach_service.coach_commands, "issue_tool_bridge", issue)
    monkeypatch.setattr(coach_service, "complete_turn_async", complete)

    result = await coach_service.run_chat_turn(
        x_user_id="bridge-context-owner",
        thread_id=int(thread["id"]),
        prior_messages=[],
        user_msg_to_store="查看本次证据",
        diagnosis=context,
        legacy_session_id=None,
        cost_session_id=None,
        tool_bridge_endpoint="http://127.0.0.1:43127/api/coach/tools/execute",
    )

    assert result.reply == "ok"
    assert captured["context"] == context
    assert captured["reachable_refs"] == {"analysis:42"}


def test_provider_request_redacts_steam_identity_from_messages():
    steam_id = "76561199033719938"
    request, _ = coach_runtime._build_turn_request(
        schema_version=coach_runtime.COACH_RUNTIME_TURN_SCHEMA_V1,
        user_id="runtime-user",
        profile=_RUNTIME_PROFILE,
        messages=[{
            "role": "user",
            "content": f"look up https://steamcommunity.com/profiles/{steam_id}/",
        }],
        analysis_summary=None,
        system_prompt=None,
    )

    assert steam_id not in json.dumps(request, ensure_ascii=False)
    assert "Steam Profile hidden" in request["messages"][0]["content"]


def test_run_pi_coach_turn_http_fails_fallback_subprocess():
    messages = [{"role": "user", "content": "x"}]
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("connection refused")
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=_ok_stdout("subprocess 回复"),
                stderr="",
            )
            reply = run_pi_coach_turn(messages=messages, analysis_summary=None)
    assert reply == "subprocess 回复"
    mock_run.assert_called_once()


def test_subprocess_command_uses_loader_file_url_and_native_entry_path(
    tmp_path, monkeypatch
):
    loader = tmp_path / "tsx" / "loader.mjs"
    loader.parent.mkdir()
    loader.write_text("", encoding="utf-8")
    entry = tmp_path / "coach runtime" / "run-turn.ts"
    entry.parent.mkdir()
    entry.write_text("", encoding="utf-8")
    monkeypatch.setattr(coach_runtime, "COACH_RUNTIME_TSX_LOADER", loader)
    monkeypatch.setattr(coach_runtime, "COACH_RUNTIME_RUN_TURN", entry)

    assert coach_runtime._subprocess_command() == [
        "node",
        f"--import={loader.resolve().as_uri()}",
        str(entry),
    ]


def test_subprocess_env_pins_absolute_pi_paths_and_preserves_caller_env(
    tmp_path, monkeypatch
):
    pi_source = tmp_path / "pi source"
    pi_source.mkdir()
    monkeypatch.setattr(coach_runtime, "PI_SOURCE_DIR", pi_source)
    monkeypatch.setenv("COACH_CALLER_SENTINEL", "preserved")
    monkeypatch.setenv("AIMING_COOKIE_DESKTOP_TOKEN", "launch-secret")
    monkeypatch.setenv("PI_SOURCE_DIR", "caller-pi")
    monkeypatch.setenv("TSX_TSCONFIG_PATH", "caller-tsconfig")

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=_ok_stdout(),
        stderr="",
    )
    with patch(
        "webapp.backend.coach_runtime._subprocess_command",
        return_value=["node", "run-turn.ts"],
    ), patch(
        "webapp.backend.coach_runtime.subprocess.run",
        return_value=completed,
    ) as mock_run:
        coach_runtime._run_turn_via_subprocess({"messages": []}, 5)

    env = mock_run.call_args.kwargs["env"]
    assert env["PI_SOURCE_DIR"] == str(pi_source.resolve())
    assert env["TSX_TSCONFIG_PATH"] == str(
        (pi_source / "tsconfig.json").resolve()
    )
    assert env["COACH_CALLER_SENTINEL"] == "preserved"
    assert "AIMING_COOKIE_DESKTOP_TOKEN" not in env


def test_tool_capable_turn_read_timeout_does_not_rerun_in_subprocess():
    tool_bridge = {
        "schema_version": "coach_tool_bridge.v1",
        "turn_id": "turn:timeout",
        "endpoint": "http://127.0.0.1:43127/api/coach/tools/execute",
        "bearer_token": "bridge-timeout-secret",
        "expires_at": "2099-01-01T00:00:00Z",
        "user_message_ref": "coach_message:1",
    }
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ReadTimeout("response lost")
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=_ok_stdout("unsafe duplicate turn"),
                stderr="",
            )
            with pytest.raises(CoachRuntimeError, match="sidecar 不可达"):
                run_pi_coach_turn(
                    messages=[{"role": "user", "content": "执行并解释"}],
                    analysis_summary=None,
                    tool_bridge=tool_bridge,
                )
    mock_run.assert_not_called()


def test_v1_turn_read_timeout_without_bridge_does_not_rerun_in_subprocess():
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ReadTimeout("response lost")
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
            with pytest.raises(CoachRuntimeError) as exc_info:
                run_pi_coach_turn(
                    messages=[{"role": "user", "content": "解释知识"}],
                    analysis_summary=None,
                )

    assert exc_info.value.side_effects_possible is True
    mock_run.assert_not_called()


def test_run_pi_coach_turn_http_fails_no_fallback_raises():
    messages = [{"role": "user", "content": "x"}]
    with patch(
        "webapp.backend.coach_runtime.COACH_SIDECAR_FALLBACK_SUBPROCESS",
        "0",
    ):
        with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.side_effect = httpx.ConnectError("down")
            mock_client_cls.return_value.__enter__.return_value = mock_client
            with pytest.raises(CoachRuntimeError, match="sidecar 不可达"):
                run_pi_coach_turn(messages=messages, analysis_summary=None)


def test_run_pi_coach_turn_http_non_200_invalid_body_does_not_rerun_subprocess():
    messages = [{"role": "user", "content": "x"}]
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "unavailable"
        mock_response.json.side_effect = json.JSONDecodeError("invalid", "unavailable", 0)
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
            with pytest.raises(CoachRuntimeError, match="sidecar HTTP 503") as exc_info:
                run_pi_coach_turn(messages=messages, analysis_summary=None)
    assert exc_info.value.side_effects_possible is True
    mock_run.assert_not_called()


def test_run_pi_coach_turn_ok_false_raises():
    err_body = _ok_response()
    err_body["ok"] = False
    err_body["reply"] = None
    err_body["error"] = {
        "category": "coach_runtime",
        "code": "turn_failed",
        "message": "LLM 不可用",
        "retryable": True,
    }
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = err_body
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with pytest.raises(CoachRuntimeError, match="LLM 不可用"):
            run_pi_coach_turn(
                messages=[{"role": "user", "content": "x"}],
                analysis_summary=None,
            )


def test_runtime_engine_preserves_tool_events_and_skips_python_fallback(monkeypatch):
    from webapp.backend import coach_engine

    event = {
        "type": "product_command",
        "command_id": "command:late-failure",
        "command_name": "analysis.create_from_run",
        "status": "succeeded",
        "result_ref": "analysis:62",
        "audit_ref": "audit:late-failure",
        "ui_event": None,
        "warning_or_error": None,
    }
    error = CoachRuntimeError("turn failed after tool")
    error.tool_events = [event]
    python_calls = 0

    class Pi:
        def complete(self, turn):
            raise error

    class Python:
        def complete_with_notes(self, turn):
            nonlocal python_calls
            python_calls += 1
            return "unsafe fallback", []

    monkeypatch.setattr(config, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(config, "COACH_RUNTIME_FALLBACK_PYTHON", "1")
    engine = coach_engine.RuntimeRoutingCoachEngine(pi=Pi(), python=Python())
    result = engine.complete_with_notes(coach_engine.CoachTurn(
        prior_messages=[],
        user_message="执行并解释",
        tool_bridge={"schema_version": "coach_tool_bridge.v1"},
    ))

    assert result.reply is None
    assert result.tool_events == [event]
    assert python_calls == 0


def test_runtime_engine_uncertain_turn_without_events_skips_python_fallback(monkeypatch):
    from webapp.backend import coach_engine

    error = CoachRuntimeError(
        "subprocess timed out after dispatch",
        side_effects_possible=True,
    )
    python_calls = 0

    class Pi:
        def complete(self, turn):
            raise error

    class Python:
        def complete_with_notes(self, turn):
            nonlocal python_calls
            python_calls += 1
            return "unsafe fallback", []

    monkeypatch.setattr(config, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(config, "COACH_RUNTIME_FALLBACK_PYTHON", "1")
    engine = coach_engine.RuntimeRoutingCoachEngine(pi=Pi(), python=Python())
    result = engine.complete_with_notes(coach_engine.CoachTurn(
        prior_messages=[],
        user_message="解释知识",
    ))

    assert result.reply is None
    assert result.tool_events == []
    assert python_calls == 0


def test_sync_pi_runtime_safe_python_fallback_reports_success(monkeypatch):
    from webapp.backend import coach_engine

    error = CoachRuntimeError("pi unavailable")

    class Pi:
        def complete(self, turn):
            raise error

    class Python:
        def complete_with_notes(self, turn):
            return "fallback reply", ["python note"]

    monkeypatch.setattr(config, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(config, "COACH_RUNTIME_FALLBACK_PYTHON", "1")
    engine = coach_engine.RuntimeRoutingCoachEngine(pi=Pi(), python=Python())
    result = engine.complete_with_notes(coach_engine.CoachTurn(
        prior_messages=[],
        user_message="safe fallback",
    ))

    assert result.reply == "fallback reply"
    assert result.status == "succeeded"
    assert result.error is None
    assert "python note" in result.notes


def test_run_pi_coach_turn_http_success_skips_subprocess():
    with _mock_httpx_post_success("仅 HTTP"):
        with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
            reply = run_pi_coach_turn(
                messages=[{"role": "user", "content": "x"}],
                analysis_summary=None,
            )
    assert reply == "仅 HTTP"
    mock_run.assert_not_called()


def test_run_pi_coach_turn_nonzero_exit_without_ok_json_raises():
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("down")
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="node: module not found",
            )
            with pytest.raises(CoachRuntimeError, match="exit 2") as exc_info:
                run_pi_coach_turn(
                    messages=[{"role": "user", "content": "x"}],
                    analysis_summary=None,
                )
    assert exc_info.value.side_effects_possible is True


def test_subprocess_timeout_after_connect_failure_is_marked_uncertain():
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("down")
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with patch(
            "webapp.backend.coach_runtime.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["node"], timeout=120),
        ):
            with pytest.raises(CoachRuntimeError, match="超时") as exc_info:
                run_pi_coach_turn(
                    messages=[{"role": "user", "content": "x"}],
                    analysis_summary=None,
                )
    assert exc_info.value.side_effects_possible is True


def test_coach_runtime_timeout_config_default():
    assert config.COACH_RUNTIME_TIMEOUT_SECONDS == 120


def test_coach_sidecar_config_defaults():
    assert config.COACH_SIDECAR_URL.startswith("http://127.0.0.1:")
    assert config.COACH_SIDECAR_FALLBACK_SUBPROCESS == "1"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/sidecar",
        "http://127.42.3.4:8765",
        "http://[::1]:8765/sidecar",
    ],
)
def test_coach_sidecar_config_accepts_explicit_loopback_http(url):
    assert config._validate_coach_sidecar_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8765",
        "http://192.0.2.1:8765",
        "https://127.0.0.1:8765",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:70000",
        "http://127.0.0.1:not-a-port",
        "http://[::1%25loopback]:8765",
        "http://user@127.0.0.1:8765",
        "http://127.0.0.1:8765/?token=secret",
        "http://127.0.0.1:8765/#fragment",
    ],
)
def test_coach_sidecar_config_rejects_non_loopback_or_unsafe_url(url):
    with pytest.raises(ValueError, match="COACH_SIDECAR_URL"):
        config._validate_coach_sidecar_url(url)


def test_invalid_coach_sidecar_config_fails_before_fake_client_receives_secret(monkeypatch):
    original = os.environ.get("COACH_SIDECAR_URL")
    monkeypatch.setenv("COACH_SIDECAR_URL", "http://192.0.2.1:8765")
    try:
        with patch("webapp.backend.coach_runtime.httpx.Client") as client:
            with pytest.raises(ValueError, match="COACH_SIDECAR_URL"):
                importlib.reload(config)
        client.assert_not_called()
    finally:
        if original is None:
            monkeypatch.delenv("COACH_SIDECAR_URL", raising=False)
        else:
            monkeypatch.setenv("COACH_SIDECAR_URL", original)
        importlib.reload(config)


def test_diagnosis_to_analysis_summary_from_dataclass():
    diagnosis = CoachDiagnosis(
        profile=ProfileMatch("decel_jitter", "减速抖动型", 0.9, []),
        issues=[
            DiagnosisIssue(
                signal="sparc",
                severity="fix",
                root_causes=[RootCause("symptom", "减速不平滑")],
                prescriptions=[],
                priority=1,
                priority_reason="严重",
            ),
        ],
        summary={"sparc": {"med": -7.0}},
        meta={"summary_type": "flicking"},
    )
    text = diagnosis_to_analysis_summary(diagnosis)
    assert text is not None
    assert "减速抖动型" in text
    assert "sparc" in text
    assert "7" in text


def test_diagnosis_to_analysis_summary_from_dict():
    d = {
        "profile": {"label": "未分类", "archetype_id": "unclassified", "confidence": 0.0},
        "issues": [{"signal": "decel_frac", "severity": "watch", "priority": 1, "root_causes": []}],
        "summary": {"decel_frac": {"p75": 0.4}},
    }
    text = diagnosis_to_analysis_summary(d)
    assert text is not None
    assert "未分类" in text
    assert "decel_frac" in text


def test_diagnosis_to_analysis_summary_invalid_returns_none():
    assert diagnosis_to_analysis_summary(None) is None
    assert diagnosis_to_analysis_summary("not-a-diagnosis") is None


def test_config_paths_exist():
    assert config.COACH_RUNTIME in ("pi", "python")
    assert config.PI_SOURCE_DIR.is_dir()
    assert config.COACH_RUNTIME_RUN_TURN.is_file()
    assert "run-turn" in config.COACH_RUNTIME_RUN_TURN.name

def test_run_pi_coach_turn_without_profile_or_legacy_config_is_recoverably_unconfigured():
    with patch(
        "webapp.backend.coach_runtime._load_legacy_provider_turn_profile",
        return_value=None,
    ):
        with pytest.raises(ProviderUnconfiguredError, match="未配置"):
            _run_pi_coach_turn(
                user_id="owner-a",
                profile=None,
                messages=[{"role": "user", "content": "hello"}],
                analysis_summary=None,
            )


def test_run_pi_coach_turn_redacts_api_key_from_sidecar_error():
    err_body = _ok_response()
    err_body["ok"] = False
    err_body["reply"] = None
    err_body["error"] = {
        "category": "provider",
        "code": "connection_failed",
        "message": "bad credential runtime-secret-key",
        "retryable": True,
    }
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = err_body
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with pytest.raises(CoachRuntimeError) as exc_info:
            run_pi_coach_turn(
                messages=[{"role": "user", "content": "x"}],
                analysis_summary=None,
            )
    assert "runtime-secret-key" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_v4_knowledge_event_uses_the_existing_bounded_event_shape():
    from kovaak_tracker.coach.knowledge_registry import (
        claim_ref,
        entry_ref,
        load_registry,
    )

    registry = load_registry(registry_version="2026-07-29.v4")
    entry = next(
        item for item in registry["entries"]
        if item["entry_id"] == "community.score-farming-context"
    )
    sections = [entry["definition"], entry["scope"], entry["expected_direction"]]
    sections.extend(entry["mechanisms"])
    sources = {
        source["source_ref"]: source for source in registry["sources"]
    }
    event = {
        "type": "knowledge",
        "registry_version": registry["registry_version"],
        "topic": "score_farming",
        "issue_signal": None,
        "entry_refs": [entry_ref(entry)],
        "entry_versions": [entry["entry_version"]],
        "source_refs": list(entry["sources"]),
        "source_levels": [sources[ref]["source_level"] for ref in entry["sources"]],
        "max_claim_levels": ["community_practice"],
        "section_refs": [section["section_ref"] for section in sections],
        "claim_refs": [claim_ref(section) for section in sections],
        "claim_levels": [section["claim_level"] for section in sections],
    }

    assert coach_runtime._validate_knowledge_event(event) == event
