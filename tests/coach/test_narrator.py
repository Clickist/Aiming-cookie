import json

from kovaak_tracker.coach.narrator import (
    generate_narration, build_user_prompt, SYSTEM_PROMPT,
)
from kovaak_tracker.coach.diagnosis import CoachDiagnosis, ProfileMatch


class _Fake:
    def __init__(self):
        self.calls = []

    def generate(self, system, user):
        self.calls.append((system, user))
        return "讲解文本"


def _diag():
    return CoachDiagnosis(
        profile=ProfileMatch("decel_jitter", "减速抖动型", 1.0, []),
        issues=[], summary={"decel_frac": {"med": 0.75}}, comparison=None,
        meta={"cm_per_360": 48.0},
    )


def test_generate_returns_backend_text():
    b = _Fake()
    out = generate_narration(_diag(), b)
    assert out == "讲解文本"
    assert b.calls[0][0] == SYSTEM_PROMPT


def test_user_prompt_contains_diagnosis_json():
    user = build_user_prompt(_diag())
    payload = json.loads(user)
    assert payload["profile"]["label"] == "减速抖动型"
    assert payload["meta"]["cm_per_360"] == 48.0


def test_system_prompt_forbids_fabrication():
    assert "不编造" in SYSTEM_PROMPT or "不要编造" in SYSTEM_PROMPT


# --- progress narration tests (Task 3) ---

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
