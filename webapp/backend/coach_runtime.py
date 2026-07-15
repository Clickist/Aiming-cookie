"""Client for the local Pi coach sidecar and its provider catalog."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

import httpx

from . import config
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

COACH_RUNTIME_TURN_SCHEMA_V0 = "coach_runtime_turn.v0"
COACH_RUNTIME_TURN_SCHEMA_V1 = "coach_runtime_turn.v1"
COACH_RUNTIME_TURN_SCHEMA = COACH_RUNTIME_TURN_SCHEMA_V1
_DEFAULT_USER_ID = "dev"  # compatibility-only legacy callers
_PROVIDERS_JSON = Path(__file__).resolve().parents[2] / "kovaak_tracker" / "coach" / "providers.json"


class CoachRuntimeError(RuntimeError):
    """Pi coach-runtime failed (sidecar or subprocess) or returned ok=false."""

    def __init__(
        self,
        message: str,
        *,
        tool_events: Sequence[Mapping[str, Any]] = (),
        side_effects_possible: bool = False,
    ) -> None:
        super().__init__(message)
        self.tool_events = [dict(event) for event in tool_events]
        self.side_effects_possible = side_effects_possible


class ProviderUnconfiguredError(CoachRuntimeError):
    """The owner has no usable selected Provider profile."""


@dataclass(frozen=True)
class PiCoachTurnResult:
    reply: str
    notes: list[str]
    tool_events: list[dict[str, Any]]


def _sidecar_fallback_enabled() -> bool:
    return COACH_SIDECAR_FALLBACK_SUBPROCESS.lower() not in ("0", "false", "no")


def credential_secret_values(value: Any) -> list[str]:
    """Collect generic credential secrets for error/log redaction only."""
    secrets: list[str] = []

    def visit_credential(item: Any, key: str | None = None) -> None:
        if isinstance(item, Mapping):
            for child_key, child in item.items():
                visit_credential(child, str(child_key).lower())
        elif isinstance(item, list):
            for child in item:
                visit_credential(child, key)
        elif isinstance(item, str) and item and key != "type":
            secrets.append(item)

    if isinstance(value, Mapping):
        credential = value.get("credential")
        if isinstance(credential, Mapping):
            visit_credential(credential)
        api_key = value.get("api_key")
        if isinstance(api_key, str) and api_key:
            secrets.append(api_key)
    return list(dict.fromkeys(secrets))


def redact_provider_secrets(value: str, profile: Mapping[str, Any] | None) -> str:
    redacted = value
    for secret in credential_secret_values(profile):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _normalize_tool_bridge(value: Mapping[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    if value is None:
        return None, []
    if not isinstance(value, Mapping) or value.get("schema_version") != "coach_tool_bridge.v1":
        raise CoachRuntimeError("tool_bridge contract 无效")
    required = {
        "schema_version", "turn_id", "endpoint", "bearer_token",
        "expires_at", "user_message_ref",
    }
    allowed = required | {"desktop_token"}
    if set(value) - allowed or required - set(value):
        raise CoachRuntimeError("tool_bridge fields 无效")
    normalized = dict(value)
    for field in required - {"schema_version"}:
        if not isinstance(normalized.get(field), str) or not normalized[field].strip():
            raise CoachRuntimeError(f"tool_bridge.{field} 无效")
        normalized[field] = normalized[field].strip()
    endpoint = urlparse(normalized["endpoint"])
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in {"127.0.0.1", "localhost"}
        or endpoint.port is None
        or endpoint.path != "/api/coach/tools/execute"
        or endpoint.params
        or endpoint.query
        or endpoint.fragment
    ):
        raise CoachRuntimeError("tool_bridge endpoint 必须是固定 loopback product route")
    desktop_token = normalized.get("desktop_token")
    if desktop_token is not None:
        if not isinstance(desktop_token, str) or not desktop_token:
            raise CoachRuntimeError("tool_bridge.desktop_token 无效")
    secrets = [normalized["bearer_token"], normalized["endpoint"]]
    if isinstance(desktop_token, str) and desktop_token:
        secrets.append(desktop_token)
    return normalized, secrets


def _load_legacy_provider_turn_profile() -> dict[str, Any] | None:
    """Compatibility-only providers.json read; never creates a DB profile."""
    try:
        with _PROVIDERS_JSON.open(encoding="utf-8") as f:
            cfg = json.load(f)
        provider = cfg.get(LLM_PROVIDER)
        if not isinstance(provider, Mapping):
            return None
        api_key_env = str(provider.get("api_key_env", ""))
        api_key = os.environ.get(api_key_env) or None
        result = {
            "provider_id": LLM_PROVIDER,
            "provider_name": LLM_PROVIDER,
            "kind": "builtin",
            "base_url": provider.get("base_url"),
            "model_id": str(provider.get("model", "")),
        }
        if api_key:
            result["credential"] = {"type": "api_key", "key": api_key}
            result["api_key"] = api_key
        return result
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _normalize_runtime_profile(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    if profile is None:
        profile = _load_legacy_provider_turn_profile()
    if not isinstance(profile, Mapping):
        raise ProviderUnconfiguredError("Coach Provider 未配置")

    provider_id = profile.get("provider_id")
    provider_name = profile.get("provider_name")
    kind = profile.get("kind")
    model_id = profile.get("model_id")
    api_key = profile.get("api_key")
    credential = profile.get("credential")
    base_url = profile.get("base_url")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (provider_id, provider_name, kind, model_id)
    ):
        raise ProviderUnconfiguredError("Coach Provider 未配置完整元数据")
    if kind not in {"builtin", "custom_openai_compatible"}:
        raise ProviderUnconfiguredError("Coach Provider 类型不受支持")
    if credential is None and isinstance(api_key, str) and api_key:
        credential = {"type": "api_key", "key": api_key}
    if credential is not None:
        from .provider_store import normalize_credential
        try:
            credential = normalize_credential(credential)
        except ValueError as error:
            raise ProviderUnconfiguredError("Coach Provider credential 无效") from error
    if profile.get("credential_needs_reauth"):
        raise ProviderUnconfiguredError("Coach Provider credential 需要重新认证")
    has_credential_secret = bool(credential_secret_values({"credential": credential}))
    if kind == "custom_openai_compatible" and (
        not isinstance(base_url, str) or not base_url.strip()
        or not has_credential_secret
    ):
        raise ProviderUnconfiguredError("Coach Provider 未配置完整凭据")
    if api_key is not None and not isinstance(api_key, str):
        raise ProviderUnconfiguredError("Coach Provider API key 无效")
    normalized = {
        "provider_id": provider_id.strip(),
        "provider_name": provider_name.strip(),
        "kind": kind,
        "base_url": base_url.strip().rstrip("/") if isinstance(base_url, str) else None,
        "model_id": model_id.strip(),
    }
    if isinstance(credential, Mapping):
        normalized["credential"] = dict(credential)
    return normalized


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
    schema_version: str,
    user_id: str | None,
    profile: Mapping[str, Any] | None,
    messages: Sequence[Mapping[str, str]],
    analysis_summary: str | None,
    system_prompt: str | None,
    tool_bridge: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    normalized_messages: list[dict[str, str]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant", "system") or not isinstance(content, str):
            raise CoachRuntimeError("messages 项须含 role(user|assistant|system) 与 content 字符串")
        normalized_messages.append({"role": role, "content": content})
    if not normalized_messages:
        raise CoachRuntimeError("messages 不能为空")

    runtime_profile = _normalize_runtime_profile(profile)
    if profile is not None and (not isinstance(user_id, str) or not user_id.strip()):
        raise CoachRuntimeError("selected Provider turn requires user_id")
    effective_user_id = user_id.strip() if isinstance(user_id, str) and user_id.strip() else _DEFAULT_USER_ID
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "run_id": str(uuid.uuid4()),
        "user_id": effective_user_id,
        "messages": normalized_messages,
        "analysis_summary": analysis_summary,
        "model": runtime_profile,
    }
    if system_prompt is not None:
        payload["system_prompt"] = system_prompt
    normalized_bridge, bridge_secrets = _normalize_tool_bridge(tool_bridge)
    if normalized_bridge is not None:
        payload["tool_bridge"] = normalized_bridge
    return payload, credential_secret_values(runtime_profile) + bridge_secrets


_TOOL_EVENT_KEYS = {
    "knowledge": {
        "type", "registry_version", "topic", "issue_signal",
        "entry_refs", "entry_versions", "source_refs", "source_levels",
        "max_claim_levels",
    },
    "product_command": {
        "type", "command_id", "command_name", "status", "result_ref",
        "audit_ref", "ui_event", "warning_or_error",
    },
}
_FORBIDDEN_EVENT_KEYS = {
    "owner", "owner_id", "owner_scope", "risk", "parameters", "result",
    "payload", "internal_payload", "path", "video_path", "raw_trace",
    "trace", "credential", "authorization", "token", "bearer_token",
    "desktop_token", "endpoint", "url",
}


def _safe_tool_scalar(value: object) -> bool:
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if not isinstance(value, str) or len(value) > 1000:
        return False
    lowered = value.casefold()
    if value.startswith(("/", "\\", "~/", "../", "file://")):
        return False
    if len(value) >= 3 and value[0].isalpha() and value[1:3] in {":/", ":\\"}:
        return False
    return not any(marker in lowered for marker in ("bearer ", "api_key=", "access_token="))


_KNOWLEDGE_ENTRY_REF = re.compile(r"^knowledge:[a-z0-9._-]+@([1-9][0-9]*)$")
_KNOWLEDGE_SOURCE_LEVELS = frozenset({
    "product_contract",
    "academic_peer_reviewed",
    "community_consensus",
    "personal_experience_unverified",
    "experimental",
})
_KNOWLEDGE_CLAIM_LEVELS = frozenset({
    "deterministic_rule",
    "research_supported",
    "community_consensus",
    "experimental",
})


def _validate_knowledge_event(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != _TOOL_EVENT_KEYS["knowledge"]:
        raise CoachRuntimeError("unsafe knowledge event fields")
    registry_version = value.get("registry_version")
    topic = value.get("topic")
    issue_signal = value.get("issue_signal")
    if (
        not isinstance(registry_version, str)
        or not registry_version.strip()
        or not _safe_tool_scalar(registry_version)
        or topic is not None
        and (not isinstance(topic, str) or len(topic) > 160 or not _safe_tool_scalar(topic))
        or issue_signal is not None
        and (
            not isinstance(issue_signal, str)
            or len(issue_signal) > 160
            or not _safe_tool_scalar(issue_signal)
        )
    ):
        raise CoachRuntimeError("unsafe knowledge event value")

    entry_refs = value.get("entry_refs")
    entry_versions = value.get("entry_versions")
    source_refs = value.get("source_refs")
    source_levels = value.get("source_levels")
    max_claim_levels = value.get("max_claim_levels")
    if not all(isinstance(items, list) for items in (
        entry_refs, entry_versions, source_refs, source_levels, max_claim_levels,
    )):
        raise CoachRuntimeError("unsafe knowledge event list")
    if not (
        len(entry_refs) == len(entry_versions) == len(max_claim_levels) <= 3
        and len(source_refs) == len(source_levels) <= 32
    ):
        raise CoachRuntimeError("unsafe knowledge event list")

    for ref, version in zip(entry_refs, entry_versions):
        match = _KNOWLEDGE_ENTRY_REF.fullmatch(ref) if isinstance(ref, str) else None
        if (
            match is None
            or not _safe_tool_scalar(ref)
            or isinstance(version, bool)
            or not isinstance(version, int)
            or version < 1
            or int(match.group(1)) != version
        ):
            raise CoachRuntimeError("unsafe knowledge entry reference")
    if not all(
        isinstance(ref, str) and len(ref) <= 160 and _safe_tool_scalar(ref)
        for ref in source_refs
    ):
        raise CoachRuntimeError("unsafe knowledge source reference")
    if not all(level in _KNOWLEDGE_SOURCE_LEVELS for level in source_levels):
        raise CoachRuntimeError("unsafe knowledge source level")
    if not all(level in _KNOWLEDGE_CLAIM_LEVELS for level in max_claim_levels):
        raise CoachRuntimeError("unsafe knowledge claim level")

    from kovaak_tracker.coach.knowledge_registry import entry_ref, load_registry

    registry = load_registry()
    if registry_version != registry["registry_version"]:
        raise CoachRuntimeError("unknown knowledge registry version")
    active_by_ref = {
        entry_ref(entry): entry
        for entry in registry["entries"]
        if entry["status"] == "active"
    }
    try:
        entries = [active_by_ref[ref] for ref in entry_refs]
    except KeyError as error:
        raise CoachRuntimeError("unknown knowledge entry reference") from error
    expected_sources = [source for entry in entries for source in entry["sources"]]
    if (
        entry_versions != [entry["entry_version"] for entry in entries]
        or source_refs != [source["source_ref"] for source in expected_sources]
        or source_levels != [source["source_level"] for source in expected_sources]
        or max_claim_levels != [entry["max_claim_level"] for entry in entries]
    ):
        raise CoachRuntimeError("knowledge event does not match canonical registry")
    return dict(value)


def _validate_safe_ui_event(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CoachRuntimeError("unsafe tool event ui_event")
    kind = value.get("kind")
    allowed_by_kind = {
        "history": {"schema_version", "kind"},
        "analysis": {"schema_version", "kind", "analysis_ref", "section"},
        "flick": {"schema_version", "kind", "analysis_ref", "event_ref"},
        "evidence": {"schema_version", "kind", "analysis_ref", "evidence_ref"},
        "video_time": {"schema_version", "kind", "analysis_ref", "time_ms"},
        "tasks": {"schema_version", "kind", "plan_ref"},
        "analyze": {"schema_version", "kind", "run_ref"},
    }
    allowed = allowed_by_kind.get(kind)
    if allowed is None or set(value) - allowed:
        raise CoachRuntimeError("unsafe tool event ui_event")
    if value.get("schema_version") != "coach_ui_event.v1":
        raise CoachRuntimeError("unsafe tool event ui_event schema")
    if not all(_safe_tool_scalar(item) for item in value.values()):
        raise CoachRuntimeError("unsafe tool event ui_event value")
    return dict(value)


def _validate_tool_events(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32:
        raise CoachRuntimeError("tool events 必须是有限列表")
    safe: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise CoachRuntimeError("unsafe tool event")
        event_type = item.get("type")
        allowed = _TOOL_EVENT_KEYS.get(event_type)
        if allowed is None or set(item) - allowed or set(item) & _FORBIDDEN_EVENT_KEYS:
            raise CoachRuntimeError("unsafe tool event fields")
        if event_type == "knowledge":
            safe.append(_validate_knowledge_event(item))
            continue
        event = dict(item)
        if event_type == "product_command":
            event["ui_event"] = _validate_safe_ui_event(event.get("ui_event"))
            warning = event.get("warning_or_error")
            if warning is not None:
                if not isinstance(warning, Mapping) or set(warning) - {"code", "message", "retryable"}:
                    raise CoachRuntimeError("unsafe tool event warning")
                if not all(_safe_tool_scalar(field) for field in warning.values()):
                    raise CoachRuntimeError("unsafe tool event warning value")
                event["warning_or_error"] = dict(warning)
        for key, field in event.items():
            if key in {"ui_event", "warning_or_error"}:
                continue
            if isinstance(field, list):
                if len(field) > 32 or not all(_safe_tool_scalar(entry) for entry in field):
                    raise CoachRuntimeError("unsafe tool event list")
            elif not _safe_tool_scalar(field):
                raise CoachRuntimeError("unsafe tool event value")
        safe.append(event)
    return safe


def _validate_turn_response(
    response: Mapping[str, Any],
    *,
    expected_schema: str,
    exit_code: int | None = None,
    secrets: Sequence[str] = (),
) -> PiCoachTurnResult:
    def redact(value: str) -> str:
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted

    if response.get("schema_version") != expected_schema:
        raise CoachRuntimeError(
            f"不支持的 schema_version: {response.get('schema_version')!r}"
        )
    tool_events = _validate_tool_events(response.get("tool_events"))
    if not response.get("ok"):
        err = response.get("error") or {}
        if isinstance(err, Mapping):
            message = str(err.get("message") or err.get("code") or "unknown error")
        else:
            message = "coach-runtime 返回 ok=false"
        message = redact(message)
        if exit_code is not None and exit_code != 0:
            message = f"{message} (exit {exit_code})"
        raise CoachRuntimeError(
            message,
            tool_events=tool_events,
            side_effects_possible=bool(tool_events),
        )

    reply = response.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise CoachRuntimeError("coach-runtime 成功但 reply 为空")
    notes = response.get("notes")
    if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
        raise CoachRuntimeError("coach-runtime notes 无效")
    return PiCoachTurnResult(
        reply=redact(reply),
        notes=[redact(note) for note in notes],
        tool_events=tool_events,
    )


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
    schema_version = str(request.get("schema_version") or COACH_RUNTIME_TURN_SCHEMA_V0)
    path = "/v1/turn" if schema_version == COACH_RUNTIME_TURN_SCHEMA_V1 else "/v0/turn"
    url = f"{COACH_SIDECAR_URL.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=request)
    except httpx.HTTPError as e:
        raise CoachRuntimeError(
            f"sidecar 不可达: {type(e).__name__}",
            side_effects_possible=not isinstance(e, httpx.ConnectError),
        ) from e

    if resp.status_code != 200:
        try:
            parsed = resp.json()
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("ok") is False:
            return parsed
        raise CoachRuntimeError(
            f"sidecar HTTP {resp.status_code}",
            side_effects_possible=True,
        )

    try:
        parsed = resp.json()
    except json.JSONDecodeError as e:
        raise CoachRuntimeError(
            f"sidecar 响应非 JSON: {e}",
            side_effects_possible=True,
        ) from e
    if not isinstance(parsed, dict):
        raise CoachRuntimeError(
            "sidecar 响应须为 JSON 对象",
            side_effects_possible=True,
        )
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
            f"coach-runtime 超时（>{timeout_s}s）",
            side_effects_possible=True,
        ) from e
    except OSError as e:
        raise CoachRuntimeError(f"无法启动 coach-runtime: {type(e).__name__}") from e

    if completed.returncode != 0 and not completed.stdout.strip():
        raise CoachRuntimeError(
            f"coach-runtime exit {completed.returncode}",
            side_effects_possible=True,
        )

    try:
        response = _parse_turn_response_stdout(completed.stdout)
    except CoachRuntimeError as error:
        raise CoachRuntimeError(
            str(error),
            tool_events=error.tool_events,
            side_effects_possible=True,
        ) from error
    response["_exit_code"] = completed.returncode
    return response


def run_pi_coach_turn(
    *,
    messages: Sequence[Mapping[str, str]],
    analysis_summary: str | None,
    user_id: str | None = None,
    profile: Mapping[str, Any] | None = None,
    system_prompt: str | None = None,
    tool_bridge: Mapping[str, Any] | None = None,
    return_result: bool = False,
    timeout_s: int | None = None,
) -> str | PiCoachTurnResult:
    """Run one selected-profile turn; legacy callers may use providers.json only."""
    schema_version = (
        COACH_RUNTIME_TURN_SCHEMA_V1
        if profile is not None
        else COACH_RUNTIME_TURN_SCHEMA_V0
    )
    request, secrets = _build_turn_request(
        schema_version=schema_version,
        user_id=user_id,
        profile=profile,
        messages=messages,
        analysis_summary=analysis_summary,
        system_prompt=system_prompt,
        tool_bridge=tool_bridge,
    )
    timeout = timeout_s if timeout_s is not None else COACH_RUNTIME_TIMEOUT_SECONDS

    response: dict[str, Any] | None = None
    response_source: str | None = None
    exit_code: int | None = None

    try:
        response = _post_turn_to_sidecar(request, timeout)
        response_source = "sidecar"
    except CoachRuntimeError as error:
        if not _sidecar_fallback_enabled() or (
            schema_version == COACH_RUNTIME_TURN_SCHEMA_V1
            and error.side_effects_possible
        ):
            message = str(error)
            for secret in secrets:
                message = message.replace(secret, "[REDACTED]")
            raise CoachRuntimeError(
                message,
                tool_events=error.tool_events,
                side_effects_possible=error.side_effects_possible,
            ) from error
        _log.warning(
            "coach sidecar unavailable, subprocess fallback: %s",
            redact_provider_secrets(str(error), request.get("model")),
        )

    if response is None:
        response = _run_turn_via_subprocess(request, timeout)
        response_source = "subprocess"
        exit_code = response.pop("_exit_code", None)

    try:
        result = _validate_turn_response(
            response,
            expected_schema=schema_version,
            exit_code=exit_code,
            secrets=secrets,
        )
    except CoachRuntimeError as error:
        if response_source is not None:
            raise CoachRuntimeError(
                str(error),
                tool_events=error.tool_events,
                side_effects_possible=True,
            ) from error
        raise
    return result if return_result else result.reply


async def fetch_provider_catalog(timeout_s: float = 5.0) -> Any:
    """Fetch the Pi catalog without applying an Aiming Cookie allow-list."""
    url = f"{COACH_SIDECAR_URL.rstrip('/')}/v0/providers/catalog"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url)
    except httpx.HTTPError as error:
        raise CoachRuntimeError(f"Pi provider catalog unavailable: {type(error).__name__}") from error
    if resp.status_code != 200:
        raise CoachRuntimeError(f"Pi provider catalog HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as error:
        raise CoachRuntimeError("Pi provider catalog response is not JSON") from error


def _provider_status_result(
    profile: Mapping[str, Any],
    data: Mapping[str, Any],
    *,
    explicit_test: bool,
) -> dict[str, Any]:
    allowed = {
        "ready", "unconfigured", "auth_expired", "needs_reauth", "model_unavailable",
        "connection_failed",
    }
    status = str(data.get("status") or ("ready" if data.get("ok") else "connection_failed"))
    if status not in allowed:
        status = "connection_failed"
    configured = status in {
        "ready", "auth_expired", "model_unavailable", "connection_failed",
    }
    if status == "ready":
        message = "Provider 连接成功" if explicit_test else "Coach Provider 已就绪"
    elif status == "auth_expired":
        message = "Provider OAuth credential 已过期"
    elif status == "needs_reauth":
        message = "Provider credential 需要重新认证"
    elif status == "model_unavailable":
        message = "当前 Provider model 不可用，请重新选择"
    elif explicit_test:
        message = "Provider 连接测试失败，请检查设置后重试"
    else:
        message = "Coach Provider 尚未就绪"
    raw_error = data.get("error")
    if isinstance(raw_error, Mapping) and isinstance(raw_error.get("message"), str):
        message = redact_provider_secrets(raw_error["message"], profile)
    return {"configured": configured, "status": status, "message": message}


async def get_provider_profile_status(
    profile: Mapping[str, Any],
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Read readiness via /v1/profile/status; this endpoint sends no completion."""
    from .provider_store import runtime_profile_configured

    if not runtime_profile_configured(profile):
        return {
            "configured": False,
            "status": "needs_reauth" if profile.get("credential_needs_reauth") else "unconfigured",
            "message": (
                "Provider credential 需要重新认证"
                if profile.get("credential_needs_reauth")
                else "Coach Provider 未配置完整凭据"
            ),
        }
    try:
        runtime_snapshot = _normalize_runtime_profile(profile)
    except ProviderUnconfiguredError:
        return {
            "configured": False,
            "status": "needs_reauth" if profile.get("credential_needs_reauth") else "unconfigured",
            "message": "Coach Provider 未配置完整凭据",
        }
    timeout_s = max(0.001, min(float(timeout_s), 30.0))
    url = f"{COACH_SIDECAR_URL.rstrip('/')}/v1/profile/status"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json={"profile": runtime_snapshot})
        data = resp.json()
        if not isinstance(data, Mapping):
            raise ValueError("response must be an object")
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError("sidecar failure", request=resp.request, response=resp)
        return _provider_status_result(profile, data, explicit_test=False)
    except (httpx.HTTPError, ValueError, TypeError):
        return {
            "configured": True,
            "status": "connection_failed",
            "message": "Pi Provider 状态暂不可用",
        }


async def test_provider_profile(
    profile: Mapping[str, Any],
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Ask the local sidecar to test one runtime-only profile snapshot."""
    from .provider_store import runtime_profile_configured

    if not runtime_profile_configured(profile):
        return {
            "configured": False,
            "status": "unconfigured",
            "message": "Coach Provider 未配置完整凭据",
        }
    timeout_s = max(0.001, min(float(timeout_s), 30.0))
    url = f"{COACH_SIDECAR_URL.rstrip('/')}/v0/providers/test"
    try:
        runtime_snapshot = _normalize_runtime_profile(profile)
    except ProviderUnconfiguredError:
        return {
            "configured": False,
            "status": "unconfigured",
            "message": "Coach Provider 未配置完整凭据",
        }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                url,
                json={"profile": runtime_snapshot, "timeout_ms": int(timeout_s * 1000)},
            )
        if resp.status_code != 200:
            return {
                "configured": True,
                "status": "connection_failed",
                "message": "Provider 连接测试失败，请检查设置后重试",
            }
        data = resp.json()
        if not isinstance(data, Mapping):
            raise ValueError("response must be an object")
        return _provider_status_result(profile, data, explicit_test=True)
    except (httpx.HTTPError, ValueError, TypeError):
        return {
            "configured": True,
            "status": "connection_failed",
            "message": "Provider 连接测试失败，请检查设置后重试",
        }
