"""Client for Node Pi coach-runtime (HTTP sidecar + subprocess fallback)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

from .config import (
    COACH_RUNTIME_RUN_TURN,
    COACH_RUNTIME_TIMEOUT_SECONDS,
    COACH_RUNTIME_TSX_LOADER,
    COACH_SIDECAR_FALLBACK_SUBPROCESS,
    COACH_SIDECAR_URL,
    LLM_PROVIDER,
    PI_SOURCE_DIR,
)

_log = logging.getLogger(__name__)

COACH_RUNTIME_TURN_SCHEMA = "coach_runtime_turn.v0"
_DEFAULT_USER_ID = "dev"
_PROVIDERS_JSON = Path(__file__).resolve().parents[2] / "kovaak_tracker" / "coach" / "providers.json"


class CoachRuntimeError(RuntimeError):
    """Pi coach-runtime failed (sidecar or subprocess) or returned ok=false."""


def _sidecar_fallback_enabled() -> bool:
    return COACH_SIDECAR_FALLBACK_SUBPROCESS.lower() not in ("0", "false", "no")


def _load_provider_turn_model() -> dict[str, str]:
    with _PROVIDERS_JSON.open(encoding="utf-8") as f:
        cfg = json.load(f)
    if LLM_PROVIDER not in cfg:
        raise CoachRuntimeError(
            f"LLM provider {LLM_PROVIDER!r} missing in providers.json"
        )
    p = cfg[LLM_PROVIDER]
    return {
        "base_url": str(p.get("base_url", "https://api.deepseek.com")),
        "api_key_env": str(p.get("api_key_env", "DEEPSEEK_API_KEY")),
        "model_id": str(p.get("model", "deepseek-chat")),
    }


def diagnosis_to_analysis_summary(diagnosis: Any) -> str | None:
    """Compatibility name: serialize the canonical allow-list context exactly."""
    try:
        from .coach_context import (
            coerce_coach_diagnostic_context,
            serialize_coach_diagnostic_context,
        )

        return serialize_coach_diagnostic_context(
            coerce_coach_diagnostic_context(diagnosis)
        )
    except Exception:
        _log.debug("diagnosis_to_analysis_summary failed", exc_info=True)
        return None


def _build_turn_request(
    *,
    messages: Sequence[Mapping[str, str]],
    analysis_summary: str | None,
    system_prompt: str | None,
) -> dict[str, Any]:
    normalized_messages: list[dict[str, str]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant", "system") or not isinstance(content, str):
            raise CoachRuntimeError("messages 项须含 role(user|assistant|system) 与 content 字符串")
        normalized_messages.append({"role": role, "content": content})
    if not normalized_messages:
        raise CoachRuntimeError("messages 不能为空")
    payload: dict[str, Any] = {
        "schema_version": COACH_RUNTIME_TURN_SCHEMA,
        "run_id": str(uuid.uuid4()),
        "user_id": _DEFAULT_USER_ID,
        "messages": normalized_messages,
        "analysis_summary": analysis_summary,
        "model": _load_provider_turn_model(),
    }
    if system_prompt is not None:
        payload["system_prompt"] = system_prompt
    return payload


def _validate_turn_response(
    response: Mapping[str, Any],
    *,
    exit_code: int | None = None,
) -> str:
    if response.get("schema_version") != COACH_RUNTIME_TURN_SCHEMA:
        raise CoachRuntimeError(
            f"不支持的 schema_version: {response.get('schema_version')!r}"
        )
    if not response.get("ok"):
        err = response.get("error") or {}
        if isinstance(err, Mapping):
            message = str(err.get("message") or err.get("code") or "unknown error")
        else:
            message = "coach-runtime 返回 ok=false"
        if exit_code is not None and exit_code != 0:
            message = f"{message} (exit {exit_code})"
        raise CoachRuntimeError(message)

    reply = response.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise CoachRuntimeError("coach-runtime 成功但 reply 为空")
    return reply


def _parse_turn_response_stdout(stdout: str) -> dict[str, Any]:
    line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if not line:
        raise CoachRuntimeError("coach-runtime stdout 为空")
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as e:
        raise CoachRuntimeError(f"coach-runtime stdout 非 JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise CoachRuntimeError("coach-runtime 响应须为 JSON 对象")
    return parsed


def _subprocess_command() -> list[str]:
    if not COACH_RUNTIME_RUN_TURN.is_file():
        raise CoachRuntimeError(f"run-turn 入口不存在: {COACH_RUNTIME_RUN_TURN}")
    if not COACH_RUNTIME_TSX_LOADER.is_file():
        raise CoachRuntimeError(
            f"tsx loader 不存在: {COACH_RUNTIME_TSX_LOADER} "
            "(请在 third_party/pi 安装依赖)"
        )
    return [
        "node",
        f"--import={COACH_RUNTIME_TSX_LOADER}",
        str(COACH_RUNTIME_RUN_TURN),
    ]


def _post_turn_to_sidecar(request: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    url = f"{COACH_SIDECAR_URL.rstrip('/')}/v0/turn"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=request)
    except httpx.HTTPError as e:
        raise CoachRuntimeError(f"sidecar 不可达: {e}") from e

    if resp.status_code != 200:
        body_preview = (resp.text or "")[:200]
        raise CoachRuntimeError(
            f"sidecar HTTP {resp.status_code}"
            + (f": {body_preview}" if body_preview else "")
        )

    try:
        parsed = resp.json()
    except json.JSONDecodeError as e:
        raise CoachRuntimeError(f"sidecar 响应非 JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise CoachRuntimeError("sidecar 响应须为 JSON 对象")
    return parsed


def _run_turn_via_subprocess(
    request: dict[str, Any],
    timeout_s: int,
) -> dict[str, Any]:
    cmd = _subprocess_command()
    env = {**os.environ, "PI_SOURCE_DIR": str(PI_SOURCE_DIR)}
    input_line = json.dumps(request, ensure_ascii=False)

    try:
        completed = subprocess.run(
            cmd,
            input=input_line,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise CoachRuntimeError(
            f"coach-runtime 超时（>{timeout_s}s）"
        ) from e
    except OSError as e:
        raise CoachRuntimeError(f"无法启动 coach-runtime: {e}") from e

    if completed.returncode != 0 and not completed.stdout.strip():
        stderr = (completed.stderr or "").strip()
        raise CoachRuntimeError(
            f"coach-runtime exit {completed.returncode}"
            + (f": {stderr}" if stderr else "")
        )

    response = _parse_turn_response_stdout(completed.stdout)
    response["_exit_code"] = completed.returncode
    return response


def run_pi_coach_turn(
    *,
    messages: Sequence[Mapping[str, str]],
    analysis_summary: str | None,
    system_prompt: str | None = None,
    timeout_s: int | None = None,
) -> str:
    """组装 coach_runtime_turn.v0，优先 HTTP sidecar，失败可回退 subprocess。"""
    request = _build_turn_request(
        messages=messages,
        analysis_summary=analysis_summary,
        system_prompt=system_prompt,
    )
    timeout = (
        timeout_s if timeout_s is not None else COACH_RUNTIME_TIMEOUT_SECONDS
    )

    response: dict[str, Any] | None = None
    exit_code: int | None = None

    try:
        response = _post_turn_to_sidecar(request, timeout)
    except CoachRuntimeError as e:
        if not _sidecar_fallback_enabled():
            raise
        _log.warning("coach sidecar unavailable, subprocess fallback: %s", e)

    if response is None:
        response = _run_turn_via_subprocess(request, timeout)
        exit_code = response.pop("_exit_code", None)

    return _validate_turn_response(response, exit_code=exit_code)
