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
