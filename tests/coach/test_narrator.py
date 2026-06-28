import json

from kovaak_tracker.coach.narrator import (
    generate_narration, build_user_prompt, build_system_prompt, BASE_SYSTEM_PROMPT,
)
from kovaak_tracker.coach.diagnosis import (
    CoachDiagnosis, ProfileMatch, DiagnosisIssue, RootCause,
)


class _Fake:
    def __init__(self):
        self.calls = []

    def generate(self, system, user):
        self.calls.append((system, user))
        return "讲解文本"


def _diag(issues=None):
    return CoachDiagnosis(
        profile=ProfileMatch("decel_jitter", "减速抖动型", 1.0, []),
        issues=issues or [], summary={"decel_frac": {"med": 0.75}}, comparison=None,
        meta={"cm_per_360": 48.0},
    )


def _issue(signal):
    return DiagnosisIssue(
        signal=signal, severity="fix",
        root_causes=[RootCause("symptom", "x")], prescriptions=[],
        priority=1, priority_reason="x",
    )


def test_generate_returns_backend_text():
    b = _Fake()
    out = generate_narration(_diag(), b)
    assert out == "讲解文本"
    # 无 issues 时退化成 BASE 框架
    assert b.calls[0][0] == BASE_SYSTEM_PROMPT


def test_user_prompt_contains_diagnosis_json():
    user = build_user_prompt(_diag())
    payload = json.loads(user)
    assert payload["profile"]["label"] == "减速抖动型"
    assert payload["meta"]["cm_per_360"] == 48.0


def test_base_system_prompt_forbids_fabrication():
    assert "不编造" in BASE_SYSTEM_PROMPT or "不要编造" in BASE_SYSTEM_PROMPT


def test_build_system_prompt_is_progressive():
    """渐进式：只注入触发的 signal 知识，未触发的不进入。"""
    diag = _diag(issues=[_issue("sparc low")])
    prompt = build_system_prompt(diag)
    # 触发的 signal 知识进入
    assert "sparc low" in prompt
    assert "暴露疗法" in prompt          # sparc low 的 cue
    # 未触发的 signal 知识不进入
    assert "underflick" not in prompt    # reverse_ratio high 的 cue，未触发
    assert "Bardoz" not in prompt and "bardOZ" not in prompt  # two-stage 的，未触发


def test_build_system_prompt_empty_issues_is_base():
    """无 issues 时退化为纯 BASE，不带知识块。"""
    prompt = build_system_prompt(_diag())
    assert prompt == BASE_SYSTEM_PROMPT
    assert "仅本次诊断触发的信号" not in prompt  # 注入块标题特征；BASE 无此串


# --- progress narration tests ---

from kovaak_tracker.coach.narrator import (
    generate_progress_narration, PROGRESS_SYSTEM_PROMPT,
)


class _ProgressFake:
    def __init__(self):
        self.calls = []

    def generate(self, system, user):
        self.calls.append((system, user))
        return "进步解读文本"


def test_progress_narration_returns_backend_text():
    b = _ProgressFake()
    trend = {"linearity": [("t1", 0.2), ("t2", 0.17)]}
    comparison = [{"metric": "linearity", "current": 0.17, "baseline": 0.20, "verdict": "better"}]
    out = generate_progress_narration(trend, comparison, b)
    assert out == "进步解读文本"
    assert b.calls[0][0] == PROGRESS_SYSTEM_PROMPT


def test_progress_prompt_contains_data_json():
    b = _ProgressFake()
    trend = {"sparc": [("t1", -7.0)]}
    comparison = [{"metric": "sparc", "current": -5.0, "baseline": -7.0, "verdict": "better"}]
    generate_progress_narration(trend, comparison, b)
    payload = json.loads(b.calls[0][1])
    assert "trend" in payload and "comparison" in payload
    assert payload["comparison"][0]["metric"] == "sparc"


def test_progress_system_prompt_forbids_fabrication():
    assert "不编造" in PROGRESS_SYSTEM_PROMPT or "不要编造" in PROGRESS_SYSTEM_PROMPT


# --- plan narration tests (④ Task 3) ---

from kovaak_tracker.coach.planning import TrainingPlan, PlanAdjustment
from kovaak_tracker.coach.narrator import (
    generate_plan_narration, build_plan_user_prompt, PLAN_SYSTEM_PROMPT,
)


def _plan():
    return TrainingPlan(
        focus_metrics=["sparc"],
        adjustments=[PlanAdjustment(
            kind="interleave", target_metric="sparc", scenarios=[],
            reason="交错多场景长期保留更好。感觉进步快≠长期记住。",
            evidence="§4.2 渐进 hybrid",
        )],
        schedule_note="每周复测 2-3 次、间隔练习。",
        evidence_anchors=["§4.2 渐进 hybrid"],
        notes=[],
    )


def test_plan_system_prompt_has_anti_hallucination():
    assert "不要编造" in PLAN_SYSTEM_PROMPT or "铁律" in PLAN_SYSTEM_PROMPT


def test_build_plan_user_prompt_contains_plan_data():
    payload = build_plan_user_prompt(_plan())
    assert "sparc" in payload
    assert "interleave" in payload


def test_generate_plan_narration_calls_backend():
    class _Mock:
        def __init__(self):
            self.calls = 0
        def generate(self, system, user):
            self.calls += 1
            assert "教练" in system or "flicking" in system
            return "计划讲解文本"
    m = _Mock()
    out = generate_plan_narration(_plan(), m)
    assert out == "计划讲解文本" and m.calls == 1
