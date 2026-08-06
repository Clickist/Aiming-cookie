from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from webapp.backend.coach_runtime import CoachRuntimeError, run_pi_coach_turn

_PROFILE = {
    "provider_id": "test-provider",
    "provider_name": "Test Provider",
    "kind": "custom_openai_compatible",
    "base_url": "https://provider.test/v1",
    "model_id": "test-model",
    "context_window": 32768,
    "max_tokens": 4096,
    "api_key": "provider-secret-sentinel",
}

_BRIDGE = {
    "schema_version": "coach_tool_bridge.v1",
    "turn_id": "turn-safe-1",
    "endpoint": "http://127.0.0.1:43127/api/coach/tools/execute",
    "bearer_token": "bridge-secret-sentinel",
    "desktop_token": "desktop-secret-sentinel",
    "expires_at": "2099-01-01T00:00:00Z",
    "user_message_ref": "coach_message:7",
}


def _response(*, tool_events: list[dict] | None = None) -> dict:
    return {
        "schema_version": "coach_runtime_turn.v1",
        "ok": True,
        "reply": "已打开对应分析。",
        "error": None,
        "notes": [],
        "tool_events": tool_events or [],
    }


def _mock_sidecar(response: dict, *, status_code: int = 200):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_client
    return patch("webapp.backend.coach_runtime.httpx.Client", return_value=mock_context), mock_client


def test_structured_pi_turn_sends_turn_scoped_bridge_and_returns_only_safe_tool_events():
    safe_event = {
        "type": "product_command",
        "command_id": "cmd-1",
        "command_name": "navigation.open",
        "status": "succeeded",
        "result_ref": "analysis:12",
        "audit_ref": "coach_audit:5",
        "ui_event": {
            "schema_version": "coach_ui_event.v1",
            "kind": "analysis",
            "analysis_ref": "analysis:12",
            "section": "evidence",
        },
        "warning_or_error": None,
    }
    patcher, mock_client = _mock_sidecar(_response(tool_events=[safe_event]))
    with patcher:
        result = run_pi_coach_turn(
            user_id="owner-a",
            profile=_PROFILE,
            messages=[{"role": "user", "content": "打开证据"}],
            analysis_summary=None,
            tool_bridge=_BRIDGE,
            return_result=True,
        )

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["tool_bridge"] == _BRIDGE
    assert result.reply == "已打开对应分析。"
    assert result.tool_events == [safe_event]
    serialized_result = json.dumps(result.tool_events, ensure_ascii=False)
    assert "bridge-secret-sentinel" not in serialized_result
    assert "desktop-secret-sentinel" not in serialized_result
    assert "provider-secret-sentinel" not in serialized_result


def test_runtime_accepts_only_versioned_knowledge_references_for_persistence():
    safe_event = {
        "type": "knowledge",
        "registry_version": "2026-07-14.v1",
        "topic": "smoothness_sparc",
        "issue_signal": "sparc low",
        "entry_refs": ["knowledge:metric.sparc.definition@1"],
        "entry_versions": [1],
        "source_refs": [
            "product:metric:sparc",
            "research:balasubramanian-2012:sparc",
        ],
        "source_levels": ["product_contract", "academic_peer_reviewed"],
        "max_claim_levels": ["deterministic_rule"],
    }
    patcher, _ = _mock_sidecar(_response(tool_events=[safe_event]))
    with patcher:
        result = run_pi_coach_turn(
            user_id="owner-a",
            profile=_PROFILE,
            messages=[{"role": "user", "content": "解释 SPARC"}],
            analysis_summary=None,
            return_result=True,
        )

    assert result.tool_events == [safe_event]


def _v2_knowledge_event() -> dict:
    from kovaak_tracker.coach.agent_tools import make_fetch_knowledge
    from kovaak_tracker.coach.knowledge_registry import entry_ref, load_registry

    result = make_fetch_knowledge()("reverse high")
    entries = result["entries"]
    registry = load_registry(registry_version=result["registry_version"])
    source_levels = {
        source["source_ref"]: source["source_level"]
        for source in registry["sources"]
    }
    entries_by_ref = {entry_ref(entry): entry for entry in registry["entries"]}
    source_refs = []
    for projected_entry in entries:
        entry = entries_by_ref[projected_entry["entry_ref"]]
        sections = [entry["definition"], entry["scope"], entry["expected_direction"]]
        sections.extend(entry["mechanisms"])
        for name in (
            "cue", "dose_guardrail", "matched_retest", "near_transfer_retest",
            "stop_adjust_rule",
        ):
            value = entry.get(name)
            if isinstance(value, dict):
                sections.append(value)
            elif isinstance(value, list):
                sections.extend(value)
        selected = {
            section["section_ref"]: section
            for section in sections
            if section["section_ref"] in projected_entry["section_refs"]
        }
        source_refs.extend(list(dict.fromkeys(
            source_ref
            for section in selected.values()
            for source_ref in section["source_refs"]
        ))[:8])
    return {
        "type": "knowledge",
        "registry_version": result["registry_version"],
        "topic": "static_clicking",
        "issue_signal": result["signal"],
        "entry_refs": [entry["entry_ref"] for entry in entries],
        "entry_versions": [entry["entry_version"] for entry in entries],
        "section_refs": [
            ref for entry in entries for ref in entry["section_refs"]
        ],
        "claim_refs": [
            ref for entry in entries for ref in entry["claim_refs"]
        ],
        "claim_levels": [
            level for entry in entries for level in entry["claim_levels"]
        ],
        "source_refs": source_refs,
        "source_levels": [source_levels[source_ref] for source_ref in source_refs],
        "max_claim_levels": [entry["max_claim_level"] for entry in entries],
    }


def test_runtime_accepts_v2_section_source_and_claim_refs_only():
    safe_event = _v2_knowledge_event()
    patcher, _ = _mock_sidecar(_response(tool_events=[safe_event]))
    with patcher:
        result = run_pi_coach_turn(
            user_id="owner-a",
            profile=_PROFILE,
            messages=[{"role": "user", "content": "解释制动"}],
            analysis_summary=None,
            return_result=True,
        )

    assert result.tool_events == [safe_event]
    assert "text" not in json.dumps(result.tool_events)


@pytest.mark.parametrize("field", ["section_refs", "claim_refs", "claim_levels"])
def test_runtime_rejects_v2_knowledge_ref_mismatch(field):
    event = _v2_knowledge_event()
    event[field] = ["invented-ref"]
    patcher, _ = _mock_sidecar(_response(tool_events=[event]))
    with patcher, pytest.raises(CoachRuntimeError, match="knowledge"):
        run_pi_coach_turn(
            user_id="owner-a",
            profile=_PROFILE,
            messages=[{"role": "user", "content": "解释制动"}],
            analysis_summary=None,
            return_result=True,
        )


@pytest.mark.parametrize(
    "unsafe_field",
    [
        {"text": "knowledge prose must not be persisted"},
        {"limitations": ["knowledge prose must not be persisted"]},
        {"counterevidence": ["knowledge prose must not be persisted"]},
        {"path": "/Users/someone/private.mp4"},
        {"payload": {"raw_trace": [1, 2]}},
        {"api_key": "secret-sentinel"},
    ],
)
def test_runtime_rejects_knowledge_prose_paths_secrets_and_raw_payloads(unsafe_field):
    event = {
        "type": "knowledge",
        "registry_version": "2026-07-14.v1",
        "topic": "smoothness_sparc",
        "issue_signal": "sparc low",
        "entry_refs": ["knowledge:metric.sparc.definition@1"],
        "entry_versions": [1],
        "source_refs": [
            "product:metric:sparc",
            "research:balasubramanian-2012:sparc",
        ],
        "source_levels": ["product_contract", "academic_peer_reviewed"],
        "max_claim_levels": ["deterministic_rule"],
        **unsafe_field,
    }
    patcher, _ = _mock_sidecar(_response(tool_events=[event]))
    with patcher, pytest.raises(CoachRuntimeError, match="knowledge event|tool event"):
        run_pi_coach_turn(
            user_id="owner-a",
            profile=_PROFILE,
            messages=[{"role": "user", "content": "解释 SPARC"}],
            analysis_summary=None,
            return_result=True,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("entry_refs", ["knowledge:metric.sparc.definition@2"]),
        ("entry_versions", [0]),
        ("source_levels", ["invented"]),
        ("max_claim_levels", ["measured"]),
        ("source_refs", ["secret:runtime-key", "research:balasubramanian-2012:sparc"]),
        ("registry_version", "2026-07-14.v999"),
    ],
)
def test_runtime_rejects_mismatched_or_unbounded_knowledge_references(field, value):
    event = {
        "type": "knowledge",
        "registry_version": "2026-07-14.v1",
        "topic": "smoothness_sparc",
        "issue_signal": "sparc low",
        "entry_refs": ["knowledge:metric.sparc.definition@1"],
        "entry_versions": [1],
        "source_refs": [
            "product:metric:sparc",
            "research:balasubramanian-2012:sparc",
        ],
        "source_levels": ["product_contract", "academic_peer_reviewed"],
        "max_claim_levels": ["deterministic_rule"],
    }
    event[field] = value
    patcher, _ = _mock_sidecar(_response(tool_events=[event]))
    with patcher, pytest.raises(CoachRuntimeError, match="knowledge"):
        run_pi_coach_turn(
            user_id="owner-a",
            profile=_PROFILE,
            messages=[{"role": "user", "content": "解释 SPARC"}],
            analysis_summary=None,
            return_result=True,
        )


def test_runtime_rejects_unsafe_tool_event_instead_of_persisting_internal_payload():
    unsafe = {
        "type": "product_command",
        "command_id": "cmd-unsafe",
        "command_name": "analysis.get",
        "status": "succeeded",
        "audit_ref": "coach_audit:6",
        "internal_payload": {"video_path": "/Users/someone/private.mp4"},
    }
    patcher, _ = _mock_sidecar(_response(tool_events=[unsafe]))
    with patcher, pytest.raises(CoachRuntimeError, match="tool event"):
        run_pi_coach_turn(
            user_id="owner-a",
            profile=_PROFILE,
            messages=[{"role": "user", "content": "读取分析"}],
            analysis_summary=None,
            tool_bridge=_BRIDGE,
            return_result=True,
        )


def test_runtime_rejects_full_evidence_result_in_persistable_tool_event():
    unsafe = {
        "type": "product_command",
        "command_id": "cmd-evidence-full-result",
        "command_name": "analysis.evidence.signal_window",
        "status": "succeeded",
        "result_ref": "analysis:12:segment:1:signal-window",
        "audit_ref": "coach_audit:evidence:1",
        "ui_event": None,
        "warning_or_error": None,
        "result": {
            "points": [[0, 1.0]],
            "next_cursor": "cursor:must-not-persist",
        },
    }
    patcher, _ = _mock_sidecar(_response(tool_events=[unsafe]))
    with patcher, pytest.raises(CoachRuntimeError, match="tool event"):
        run_pi_coach_turn(
            user_id="owner-a",
            profile=_PROFILE,
            messages=[{"role": "user", "content": "查看信号"}],
            analysis_summary=None,
            tool_bridge=_BRIDGE,
            return_result=True,
        )


def test_bridge_secrets_are_redacted_from_runtime_errors():
    failed = {
        "schema_version": "coach_runtime_turn.v1",
        "ok": False,
        "reply": None,
        "error": {
            "category": "coach_runtime",
            "code": "tool_bridge_failed",
            "message": (
                "bridge-secret-sentinel desktop-secret-sentinel "
                "provider-secret-sentinel"
            ),
            "retryable": False,
        },
        "notes": [],
        "tool_events": [],
    }
    patcher, _ = _mock_sidecar(failed)
    with patcher, pytest.raises(CoachRuntimeError) as exc_info:
        run_pi_coach_turn(
            user_id="owner-a",
            profile=_PROFILE,
            messages=[{"role": "user", "content": "执行"}],
            analysis_summary=None,
            tool_bridge=_BRIDGE,
            return_result=True,
        )

    message = str(exc_info.value)
    assert "bridge-secret-sentinel" not in message
    assert "desktop-secret-sentinel" not in message
    assert "provider-secret-sentinel" not in message
    assert "[REDACTED]" in message


def test_failed_runtime_response_preserves_safe_tool_events_on_error():
    event = {
        "type": "product_command",
        "command_id": "command:completed-before-failure",
        "command_name": "analysis.create_from_run",
        "status": "succeeded",
        "result_ref": "analysis:62",
        "audit_ref": "audit:completed-before-failure",
        "ui_event": None,
        "warning_or_error": None,
    }
    failed = _response(tool_events=[event])
    failed.update({
        "ok": False,
        "reply": None,
        "error": {
            "category": "coach_runtime",
            "code": "turn_failed",
            "message": "stream failed after product command",
            "retryable": False,
        },
    })
    patcher, _ = _mock_sidecar(failed, status_code=500)

    with patcher, patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
        with pytest.raises(CoachRuntimeError) as exc_info:
            run_pi_coach_turn(
                user_id="owner-a",
                profile=_PROFILE,
                messages=[{"role": "user", "content": "执行"}],
                analysis_summary=None,
                tool_bridge=_BRIDGE,
                return_result=True,
            )

    assert exc_info.value.tool_events == [event]
    assert exc_info.value.side_effects_possible is True
    mock_run.assert_not_called()


def test_bridge_secrets_are_redacted_from_success_reply_and_notes():
    leaked = _response()
    leaked["reply"] = "bridge-secret-sentinel"
    leaked["notes"] = ["desktop-secret-sentinel provider-secret-sentinel"]
    patcher, _ = _mock_sidecar(leaked)
    with patcher:
        result = run_pi_coach_turn(
            user_id="owner-a",
            profile=_PROFILE,
            messages=[{"role": "user", "content": "执行"}],
            analysis_summary=None,
            tool_bridge=_BRIDGE,
            return_result=True,
        )
    assert result.reply == "[REDACTED]"
    assert result.notes == ["[REDACTED] [REDACTED]"]

@pytest.mark.asyncio
async def test_chat_turn_issues_and_revokes_bridge_and_persists_safe_tool_trace(monkeypatch):
    from webapp.backend import coach_commands, coach_service, coach_store, config
    from webapp.backend.coach_engine import EngineCompleteResult

    thread = await coach_store.get_or_create_primary_thread("owner-trace")
    captured: dict[str, object] = {}
    revoked: list[str] = []

    async def default_profile(owner_id: str):
        assert owner_id == "owner-trace"
        return {
            "profile_id": 1,
            "provider_id": "test-provider",
            "provider_name": "Test Provider",
            "kind": "custom_openai_compatible",
            "base_url": "https://provider.test/v1",
            "model_id": "test-model",
            "credential": {"type": "api_key", "key": "runtime-key"},
        }

    async def complete(turn):
        captured["turn"] = turn
        return EngineCompleteResult(
            reply="已定位证据。",
            notes=[],
            tool_events=[{
                "type": "product_command",
                "command_id": "cmd-trace",
                "command_name": "navigation.open",
                "status": "succeeded",
                "result_ref": "analysis:3",
                "audit_ref": "coach_audit:3",
                "ui_event": {
                    "schema_version": "coach_ui_event.v1",
                    "kind": "analysis",
                    "analysis_ref": "analysis:3",
                    "section": "evidence",
                },
                "warning_or_error": None,
            }],
        )

    original_issue = coach_commands.issue_tool_bridge
    original_revoke = coach_commands.revoke_tool_bridge

    def issue(*args, **kwargs):
        bridge = original_issue(*args, **kwargs)
        captured["bridge"] = bridge
        return bridge

    async def revoke(token: str):
        revoked.append(token)
        return await original_revoke(token)

    monkeypatch.setattr(config, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(coach_service.provider_store, "get_default_runtime_profile", default_profile)
    monkeypatch.setattr(coach_service.provider_store, "runtime_profile_configured", lambda value: True)
    monkeypatch.setattr(coach_service, "complete_turn_async", complete)
    monkeypatch.setattr(coach_service.coach_commands, "issue_tool_bridge", issue)
    monkeypatch.setattr(coach_service.coach_commands, "revoke_tool_bridge", revoke)

    result = await coach_service.run_chat_turn(
        x_user_id="owner-trace",
        thread_id=int(thread["id"]),
        prior_messages=[],
        user_msg_to_store="打开第三次分析的证据",
        diagnosis=None,
        legacy_session_id=None,
        cost_session_id=None,
        tool_bridge_endpoint="http://127.0.0.1:43127/api/coach/tools/execute",
        desktop_token="desktop-runtime-secret",
    )

    assert result.reply == "已定位证据。"
    turn = captured["turn"]
    bridge = captured["bridge"]
    assert turn.tool_bridge == bridge
    assert bridge["user_message_ref"].startswith("coach_message:")
    assert revoked == [bridge["bearer_token"]]

    messages = await coach_store.load_messages(int(thread["id"]))
    assert messages[-1]["trace"][0]["command_id"] == "cmd-trace"
    persisted = json.dumps(messages, ensure_ascii=False)
    assert bridge["bearer_token"] not in persisted
    assert "desktop-runtime-secret" not in persisted
    assert "runtime-key" not in persisted

@pytest.mark.asyncio
async def test_real_analysis_registry_pi_event_is_persisted_as_refs_only(monkeypatch):
    from dataclasses import asdict
    from pathlib import Path
    import subprocess

    from kovaak_tracker.advice import advise
    from kovaak_tracker.coach.diagnosis import build_diagnosis
    from webapp.backend import coach_service, coach_store, config
    from webapp.backend.coach_engine import EngineCompleteResult
    from webapp.backend.coach_runtime import _validate_turn_response
    from webapp.backend.contracts import build_analysis_result_v2

    findings = advise({"sparc": {"med": -7.0}})
    assert [finding.signal for finding in findings] == ["sparc low"]
    diagnosis = build_diagnosis(
        findings,
        {"sparc": {"med": -7.0, "classification": "deterministic"}},
        None,
        {"summary_type": "flicking", "classification": "deterministic"},
    )
    analysis_result = build_analysis_result_v2(
        analysis_id="analysis:e2e",
        analysis_type="flicking",
        input_mode="input_native",
        kovaak_run_ref="run:e2e",
        evidence={
            "sources": {},
            "provenance": {},
            "availability": {"raw_input": "available"},
            "alignment": {"status": "aligned"},
            "warnings": [],
        },
        deterministic={
            "diagnosis": asdict(diagnosis),
            "metrics": {"sparc": {"med": -7.0, "classification": "deterministic"}},
            "timeline": [],
        },
        artifact_manifest={
            "schema_version": "artifact_manifest.v2",
            "external_inputs": [],
            "owned_outputs": [],
        },
        input_snapshot={"scenario": "e2e"},
        created_at="2026-07-14T00:00:00Z",
        completed_at="2026-07-14T00:00:01Z",
        warnings=[],
        errors=[],
    )

    thread = await coach_store.get_or_create_primary_thread("owner-knowledge-e2e")
    runtime_result = None

    async def default_profile(owner_id: str):
        assert owner_id == "owner-knowledge-e2e"
        return {
            "profile_id": 1,
            "provider_id": "anthropic",
            "provider_name": "Anthropic",
            "kind": "builtin",
            "base_url": "https://api.anthropic.com",
            "model_id": "claude-haiku-4-5",
            "credential": {"type": "api_key", "key": "runtime-key"},
        }

    async def complete(turn):
        nonlocal runtime_result
        analysis_summary = json.dumps(
            turn.diagnostic_context,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        script = (
            'import { runAnalysisKnowledgeE2E } from '
            '"./webapp/coach-runtime/test/knowledge-analysis-e2e-fixture.ts";'
            'const input = await new Promise((resolve) => {'
            'let data=""; process.stdin.setEncoding("utf8");'
            'process.stdin.on("data", chunk => data += chunk);'
            'process.stdin.on("end", () => resolve(data));});'
            'process.stdout.write(JSON.stringify(await runAnalysisKnowledgeE2E(String(input))));'
        )
        completed = subprocess.run(
            [
                "node",
                f"--import={config.COACH_RUNTIME_TSX_LOADER.resolve().as_uri()}",
                "--input-type=module",
                "--eval",
                script,
            ],
            input=analysis_summary,
            capture_output=True,
            encoding="utf-8",
            check=True,
            cwd=Path(__file__).resolve().parents[2],
            env={
                **__import__("os").environ,
                "PI_SOURCE_DIR": str(config.PI_SOURCE_DIR.resolve()),
                "TSX_TSCONFIG_PATH": str(
                    (config.PI_SOURCE_DIR / "tsconfig.json").resolve()
                ),
            },
        )
        runtime_response = json.loads(completed.stdout)
        runtime_result = _validate_turn_response(
            runtime_response,
            expected_schema="coach_runtime_turn.v1",
        )
        return EngineCompleteResult(
            reply=runtime_result.reply,
            notes=runtime_result.notes,
            tool_events=runtime_result.tool_events,
        )

    monkeypatch.setattr(config, "COACH_RUNTIME", "pi")
    monkeypatch.setattr(coach_service.provider_store, "get_default_runtime_profile", default_profile)
    monkeypatch.setattr(coach_service.provider_store, "runtime_profile_configured", lambda value: True)
    monkeypatch.setattr(coach_service, "complete_turn_async", complete)

    result = await coach_service.run_chat_turn(
        x_user_id="owner-knowledge-e2e",
        thread_id=int(thread["id"]),
        prior_messages=[],
        user_msg_to_store="解释当前最优先问题",
        diagnosis=analysis_result,
        legacy_session_id=None,
        cost_session_id=None,
    )

    assert runtime_result is not None
    assert result.reply is not None
    assert result.tool_events[0]["entry_refs"] == [
        "knowledge:static.flicking-terminal-control@2",
        "knowledge:tracking.control-smoothness@2",
        "knowledge:hypothesis.tension-management@3",
    ]
    messages = await coach_store.load_messages(int(thread["id"]))
    trace = messages[-1]["trace"]
    assert trace == result.tool_events
    assert trace[0]["registry_version"] == "2026-08-06.v6"
    assert trace[0]["entry_versions"] == [2, 2, 3]
    assert "product.complete-coach-spec" in trace[0]["source_refs"]
    assert "static.flicking-terminal-control.definition" in trace[0]["section_refs"]
    assert "claim:static.flicking-terminal-control.definition" in trace[0]["claim_refs"]
    assert "deterministic_rule" in trace[0]["max_claim_levels"]
    serialized_trace = json.dumps(trace, ensure_ascii=False)
    for forbidden in (
        "threshold_requires_product_calibration",
        "没有身体传感器",
        "/Users/",
        "runtime-key",
        "raw_trace",
        '"text"',
        '"limitations"',
        '"counterevidence"',
    ):
        assert forbidden not in serialized_trace
