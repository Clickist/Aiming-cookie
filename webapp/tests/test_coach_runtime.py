from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import httpx
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


def _ok_response(reply: str = "教练回复") -> dict:
    return {
        "schema_version": "coach_runtime_turn.v0",
        "ok": True,
        "reply": reply,
        "error": None,
        "notes": [],
    }


def _ok_stdout(reply: str = "教练回复") -> str:
    return json.dumps(_ok_response(reply), ensure_ascii=False)


def _mock_httpx_post_success(reply: str = "Pi 说：练减速段"):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = _ok_response(reply)
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    return patch("webapp.backend.coach_runtime.httpx.Client", return_value=mock_cm)


def test_run_pi_coach_turn_http_success():
    messages = [{"role": "user", "content": "你好"}]
    with _mock_httpx_post_success("Pi 说：练减速段"):
        reply = run_pi_coach_turn(
            messages=messages,
            analysis_summary=None,
        )
    assert reply == "Pi 说：练减速段"


def test_run_pi_coach_turn_http_success_payload():
    messages = [{"role": "user", "content": "你好"}]
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _ok_response()
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        run_pi_coach_turn(messages=messages, analysis_summary=None, timeout_s=45)

        mock_client_cls.assert_called_once_with(timeout=45)
        call_kw = mock_client.post.call_args
        assert call_kw[0][0] == f"{config.COACH_SIDECAR_URL.rstrip('/')}/v0/turn"
        stdin_payload = call_kw[1]["json"]
        assert stdin_payload["schema_version"] == "coach_runtime_turn.v0"
        assert stdin_payload["messages"] == messages
        assert stdin_payload["analysis_summary"] is None
        assert "run_id" in stdin_payload
        assert stdin_payload["model"]["api_key_env"]


def test_run_pi_coach_turn_http_fails_fallback_subprocess():
    messages = [{"role": "user", "content": "x"}]
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("connection refused")
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=_ok_stdout("subprocess 回复"),
                stderr="",
            )
            reply = run_pi_coach_turn(messages=messages, analysis_summary=None)
    assert reply == "subprocess 回复"
    mock_run.assert_called_once()


def test_run_pi_coach_turn_http_fails_no_fallback_raises():
    messages = [{"role": "user", "content": "x"}]
    with patch(
        "webapp.backend.coach_runtime.COACH_SIDECAR_FALLBACK_SUBPROCESS",
        "0",
    ):
        with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.post.side_effect = httpx.ConnectError("down")
            mock_client_cls.return_value.__enter__.return_value = mock_client
            with pytest.raises(CoachRuntimeError, match="sidecar 不可达"):
                run_pi_coach_turn(messages=messages, analysis_summary=None)


def test_run_pi_coach_turn_http_non_200_fallback_subprocess():
    messages = [{"role": "user", "content": "x"}]
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "unavailable"
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=_ok_stdout("fallback"),
                stderr="",
            )
            reply = run_pi_coach_turn(messages=messages, analysis_summary=None)
    assert reply == "fallback"


def test_run_pi_coach_turn_ok_false_raises():
    err_body = _ok_response()
    err_body["ok"] = False
    err_body["reply"] = None
    err_body["error"] = {
        "category": "coach_runtime",
        "code": "turn_failed",
        "message": "LLM 不可用",
        "retryable": True,
    }
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = err_body
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client
        with pytest.raises(CoachRuntimeError, match="LLM 不可用"):
            run_pi_coach_turn(
                messages=[{"role": "user", "content": "x"}],
                analysis_summary=None,
            )


def test_run_pi_coach_turn_http_success_skips_subprocess():
    with _mock_httpx_post_success("仅 HTTP"):
        with patch("webapp.backend.coach_runtime.subprocess.run") as mock_run:
            reply = run_pi_coach_turn(
                messages=[{"role": "user", "content": "x"}],
                analysis_summary=None,
            )
    assert reply == "仅 HTTP"
    mock_run.assert_not_called()


def test_run_pi_coach_turn_nonzero_exit_without_ok_json_raises():
    with patch("webapp.backend.coach_runtime.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("down")
        mock_client_cls.return_value.__enter__.return_value = mock_client
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


def test_coach_runtime_timeout_config_default():
    assert config.COACH_RUNTIME_TIMEOUT_SECONDS == 120


def test_coach_sidecar_config_defaults():
    assert config.COACH_SIDECAR_URL.startswith("http://127.0.0.1:")
    assert config.COACH_SIDECAR_FALLBACK_SUBPROCESS == "1"


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