from __future__ import annotations

import pytest

from webapp.backend.coach_problem_compiler import compile_coach_problem


_REGISTRY_VERSION = "2026-08-06.v5"


_CASES = (
    ("static_clicking", "reverse_ratio high", "metric:reverse_ratio", "event.flick", "knowledge:static.flicking-terminal-control@2", "terminal_control"),
    ("dynamic_clicking", "dynamic click error high", "metric:normalized_click_error", "event.dynamic_click", "knowledge:dynamic.click-error-and-acquisition@2", "dynamic_click_confirmation"),
    ("dynamic_clicking", "dynamic acquisition slow", "metric:acquisition_time_ms", "event.dynamic_click", "knowledge:dynamic.click-error-and-acquisition@2", "target_acquisition_slow"),
    ("dynamic_clicking", "relative velocity mismatch", "metric:relative_velocity_gain", "field.relative_velocity", "knowledge:dynamic.speed-matching-and-reading@2", "speed_matching_and_reading"),
    ("continuous_tracking", "speed mismatch high", "metric:tracking_error", "episode.tracking", "knowledge:tracking.predictable-speed-matching@2", "speed_matching_and_reading"),
    ("continuous_tracking", "accel mismatch high", "metric:change_response", "event.target_change", "knowledge:tracking.reactive-change-response@2", "change_response_and_reacquisition"),
    ("continuous_tracking", "correction burden high", "metric:correction_burden", "episode.tracking", "knowledge:tracking.control-smoothness@2", "continuous_correction_burden"),
    ("target_switching", "switch transition slow", "metric:target_switching.transition_time_ms", "event.switch_chain", "knowledge:switching.transition-and-arrival@2", "switch_transition_and_arrival"),
)


def _context(
    family: str,
    signal: str,
    metric_ref: str,
    observation_ref: str,
    knowledge_ref: str,
    *,
    context_ref: str = "context:one",
    analysis_ref: str = "analysis:1",
    priority: int = 1,
    explicit: bool = False,
    scenario_profile_ref: str = "scenario:test.shared@1",
) -> dict:
    issue = {
        "signal": signal,
        "priority": priority,
        "plain_language_meaning": f"Observed {signal} in a matched condition.",
        "metric_refs": [metric_ref],
        "observation_ref": observation_ref,
        "knowledge_registry_version": _REGISTRY_VERSION,
        "knowledge_entry_refs": [knowledge_ref],
        "root_causes": [
            {"level": "physical", "text": "The player grips the mouse too tightly."},
            {"level": "training", "text": "Timing the final correction may be worth testing."},
        ],
    }
    return {
        "context_ref": context_ref,
        "kind": "issue" if explicit else "analysis",
        "analysis_ref": analysis_ref,
        "target_ref": f"{analysis_ref}:issue:0" if explicit else analysis_ref,
        "projection": {
            "scenario": {
                "scenario_profile_ref": scenario_profile_ref,
                "analyzer_refs": [f"{family}.v1"],
                "support_status": "supported",
            },
            "diagnosis": {
                "issues": [issue],
                "summary": {
                    metric_ref: {
                        "classification": "deterministic",
                        "value": 1.0,
                    },
                },
                "meta": {"summary_type": family},
            },
        },
    }


@pytest.mark.parametrize(("family", "signal", "metric_ref", "observation_ref", "knowledge_ref", "problem_key"), _CASES)
def test_compiler_covers_each_supported_functional_problem(
    family, signal, metric_ref, observation_ref, knowledge_ref, problem_key,
):
    result = compile_coach_problem({
        "schema_version": "coach_turn_context.v1",
        "contexts": [_context(family, signal, metric_ref, observation_ref, knowledge_ref)],
    })

    assert result is not None
    assert result["problem_id"] == problem_key
    assert result["family"] == family
    assert result["evidence_strength"] == "limited"
    assert any("\u4e00" <= char <= "\u9fff" for char in result["problem_label"])
    assert len(result["supporting_evidence"]) == 1
    assert result["primary_hypothesis"].startswith("\u53ef\u80fd")
    assert all("\u53ef\u80fd" in item for item in result["alternative_hypotheses"])
    assert len(result["alternative_hypotheses"]) <= 2
    assert result["counterevidence_status"] == "not_observed"
    assert result["counterevidence"] == []
    assert result["discriminator"] == {
        "kind": "question",
        "prompt": result["discriminator"]["prompt"],
    }


def test_compiler_groups_same_family_signals_and_uses_profile_counterevidence():
    speed = _context(*_CASES[3][:5], context_ref="context:speed", analysis_ref="analysis:20")
    post_change = _context(
        "dynamic_clicking",
        "post change error high",
        "metric:post_change_error",
        "event.target_change",
        "knowledge:dynamic.speed-matching-and-reading@2",
        context_ref="context:post-change",
        analysis_ref="analysis:21",
    )
    profile = {
        "schema_version": "aiming_profile.v1",
        "profile_ref": "profile-aiming:owner-a",
        "status": "clean",
        "dimensions": [{
            "dimension_key": "dynamic_clicking.relative_velocity",
            "observation_count": 3,
            "confidence": "high",
            "counterexample_refs": ["analysis:8"],
        }],
    }

    result = compile_coach_problem(
        {"schema_version": "coach_turn_context.v1", "contexts": [speed, post_change]},
        profile=profile,
    )

    assert result is not None
    assert result["problem_id"] == "speed_matching_and_reading"
    assert result["evidence_strength"] == "supported"
    assert [item["context_ref"] for item in result["supporting_evidence"]] == [
        "context:speed", "context:post-change",
    ]
    assert result["counterevidence_status"] == "observed"
    assert result["counterevidence"] == [{
        "kind": "profile_counterexample",
        "refs": ["analysis:8"],
    }]
    assert "\u63e1" not in result["primary_hypothesis"]
    assert "\u9f20\u6807" not in "".join(result["alternative_hypotheses"])


def test_explicit_issue_wins_without_merging_other_families():
    explicit = _context(*_CASES[1][:5], explicit=True, priority=5)
    tracking = _context(
        *_CASES[4][:5],
        context_ref="context:tracking",
        analysis_ref="analysis:2",
        priority=1,
    )

    result = compile_coach_problem({
        "schema_version": "coach_turn_context.v1",
        "contexts": [explicit, tracking],
    })

    assert result is not None
    assert result["family"] == "dynamic_clicking"
    assert result["problem_id"] == "dynamic_click_confirmation"
    assert [item["context_ref"] for item in result["supporting_evidence"]] == ["context:one"]


def test_compiler_fails_closed_when_no_deterministic_evidence_is_available():
    context = _context(*_CASES[0][:5])
    issue = context["projection"]["diagnosis"]["issues"][0]
    metric_ref = issue["metric_refs"][0]
    context["projection"]["diagnosis"]["summary"][metric_ref]["classification"] = "experimental"

    assert compile_coach_problem({
        "schema_version": "coach_turn_context.v1",
        "contexts": [context],
    }) is None


def test_compiler_marks_two_signals_in_one_analysis_as_supported_not_repeated():
    speed = _context(*_CASES[3][:5], context_ref="context:speed", analysis_ref="analysis:20")
    post_change = _context(
        "dynamic_clicking",
        "post change error high",
        "metric:post_change_error",
        "event.target_change",
        "knowledge:dynamic.speed-matching-and-reading@2",
        context_ref="context:post-change",
        analysis_ref="analysis:20",
    )

    result = compile_coach_problem({
        "schema_version": "coach_turn_context.v1",
        "contexts": [speed, post_change],
    })

    assert result is not None
    assert result["evidence_strength"] == "supported"


def test_repeated_problem_outweighs_a_single_higher_priority_issue():
    repeated_one = _context(*_CASES[3][:5], context_ref="context:one", analysis_ref="analysis:20", priority=3)
    repeated_two = _context(*_CASES[3][:5], context_ref="context:two", analysis_ref="analysis:21", priority=3)
    single = _context(
        *_CASES[5][:5],
        context_ref="context:single",
        analysis_ref="analysis:22",
        priority=1,
        scenario_profile_ref="scenario:test.other@1",
    )

    result = compile_coach_problem({
        "schema_version": "coach_turn_context.v1",
        "contexts": [single, repeated_one, repeated_two],
    })

    assert result is not None
    assert result["family"] == "dynamic_clicking"
    assert result["problem_id"] == "speed_matching_and_reading"
    assert result["evidence_strength"] == "repeated"


def test_explicit_learner_goal_only_breaks_a_compiler_tie():
    terminal = _context(*_CASES[0][:5], context_ref="context:terminal", analysis_ref="analysis:30", priority=2)
    speed = _context(*_CASES[3][:5], context_ref="context:speed", analysis_ref="analysis:31", priority=2)

    result = compile_coach_problem({
        "schema_version": "coach_turn_context.v1",
        "contexts": [terminal, speed],
        "learner_context": {
            "player_problem": "我主要想减少实战第一枪后的刹车和补修正",
            "desired_outcome": "收尾更稳定",
            "practice_intent": "main_game_transfer",
            "constraints": [],
        },
    })

    assert result is not None
    assert result["problem_id"] == "terminal_control"


def test_explicit_learner_goal_does_not_override_analysis_priority():
    terminal = _context(*_CASES[0][:5], context_ref="context:terminal", analysis_ref="analysis:30", priority=3)
    speed = _context(*_CASES[3][:5], context_ref="context:speed", analysis_ref="analysis:31", priority=1)

    result = compile_coach_problem({
        "schema_version": "coach_turn_context.v1",
        "contexts": [terminal, speed],
        "learner_context": {
            "player_problem": "我主要想减少实战第一枪后的刹车和补修正",
            "desired_outcome": "收尾更稳定",
            "practice_intent": "main_game_transfer",
            "constraints": [],
        },
    })

    assert result is not None
    assert result["problem_id"] == "speed_matching_and_reading"


def test_compiler_does_not_merge_matching_problem_keys_across_families():
    dynamic = _context(*_CASES[3][:5], priority=2)
    tracking = _context(
        *_CASES[4][:5],
        context_ref="context:tracking",
        analysis_ref="analysis:2",
        priority=1,
    )

    result = compile_coach_problem({
        "schema_version": "coach_turn_context.v1",
        "contexts": [dynamic, tracking],
    })

    assert result is not None
    assert result["family"] == "continuous_tracking"
    assert len(result["supporting_evidence"]) == 1
