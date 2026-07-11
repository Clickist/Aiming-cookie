from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from kovaak_tracker.coach.diagnosis import (
    CoachDiagnosis,
    DiagnosisIssue,
    ProfileMatch,
    RootCause,
)
from webapp.backend import config
from webapp.backend.coach_runtime import (
    CoachRuntimeError,
    diagnosis_to_analysis_summary,
    run_pi_coach_turn,
)


def _ok_stdout(reply: str = "教练回复") -> str:
    return json.dumps(
        {
            "schema_version": "coach_runtime_turn.v0",
            "ok": True,
            "reply": reply,
            "error": None,
            "notes": [],
        },
        ensure_ascii=False,
    )


def test_run_pi_coach_turn_mock_subprocess_success():
    messages = [{"role": "user", "content": "你好"}]
    with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_ok_stdout("Pi 说：练减速段"),
            stderr="",
        )
        reply = run_pi_coach_turn(
            messages=messages,
            analysis_summary=None,
        )
    assert reply == "Pi 说：练减速段"
    mock_run.assert_called_once()
    call_kw = mock_run.call_args.kwargs
    assert call_kw["timeout"] == config.COACH_RUNTIME_TIMEOUT_SECONDS
    stdin_payload = json.loads(call_kw["input"])
    assert stdin_payload["schema_version"] == "coach_runtime_turn.v0"
    assert stdin_payload["messages"] == messages
    assert stdin_payload["analysis_summary"] is None
    assert "run_id" in stdin_payload
    assert stdin_payload["model"]["api_key_env"]


def test_run_pi_coach_turn_ok_false_raises():
    with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=json.dumps(
                {
                    "schema_version": "coach_runtime_turn.v0",
                    "ok": False,
                    "reply": None,
                    "error": {
                        "category": "coach_runtime",
                        "code": "turn_failed",
                        "message": "LLM 不可用",
                        "retryable": True,
                    },
                    "notes": [],
                }
            ),
            stderr="",
        )
        with pytest.raises(CoachRuntimeError, match="LLM 不可用"):
            run_pi_coach_turn(
                messages=[{"role": "user", "content": "x"}],
                analysis_summary=None,
            )


def test_run_pi_coach_turn_nonzero_exit_without_ok_json_raises():
    with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="node: module not found",
        )
        with pytest.raises(CoachRuntimeError, match="exit 2"):
            run_pi_coach_turn(
                messages=[{"role": "user", "content": "x"}],
                analysis_summary=None,
            )


def test_run_pi_coach_turn_custom_timeout():
    with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_ok_stdout(),
            stderr="",
        )
        run_pi_coach_turn(
            messages=[{"role": "user", "content": "x"}],
            analysis_summary=None,
            timeout_s=45,
        )
    assert mock_run.call_args.kwargs["timeout"] == 45


def test_coach_runtime_timeout_config_default():
    assert config.COACH_RUNTIME_TIMEOUT_SECONDS == 120


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