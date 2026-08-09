"""Safe, owner-scoped readiness inputs for deterministic Coach guidance."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import config, kovaak_connection_store, kovaak_run_store, provider_store, queue, training_plan_store
from .native_capture_client import NativeCaptureClient
from .read_models import build_capture_status_v1


PRODUCT_READINESS_SCHEMA_VERSION = "product_readiness.v1"
_MAX_REFS = 8
_SAFE_REF = re.compile(
    r"^(?:provider_profile:[1-9][0-9]*|kovaak_connection:current|run:[1-9][0-9]*|"
    r"analysis:[1-9][0-9]*|plan:[A-Za-z0-9._:-]{1,160}|incomplete:[a-f0-9]{16,128})$"
)
_DOMAIN_NAMES = (
    "onboarding", "provider", "capture", "kovaak", "pending_runs", "analysis",
    "training_plan", "storage",
)
_GUIDANCE_TARGETS_PATH = Path(__file__).resolve().parent.parent / "shared" / "guidance-targets.v1.json"
_GUIDANCE_KINDS = frozenset({
    "execute_command", "request_confirmation", "ui_navigation", "user_action_required",
    "wait_for_state", "completed", "blocked",
})
_SAFE_GUIDANCE_TEXT = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_UNSAFE_GUIDANCE_TEXT = re.compile(
    r"(?:https?://|file:|[A-Za-z]:[\\/]|(?:api[_ -]?key|password|secret|token|credential)\s*[:=])",
    re.IGNORECASE,
)
_UNSAFE_GUIDANCE_KEY = re.compile(
    r"(?:path|file|secret|credential|password|token|url|selector|script|payload|parameter)",
    re.IGNORECASE,
)

_GOAL_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("start_first_training", ("start_first_training", "start first", "first training", "首次训练", "开始训练")),
    ("analyze_latest_run", ("analyze_latest_run", "analyze latest", "analyze the selected run", "latest run", "分析最新", "分析刚才")),
    ("practice_today", ("practice_today", "practice today", "today practice", "今天练", "今天训练", "练什么")),
    ("record_execution", ("record_execution", "record execution", "record completion", "记录完成", "完成训练", "复测")),
    ("inspect_progress", ("inspect progress", "progress", "evidence", "检查证据", "查看进度")),
    ("recover_provider", ("recover_provider", "provider", "供应商", "模型连接")),
    ("recover_capture", ("capture", "录制", "raw input", "采集权限")),
    ("recover_kovaak", ("kovaak", "科瓦克", "steam 连接")),
    ("recover_analysis", ("analysis recovery", "分析恢复", "分析失败")),
    ("recover_storage", ("storage", "存储", "未完成录制", "清理存储")),
)


def normalize_guidance_goal(goal: object) -> str:
    """Map free-form goal labels to a small deterministic compiler vocabulary."""
    text = str(goal or "").strip().casefold()
    for canonical, aliases in _GOAL_ALIASES:
        if any(alias.casefold() in text for alias in aliases):
            return canonical
    return "inspect_progress"


def detect_guidance_goal(goal: object) -> str | None:
    """Return a workflow goal only when the message explicitly names one."""
    text = str(goal or "").strip().casefold()
    if not text:
        return None
    for canonical, aliases in _GOAL_ALIASES:
        if any(alias.casefold() in text for alias in aliases):
            return canonical
    return None


def _intent_id(goal: str, readiness: Mapping[str, object], suffix: str) -> str:
    domains = readiness.get("domains") if isinstance(readiness, Mapping) else {}
    material = json.dumps(
        {"goal": goal, "domains": domains, "suffix": suffix},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return f"guidance:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _domain_state(readiness: Mapping[str, object], key: str) -> tuple[str | None, list[str]]:
    domains = readiness.get("domains") if isinstance(readiness, Mapping) else None
    raw = domains.get(key) if isinstance(domains, Mapping) else None
    if not isinstance(raw, Mapping) or raw.get("availability") != "known":
        return None, []
    state = raw.get("state") if isinstance(raw.get("state"), str) else None
    refs = [item for item in raw.get("refs", []) if isinstance(item, str)] if isinstance(raw.get("refs"), list) else []
    return state, refs


def _intent(
    goal: str,
    kind: str,
    text: str,
    *,
    target_id: str | None = None,
    prefill: Mapping[str, str] | None = None,
    readiness_key: str | None = None,
    terminal_states: Sequence[str] = (),
    suffix: str = "next",
    command_result_ref: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "guidance_intent.v1",
        "intent_id": _intent_id(goal, {"domains": {readiness_key: list(terminal_states)}}, suffix),
        "kind": kind,
        "goal": goal,
    }
    if target_id is not None:
        value["target"] = {"target_id": target_id, "safe_prefill": dict(prefill or {})}
    if command_result_ref is not None:
        value["command_result_ref"] = command_result_ref
    if readiness_key is not None:
        value["completion_condition"] = {
            "readiness_key": readiness_key,
            "terminal_states": list(terminal_states),
        }
        value["precondition"] = {"readiness_key": readiness_key}
    value["recovery"] = {"action": "retry" if kind == "wait_for_state" else "return_to_coach"}
    # Keep the user-facing sentence bounded, while preserving the canonical goal.
    value["goal"] = text if text and len(text) <= 240 else goal
    validate_guidance_intent(value)
    return value


def compile_guidance(goal: object, readiness: Mapping[str, object]) -> dict[str, object]:
    """Compile one and only one next intent from canonical readiness."""
    canonical = normalize_guidance_goal(goal)
    provider, provider_refs = _domain_state(readiness, "provider")
    capture, _ = _domain_state(readiness, "capture")
    kovaak, _ = _domain_state(readiness, "kovaak")
    pending, pending_refs = _domain_state(readiness, "pending_runs")
    analysis, analysis_refs = _domain_state(readiness, "analysis")
    plan, plan_refs = _domain_state(readiness, "training_plan")
    storage, storage_refs = _domain_state(readiness, "storage")

    if canonical == "recover_provider" or provider in {None, "missing", "needs_auth", "testing"}:
        prefill = {"provider_profile_ref": provider_refs[0]} if len(provider_refs) == 1 else {}
        return _intent(canonical, "ui_navigation", "Open Provider settings and complete the trusted connection step.", target_id="settings.provider_auth", prefill=prefill, readiness_key="provider", terminal_states=("ready",), suffix=provider or "unknown")
    if canonical == "recover_capture" or capture in {None, "needs_permission", "needs_setup"}:
        return _intent(canonical, "user_action_required", "Enable capture in the trusted desktop control, then return here.", target_id="desktop.capture_control", readiness_key="capture", terminal_states=("ready",), suffix=capture or "unknown")
    if canonical == "recover_kovaak" or kovaak in {None, "disconnected"}:
        return _intent(canonical, "user_action_required", "Connect KovaaK in the trusted product control, then return here.", target_id="coach.panel", readiness_key="kovaak", terminal_states=("connected",), suffix=kovaak or "unknown")
    if canonical == "recover_storage" or storage == "incomplete":
        return _intent(canonical, "ui_navigation", "Review incomplete captures and choose one specific item to recover.", target_id="storage.incomplete", prefill={"item_ref": storage_refs[0]} if len(storage_refs) == 1 else {}, readiness_key="storage", terminal_states=("empty",), suffix=storage or "empty")
    if canonical == "analyze_latest_run":
        if pending == "many" or (pending is None and len(pending_refs) > 1):
            return _intent(canonical, "user_action_required", "Choose one Run to analyze so Coach does not guess.", target_id="history.runs", readiness_key="pending_runs", terminal_states=("one", "none"), suffix="ambiguous")
        if pending == "one" and len(pending_refs) == 1:
            return _intent(canonical, "execute_command", "Analyze the selected Run.", command_result_ref=pending_refs[0], readiness_key="analysis", terminal_states=("ready",), suffix=pending_refs[0])
        if analysis in {"queued", "running"}:
            return _intent(canonical, "wait_for_state", "Wait for the analysis to finish, then review its result.", readiness_key="analysis", terminal_states=("ready", "failed"), suffix=analysis or "waiting")
        return _intent(canonical, "blocked", "No eligible Run is available to analyze.", target_id="history.runs", suffix="none")
    if canonical in {"start_first_training", "practice_today", "record_execution"}:
        if plan in {None, "none", "draft", "saved"}:
            return _intent(canonical, "ui_navigation", "Open the current Training Plan and choose the next practice item.", target_id="training.current", readiness_key="training_plan", terminal_states=("active",), suffix=plan or "none")
        if canonical == "record_execution":
            return _intent(canonical, "user_action_required", "Complete the real practice, then state that it is finished so Coach can record it.", target_id="training.current", prefill={"plan_ref": plan_refs[0]} if len(plan_refs) == 1 else {}, suffix="execution")
        return _intent(canonical, "user_action_required", "Complete the real practice in the active Training Plan, then return here.", target_id="training.current", prefill={"plan_ref": plan_refs[0]} if len(plan_refs) == 1 else {}, suffix="practice")
    if canonical == "recover_analysis" or analysis == "failed":
        return _intent(canonical, "ui_navigation", "Open Analysis history to inspect or retry the failed result.", target_id="history.runs", suffix="failed")
    return _intent(canonical, "ui_navigation", "Open History to inspect progress and available evidence.", target_id="history.runs", suffix=canonical)


def _domain(
    state: str,
    *,
    availability: str = "known",
    reason_code: str | None = None,
    refs: Sequence[str] = (),
    count: int | None = None,
) -> dict[str, object]:
    safe_refs = [ref for ref in refs if _SAFE_REF.fullmatch(ref)][: _MAX_REFS]
    total = len(refs) if count is None else max(0, count)
    value: dict[str, object] = {
        "state": state,
        "availability": availability,
        "refs": safe_refs,
        "count": total,
        "truncated": total > len(safe_refs),
    }
    if reason_code is not None:
        value["reason_code"] = reason_code
    return value


def _unavailable(reason_code: str) -> dict[str, object]:
    return _domain("unknown", availability="unavailable", reason_code=reason_code)


def project_product_readiness(inputs: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    """Project already-read canonical state without retaining any product data."""
    domains: dict[str, dict[str, object]] = {}
    blocking_reasons: list[str] = []
    for name in _DOMAIN_NAMES:
        raw = inputs.get(name)
        if not isinstance(raw, Mapping) or raw.get("availability") == "unavailable":
            reason = raw.get("reason_code") if isinstance(raw, Mapping) else None
            reason_code = reason if isinstance(reason, str) and reason else f"{name}_unavailable"
            domains[name] = _unavailable(reason_code)
            blocking_reasons.append(reason_code)
            continue
        state = raw.get("state")
        if not isinstance(state, str) or not state:
            domains[name] = _unavailable(f"{name}_unavailable")
            blocking_reasons.append(f"{name}_unavailable")
            continue
        refs = raw.get("refs")
        domains[name] = _domain(
            state,
            reason_code=raw.get("reason_code") if isinstance(raw.get("reason_code"), str) else None,
            refs=refs if isinstance(refs, Sequence) and not isinstance(refs, str) else (),
            count=raw.get("count") if isinstance(raw.get("count"), int) else None,
        )
    return {
        "schema_version": PRODUCT_READINESS_SCHEMA_VERSION,
        "domains": domains,
        "capabilities": ["product_readiness.read"],
        "blocking_reasons": blocking_reasons,
    }


async def _capture_input(owner_id: str) -> dict[str, object]:
    runs = await kovaak_run_store.list_kovaak_run_summaries(owner_id)
    native_status: Mapping[str, object] | None = None
    if config.NATIVE_CAPTURE_CONTROL_ADDR and config.NATIVE_CAPTURE_CONTROL_SECRET:
        client = NativeCaptureClient(
            config.NATIVE_CAPTURE_CONTROL_ADDR,
            config.NATIVE_CAPTURE_CONTROL_SECRET,
        )
        native_status = await asyncio.to_thread(client.status)
    elif bool(config.NATIVE_CAPTURE_CONTROL_ADDR) != bool(config.NATIVE_CAPTURE_CONTROL_SECRET):
        raise RuntimeError("capture control configuration is incomplete")
    status = build_capture_status_v1(native_status=native_status, runs=runs)
    if status["availability"] != "available" or status["runtime_health"] == "unavailable":
        return {"availability": "unavailable", "reason_code": "capture_status_unavailable"}
    if status["capture_enabled"] and status["raw_input_permission"] == "granted" and status["runtime_health"] == "healthy":
        state = "ready"
    elif status["raw_input_permission"] != "granted":
        state = "needs_permission"
    else:
        state = "needs_setup"
    return {"availability": "known", "state": state}


async def get_product_readiness(owner_id: str) -> dict[str, object]:
    """Read each canonical source independently; failures never imply readiness."""
    inputs: dict[str, Mapping[str, object]] = {}

    try:
        product = await queue.get_product_state(owner_id)
        inputs["onboarding"] = {
            "availability": "known",
            "state": "complete" if product.get("onboarding_completed") else "needs_onboarding",
        }
    except Exception:
        inputs["onboarding"] = {"availability": "unavailable", "reason_code": "product_state_unavailable"}

    try:
        profiles = await provider_store.list_profiles(owner_id)
        refs = [f"provider_profile:{profile['id']}" for profile in profiles if isinstance(profile.get("id"), int) and profile["id"] > 0]
        default = next((profile for profile in profiles if profile.get("is_default")), None)
        if default is None:
            state = "missing"
        elif default.get("configured") and default.get("status") == "ready":
            state = "ready"
        elif default.get("status") in {"connection_failed", "model_unavailable"}:
            state = "testing"
        else:
            state = "needs_auth"
        inputs["provider"] = {"availability": "known", "state": state, "refs": refs, "count": len(profiles)}
    except Exception:
        inputs["provider"] = {"availability": "unavailable", "reason_code": "provider_unavailable"}

    try:
        inputs["capture"] = await _capture_input(owner_id)
    except Exception:
        inputs["capture"] = {"availability": "unavailable", "reason_code": "capture_status_unavailable"}

    try:
        connected = await kovaak_connection_store.get_connection(owner_id) is not None
        inputs["kovaak"] = {
            "availability": "known", "state": "connected" if connected else "disconnected",
            "refs": ["kovaak_connection:current"] if connected else [], "count": int(connected),
        }
    except Exception:
        inputs["kovaak"] = {"availability": "unavailable", "reason_code": "kovaak_connection_unavailable"}

    try:
        runs = await kovaak_run_store.list_kovaak_run_summaries(owner_id)
        pending = [run for run in runs if run.get("readiness_state") in {"pending_analysis", "incomplete_evidence"}]
        if any(run.get("readiness_state") == "incomplete_evidence" for run in pending):
            state = "incomplete"
        else:
            state = "none" if not pending else "one" if len(pending) == 1 else "many"
        refs = [str(run["run_ref"]) for run in pending if isinstance(run.get("run_ref"), str)]
        inputs["pending_runs"] = {"availability": "known", "state": state, "refs": refs, "count": len(pending)}
    except Exception:
        inputs["pending_runs"] = {"availability": "unavailable", "reason_code": "runs_unavailable"}

    try:
        analyses = await queue.list_sessions(owner_id)
        statuses = {str(item.get("status")) for item in analyses}
        state = "failed" if statuses & {"failed", "error"} else "running" if "running" in statuses else "queued" if statuses & {"queued", "uploading"} else "ready" if statuses & {"done", "completed"} else "none"
        refs = [f"analysis:{item['id']}" for item in analyses if isinstance(item.get("id"), int) and item["id"] > 0]
        inputs["analysis"] = {"availability": "known", "state": state, "refs": refs, "count": len(analyses)}
    except Exception:
        inputs["analysis"] = {"availability": "unavailable", "reason_code": "analysis_unavailable"}

    try:
        plans = await training_plan_store.list_plans(owner_id)
        statuses = {str(item.get("status")) for item in plans}
        state = next((candidate for candidate in ("active", "paused", "saved", "draft") if candidate in statuses), "none")
        refs = [str(item["plan_ref"]) for item in plans if isinstance(item.get("plan_ref"), str)]
        inputs["training_plan"] = {"availability": "known", "state": state, "refs": refs, "count": len(plans)}
    except Exception:
        inputs["training_plan"] = {"availability": "unavailable", "reason_code": "training_plan_unavailable"}

    try:
        items = await kovaak_run_store.list_incomplete_capture_items(owner_id, config.DATA_ROOT)
        refs = [str(item["item_ref"]) for item in items if isinstance(item.get("item_ref"), str)]
        inputs["storage"] = {"availability": "known", "state": "incomplete" if items else "empty", "refs": refs, "count": len(items)}
    except Exception:
        inputs["storage"] = {"availability": "unavailable", "reason_code": "storage_unavailable"}

    return project_product_readiness(inputs)


def load_guidance_target_registry() -> dict[str, tuple[str, ...]]:
    """Load the shared semantic UI target allow-list without exposing routes or selectors."""
    try:
        raw = json.loads(_GUIDANCE_TARGETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("guidance target registry is unavailable") from error
    if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "targets"}:
        raise ValueError("guidance target registry is invalid")
    if raw.get("schema_version") != "guidance_target_registry.v1" or not isinstance(raw.get("targets"), list):
        raise ValueError("guidance target registry is invalid")
    targets: dict[str, tuple[str, ...]] = {}
    for item in raw["targets"]:
        if not isinstance(item, Mapping) or set(item) != {"target_id", "safe_prefill_keys"}:
            raise ValueError("guidance target registry is invalid")
        target_id = item.get("target_id")
        keys = item.get("safe_prefill_keys")
        if (
            not isinstance(target_id, str)
            or not _SAFE_GUIDANCE_TEXT.fullmatch(target_id)
            or target_id in targets
            or not isinstance(keys, list)
            or len(keys) > 8
            or any(not isinstance(key, str) or not _SAFE_GUIDANCE_TEXT.fullmatch(key) for key in keys)
            or any(
                set(key.lower().split("_")) & {"secret", "credential", "password", "token", "path", "file", "permission"}
                for key in keys
            )
        ):
            raise ValueError("guidance target registry is invalid")
        targets[target_id] = tuple(keys)
    return targets


def validate_guidance_intent(value: Mapping[str, object]) -> dict[str, object]:
    """Validate one bounded guidance envelope before it reaches the run event stream."""
    allowed = {
        "schema_version", "intent_id", "kind", "goal", "target", "command_result_ref",
        "precondition", "completion_condition", "recovery",
    }
    if not isinstance(value, Mapping) or set(value) - allowed or value.get("schema_version") != "guidance_intent.v1":
        raise ValueError("guidance intent is invalid")
    kind = value.get("kind")
    intent_id = value.get("intent_id")
    goal = value.get("goal")
    if kind not in _GUIDANCE_KINDS or not isinstance(intent_id, str) or not _SAFE_GUIDANCE_TEXT.fullmatch(intent_id) or not isinstance(goal, str) or not goal.strip() or len(goal) > 240 or _UNSAFE_GUIDANCE_TEXT.search(goal):
        raise ValueError("guidance intent is invalid")
    normalized = dict(value)
    for field in ("precondition", "completion_condition", "recovery"):
        payload = value.get(field)
        if payload is None:
            continue
        if not isinstance(payload, Mapping) or len(payload) > 8:
            raise ValueError("guidance condition is invalid")
        if any(not isinstance(key, str) or _UNSAFE_GUIDANCE_KEY.search(key) for key in payload):
            raise ValueError("guidance condition is invalid")
        for item in payload.values():
            if isinstance(item, list):
                if len(item) > 8 or any(not isinstance(child, str) or _UNSAFE_GUIDANCE_TEXT.search(child) for child in item):
                    raise ValueError("guidance condition is invalid")
            elif not isinstance(item, (str, int, float, bool)) or (isinstance(item, str) and _UNSAFE_GUIDANCE_TEXT.search(item)):
                raise ValueError("guidance condition is invalid")
    target = value.get("target")
    if target is not None:
        if not isinstance(target, Mapping) or set(target) != {"target_id", "safe_prefill"}:
            raise ValueError("guidance target is invalid")
        target_id = target.get("target_id")
        prefill = target.get("safe_prefill")
        registry = load_guidance_target_registry()
        if not isinstance(target_id, str) or target_id not in registry or not isinstance(prefill, Mapping):
            raise ValueError("guidance target is invalid")
        if set(prefill) - set(registry[target_id]) or any(
            not isinstance(key, str) or not isinstance(item, str) or not _SAFE_REF.fullmatch(item)
            for key, item in prefill.items()
        ):
            raise ValueError("guidance target is invalid")
        normalized["target"] = {"target_id": target_id, "safe_prefill": dict(prefill)}
    elif kind in {"ui_navigation", "user_action_required"}:
        raise ValueError("guidance target is required")
    if kind == "wait_for_state":
        completion = value.get("completion_condition")
        if not isinstance(completion, Mapping) or set(completion) != {"readiness_key", "terminal_states"}:
            raise ValueError("guidance completion condition is invalid")
        readiness_key = completion.get("readiness_key")
        terminal_states = completion.get("terminal_states")
        if readiness_key not in _DOMAIN_NAMES or not isinstance(terminal_states, list) or not terminal_states or len(terminal_states) > 8 or any(not isinstance(item, str) or not _SAFE_GUIDANCE_TEXT.fullmatch(item) for item in terminal_states):
            raise ValueError("guidance completion condition is invalid")
    if kind == "execute_command":
        result_ref = value.get("command_result_ref")
        if not isinstance(result_ref, str) or not _SAFE_GUIDANCE_TEXT.fullmatch(result_ref):
            raise ValueError("guidance command result is invalid")
    return normalized


__all__ = [
    "PRODUCT_READINESS_SCHEMA_VERSION", "get_product_readiness", "load_guidance_target_registry",
    "project_product_readiness", "validate_guidance_intent", "normalize_guidance_goal",
    "detect_guidance_goal", "compile_guidance",
]
