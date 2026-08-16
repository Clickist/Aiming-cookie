"""One-shot process boundary for deterministic local visual preprocessing."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Mapping

from kovaak_tracker.generic_static_clicking_analysis import (
    GenericVisualPreprocessingUnavailable,
)
from kovaak_tracker.visual_signals import VisualPreprocessingUnavailable

MAX_REQUEST_BYTES = 8 * 1024 * 1024
CV_WORKER_THREAD_LIMIT = 16
SENSITIVE_ENV_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
)
CAPTURE_CONTROL_ADDRESS_ENV = "AIMING_COOKIE_NATIVE_CAPTURE_CONTROL_ADDR"


def build_child_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep runtime settings while removing credentials the CV child never needs."""
    environment = dict(source or os.environ)
    for key in tuple(environment):
        upper = key.upper()
        if upper == CAPTURE_CONTROL_ADDRESS_ENV or any(
            marker in upper for marker in SENSITIVE_ENV_MARKERS
        ):
            environment.pop(key, None)
    return environment


def _error_code(error: BaseException) -> str:
    name = type(error).__name__
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _configure_opencv_threads() -> None:
    import cv2

    cv2.setNumThreads(min(CV_WORKER_THREAD_LIMIT, os.cpu_count() or 1))


def _run_generic_static_clicking_job(job: dict) -> dict:
    """Run the untrained generic static-clicking detector for one Run."""
    from kovaak_tracker.generic_static_clicking_analysis import (
        run_generic_static_clicking_detection_v1,
    )
    from .worker_visual_producers import _run_owned_visual_video_time_mapping_v2

    snapshot = job.get("input_snapshot") or {}
    window = snapshot.get("canonical_time_window")
    if not isinstance(window, dict):
        raise ValueError("generic static clicking requires a canonical window")
    mapping = _run_owned_visual_video_time_mapping_v2(job)
    return run_generic_static_clicking_detection_v1(
        media_path=str(job["video_path"]),
        analysis_ref=f"analysis:{job['id']}",
        canonical_time_window=window,
        video_time_mapping=mapping,
    )


def _run_visual_job(job: dict) -> dict:
    from .worker import _parse_frozen_stats_for_visual, run_visual_preprocessing

    snapshot = job.get("input_snapshot") or {}
    parsed_stats = _parse_frozen_stats_for_visual(snapshot)
    _configure_opencv_threads()
    return run_visual_preprocessing(job, parsed_stats=parsed_stats)


def _run_continuous_tracking_job(job: dict, visual_result: dict) -> dict | None:
    from .worker import run_continuous_tracking_analysis

    quality = visual_result.get("quality")
    if not (
        isinstance(quality, Mapping)
        and quality.get("status") in {"accepted", "limited"}
        and "tracking" in (quality.get("enabled_metric_families") or [])
    ):
        return None
    return run_continuous_tracking_analysis(job, visual_result)


def _run_target_switching_job(visual_result: dict) -> dict:
    """Build only local target-observation episodes from serialized frames."""
    from kovaak_tracker.visual_signals import (
        preprocess_visual_target_episodes_v1,
    )

    quality = visual_result.get("quality")
    if not (
        isinstance(quality, Mapping)
        and quality.get("status") in {"accepted", "limited"}
        and bool(
            {"switching", "target_switching"}.intersection(
                quality.get("enabled_metric_families") or []
            )
        )
    ):
        raise ValueError("target switching visual quality is unavailable")
    analysis_ref = visual_result.get("analysis_ref")
    observations = visual_result.get("frame_observations")
    if not isinstance(analysis_ref, str) or not isinstance(observations, list):
        raise ValueError("target switching visual observations are unavailable")
    return preprocess_visual_target_episodes_v1(
        analysis_ref=analysis_ref,
        frame_observations=observations,
    )


def _commit_continuous_tracking_evidence(
    job: dict,
    result: dict,
    visual_result: dict,
    tracking_result: dict,
) -> dict:
    from .worker import _maybe_commit_analysis_evidence, _parse_frozen_stats_for_visual

    parsed_stats = _parse_frozen_stats_for_visual(job.get("input_snapshot") or {})
    return _maybe_commit_analysis_evidence(
        job,
        result,
        parsed_stats=parsed_stats,
        visual_result=visual_result,
        tracking_result=tracking_result,
    )


def execute_request(
    request: object,
    *,
    runner: Callable[[dict], dict] | None = None,
    continuous_tracking_runner: Callable[[dict, dict], dict | None] | None = None,
    evidence_runner: Callable[[dict, dict, dict, dict], dict] | None = None,
) -> dict:
    """Execute one validated request without exposing exception messages."""
    from .worker_source_validation import SourceSnapshotChangedError

    if not isinstance(request, dict):
        return {
            "ok": False,
            "error": {"kind": "visual_preprocessing_failed", "code": "invalid_request"},
        }
    request_keys = set(request)
    evidence_keys = {
        "operation", "job", "result", "visual_result", "tracking_result",
    }
    if request_keys == evidence_keys:
        job = request.get("job")
        result = request.get("result")
        visual_result = request.get("visual_result")
        tracking_result = request.get("tracking_result")
        if not (
            request.get("operation") == "commit_continuous_tracking_evidence"
            and isinstance(job, dict)
            and isinstance(result, dict)
            and isinstance(visual_result, dict)
            and isinstance(tracking_result, dict)
        ):
            return {
                "ok": False,
                "error": {"kind": "evidence_commit_failed", "code": "invalid_request"},
            }
        try:
            committed = (evidence_runner or _commit_continuous_tracking_evidence)(
                job, result, visual_result, tracking_result,
            )
        except Exception as error:
            return {
                "ok": False,
                "error": {"kind": "evidence_commit_failed", "code": _error_code(error)},
            }
        if not isinstance(committed, dict):
            return {
                "ok": False,
                "error": {"kind": "evidence_commit_failed", "code": "invalid_result"},
            }
        return {"ok": True, "result": committed}
    if request_keys not in (
        {"job"},
        {"job", "postprocess"},
    ):
        return {
            "ok": False,
            "error": {"kind": "visual_preprocessing_failed", "code": "invalid_request"},
        }
    postprocess = request.get("postprocess")
    if postprocess not in {
        None, "continuous_tracking", "target_switching", "generic_static_clicking",
    }:
        return {
            "ok": False,
            "error": {"kind": "visual_preprocessing_failed", "code": "invalid_request"},
        }
    job = request.get("job")
    if not isinstance(job, dict):
        return {
            "ok": False,
            "error": {"kind": "visual_preprocessing_failed", "code": "invalid_request"},
        }
    try:
        if postprocess == "generic_static_clicking":
            result = _run_generic_static_clicking_job(job)
        else:
            result = (runner or _run_visual_job)(job)
    except SourceSnapshotChangedError as error:
        return {
            "ok": False,
            "error": {"kind": "source_snapshot_changed", "code": str(error)},
        }
    except GenericVisualPreprocessingUnavailable as error:
        return {
            "ok": False,
            "error": {
                "kind": "generic_visual_unavailable",
                "code": error.code,
            },
        }
    except VisualPreprocessingUnavailable as error:
        return {
            "ok": False,
            "error": {
                "kind": "visual_preprocessing_unavailable",
                "code": error.code,
            },
        }
    except Exception as error:
        return {
            "ok": False,
            "error": {"kind": "visual_preprocessing_failed", "code": _error_code(error)},
        }
    if not isinstance(result, dict):
        return {
            "ok": False,
            "error": {"kind": "visual_preprocessing_failed", "code": "invalid_result"},
        }
    if postprocess == "continuous_tracking":
        try:
            family_result = (
                continuous_tracking_runner or _run_continuous_tracking_job
            )(job, result)
        except SourceSnapshotChangedError as error:
            return {
                "ok": False,
                "error": {"kind": "source_snapshot_changed", "code": str(error)},
            }
        except Exception as error:
            return {
                "ok": False,
                "error": {"kind": "family_analysis_failed", "code": _error_code(error)},
                "visual_result": result,
            }
        if family_result is not None and not isinstance(family_result, dict):
            return {
                "ok": False,
                "error": {"kind": "family_analysis_failed", "code": "invalid_result"},
            }
        result = {"visual_result": result, "family_result": family_result}
    elif postprocess == "target_switching":
        try:
            family_result = _run_target_switching_job(result)
            from kovaak_tracker.visual_signals import (
                project_visual_target_episodes_v1,
            )

            projected_visual_result = project_visual_target_episodes_v1(
                result, family_result,
            )
        except SourceSnapshotChangedError as error:
            return {
                "ok": False,
                "error": {"kind": "source_snapshot_changed", "code": str(error)},
            }
        except Exception as error:
            return {
                "ok": False,
                "error": {"kind": "family_analysis_failed", "code": _error_code(error)},
                "visual_result": result,
            }
        if not isinstance(family_result, dict):
            return {
                "ok": False,
                "error": {"kind": "family_analysis_failed", "code": "invalid_result"},
            }
        result = {
            "visual_result": projected_visual_result,
            "family_result": family_result,
        }
    return {"ok": True, "result": result}


def main() -> None:
    payload = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(payload) > MAX_REQUEST_BYTES:
        response = {
            "ok": False,
            "error": {"kind": "visual_preprocessing_failed", "code": "request_too_large"},
        }
    else:
        try:
            request = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            request = None
        response = execute_request(request)
    sys.stdout.write(json.dumps(response, ensure_ascii=True, separators=(",", ":")))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
