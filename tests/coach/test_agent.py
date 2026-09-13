"""Tests for the retained coach diagnosis tool surface (agent_tools).

The Provider tool-use agent loop was removed (2026-09-13); what remains is the
deterministic diagnosis payload and knowledge-registry fetch/enumeration path.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from kovaak_tracker.coach import agent_tools
from kovaak_tracker.coach.agent_tools import build_diagnosis_tools, diagnosis_payload
from kovaak_tracker.coach.diagnosis import (
    CoachDiagnosis, DiagnosisIssue, ProfileMatch, RootCause,
)
from kovaak_tracker.advice import Prescription


def _diag() -> CoachDiagnosis:
    return CoachDiagnosis(
        profile=ProfileMatch("decel_jitter", "减速抖动型", 1.0, []),
        issues=[DiagnosisIssue(
            signal="sparc low", severity="fix", priority=1,
            priority_reason="top",
            root_causes=[RootCause("symptom", "减速段抖动")],
            prescriptions=[],
        )],
        summary={"sparc": {"med": -7.0}},
        comparison=None,
        meta={"cm_per_360": 48.0},
    )


def _contract_diag() -> CoachDiagnosis:
    return CoachDiagnosis(
        profile=ProfileMatch("decel_jitter", "减速抖动观察型", 0.8, []),
        issues=[DiagnosisIssue(
            signal="sparc low",
            severity="info",
            priority=1,
            priority_reason="[experimental] 观察项排序第 1",
            root_causes=[RootCause("symptom", "减速轮廓存在较多快速波动")],
            prescriptions=[Prescription(
                scenario="pasu",
                reason="练习连续减速",
                cue="接近目标时让速度连续下降，不要硬停",
                purpose="减少减速末段的速度波动",
                target_metrics=["sparc", "reverse_ratio"],
                expected_direction=["sparc ↑", "reverse_ratio ↓"],
                retest_after="同场景完成一组后复测",
                stop_or_adjust_rule="若准确率明显下降，降低速度或放大目标",
                source_level="community_consensus",
            )],
            plain_language_meaning="减速过程不够连续",
            claim_level="experimental",
            metric_refs=["sparc", "reverse_ratio"],
            event_refs=["flick:37"],
            limitations=["threshold_requires_product_calibration"],
            expected_result="减速更连续，反向修正减少",
            verification={
                "comparable_requirements": ["相同场景", "相同设置"],
                "success_signals": ["sparc ↑", "reverse_ratio ↓"],
                "insufficient_evidence_behavior": "样本不足时只记录",
                "raw_payload": {"dx": [123456]},
                "api_key": "sk-secret-sentinel",
                "source_path": "/Users/clickist/private.trace",
            },
        )],
        summary={"raw_dx": [123456]},
        comparison=[{
            "metric": "sparc",
            "status": "below_reference",
            "reason": "sk-secret-in-allowed-field",
            "api_key": "sk-secret-sentinel",
            "source_path": "/Users/clickist/private.trace",
        }],
        meta={
            "cm_per_360": 48.0,
            "fps": 240.0,
            "reference_label": "self baseline",
            "analysis_context": {"raw_dx": [123456]},
            "classification": "/Users/clickist/private.trace",
            "raw_payload": {"dx": [123456]},
            "api_key": "sk-secret-sentinel",
            "source_path": "/Users/clickist/private.trace",
        },
    )


def _assert_explanation_contract(payload: dict[str, Any]) -> None:
    issue = payload["issues"][0]
    assert issue["plain_language_meaning"] == "减速过程不够连续"
    assert issue["claim_level"] == "experimental"
    assert issue["metric_refs"] == ["sparc", "reverse_ratio"]
    assert issue["event_refs"] == ["flick:37"]
    assert issue["limitations"] == ["threshold_requires_product_calibration"]
    assert issue["expected_result"] == "减速更连续，反向修正减少"
    assert issue["verification"] == {
        "comparable_requirements": ["相同场景", "相同设置"],
        "success_signals": ["sparc ↑", "reverse_ratio ↓"],
        "insufficient_evidence_behavior": "样本不足时只记录",
    }

    prescription = issue["prescriptions"][0]
    assert prescription == {
        "scenario": "pasu",
        "reason": "练习连续减速",
        "cue": "接近目标时让速度连续下降，不要硬停",
        "purpose": "减少减速末段的速度波动",
        "target_metrics": ["sparc", "reverse_ratio"],
        "expected_direction": ["sparc ↑", "reverse_ratio ↓"],
        "retest_after": "同场景完成一组后复测",
        "stop_or_adjust_rule": "若准确率明显下降，降低速度或放大目标",
        "source_level": "community_consensus",
    }

    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "raw_payload", "raw_dx", "123456", "api_key",
        "sk-secret-sentinel", "sk-secret-in-allowed-field",
        "source_path", "/Users/clickist/private.trace",
    ):
        assert forbidden not in serialized


def test_get_diagnosis_tool_preserves_safe_explanation_contract():
    tools = build_diagnosis_tools(_contract_diag())

    payload = tools.dispatch("coach_get_diagnosis", {})

    _assert_explanation_contract(payload)


def test_python_sink_filters_sensitive_explanation_fields_and_fails_closed():
    base = _contract_diag()
    issue = replace(
        base.issues[0],
        plain_language_meaning="access_token=access-token-secret-sentinel",
        claim_level="unknown_weak_level",
        expected_result="../private-result.json",
        root_causes=[RootCause("physical", "Bearer sk-live-secret-sentinel")],
        prescriptions=[Prescription(
            scenario="~/private-scenario.json",
            reason="api_key=api-key-secret-sentinel",
            cue="file:///private/cue.txt",
            purpose="refresh_token=refresh-token-secret-sentinel",
            target_metrics=["sparc", "../private-metric.json"],
            expected_direction=["sparc ↑", "Bearer sk-direction-secret"],
            retest_after="/private/retest.txt",
            stop_or_adjust_rule="secret=stop-secret-sentinel",
            source_level="unknown_weak_level",
        )],
    )
    diagnosis = replace(
        base,
        profile=ProfileMatch(
            "decel_jitter",
            "api_key=profile-secret-sentinel",
            0.8,
            ["safe-tag", "../private-tag.json"],
        ),
        issues=[issue],
    )

    payload = diagnosis_payload(diagnosis)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["issues"][0]["claim_level"] == "experimental"
    assert payload["issues"][0]["prescriptions"][0]["source_level"] == "experimental"
    assert payload["profile"]["secondary_tags"] == ["safe-tag"]
    for forbidden in (
        "access-token-secret-sentinel",
        "sk-live-secret-sentinel",
        "api-key-secret-sentinel",
        "refresh-token-secret-sentinel",
        "stop-secret-sentinel",
        "profile-secret-sentinel",
        "private-result.json",
        "private-scenario.json",
        "private-metric.json",
        "private-tag.json",
    ):
        assert forbidden not in serialized


def test_registry_payload_omits_forbidden_prescription_sections_for_low_capability_entry():
    from kovaak_tracker.coach.knowledge_registry import load_registry

    registry = load_registry(registry_version="2026-07-29.v4")
    explanation_only = next(
        entry for entry in registry["entries"]
        if entry["entry_id"] == "community.linear-clicking-strategy"
    )

    payload = agent_tools._entry_payload(explanation_only, registry)

    record = payload["coaching_record"]
    assert record["observation_refs"] == []
    assert "cue" not in record
    assert "dose_guardrail" not in record
    assert "matched_retest" not in record
    assert "near_transfer_retest" not in record
    assert "stop_adjust_rule" not in record


def test_alias_mapped_pipeline_signals_resolve_through_fetch_knowledge():
    """Audit M2: a signal advice.py emits that the alias layer references must
    be hittable through the coach_fetch_knowledge tool, and no alias canonical
    may dangle without a declaring entry."""
    from kovaak_tracker.advice import _SIGNAL_METRICS
    from kovaak_tracker.coach.knowledge_registry import load_registry
    from kovaak_tracker.coach.agent_tools import make_fetch_knowledge

    data = load_registry()
    alias_keys = set(data["signal_aliases"])
    alias_values = set(data["signal_aliases"].values())
    active_signals = {
        signal
        for entry in data["entries"] if entry["status"] == "active"
        for signal in entry["signals"]
    }
    assert alias_values <= active_signals

    fetch = make_fetch_knowledge()
    mapped = [
        signal for signal in _SIGNAL_METRICS
        if signal in alias_keys or signal in alias_values
    ]
    assert mapped  # the invariant is only meaningful for mapped signals
    for signal in mapped:
        result = fetch(signal)
        assert "error" not in result, signal
    throughput = fetch("throughput below reference")
    assert "error" not in throughput
    assert throughput["signal"] == "throughput below reference"
    assert any(
        entry["entry_ref"] == "knowledge:research.speed-precision.fitts@1"
        for entry in throughput["entries"]
    )


def test_signal_enumeration_excludes_prescription_entries(monkeypatch):
    """Audit P2: signals advertised by list_signals / fetch_knowledge must match
    what query_registry can actually return. prescription.* entries are never
    returned, so their signals must not be advertised as known/valid either."""
    from kovaak_tracker.coach.agent_tools import make_fetch_knowledge, make_list_signals

    data = {
        "signal_aliases": {},
        "entries": [
            {"entry_id": "research.normal", "status": "active",
             "signals": ["shared signal"]},
            {"entry_id": "prescription.p999.audit", "status": "active",
             "signals": ["prescription only signal"]},
            {"entry_id": "research.retired", "status": "retired",
             "signals": ["retired signal"]},
        ],
    }
    monkeypatch.setattr(agent_tools, "load_registry", lambda: data)
    # The error path is reached before query_registry returns entries; stub it so
    # the synthetic registry need not satisfy the full schema validation.
    monkeypatch.setattr(agent_tools, "query_registry", lambda *args, **kwargs: [])

    known = make_list_signals(_diag())()["knowledge_known_signals"]
    assert "shared signal" in known
    assert "prescription only signal" not in known
    assert "retired signal" not in known

    result = make_fetch_knowledge()("garbage")
    assert result["error"] == "unknown signal"
    assert "shared signal" in result["valid_signals"]
    assert "prescription only signal" not in result["valid_signals"]
    assert "retired signal" not in result["valid_signals"]
