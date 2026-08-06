import pytest

from kovaak_tracker.coach.diagnosis import (
    RootCause, ProfileMatch, DiagnosisIssue, CoachDiagnosis,
    resolve_candidate_knowledge_refs,
)
from kovaak_tracker.coach import profiles


def test_rootcause_construct():
    rc = RootCause("symptom", "x")
    assert rc.level == "symptom" and rc.text == "x"


def test_profiles_cover_all_root_cause_signals():
    # every archetype condition signal must have a ROOT_CAUSES entry
    for arch in profiles.ARCHETYPES:
        for sig in arch["conditions"]:
            assert sig in profiles.ROOT_CAUSES, f"missing root cause for {sig}"


def test_diagnosis_frozen():
    d = CoachDiagnosis(ProfileMatch("x", "y", 0.5), [], {})
    try:
        d.profile = ProfileMatch("a", "b", 0.1)  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass


def test_issue_has_one_primary_and_at_most_two_supporting_evidence_segments():
    issue = DiagnosisIssue(
        signal="sparc low",
        severity="info",
        root_causes=[],
        prescriptions=[],
        priority=1,
        priority_reason="fixture",
        primary_evidence_segment_ref="analysis:7:segment:worst:1",
        supporting_evidence_segment_refs=[
            "analysis:7:segment:typical:1",
            "analysis:7:segment:improved:1",
        ],
    )

    assert issue.primary_evidence_segment_ref == "analysis:7:segment:worst:1"
    assert issue.supporting_evidence_segment_refs == [
        "analysis:7:segment:typical:1",
        "analysis:7:segment:improved:1",
    ]

    with pytest.raises(ValueError, match="at most two"):
        DiagnosisIssue(
            signal="sparc low",
            severity="info",
            root_causes=[],
            prescriptions=[],
            priority=1,
            priority_reason="fixture",
            primary_evidence_segment_ref="analysis:7:segment:worst:1",
            supporting_evidence_segment_refs=[
                "analysis:7:segment:typical:1",
                "analysis:7:segment:improved:1",
                "analysis:7:segment:extra:1",
            ],
        )


from kovaak_tracker.coach.diagnosis import build_diagnosis
from kovaak_tracker.advice import Finding, Prescription


def _f(signal, severity="fix"):
    return Finding(signal=signal, severity=severity, diagnosis="d",
                   prescriptions=[Prescription("pasu", "r")])


def test_match_long_decel_profile():
    findings = [_f("decel_frac high"), _f("peak_position low")]
    d = build_diagnosis(findings, {}, None, {})
    assert d.profile.archetype_id == "long_decel"
    assert d.profile.confidence == 1.0


def test_partial_evidence_cannot_receive_default_fluid_profile():
    diagnosis = build_diagnosis(
        [],
        {},
        None,
        {"summary_type": "flicking", "quality_status": "limited"},
    )

    assert diagnosis.profile.archetype_id == "unclassified"
    assert diagnosis.profile.confidence == 0.0


def test_secondary_tags_collect_other_hits():
    findings = [_f("decel_frac high"), _f("sparc low")]
    d = build_diagnosis(findings, {}, None, {})
    assert "减速抖动型" in d.profile.secondary_tags


def test_root_cause_chain_three_layers():
    findings = [_f("sparc low")]
    d = build_diagnosis(findings, {}, None, {})
    levels = [rc.level for rc in d.issues[0].root_causes]
    assert levels == ["symptom", "physical", "training"]


def test_priority_orders_by_severity():
    findings = [_f("x", "info"), _f("sparc low", "fix"), _f("y", "watch")]
    d = build_diagnosis(findings, {}, None, {})
    sev = [i.severity for i in d.issues]
    assert sev[0] == "fix" and sev[1] == "watch" and sev[2] == "info"
    assert d.issues[0].priority == 1


def test_unknown_signal_falls_back_to_symptom_only():
    findings = [_f("totally unknown signal")]
    d = build_diagnosis(findings, {}, None, {})
    assert len(d.issues[0].root_causes) == 1
    assert d.issues[0].root_causes[0].level == "symptom"


def test_issue_exposes_explanation_and_training_verification_contract():
    finding = Finding(
        signal="sparc low",
        severity="info",
        diagnosis="减速速度轮廓含较多高频变化",
        prescriptions=[
            Prescription(
                scenario="pasu",
                reason="练习连续减速",
                cue="接近目标时让速度连续下降，不要硬停",
                purpose="减少减速末段的速度波动",
                target_metrics=["sparc", "reverse_ratio"],
                expected_direction=["sparc ↑", "reverse_ratio ↓"],
                retest_after="同场景完成一组后复测",
                stop_or_adjust_rule="若准确率明显下降，降低速度或放大目标",
                source_level="community_consensus",
            )
        ],
        claim_level="experimental",
        metric_refs=["sparc", "reverse_ratio"],
        limitations=["threshold_requires_product_calibration"],
        plain_language_meaning="你的减速过程不够连续",
        expected_result="减速更连续，反向修正减少",
        verification={
            "success_signals": ["sparc ↑", "reverse_ratio ↓"],
            "insufficient_evidence_behavior": "没有足够样本时只记录，不判停滞",
        },
    )
    diagnosis = build_diagnosis([finding], {}, None, {})
    issue = diagnosis.issues[0]

    assert issue.claim_level == "experimental"
    assert issue.priority_reason == "本次优先观察项"
    assert "experimental" not in issue.priority_reason
    assert issue.metric_refs == ["sparc", "reverse_ratio"]
    assert issue.plain_language_meaning == "你的减速过程不够连续"
    assert issue.expected_result == "减速更连续，反向修正减少"
    assert issue.verification["success_signals"] == ["sparc ↑", "reverse_ratio ↓"]
    prescription = issue.prescriptions[0]
    assert prescription.target_metrics == ["sparc", "reverse_ratio"]
    assert prescription.expected_direction == ["sparc ↑", "reverse_ratio ↓"]
    assert prescription.source_level == "community_consensus"


def test_profile_and_root_cause_labels_stay_observational():
    finding = Finding(
        signal="peak_speed below reference",
        severity="info",
        diagnosis="峰值速度低于可比参考，身体原因未被输入数据直接测量",
        claim_level="experimental",
    )

    diagnosis = build_diagnosis([finding], {}, None, {})

    assert diagnosis.profile.label == "参考速度效率偏低型"
    assert "发力不足" not in str(diagnosis.issues[0].root_causes)
    assert "未被输入数据直接测量" in diagnosis.issues[0].root_causes[1].text


def test_candidate_knowledge_refs_only_annotate_an_existing_analyzer_fact():
    refs = resolve_candidate_knowledge_refs(
        issue_signal="post change error high",
        metric_refs=["metric:post_change_error"],
    )

    assert refs.registry_version == "2026-08-06.v6"
    assert 1 <= len(refs.entry_refs) <= 3
    assert all(ref.startswith("knowledge:") for ref in refs.entry_refs)
    assert not hasattr(refs, "severity")
    assert not hasattr(refs, "prescriptions")

    unknown = resolve_candidate_knowledge_refs(
        issue_signal="unimplemented measured fact",
        metric_refs=[],
    )
    assert unknown.entry_refs == []


def test_static_registry_backed_issue_carries_exact_observation_and_knowledge_refs():
    diagnosis = build_diagnosis([
        Finding(
            signal="reverse_ratio high",
            severity="info",
            diagnosis="fixture",
            metric_refs=["reverse_ratio"],
        ),
    ], {}, None, {})

    issue = diagnosis.issues[0]
    assert issue.observation_ref == "metric.terminal_control"
    assert issue.knowledge_registry_version == "2026-08-06.v6"
    assert issue.knowledge_entry_refs == [
        "knowledge:static.flicking-terminal-control@2"
    ]


def test_static_issue_without_registry_coverage_stays_visible_without_refs():
    diagnosis = build_diagnosis([
        Finding(
            signal="decel_frac high",
            severity="info",
            diagnosis="fixture",
            metric_refs=["decel_frac"],
        ),
    ], {}, None, {})

    issue = diagnosis.issues[0]
    assert issue.signal == "decel_frac high"
    assert issue.observation_ref is None
    assert issue.knowledge_registry_version is None
    assert issue.knowledge_entry_refs == []


def test_static_clicking_copy_separates_observation_boundary_and_training_direction():
    from kovaak_tracker.advice import advise

    findings = advise({
        "decel_frac": {"med": 0.8},
        "reverse_ratio": {"med": 0.3},
        "submovement_overlap": {"med": 0.2},
    })
    diagnosis = build_diagnosis(findings, {}, None, {})
    issues = {issue.signal: issue for issue in diagnosis.issues}

    assert {issue.claim_level for issue in issues.values()} == {"experimental"}
    assert all(issue.priority_reason == "本次优先观察项" for issue in issues.values())

    assert issues["reverse_ratio high"].plain_language_meaning == "移动收尾时出现了较多反向修正"
    reverse = issues["reverse_ratio high"].root_causes
    assert reverse[0].text == "减速段反复修正"
    assert reverse[1].text == "证据只能说明反向修正偏多"
    assert reverse[2].text == "把修正并入减速过程，避免停住后再二次修正"

    two_stage = issues["submovement two-stage"].root_causes
    assert two_stage[0].text == "主要移动和后续修正呈较分离的两个阶段"
    assert two_stage[1].text == "证据只能说明主要移动和后续修正较分离"
    assert two_stage[2].text == "尝试让主要移动和收尾修正保持衔接，减少停住后再单独修正"
