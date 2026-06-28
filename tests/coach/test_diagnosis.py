from kovaak_tracker.coach.diagnosis import (
    RootCause, ProfileMatch, DiagnosisIssue, CoachDiagnosis,
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
