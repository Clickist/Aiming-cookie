"""Deterministic numerical visual evidence built from local observations.

Frames and media paths stay outside this contract.  The producer consumes
detector observations with source PTS and emits only canonical-time numeric
tracks, typed events, quality state, and local sample sets.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from statistics import median


VISUAL_SIGNAL_SCHEMA_VERSION = "visual_signal_artifact.v1"
VISUAL_ANNOTATION_LEDGER_SCHEMA_VERSION = "visual_annotation_ledger.v1"
VISUAL_ANNOTATION_LEDGER_V2_SCHEMA_VERSION = "visual_annotation_ledger.v2"
VISUAL_QUALITY_PROFILE_SCHEMA_VERSION = "visual_quality_profile.v2"
VISUAL_PRODUCER_ID = "visual_signals.round_detector"
# The field-reviewed round detector is part of the producer identity.  A
# change to this predicate must create a new profile binding instead of
# silently reusing the old visual-quality evidence.
VISUAL_PRODUCER_VERSION = (
    "visual_round_detector.circularity_0_60_center_overlay_0_50.v2"
)
VISUAL_TEMPORAL_PRODUCER_ID = "visual_signals.round_detector_temporal_tracker"
VISUAL_TEMPORAL_PRODUCER_VERSION = (
    "visual_round_detector.csrt_detector_guard.v1"
)
VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID = "visual_signals.legacy_single_target_csrt"
VISUAL_SINGLE_TARGET_CSRT_PRODUCER_VERSION = (
    "visual_legacy_ball_csrt_single_target.v1"
)
VISUAL_TARGET_EPISODE_PRODUCER_ID = "visual_signals.event_local_target_episode"
VISUAL_TARGET_EPISODE_PRODUCER_VERSION = (
    "visual_target_episode.local_unique_match.v1"
)
ROUND_DETECTOR_MIN_CIRCULARITY = 0.60
CENTER_OVERLAY_MIN_CIRCULARITY = 0.50
_CENTER_PEAK_RELATIVE_HEIGHT = 0.75
_CENTER_PEAK_MIN_RADIUS_PX = 3.0
_CENTER_PEAK_NMS_RADIUS_FACTOR = 1.1
_MULTI_TARGET_POSITION_RESIDUAL_PX = 24.0
_MULTI_TARGET_VELOCITY_RESIDUAL_PX_PER_MS = 1.0
_MULTI_TARGET_MAX_RADIUS_RATIO_DELTA = 0.25
_TARGET_EPISODE_MAX_OBSERVATION_GAP_MS = 50.0

_VERSION_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*\.v[1-9][0-9]*$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._@+-]{0,239}$")
_RUNTIME_SELECTOR_FIELDS = (
    "schema_version",
    "scenario_hash",
    "resolution",
    "canonical_video_mapping_version",
    "fov",
)
_REQUIRED_RUNTIME_SELECTOR_FIELDS = (
    "scenario_hash",
    "resolution",
    "canonical_video_mapping_version",
)
_THRESHOLD_FIELDS = {
    "center_error_median_px",
    "center_error_p95_px",
    "radius_or_hitbox_error_px",
    "false_positive_rate",
    "identity_switch_rate",
    "occlusion_reentry_accuracy",
    "minimum_coverage",
}
_MINIMUM_QUALITY_FIELDS = {"occlusion_reentry_accuracy", "minimum_coverage"}
_METRIC_FAMILIES = {"dynamic_clicking", "tracking", "switching"}
_EVENT_BUNDLE_LIMIT = 512
_MAX_CONTIGUOUS_FRAME_GAP_MS = 100
_TEMPORAL_TRACKER_MAX_GAP_FRAMES = 8
_TEMPORAL_TRACKER_MAX_GAP_MS = 150.0
_TEMPORAL_TRACKER_CONFIDENCE = 0.5


class VisualPreprocessingUnavailable(ValueError):
    """A reviewed visual profile is unavailable for this frozen Run."""

    def __init__(self, code: str) -> None:
        self.code = _bounded_text("visual preprocessing code", code)
        super().__init__(self.code)


def _bounded_text(field: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 240
        or any(ord(char) < 32 for char in value)
        or "://" in value
        or re.search(r"(?:[A-Za-z]:[\\/]|^[/\\]|\\\\)", value)
    ):
        raise ValueError(f"{field} must be safe bounded text")
    return value


def _stable_ref(field: str, value: object) -> str:
    if not isinstance(value, str) or not _REF_RE.fullmatch(value):
        raise ValueError(f"{field} must be a stable ref")
    return value


def _finite(field: str, value: object, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"{field} must be a finite number")
    return number


def _ratio(field: str, value: object) -> float:
    number = _finite(field, value)
    if not 0 <= number <= 1:
        raise ValueError(f"{field} must be between zero and one")
    return number


def _validate_runtime_selector(value: object, field: str) -> dict:
    if not isinstance(value, Mapping) or set(value) != set(_RUNTIME_SELECTOR_FIELDS):
        raise ValueError(f"{field} fields are invalid")
    if value["schema_version"] != "visual_runtime_selector.v1":
        raise ValueError(f"{field}.schema_version is invalid")
    resolution = value["resolution"]
    if (
        not isinstance(resolution, Sequence)
        or isinstance(resolution, (str, bytes))
        or len(resolution) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in resolution)
    ):
        raise ValueError(f"{field}.resolution is invalid")
    mapping_version = _bounded_text(
        f"{field}.canonical_video_mapping_version",
        value["canonical_video_mapping_version"],
    )
    if not _VERSION_RE.fullmatch(mapping_version):
        raise ValueError(f"{field}.canonical_video_mapping_version is not versioned")
    raw_fov = value["fov"]
    selector = {
        "schema_version": "visual_runtime_selector.v1",
        "scenario_hash": _bounded_text(f"{field}.scenario_hash", value["scenario_hash"]),
        "resolution": [int(resolution[0]), int(resolution[1])],
        "canonical_video_mapping_version": mapping_version,
        "fov": None if raw_fov is None else _finite(f"{field}.fov", raw_fov, minimum=1.0),
    }
    return selector


def _validate_calibration_context(value: object) -> dict:
    expected = {
        "detector_config_ref",
        "hud_mask_version",
        "annotated_map_or_background_labels",
        "annotated_target_appearance_labels",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("calibration_context fields are invalid")
    hud_mask_version = value["hud_mask_version"]
    if hud_mask_version is not None:
        hud_mask_version = _bounded_text("hud_mask_version", hud_mask_version)
        if not _VERSION_RE.fullmatch(hud_mask_version):
            raise ValueError("hud_mask_version is not versioned")
    normalized_labels: dict[str, list[str]] = {}
    for field in (
        "annotated_map_or_background_labels",
        "annotated_target_appearance_labels",
    ):
        raw_labels = value[field]
        if (
            not isinstance(raw_labels, Sequence)
            or isinstance(raw_labels, (str, bytes))
            or not 1 <= len(raw_labels) <= 16
        ):
            raise ValueError(f"{field} must be a bounded non-empty list")
        labels = [_bounded_text(field, label) for label in raw_labels]
        if len(set(labels)) != len(labels):
            raise ValueError(f"{field} labels are duplicated")
        normalized_labels[field] = labels
    return {
        "detector_config_ref": _stable_ref(
            "detector_config_ref", value["detector_config_ref"]
        ),
        "hud_mask_version": hud_mask_version,
        **normalized_labels,
    }


def build_visual_quality_profile_v2(
    *,
    producer_id: str,
    producer_version: str,
    annotation_set_ref: str,
    annotation_protocol_version: str,
    coordinate_space: str,
    calibration_context: Mapping[str, object],
    validated_selectors: Sequence[Mapping[str, object]],
    required_selector_keys_by_metric_family: Mapping[str, Sequence[str]],
    required_quality_fields_by_metric_family: Mapping[str, Sequence[str]],
    compatibility_predicate_version: str,
    acceptance_thresholds: Mapping[str, object],
    validation_results: Mapping[str, object],
    validated_metric_families: Sequence[str],
    status: str,
    limitations: Sequence[str],
) -> dict:
    """Build one immutable, auditable visual-quality profile."""
    producer_id = _bounded_text("producer_id", producer_id)
    producer_version = _bounded_text("producer_version", producer_version)
    annotation_set_ref = _stable_ref("annotation_set_ref", annotation_set_ref)
    annotation_protocol_version = _bounded_text(
        "annotation_protocol_version", annotation_protocol_version
    )
    compatibility_predicate_version = _bounded_text(
        "compatibility_predicate_version", compatibility_predicate_version
    )
    for field, version in (
        ("producer_version", producer_version),
        ("annotation_protocol_version", annotation_protocol_version),
        ("compatibility_predicate_version", compatibility_predicate_version),
    ):
        if not _VERSION_RE.fullmatch(version):
            raise ValueError(f"{field} is not versioned")
    coordinate_space = _bounded_text("coordinate_space", coordinate_space)
    normalized_calibration_context = _validate_calibration_context(calibration_context)
    if not isinstance(validated_selectors, Sequence) or not 1 <= len(validated_selectors) <= 16:
        raise ValueError("validated_selectors must be a bounded non-empty list")
    selectors = [
        _validate_runtime_selector(selector, f"validated_selectors[{index}]")
        for index, selector in enumerate(validated_selectors)
    ]
    if not isinstance(acceptance_thresholds, Mapping) or set(acceptance_thresholds) != _THRESHOLD_FIELDS:
        raise ValueError("acceptance_thresholds fields are invalid")
    thresholds = {
        key: _finite(f"acceptance_thresholds.{key}", acceptance_thresholds[key], minimum=0.0)
        for key in sorted(_THRESHOLD_FIELDS)
    }
    for key in (
        "false_positive_rate",
        "identity_switch_rate",
        "occlusion_reentry_accuracy",
        "minimum_coverage",
    ):
        _ratio(f"acceptance_thresholds.{key}", thresholds[key])
    if not isinstance(validation_results, Mapping) or set(validation_results) != _THRESHOLD_FIELDS:
        raise ValueError("validation_results fields are invalid")
    results = {
        key: (
            None
            if validation_results[key] is None
            else _finite(
                f"validation_results.{key}", validation_results[key], minimum=0.0
            )
        )
        for key in sorted(_THRESHOLD_FIELDS)
    }
    for key in (
        "false_positive_rate",
        "identity_switch_rate",
        "occlusion_reentry_accuracy",
        "minimum_coverage",
    ):
        if results[key] is not None:
            _ratio(f"validation_results.{key}", results[key])
    families = [_bounded_text("validated_metric_family", item) for item in validated_metric_families]
    if not families or len(families) > len(_METRIC_FAMILIES) or len(set(families)) != len(families):
        raise ValueError("validated_metric_families are invalid")
    if set(families) - _METRIC_FAMILIES:
        raise ValueError("validated_metric_families contain an unknown family")
    if (
        not isinstance(required_selector_keys_by_metric_family, Mapping)
        or set(required_selector_keys_by_metric_family) != set(families)
    ):
        raise ValueError("required selector keys must cover each metric family")
    required_keys: dict[str, list[str]] = {}
    selectable_fields = set(_RUNTIME_SELECTOR_FIELDS) - {"schema_version"}
    for family in families:
        raw_keys = required_selector_keys_by_metric_family[family]
        if (
            not isinstance(raw_keys, Sequence)
            or isinstance(raw_keys, (str, bytes))
            or len(set(raw_keys)) != len(raw_keys)
            or not set(_REQUIRED_RUNTIME_SELECTOR_FIELDS) <= set(raw_keys)
            or set(raw_keys) - selectable_fields
        ):
            raise ValueError(f"required selector keys for {family} are invalid")
        keys = [field for field in _RUNTIME_SELECTOR_FIELDS if field in raw_keys]
        if "fov" in keys and any(selector["fov"] is None for selector in selectors):
            raise ValueError(f"validated selector fov is required for {family}")
        required_keys[family] = keys
    if (
        not isinstance(required_quality_fields_by_metric_family, Mapping)
        or set(required_quality_fields_by_metric_family) != set(families)
    ):
        raise ValueError("required quality fields must cover each metric family")
    required_quality_fields: dict[str, list[str]] = {}
    quality_status_by_family: dict[str, str] = {}
    for family in families:
        raw_fields = required_quality_fields_by_metric_family[family]
        if (
            not isinstance(raw_fields, Sequence)
            or isinstance(raw_fields, (str, bytes))
            or not raw_fields
            or len(set(raw_fields)) != len(raw_fields)
            or "minimum_coverage" not in raw_fields
            or set(raw_fields) - _THRESHOLD_FIELDS
        ):
            raise ValueError(f"required quality fields for {family} are invalid")
        fields = [field for field in sorted(_THRESHOLD_FIELDS) if field in raw_fields]
        required_quality_fields[family] = fields
        failed = [
            field
            for field in fields
            if results[field] is None
            or (
                results[field] < thresholds[field]
                if field in _MINIMUM_QUALITY_FIELDS
                else results[field] > thresholds[field]
            )
        ]
        coverage = results["minimum_coverage"]
        quality_status_by_family[family] = (
            "accepted"
            if not failed
            else "limited"
            if coverage is not None and coverage > 0
            else "rejected"
        )
    derived_status = (
        "accepted"
        if all(value == "accepted" for value in quality_status_by_family.values())
        else "limited"
        if any(value != "rejected" for value in quality_status_by_family.values())
        else "rejected"
    )
    if status not in {"accepted", "limited", "rejected"}:
        raise ValueError("visual quality profile status is invalid")
    if status != derived_status:
        raise ValueError("visual quality profile status disagrees with validation results")
    limitation_list = [_bounded_text("limitation", item) for item in limitations]
    if len(limitation_list) > 16 or len(set(limitation_list)) != len(limitation_list):
        raise ValueError("limitations are invalid")
    return {
        "schema_version": VISUAL_QUALITY_PROFILE_SCHEMA_VERSION,
        "profile_ref": f"visual-quality:{producer_id}@{producer_version}",
        "producer_id": producer_id,
        "producer_version": producer_version,
        "annotation_set_ref": annotation_set_ref,
        "annotation_protocol_version": annotation_protocol_version,
        "coordinate_space": coordinate_space,
        "calibration_context": normalized_calibration_context,
        "validated_selectors": selectors,
        "required_selector_keys_by_metric_family": required_keys,
        "required_quality_fields_by_metric_family": required_quality_fields,
        "quality_status_by_metric_family": quality_status_by_family,
        "compatibility_predicate_version": compatibility_predicate_version,
        "acceptance_thresholds": thresholds,
        "validation_results": results,
        "validated_metric_families": families,
        "status": status,
        "limitations": limitation_list,
    }


def _validate_profile(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError("visual quality profile must be a mapping")
    expected = {
        "schema_version", "profile_ref", "producer_id", "producer_version",
        "annotation_set_ref", "annotation_protocol_version", "coordinate_space",
        "calibration_context",
        "validated_selectors", "required_selector_keys_by_metric_family",
        "required_quality_fields_by_metric_family", "quality_status_by_metric_family",
        "compatibility_predicate_version",
        "acceptance_thresholds", "validation_results", "validated_metric_families",
        "status", "limitations",
    }
    if set(value) != expected or value.get("schema_version") != VISUAL_QUALITY_PROFILE_SCHEMA_VERSION:
        raise ValueError("visual quality profile fields are invalid")
    rebuilt = build_visual_quality_profile_v2(
        producer_id=value["producer_id"],
        producer_version=value["producer_version"],
        annotation_set_ref=value["annotation_set_ref"],
        annotation_protocol_version=value["annotation_protocol_version"],
        coordinate_space=value["coordinate_space"],
        calibration_context=value["calibration_context"],
        validated_selectors=value["validated_selectors"],
        required_selector_keys_by_metric_family=(
            value["required_selector_keys_by_metric_family"]
        ),
        required_quality_fields_by_metric_family=(
            value["required_quality_fields_by_metric_family"]
        ),
        compatibility_predicate_version=value["compatibility_predicate_version"],
        acceptance_thresholds=value["acceptance_thresholds"],
        validation_results=value["validation_results"],
        validated_metric_families=value["validated_metric_families"],
        status=value["status"],
        limitations=value["limitations"],
    )
    if rebuilt != dict(value):
        raise ValueError("visual quality profile is not canonical")
    return rebuilt


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def _validate_annotation_target(
    value: object,
    *,
    identity_field: str,
    field: str,
) -> dict:
    expected = {identity_field, "x", "y", "visible_radius"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{field} fields are invalid")
    return {
        identity_field: _bounded_text(f"{field}.{identity_field}", value[identity_field]),
        "x": _finite(f"{field}.x", value["x"]),
        "y": _finite(f"{field}.y", value["y"]),
        "visible_radius": _finite(
            f"{field}.visible_radius", value["visible_radius"], minimum=0.0
        ),
    }


def _validate_annotation_frames(
    values: object,
    *,
    identity_field: str,
    field: str,
) -> list[dict]:
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or not values
        or len(values) > 10_000
    ):
        raise ValueError(f"{field} must be a bounded non-empty sequence")
    frames: list[dict] = []
    seen_indices: set[int] = set()
    for frame_position, value in enumerate(values):
        if not isinstance(value, Mapping) or set(value) != {"frame_index", "targets"}:
            raise ValueError(f"{field}[{frame_position}] fields are invalid")
        frame_index = value["frame_index"]
        targets = value["targets"]
        if (
            isinstance(frame_index, bool)
            or not isinstance(frame_index, int)
            or frame_index < 0
            or frame_index in seen_indices
            or not isinstance(targets, Sequence)
            or isinstance(targets, (str, bytes))
            or len(targets) > 64
        ):
            raise ValueError(f"{field}[{frame_position}] is invalid")
        seen_indices.add(frame_index)
        normalized_targets = [
            _validate_annotation_target(
                target,
                identity_field=identity_field,
                field=f"{field}[{frame_position}].targets[{target_index}]",
            )
            for target_index, target in enumerate(targets)
        ]
        identities = [target[identity_field] for target in normalized_targets]
        if len(set(identities)) != len(identities):
            raise ValueError(f"{field}[{frame_position}] target identities are duplicated")
        frames.append({"frame_index": frame_index, "targets": normalized_targets})
    return sorted(frames, key=lambda frame: frame["frame_index"])


def build_visual_annotation_ledger_v1(
    *,
    source_ref: str,
    source_sha256: str,
    annotation_protocol_version: str,
    reviewer_ref: str,
    review_round: int,
    frames: Sequence[Mapping[str, object]],
) -> dict:
    """Build a bounded, path-free ledger for independently reviewed frames."""
    source_ref = _stable_ref("source_ref", source_ref)
    if (
        not isinstance(source_sha256, str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256)
    ):
        raise ValueError("source_sha256 must be a SHA-256 hex digest")
    annotation_protocol_version = _bounded_text(
        "annotation_protocol_version", annotation_protocol_version
    )
    if not _VERSION_RE.fullmatch(annotation_protocol_version):
        raise ValueError("annotation_protocol_version is not versioned")
    reviewer_ref = _stable_ref("reviewer_ref", reviewer_ref)
    if (
        isinstance(review_round, bool)
        or not isinstance(review_round, int)
        or not 1 <= review_round <= 16
    ):
        raise ValueError("review_round is invalid")
    return {
        "schema_version": VISUAL_ANNOTATION_LEDGER_SCHEMA_VERSION,
        "source_ref": source_ref,
        "source_sha256": source_sha256.lower(),
        "annotation_protocol_version": annotation_protocol_version,
        "reviewer_ref": reviewer_ref,
        "review_round": review_round,
        "frames": _validate_annotation_frames(
            frames, identity_field="target_id", field="frames"
        ),
    }


def _source_sha256(field: str, value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return value.lower()


def _validate_annotation_frames_v2(values: object) -> list[dict]:
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or not values
        or len(values) > 10_000
    ):
        raise ValueError("frames must be a bounded non-empty sequence")
    frames: list[dict] = []
    seen_indices: set[int] = set()
    target_states = {"visible", "occluded", "merged", "hud_excluded"}
    for frame_position, value in enumerate(values):
        if not isinstance(value, Mapping) or set(value) != {"frame_index", "targets"}:
            raise ValueError(f"frames[{frame_position}] fields are invalid")
        frame_index = value["frame_index"]
        targets = value["targets"]
        if (
            isinstance(frame_index, bool)
            or not isinstance(frame_index, int)
            or frame_index < 0
            or frame_index in seen_indices
            or not isinstance(targets, Sequence)
            or isinstance(targets, (str, bytes))
            or len(targets) > 64
        ):
            raise ValueError(f"frames[{frame_position}] is invalid")
        seen_indices.add(frame_index)
        normalized_targets: list[dict] = []
        seen_target_ids: set[str] = set()
        for target_position, target in enumerate(targets):
            field = f"frames[{frame_position}].targets[{target_position}]"
            expected = {
                "target_id", "state", "x", "y", "visible_radius",
                "exclusion_reason",
            }
            if not isinstance(target, Mapping) or set(target) != expected:
                raise ValueError(f"{field} fields are invalid")
            target_id = _bounded_text(f"{field}.target_id", target["target_id"])
            state = target["state"]
            if state not in target_states or target_id in seen_target_ids:
                raise ValueError(f"{field} is invalid")
            seen_target_ids.add(target_id)
            geometry_required = state in {"visible", "merged"}
            raw_geometry = (target["x"], target["y"], target["visible_radius"])
            if geometry_required:
                x = _finite(f"{field}.x", raw_geometry[0])
                y = _finite(f"{field}.y", raw_geometry[1])
                radius = _finite(
                    f"{field}.visible_radius", raw_geometry[2], minimum=0.0
                )
            elif any(item is not None for item in raw_geometry):
                raise ValueError(f"{field} geometry is invalid for {state}")
            else:
                x = y = radius = None
            exclusion_reason = target["exclusion_reason"]
            if state == "hud_excluded":
                exclusion_reason = _bounded_text(
                    f"{field}.exclusion_reason", exclusion_reason
                )
            elif exclusion_reason is not None:
                raise ValueError(f"{field}.exclusion_reason is invalid")
            normalized_targets.append({
                "target_id": target_id,
                "state": state,
                "x": x,
                "y": y,
                "visible_radius": radius,
                "exclusion_reason": exclusion_reason,
            })
        frames.append({"frame_index": frame_index, "targets": normalized_targets})
    return sorted(frames, key=lambda frame: frame["frame_index"])


def _validate_click_windows(values: object, *, frame_indices: set[int]) -> list[dict]:
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or not values
        or len(values) > 10_000
    ):
        raise ValueError("click windows must be a bounded non-empty sequence")
    windows: list[dict] = []
    seen_refs: set[str] = set()
    for position, value in enumerate(values):
        if not isinstance(value, Mapping) or set(value) != {
            "click_ref", "frame_index", "status",
        }:
            raise ValueError(f"click window {position} fields are invalid")
        click_ref = _stable_ref(f"click window {position}.click_ref", value["click_ref"])
        frame_index = value["frame_index"]
        if (
            click_ref in seen_refs
            or isinstance(frame_index, bool)
            or not isinstance(frame_index, int)
            or frame_index not in frame_indices
            or value["status"] not in {"annotated", "hud_excluded", "ambiguous"}
        ):
            raise ValueError(f"click window {position} is invalid")
        seen_refs.add(click_ref)
        windows.append({
            "click_ref": click_ref,
            "frame_index": frame_index,
            "status": value["status"],
        })
    return sorted(windows, key=lambda item: item["click_ref"])


def build_visual_annotation_ledger_v2(
    *,
    dataset_role: str,
    source_ref: str,
    source_sha256: str,
    scenario_hash: str,
    visual_runtime_selector: Mapping[str, object],
    detector_config_ref: str,
    annotation_protocol_version: str,
    annotator_ref: str,
    reviewer_ref: str,
    review_round: int,
    frames: Sequence[Mapping[str, object]],
    click_windows: Sequence[Mapping[str, object]],
) -> dict:
    """Build a path-free Dynamic ledger with explicit review and click coverage."""
    if dataset_role not in {"calibration", "untouched_holdout"}:
        raise ValueError("dataset_role is invalid")
    source_ref = _stable_ref("source_ref", source_ref)
    source_sha256 = _source_sha256("source_sha256", source_sha256)
    scenario_hash = _bounded_text("scenario_hash", scenario_hash)
    selector = _validate_runtime_selector(
        visual_runtime_selector, "visual_runtime_selector"
    )
    if selector["scenario_hash"] != scenario_hash:
        raise ValueError("visual runtime selector scenario hash is invalid")
    detector_config_ref = _stable_ref("detector_config_ref", detector_config_ref)
    annotation_protocol_version = _bounded_text(
        "annotation_protocol_version", annotation_protocol_version
    )
    if not _VERSION_RE.fullmatch(annotation_protocol_version):
        raise ValueError("annotation_protocol_version is not versioned")
    annotator_ref = _stable_ref("annotator_ref", annotator_ref)
    reviewer_ref = _stable_ref("reviewer_ref", reviewer_ref)
    if annotator_ref == reviewer_ref:
        raise ValueError("annotation requires an independent reviewer")
    if (
        isinstance(review_round, bool)
        or not isinstance(review_round, int)
        or not 1 <= review_round <= 16
    ):
        raise ValueError("review_round is invalid")
    normalized_frames = _validate_annotation_frames_v2(frames)
    normalized_windows = _validate_click_windows(
        click_windows,
        frame_indices={frame["frame_index"] for frame in normalized_frames},
    )
    return {
        "schema_version": VISUAL_ANNOTATION_LEDGER_V2_SCHEMA_VERSION,
        "dataset_role": dataset_role,
        "source_ref": source_ref,
        "source_sha256": source_sha256,
        "scenario_hash": scenario_hash,
        "visual_runtime_selector": selector,
        "detector_config_ref": detector_config_ref,
        "annotation_protocol_version": annotation_protocol_version,
        "annotator_ref": annotator_ref,
        "reviewer_ref": reviewer_ref,
        "review_round": review_round,
        "frames": normalized_frames,
        "click_windows": normalized_windows,
    }


def _validate_annotation_ledger_v2(value: object, field: str) -> dict:
    if not isinstance(value, Mapping) or value.get("schema_version") != (
        VISUAL_ANNOTATION_LEDGER_V2_SCHEMA_VERSION
    ):
        raise ValueError(f"{field} is invalid")
    rebuilt = build_visual_annotation_ledger_v2(**{
        key: item for key, item in value.items() if key != "schema_version"
    })
    if rebuilt != dict(value):
        raise ValueError(f"{field} is not canonical")
    return rebuilt


def _canonical_sha256(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_visual_calibration_holdout_split_v1(
    *,
    calibration_ledger: Mapping[str, object],
    holdout_ledger: Mapping[str, object],
    expected_source_sha256_by_role: Mapping[str, object],
) -> dict:
    """Freeze two distinct, exact-condition ledgers before detector tuning."""
    calibration = _validate_annotation_ledger_v2(
        calibration_ledger, "calibration_ledger"
    )
    holdout = _validate_annotation_ledger_v2(holdout_ledger, "holdout_ledger")
    if calibration["dataset_role"] != "calibration" or holdout[
        "dataset_role"
    ] != "untouched_holdout":
        raise ValueError("calibration and holdout roles are invalid")
    if (
        calibration["source_ref"] == holdout["source_ref"]
        or calibration["source_sha256"] == holdout["source_sha256"]
    ):
        raise ValueError("calibration and holdout source overlap")
    if not isinstance(expected_source_sha256_by_role, Mapping) or set(
        expected_source_sha256_by_role
    ) != {"calibration", "untouched_holdout"}:
        raise ValueError("expected source digests are invalid")
    for role, ledger in (
        ("calibration", calibration), ("untouched_holdout", holdout),
    ):
        expected_digest = _source_sha256(
            f"expected_source_sha256_by_role.{role}",
            expected_source_sha256_by_role[role],
        )
        if ledger["source_sha256"] != expected_digest:
            raise ValueError(f"{role} source digest mismatch")
    if (
        calibration["scenario_hash"] != holdout["scenario_hash"]
        or calibration["visual_runtime_selector"]
        != holdout["visual_runtime_selector"]
        or calibration["detector_config_ref"] != holdout["detector_config_ref"]
    ):
        raise ValueError("calibration and holdout selector mismatch")
    return {
        "schema_version": "visual_calibration_holdout_split.v1",
        "scenario_hash": calibration["scenario_hash"],
        "visual_runtime_selector": calibration["visual_runtime_selector"],
        "detector_config_ref": calibration["detector_config_ref"],
        "calibration": {
            "source_ref": calibration["source_ref"],
            "source_sha256": calibration["source_sha256"],
            "ledger_sha256": _canonical_sha256(calibration),
        },
        "untouched_holdout": {
            "source_ref": holdout["source_ref"],
            "source_sha256": holdout["source_sha256"],
            "ledger_sha256": _canonical_sha256(holdout),
        },
    }


def evaluate_visual_annotation_quality_v1(
    *,
    annotations: Sequence[Mapping[str, object]],
    predictions: Sequence[Mapping[str, object]],
    maximum_match_distance_px: float,
) -> dict:
    """Measure detector output against an independently reviewed sparse ledger."""
    from scipy.optimize import linear_sum_assignment

    maximum_distance = _finite(
        "maximum_match_distance_px", maximum_match_distance_px, minimum=0.0
    )
    annotated_frames = _validate_annotation_frames(
        annotations, identity_field="target_id", field="annotations"
    )
    predicted_frames = _validate_annotation_frames(
        predictions, identity_field="track_id", field="predictions"
    )
    annotated_indices = {frame["frame_index"] for frame in annotated_frames}
    if any(frame["frame_index"] not in annotated_indices for frame in predicted_frames):
        raise ValueError("predictions contain an unannotated frame")
    predictions_by_frame = {
        frame["frame_index"]: frame["targets"] for frame in predicted_frames
    }

    center_errors: list[float] = []
    radius_errors: list[float] = []
    annotated_target_count = 0
    predicted_target_count = 0
    matched_target_count = 0
    false_positive_count = 0
    identity_comparisons = 0
    identity_switches = 0
    occlusion_reentries = 0
    correct_occlusion_reentries = 0
    identity_state: dict[str, dict[str, object]] = {}

    for frame in annotated_frames:
        truth_targets = frame["targets"]
        predicted_targets = predictions_by_frame.get(frame["frame_index"], [])
        annotated_target_count += len(truth_targets)
        predicted_target_count += len(predicted_targets)
        current_truth_ids = {target["target_id"] for target in truth_targets}
        previously_present = {
            target_id
            for target_id, state in identity_state.items()
            if state["present_in_previous_frame"]
        }
        for target_id, state in identity_state.items():
            if state["seen"] and target_id not in current_truth_ids:
                state["occluded"] = True
            state["present_in_previous_frame"] = False

        matched_truth_to_track: dict[str, str] = {}
        matched_prediction_indices: set[int] = set()
        if truth_targets and predicted_targets:
            distances = [
                [
                    math.hypot(
                        truth["x"] - prediction["x"],
                        truth["y"] - prediction["y"],
                    )
                    for prediction in predicted_targets
                ]
                for truth in truth_targets
            ]
            pair_count = min(len(truth_targets), len(predicted_targets))
            infeasible_cost = (pair_count + 1) * (maximum_distance + 1.0)
            assignment_costs = [
                [
                    distance if distance <= maximum_distance else infeasible_cost
                    for distance in row
                ]
                for row in distances
            ]
            truth_indices, prediction_indices = linear_sum_assignment(assignment_costs)
            for truth_index, prediction_index in zip(
                truth_indices.tolist(), prediction_indices.tolist()
            ):
                distance = float(distances[truth_index][prediction_index])
                if distance > maximum_distance:
                    continue
                truth = truth_targets[truth_index]
                prediction = predicted_targets[prediction_index]
                matched_target_count += 1
                matched_prediction_indices.add(prediction_index)
                matched_truth_to_track[truth["target_id"]] = prediction["track_id"]
                center_errors.append(distance)
                radius_errors.append(
                    abs(truth["visible_radius"] - prediction["visible_radius"])
                )
        false_positive_count += len(predicted_targets) - len(matched_prediction_indices)

        for truth in truth_targets:
            target_id = truth["target_id"]
            track_id = matched_truth_to_track.get(target_id)
            state = identity_state.setdefault(target_id, {
                "seen": False,
                "occluded": False,
                "last_track_id": None,
                "present_in_previous_frame": False,
            })
            previous_track_id = state["last_track_id"]
            if state["occluded"]:
                occlusion_reentries += 1
                if track_id is not None and track_id == previous_track_id:
                    correct_occlusion_reentries += 1
            elif target_id in previously_present:
                if track_id is not None and previous_track_id is not None:
                    identity_comparisons += 1
                    if track_id != previous_track_id:
                        identity_switches += 1
            if track_id is not None:
                state["last_track_id"] = track_id
            state["seen"] = True
            state["occluded"] = False
            state["present_in_previous_frame"] = True

    limitations: list[str] = []
    if not center_errors:
        limitations.append("target_localization_not_observed")
    if identity_comparisons == 0:
        limitations.append("identity_continuity_not_observed")
    if occlusion_reentries == 0:
        limitations.append("occlusion_reentry_not_observed")
    metrics = {
        "center_error_median_px": _percentile(center_errors, 0.5),
        "center_error_p95_px": _percentile(center_errors, 0.95),
        "radius_or_hitbox_error_px": _percentile(radius_errors, 0.5),
        "false_positive_rate": (
            false_positive_count / predicted_target_count
            if predicted_target_count
            else 0.0
        ),
        "identity_switch_rate": (
            identity_switches / identity_comparisons
            if identity_comparisons
            else None
        ),
        "occlusion_reentry_accuracy": (
            correct_occlusion_reentries / occlusion_reentries
            if occlusion_reentries
            else None
        ),
        "minimum_coverage": (
            matched_target_count / annotated_target_count
            if annotated_target_count
            else 0.0
        ),
    }
    return {
        "schema_version": "visual_annotation_quality.v1",
        "metrics": metrics,
        "counts": {
            "annotated_targets": annotated_target_count,
            "predicted_targets": predicted_target_count,
            "matched_targets": matched_target_count,
            "false_positives": false_positive_count,
            "identity_comparisons": identity_comparisons,
            "identity_switches": identity_switches,
            "occlusion_reentries": occlusion_reentries,
            "correct_occlusion_reentries": correct_occlusion_reentries,
        },
        "limitations": limitations,
    }


def _localization_annotations_from_v2(
    ledger: Mapping[str, object],
) -> tuple[list[dict], int]:
    scored_frames = [
        frame
        for frame in ledger["frames"]
        if all(target["state"] == "visible" for target in frame["targets"])
    ]
    annotations = [
        {
            "frame_index": frame["frame_index"],
            "targets": [
                {
                    "target_id": target["target_id"],
                    "x": target["x"],
                    "y": target["y"],
                    "visible_radius": target["visible_radius"],
                }
                for target in frame["targets"]
                if target["state"] == "visible"
            ],
        }
        for frame in scored_frames
    ]
    return annotations, len(ledger["frames"]) - len(scored_frames)


def _predictions_for_ledger(
    predictions: object,
    *,
    frame_indices: set[int],
    field: str,
) -> list[dict]:
    if not isinstance(predictions, Sequence) or isinstance(predictions, (str, bytes)):
        raise ValueError(f"{field} is invalid")
    selected: list[Mapping[str, object]] = []
    seen: set[int] = set()
    for position, prediction in enumerate(predictions):
        if not isinstance(prediction, Mapping):
            raise ValueError(f"{field}[{position}] is invalid")
        frame_index = prediction.get("frame_index")
        if frame_index in frame_indices:
            if frame_index in seen:
                raise ValueError(f"{field} contains duplicate frames")
            seen.add(frame_index)
            selected.append(prediction)
    missing = frame_indices - seen
    if missing:
        selected.extend({"frame_index": index, "targets": []} for index in missing)
    return _validate_annotation_frames(
        selected, identity_field="track_id", field=field
    )


def _localization_quality_only(result: Mapping[str, object]) -> dict:
    normalized = copy.deepcopy(dict(result))
    normalized["metrics"]["identity_switch_rate"] = None
    normalized["metrics"]["occlusion_reentry_accuracy"] = None
    for field in (
        "identity_comparisons", "identity_switches", "occlusion_reentries",
        "correct_occlusion_reentries",
    ):
        normalized["counts"][field] = 0
    normalized["limitations"] = sorted(set([
        *normalized["limitations"],
        "identity_continuity_not_observed",
        "occlusion_reentry_not_observed",
    ]))
    return normalized


def _localization_quality_accepted(
    metrics: Mapping[str, object], thresholds: Mapping[str, float]
) -> bool:
    maximum_fields = (
        "center_error_median_px", "center_error_p95_px",
        "radius_or_hitbox_error_px", "false_positive_rate",
    )
    return (
        all(
            metrics[field] is not None
            and float(metrics[field]) <= thresholds[field]
            for field in maximum_fields
        )
        and metrics["minimum_coverage"] is not None
        and float(metrics["minimum_coverage"])
        >= thresholds["minimum_coverage"]
    )


def evaluate_visual_split_quality_v1(
    *,
    calibration_ledger: Mapping[str, object],
    calibration_predictions: Sequence[Mapping[str, object]],
    holdout_ledger: Mapping[str, object],
    holdout_predictions: Sequence[Mapping[str, object]],
    maximum_match_distance_px: float,
    acceptance_thresholds: Mapping[str, object],
) -> dict:
    """Require calibration and untouched holdout localization to pass separately."""
    calibration = _validate_annotation_ledger_v2(
        calibration_ledger, "calibration_ledger"
    )
    holdout = _validate_annotation_ledger_v2(holdout_ledger, "holdout_ledger")
    validate_visual_calibration_holdout_split_v1(
        calibration_ledger=calibration,
        holdout_ledger=holdout,
        expected_source_sha256_by_role={
            "calibration": calibration["source_sha256"],
            "untouched_holdout": holdout["source_sha256"],
        },
    )
    required_fields = {
        "center_error_median_px", "center_error_p95_px",
        "radius_or_hitbox_error_px", "false_positive_rate", "minimum_coverage",
    }
    if not isinstance(acceptance_thresholds, Mapping) or set(
        acceptance_thresholds
    ) != required_fields:
        raise ValueError("localization acceptance thresholds are invalid")
    thresholds = {
        field: (
            _ratio(field, acceptance_thresholds[field])
            if field in {"false_positive_rate", "minimum_coverage"}
            else _finite(field, acceptance_thresholds[field], minimum=0.0)
        )
        for field in sorted(required_fields)
    }

    results: dict[str, dict] = {}
    for role, ledger, predictions in (
        ("calibration", calibration, calibration_predictions),
        ("untouched_holdout", holdout, holdout_predictions),
    ):
        annotations, excluded_frame_count = _localization_annotations_from_v2(
            ledger
        )
        frame_indices = {item["frame_index"] for item in annotations}
        quality = _localization_quality_only(evaluate_visual_annotation_quality_v1(
            annotations=annotations,
            predictions=_predictions_for_ledger(
                predictions,
                frame_indices=frame_indices,
                field=f"{role}_predictions",
            ),
            maximum_match_distance_px=maximum_match_distance_px,
        ))
        quality["excluded_frame_count"] = excluded_frame_count
        results[role] = {
            "accepted": _localization_quality_accepted(
                quality["metrics"], thresholds
            ),
            "quality": quality,
        }

    maximum_fields = (
        "center_error_median_px", "center_error_p95_px",
        "radius_or_hitbox_error_px", "false_positive_rate",
    )
    validation_results = {
        field: max(
            float(results[role]["quality"]["metrics"][field])
            for role in ("calibration", "untouched_holdout")
            if results[role]["quality"]["metrics"][field] is not None
        )
        for field in maximum_fields
    }
    validation_results["minimum_coverage"] = min(
        float(results[role]["quality"]["metrics"]["minimum_coverage"])
        for role in ("calibration", "untouched_holdout")
    )
    validation_results["identity_switch_rate"] = None
    validation_results["occlusion_reentry_accuracy"] = None
    return {
        "schema_version": "visual_split_quality.v1",
        "status": (
            "accepted"
            if all(results[role]["accepted"] for role in results)
            else "rejected"
        ),
        "acceptance_thresholds": thresholds,
        "calibration": results["calibration"],
        "untouched_holdout": results["untouched_holdout"],
        "validation_results": validation_results,
        "limitations": [
            "identity_continuity_not_observed",
            "occlusion_reentry_not_observed",
        ],
    }


def evaluate_visual_runtime_compatibility_v2(
    visual_quality_profile: Mapping[str, object],
    visual_runtime_selector: Mapping[str, object],
) -> dict:
    """Fail closed unless the runtime selector matches a calibrated profile."""
    profile = _validate_profile(visual_quality_profile)
    observed = _validate_runtime_selector(
        visual_runtime_selector, "visual_runtime_selector"
    )
    enabled_families: list[str] = []
    compatibility_limitations: list[str] = []
    for family in profile["validated_metric_families"]:
        if profile["quality_status_by_metric_family"][family] != "accepted":
            for field in profile["required_quality_fields_by_metric_family"][family]:
                result = profile["validation_results"][field]
                threshold = profile["acceptance_thresholds"][field]
                if result is None or (
                    result < threshold
                    if field in _MINIMUM_QUALITY_FIELDS
                    else result > threshold
                ):
                    compatibility_limitations.append(
                        f"visual_quality_below_threshold:{family}:{field}"
                    )
            continue
        required_keys = profile["required_selector_keys_by_metric_family"][family]
        best_mismatches: list[str] | None = None
        for selector in profile["validated_selectors"]:
            mismatches = [
                field for field in required_keys
                if observed[field] != selector[field]
            ]
            if best_mismatches is None or len(mismatches) < len(best_mismatches):
                best_mismatches = mismatches
            if not mismatches:
                enabled_families.append(family)
                break
        else:
            compatibility_limitations.extend(
                f"visual_selector_mismatch:{family}:{field}"
                for field in (best_mismatches or required_keys)
            )
    return {
        "status": (
            "accepted" if len(enabled_families) == len(profile["validated_metric_families"])
            else "limited" if enabled_families
            else "rejected"
        ),
        "enabled_metric_families": enabled_families,
        "limitations": [*profile["limitations"], *compatibility_limitations],
    }


def detect_color_candidates_v1(
    image,
    *,
    hsv_lower: Sequence[int],
    hsv_upper: Sequence[int],
    min_area: float,
    max_area_ratio: float,
    shape: str,
    excluded_regions: Sequence[Sequence[float]] = (),
) -> list[dict]:
    """Adapt the shared legacy vision primitive to the visual signal contract."""
    import numpy as np

    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("detector image must be a BGR array")
    if (
        len(hsv_lower) != 3
        or len(hsv_upper) != 3
        or any(isinstance(item, bool) or not isinstance(item, int) for item in [*hsv_lower, *hsv_upper])
    ):
        raise ValueError("HSV bounds are invalid")
    if shape not in {"round", "any"}:
        raise ValueError("detector shape is invalid")
    min_area = _finite("min_area", min_area, minimum=0.0)
    max_area_ratio = _ratio("max_area_ratio", max_area_ratio)
    if max_area_ratio == 0:
        raise ValueError("max_area_ratio must be positive")
    normalized_regions = _validate_excluded_regions(excluded_regions)

    from .vision import detect_color_blobs

    blobs = detect_color_blobs(
        image,
        np.asarray(hsv_lower, dtype=np.uint8),
        np.asarray(hsv_upper, dtype=np.uint8),
        min_area=min_area,
        max_area_ratio=max_area_ratio,
        min_circularity=ROUND_DETECTOR_MIN_CIRCULARITY if shape == "round" else None,
    )
    height, width = image.shape[:2]
    return [{
        "x": blob["x"],
        "y": blob["y"],
        "visible_radius": blob["visible_radius"],
        "confidence": blob["confidence"],
    } for blob in blobs if not any(
        left * width <= blob["x"] <= right * width
        and top * height <= blob["y"] <= bottom * height
        for left, top, right, bottom in normalized_regions
    )]


def _component_peak_count_v1(contour) -> int:
    """Count at most two spatially separate distance-transform peaks."""
    import cv2
    import numpy as np

    x, y, width, height = cv2.boundingRect(contour)
    if width <= 0 or height <= 0:
        return 0
    shifted = contour.copy()
    shifted[:, 0, 0] -= x
    shifted[:, 0, 1] -= y
    component = np.zeros((height, width), dtype=np.uint8)
    cv2.drawContours(component, [shifted], -1, 255, thickness=-1)
    distance = cv2.distanceTransform(component, cv2.DIST_L2, 5)
    maximum = float(distance.max())
    threshold = max(
        _CENTER_PEAK_MIN_RADIUS_PX,
        maximum * _CENTER_PEAK_RELATIVE_HEIGHT,
    )
    if maximum < threshold:
        return 0

    working = distance.copy()
    suppression_radius = int(math.ceil(max(
        8.0,
        maximum * _CENTER_PEAK_NMS_RADIUS_FACTOR,
    )))
    peak_count = 0
    while True:
        _, value, _, location = cv2.minMaxLoc(working)
        if value < threshold:
            return peak_count
        peak_count += 1
        if peak_count == 2:
            return peak_count
        cv2.circle(working, location, suppression_radius, 0.0, thickness=-1)


def detect_color_observations_v2(
    image,
    *,
    hsv_lower: Sequence[int],
    hsv_upper: Sequence[int],
    min_area: float,
    max_area_ratio: float,
    shape: str,
    excluded_regions: Sequence[Sequence[float]] = (),
    minimum_circularity: float = ROUND_DETECTOR_MIN_CIRCULARITY,
) -> dict:
    """Return trackable targets separately from merged center components."""
    import numpy as np

    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("detector image must be a BGR array")
    if (
        len(hsv_lower) != 3
        or len(hsv_upper) != 3
        or any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in [*hsv_lower, *hsv_upper]
        )
    ):
        raise ValueError("HSV bounds are invalid")
    if shape not in {"round", "any"}:
        raise ValueError("detector shape is invalid")
    min_area = _finite("min_area", min_area, minimum=0.0)
    max_area_ratio = _ratio("max_area_ratio", max_area_ratio)
    minimum_circularity = _ratio(
        "minimum_circularity", minimum_circularity,
    )
    if max_area_ratio == 0:
        raise ValueError("max_area_ratio must be positive")
    normalized_regions = _validate_excluded_regions(excluded_regions)

    from .vision import detect_color_blobs

    blobs = detect_color_blobs(
        image,
        np.asarray(hsv_lower, dtype=np.uint8),
        np.asarray(hsv_upper, dtype=np.uint8),
        min_area=min_area,
        max_area_ratio=max_area_ratio,
        min_circularity=None,
        include_contours=shape == "round",
    )
    height, width = image.shape[:2]
    center_x = width / 2.0
    center_y = height / 2.0
    targets: list[dict] = []
    ambiguities: list[dict] = []
    for blob in blobs:
        x = float(blob["x"])
        y = float(blob["y"])
        radius = float(blob["visible_radius"])
        if any(
            left * width <= x <= right * width
            and top * height <= y <= bottom * height
            for left, top, right, bottom in normalized_regions
        ):
            continue
        circularity = float(blob["circularity"])
        center_overlap = math.hypot(x - center_x, y - center_y) <= radius
        if shape == "round" and center_overlap:
            peak_count = _component_peak_count_v1(blob["_contour"])
            if peak_count >= 2:
                ambiguities.append({
                    "ambiguity_kind": "merged_target_component",
                    "x": x,
                    "y": y,
                    "visible_radius": radius,
                    "confidence": 0.0,
                })
                continue
            accepted = (
                peak_count == 1
                and circularity >= min(
                    CENTER_OVERLAY_MIN_CIRCULARITY, minimum_circularity,
                )
            )
        else:
            accepted = shape == "any" or circularity >= minimum_circularity
        if accepted:
            targets.append({
                "x": x,
                "y": y,
                "visible_radius": radius,
                "confidence": min(1.0, max(0.0, circularity)),
            })
    targets.sort(key=lambda item: (item["x"], item["y"], item["visible_radius"]))
    ambiguities.sort(
        key=lambda item: (item["x"], item["y"], item["visible_radius"])
    )
    return {"targets": targets, "target_ambiguities": ambiguities}


def _validate_excluded_regions(value: object) -> list[list[float]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) > 32
    ):
        raise ValueError("excluded regions are invalid")
    regions: list[list[float]] = []
    for index, region in enumerate(value):
        if (
            not isinstance(region, Sequence)
            or isinstance(region, (str, bytes))
            or len(region) != 4
        ):
            raise ValueError(f"excluded region {index} is invalid")
        left, top, right, bottom = [
            _finite(f"excluded region {index}", item) for item in region
        ]
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError(f"excluded region {index} is invalid")
        regions.append([left, top, right, bottom])
    return regions


def _validate_detector_part(value: object, field: str) -> dict:
    expected = {"hsv_lower", "hsv_upper", "min_area", "max_area_ratio", "shape"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{field} detector config is invalid")
    lower = value["hsv_lower"]
    upper = value["hsv_upper"]
    if (
        not isinstance(lower, Sequence)
        or isinstance(lower, (str, bytes))
        or not isinstance(upper, Sequence)
        or isinstance(upper, (str, bytes))
        or len(lower) != 3
        or len(upper) != 3
    ):
        raise ValueError(f"{field} HSV bounds are invalid")
    normalized_lower = [int(item) for item in lower]
    normalized_upper = [int(item) for item in upper]
    if any(
        isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= limit
        for item, limit in [
            (lower[0], 179), (lower[1], 255), (lower[2], 255),
            (upper[0], 179), (upper[1], 255), (upper[2], 255),
        ]
    ):
        raise ValueError(f"{field} HSV bounds are invalid")
    shape = value["shape"]
    if shape not in {"round", "any"}:
        raise ValueError(f"{field} detector shape is invalid")
    return {
        "hsv_lower": normalized_lower,
        "hsv_upper": normalized_upper,
        "min_area": _finite(f"{field}.min_area", value["min_area"], minimum=0.0),
        "max_area_ratio": _ratio(f"{field}.max_area_ratio", value["max_area_ratio"]),
        "shape": shape,
    }


def _canonical_visual_detector_config(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError("visual detector config is invalid")
    detector_version = value.get("schema_version")
    if detector_version == "visual_target_detector.v1":
        expected_fields = {"schema_version", "aim_point_mode", "target"}
        excluded_regions: list[list[float]] = []
    elif detector_version == "visual_target_detector.v2":
        expected_fields = {
            "schema_version", "aim_point_mode", "target", "excluded_regions",
        }
        excluded_regions = _validate_excluded_regions(value.get("excluded_regions"))
    else:
        raise ValueError("visual detector config is invalid")
    if (
        set(value) != expected_fields
        or value.get("aim_point_mode") != "fixed_viewport_center"
    ):
        raise ValueError("visual detector config is invalid")
    canonical = {
        "schema_version": detector_version,
        "aim_point_mode": "fixed_viewport_center",
        "target": _validate_detector_part(value["target"], "target"),
    }
    if detector_version == "visual_target_detector.v2":
        canonical["excluded_regions"] = excluded_regions
    return canonical


def visual_detector_config_ref_v1(value: object) -> str:
    """Return the stable ref for the exact validated detector configuration."""
    canonical = _canonical_visual_detector_config(value)
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"detector-config:sha256:{hashlib.sha256(encoded).hexdigest()}"


def preprocess_visual_video_v1(
    *,
    media_path: str,
    analysis_ref: str,
    canonical_time_window: Mapping[str, object],
    visual_quality_profile: Mapping[str, object],
    visual_runtime_selector: Mapping[str, object],
    video_time_mapping: Mapping[str, object],
    detector_config: Mapping[str, object],
    outcome_observations: Sequence[Mapping[str, object]] = (),
    source_ref: str | None = None,
) -> dict:
    """Decode a local media file and immediately discard all image arrays."""
    import cv2

    if not isinstance(media_path, str) or not media_path:
        raise ValueError("local media source is required")
    profile = _validate_profile(visual_quality_profile)
    episode_mode = (
        profile["producer_id"] == VISUAL_TARGET_EPISODE_PRODUCER_ID
        and profile["producer_version"] == VISUAL_TARGET_EPISODE_PRODUCER_VERSION
    )
    if not episode_mode and (
        profile["producer_id"] != VISUAL_PRODUCER_ID
        or profile["producer_version"] != VISUAL_PRODUCER_VERSION
    ):
        raise ValueError("visual producer profile is not current")
    canonical_detector_config = _canonical_visual_detector_config(detector_config)
    if (
        profile["calibration_context"]["detector_config_ref"]
        != visual_detector_config_ref_v1(canonical_detector_config)
    ):
        raise ValueError("visual detector config does not match quality profile")
    excluded_regions = canonical_detector_config.get("excluded_regions", [])
    target_config = canonical_detector_config["target"]
    selector = _validate_runtime_selector(
        visual_runtime_selector, "visual_runtime_selector"
    )
    window, _, _ = _window_bounds(canonical_time_window)
    _validate_video_time_mapping(
        video_time_mapping,
        canonical_time_window=window,
    )
    frame_budget = min(
        250_000,
        max(1, int(math.ceil(window["duration_ms"] / 1_000 * 240)) + 240),
    )

    capture = cv2.VideoCapture(media_path)
    observations: list[dict] = []
    try:
        if not capture.isOpened():
            raise ValueError("local media decoder is unavailable")
        while True:
            ok, image = capture.read()
            if not ok:
                break
            if len(observations) >= frame_budget:
                raise ValueError("local media exceeds the visual frame budget")
            if [int(image.shape[1]), int(image.shape[0])] != selector["resolution"]:
                raise ValueError("decoded resolution does not match the visual runtime selector")
            pts = capture.get(cv2.CAP_PROP_POS_MSEC)
            pts_value = float(pts) if isinstance(pts, (int, float)) and math.isfinite(float(pts)) else None
            target_observations = detect_color_observations_v2(
                image,
                excluded_regions=excluded_regions,
                minimum_circularity=(
                    0.45 if episode_mode else ROUND_DETECTOR_MIN_CIRCULARITY
                ),
                **target_config,
            )
            observations.append({
                "source_pts_ms": pts_value,
                "crosshair": {
                    "x": float(image.shape[1]) / 2.0,
                    "y": float(image.shape[0]) / 2.0,
                    "confidence": 1.0,
                },
                "targets": target_observations["targets"],
                "target_ambiguities": target_observations["target_ambiguities"],
                "scene": "gameplay",
            })
    finally:
        capture.release()
    if not observations:
        raise ValueError("local media decoder returned no observations")
    result = preprocess_visual_signals_v1(
        analysis_ref=analysis_ref,
        canonical_time_window=canonical_time_window,
        frame_observations=observations,
        visual_quality_profile=profile,
        visual_runtime_selector=selector,
        video_time_mapping=video_time_mapping,
        outcome_observations=outcome_observations,
        source_ref=source_ref or f"{analysis_ref}:source:local-visual",
    )
    if episode_mode:
        result["frame_observations"] = copy.deepcopy(observations)
    return result


def _tracker_bbox_from_target(target: Mapping[str, object]) -> tuple[int, int, int, int]:
    radius = _finite("target.visible_radius", target.get("visible_radius"), minimum=0.01)
    half_size = max(4.0, radius * 1.25)
    x = _finite("target.x", target.get("x"))
    y = _finite("target.y", target.get("y"))
    size = max(1, int(round(half_size * 2.0)))
    return (
        int(round(x - size / 2.0)),
        int(round(y - size / 2.0)),
        size,
        size,
    )


def _tracker_target_from_bbox(
    bbox: Sequence[object],
    *,
    visible_radius: float,
    detector_ref: str | None,
    frame_width: int,
    frame_height: int,
) -> dict | None:
    if len(bbox) != 4:
        return None
    x, y, width, height = [
        _finite("tracker bbox", item, minimum=0.0 if index >= 2 else None)
        for index, item in enumerate(bbox)
    ]
    if width <= 0 or height <= 0:
        return None
    center_x = x + width / 2.0
    center_y = y + height / 2.0
    if not (0 <= center_x < frame_width and 0 <= center_y < frame_height):
        return None
    return {
        "detector_ref": detector_ref,
        "x": center_x,
        "y": center_y,
        "visible_radius": visible_radius,
        "confidence": _TEMPORAL_TRACKER_CONFIDENCE,
        "measurement_source": "temporal_tracker_confirmed",
    }


def preprocess_visual_video_temporal_v1(
    *,
    media_path: str,
    analysis_ref: str,
    canonical_time_window: Mapping[str, object],
    visual_quality_profile: Mapping[str, object],
    visual_runtime_selector: Mapping[str, object],
    video_time_mapping: Mapping[str, object],
    detector_config: Mapping[str, object],
    outcome_observations: Sequence[Mapping[str, object]] = (),
    source_ref: str | None = None,
) -> dict:
    """Bridge short detector dropouts only after the next direct observation agrees."""
    import cv2

    if not isinstance(media_path, str) or not media_path:
        raise ValueError("local media source is required")
    profile = _validate_profile(visual_quality_profile)
    if (
        profile["producer_id"] != VISUAL_TEMPORAL_PRODUCER_ID
        or profile["producer_version"] != VISUAL_TEMPORAL_PRODUCER_VERSION
    ):
        raise ValueError("temporal visual producer profile is not current")
    canonical_detector_config = _canonical_visual_detector_config(detector_config)
    if (
        profile["calibration_context"]["detector_config_ref"]
        != visual_detector_config_ref_v1(canonical_detector_config)
    ):
        raise ValueError("visual detector config does not match quality profile")
    excluded_regions = canonical_detector_config.get("excluded_regions", [])
    target_config = canonical_detector_config["target"]
    selector = _validate_runtime_selector(
        visual_runtime_selector, "visual_runtime_selector"
    )
    window, _, _ = _window_bounds(canonical_time_window)
    _validate_video_time_mapping(
        video_time_mapping,
        canonical_time_window=window,
    )
    frame_budget = min(
        250_000,
        max(1, int(math.ceil(window["duration_ms"] / 1_000 * 240)) + 240),
    )

    capture = cv2.VideoCapture(media_path)
    observations: list[dict] = []
    pending: list[tuple[dict, dict]] = []
    tracker = None
    last_radius: float | None = None
    gap_started_at: float | None = None
    identity_unresolved = False
    tracker_fill_used = False
    tracker_gap_rejected = False
    tracker_unavailable = False
    target_set_ambiguous = False
    last_direct: dict | None = None
    direct_count = 0
    recovered_count = 0
    rejected_count = 0
    detector_ref = "temporal-single-target"

    def observation(image, pts_value: float | None, target_observations: Mapping[str, object]) -> dict:
        return {
            "source_pts_ms": pts_value,
            "crosshair": {
                "x": float(image.shape[1]) / 2.0,
                "y": float(image.shape[0]) / 2.0,
                "confidence": 1.0,
            },
            "targets": copy.deepcopy(target_observations["targets"]),
            "target_ambiguities": copy.deepcopy(
                target_observations["target_ambiguities"]
            ),
            "scene": "gameplay",
        }

    def flush_pending(*, accepted: bool) -> None:
        nonlocal identity_unresolved, pending, recovered_count, rejected_count
        nonlocal tracker_fill_used, tracker_gap_rejected
        if not pending:
            return
        if accepted:
            for base, tracked_target in pending:
                base["targets"] = [tracked_target]
                base["target_ambiguities"] = []
                observations.append(base)
            tracker_fill_used = True
            recovered_count += len(pending)
        else:
            observations.extend(base for base, _tracked_target in pending)
            identity_unresolved = True
            tracker_gap_rejected = True
            rejected_count += len(pending)
        pending = []

    def initialize_tracker(image, target: Mapping[str, object]):
        nonlocal tracker_unavailable
        try:
            try:
                candidate = cv2.TrackerCSRT_create()
            except AttributeError:
                candidate = cv2.legacy.TrackerCSRT_create()
            left, top, width, height = _tracker_bbox_from_target(target)
            left = max(0, min(int(image.shape[1]) - 1, left))
            top = max(0, min(int(image.shape[0]) - 1, top))
            width = max(1, min(width, int(image.shape[1]) - left))
            height = max(1, min(height, int(image.shape[0]) - top))
            initialized = candidate.init(image, (left, top, width, height))
            if initialized is False:
                tracker_unavailable = True
                return None
            return candidate
        except (AttributeError, RuntimeError, cv2.error):
            tracker_unavailable = True
            return None

    def pending_agrees_with_direct(
        direct: Mapping[str, object],
        tracker_target: Mapping[str, object] | None,
        current_pts: float | None,
    ) -> bool:
        if (
            tracker_target is None
            or last_direct is None
            or current_pts is None
            or last_direct["source_pts_ms"] is None
            or current_pts <= last_direct["source_pts_ms"]
            or current_pts - last_direct["source_pts_ms"]
            > _TEMPORAL_TRACKER_MAX_GAP_MS
        ):
            return False
        tolerance = max(
            6.0,
            _finite(
                "target.visible_radius", direct["visible_radius"], minimum=0.01
            ) * 1.5,
        )
        if math.hypot(
            tracker_target["x"] - direct["x"],
            tracker_target["y"] - direct["y"],
        ) > tolerance:
            return False
        duration = current_pts - last_direct["source_pts_ms"]
        for base, pending_target in pending:
            pending_pts = base["source_pts_ms"]
            if pending_pts is None or not last_direct["source_pts_ms"] < pending_pts < current_pts:
                return False
            alpha = (pending_pts - last_direct["source_pts_ms"]) / duration
            expected_x = last_direct["x"] + (direct["x"] - last_direct["x"]) * alpha
            expected_y = last_direct["y"] + (direct["y"] - last_direct["y"]) * alpha
            if math.hypot(
                pending_target["x"] - expected_x,
                pending_target["y"] - expected_y,
            ) > tolerance:
                return False
        return True

    try:
        if not capture.isOpened():
            raise ValueError("local media decoder is unavailable")
        decoded_count = 0
        while True:
            ok, image = capture.read()
            if not ok:
                break
            if decoded_count >= frame_budget:
                raise ValueError("local media exceeds the visual frame budget")
            decoded_count += 1
            if [int(image.shape[1]), int(image.shape[0])] != selector["resolution"]:
                raise ValueError("decoded resolution does not match the visual runtime selector")
            pts = capture.get(cv2.CAP_PROP_POS_MSEC)
            pts_value = (
                float(pts)
                if isinstance(pts, (int, float)) and math.isfinite(float(pts))
                else None
            )
            detected = detect_color_observations_v2(
                image,
                excluded_regions=excluded_regions,
                **target_config,
            )
            base = observation(image, pts_value, detected)
            direct_targets = detected["targets"]
            direct_unique = (
                len(direct_targets) == 1 and not detected["target_ambiguities"]
            )

            tracker_target = None
            if tracker is not None:
                success, bbox = tracker.update(image)
                if success and last_radius is not None:
                    tracker_target = _tracker_target_from_bbox(
                        bbox,
                        visible_radius=last_radius,
                        detector_ref=None if identity_unresolved else detector_ref,
                        frame_width=int(image.shape[1]),
                        frame_height=int(image.shape[0]),
                    )
                if tracker_target is None:
                    flush_pending(accepted=False)
                    tracker = None

            if direct_unique:
                direct = dict(direct_targets[0])
                direct["measurement_source"] = "direct_detector"
                direct["detector_ref"] = (
                    None if identity_unresolved else detector_ref
                )
                direct_count += 1
                if pending:
                    agrees = pending_agrees_with_direct(
                        direct, tracker_target, pts_value
                    )
                    flush_pending(accepted=agrees)
                    if not agrees:
                        direct["detector_ref"] = None
                        tracker = None
                observations.append({**base, "targets": [direct]})
                last_radius = _finite(
                    "target.visible_radius", direct["visible_radius"], minimum=0.01
                )
                last_direct = {
                    "source_pts_ms": pts_value,
                    "x": direct["x"],
                    "y": direct["y"],
                }
                gap_started_at = None
                if tracker is None:
                    tracker = initialize_tracker(image, direct)
                continue

            bridgeable_dropout = (
                not direct_targets and not detected["target_ambiguities"]
            )
            if not bridgeable_dropout:
                flush_pending(accepted=False)
                observations.append(base)
                tracker = None
                gap_started_at = None
                identity_unresolved = True
                target_set_ambiguous = True
                continue

            if tracker_target is not None:
                if gap_started_at is None:
                    gap_started_at = pts_value
                elapsed = (
                    pts_value - gap_started_at
                    if pts_value is not None and gap_started_at is not None
                    else 0.0
                )
                if (
                    len(pending) < _TEMPORAL_TRACKER_MAX_GAP_FRAMES
                    and elapsed <= _TEMPORAL_TRACKER_MAX_GAP_MS
                ):
                    pending.append((base, tracker_target))
                    continue

            flush_pending(accepted=False)
            observations.append(base)
            tracker = None
            gap_started_at = None
    finally:
        capture.release()
    flush_pending(accepted=False)
    if not observations:
        raise ValueError("local media decoder returned no observations")
    result = preprocess_visual_signals_v1(
        analysis_ref=analysis_ref,
        canonical_time_window=canonical_time_window,
        frame_observations=observations,
        visual_quality_profile=profile,
        visual_runtime_selector=selector,
        video_time_mapping=video_time_mapping,
        outcome_observations=outcome_observations,
        source_ref=source_ref or f"{analysis_ref}:source:local-visual",
    )
    result["temporal_measurement_counts"] = {
        "direct_detector": direct_count,
        "temporal_tracker_confirmed": recovered_count,
        "rejected": rejected_count,
    }
    result["safe_summary"]["temporal_measurement_counts"] = copy.deepcopy(
        result["temporal_measurement_counts"]
    )
    for limitation, present in (
        ("temporal_tracker_fill_used", tracker_fill_used),
        ("temporal_tracker_gap_rejected", tracker_gap_rejected),
        ("temporal_tracker_unavailable", tracker_unavailable),
        ("temporal_target_set_ambiguous", target_set_ambiguous),
    ):
        if present and limitation not in result["limitations"]:
            result["limitations"].append(limitation)
            result["safe_summary"]["limitations"].append(limitation)
    if tracker_gap_rejected or tracker_unavailable or target_set_ambiguous:
        result["completeness"] = "partial"
        result["safe_summary"]["completeness"] = "partial"
    temporal_required_fields = set(
        profile["required_quality_fields_by_metric_family"].get("tracking", [])
    )
    required_tracking_fields = {
        "center_error_median_px",
        "center_error_p95_px",
        "radius_or_hitbox_error_px",
        "false_positive_rate",
        "minimum_coverage",
    }
    if not required_tracking_fields <= temporal_required_fields:
        raise ValueError("temporal tracking profile quality fields are incomplete")
    runtime_coverage_threshold = profile["acceptance_thresholds"]["minimum_coverage"]
    runtime_failure = (
        result["safe_summary"]["target_coverage"] < runtime_coverage_threshold
        or target_set_ambiguous
        or tracker_gap_rejected
        or tracker_unavailable
    )
    if runtime_failure and "tracking" in result["quality"]["enabled_metric_families"]:
        limitation = (
            "visual_quality_below_threshold:tracking:temporal_continuity"
            if target_set_ambiguous or tracker_gap_rejected or tracker_unavailable
            else "visual_quality_below_threshold:tracking:runtime_coverage"
        )
        result["quality"]["enabled_metric_families"].remove("tracking")
        result["quality"]["status"] = "rejected"
        result["quality"]["limitations"].append(limitation)
        result["safe_summary"]["enabled_metric_families"] = []
        result["safe_summary"]["quality_status"] = "rejected"
        result["limitations"].append(limitation)
        result["safe_summary"]["limitations"].append(limitation)
    return result


def preprocess_visual_video_single_target_csrt_v1(
    *,
    media_path: str,
    analysis_ref: str,
    canonical_time_window: Mapping[str, object],
    visual_quality_profile: Mapping[str, object],
    visual_runtime_selector: Mapping[str, object],
    video_time_mapping: Mapping[str, object],
    detector_config: Mapping[str, object],
    outcome_observations: Sequence[Mapping[str, object]] = (),
    source_ref: str | None = None,
) -> dict:
    """Adapt the reviewed legacy black-ball detector and CSRT path.

    This producer is intentionally exact-scenario only.  The legacy detector
    picks the nearest black blob once, then CSRT carries that single identity;
    it never falls back to KCF or silently reselects a target after a loss.
    """
    import cv2
    import numpy as np

    if not isinstance(media_path, str) or not media_path:
        raise ValueError("local media source is required")
    profile = _validate_profile(visual_quality_profile)
    if (
        profile["producer_id"] != VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID
        or profile["producer_version"] != VISUAL_SINGLE_TARGET_CSRT_PRODUCER_VERSION
    ):
        raise ValueError("single-target CSRT profile is not current")
    canonical_detector_config = _canonical_visual_detector_config(detector_config)
    expected_target = {
        "hsv_lower": [0, 0, 0],
        "hsv_upper": [179, 255, 80],
        "min_area": 50.0,
        "max_area_ratio": 0.05,
        "shape": "round",
    }
    if (
        canonical_detector_config["schema_version"] != "visual_target_detector.v1"
        or canonical_detector_config["target"] != expected_target
        or profile["calibration_context"]["detector_config_ref"]
        != visual_detector_config_ref_v1(canonical_detector_config)
    ):
        raise ValueError("legacy single-target detector config is not current")
    selector = _validate_runtime_selector(
        visual_runtime_selector, "visual_runtime_selector"
    )
    window, _, _ = _window_bounds(canonical_time_window)
    _validate_video_time_mapping(video_time_mapping, canonical_time_window=window)
    frame_budget = min(
        250_000,
        max(1, int(math.ceil(window["duration_ms"] / 1_000 * 240)) + 240),
    )

    from .vision import detect_ball_by_color

    capture = cv2.VideoCapture(media_path)
    observations: list[dict] = []
    tracker = None
    tracker_unavailable = False
    tracker_lost = False
    direct_count = 0
    tracked_count = 0

    def base_observation(image, pts_value: float | None) -> dict:
        return {
            "source_pts_ms": pts_value,
            "crosshair": {
                "x": float(image.shape[1]) / 2.0,
                "y": float(image.shape[0]) / 2.0,
                "confidence": 1.0,
            },
            "targets": [],
            "target_ambiguities": [],
            "scene": "gameplay",
        }

    def create_csrt(image, bbox):
        nonlocal tracker_unavailable
        try:
            try:
                candidate = cv2.TrackerCSRT_create()
            except AttributeError:
                candidate = cv2.legacy.TrackerCSRT_create()
            initialized = candidate.init(image, bbox)
            if initialized is False:
                tracker_unavailable = True
                return None
            return candidate
        except (AttributeError, RuntimeError, cv2.error):
            tracker_unavailable = True
            return None

    try:
        if not capture.isOpened():
            raise ValueError("local media decoder is unavailable")
        decoded_count = 0
        while True:
            ok, image = capture.read()
            if not ok:
                break
            if decoded_count >= frame_budget:
                raise ValueError("local media exceeds the visual frame budget")
            decoded_count += 1
            if [int(image.shape[1]), int(image.shape[0])] != selector["resolution"]:
                raise ValueError("decoded resolution does not match the visual runtime selector")
            pts = capture.get(cv2.CAP_PROP_POS_MSEC)
            pts_value = (
                float(pts)
                if isinstance(pts, (int, float)) and math.isfinite(float(pts))
                else None
            )
            observation = base_observation(image, pts_value)

            if tracker is None and not tracker_unavailable and not tracker_lost:
                position, width, height = detect_ball_by_color(
                    image,
                    np.asarray(expected_target["hsv_lower"], dtype=np.uint8),
                    np.asarray(expected_target["hsv_upper"], dtype=np.uint8),
                )
                if position is not None and width and height:
                    center_x, center_y = position
                    visible_radius = (float(width) + float(height)) / 4.0
                    target = {
                        "detector_ref": "legacy-single-target-hsv",
                        "measurement_source": "direct_detector",
                        "x": float(center_x),
                        "y": float(center_y),
                        "visible_radius": visible_radius,
                        "confidence": 1.0,
                    }
                    observation["targets"] = [target]
                    direct_count += 1
                    left = max(0, int(round(center_x - width / 2.0)))
                    top = max(0, int(round(center_y - height / 2.0)))
                    bounded_width = max(1, min(int(width), int(image.shape[1]) - left))
                    bounded_height = max(1, min(int(height), int(image.shape[0]) - top))
                    tracker = create_csrt(
                        image, (left, top, bounded_width, bounded_height)
                    )
            elif tracker is not None:
                success, bbox = tracker.update(image)
                if success:
                    x, y, width, height = [float(value) for value in bbox]
                    center_x = x + width / 2.0
                    center_y = y + height / 2.0
                    if (
                        width > 0
                        and height > 0
                        and 0 <= center_x < image.shape[1]
                        and 0 <= center_y < image.shape[0]
                    ):
                        observation["targets"] = [{
                            "detector_ref": "legacy-single-target-hsv",
                            "measurement_source": "temporal_tracker_confirmed",
                            "x": center_x,
                            "y": center_y,
                            "visible_radius": (width + height) / 4.0,
                            "confidence": _TEMPORAL_TRACKER_CONFIDENCE,
                        }]
                        tracked_count += 1
                    else:
                        tracker = None
                        tracker_lost = True
                else:
                    tracker = None
                    tracker_lost = True
            observations.append(observation)
    finally:
        capture.release()
    if not observations:
        raise ValueError("local media decoder returned no observations")

    result = preprocess_visual_signals_v1(
        analysis_ref=analysis_ref,
        canonical_time_window=canonical_time_window,
        frame_observations=observations,
        visual_quality_profile=profile,
        visual_runtime_selector=selector,
        video_time_mapping=video_time_mapping,
        outcome_observations=outcome_observations,
        source_ref=source_ref or f"{analysis_ref}:source:local-visual",
    )
    result["single_target_csrt_measurement_counts"] = {
        "direct_detector": direct_count,
        "temporal_tracker_confirmed": tracked_count,
    }
    result["safe_summary"]["single_target_csrt_measurement_counts"] = copy.deepcopy(
        result["single_target_csrt_measurement_counts"]
    )
    limitations: list[str] = []
    if tracker_unavailable:
        limitations.append("single_target_csrt_unavailable")
    if tracker_lost:
        limitations.append("single_target_csrt_tracking_lost")
    for limitation in limitations:
        if limitation not in result["limitations"]:
            result["limitations"].append(limitation)
            result["safe_summary"]["limitations"].append(limitation)
    runtime_coverage_threshold = profile["acceptance_thresholds"]["minimum_coverage"]
    runtime_failure = (
        result["safe_summary"]["target_coverage"] < runtime_coverage_threshold
        or bool(limitations)
    )
    if runtime_failure and "tracking" in result["quality"]["enabled_metric_families"]:
        result["quality"]["enabled_metric_families"].remove("tracking")
        result["quality"]["status"] = "rejected"
        result["quality"]["limitations"].append(
            "visual_quality_below_threshold:tracking:single_target_csrt"
        )
        result["safe_summary"]["enabled_metric_families"] = []
        result["safe_summary"]["quality_status"] = "rejected"
    return result


def _window_bounds(value: object) -> tuple[dict, int, int]:
    required_fields = {
        "schema_version", "start_ms", "end_ms", "duration_ms", "window_semantics",
        "timebase_version", "start_source", "end_source", "warnings",
    }
    if not isinstance(value, Mapping) or not required_fields <= set(value):
        raise ValueError("canonical_time_window fields are invalid")
    start = value["start_ms"]
    end = value["end_ms"]
    duration = value["duration_ms"]
    if (
        value["schema_version"] != "canonical_time_window.v1"
        or isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or isinstance(duration, bool)
        or not isinstance(duration, int)
        or start < 0
        or end <= start
        or duration != end - start
        or value["window_semantics"] != "half_open"
    ):
        raise ValueError("canonical_time_window is invalid")
    return copy.deepcopy(dict(value)), start, end


def _validate_video_time_mapping(
    value: object,
    *,
    canonical_time_window: Mapping[str, object],
) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError("video_time_mapping fields are invalid")
    schema_version = value.get("schema_version")
    fields = {
        "schema_version", "source_pts_origin_ms", "canonical_origin_ms",
        "mapping_method", "timebase_version",
    }
    if schema_version == "visual_video_time_mapping.v2":
        # v2 carries the capture receipt decode preroll so canonical times
        # line up with the true visible window; v1 keeps its frozen shape.
        fields = fields | {"decode_preroll_ms"}
    if set(value) != fields:
        raise ValueError("video_time_mapping fields are invalid")
    if schema_version not in {
        "visual_video_time_mapping.v1", "visual_video_time_mapping.v2",
    }:
        raise ValueError("video_time_mapping version is unsupported")
    source_origin = _finite(
        "video_time_mapping.source_pts_origin_ms",
        value["source_pts_origin_ms"],
        minimum=0.0,
    )
    canonical_origin = value["canonical_origin_ms"]
    if isinstance(canonical_origin, bool) or not isinstance(canonical_origin, int):
        raise ValueError("video_time_mapping canonical origin is invalid")
    if not (
        canonical_time_window["start_ms"]
        <= canonical_origin
        < canonical_time_window["end_ms"]
    ):
        raise ValueError("video_time_mapping canonical origin is outside the window")
    method = _bounded_text("video_time_mapping.mapping_method", value["mapping_method"])
    if method not in {"run_owned_exact_canonical_clip", "validated_fixture_offset"}:
        raise ValueError("video_time_mapping method is unsupported")
    timebase_version = _bounded_text(
        "video_time_mapping.timebase_version", value["timebase_version"]
    )
    if timebase_version != canonical_time_window["timebase_version"]:
        raise ValueError("video_time_mapping timebase does not match the canonical window")
    normalized = {
        "schema_version": schema_version,
        "source_pts_origin_ms": source_origin,
        "canonical_origin_ms": canonical_origin,
        "mapping_method": method,
        "timebase_version": timebase_version,
    }
    if schema_version == "visual_video_time_mapping.v2":
        normalized["decode_preroll_ms"] = _finite(
            "video_time_mapping.decode_preroll_ms",
            value["decode_preroll_ms"],
            minimum=0.0,
        )
    return normalized


def _source_pts_to_canonical_time(
    time_mapping: Mapping[str, object],
    source_pts_ms: float,
) -> int:
    return int(time_mapping["canonical_origin_ms"] + round(
        source_pts_ms
        - time_mapping["source_pts_origin_ms"]
        + time_mapping.get("decode_preroll_ms", 0.0)
    ))


def _event(
    analysis_ref: str,
    index: int,
    kind: str,
    time_ms: int,
    source_ref: str,
    *,
    actor_refs: Sequence[str] = (),
    confidence: float = 1.0,
    attributes: Mapping[str, object] | None = None,
    limitations: Sequence[str] = (),
) -> dict:
    return {
        "event_id": f"{analysis_ref}:event:visual:{index}",
        "event_kind": kind,
        "start_ms": time_ms,
        "end_ms": time_ms,
        "actor_refs": list(actor_refs),
        "source_refs": [source_ref],
        "confidence": _ratio("event.confidence", confidence),
        "attributes": dict(attributes or {}),
        "limitations": list(limitations),
    }


def preprocess_visual_target_episodes_v1(
    *,
    analysis_ref: str,
    frame_observations: Sequence[Mapping[str, object]],
) -> dict:
    """Build bounded local target-observation episodes without object identity.

    Each episode is restricted to an unambiguous run of adjacent detector
    observations.  A merge, crossing, non-unique match, missing frame, or
    non-gameplay frame ends affected episodes.  Later observations always get
    fresh episode refs, so this artifact cannot claim re-entry continuity.
    """
    analysis_ref = _stable_ref("analysis_ref", analysis_ref)
    if (
        not isinstance(frame_observations, Sequence)
        or isinstance(frame_observations, (str, bytes))
        or not frame_observations
        or len(frame_observations) > 250_000
    ):
        raise ValueError("frame_observations must be a bounded non-empty sequence")

    episodes: dict[int, dict] = {}
    active_ids: set[int] = set()
    boundaries: list[dict] = []
    limitations: list[str] = []
    previous_time: float | None = None

    def add_boundary(source_pts_ms: float, reason: str, affected: set[int]) -> None:
        if affected:
            boundaries.append({
                "source_pts_ms": source_pts_ms,
                "reason": reason,
            })

    def end_active(source_pts_ms: float, reason: str, affected: set[int] | None = None) -> None:
        nonlocal active_ids
        ending = set(active_ids if affected is None else affected).intersection(active_ids)
        add_boundary(source_pts_ms, reason, ending)
        active_ids.difference_update(ending)

    def normalized_targets(observation: Mapping[str, object]) -> list[dict]:
        raw_targets = observation.get("targets") or []
        if not isinstance(raw_targets, Sequence) or isinstance(raw_targets, (str, bytes)):
            raise ValueError("targets must be a sequence")
        targets: list[dict] = []
        for target in raw_targets:
            if not isinstance(target, Mapping):
                raise ValueError("target observation must be a mapping")
            targets.append({
                "x": _finite("target.x", target.get("x")),
                "y": _finite("target.y", target.get("y")),
                "visible_radius": _finite(
                    "target.visible_radius", target.get("visible_radius"), minimum=0.01,
                ),
                "confidence": _ratio("target.confidence", target.get("confidence")),
            })
        return sorted(targets, key=lambda target: (
            target["x"], target["y"], target["visible_radius"],
        ))

    def start_episode(target: Mapping[str, object], source_pts_ms: float) -> int:
        episode_id = len(episodes) + 1
        episodes[episode_id] = {
            "episode_ref": f"{analysis_ref}:target-episode:{episode_id}",
            "status": "available",
            "samples": [{"source_pts_ms": source_pts_ms, **dict(target)}],
        }
        active_ids.add(episode_id)
        return episode_id

    for frame_index, observation in enumerate(frame_observations):
        if not isinstance(observation, Mapping):
            raise ValueError(f"observation[{frame_index}] must be a mapping")
        source_pts_ms = _finite(
            f"observation[{frame_index}].source_pts_ms",
            observation.get("source_pts_ms"),
            minimum=0.0,
        )
        if previous_time is not None and source_pts_ms <= previous_time:
            raise ValueError("source_pts_ms must be strictly increasing")
        if (
            previous_time is not None
            and source_pts_ms - previous_time > _TARGET_EPISODE_MAX_OBSERVATION_GAP_MS
        ):
            end_active(source_pts_ms, "target_observation_gap")
            if "target_observation_gap" not in limitations:
                limitations.append("target_observation_gap")
        previous_time = source_pts_ms
        if observation.get("scene", "gameplay") != "gameplay":
            end_active(source_pts_ms, "non_gameplay_scene")
            if "non_gameplay_scene" not in limitations:
                limitations.append("non_gameplay_scene")
            continue
        raw_ambiguities = observation.get("target_ambiguities") or []
        if not isinstance(raw_ambiguities, Sequence) or isinstance(raw_ambiguities, (str, bytes)):
            raise ValueError("target_ambiguities must be a sequence")
        ambiguities: list[dict] = []
        for ambiguity in raw_ambiguities:
            if (
                not isinstance(ambiguity, Mapping)
                or ambiguity.get("ambiguity_kind") != "merged_target_component"
            ):
                raise ValueError("target ambiguities must be merged target components")
            ambiguities.append({
                "x": _finite("target ambiguity.x", ambiguity.get("x")),
                "y": _finite("target ambiguity.y", ambiguity.get("y")),
                "visible_radius": _finite(
                    "target ambiguity.visible_radius",
                    ambiguity.get("visible_radius"),
                    minimum=0.01,
                ),
            })
        if raw_ambiguities:
            affected = {
                episode_id
                for episode_id in active_ids
                if any(
                    math.hypot(
                        episodes[episode_id]["samples"][-1]["x"] - ambiguity["x"],
                        episodes[episode_id]["samples"][-1]["y"] - ambiguity["y"],
                    ) <= (
                        episodes[episode_id]["samples"][-1]["visible_radius"]
                        + ambiguity["visible_radius"]
                    )
                    for ambiguity in ambiguities
                )
            }
            end_active(source_pts_ms, "target_merge_ambiguous", affected)
            if "target_merge_ambiguous" not in limitations:
                limitations.append("target_merge_ambiguous")
            continue
        targets = normalized_targets(observation)
        if not targets:
            end_active(source_pts_ms, "target_disappearance")
            continue
        if not active_ids:
            for target in targets:
                start_episode(target, source_pts_ms)
            continue

        candidate_ids_by_target: list[list[int]] = []
        candidate_targets_by_episode: dict[int, list[int]] = {
            episode_id: [] for episode_id in active_ids
        }
        for target_index, target in enumerate(targets):
            candidates = [
                episode_id
                for episode_id in sorted(active_ids)
                if math.hypot(
                    target["x"] - episodes[episode_id]["samples"][-1]["x"],
                    target["y"] - episodes[episode_id]["samples"][-1]["y"],
                ) <= _MULTI_TARGET_POSITION_RESIDUAL_PX
            ]
            candidate_ids_by_target.append(candidates)
            for episode_id in candidates:
                candidate_targets_by_episode[episode_id].append(target_index)
        pairs = [
            (target_index, candidates[0])
            for target_index, candidates in enumerate(candidate_ids_by_target)
            if len(candidates) == 1
            and len(candidate_targets_by_episode[candidates[0]]) == 1
        ]
        crossing_ids = {
            episode_id
            for pair_index, (left_target, left_episode) in enumerate(pairs)
            for right_target, right_episode in pairs[pair_index + 1:]
            if (
                episodes[left_episode]["samples"][-1]["x"]
                - episodes[right_episode]["samples"][-1]["x"]
            ) * (
                targets[left_target]["x"] - targets[right_target]["x"]
            ) <= 0.0
            for episode_id in (left_episode, right_episode)
        }
        if crossing_ids:
            end_active(source_pts_ms, "target_crossing_ambiguous", crossing_ids)
            if "target_crossing_ambiguous" not in limitations:
                limitations.append("target_crossing_ambiguous")
        crossing_target_indices = {
            target_index
            for target_index, episode_id in pairs
            if episode_id in crossing_ids
        }
        matched_targets = {
            target_index
            for target_index, episode_id in pairs
            if episode_id in active_ids
        }
        matched_ids = {
            episode_id
            for _target_index, episode_id in pairs
            if episode_id in active_ids
        }
        ambiguous_ids = {
            episode_id
            for episode_id, candidates in candidate_targets_by_episode.items()
            if len(candidates) > 1
        }
        ambiguous_ids.update(
            episode_id
            for candidates in candidate_ids_by_target
            if len(candidates) > 1
            for episode_id in candidates
        )
        if ambiguous_ids:
            end_active(source_pts_ms, "target_local_match_ambiguous", ambiguous_ids)
            if "target_local_match_ambiguous" not in limitations:
                limitations.append("target_local_match_ambiguous")
        for target_index, episode_id in pairs:
            if episode_id not in active_ids:
                continue
            episodes[episode_id]["samples"].append({
                "source_pts_ms": source_pts_ms,
                **targets[target_index],
            })
        end_active(
            source_pts_ms,
            "target_disappearance",
            active_ids.difference(matched_ids).difference(ambiguous_ids),
        )
        for target_index, target in enumerate(targets):
            if (
                target_index not in matched_targets
                and target_index not in crossing_target_indices
            ):
                start_episode(target, source_pts_ms)

    return {
        "schema_version": "visual_target_episode_artifact.v1",
        "producer": {
            "id": VISUAL_TARGET_EPISODE_PRODUCER_ID,
            "version": VISUAL_TARGET_EPISODE_PRODUCER_VERSION,
        },
        "status": "partial" if limitations else "available",
        "limitations": limitations,
        "episodes": [episodes[episode_id] for episode_id in sorted(episodes)],
        "boundaries": boundaries,
    }


def project_visual_target_episodes_v1(
    visual_result: Mapping[str, object],
    episode_result: Mapping[str, object],
) -> dict:
    """Project safe local episodes into the existing numeric visual artifact."""
    if not isinstance(visual_result, Mapping):
        raise ValueError("visual result must be a mapping")
    projected = copy.deepcopy(dict(visual_result))
    analysis_ref = _stable_ref("analysis_ref", projected.get("analysis_ref"))
    window, window_start, window_end = _window_bounds(projected.get("canonical_time_window"))
    time_mapping = _validate_video_time_mapping(
        projected.get("video_time_mapping"), canonical_time_window=window,
    )
    if (
        not isinstance(episode_result, Mapping)
        or episode_result.get("schema_version") != "visual_target_episode_artifact.v1"
        or episode_result.get("producer") != {
            "id": VISUAL_TARGET_EPISODE_PRODUCER_ID,
            "version": VISUAL_TARGET_EPISODE_PRODUCER_VERSION,
        }
        or episode_result.get("status") not in {"available", "partial"}
    ):
        raise ValueError("target episode result is invalid")
    episodes = episode_result.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("target episode evidence is unavailable")
    signal_bundle = projected.get("signal_bundle")
    sample_sets = projected.get("sample_sets")
    local_samples = projected.get("local_samples")
    if (
        not isinstance(signal_bundle, Mapping)
        or not isinstance(sample_sets, list)
        or not isinstance(local_samples, Mapping)
    ):
        raise ValueError("visual numerical evidence is unavailable")
    preserved_channels = [
        copy.deepcopy(channel)
        for channel in signal_bundle.get("channels") or []
        if isinstance(channel, Mapping)
        and not str(channel.get("channel_key") or "").startswith("target.")
    ]
    source_refs = sorted({
        ref
        for channel in preserved_channels
        for ref in channel.get("source_refs") or []
        if isinstance(ref, str)
    })
    if not source_refs:
        raise ValueError("visual source reference is unavailable")
    preserved_sample_sets = [
        copy.deepcopy(sample_set)
        for sample_set in sample_sets
        if isinstance(sample_set, Mapping)
        and not str(sample_set.get("channel_key") or "").startswith("target.")
    ]
    new_local_samples = {
        key: copy.deepcopy(value)
        for key, value in local_samples.items()
        if not str(key).startswith("target.")
    }
    target_channels: list[dict] = []
    target_sample_sets: list[dict] = []
    track_summaries: list[dict] = []
    episode_prefix = f"{analysis_ref}:target-episode:"
    observation_count = len(projected.get("frame_observations") or [])
    for raw_episode in episodes:
        if (
            not isinstance(raw_episode, Mapping)
            or raw_episode.get("status") != "available"
        ):
            raise ValueError("target episode is invalid")
        episode_ref = _stable_ref("episode_ref", raw_episode.get("episode_ref"))
        if not episode_ref.startswith(episode_prefix):
            raise ValueError("target episode is bound to another analysis")
        episode_id = episode_ref[len(episode_prefix):]
        if not re.fullmatch(r"[1-9][0-9]*", episode_id):
            raise ValueError("target episode suffix is invalid")
        canonical_samples = []
        for raw_sample in raw_episode.get("samples") or []:
            if not isinstance(raw_sample, Mapping):
                raise ValueError("target episode sample is invalid")
            canonical_time = _source_pts_to_canonical_time(
                time_mapping,
                _finite("episode sample source_pts_ms", raw_sample.get("source_pts_ms"), minimum=0.0),
            )
            if not window_start <= canonical_time < window_end:
                continue
            canonical_samples.append({
                "canonical_time_ms": canonical_time,
                "x": _finite("episode sample x", raw_sample.get("x")),
                "y": _finite("episode sample y", raw_sample.get("y")),
                "visible_radius": _finite(
                    "episode sample radius", raw_sample.get("visible_radius"), minimum=0.01,
                ),
                "confidence": _ratio("episode sample confidence", raw_sample.get("confidence")),
            })
        if not canonical_samples:
            continue
        canonical_samples.sort(key=lambda item: item["canonical_time_ms"])
        if len({item["canonical_time_ms"] for item in canonical_samples}) != len(canonical_samples):
            raise ValueError("target episode sample times are duplicated")
        prefix = f"target.{episode_id}"
        new_local_samples[f"{prefix}.position"] = copy.deepcopy(canonical_samples)
        for part, field in (
            ("position_x", "x"),
            ("position_y", "y"),
            ("visible_radius", "visible_radius"),
        ):
            channel_key = f"{prefix}.{part}"
            sample_ref = f"{analysis_ref}:samples:{channel_key.replace('.', '-') }"
            points = [[sample["canonical_time_ms"], sample[field]] for sample in canonical_samples]
            target_sample_sets.append({
                "sample_set_id": sample_ref,
                "channel_key": channel_key,
                "unit": "px",
                "points": points,
            })
            target_channels.append({
                "channel_key": channel_key,
                "source_refs": source_refs,
                "coordinate_space": "capture_coordinates",
                "unit": "px",
                "sample_rate_semantics": "source_pts_irregular",
                "samples_ref": sample_ref,
                "coverage": min(1.0, len(points) / observation_count) if observation_count else 0.0,
                "confidence_summary": sum(sample["confidence"] for sample in canonical_samples) / len(canonical_samples),
                "transform_version": VISUAL_TARGET_EPISODE_PRODUCER_VERSION,
                "limitations": [],
            })
        track_summaries.append({
            "track_ref": f"{analysis_ref}:target-track:{episode_id}",
            "observation_source": "event_local_target_episode",
            "visible_radius_px": float(median(sample["visible_radius"] for sample in canonical_samples)),
            "sample_count": len(canonical_samples),
            "coverage": min(1.0, len(canonical_samples) / observation_count) if observation_count else 0.0,
            "limitations": [],
        })
    if not track_summaries:
        raise ValueError("target episode evidence is unavailable")
    upstream_quality = projected.get("quality")
    if not isinstance(upstream_quality, Mapping):
        raise ValueError("upstream visual quality is unavailable")
    upstream_status = upstream_quality.get("status")
    upstream_families = upstream_quality.get("enabled_metric_families")
    upstream_limitations = upstream_quality.get("limitations")
    if (
        upstream_status not in {"accepted", "limited", "rejected"}
        or not isinstance(upstream_families, list)
        or not isinstance(upstream_limitations, list)
    ):
        raise ValueError("upstream visual quality is invalid")
    episode_limitations = list(episode_result.get("limitations") or [])
    quality_limitations = list(dict.fromkeys([
        *upstream_limitations,
        *episode_limitations,
    ]))
    limitations = list(dict.fromkeys([
        *(projected.get("limitations") or []),
        *episode_limitations,
    ]))
    status = upstream_status
    if status == "accepted" and episode_limitations:
        status = "limited"
    quality = {
        "status": status,
        "enabled_metric_families": (
            ["target_switching"]
            if status != "rejected" and "switching" in upstream_families
            else []
        ),
        "limitations": quality_limitations,
    }
    projected["quality"] = quality
    projected["track_summaries"] = track_summaries
    projected["signal_bundle"] = {
        **copy.deepcopy(dict(signal_bundle)),
        "channels": [*preserved_channels, *target_channels],
    }
    projected["event_bundle"] = {
        "schema_version": "event_bundle.v1",
        "analysis_ref": analysis_ref,
        "events": [],
        "outcome_associations": [],
    }
    projected["sample_sets"] = [*preserved_sample_sets, *target_sample_sets]
    projected["local_samples"] = new_local_samples
    projected["limitations"] = limitations
    projected.pop("frame_observations", None)
    safe_summary = copy.deepcopy(dict(projected.get("safe_summary") or {}))
    safe_summary.update({
        "producer_version": VISUAL_TARGET_EPISODE_PRODUCER_VERSION,
        "quality_status": quality["status"],
        "enabled_metric_families": list(quality["enabled_metric_families"]),
        "track_count": len(track_summaries),
        "limitations": limitations,
        "event_counts": {},
    })
    projected["safe_summary"] = safe_summary
    from .analysis_evidence import validate_event_bundle_v1, validate_signal_bundle_v1

    validate_signal_bundle_v1(projected["signal_bundle"])
    validate_event_bundle_v1(projected["event_bundle"])
    return projected


def _preprocess_visual_signals_with_global_identity_v1(
    *,
    analysis_ref: str,
    canonical_time_window: Mapping[str, object],
    frame_observations: Sequence[Mapping[str, object]],
    visual_quality_profile: Mapping[str, object],
    visual_runtime_selector: Mapping[str, object],
    video_time_mapping: Mapping[str, object],
    outcome_observations: Sequence[Mapping[str, object]] = (),
    source_ref: str | None = None,
) -> dict:
    """Convert local detector observations into a typed numerical artifact."""
    analysis_ref = _stable_ref("analysis_ref", analysis_ref)
    window, window_start, window_end = _window_bounds(canonical_time_window)
    profile = _validate_profile(visual_quality_profile)
    selector = _validate_runtime_selector(
        visual_runtime_selector, "visual_runtime_selector"
    )
    time_mapping = _validate_video_time_mapping(
        video_time_mapping,
        canonical_time_window=window,
    )
    compatibility = evaluate_visual_runtime_compatibility_v2(profile, selector)
    source_ref = _stable_ref(
        "source_ref", source_ref or f"{analysis_ref}:source:visual-observation"
    )
    if not isinstance(frame_observations, Sequence) or isinstance(frame_observations, (str, bytes)):
        raise ValueError("frame_observations must be a sequence")
    if len(frame_observations) > 250_000:
        raise ValueError("frame_observations exceeds the local artifact bound")
    if not isinstance(outcome_observations, Sequence) or isinstance(
        outcome_observations, (str, bytes)
    ):
        raise ValueError("outcome_observations must be a sequence")
    if len(outcome_observations) > _EVENT_BUNDLE_LIMIT:
        raise ValueError("outcome_observations exceeds the event bundle bound")
    outcome_event_reserve = 0
    for observation in outcome_observations:
        if not isinstance(observation, Mapping):
            raise ValueError("outcome observation must be a mapping")
        requested_level = observation.get("association_kind")
        if requested_level not in {
            "directly_observed", "validated_aligned", "inferred", "unavailable",
        }:
            raise ValueError("outcome association level is invalid")
        outcome_event_reserve += 1
        if observation.get("outcome_pts_ms") is not None and requested_level != "unavailable":
            outcome_event_reserve += 1
    if outcome_event_reserve > _EVENT_BUNDLE_LIMIT:
        raise ValueError("outcome observations exceed the event bundle bound")
    derived_event_limit = _EVENT_BUNDLE_LIMIT - outcome_event_reserve

    limitations: list[str] = list(compatibility["limitations"])
    mapped: list[tuple[int, Mapping[str, object]]] = []
    previous_pts: float | None = None
    previous_canonical_time: int | None = None
    for index, observation in enumerate(frame_observations):
        if not isinstance(observation, Mapping):
            raise ValueError(f"observation[{index}] must be a mapping")
        pts = observation.get("source_pts_ms")
        if pts is None:
            if "missing_frame_pts" not in limitations:
                limitations.append("missing_frame_pts")
            continue
        number = _finite(f"observation[{index}].source_pts_ms", pts, minimum=0.0)
        if previous_pts is not None and number <= previous_pts:
            if "non_monotonic_frame_pts" not in limitations:
                limitations.append("non_monotonic_frame_pts")
            continue
        previous_pts = number
        canonical_time = _source_pts_to_canonical_time(time_mapping, number)
        if not window_start <= canonical_time < window_end:
            if "frame_pts_outside_canonical_window" not in limitations:
                limitations.append("frame_pts_outside_canonical_window")
            continue
        if (
            previous_canonical_time is not None
            and canonical_time - previous_canonical_time > _MAX_CONTIGUOUS_FRAME_GAP_MS
            and "visual_frame_gap" not in limitations
        ):
            limitations.append("visual_frame_gap")
        previous_canonical_time = canonical_time
        mapped.append((canonical_time, observation))

    if mapped:
        mapped_times = [time_ms for time_ms, _ in mapped]
        deltas = [
            right - left
            for left, right in zip(mapped_times, mapped_times[1:])
            if right > left
        ]
        if deltas:
            boundary_tolerance_ms = max(
                1,
                int(math.ceil(float(median(deltas)) * 1.5)),
            )
            if (
                mapped_times[0] - window_start > boundary_tolerance_ms
                or window_end - mapped_times[-1] > boundary_tolerance_ms
            ):
                limitations.append("visual_frame_boundary_gap")
        else:
            limitations.append("visual_frame_boundary_unverifiable")

    crosshair_samples: list[dict] = []
    tracks: dict[int, dict] = {}
    detector_tracks: dict[str, int] = {}
    active_ids: set[int] = set()
    events: list[dict] = []
    event_index = 0
    active_state_signature: tuple[object, ...] | None = None
    active_state_event: dict | None = None

    def add_event(kind: str, time_ms: int, **kwargs) -> None:
        nonlocal event_index
        if event_index >= derived_event_limit:
            if "visual_event_budget_exceeded" not in limitations:
                limitations.append("visual_event_budget_exceeded")
            return
        event_index += 1
        events.append(_event(analysis_ref, event_index, kind, time_ms, source_ref, **kwargs))

    def add_state_event(
        kind: str,
        time_ms: int,
        *,
        actor_refs: Sequence[str] = (),
        confidence: float,
        state_limitations: Sequence[str],
    ) -> None:
        nonlocal event_index, active_state_event, active_state_signature
        normalized_actor_refs = tuple(sorted(set(actor_refs)))
        signature = (
            kind,
            normalized_actor_refs,
            confidence,
            tuple(state_limitations),
        )
        if (
            active_state_event is not None
            and signature == active_state_signature
            and time_ms - active_state_event["end_ms"] <= _MAX_CONTIGUOUS_FRAME_GAP_MS
        ):
            active_state_event["end_ms"] = time_ms
            return
        if event_index >= derived_event_limit:
            if "visual_event_budget_exceeded" not in limitations:
                limitations.append("visual_event_budget_exceeded")
            clear_state_event()
            return
        event_index += 1
        active_state_event = _event(
            analysis_ref,
            event_index,
            kind,
            time_ms,
            source_ref,
            actor_refs=normalized_actor_refs,
            confidence=confidence,
            limitations=state_limitations,
        )
        active_state_signature = signature
        events.append(active_state_event)

    def clear_state_event() -> None:
        nonlocal active_state_event, active_state_signature
        active_state_event = None
        active_state_signature = None

    for canonical_time, observation in mapped:
        scene = observation.get("scene")
        if scene != "gameplay":
            if "non_gameplay_scene" not in limitations:
                limitations.append("non_gameplay_scene")
            for track in tracks.values():
                if track["samples"]:
                    track["was_missing"] = True
            active_ids = set()
            add_state_event(
                "low_confidence",
                canonical_time,
                confidence=0.0,
                state_limitations=["non_gameplay_scene"],
            )
            continue
        crosshair = observation.get("crosshair")
        if crosshair is None:
            if "crosshair_not_observed" not in limitations:
                limitations.append("crosshair_not_observed")
        elif isinstance(crosshair, Mapping):
            crosshair_samples.append({
                "canonical_time_ms": canonical_time,
                "x": _finite("crosshair.x", crosshair.get("x")),
                "y": _finite("crosshair.y", crosshair.get("y")),
                "confidence": _ratio("crosshair.confidence", crosshair.get("confidence", 1.0)),
            })
        else:
            raise ValueError("crosshair observation is invalid")

        raw_ambiguities = observation.get("target_ambiguities") or []
        if not isinstance(raw_ambiguities, Sequence) or isinstance(
            raw_ambiguities, (str, bytes)
        ):
            raise ValueError("target_ambiguities must be a sequence")
        normalized_ambiguities: list[dict] = []
        for ambiguity in raw_ambiguities:
            if not isinstance(ambiguity, Mapping) or set(ambiguity) != {
                "ambiguity_kind", "x", "y", "visible_radius", "confidence",
            }:
                raise ValueError("target ambiguity fields are invalid")
            if ambiguity["ambiguity_kind"] != "merged_target_component":
                raise ValueError("target ambiguity kind is invalid")
            normalized_ambiguities.append({
                "ambiguity_kind": "merged_target_component",
                "x": _finite("target ambiguity.x", ambiguity["x"]),
                "y": _finite("target ambiguity.y", ambiguity["y"]),
                "visible_radius": _finite(
                    "target ambiguity.visible_radius",
                    ambiguity["visible_radius"],
                    minimum=0.01,
                ),
                "confidence": _ratio(
                    "target ambiguity.confidence", ambiguity["confidence"]
                ),
            })
        blocked_active_ids: set[int] = set()
        if normalized_ambiguities:
            if "target_merge_ambiguous" not in limitations:
                limitations.append("target_merge_ambiguous")
            for track_id in active_ids:
                track = tracks[track_id]
                if not track["samples"]:
                    continue
                last_sample = track["samples"][-1]
                if any(
                    math.hypot(
                        last_sample["x"] - ambiguity["x"],
                        last_sample["y"] - ambiguity["y"],
                    )
                    <= last_sample["visible_radius"] + ambiguity["visible_radius"]
                    for ambiguity in normalized_ambiguities
                ):
                    blocked_active_ids.add(track_id)
                    track["was_missing"] = True
                    if "target_merge_ambiguous" not in track["limitations"]:
                        track["limitations"].append("target_merge_ambiguous")
            active_ids -= blocked_active_ids

        raw_targets = observation.get("targets") or []
        if not isinstance(raw_targets, Sequence) or isinstance(raw_targets, (str, bytes)):
            raise ValueError("targets must be a sequence")
        normalized_targets: list[dict] = []
        for target_index, target in enumerate(raw_targets):
            if not isinstance(target, Mapping):
                raise ValueError("target observation must be a mapping")
            detector_ref = target.get("detector_ref")
            if detector_ref is not None:
                detector_ref = _bounded_text("target.detector_ref", detector_ref)
            measurement_source = target.get("measurement_source", "direct_detector")
            if measurement_source not in {
                "direct_detector", "temporal_tracker_confirmed",
            }:
                raise ValueError("target measurement source is invalid")
            normalized_targets.append({
                "detector_ref": detector_ref,
                "measurement_source": measurement_source,
                "x": _finite("target.x", target.get("x")),
                "y": _finite("target.y", target.get("y")),
                "visible_radius": _finite(
                    "target.visible_radius", target.get("visible_radius"), minimum=0.01
                ),
                "confidence": _ratio("target.confidence", target.get("confidence")),
                "source_order": target_index,
            })
        normalized_targets.sort(
            key=lambda item: (
                item["detector_ref"] is None,
                item["detector_ref"] or "",
                item["x"],
                item["y"],
                item["source_order"],
            )
        )

        assigned: list[tuple[int, dict]] = []
        unused_active = set(active_ids)
        for target in normalized_targets:
            missing_before_assignment = [
                missing_track_id
                for missing_track_id, missing_track in tracks.items()
                if missing_track["was_missing"]
            ]
            track_id = None
            detector_ref = target["detector_ref"]
            if detector_ref is not None:
                track_id = detector_tracks.get(detector_ref)
            if track_id is None and detector_ref is None and unused_active:
                candidates = sorted(
                    (
                        math.hypot(
                            target["x"] - tracks[item]["samples"][-1]["x"],
                            target["y"] - tracks[item]["samples"][-1]["y"],
                        ),
                        item,
                    )
                    for item in unused_active
                )
                if candidates and candidates[0][0] <= 96.0:
                    track_id = candidates[0][1]
            if track_id is None:
                track_id = len(tracks) + 1
                tracks[track_id] = {
                    "detector_ref": detector_ref,
                    "samples": [],
                    "was_missing": False,
                    "limitations": [],
                }
                if detector_ref is not None:
                    detector_tracks[detector_ref] = track_id
            if track_id in unused_active:
                unused_active.remove(track_id)
            track = tracks[track_id]
            if detector_ref is None and missing_before_assignment:
                # Proximity cannot prove that a post-occlusion blob is the same target.
                if "reentry_identity_unresolved" not in limitations:
                    limitations.append("reentry_identity_unresolved")
                for affected_track_id in [*missing_before_assignment, track_id]:
                    affected_track = tracks[affected_track_id]
                    if "reentry_identity_unresolved" not in affected_track["limitations"]:
                        affected_track["limitations"].append("reentry_identity_unresolved")
            if track["was_missing"]:
                track_ref = f"{analysis_ref}:target-track:{track_id}"
                add_event(
                    "reacquired", canonical_time, actor_refs=[track_ref], confidence=0.8,
                    limitations=["target_occlusion"],
                )
                track["was_missing"] = False
            track["samples"].append({
                "canonical_time_ms": canonical_time,
                "x": target["x"],
                "y": target["y"],
                "visible_radius": target["visible_radius"],
                "confidence": target["confidence"],
                "measurement_source": target["measurement_source"],
            })
            assigned.append((track_id, target))

        assigned_ids = {item[0] for item in assigned}
        missing_ids = set(tracks) - assigned_ids
        for track_id in missing_ids:
            if tracks[track_id]["samples"]:
                tracks[track_id]["was_missing"] = True
        active_ids = assigned_ids

        if normalized_ambiguities:
            add_state_event(
                "candidate_visible",
                canonical_time,
                confidence=0.0,
                state_limitations=["target_merge_ambiguous"],
            )
        elif not normalized_targets:
            no_target_limitation = "target_occlusion" if tracks else "no_target_visible"
            if no_target_limitation not in limitations:
                limitations.append(no_target_limitation)
            add_state_event(
                "low_confidence",
                canonical_time,
                confidence=0.0,
                state_limitations=[no_target_limitation],
            )
        elif len(normalized_targets) > 1:
            if "multiple_targets_visible" not in limitations:
                limitations.append("multiple_targets_visible")
            add_state_event(
                "candidate_visible",
                canonical_time,
                confidence=0.8,
                state_limitations=["multiple_targets_visible"],
            )
        else:
            clear_state_event()

    ordered_tracks = sorted(tracks.items())
    for left_index, (left_id, left) in enumerate(ordered_tracks):
        left_by_time = {sample["canonical_time_ms"]: sample for sample in left["samples"]}
        for right_id, right in ordered_tracks[left_index + 1:]:
            right_by_time = {sample["canonical_time_ms"]: sample for sample in right["samples"]}
            common = sorted(set(left_by_time) & set(right_by_time))
            signs = []
            for time_ms in common:
                delta = left_by_time[time_ms]["x"] - right_by_time[time_ms]["x"]
                signs.append(1 if delta > 0 else -1 if delta < 0 else 0)
            nonzero = [sign for sign in signs if sign]
            if any(first != second for first, second in zip(nonzero, nonzero[1:])):
                if "identity_crossing_ambiguous" not in limitations:
                    limitations.append("identity_crossing_ambiguous")
                for track_id in (left_id, right_id):
                    if "identity_crossing_ambiguous" not in tracks[track_id]["limitations"]:
                        tracks[track_id]["limitations"].append("identity_crossing_ambiguous")

    for track_id, track in ordered_tracks:
        samples = track["samples"]
        change_deadband = profile["acceptance_thresholds"]["center_error_p95_px"]
        for sample_index in range(2, len(samples)):
            reversed_outside_noise = any(
                first_delta * second_delta < 0
                and min(abs(first_delta), abs(second_delta)) > change_deadband
                for axis in ("x", "y")
                for first_delta, second_delta in [(
                    samples[sample_index - 1][axis] - samples[sample_index - 2][axis],
                    samples[sample_index][axis] - samples[sample_index - 1][axis],
                )]
            )
            if reversed_outside_noise:
                add_event(
                    "target_change_point",
                    samples[sample_index]["canonical_time_ms"],
                    actor_refs=[f"{analysis_ref}:target-track:{track_id}"],
                    attributes={"change_kind": "direction_reversal"},
                    limitations=["change_point_not_independently_validated"],
                )

    valid_observation_count = len(mapped)
    frame_pts_coverage = (
        valid_observation_count / len(frame_observations)
        if frame_observations else 0.0
    )
    target_visible_count = sum(
        1 for _, observation in mapped if observation.get("scene") == "gameplay" and observation.get("targets")
    )
    target_coverage = (
        target_visible_count / valid_observation_count if valid_observation_count else 0.0
    )
    crosshair_coverage = (
        len(crosshair_samples) / valid_observation_count if valid_observation_count else 0.0
    )
    target_coverage = min(target_coverage, frame_pts_coverage)
    crosshair_coverage = min(crosshair_coverage, frame_pts_coverage)
    runtime_quality_limitations = []
    runtime_disabled_families: set[str] = set()
    compatible_families = list(compatibility["enabled_metric_families"])

    def disable_all_compatible_families(limitation: str) -> None:
        runtime_disabled_families.update(compatible_families)
        runtime_quality_limitations.append(limitation)

    # Annotation recall and per-frame target presence have different
    # denominators.  Only the fixed viewport aim point has a runtime invariant.
    if crosshair_coverage < 1.0:
        disable_all_compatible_families("visual_quality_below_threshold:crosshair_coverage")
    if "identity_crossing_ambiguous" in limitations:
        for family in compatible_families:
            if (
                "identity_switch_rate"
                in profile["required_quality_fields_by_metric_family"][family]
            ):
                runtime_disabled_families.add(family)
                runtime_quality_limitations.append(
                    f"visual_quality_below_threshold:{family}:identity_continuity"
                )
    if "target_merge_ambiguous" in limitations:
        for family in compatible_families:
            if (
                "identity_switch_rate"
                in profile["required_quality_fields_by_metric_family"][family]
            ):
                runtime_disabled_families.add(family)
                runtime_quality_limitations.append(
                    f"visual_quality_below_threshold:{family}:identity_continuity"
                )
    if "reentry_identity_unresolved" in limitations:
        reentry_dependent_families = []
        for family in compatible_families:
            required_quality_fields = set(
                profile["required_quality_fields_by_metric_family"][family]
            )
            if required_quality_fields & {
                "identity_switch_rate", "occlusion_reentry_accuracy",
            }:
                reentry_dependent_families.append(family)
                runtime_disabled_families.add(family)
                runtime_quality_limitations.append(
                    f"visual_quality_below_threshold:{family}:occlusion_reentry"
                )
        if reentry_dependent_families:
            runtime_quality_limitations.append(
                "visual_quality_below_threshold:occlusion_reentry"
            )
    if "visual_event_budget_exceeded" in limitations:
        disable_all_compatible_families(
            "visual_quality_below_threshold:event_completeness"
        )
    if (
        "visual_frame_gap" in limitations
        or "visual_frame_boundary_gap" in limitations
        or "visual_frame_boundary_unverifiable" in limitations
    ):
        disable_all_compatible_families("visual_quality_below_threshold:frame_coverage")
    if {
        "missing_frame_pts",
        "non_monotonic_frame_pts",
        "frame_pts_outside_canonical_window",
    }.intersection(limitations):
        runtime_quality_limitations.append(
            "visual_quality_below_threshold:frame_pts_completeness"
        )
    quality = dict(compatibility)
    if compatible_families and runtime_quality_limitations:
        enabled_families = [
            family
            for family in compatible_families
            if family not in runtime_disabled_families
        ]
        quality = {
            "status": "limited",
            "enabled_metric_families": enabled_families,
            "limitations": [
                *compatibility["limitations"], *runtime_quality_limitations,
            ],
        }
        for limitation in runtime_quality_limitations:
            if limitation not in limitations:
                limitations.append(limitation)

    sample_sets: list[dict] = []
    channels: list[dict] = []

    def add_channel(channel_key: str, unit: str, points: list[list[float]], confidence: float) -> None:
        sample_ref = f"{analysis_ref}:samples:{channel_key.replace('.', '-')}"
        sample_sets.append({
            "sample_set_id": sample_ref,
            "channel_key": channel_key,
            "unit": unit,
            "points": points,
        })
        coverage = len(points) / len(frame_observations) if frame_observations else 0.0
        channels.append({
            "channel_key": channel_key,
            "source_refs": [source_ref],
            "coordinate_space": "capture_coordinates",
            "unit": unit,
            "sample_rate_semantics": "source_pts_irregular",
            "samples_ref": sample_ref,
            "coverage": min(1.0, coverage),
            "confidence_summary": confidence,
            "transform_version": profile["producer_version"],
            "limitations": list(quality["limitations"]),
        })

    if crosshair_samples:
        confidence = sum(item["confidence"] for item in crosshair_samples) / len(crosshair_samples)
        add_channel(
            "crosshair.position_x", "px",
            [[item["canonical_time_ms"], item["x"]] for item in crosshair_samples],
            confidence,
        )
        add_channel(
            "crosshair.position_y", "px",
            [[item["canonical_time_ms"], item["y"]] for item in crosshair_samples],
            confidence,
        )
    for track_id, track in ordered_tracks:
        confidence = sum(item["confidence"] for item in track["samples"]) / len(track["samples"])
        prefix = f"target.{track_id}"
        add_channel(prefix + ".position_x", "px", [
            [item["canonical_time_ms"], item["x"]] for item in track["samples"]
        ], confidence)
        add_channel(prefix + ".position_y", "px", [
            [item["canonical_time_ms"], item["y"]] for item in track["samples"]
        ], confidence)
        add_channel(prefix + ".visible_radius", "px", [
            [item["canonical_time_ms"], item["visible_radius"]] for item in track["samples"]
        ], confidence)

    if not channels:
        raise ValueError("visual observations contain no numerical channels")

    associations: list[dict] = []
    for index, observation in enumerate(outcome_observations, 1):
        requested_level = observation.get("association_kind")
        shot_time = _source_pts_to_canonical_time(
            time_mapping,
            _finite("shot_pts_ms", observation.get("shot_pts_ms"), minimum=0.0),
        )
        if not window_start <= shot_time < window_end:
            raise ValueError("shot observation is outside canonical window")
        event_index += 1
        shot = _event(analysis_ref, event_index, "shot", shot_time, source_ref)
        events.append(shot)
        outcome_ref = None
        outcome_pts = observation.get("outcome_pts_ms")
        if outcome_pts is not None and requested_level != "unavailable":
            outcome_time = _source_pts_to_canonical_time(
                time_mapping,
                _finite("outcome_pts_ms", outcome_pts, minimum=0.0),
            )
            if not window_start <= outcome_time < window_end:
                raise ValueError("outcome observation is outside canonical window")
            event_index += 1
            outcome = _event(analysis_ref, event_index, "hit", outcome_time, source_ref)
            events.append(outcome)
            outcome_ref = outcome["event_id"]
        detector_ref = observation.get("detector_ref")
        track_id = detector_tracks.get(detector_ref) if isinstance(detector_ref, str) else None
        validation_ref = observation.get("validation_ref")
        if validation_ref is not None:
            validation_ref = _stable_ref("outcome validation_ref", validation_ref)
        if requested_level in {"directly_observed", "validated_aligned"}:
            if outcome_ref is None or track_id is None:
                raise ValueError("available outcome association requires target and outcome evidence")
            if requested_level == "validated_aligned" and validation_ref is None:
                raise ValueError("available outcome association requires a validation ref")
            association_kind = requested_level
            availability = "available"
            confidence = 1.0 if requested_level == "directly_observed" else 0.9
            association_limitations: list[str] = []
        elif requested_level == "inferred":
            association_kind = "inferred"
            availability = "partial"
            confidence = 0.5
            association_limitations = ["outcome_association_inferred"]
        else:
            association_kind = "inferred"
            availability = "unavailable"
            confidence = 0.0
            association_limitations = ["outcome_association_unavailable"]
        associations.append({
            "association_id": f"{analysis_ref}:association:visual:{index}",
            "shot_event_ref": shot["event_id"],
            "outcome_event_ref": outcome_ref,
            "target_track_ref": (
                f"{analysis_ref}:target-track:{track_id}" if track_id is not None else None
            ),
            "weapon_temporal_model": observation.get("weapon_temporal_model", "unknown"),
            "association_kind": association_kind,
            "source_refs": [source_ref, *([validation_ref] if validation_ref else [])],
            "confidence": confidence,
            "availability": availability,
            "limitations": association_limitations,
        })

    track_summaries = [
        {
            "track_ref": f"{analysis_ref}:target-track:{track_id}",
            "identity_source": "detector_ref" if track["detector_ref"] is not None else "deterministic_proximity",
            "visible_radius_px": float(median(
                sample["visible_radius"] for sample in track["samples"]
            )),
            "sample_count": len(track["samples"]),
            "coverage": len(track["samples"]) / valid_observation_count if valid_observation_count else 0.0,
            "limitations": list(track["limitations"]),
        }
        for track_id, track in ordered_tracks
    ]
    events.sort(key=lambda item: (item["start_ms"], item["event_id"]))
    signal_bundle = {
        "schema_version": "signal_bundle.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window_ref": f"{analysis_ref}:canonical-window",
        "visual_quality_profile_ref": profile["profile_ref"],
        # signal_bundle.v1 keeps its historical container field; the versioned
        # value stored in it is the new runtime selector, not capture metadata.
        "observed_visual_domain": selector,
        "channels": channels,
    }
    event_bundle = {
        "schema_version": "event_bundle.v1",
        "analysis_ref": analysis_ref,
        "events": events,
        "outcome_associations": associations,
    }
    from .analysis_evidence import validate_event_bundle_v1, validate_signal_bundle_v1

    validate_signal_bundle_v1(signal_bundle)
    validate_event_bundle_v1(event_bundle)
    completeness = "partial" if any(item in limitations for item in (
        "missing_frame_pts", "non_monotonic_frame_pts", "frame_pts_outside_canonical_window",
        "visual_event_budget_exceeded", "visual_frame_gap", "visual_frame_boundary_gap",
        "visual_frame_boundary_unverifiable",
    )) else "complete"
    safe_summary = {
        "schema_version": "visual_signal_summary.v1",
        "status": "available",
        "producer_version": profile["producer_version"],
        "quality_status": quality["status"],
        "enabled_metric_families": list(quality["enabled_metric_families"]),
        "track_count": len(track_summaries),
        "observation_count": len(mapped),
        "target_coverage": target_coverage,
        "crosshair_coverage": crosshair_coverage,
        "completeness": completeness,
        "event_counts": {
            kind: sum(1 for event in events if event["event_kind"] == kind)
            for kind in sorted({event["event_kind"] for event in events})
        },
        "limitations": list(limitations),
    }
    return {
        "schema_version": VISUAL_SIGNAL_SCHEMA_VERSION,
        "analysis_ref": analysis_ref,
        "canonical_time_window": window,
        "video_time_mapping": time_mapping,
        "visual_quality_profile_ref": profile["profile_ref"],
        "visual_runtime_selector": selector,
        "quality": quality,
        "completeness": completeness,
        "track_summaries": track_summaries,
        "signal_bundle": signal_bundle,
        "event_bundle": event_bundle,
        "sample_sets": sample_sets,
        "local_samples": {
            "crosshair.position": crosshair_samples,
            **{
                f"target.{track_id}.position": copy.deepcopy(track["samples"])
                for track_id, track in ordered_tracks
            },
        },
        "safe_summary": safe_summary,
        "limitations": limitations,
    }


def _preprocess_visual_target_episode_signals_v1(
    *,
    analysis_ref: str,
    canonical_time_window: Mapping[str, object],
    frame_observations: Sequence[Mapping[str, object]],
    visual_quality_profile: Mapping[str, object],
    visual_runtime_selector: Mapping[str, object],
    video_time_mapping: Mapping[str, object],
    outcome_observations: Sequence[Mapping[str, object]] = (),
    source_ref: str | None = None,
) -> dict:
    """Prepare Switching observations without constructing global target identity."""
    analysis_ref = _stable_ref("analysis_ref", analysis_ref)
    window, window_start, window_end = _window_bounds(canonical_time_window)
    profile = _validate_profile(visual_quality_profile)
    selector = _validate_runtime_selector(
        visual_runtime_selector, "visual_runtime_selector"
    )
    time_mapping = _validate_video_time_mapping(
        video_time_mapping,
        canonical_time_window=window,
    )
    compatibility = evaluate_visual_runtime_compatibility_v2(profile, selector)
    source_ref = _stable_ref(
        "source_ref", source_ref or f"{analysis_ref}:source:visual-observation"
    )
    if not isinstance(frame_observations, Sequence) or isinstance(
        frame_observations, (str, bytes)
    ):
        raise ValueError("frame_observations must be a sequence")
    if len(frame_observations) > 250_000:
        raise ValueError("frame_observations exceeds the local artifact bound")
    if not isinstance(outcome_observations, Sequence) or isinstance(
        outcome_observations, (str, bytes)
    ):
        raise ValueError("outcome_observations must be a sequence")
    if len(outcome_observations) > _EVENT_BUNDLE_LIMIT:
        raise ValueError("outcome_observations exceeds the event bundle bound")

    limitations: list[str] = list(compatibility["limitations"])
    mapped: list[tuple[int, Mapping[str, object]]] = []
    previous_pts: float | None = None
    previous_canonical_time: int | None = None
    for index, observation in enumerate(frame_observations):
        if not isinstance(observation, Mapping):
            raise ValueError(f"observation[{index}] must be a mapping")
        pts = observation.get("source_pts_ms")
        if pts is None:
            if "missing_frame_pts" not in limitations:
                limitations.append("missing_frame_pts")
            continue
        number = _finite(f"observation[{index}].source_pts_ms", pts, minimum=0.0)
        if previous_pts is not None and number <= previous_pts:
            if "non_monotonic_frame_pts" not in limitations:
                limitations.append("non_monotonic_frame_pts")
            continue
        previous_pts = number
        canonical_time = _source_pts_to_canonical_time(time_mapping, number)
        if not window_start <= canonical_time < window_end:
            if "frame_pts_outside_canonical_window" not in limitations:
                limitations.append("frame_pts_outside_canonical_window")
            continue
        if (
            previous_canonical_time is not None
            and canonical_time - previous_canonical_time > _MAX_CONTIGUOUS_FRAME_GAP_MS
            and "visual_frame_gap" not in limitations
        ):
            limitations.append("visual_frame_gap")
        previous_canonical_time = canonical_time
        mapped.append((canonical_time, observation))

    if mapped:
        mapped_times = [time_ms for time_ms, _ in mapped]
        deltas = [
            right - left
            for left, right in zip(mapped_times, mapped_times[1:])
            if right > left
        ]
        if deltas:
            boundary_tolerance_ms = max(1, int(math.ceil(float(median(deltas)) * 1.5)))
            if (
                mapped_times[0] - window_start > boundary_tolerance_ms
                or window_end - mapped_times[-1] > boundary_tolerance_ms
            ):
                limitations.append("visual_frame_boundary_gap")
        else:
            limitations.append("visual_frame_boundary_unverifiable")

    crosshair_samples: list[dict] = []
    for canonical_time, observation in mapped:
        if observation.get("scene") != "gameplay":
            if "non_gameplay_scene" not in limitations:
                limitations.append("non_gameplay_scene")
            continue
        crosshair = observation.get("crosshair")
        if crosshair is None:
            if "crosshair_not_observed" not in limitations:
                limitations.append("crosshair_not_observed")
        elif isinstance(crosshair, Mapping):
            crosshair_samples.append({
                "canonical_time_ms": canonical_time,
                "x": _finite("crosshair.x", crosshair.get("x")),
                "y": _finite("crosshair.y", crosshair.get("y")),
                "confidence": _ratio(
                    "crosshair.confidence", crosshair.get("confidence", 1.0)
                ),
            })
        else:
            raise ValueError("crosshair observation is invalid")

    valid_observation_count = len(mapped)
    target_visible_count = sum(
        1
        for _, observation in mapped
        if observation.get("scene") == "gameplay" and observation.get("targets")
    )
    frame_pts_coverage = (
        valid_observation_count / len(frame_observations)
        if frame_observations else 0.0
    )
    target_coverage = min(
        target_visible_count / valid_observation_count if valid_observation_count else 0.0,
        frame_pts_coverage,
    )
    crosshair_coverage = min(
        len(crosshair_samples) / valid_observation_count if valid_observation_count else 0.0,
        frame_pts_coverage,
    )
    runtime_quality_limitations: list[str] = []
    if crosshair_coverage < 1.0:
        runtime_quality_limitations.append(
            "visual_quality_below_threshold:crosshair_coverage"
        )
    if {
        "visual_frame_gap",
        "visual_frame_boundary_gap",
        "visual_frame_boundary_unverifiable",
    }.intersection(limitations):
        runtime_quality_limitations.append(
            "visual_quality_below_threshold:frame_coverage"
        )
    quality = dict(compatibility)
    if compatibility["enabled_metric_families"] and runtime_quality_limitations:
        quality = {
            "status": "limited",
            "enabled_metric_families": [],
            "limitations": [
                *compatibility["limitations"], *runtime_quality_limitations,
            ],
        }
        for limitation in runtime_quality_limitations:
            if limitation not in limitations:
                limitations.append(limitation)

    sample_sets: list[dict] = []
    channels: list[dict] = []
    for channel_key, field in (
        ("crosshair.position_x", "x"),
        ("crosshair.position_y", "y"),
    ):
        sample_ref = f"{analysis_ref}:samples:{channel_key.replace('.', '-')}"
        points = [[sample["canonical_time_ms"], sample[field]] for sample in crosshair_samples]
        sample_sets.append({
            "sample_set_id": sample_ref,
            "channel_key": channel_key,
            "unit": "px",
            "points": points,
        })
        channels.append({
            "channel_key": channel_key,
            "source_refs": [source_ref],
            "coordinate_space": "capture_coordinates",
            "unit": "px",
            "sample_rate_semantics": "source_pts_irregular",
            "samples_ref": sample_ref,
            "coverage": crosshair_coverage,
            "confidence_summary": (
                sum(sample["confidence"] for sample in crosshair_samples)
                / len(crosshair_samples)
                if crosshair_samples else 0.0
            ),
            "transform_version": profile["producer_version"],
            "limitations": list(quality["limitations"]),
        })

    from .analysis_evidence import validate_event_bundle_v1, validate_signal_bundle_v1

    signal_bundle = {
        "schema_version": "signal_bundle.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window_ref": f"{analysis_ref}:canonical-window",
        "visual_quality_profile_ref": profile["profile_ref"],
        "observed_visual_domain": selector,
        "channels": channels,
    }
    event_bundle = {
        "schema_version": "event_bundle.v1",
        "analysis_ref": analysis_ref,
        "events": [],
        "outcome_associations": [],
    }
    validate_signal_bundle_v1(signal_bundle)
    validate_event_bundle_v1(event_bundle)
    completeness = "partial" if any(item in limitations for item in (
        "missing_frame_pts", "non_monotonic_frame_pts", "frame_pts_outside_canonical_window",
        "visual_frame_gap", "visual_frame_boundary_gap", "visual_frame_boundary_unverifiable",
    )) else "complete"
    return {
        "schema_version": VISUAL_SIGNAL_SCHEMA_VERSION,
        "analysis_ref": analysis_ref,
        "canonical_time_window": window,
        "video_time_mapping": time_mapping,
        "visual_quality_profile_ref": profile["profile_ref"],
        "visual_runtime_selector": selector,
        "quality": quality,
        "completeness": completeness,
        "track_summaries": [],
        "signal_bundle": signal_bundle,
        "event_bundle": event_bundle,
        "sample_sets": sample_sets,
        "local_samples": {"crosshair.position": crosshair_samples},
        "frame_observations": [
            copy.deepcopy(observation) for _canonical_time, observation in mapped
        ],
        "safe_summary": {
            "schema_version": "visual_signal_summary.v1",
            "status": "available",
            "producer_version": profile["producer_version"],
            "quality_status": quality["status"],
            "enabled_metric_families": list(quality["enabled_metric_families"]),
            "track_count": 0,
            "observation_count": valid_observation_count,
            "target_coverage": target_coverage,
            "crosshair_coverage": crosshair_coverage,
            "completeness": completeness,
            "event_counts": {},
            "limitations": list(limitations),
        },
        "limitations": limitations,
    }


def preprocess_visual_signals_v1(
    *,
    analysis_ref: str,
    canonical_time_window: Mapping[str, object],
    frame_observations: Sequence[Mapping[str, object]],
    visual_quality_profile: Mapping[str, object],
    visual_runtime_selector: Mapping[str, object],
    video_time_mapping: Mapping[str, object],
    outcome_observations: Sequence[Mapping[str, object]] = (),
    source_ref: str | None = None,
) -> dict:
    """Select the reviewed producer's minimal preprocessing path."""
    profile = _validate_profile(visual_quality_profile)
    if (
        profile["producer_id"] == VISUAL_TARGET_EPISODE_PRODUCER_ID
        and profile["producer_version"] == VISUAL_TARGET_EPISODE_PRODUCER_VERSION
    ):
        return _preprocess_visual_target_episode_signals_v1(
            analysis_ref=analysis_ref,
            canonical_time_window=canonical_time_window,
            frame_observations=frame_observations,
            visual_quality_profile=profile,
            visual_runtime_selector=visual_runtime_selector,
            video_time_mapping=video_time_mapping,
            outcome_observations=outcome_observations,
            source_ref=source_ref,
        )
    return _preprocess_visual_signals_with_global_identity_v1(
        analysis_ref=analysis_ref,
        canonical_time_window=canonical_time_window,
        frame_observations=frame_observations,
        visual_quality_profile=profile,
        visual_runtime_selector=visual_runtime_selector,
        video_time_mapping=video_time_mapping,
        outcome_observations=outcome_observations,
        source_ref=source_ref,
    )


def extend_analysis_evidence_with_visual_signals_v1(
    artifact: Mapping[str, object],
    visual_result: Mapping[str, object],
) -> dict:
    """Append validated visual bundles to the generic local evidence artifact."""
    from .analysis_evidence import validate_analysis_evidence_artifact_v1

    validated = validate_analysis_evidence_artifact_v1(artifact)
    if visual_result.get("schema_version") != VISUAL_SIGNAL_SCHEMA_VERSION:
        raise ValueError("visual signal artifact version is unsupported")
    if visual_result.get("analysis_ref") != validated["analysis_ref"]:
        raise ValueError("visual signal artifact is bound to another analysis")
    if visual_result.get("canonical_time_window") != validated["canonical_time_window"]:
        raise ValueError("visual signal artifact is bound to another canonical window")
    projected = copy.deepcopy(validated)
    projected["signal_bundles"].append(copy.deepcopy(visual_result["signal_bundle"]))
    projected["event_bundles"].append(copy.deepcopy(visual_result["event_bundle"]))
    projected["sample_sets"].extend(copy.deepcopy(visual_result["sample_sets"]))
    for limitation in visual_result.get("limitations") or []:
        if limitation not in projected["limitations"]:
            projected["limitations"].append(limitation)
    return validate_analysis_evidence_artifact_v1(projected)


__all__ = [
    "VISUAL_PRODUCER_VERSION",
    "VISUAL_PRODUCER_ID",
    "VISUAL_TEMPORAL_PRODUCER_VERSION",
    "VISUAL_TEMPORAL_PRODUCER_ID",
    "VISUAL_SINGLE_TARGET_CSRT_PRODUCER_VERSION",
    "VISUAL_SINGLE_TARGET_CSRT_PRODUCER_ID",
    "VISUAL_TARGET_EPISODE_PRODUCER_VERSION",
    "VISUAL_TARGET_EPISODE_PRODUCER_ID",
    "ROUND_DETECTOR_MIN_CIRCULARITY",
    "CENTER_OVERLAY_MIN_CIRCULARITY",
    "VISUAL_QUALITY_PROFILE_SCHEMA_VERSION",
    "VISUAL_SIGNAL_SCHEMA_VERSION",
    "VisualPreprocessingUnavailable",
    "build_visual_quality_profile_v2",
    "visual_detector_config_ref_v1",
    "detect_color_candidates_v1",
    "detect_color_observations_v2",
    "evaluate_visual_annotation_quality_v1",
    "evaluate_visual_runtime_compatibility_v2",
    "extend_analysis_evidence_with_visual_signals_v1",
    "preprocess_visual_video_v1",
    "preprocess_visual_video_temporal_v1",
    "preprocess_visual_video_single_target_csrt_v1",
    "preprocess_visual_target_episodes_v1",
    "project_visual_target_episodes_v1",
    "preprocess_visual_signals_v1",
]
