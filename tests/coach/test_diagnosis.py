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
