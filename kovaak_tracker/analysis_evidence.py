"""Typed, path-free contracts for analysis-owned coaching evidence.

The module deliberately keeps high-frequency samples private to a local
artifact.  Public projections contain only allow-listed facts, records,
metrics and segment references.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime
from typing import Iterable


EVIDENCE_CONTRACT_VERSION = "analysis_evidence.v1"
FIELD_REGISTRY_VERSION = "source_field_registry.v1"
_MAX_STRING = 240
_MAX_LIST = 512
_PATH_OR_URL_RE = re.compile(r"(?:[A-Za-z]:[\\/]|^[/\\]|://|\\\\)")
_SECRET_RE = re.compile(r"(?:bearer\s+|api[_-]?key\s*[:=]|sk-[A-Za-z0-9]|<secret>)", re.I)
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._@+-]{0,239}$")
_VERSION_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*\.v[1-9][0-9]*$")
_TARGET_TRACK_SUFFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CHANNEL_RE = re.compile(
    r"^target\.[A-Za-z0-9._:-]+\."
    r"(?:position_[xy]|velocity_[xy]|acceleration_[xy]|visible_radius|hitbox)$"
)

_NATIVE_STATIC_EVIDENCE_EXTENSION_V1 = {
    "schema_version": "analysis_evidence_extension.v1",
    "extension_ref": "native-static-clicking@1",
    "channel_keys": [],
    "event_kinds": {
        "static_flick": [
            "legacy_event_ref", "peak_ms", "settle_end_ms", "quality",
            "movement_duration_ms", "time_to_peak_ms", "accel_duration_ms",
            "decel_duration_ms", "settle_duration_ms", "decel_frac",
            "peak_position_pct", "peak_speed", "path_length", "displacement",
            "path_efficiency", "straightness", "reverse_ratio",
            "direction_reverse_ratio", "corrective_count", "submovement_count",
            "trough_depth_ratio", "submovement_overlap", "sparc",
        ],
    },
    "metric_keys": [
        "static_clicking.path_length",
        "static_clicking.mean_speed",
        "static_clicking.mean_acceleration",
        "static_clicking.calibrated_path_length",
        "static_clicking.flick_count",
        "static_clicking.movement_duration_ms",
        "static_clicking.time_to_peak_ms",
        "static_clicking.accel_duration_ms",
        "static_clicking.decel_duration_ms",
        "static_clicking.settle_duration_ms",
        "static_clicking.decel_frac",
        "static_clicking.peak_position_pct",
        "static_clicking.peak_speed",
        "static_clicking.flick_path_length",
        "static_clicking.displacement",
        "static_clicking.path_efficiency",
        "static_clicking.straightness",
        "static_clicking.reverse_ratio",
        "static_clicking.direction_reverse_ratio",
        "static_clicking.corrective_count",
        "static_clicking.submovement_count",
        "static_clicking.trough_depth_ratio",
        "static_clicking.submovement_overlap",
        "static_clicking.sparc",
    ],
    "segment_kinds": [],
}

_DYNAMIC_CLICKING_EVIDENCE_EXTENSION_V1 = {
    "schema_version": "analysis_evidence_extension.v1",
    "extension_ref": "dynamic-clicking@1",
    "channel_keys": [],
    "event_kinds": {
        "dynamic_click": [
            "click_ref", "target_track_ref", "click_time_ms",
            "target_radius", "target_association_basis",
            "normalized_click_error", "miss_vector_x", "miss_vector_y",
            "target_speed", "target_acceleration",
            "relative_velocity_x", "relative_velocity_y",
            "relative_velocity_magnitude", "signed_lead_lag",
            "lead_lag_descriptor", "acquisition_start_ms", "acquisition_time_ms", "change_state",
            "target_motion_class", "association_availability",
            "outcome_available", "outcome_success", "condition_ref",
        ],
        "motion_predictability_evidence": [
            "segment_ref", "model_ref", "model_version", "fit_metric",
            "fit_value", "threshold_ref", "acceptance",
        ],
    },
    "metric_keys": [
        "dynamic_clicking.normalized_click_error",
        "dynamic_clicking.acquisition_time_ms",
        "dynamic_clicking.relative_velocity",
        "dynamic_clicking.target_state_accuracy",
        "dynamic_clicking.predictive_lead",
    ],
    "segment_kinds": [],
}

_CONTINUOUS_TRACKING_EVIDENCE_EXTENSION_V1 = {
    "schema_version": "analysis_evidence_extension.v1",
    "extension_ref": "continuous-tracking@1",
    "channel_keys": [],
    "event_kinds": {
        "tracking_episode": [
            "target_track_ref", "condition_ref", "sample_count",
            "usable_sample_count", "target_relative_error_px",
            "time_in_radius_ratio", "loss_count", "correction_burden",
            "sparc", "phase_lag_ms", "velocity_gain", "coherence",
        ],
        "tracking_fixed_window": [
            "target_track_ref", "condition_ref", "sample_count",
            "usable_sample_count", "target_relative_error_px",
            "time_in_radius_ratio", "correction_burden", "sparc",
        ],
        "tracking_loss": ["target_track_ref", "duration_ms"],
        "tracking_reacquisition": [
            "target_track_ref", "loss_ref", "reacquisition_latency_ms",
        ],
        "tracking_change_response": [
            "target_track_ref", "change_ref", "observed_change_response_ms",
            "alignment_latency_ms", "post_change_error_px",
        ],
        "motion_predictability_evidence": [
            "schema_version", "kind", "fit_metric_version", "availability",
        ],
    },
    "metric_keys": [
        "continuous_tracking.target_relative_error_px",
        "continuous_tracking.time_in_radius_ratio",
        "continuous_tracking.loss_count",
        "continuous_tracking.loss_duration_ms",
        "continuous_tracking.reacquisition_latency_ms",
        "continuous_tracking.relative_lag_ms",
        "continuous_tracking.phase_lag_ms",
        "continuous_tracking.coherence",
        "continuous_tracking.velocity_gain",
        "continuous_tracking.alignment_latency_ms",
        "continuous_tracking.observed_change_response_ms",
        "continuous_tracking.human_response_latency_ms",
        "continuous_tracking.correction_direction_reversal_count",
        "continuous_tracking.smoothness_acceleration_rms",
        "continuous_tracking.sparc",
        "continuous_tracking.predictive_lead_ms",
    ],
    "segment_kinds": [],
}

_TARGET_SWITCHING_ROW_ATTRIBUTES_V1 = [
    "chain_ref", "classification", "previous_outcome_association_ref",
    "previous_target_track_ref", "previous_outcome_time_ms", "leave_time_ms",
    "candidate_count", "selection_observation_ref", "selected_target_track_ref",
    "next_target_track_ref", "acquire_time_ms", "settle_time_ms",
    "transition_time_ms", "transition_distance_px", "transition_direction_deg",
    "transition_path_length_px", "path_efficiency", "settle_duration_ms",
    "first_shot_event_ref", "first_shot_latency_ms", "first_damage_event_ref",
    "first_damage_latency_ms", "carry_over_overshoot",
    "carry_over_overshoot_observation_ref", "terminal_correction_observed",
    "terminal_correction_observation_ref",
]

_TARGET_SWITCHING_EVIDENCE_EXTENSION_V1 = {
    "schema_version": "analysis_evidence_extension.v1",
    "extension_ref": "target-switching@1",
    "channel_keys": [],
    "event_kinds": {
        "switch_chain": _TARGET_SWITCHING_ROW_ATTRIBUTES_V1,
        "unclassified_discrete_acquisition": _TARGET_SWITCHING_ROW_ATTRIBUTES_V1,
        "switch_previous_outcome": ["row_ref"],
        "leave_previous": ["row_ref"],
        "candidate_visible": ["row_ref"],
        "target_selected": ["row_ref"],
        "transition": ["row_ref"],
        "next_target_acquired": ["row_ref"],
        "settle": ["row_ref"],
        "switch_first_shot": ["row_ref"],
        "first_damage": ["row_ref"],
    },
    "metric_keys": [
        "target_switching.transition_time_ms",
        "target_switching.transition_distance_px",
        "target_switching.path_efficiency",
        "target_switching.settle_duration_ms",
        "target_switching.first_shot_latency_ms",
        "target_switching.first_damage_latency_ms",
        "target_switching.carry_over_overshoot_ratio",
        "target_switching.terminal_correction_ratio",
    ],
    "segment_kinds": [],
}

_DYNAMIC_PROCESSED_EVENT_FIELDS_V1 = (
    ("event_id", "identity", "ref", None, None, None, "descriptive_only"),
    ("start_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("end_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("confidence", "quality", "number", "ratio", None, None, "descriptive_only"),
    ("limitations", "quality", "string_list", None, None, None, "descriptive_only"),
    ("click_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("target_track_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("click_time_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("target_radius", "condition", "number", "px", None, None, "descriptive_only"),
    ("target_association_basis", "quality", "string", None, None, None, "descriptive_only"),
    ("normalized_click_error", "metric", "number", "visible_radius", "dynamic_clicking.normalized_click_error", "dynamic_clicking.normalized_click_error.v1", "comparison_only"),
    ("miss_vector_x", "metric", "number", "px", None, None, "descriptive_only"),
    ("miss_vector_y", "metric", "number", "px", None, None, "descriptive_only"),
    ("target_speed", "condition", "number", "px_per_ms", None, None, "descriptive_only"),
    ("target_acceleration", "condition", "number", "px_per_ms2", None, None, "descriptive_only"),
    ("relative_velocity_x", "metric", "number", "px_per_ms", None, None, "comparison_only"),
    ("relative_velocity_y", "metric", "number", "px_per_ms", None, None, "comparison_only"),
    ("relative_velocity_magnitude", "metric", "number", "px_per_ms", "dynamic_clicking.relative_velocity", "dynamic_clicking.relative_velocity.v1", "target_band"),
    ("signed_lead_lag", "metric", "number", "visible_radius", None, None, "descriptive_only"),
    ("lead_lag_descriptor", "condition", "string", None, None, None, "descriptive_only"),
    ("acquisition_start_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("acquisition_time_ms", "timing", "number", "ms", "dynamic_clicking.acquisition_time_ms", "dynamic_clicking.acquisition_time_ms.v1", "comparison_only"),
    ("change_state", "condition", "string", None, None, None, "descriptive_only"),
    ("target_motion_class", "condition", "string", None, None, None, "descriptive_only"),
    ("association_availability", "quality", "string", None, None, None, "descriptive_only"),
    ("outcome_available", "quality", "boolean", None, None, None, "descriptive_only"),
    ("outcome_success", "outcome", "number", "ratio", "dynamic_clicking.target_state_accuracy", "dynamic_clicking.target_state_accuracy.v1", "higher_better"),
    ("condition_ref", "condition", "ref", None, None, None, "descriptive_only"),
)

_TRACKING_COMMON_PROCESSED_EVENT_FIELDS_V1 = (
    ("event_id", "identity", "ref", None, None, None, "descriptive_only"),
    ("start_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("end_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("confidence", "quality", "number", "ratio", None, None, "descriptive_only"),
    ("limitations", "quality", "string_list", None, None, None, "descriptive_only"),
    ("target_track_ref", "identity", "ref", None, None, None, "descriptive_only"),
)

_TRACKING_PROCESSED_EVENT_FIELDS_V1 = {
    "tracking_episode": (
        ("condition_ref", "condition", "ref", None, None, None, "descriptive_only"),
        ("sample_count", "quality", "number", "count", None, None, "descriptive_only"),
        ("usable_sample_count", "quality", "number", "count", None, None, "descriptive_only"),
        ("target_relative_error_px", "metric", "number", "px", "continuous_tracking.target_relative_error_px", "continuous_tracking.target_relative_error_px.v1", "comparison_only"),
        ("time_in_radius_ratio", "metric", "number", "ratio", "continuous_tracking.time_in_radius_ratio", "continuous_tracking.time_in_radius_ratio.v1", "higher_better"),
        ("loss_count", "metric", "number", "count", "continuous_tracking.loss_count", "continuous_tracking.loss_count.v1", "comparison_only"),
        ("correction_burden", "metric", "number", "count", "continuous_tracking.correction_direction_reversal_count", "continuous_tracking.correction_direction_reversal_count.v1", "comparison_only"),
        ("sparc", "metric", "number", "dimensionless", "continuous_tracking.sparc", "continuous_tracking.sparc.v1", "comparison_only"),
        ("phase_lag_ms", "metric", "number", "ms", "continuous_tracking.phase_lag_ms", "continuous_tracking.phase_lag_ms.v1", "comparison_only"),
        ("velocity_gain", "metric", "number", "ratio", "continuous_tracking.velocity_gain", "continuous_tracking.velocity_gain.v1", "target_band"),
        ("coherence", "quality", "number", "ratio", "continuous_tracking.coherence", "continuous_tracking.coherence.v1", "descriptive_only"),
    ),
    "tracking_fixed_window": (
        ("condition_ref", "condition", "ref", None, None, None, "descriptive_only"),
        ("sample_count", "quality", "number", "count", None, None, "descriptive_only"),
        ("usable_sample_count", "quality", "number", "count", None, None, "descriptive_only"),
        ("target_relative_error_px", "metric", "number", "px", "continuous_tracking.target_relative_error_px", "continuous_tracking.target_relative_error_px.v1", "comparison_only"),
        ("time_in_radius_ratio", "metric", "number", "ratio", "continuous_tracking.time_in_radius_ratio", "continuous_tracking.time_in_radius_ratio.v1", "higher_better"),
        ("correction_burden", "metric", "number", "count", "continuous_tracking.correction_direction_reversal_count", "continuous_tracking.correction_direction_reversal_count.v1", "comparison_only"),
        ("sparc", "metric", "number", "dimensionless", "continuous_tracking.sparc", "continuous_tracking.sparc.v1", "comparison_only"),
    ),
    "tracking_loss": (
        ("duration_ms", "timing", "number", "ms", "continuous_tracking.loss_duration_ms", "continuous_tracking.loss_duration_ms.v1", "comparison_only"),
    ),
    "tracking_reacquisition": (
        ("loss_ref", "identity", "ref", None, None, None, "descriptive_only"),
        ("reacquisition_latency_ms", "timing", "number", "ms", "continuous_tracking.reacquisition_latency_ms", "continuous_tracking.reacquisition_latency_ms.v1", "comparison_only"),
    ),
    "tracking_change_response": (
        ("change_ref", "identity", "ref", None, None, None, "descriptive_only"),
        ("observed_change_response_ms", "timing", "number", "ms", "continuous_tracking.observed_change_response_ms", "continuous_tracking.observed_change_response_ms.v1", "comparison_only"),
        ("alignment_latency_ms", "quality", "number", "ms", "continuous_tracking.alignment_latency_ms", "continuous_tracking.alignment_latency_ms.v1", "descriptive_only"),
        ("post_change_error_px", "metric", "number", "px", None, None, "comparison_only"),
    ),
}

_TARGET_SWITCHING_PROCESSED_EVENT_FIELDS_V1 = (
    ("event_id", "identity", "ref", None, None, None, "descriptive_only"),
    ("start_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("end_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("confidence", "quality", "number", "ratio", None, None, "descriptive_only"),
    ("limitations", "quality", "string_list", None, None, None, "descriptive_only"),
    ("chain_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("classification", "quality", "string", None, None, None, "descriptive_only"),
    ("previous_outcome_association_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("previous_target_track_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("previous_outcome_time_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("leave_time_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("candidate_count", "condition", "number", "count", None, None, "descriptive_only"),
    ("selection_observation_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("selected_target_track_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("next_target_track_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("acquire_time_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("settle_time_ms", "timing", "number", "ms", None, None, "descriptive_only"),
    ("transition_time_ms", "metric", "number", "ms", "target_switching.transition_time_ms", "target_switching.transition_time_ms.v1", "comparison_only"),
    ("transition_distance_px", "condition", "number", "px", "target_switching.transition_distance_px", "target_switching.transition_distance_px.v1", "comparison_only"),
    ("transition_direction_deg", "condition", "number", "deg", None, None, "descriptive_only"),
    ("transition_path_length_px", "metric", "number", "px", None, None, "comparison_only"),
    ("path_efficiency", "metric", "number", "ratio", "target_switching.path_efficiency", "target_switching.path_efficiency.v1", "comparison_only"),
    ("settle_duration_ms", "metric", "number", "ms", "target_switching.settle_duration_ms", "target_switching.settle_duration_ms.v1", "comparison_only"),
    ("first_shot_event_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("first_shot_latency_ms", "metric", "number", "ms", "target_switching.first_shot_latency_ms", "target_switching.first_shot_latency_ms.v1", "comparison_only"),
    ("first_damage_event_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("first_damage_latency_ms", "metric", "number", "ms", "target_switching.first_damage_latency_ms", "target_switching.first_damage_latency_ms.v1", "comparison_only"),
    ("carry_over_overshoot", "metric", "boolean", None, "target_switching.carry_over_overshoot_ratio", "target_switching.carry_over_overshoot_ratio.v1", "comparison_only"),
    ("carry_over_overshoot_observation_ref", "identity", "ref", None, None, None, "descriptive_only"),
    ("terminal_correction_observed", "metric", "boolean", None, "target_switching.terminal_correction_ratio", "target_switching.terminal_correction_ratio.v1", "comparison_only"),
    ("terminal_correction_observation_ref", "identity", "ref", None, None, None, "descriptive_only"),
)

_STATIC_PROCESSED_EVENT_FIELDS_V1 = (
    ("event_id", "identity", "ref", None, None, None),
    ("start_ms", "timing", "number", "ms", None, None),
    ("end_ms", "timing", "number", "ms", None, None),
    ("confidence", "quality", "number", "ratio", None, None),
    ("limitations", "quality", "string_list", None, None, None),
    ("legacy_event_ref", "identity", "ref", None, None, None),
    ("peak_ms", "timing", "number", "ms", None, None),
    ("settle_end_ms", "timing", "number", "ms", None, None),
    ("quality", "quality", "string", None, None, None),
    ("movement_duration_ms", "metric", "number", "ms", "static_clicking.movement_duration_ms", "native_flicking.v1"),
    ("time_to_peak_ms", "metric", "number", "ms", "static_clicking.time_to_peak_ms", "native_flicking.v1"),
    ("accel_duration_ms", "metric", "number", "ms", "static_clicking.accel_duration_ms", "native_flicking.v1"),
    ("decel_duration_ms", "metric", "number", "ms", "static_clicking.decel_duration_ms", "native_flicking.v1"),
    ("settle_duration_ms", "metric", "number", "ms", "static_clicking.settle_duration_ms", "native_flicking.v1"),
    ("decel_frac", "metric", "number", "dimensionless", "static_clicking.decel_frac", "native_flicking.v1"),
    ("peak_position_pct", "metric", "number", "percent", "static_clicking.peak_position_pct", "native_flicking.v1"),
    ("peak_speed", "metric", "number", "raw_counts_per_second", "static_clicking.peak_speed", "native_flicking.v1"),
    ("path_length", "metric", "number", "raw_counts", "static_clicking.flick_path_length", "native_flicking.v1"),
    ("displacement", "metric", "number", "raw_counts", "static_clicking.displacement", "native_flicking.v1"),
    ("path_efficiency", "metric", "number", "dimensionless", "static_clicking.path_efficiency", "native_flicking.v1"),
    ("straightness", "metric", "number", "dimensionless", "static_clicking.straightness", "native_flicking.v1"),
    ("reverse_ratio", "metric", "number", "dimensionless", "static_clicking.reverse_ratio", "native_flicking.v1"),
    ("direction_reverse_ratio", "metric", "number", "dimensionless", "static_clicking.direction_reverse_ratio", "native_flicking.v1"),
    ("corrective_count", "metric", "number", "count", "static_clicking.corrective_count", "native_flicking.v1"),
    ("submovement_count", "metric", "number", "count", "static_clicking.submovement_count", "native_flicking.v1"),
    ("trough_depth_ratio", "metric", "number", "dimensionless", "static_clicking.trough_depth_ratio", "native_flicking.v1"),
    ("submovement_overlap", "metric", "number", "dimensionless", "static_clicking.submovement_overlap", "native_flicking.v1"),
    ("sparc", "metric", "number", "dimensionless", "static_clicking.sparc", "native_flicking.sparc.v2"),
)

_STATIC_PROCESSED_INDEX_FIELDS_V1 = (
    "movement_duration_ms", "peak_speed", "path_efficiency", "reverse_ratio",
    "corrective_count", "submovement_count", "sparc", "quality",
)


class UnsupportedEvidenceContractVersion(ValueError):
    """Raised when an analysis evidence contract cannot be safely consumed."""


class EvidenceKeyRegistry:
    """Versioned extension registry used by family analyzers."""

    _CORE_CHANNELS = {
        "mouse.delta_x", "mouse.delta_y", "mouse.position_x", "mouse.position_y",
        "mouse.speed", "mouse.acceleration", "crosshair.position_x",
        "crosshair.position_y", "crosshair.velocity_x", "crosshair.velocity_y",
        "aim_error.x", "aim_error.y", "aim_error.radial", "aim_error.normalized_radius",
        "target_relative.crosshair_velocity_projection", "outcome.score_rate",
        "outcome.damage_rate",
    }
    _CORE_EVENTS = {
        "shot", "hit", "miss", "kill", "movement_start", "movement_end",
        "low_confidence", "target_available", "acquire", "settle", "click_anchor",
        "tracking_episode", "off_target_start", "off_target_end", "reacquired",
        "target_change_point", "leave_previous", "candidate_visible", "target_selected",
        "transition", "next_target_acquired", "first_damage",
        "unclassified_discrete_acquisition",
    }
    _CORE_METRICS = {
        "outcome.score_rate", "outcome.damage_rate",
    }
    _CORE_SEGMENTS = {
        "typical", "worst", "improved", "comparison", "low_confidence",
    }

    def __init__(self) -> None:
        self.channels = set(self._CORE_CHANNELS)
        self.events = set(self._CORE_EVENTS)
        self.event_attributes: dict[str, set[str]] = {
            event: set() for event in self.events
        }
        self.event_attributes["kill"].update(
            {
                "kill_index", "bot_name", "weapon_name", "ttk_s", "shots", "hits",
                "accuracy", "damage_done", "damage_possible", "efficiency", "overshots",
            }
        )
        self.event_attributes["target_change_point"].add("change_kind")
        self.metrics = set(self._CORE_METRICS)
        self.segment_kinds = set(self._CORE_SEGMENTS)
        self.extensions: dict[str, dict] = {}
        self.register_extension(copy.deepcopy(_NATIVE_STATIC_EVIDENCE_EXTENSION_V1))
        self.register_extension(copy.deepcopy(_DYNAMIC_CLICKING_EVIDENCE_EXTENSION_V1))
        self.register_extension(copy.deepcopy(_CONTINUOUS_TRACKING_EVIDENCE_EXTENSION_V1))
        self.register_extension(copy.deepcopy(_TARGET_SWITCHING_EVIDENCE_EXTENSION_V1))

    def register_extension(self, extension: dict) -> None:
        _expect_exact(
            extension,
            {"schema_version", "extension_ref", "channel_keys", "event_kinds", "metric_keys", "segment_kinds"},
            "extension",
        )
        if extension.get("schema_version") != "analysis_evidence_extension.v1":
            raise UnsupportedEvidenceContractVersion(extension.get("schema_version"))
        extension_ref = _safe_ref("extension.extension_ref", extension.get("extension_ref"))
        if not re.fullmatch(r"[a-z][a-z0-9._-]*@[1-9][0-9]*", extension_ref):
            raise ValueError("evidence extension ref must be versioned")
        if extension_ref in self.extensions:
            raise ValueError("duplicate evidence extension")
        channels = _safe_string_list("extension.channel_keys", extension.get("channel_keys"))
        metrics = _safe_string_list("extension.metric_keys", extension.get("metric_keys"))
        segments = _safe_string_list("extension.segment_kinds", extension.get("segment_kinds"))
        events = extension.get("event_kinds")
        if not isinstance(events, dict) or len(events) > 64:
            raise ValueError("extension.event_kinds must be a dict")
        normalized_events: dict[str, list[str]] = {}
        for event, attributes in events.items():
            event_name = _safe_token("extension.event_kind", event)
            normalized_events[event_name] = _safe_string_list(
                f"extension.event_kinds.{event_name}", attributes,
            )
        all_keys = self.channels | self.metrics | self.segment_kinds | self.events
        requested_non_events = set(channels) | set(metrics) | set(segments)
        new_events = set(normalized_events) - self.events
        if all_keys.intersection(requested_non_events) or (
            (self.channels | self.metrics | self.segment_kinds).intersection(new_events)
        ):
            raise ValueError("evidence extension key collides with an existing key")
        for event_name, attributes in normalized_events.items():
            existing_attributes = self.event_attributes.get(event_name, set())
            if existing_attributes.intersection(attributes):
                raise ValueError("evidence extension event attribute already exists")
        self.channels.update(channels)
        self.metrics.update(metrics)
        self.segment_kinds.update(segments)
        self.events.update(normalized_events)
        for event_name, attributes in normalized_events.items():
            self.event_attributes.setdefault(event_name, set()).update(attributes)
        self.extensions[extension_ref] = copy.deepcopy(extension)

    def allows_channel(self, key: str) -> bool:
        return key in self.channels or bool(_CHANNEL_RE.fullmatch(key))

    def allows_event(self, key: str) -> bool:
        return key in self.events

    def allows_metric(self, key: str) -> bool:
        return key in self.metrics

    def allows_outcome_metric(self, key: str) -> bool:
        source_metrics = {
            f"performance.{field['source_key']}"
            for field in source_field_registry_v1()["fields"]
            if field["source_group"] == "performance.metric_change"
        }
        source_metrics.update(
            f"stats.kill.{field['canonical_key']}"
            for field in source_field_registry_v1()["fields"]
            if field["source_group"] == "stats.kill_row"
            and field["source_key"] != "Timestamp"
        )
        return key in source_metrics

    def allows_segment(self, key: str) -> bool:
        return key in self.segment_kinds


def _field(
    source_group: str,
    source_key: str,
    canonical_key: str,
    value_type: str,
    unit: str,
    projection_policy: str,
    presence_policy: str = "optional",
    value_semantics: str | None = None,
) -> dict:
    item = {
        "field_key": f"{source_group}.{source_key}",
        "source_group": source_group,
        "source_key": source_key,
        "canonical_key": canonical_key,
        "value_type": value_type,
        "unit": unit,
        "projection_policy": projection_policy,
        "presence_policy": presence_policy,
    }
    if value_semantics is not None:
        item["value_semantics"] = value_semantics
    return item


def source_field_registry_v1() -> dict:
    """Return the immutable allow-list for the current parser contracts."""
    summary = [
        ("Kills", "outcome_totals.kills", "int", "count"),
        ("Deaths", "outcome_totals.deaths", "int", "count"),
        ("Fight Time", "outcome_totals.fight_time_s", "float", "seconds"),
        ("Time Remaining", "outcome_totals.time_remaining_s", "float", "seconds"),
        ("Avg TTK", "outcome_totals.avg_ttk_s", "float", "seconds"),
        ("Damage Done", "outcome_totals.damage_done", "float", "source_damage_unit"),
        ("Total Overshots", "outcome_totals.overshots", "int", "count"),
        ("Damage Taken", "outcome_totals.damage_taken", "float", "source_damage_unit"),
        ("Hit Count", "outcome_totals.hits", "int", "count"),
        ("Miss Count", "outcome_totals.misses", "int", "count"),
        ("Midairs", "outcome_totals.midairs", "int", "count"),
        ("Midaired", "outcome_totals.midaired", "int", "count"),
        ("Directs", "outcome_totals.directs", "int", "count"),
        ("Directed", "outcome_totals.directed", "int", "count"),
        ("Reloads", "outcome_totals.reloads", "int", "count"),
        ("Distance Traveled", "outcome_totals.distance_traveled", "float", "source_native"),
        ("MBS Points", "outcome_totals.mbs_points", "float", "points"),
        ("Score", "outcome_totals.score", "float", "points"),
        ("Scenario", "scenario.stats_display_name", "string", "untrusted_text"),
        ("Hash", "scenario.stats_scenario_hash", "string", "scenario_hash"),
        ("Game Version", "scenario.game_version", "string", "untrusted_text"),
        ("Challenge Start", "source_quality.stats_start_time_of_day_ms", "int", "milliseconds_since_local_midnight"),
        ("Pause Count", "outcome_totals.pause_count", "int", "count"),
        ("Pause Duration", "outcome_totals.pause_duration_s", "float", "seconds"),
        ("Avg Target Scale", "challenge_configuration.avg_target_scale", "float", "source_scale"),
        ("Avg Time Dilation", "challenge_configuration.avg_time_dilation", "float", "source_scale"),
    ]
    config = [
        ("Input Lag", "input_and_calibration.input_lag", "float", "source_native_unknown_unit"),
        ("Max FPS (config)", "input_and_calibration.max_fps", "float", "fps"),
        ("Sens Scale", "input_and_calibration.sensitivity_scale", "string", "untrusted_text"),
        ("Sens Increment", "input_and_calibration.sensitivity_increment", "float", "source_scale"),
        ("Horiz Sens", "input_and_calibration.horizontal_sensitivity", "float", "source_scale"),
        ("Vert Sens", "input_and_calibration.vertical_sensitivity", "float", "source_scale"),
        ("DPI", "input_and_calibration.dpi", "int", "counts_per_inch"),
        ("FOV", "input_and_calibration.fov_source_value", "float", "source_scale"),
        ("FOVScale", "input_and_calibration.fov_scale", "string", "untrusted_text"),
        ("Hide Gun", "challenge_configuration.hide_gun", "bool", "boolean"),
        ("Crosshair", "challenge_configuration.crosshair_asset_configured", "bool", "presence_only"),
        ("Crosshair Scale", "challenge_configuration.crosshair_scale", "float", "source_scale"),
        ("Crosshair Color", "challenge_configuration.crosshair_color_rgba", "string", "rgba_hex"),
        ("Resolution", "input_and_calibration.resolution_width/height", "resolution", "pixels"),
        ("Avg FPS", "input_and_calibration.avg_fps", "float", "fps"),
        ("Resolution Scale", "input_and_calibration.resolution_scale_pct", "float", "percent"),
    ]
    kill = [
        ("Kill #", "kill_index", "int", "count"),
        ("Timestamp", "source_time_of_day_ms + mapped canonical_time_ms", "timestamp", "milliseconds"),
        ("Bot", "bot_name", "string", "untrusted_text"),
        ("Weapon", "weapon_name", "string", "untrusted_text"),
        ("TTK", "ttk_s", "float", "seconds"),
        ("Shots", "shots", "int", "count"),
        ("Hits", "hits", "int", "count"),
        ("Accuracy", "accuracy", "float", "ratio"),
        ("Damage Done", "damage_done", "float", "source_damage_unit"),
        ("Damage Possible", "damage_possible", "float", "source_damage_unit"),
        ("Efficiency", "efficiency", "float", "ratio"),
        ("Cheated", "cheated", "bool", "boolean"),
        ("OverShots", "overshots", "int", "count"),
    ]
    weapon = [
        ("Weapon", "weapon_name", "string", "untrusted_text"),
        ("Shots", "shots", "int", "count"),
        ("Hits", "hits", "int", "count"),
        ("Damage Done", "damage_done", "float", "source_damage_unit"),
        ("Damage Possible", "damage_possible", "float", "source_damage_unit"),
    ]
    header = [
        ("scenario_name", "scenario.performance_display_name", "string", "untrusted_text"),
        ("scenario_hash", "scenario.performance_scenario_hash", "string", "scenario_hash"),
        ("challenge_start_utc", "source_quality.performance_start_utc_ms", "int", "unix_epoch_milliseconds"),
        ("schema_version", "source_quality.performance_schema_version", "int", "schema_version"),
    ]
    profile = [
        ("time_limit", "challenge_configuration.time_limit_s", "float", "seconds"),
        ("player_profile", "challenge_configuration.player_profile", "string", "untrusted_text"),
        ("added_bots", "challenge_configuration.added_bots", "string_list", "untrusted_text"),
        ("player_max_lives", "challenge_configuration.player_max_lives", "int", "count"),
        ("bot_max_lives", "challenge_configuration.bot_max_lives", "int_list", "count"),
        ("player_team", "challenge_configuration.player_team", "int", "source_team_id"),
        ("bot_teams", "challenge_configuration.bot_teams", "int_list", "source_team_id"),
        ("map_name", "challenge_configuration.map_name", "string", "untrusted_text"),
        ("map_scale", "challenge_configuration.map_scale", "float", "source_scale"),
        ("timescale", "challenge_configuration.timescale", "float", "source_scale"),
        ("end_challenge_after_kills", "challenge_configuration.end_after_kills", "float", "source_condition"),
        ("end_challenge_after_damage", "challenge_configuration.end_after_damage", "float", "source_damage_unit"),
    ]
    payload = [
        (name, name, "int", "count", "count_increment")
        for name in ("shotsFired", "shotsHit", "shotsMissed", "kills", "deaths", "overshots", "reloads", "pauseCount")
    ] + [
        (name, name, "float", "source_native", "delta")
        for name in ("damageDone", "damagePossible", "score", "playerDamageTaken", "distanceTraveled", "mbsPoints")
    ] + [
        (name, name, "float", "source_native", "instantaneous")
        for name in ("targetSize", "targetSpeed", "randomSensScale")
    ]
    fields: list[dict] = []
    for source_key, canonical, value_type, unit in summary:
        fields.append(_field("stats.summary", source_key, canonical, value_type, unit, "allowlisted_fact"))
    for source_key, canonical, value_type, unit in config:
        fields.append(_field("stats.config", source_key, canonical, value_type, unit, "allowlisted_fact"))
    for source_key, canonical, value_type, unit in kill:
        fields.append(_field("stats.kill_row", source_key, canonical, value_type, unit, "normalized_record", "record_required"))
    for source_key, canonical, value_type, unit in weapon:
        fields.append(_field("stats.weapon_aggregate", source_key, canonical, value_type, unit, "normalized_record", "record_required"))
    for source_key, canonical, value_type, unit in header:
        fields.append(_field("performance.header", source_key, canonical, value_type, unit, "allowlisted_fact", "header_required"))
    for source_key, canonical, value_type, unit in profile:
        fields.append(_field("performance.profile", source_key, canonical, value_type, unit, "allowlisted_fact"))
    for source_key, canonical, value_type, unit, semantics in payload:
        fields.append(_field("performance.metric_change", source_key, canonical, value_type, unit, "normalized_record", "oneof_payload", semantics))
    return {"schema_version": FIELD_REGISTRY_VERSION, "fields": copy.deepcopy(fields)}


def validate_source_field_registry_v1(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("source field registry must be a dict")
    if value.get("schema_version") != FIELD_REGISTRY_VERSION:
        raise UnsupportedEvidenceContractVersion(value.get("schema_version"))
    golden = source_field_registry_v1()
    if value != golden:
        raise ValueError("source field registry does not match v1 golden contract")
    return copy.deepcopy(value)


def _expect_exact(value: object, fields: set[str], name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a dict")
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown or missing:
        raise ValueError(f"{name} fields are invalid")


def _safe_token(field: str, value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_STRING:
        raise ValueError(f"{field} must be a bounded string")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} contains control characters")
    if _PATH_OR_URL_RE.search(value) or _SECRET_RE.search(value):
        raise ValueError(f"{field} contains an unsafe path/url/secret sentinel")
    return value


def _safe_ref(field: str, value: object) -> str:
    if not isinstance(value, str) or not _REF_RE.fullmatch(value):
        raise ValueError(f"{field} must be a stable ref")
    return value


def _safe_string_list(field: str, value: object, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or len(value) > _MAX_LIST or (not allow_empty and not value):
        raise ValueError(f"{field} must be a bounded list")
    out = [_safe_token(f"{field}[{index}]", item) for index, item in enumerate(value)]
    if len(set(out)) != len(out):
        raise ValueError(f"{field} must not contain duplicates")
    return out


def _finite_number(field: str, value: object, *, integer: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    if not math.isfinite(float(value)):
        raise ValueError(f"{field} must be finite")
    if integer and not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _ratio(field: str, value: object) -> float:
    number = float(_finite_number(field, value))
    if not 0 <= number <= 1:
        raise ValueError(f"{field} must be between 0 and 1")
    return number


def _stable_refs(field: str, value: object, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or len(value) > _MAX_LIST or (not allow_empty and not value):
        raise ValueError(f"{field} must be a list")
    out = [_safe_ref(f"{field}[{index}]", item) for index, item in enumerate(value)]
    if len(set(out)) != len(out):
        raise ValueError(f"{field} must not contain duplicates")
    return out


def _assert_safe_json(value: object, *, path: str = "$", allow_samples: bool = False) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            compact = key_text.casefold().replace("_", "")
            if not allow_samples and (compact.endswith("path") or compact.endswith("paths") or compact in {"raw", "payload", "frame", "video"}):
                raise ValueError(f"unsafe private field at {path}.{key_text}")
            _assert_safe_json(child, path=f"{path}.{key_text}", allow_samples=allow_samples)
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_safe_json(child, path=f"{path}[{index}]", allow_samples=allow_samples)
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite value at {path}")
    if isinstance(value, str):
        if len(value) > _MAX_STRING or any(ord(char) < 32 for char in value):
            raise ValueError(f"unsafe string at {path}")
        if not allow_samples and (_PATH_OR_URL_RE.search(value) or _SECRET_RE.search(value)):
            raise ValueError(f"unsafe string at {path}")


def _window_bounds(window: dict) -> tuple[int, int]:
    if not isinstance(window, dict):
        raise ValueError("canonical_time_window must be a dict")
    required_window_fields = {
        "schema_version", "start_ms", "end_ms", "duration_ms", "window_semantics",
        "timebase_version", "start_source", "end_source", "warnings",
    }
    if not required_window_fields <= set(window):
        raise ValueError("canonical_time_window is missing required fields")
    _assert_safe_json(window)
    if window.get("schema_version") != "canonical_time_window.v1":
        raise UnsupportedEvidenceContractVersion(window.get("schema_version"))
    start = _finite_number("canonical_time_window.start_ms", window.get("start_ms"), integer=True)
    end = _finite_number("canonical_time_window.end_ms", window.get("end_ms"), integer=True)
    duration = _finite_number("canonical_time_window.duration_ms", window.get("duration_ms"), integer=True)
    if start < 0 or end <= start or duration != end - start or window.get("window_semantics") != "half_open":
        raise ValueError("canonical_time_window has an invalid range")
    _safe_token("canonical_time_window.timebase_version", window.get("timebase_version"))
    _safe_token("canonical_time_window.start_source", window.get("start_source"))
    _safe_token("canonical_time_window.end_source", window.get("end_source"))
    _safe_string_list("canonical_time_window.warnings", window.get("warnings"))
    return int(start), int(end)


def _validate_channel_key(key: object, registry: EvidenceKeyRegistry, field: str) -> str:
    if not isinstance(key, str) or not key or len(key) > _MAX_STRING:
        raise ValueError(f"{field} is invalid")
    if not registry.allows_channel(key):
        raise ValueError(f"{field} is not registered")
    return key


def validate_signal_bundle_v1(value: object, *, registry: EvidenceKeyRegistry | None = None) -> dict:
    registry = registry or EvidenceKeyRegistry()
    _expect_exact(value, {"schema_version", "analysis_ref", "canonical_time_window_ref", "visual_quality_profile_ref", "observed_visual_domain", "channels"}, "signal_bundle")
    if value.get("schema_version") != "signal_bundle.v1":
        raise UnsupportedEvidenceContractVersion(value.get("schema_version"))
    _safe_ref("signal_bundle.analysis_ref", value.get("analysis_ref"))
    _safe_ref("signal_bundle.canonical_time_window_ref", value.get("canonical_time_window_ref"))
    for field in ("visual_quality_profile_ref",):
        if value[field] is not None:
            _safe_ref(f"signal_bundle.{field}", value[field])
    if value["observed_visual_domain"] is not None:
        if not isinstance(value["observed_visual_domain"], dict):
            raise ValueError("signal_bundle.observed_visual_domain must be a dict or null")
        _assert_safe_json(value["observed_visual_domain"])
    channels = value.get("channels")
    if not isinstance(channels, list) or not channels:
        raise ValueError("signal_bundle.channels must be non-empty")
    channel_keys: set[str] = set()
    sample_refs: set[str] = set()
    for index, channel in enumerate(channels):
        _expect_exact(channel, {"channel_key", "source_refs", "coordinate_space", "unit", "sample_rate_semantics", "samples_ref", "coverage", "confidence_summary", "transform_version", "limitations"}, f"signal_bundle.channels[{index}]")
        channel_key = _validate_channel_key(channel["channel_key"], registry, f"signal_bundle.channels[{index}].channel_key")
        samples_ref = _safe_ref(
            f"signal_bundle.channels[{index}].samples_ref",
            channel["samples_ref"],
        )
        if channel_key in channel_keys or samples_ref in sample_refs:
            raise ValueError("signal bundle channels must be unique")
        channel_keys.add(channel_key)
        sample_refs.add(samples_ref)
        _stable_refs(f"signal_bundle.channels[{index}].source_refs", channel["source_refs"], allow_empty=False)
        for field in ("coordinate_space", "unit", "sample_rate_semantics", "transform_version"):
            _safe_token(f"signal_bundle.channels[{index}].{field}", channel[field])
        _ratio(f"signal_bundle.channels[{index}].coverage", channel["coverage"])
        _ratio(f"signal_bundle.channels[{index}].confidence_summary", channel["confidence_summary"])
        _safe_string_list(f"signal_bundle.channels[{index}].limitations", channel["limitations"])
    _assert_safe_json(value)
    return copy.deepcopy(value)


def validate_event_bundle_v1(value: object, *, registry: EvidenceKeyRegistry | None = None) -> dict:
    registry = registry or EvidenceKeyRegistry()
    _expect_exact(value, {"schema_version", "analysis_ref", "events", "outcome_associations"}, "event_bundle")
    if value.get("schema_version") != "event_bundle.v1":
        raise UnsupportedEvidenceContractVersion(value.get("schema_version"))
    analysis_ref = _safe_ref("event_bundle.analysis_ref", value.get("analysis_ref"))
    events = value.get("events")
    if not isinstance(events, list) or len(events) > _MAX_LIST:
        raise ValueError("event_bundle.events must be a bounded list")
    event_kinds: dict[str, str] = {}
    for index, event in enumerate(events):
        _expect_exact(event, {"event_id", "event_kind", "start_ms", "end_ms", "actor_refs", "source_refs", "confidence", "attributes", "limitations"}, f"event_bundle.events[{index}]")
        event_id = _safe_ref(
            f"event_bundle.events[{index}].event_id", event["event_id"],
        )
        if event_id in event_kinds:
            raise ValueError("event bundle event ids must be unique")
        kind = event["event_kind"]
        if not registry.allows_event(kind):
            raise ValueError(f"event_bundle.events[{index}].event_kind is not registered")
        event_kinds[event_id] = kind
        start = _finite_number(f"event_bundle.events[{index}].start_ms", event["start_ms"], integer=True)
        end = _finite_number(f"event_bundle.events[{index}].end_ms", event["end_ms"], integer=True)
        if start < 0 or end < start:
            raise ValueError("event interval is invalid")
        _stable_refs(f"event_bundle.events[{index}].actor_refs", event["actor_refs"])
        _stable_refs(f"event_bundle.events[{index}].source_refs", event["source_refs"], allow_empty=False)
        _ratio(f"event_bundle.events[{index}].confidence", event["confidence"])
        attributes = event["attributes"]
        if not isinstance(attributes, dict):
            raise ValueError("event attributes must be a dict")
        allowed = registry.event_attributes.get(kind, set())
        if set(attributes) - allowed:
            raise ValueError("event attributes contain unregistered fields")
        if kind == "kill":
            for field in ("kill_index", "shots", "hits", "overshots"):
                if field in attributes:
                    number = attributes[field]
                    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                        raise ValueError("kill event integer attribute is invalid")
            for field in ("ttk_s", "damage_done", "damage_possible"):
                if field in attributes:
                    _finite_number(f"kill.attributes.{field}", attributes[field])
            for field in ("accuracy", "efficiency"):
                if field in attributes:
                    _ratio(f"kill.attributes.{field}", attributes[field])
            for field in ("bot_name", "weapon_name"):
                if field in attributes:
                    _safe_token(f"kill.attributes.{field}", attributes[field])
        elif kind == "motion_predictability_evidence":
            tracking_shape = attributes.get("schema_version") == "motion_predictability_evidence.v1"
            expected_fields = {
                "segment_ref", "model_ref", "model_version", "fit_metric",
                "fit_value", "threshold_ref", "acceptance",
            }
            if tracking_shape:
                expected_fields.update({
                    "schema_version", "kind", "fit_metric_version", "availability",
                })
            _expect_exact(attributes, expected_fields, "motion_predictability_evidence.attributes")
            _safe_ref("motion_predictability_evidence.segment_ref", attributes["segment_ref"])
            _safe_ref("motion_predictability_evidence.model_ref", attributes["model_ref"])
            _safe_token("motion_predictability_evidence.model_version", attributes["model_version"])
            _safe_token("motion_predictability_evidence.fit_metric", attributes["fit_metric"])
            _finite_number("motion_predictability_evidence.fit_value", attributes["fit_value"])
            _safe_ref("motion_predictability_evidence.threshold_ref", attributes["threshold_ref"])
            if attributes["acceptance"] not in {"accepted", "rejected"}:
                raise ValueError("motion predictability acceptance is invalid")
            if tracking_shape:
                if attributes["kind"] not in {
                    "known_script", "periodicity", "repeatability", "model_fit",
                }:
                    raise ValueError("motion predictability kind is invalid")
                _safe_token(
                    "motion_predictability_evidence.fit_metric_version",
                    attributes["fit_metric_version"],
                )
                if attributes["availability"] not in {"available", "unavailable"}:
                    raise ValueError("motion predictability availability is invalid")
        elif any(isinstance(item, (dict, list, tuple)) for item in attributes.values()):
            raise ValueError("extension event attributes must be typed scalars")
        _assert_safe_json(attributes)
        _safe_string_list(f"event_bundle.events[{index}].limitations", event["limitations"])
    associations = value.get("outcome_associations")
    if not isinstance(associations, list) or len(associations) > _MAX_LIST:
        raise ValueError("event_bundle.outcome_associations must be a bounded list")
    association_ids: set[str] = set()
    for index, association in enumerate(associations):
        _expect_exact(association, {"association_id", "shot_event_ref", "outcome_event_ref", "target_track_ref", "weapon_temporal_model", "association_kind", "source_refs", "confidence", "availability", "limitations"}, f"event_bundle.outcome_associations[{index}]")
        association_id = _safe_ref(f"event_bundle.outcome_associations[{index}].association_id", association["association_id"])
        if association_id in association_ids:
            raise ValueError("outcome association ids must be unique")
        association_ids.add(association_id)
        shot_ref = _safe_ref(f"event_bundle.outcome_associations[{index}].shot_event_ref", association["shot_event_ref"])
        if event_kinds.get(shot_ref) != "shot":
            raise ValueError("outcome association shot ref is not reachable")
        for field in ("outcome_event_ref", "target_track_ref"):
            if association[field] is not None:
                _safe_ref(f"event_bundle.outcome_associations[{index}].{field}", association[field])
        target_ref = association["target_track_ref"]
        target_prefix = f"{analysis_ref}:target-track:"
        if target_ref is not None and (
            not target_ref.startswith(target_prefix)
            or not _TARGET_TRACK_SUFFIX_RE.fullmatch(target_ref[len(target_prefix):])
        ):
            raise ValueError("outcome association target track ref is not analysis-bound")
        if (
            association["outcome_event_ref"] is not None
            and association["outcome_event_ref"] not in event_kinds
        ):
            raise ValueError("outcome association event ref is not reachable")
        if association["weapon_temporal_model"] not in {"hitscan", "projectile", "unknown"}:
            raise ValueError("invalid weapon temporal model")
        association_kind = association["association_kind"]
        if association_kind not in {"directly_observed", "validated_aligned", "inferred"}:
            raise ValueError("invalid association kind")
        if association_kind == "validated_aligned":
            raise ValueError(
                "validated aligned outcome association requires a registered rule contract"
            )
        source_refs = _stable_refs(
            f"event_bundle.outcome_associations[{index}].source_refs",
            association["source_refs"],
            allow_empty=False,
        )
        confidence = _ratio(
            f"event_bundle.outcome_associations[{index}].confidence",
            association["confidence"],
        )
        availability = association["availability"]
        if availability not in {"available", "partial", "unavailable"}:
            raise ValueError("invalid association availability")
        limitations = _safe_string_list(
            f"event_bundle.outcome_associations[{index}].limitations",
            association["limitations"],
        )
        outcome_ref = association["outcome_event_ref"]
        target_ref = association["target_track_ref"]
        if outcome_ref is not None and event_kinds.get(outcome_ref) not in {
            "hit", "miss", "kill", "first_damage",
        }:
            raise ValueError("outcome association ref is not an outcome event")
        if availability == "available":
            if (
                association_kind != "directly_observed"
                or outcome_ref is None
                or target_ref is None
                or confidence <= 0
                or {
                    "outcome_association_inferred",
                    "outcome_association_unavailable",
                }.intersection(limitations)
            ):
                raise ValueError("available outcome association evidence is incomplete")
        elif availability == "partial":
            if (
                association_kind != "inferred"
                or not 0 < confidence < 1
                or "outcome_association_inferred" not in limitations
                or "outcome_association_unavailable" in limitations
            ):
                raise ValueError("partial outcome association state is inconsistent")
        elif (
            association_kind != "inferred"
            or outcome_ref is not None
            or confidence != 0
            or "outcome_association_unavailable" not in limitations
            or "outcome_association_inferred" in limitations
        ):
            raise ValueError("unavailable outcome association state is inconsistent")
    _assert_safe_json(value)
    return copy.deepcopy(value)


def _canonical_sha256(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _validate_outcome_rule_binding_v1(value: object, index: int) -> dict:
    path = f"event_bundle.outcome_association_rule_bindings[{index}]"
    fields = {
        "schema_version", "rule_ref", "rule_sha256", "scenario_profile_ref",
        "canonical_timebase_version", "raw_click_extractor_version",
        "stats_parser_version", "outcome_semantics", "weapon_temporal_model",
        "stats_predicate", "timing_window_ms", "track_predicate",
        "visual_quality_profile_ref", "fixture_set_ref", "annotation_set_ref",
    }
    _expect_exact(value, fields, path)
    if value["schema_version"] != "outcome_association_rule_binding.v1":
        raise UnsupportedEvidenceContractVersion(value["schema_version"])
    for field in (
        "rule_ref", "scenario_profile_ref", "visual_quality_profile_ref",
        "fixture_set_ref", "annotation_set_ref",
    ):
        _safe_ref(f"{path}.{field}", value[field])
    for field in (
        "canonical_timebase_version", "raw_click_extractor_version",
        "stats_parser_version",
    ):
        _safe_token(f"{path}.{field}", value[field])
    if value["outcome_semantics"] != "one_shot_kill":
        raise ValueError("outcome association rule semantics are unsupported")
    if value["weapon_temporal_model"] != "hitscan":
        raise ValueError("outcome association rule must be hitscan")
    _expect_exact(
        value["stats_predicate"],
        {"shots_equals", "hits_equals", "overshots_equals"},
        f"{path}.stats_predicate",
    )
    if value["stats_predicate"] != {
        "shots_equals": 1, "hits_equals": 1, "overshots_equals": 0,
    }:
        raise ValueError("outcome association rule stats predicate is unsupported")
    _expect_exact(
        value["timing_window_ms"], {"minimum", "maximum"},
        f"{path}.timing_window_ms",
    )
    minimum = _finite_number(
        f"{path}.timing_window_ms.minimum",
        value["timing_window_ms"]["minimum"],
        integer=True,
    )
    maximum = _finite_number(
        f"{path}.timing_window_ms.maximum",
        value["timing_window_ms"]["maximum"],
        integer=True,
    )
    if minimum < 0 or maximum < minimum:
        raise ValueError("outcome association rule timing window is invalid")
    _expect_exact(
        value["track_predicate"],
        {
            "identity_status", "max_sample_gap_ms", "require_inner_hitbox",
            "hitbox_inset_px", "minimum_sample_confidence",
        },
        f"{path}.track_predicate",
    )
    if value["track_predicate"]["identity_status"] != "stable":
        raise ValueError("outcome association rule identity predicate is unsupported")
    max_gap = _finite_number(
        f"{path}.track_predicate.max_sample_gap_ms",
        value["track_predicate"]["max_sample_gap_ms"],
        integer=True,
    )
    inset = _finite_number(
        f"{path}.track_predicate.hitbox_inset_px",
        value["track_predicate"]["hitbox_inset_px"],
    )
    minimum_confidence = _ratio(
        f"{path}.track_predicate.minimum_sample_confidence",
        value["track_predicate"]["minimum_sample_confidence"],
    )
    if minimum_confidence != 1.0:
        raise ValueError(
            "outcome association v1 requires exact sample confidence"
        )
    if (
        max_gap < 0
        or inset < 0
        or value["track_predicate"]["require_inner_hitbox"] is not True
    ):
        raise ValueError("outcome association rule track predicate is invalid")
    digest = value["rule_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("outcome association rule digest is invalid")
    digest_payload = {key: copy.deepcopy(child) for key, child in value.items() if key != "rule_sha256"}
    if _canonical_sha256(digest_payload) != digest:
        raise ValueError("outcome association rule digest mismatch")
    _assert_safe_json(value)
    return copy.deepcopy(value)


def validate_outcome_association_rule_binding_v1(value: object) -> dict:
    return _validate_outcome_rule_binding_v1(value, 0)


def validate_event_bundle_v2(
    value: object, *, registry: EvidenceKeyRegistry | None = None,
) -> dict:
    registry = registry or EvidenceKeyRegistry()
    _expect_exact(
        value,
        {
            "schema_version", "analysis_ref", "events",
            "outcome_association_rule_bindings", "outcome_associations",
        },
        "event_bundle",
    )
    if value.get("schema_version") != "event_bundle.v2":
        raise UnsupportedEvidenceContractVersion(value.get("schema_version"))
    analysis_ref = _safe_ref("event_bundle.analysis_ref", value["analysis_ref"])
    events = value["events"]
    validate_event_bundle_v1({
        "schema_version": "event_bundle.v1",
        "analysis_ref": analysis_ref,
        "events": events,
        "outcome_associations": [],
    }, registry=registry)
    events_by_id = {event["event_id"]: event for event in events}

    bindings = value["outcome_association_rule_bindings"]
    if not isinstance(bindings, list) or len(bindings) > _MAX_LIST:
        raise ValueError("outcome association rule bindings must be a bounded list")
    bindings_by_ref: dict[str, dict] = {}
    for index, raw_binding in enumerate(bindings):
        binding = _validate_outcome_rule_binding_v1(raw_binding, index)
        if binding["rule_ref"] in bindings_by_ref:
            raise ValueError("outcome association rule bindings must be unique")
        bindings_by_ref[binding["rule_ref"]] = binding

    associations = value["outcome_associations"]
    if not isinstance(associations, list) or len(associations) > _MAX_LIST:
        raise ValueError("event_bundle.outcome_associations must be a bounded list")
    association_ids: set[str] = set()
    used_rule_refs: set[str] = set()
    validated_shot_refs: set[str] = set()
    validated_outcome_refs: set[str] = set()
    for index, association in enumerate(associations):
        path = f"event_bundle.outcome_associations[{index}]"
        _expect_exact(
            association,
            {
                "association_id", "shot_event_ref", "outcome_event_ref",
                "target_track_ref", "weapon_temporal_model", "association_kind",
                "source_refs", "validation", "confidence", "availability",
                "limitations",
            },
            path,
        )
        association_id = _safe_ref(f"{path}.association_id", association["association_id"])
        if association_id in association_ids:
            raise ValueError("outcome association ids must be unique")
        association_ids.add(association_id)
        if association["association_kind"] != "validated_aligned":
            if association["validation"] is not None:
                raise ValueError("non-validated outcome association cannot contain validation")
            legacy = {key: copy.deepcopy(child) for key, child in association.items() if key != "validation"}
            validate_event_bundle_v1({
                "schema_version": "event_bundle.v1",
                "analysis_ref": analysis_ref,
                "events": events,
                "outcome_associations": [legacy],
            }, registry=registry)
            continue

        shot_ref = _safe_ref(f"{path}.shot_event_ref", association["shot_event_ref"])
        outcome_ref = _safe_ref(f"{path}.outcome_event_ref", association["outcome_event_ref"])
        target_ref = _safe_ref(f"{path}.target_track_ref", association["target_track_ref"])
        if events_by_id.get(shot_ref, {}).get("event_kind") != "shot":
            raise ValueError("validated outcome association shot ref is not reachable")
        if events_by_id.get(outcome_ref, {}).get("event_kind") != "kill":
            raise ValueError("validated outcome association must reference a Stats kill")
        if shot_ref in validated_shot_refs or outcome_ref in validated_outcome_refs:
            raise ValueError("validated outcome associations must be one-to-one")
        validated_shot_refs.add(shot_ref)
        validated_outcome_refs.add(outcome_ref)
        target_prefix = f"{analysis_ref}:target-track:"
        if not target_ref.startswith(target_prefix) or not _TARGET_TRACK_SUFFIX_RE.fullmatch(
            target_ref[len(target_prefix):]
        ):
            raise ValueError("validated outcome association target ref is not analysis-bound")
        if association["weapon_temporal_model"] != "hitscan":
            raise ValueError("validated outcome association must be hitscan")
        source_refs = _stable_refs(f"{path}.source_refs", association["source_refs"], allow_empty=False)
        if (
            association["availability"] != "available"
            or _ratio(f"{path}.confidence", association["confidence"]) != 1.0
            or _safe_string_list(f"{path}.limitations", association["limitations"])
        ):
            raise ValueError("validated outcome association state is incomplete")

        validation = association["validation"]
        _expect_exact(
            validation,
            {
                "schema_version", "rule_ref", "rule_sha256",
                "scenario_profile_ref", "canonical_time_window_ref",
                "raw_input_source_ref", "stats_source_ref", "visual_source_ref",
                "visual_quality_profile_ref", "click_time_ms", "outcome_time_ms",
                "click_to_outcome_ms", "temporal_candidate_count",
                "geometric_candidate_count", "stats_kill", "track_check",
            },
            f"{path}.validation",
        )
        if validation["schema_version"] != "outcome_association_validation.v1":
            raise UnsupportedEvidenceContractVersion(validation["schema_version"])
        rule_ref = _safe_ref(f"{path}.validation.rule_ref", validation["rule_ref"])
        binding = bindings_by_ref.get(rule_ref)
        if binding is None or validation["rule_sha256"] != binding["rule_sha256"]:
            raise ValueError("validated outcome association rule binding is unavailable")
        used_rule_refs.add(rule_ref)
        if validation["scenario_profile_ref"] != binding["scenario_profile_ref"]:
            raise ValueError("validated outcome association scenario profile mismatch")
        if validation["visual_quality_profile_ref"] != binding["visual_quality_profile_ref"]:
            raise ValueError("validated outcome association visual quality profile mismatch")
        for field in (
            "scenario_profile_ref", "canonical_time_window_ref", "raw_input_source_ref",
            "stats_source_ref", "visual_source_ref", "visual_quality_profile_ref",
        ):
            _safe_ref(f"{path}.validation.{field}", validation[field])
        if validation["canonical_time_window_ref"] != f"{analysis_ref}:canonical-window":
            raise ValueError("validated outcome association canonical window mismatch")
        required_sources = {
            validation["raw_input_source_ref"], validation["stats_source_ref"],
            validation["visual_source_ref"],
        }
        if not required_sources <= set(source_refs):
            raise ValueError("validated outcome association source refs are incomplete")
        if validation["raw_input_source_ref"] not in events_by_id[shot_ref]["source_refs"]:
            raise ValueError("validated outcome association Raw source is unreachable")
        if validation["stats_source_ref"] not in events_by_id[outcome_ref]["source_refs"]:
            raise ValueError("validated outcome association Stats source is unreachable")

        click_time = _finite_number(f"{path}.validation.click_time_ms", validation["click_time_ms"], integer=True)
        outcome_time = _finite_number(f"{path}.validation.outcome_time_ms", validation["outcome_time_ms"], integer=True)
        latency = _finite_number(f"{path}.validation.click_to_outcome_ms", validation["click_to_outcome_ms"], integer=True)
        timing = binding["timing_window_ms"]
        if (
            click_time != events_by_id[shot_ref]["start_ms"]
            or outcome_time != events_by_id[outcome_ref]["start_ms"]
            or latency != outcome_time - click_time
            or not timing["minimum"] <= latency <= timing["maximum"]
        ):
            raise ValueError("validated outcome association timing is inconsistent")
        temporal_candidates = [
            event
            for event in events
            if event["event_kind"] == "shot"
            and validation["raw_input_source_ref"] in event["source_refs"]
            and timing["minimum"]
            <= outcome_time - event["start_ms"]
            <= timing["maximum"]
        ]
        if (
            validation["temporal_candidate_count"] != 1
            or len(temporal_candidates) != 1
            or temporal_candidates[0]["event_id"] != shot_ref
        ):
            raise ValueError("validated outcome association temporal candidate is not unique")
        if validation["geometric_candidate_count"] != 1:
            raise ValueError("validated outcome association geometric candidate is not unique")

        _expect_exact(
            validation["stats_kill"], {"kill_index", "shots", "hits", "overshots"},
            f"{path}.validation.stats_kill",
        )
        stats_kill = validation["stats_kill"]
        for field in ("kill_index", "shots", "hits", "overshots"):
            _finite_number(f"{path}.validation.stats_kill.{field}", stats_kill[field], integer=True)
        if stats_kill != {
            "kill_index": stats_kill["kill_index"],
            "shots": binding["stats_predicate"]["shots_equals"],
            "hits": binding["stats_predicate"]["hits_equals"],
            "overshots": binding["stats_predicate"]["overshots_equals"],
        }:
            raise ValueError("validated outcome association Stats kill predicate failed")
        kill_attributes = events_by_id[outcome_ref]["attributes"]
        if any(kill_attributes.get(field) != stats_kill[field] for field in stats_kill):
            raise ValueError("validated outcome association Stats kill record mismatch")

        _expect_exact(
            validation["track_check"],
            {
                "identity_status", "sample_gap_ms", "sample_confidence",
                "center_distance_px", "effective_radius_px",
            },
            f"{path}.validation.track_check",
        )
        track_check = validation["track_check"]
        sample_gap = _finite_number(f"{path}.validation.track_check.sample_gap_ms", track_check["sample_gap_ms"], integer=True)
        sample_confidence = _ratio(
            f"{path}.validation.track_check.sample_confidence",
            track_check["sample_confidence"],
        )
        center_distance = _finite_number(f"{path}.validation.track_check.center_distance_px", track_check["center_distance_px"])
        effective_radius = _finite_number(f"{path}.validation.track_check.effective_radius_px", track_check["effective_radius_px"])
        if (
            track_check["identity_status"] != binding["track_predicate"]["identity_status"]
            or sample_gap < 0
            or sample_gap > binding["track_predicate"]["max_sample_gap_ms"]
            or sample_confidence < binding["track_predicate"]["minimum_sample_confidence"]
            or center_distance < 0
            or effective_radius <= 0
            or center_distance > effective_radius
        ):
            raise ValueError("validated outcome association track check failed")
    if used_rule_refs != set(bindings_by_ref):
        raise ValueError("outcome association rule binding is unused")
    _assert_safe_json(value)
    return copy.deepcopy(value)


def validate_event_bundle(
    value: object, *, registry: EvidenceKeyRegistry | None = None,
) -> dict:
    if not isinstance(value, dict):
        raise ValueError("event_bundle must be a dict")
    version = value.get("schema_version")
    if version == "event_bundle.v1":
        return validate_event_bundle_v1(value, registry=registry)
    if version == "event_bundle.v2":
        return validate_event_bundle_v2(value, registry=registry)
    raise UnsupportedEvidenceContractVersion(version)


def _registry_field_map() -> dict[str, dict]:
    return {field["field_key"]: field for field in source_field_registry_v1()["fields"]}


def _registry_section(field: dict) -> str:
    if field["source_group"] == "stats.weapon_aggregate":
        return "weapon_aggregates"
    if field["source_group"] in {"stats.kill_row", "performance.metric_change"}:
        return "outcome_records"
    return field["canonical_key"].split(".", 1)[0]


def _canonical_fact_keys(field: dict) -> set[str]:
    canonical_key = field["canonical_key"]
    if canonical_key == "input_and_calibration.resolution_width/height":
        return {"resolution_width", "resolution_height"}
    if "." not in canonical_key:
        return {canonical_key}
    return {canonical_key.split(".", 1)[1]}


def _validate_canonical_fact_value(field: dict, facts: dict) -> None:
    keys = _canonical_fact_keys(field)
    value_type = field["value_type"]
    if value_type == "resolution":
        for key in keys:
            value = facts[key]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("canonical resolution fact is invalid")
        return
    key = next(iter(keys))
    value = facts[key]
    if value_type == "string":
        _safe_token(f"canonical_fact.{key}", value)
        if field["unit"] == "rgba_hex" and not re.fullmatch(
            r"[0-9A-F]{8}", value,
        ):
            raise ValueError("canonical RGBA fact is invalid")
    elif value_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("canonical integer fact is invalid")
    elif value_type == "float":
        _finite_number(f"canonical_fact.{key}", value)
    elif value_type == "bool":
        if not isinstance(value, bool):
            raise ValueError("canonical boolean fact is invalid")
    elif value_type == "string_list":
        if not isinstance(value, list):
            raise ValueError("canonical string-list fact is invalid")
        for index, item in enumerate(value):
            _safe_token(f"canonical_fact.{key}[{index}]", item)
    elif value_type == "int_list":
        if not isinstance(value, list) or any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in value
        ):
            raise ValueError("canonical integer-list fact is invalid")
    else:
        raise ValueError("canonical fact type is not supported")


def _validate_omitted(field: str, value: object, registry_fields: dict[str, dict]) -> list[dict]:
    if not isinstance(value, list) or len(value) > len(registry_fields):
        raise ValueError(f"{field} must be a list")
    out = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        _expect_exact(item, {"field_key", "reason"}, f"{field}[{index}]")
        key = _safe_token(f"{field}[{index}].field_key", item["field_key"])
        if key not in registry_fields or key in seen:
            raise ValueError(f"{field} references an invalid field")
        seen.add(key)
        _safe_token(f"{field}[{index}].reason", item["reason"])
        out.append(copy.deepcopy(item))
    return out


def validate_canonical_run_facts_v1(value: object) -> dict:
    registry_fields = _registry_field_map()
    _expect_exact(value, {"schema_version", "analysis_ref", "scenario_profile_ref", "canonical_time_window_ref", "field_registry_version", "source_contracts", "sections", "outcome_record_sets", "completeness", "unknown_field_policy", "limitations"}, "canonical_run_facts")
    if value.get("schema_version") != "canonical_run_facts.v1":
        raise UnsupportedEvidenceContractVersion(value.get("schema_version"))
    _safe_ref("canonical_run_facts.analysis_ref", value["analysis_ref"])
    if value["scenario_profile_ref"] is not None:
        _safe_ref("canonical_run_facts.scenario_profile_ref", value["scenario_profile_ref"])
    _safe_ref("canonical_run_facts.canonical_time_window_ref", value["canonical_time_window_ref"])
    if value["field_registry_version"] != FIELD_REGISTRY_VERSION:
        raise UnsupportedEvidenceContractVersion(value["field_registry_version"])
    contracts = value["source_contracts"]
    if not isinstance(contracts, list) or not contracts:
        raise ValueError("canonical_run_facts.source_contracts must be non-empty")
    source_kinds: set[str] = set()
    for index, contract in enumerate(contracts):
        _expect_exact(contract, {"source_kind", "source_ref", "parser_version", "source_schema_version", "recognized_schema_status", "unknown_field_observability"}, f"canonical_run_facts.source_contracts[{index}]")
        source_kind = _safe_token(
            f"source_contracts[{index}].source_kind", contract["source_kind"],
        )
        if source_kind not in {"stats", "performance"} or source_kind in source_kinds:
            raise ValueError("canonical facts source contract is invalid")
        source_kinds.add(source_kind)
        _safe_ref(f"source_contracts[{index}].source_ref", contract["source_ref"])
        _safe_token(f"source_contracts[{index}].parser_version", contract["parser_version"])
        if contract["source_schema_version"] is not None:
            _safe_token(f"source_contracts[{index}].source_schema_version", contract["source_schema_version"])
        if contract["recognized_schema_status"] not in {"recognized", "forward_compatible", "unrecognized", "not_versioned"}:
            raise ValueError("invalid recognized schema status")
        if contract["unknown_field_observability"] not in {"none_detected", "detected", "not_observable"}:
            raise ValueError("invalid unknown-field observability")
    sections = value["sections"]
    if not isinstance(sections, list) or not sections:
        raise ValueError("canonical_run_facts.sections must be non-empty")
    allowed_sections = {
        "outcome_totals", "scenario", "source_quality",
        "challenge_configuration", "input_and_calibration",
        "weapon_aggregates", "outcome_records",
    }
    section_keys = [section.get("section_key") for section in sections if isinstance(section, dict)]
    if (
        len(section_keys) != len(sections)
        or len(set(section_keys)) != len(section_keys)
        or not set(section_keys) <= allowed_sections
    ):
        raise ValueError("canonical facts sections are invalid")
    all_present: set[str] = set()
    all_absent: set[str] = set()
    covered_sections: dict[str, str] = {}
    for index, section in enumerate(sections):
        _expect_exact(section, {"section_key", "facts", "present_field_keys", "source_absent_field_keys", "omitted_known_fields", "completeness"}, f"canonical_run_facts.sections[{index}]")
        _safe_token(f"sections[{index}].section_key", section["section_key"])
        if not isinstance(section["facts"], dict):
            raise ValueError("canonical facts section facts must be a dict")
        _assert_safe_json(section["facts"])
        present = _safe_string_list(f"sections[{index}].present_field_keys", section["present_field_keys"])
        absent = _safe_string_list(f"sections[{index}].source_absent_field_keys", section["source_absent_field_keys"])
        if set(present) - set(registry_fields) or set(absent) - set(registry_fields) or set(present) & set(absent):
            raise ValueError("canonical facts section field coverage is invalid")
        for field_key in [*present, *absent]:
            previous_section = covered_sections.get(field_key)
            if (
                previous_section is not None
                and previous_section != section["section_key"]
            ):
                raise ValueError("canonical facts field appears in multiple sections")
            covered_sections[field_key] = section["section_key"]
        omitted = _validate_omitted(f"sections[{index}].omitted_known_fields", section["omitted_known_fields"], registry_fields)
        omitted_keys = {item["field_key"] for item in omitted}
        if omitted_keys & set(absent):
            raise ValueError("canonical facts omitted fields overlap source-absent fields")
        covered_keys = [*present, *absent, *omitted_keys]
        if any(
            _registry_section(registry_fields[field_key]) != section["section_key"]
            for field_key in covered_keys
        ):
            raise ValueError("canonical facts field is in the wrong section")
        if section["section_key"] == "outcome_records":
            if section["facts"]:
                raise ValueError("outcome record facts must remain in the paged record set")
        elif section["section_key"] == "weapon_aggregates":
            expected_keys = set().union(
                *(
                    _canonical_fact_keys(registry_fields[field_key])
                    for field_key in present
                ),
            ) if present else set()
            if present:
                if set(section["facts"]) != {"records"} or not isinstance(
                    section["facts"]["records"], list,
                ):
                    raise ValueError("weapon aggregate facts must be typed records")
                observed_keys: set[str] = set()
                for record in section["facts"]["records"]:
                    if not isinstance(record, dict) or set(record) - expected_keys:
                        raise ValueError("weapon aggregate record has unregistered fields")
                    observed_keys.update(record)
                    for field_key in present:
                        field = registry_fields[field_key]
                        if next(iter(_canonical_fact_keys(field))) in record:
                            _validate_canonical_fact_value(field, record)
                if observed_keys != expected_keys:
                    raise ValueError("weapon aggregate facts do not match field coverage")
            elif section["facts"]:
                raise ValueError("source-absent weapon facts must be empty")
        else:
            expected_keys = set().union(
                *(
                    _canonical_fact_keys(registry_fields[field_key])
                    for field_key in present
                ),
            ) if present else set()
            if set(section["facts"]) != expected_keys:
                raise ValueError("canonical facts do not match allow-listed field coverage")
            for field_key in present:
                _validate_canonical_fact_value(
                    registry_fields[field_key], section["facts"],
                )
        if section["completeness"] not in {"complete_allowlisted", "partial"}:
            raise ValueError("invalid canonical facts completeness")
        if section["completeness"] == "complete_allowlisted" and omitted:
            raise ValueError("complete canonical facts cannot omit known fields")
        all_present.update(present)
        all_absent.update(absent)
    sets = value["outcome_record_sets"]
    _expect_exact(sets, {"stats_kill_rows_ref", "performance_metric_changes_ref"}, "canonical_run_facts.outcome_record_sets")
    for field in sets:
        if sets[field] is not None:
            _safe_ref(f"canonical_run_facts.outcome_record_sets.{field}", sets[field])
    if value["completeness"] not in {"complete_allowlisted", "partial"}:
        raise ValueError("invalid canonical facts completeness")
    if value["completeness"] == "complete_allowlisted" and len(all_present | all_absent) < len(registry_fields):
        raise ValueError("complete canonical facts must account for every registry field")
    if value["unknown_field_policy"] != "excluded":
        raise ValueError("unknown_field_policy must be excluded")
    _safe_string_list("canonical_run_facts.limitations", value["limitations"])
    _assert_safe_json(value)
    return copy.deepcopy(value)


def _validate_outcome_record(value: object, index: int) -> dict:
    _expect_exact(value, {"canonical_time_ms", "source_time", "source_priority", "source_event_index", "values", "source_refs"}, f"outcome_record[{index}]")
    canonical_time_ms = _finite_number(
        f"outcome_record[{index}].canonical_time_ms",
        value["canonical_time_ms"],
        integer=True,
    )
    if canonical_time_ms < 0:
        raise ValueError("outcome record time must be non-negative")
    source_time = value["source_time"]
    _expect_exact(source_time, {"clock_domain", "value", "unit", "precision"}, f"outcome_record[{index}].source_time")
    _safe_token(f"outcome_record[{index}].source_time.clock_domain", source_time["clock_domain"])
    if isinstance(source_time["value"], str):
        _safe_token(f"outcome_record[{index}].source_time.value", source_time["value"])
    else:
        _finite_number(f"outcome_record[{index}].source_time.value", source_time["value"])
    for field in ("unit", "precision"):
        _safe_token(f"outcome_record[{index}].source_time.{field}", source_time[field])
    source_priority = _finite_number(
        f"outcome_record[{index}].source_priority",
        value["source_priority"],
        integer=True,
    )
    if source_priority not in {10, 20}:
        raise ValueError("outcome record source priority is not registered")
    expected_source_time = (
        ("stats_local_time_of_day", "HH:MM:SS.mmm", "milliseconds")
        if source_priority == 10
        else ("performance_challenge_relative", "seconds", "float32")
    )
    if (
        source_time["clock_domain"],
        source_time["unit"],
        source_time["precision"],
    ) != expected_source_time:
        raise ValueError("outcome record source time does not match source priority")
    source_event_index = _finite_number(
        f"outcome_record[{index}].source_event_index",
        value["source_event_index"],
        integer=True,
    )
    if source_event_index < 0:
        raise ValueError("outcome record source index must be non-negative")
    values = value["values"]
    if not isinstance(values, list) or not values:
        raise ValueError("outcome record values must be non-empty")
    registry = EvidenceKeyRegistry()
    for value_index, item in enumerate(values):
        _expect_exact(item, {"metric_key", "value", "value_semantics", "unit"}, f"outcome_record[{index}].values[{value_index}]")
        metric_key = _safe_token(
            f"outcome_record[{index}].values[{value_index}].metric_key",
            item["metric_key"],
        )
        if not registry.allows_outcome_metric(metric_key):
            raise ValueError("outcome record metric is not registered")
        if metric_key.startswith("performance."):
            field = next(
                field
                for field in source_field_registry_v1()["fields"]
                if field["source_group"] == "performance.metric_change"
                and f"performance.{field['source_key']}" == metric_key
            )
            expected_priority = 20
            expected_semantics = field["value_semantics"]
        else:
            field = next(
                field
                for field in source_field_registry_v1()["fields"]
                if field["source_group"] == "stats.kill_row"
                and field["source_key"] != "Timestamp"
                and f"stats.kill.{field['canonical_key']}" == metric_key
            )
            expected_priority = 10
            expected_semantics = "aggregate_within_kill_row"
        if source_priority != expected_priority:
            raise ValueError("outcome record metric/source priority mismatch")
        if item["unit"] != field["unit"]:
            raise ValueError("outcome record metric unit does not match registry")
        item_value = item["value"]
        if item_value is None:
            raise ValueError("normalized outcome record cannot contain null values")
        if field["value_type"] == "string":
            _safe_token(
                f"outcome_record[{index}].values[{value_index}].value",
                item_value,
            )
        elif field["value_type"] == "bool":
            if not isinstance(item_value, bool):
                raise ValueError("outcome record boolean value is invalid")
        elif field["value_type"] == "int":
            if isinstance(item_value, bool) or not isinstance(item_value, int):
                raise ValueError("outcome record integer value is invalid")
        elif field["value_type"] == "float":
            _finite_number(
                f"outcome_record[{index}].values[{value_index}].value",
                item_value,
            )
            if field["unit"] == "ratio" and not 0 <= float(item_value) <= 1:
                raise ValueError("outcome record ratio is out of range")
        else:
            raise ValueError("outcome record value type is unsupported")
        if item["value_semantics"] != expected_semantics:
            raise ValueError("outcome record value semantics do not match registry")
        _safe_token(f"outcome_record[{index}].values[{value_index}].unit", item["unit"])
    _stable_refs(f"outcome_record[{index}].source_refs", value["source_refs"], allow_empty=False)
    return copy.deepcopy(value)


def validate_normalized_outcome_timeline_v1(value: object) -> dict:
    _expect_exact(value, {"schema_version", "analysis_ref", "scope", "segment_ref", "canonical_time_window_ref", "mode", "resolution", "selected_series", "overview_series", "records", "event_refs", "completeness", "next_cursor", "limitations"}, "normalized_outcome_timeline")
    if value.get("schema_version") != "normalized_outcome_timeline.v1":
        raise UnsupportedEvidenceContractVersion(value.get("schema_version"))
    _safe_ref("normalized_outcome_timeline.analysis_ref", value["analysis_ref"])
    if value["scope"] not in {"whole_run", "evidence_segment"}:
        raise ValueError("invalid timeline scope")
    if value["scope"] == "evidence_segment" and value["segment_ref"] is None:
        raise ValueError("segment timeline requires segment_ref")
    if value["segment_ref"] is not None:
        _safe_ref("normalized_outcome_timeline.segment_ref", value["segment_ref"])
    _safe_ref("normalized_outcome_timeline.canonical_time_window_ref", value["canonical_time_window_ref"])
    if value["mode"] not in {"overview", "exact_page"} or value["resolution"] not in {"deterministic_binned", "source_native"}:
        raise ValueError("invalid timeline mode or resolution")
    selected_series = _safe_string_list(
        "normalized_outcome_timeline.selected_series",
        value["selected_series"],
        allow_empty=False,
    )
    if len(selected_series) > 8:
        raise ValueError("timeline may select at most 8 series")
    registry = EvidenceKeyRegistry()
    if not all(registry.allows_outcome_metric(item) for item in selected_series):
        raise ValueError("timeline selected series is not registered")
    overview_series = value["overview_series"]
    records = value["records"]
    if value["mode"] == "exact_page":
        if value["resolution"] != "source_native" or overview_series is not None:
            raise ValueError("exact timeline must use source-native records")
        if not isinstance(records, list) or len(records) > 120:
            raise ValueError("timeline exact page may contain at most 120 records")
        for index, record in enumerate(records):
            _validate_outcome_record(record, index)
            if any(
                item["metric_key"] not in selected_series
                for item in record["values"]
            ):
                raise ValueError("timeline record contains an unselected series")
        sort_keys = [
            (record["canonical_time_ms"], record["source_priority"], record["source_event_index"])
            for record in records
        ]
        if sort_keys != sorted(sort_keys):
            raise ValueError("timeline records are not deterministically ordered")
    else:
        if (
            value["resolution"] != "deterministic_binned"
            or records is not None
            or not isinstance(overview_series, list)
        ):
            raise ValueError("overview timeline must use deterministic bins")
        for index, series in enumerate(overview_series):
            _expect_exact(
                series,
                {"metric_key", "unit", "points", "source_refs"},
                f"overview_series[{index}]",
            )
            if series["metric_key"] not in selected_series:
                raise ValueError("overview contains an unselected series")
            source_field = next(
                field
                for field in source_field_registry_v1()["fields"]
                if (
                    field["source_group"] == "performance.metric_change"
                    and f"performance.{field['source_key']}" == series["metric_key"]
                ) or (
                    field["source_group"] == "stats.kill_row"
                    and field["source_key"] != "Timestamp"
                    and f"stats.kill.{field['canonical_key']}" == series["metric_key"]
                )
            )
            if series["unit"] != source_field["unit"]:
                raise ValueError("overview series unit does not match registry")
            _stable_refs(
                f"overview_series[{index}].source_refs",
                series["source_refs"],
                allow_empty=False,
            )
            if not isinstance(series["points"], list):
                raise ValueError("overview points must be a list")
            previous_time = None
            for point_index, point in enumerate(series["points"]):
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError("overview point must be [time, value]")
                point_time = _finite_number(
                    f"overview_series[{index}].points[{point_index}][0]",
                    point[0],
                    integer=True,
                )
                _finite_number(
                    f"overview_series[{index}].points[{point_index}][1]",
                    point[1],
                )
                if previous_time is not None and point_time < previous_time:
                    raise ValueError("overview points are not ordered")
                previous_time = point_time
    _stable_refs("normalized_outcome_timeline.event_refs", value["event_refs"])
    if value["completeness"] not in {"complete", "paged", "downsampled", "partial"}:
        raise ValueError("invalid timeline completeness")
    if value["mode"] == "overview" and value["completeness"] not in {
        "downsampled", "partial",
    }:
        raise ValueError("overview timeline must declare downsampling")
    if value["mode"] == "exact_page" and value["completeness"] == "downsampled":
        raise ValueError("exact timeline cannot be downsampled")
    if value["next_cursor"] is not None:
        _safe_ref("normalized_outcome_timeline.next_cursor", value["next_cursor"])
    _safe_string_list("normalized_outcome_timeline.limitations", value["limitations"])
    _assert_safe_json(value)
    return copy.deepcopy(value)


def validate_metric_record_v1(value: object, *, registry: EvidenceKeyRegistry | None = None) -> dict:
    registry = registry or EvidenceKeyRegistry()
    _expect_exact(value, {"schema_version", "metric_key", "metric_version", "value", "unit", "availability", "classification", "provenance", "population", "distribution", "condition_refs", "event_refs", "evidence_segment_refs", "coverage", "confidence", "limitations"}, "metric_record")
    if value.get("schema_version") != "metric_record.v1":
        raise UnsupportedEvidenceContractVersion(value.get("schema_version"))
    metric_key = _safe_token("metric_record.metric_key", value["metric_key"])
    if not registry.allows_metric(metric_key):
        raise ValueError("metric_record.metric_key is not registered")
    _safe_token("metric_record.metric_version", value["metric_version"])
    if value["value"] is not None:
        _finite_number("metric_record.value", value["value"])
    _safe_token("metric_record.unit", value["unit"])
    if value["availability"] not in {"available", "partial", "unavailable"}:
        raise ValueError("invalid metric availability")
    if value["availability"] == "available" and value["value"] is None:
        raise ValueError("available metric requires a value")
    if value["availability"] == "unavailable" and value["value"] is not None:
        raise ValueError("unavailable metric cannot contain a value")
    if value["classification"] not in {"deterministic", "experimental"}:
        raise ValueError("invalid metric classification")
    provenance = value["provenance"]
    _expect_exact(provenance, {"kind", "source_refs"}, "metric_record.provenance")
    if provenance["kind"] not in {"measured", "derived", "fused", "inferred"}:
        raise ValueError("invalid metric provenance kind")
    _stable_refs("metric_record.provenance.source_refs", provenance["source_refs"], allow_empty=False)
    population = value["population"]
    _expect_exact(population, {"sample_count", "valid_count", "excluded_count"}, "metric_record.population")
    for field in population:
        _finite_number(f"metric_record.population.{field}", population[field], integer=True)
        if population[field] < 0:
            raise ValueError("metric population counts must be non-negative")
    if population["valid_count"] + population["excluded_count"] > population["sample_count"]:
        raise ValueError("metric population counts do not balance")
    distribution = value["distribution"]
    if distribution is not None:
        if value["availability"] == "unavailable":
            raise ValueError("unavailable metric cannot contain a distribution")
        _expect_exact(distribution, {"min", "p10", "p25", "median", "p75", "p90", "max", "histogram_bins"}, "metric_record.distribution")
        previous = None
        for field in ("min", "p10", "p25", "median", "p75", "p90", "max"):
            if distribution[field] is not None:
                number = float(_finite_number(f"distribution.{field}", distribution[field]))
                if previous is not None and number < previous:
                    raise ValueError("metric distribution is not ordered")
                previous = number
        if not isinstance(distribution["histogram_bins"], list):
            raise ValueError("metric histogram_bins must be a list")
        _assert_safe_json(distribution["histogram_bins"])
    _stable_refs("metric_record.condition_refs", value["condition_refs"])
    _stable_refs("metric_record.event_refs", value["event_refs"])
    _stable_refs("metric_record.evidence_segment_refs", value["evidence_segment_refs"])
    if value["coverage"] is not None:
        _ratio("metric_record.coverage", value["coverage"])
    if value["confidence"] is not None:
        _ratio("metric_record.confidence", value["confidence"])
    _safe_string_list("metric_record.limitations", value["limitations"])
    _assert_safe_json(value)
    return copy.deepcopy(value)


def validate_evidence_segment_v1(value: object, *, canonical_window: dict, registry: EvidenceKeyRegistry | None = None) -> dict:
    registry = registry or EvidenceKeyRegistry()
    _expect_exact(value, {"schema_version", "segment_id", "analysis_ref", "analyzer_ref", "segment_kind", "start_ms", "end_ms", "focus_start_ms", "focus_end_ms", "title_key", "rank_reason", "issue_refs", "metric_refs", "event_refs", "available_channels", "source_coverage", "confidence", "video_playback", "limitations"}, "evidence_segment")
    if value.get("schema_version") != "evidence_segment.v1":
        raise UnsupportedEvidenceContractVersion(value.get("schema_version"))
    _safe_ref("evidence_segment.segment_id", value["segment_id"])
    _safe_ref("evidence_segment.analysis_ref", value["analysis_ref"])
    _safe_token("evidence_segment.analyzer_ref", value["analyzer_ref"])
    if not registry.allows_segment(value["segment_kind"]):
        raise ValueError("evidence_segment.segment_kind is not registered")
    _safe_token("evidence_segment.title_key", value["title_key"])
    if value["rank_reason"] not in {"typical", "worst", "improved", "comparison", "low_confidence"}:
        raise ValueError("invalid evidence segment rank reason")
    start, end = _window_bounds(canonical_window)
    for field in ("start_ms", "end_ms", "focus_start_ms", "focus_end_ms"):
        _finite_number(f"evidence_segment.{field}", value[field], integer=True)
    if not (start <= value["start_ms"] < value["end_ms"] <= end):
        raise ValueError("evidence segment interval is outside canonical window")
    if not (value["start_ms"] <= value["focus_start_ms"] < value["focus_end_ms"] <= value["end_ms"]):
        raise ValueError("evidence segment focus range is outside segment")
    for field in ("issue_refs", "metric_refs", "event_refs"):
        _stable_refs(f"evidence_segment.{field}", value[field])
    channels = value["available_channels"]
    if not isinstance(channels, list):
        raise ValueError("evidence_segment.available_channels must be a list")
    for index, channel in enumerate(channels):
        _validate_channel_key(channel, registry, f"evidence_segment.available_channels[{index}]")
    _ratio("evidence_segment.source_coverage", value["source_coverage"])
    _ratio("evidence_segment.confidence", value["confidence"])
    playback = value["video_playback"]
    _expect_exact(playback, {"availability", "artifact_ref", "start_ms", "end_ms"}, "evidence_segment.video_playback")
    if playback["availability"] not in {"available", "partial", "unavailable"}:
        raise ValueError("invalid video playback availability")
    if playback["availability"] == "available" and playback["artifact_ref"] is None:
        raise ValueError("available video playback requires an artifact ref")
    if playback["availability"] == "unavailable" and playback["artifact_ref"] is not None:
        raise ValueError("unavailable video playback cannot contain an artifact ref")
    if playback["artifact_ref"] is not None:
        _safe_ref("evidence_segment.video_playback.artifact_ref", playback["artifact_ref"])
        if playback["start_ms"] is None or playback["end_ms"] is None:
            raise ValueError("available video playback requires bounds")
        _finite_number("video_playback.start_ms", playback["start_ms"], integer=True)
        _finite_number("video_playback.end_ms", playback["end_ms"], integer=True)
        if not (value["start_ms"] <= playback["start_ms"] < playback["end_ms"] <= value["end_ms"]):
            raise ValueError("video playback bounds are outside segment")
    elif playback["start_ms"] is not None or playback["end_ms"] is not None:
        raise ValueError("unavailable video playback cannot contain bounds")
    _safe_string_list("evidence_segment.limitations", value["limitations"])
    _assert_safe_json(value)
    return copy.deepcopy(value)


def _validate_analysis_evidence_artifact(
    value: object,
    *,
    schema_version: str,
    allow_event_bundle_v2: bool,
    registry: EvidenceKeyRegistry | None = None,
) -> dict:
    registry = registry or EvidenceKeyRegistry()
    _expect_exact(value, {"schema_version", "analysis_ref", "canonical_time_window", "canonical_run_facts", "normalized_outcome_records", "signal_bundles", "event_bundles", "metric_records", "evidence_segments", "sample_sets", "limitations"}, "analysis_evidence_artifact")
    if value.get("schema_version") != schema_version:
        raise UnsupportedEvidenceContractVersion(value.get("schema_version"))
    analysis_ref = _safe_ref(
        "analysis_evidence_artifact.analysis_ref", value["analysis_ref"],
    )
    window_start, window_end = _window_bounds(value["canonical_time_window"])
    window_ref = f"{analysis_ref}:canonical-window"
    if value["canonical_run_facts"] is not None:
        facts = validate_canonical_run_facts_v1(value["canonical_run_facts"])
        if (
            facts["analysis_ref"] != analysis_ref
            or facts["canonical_time_window_ref"] != window_ref
        ):
            raise ValueError("canonical facts are bound to another analysis window")
    records = value["normalized_outcome_records"]
    if not isinstance(records, list) or len(records) > 1_000_000:
        raise ValueError("normalized outcome records must be a bounded list")
    for index, record in enumerate(records):
        _validate_outcome_record(record, index)
        if not window_start <= record["canonical_time_ms"] < window_end:
            raise ValueError("outcome record is outside canonical window")
    sort_keys = [(record["canonical_time_ms"], record["source_priority"], record["source_event_index"]) for record in records]
    if sort_keys != sorted(sort_keys):
        raise ValueError("artifact outcome records are not ordered")
    for field, validator in (
        ("signal_bundles", validate_signal_bundle_v1),
        (
            "event_bundles",
            validate_event_bundle if allow_event_bundle_v2 else validate_event_bundle_v1,
        ),
    ):
        items = value[field]
        if not isinstance(items, list):
            raise ValueError(f"{field} must be a list")
        for item in items:
            validator(item, registry=registry)
            if item["analysis_ref"] != analysis_ref:
                raise ValueError(f"{field} is bound to another analysis")
            if field == "signal_bundles" and item["canonical_time_window_ref"] != window_ref:
                raise ValueError("signal bundle is bound to another canonical window")
            if field == "event_bundles":
                for event in item["events"]:
                    if not (
                        window_start <= event["start_ms"] < window_end
                        and event["start_ms"] <= event["end_ms"] <= window_end
                    ):
                        raise ValueError("event is outside canonical window")
    if allow_event_bundle_v2:
        event_ids = [
            event["event_id"]
            for bundle in value["event_bundles"]
            for event in bundle["events"]
        ]
        association_ids = [
            association["association_id"]
            for bundle in value["event_bundles"]
            for association in bundle["outcome_associations"]
        ]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("v2 artifact event refs must be globally unique")
        if len(association_ids) != len(set(association_ids)):
            raise ValueError("v2 artifact association refs must be globally unique")
    target_channel_parts: dict[str, set[str]] = {}
    target_channel_profiles: dict[str, set[str]] = {}
    target_channel_sources: dict[str, set[str]] = {}
    for bundle in value["signal_bundles"]:
        for channel in bundle["channels"]:
            channel_key = channel["channel_key"]
            if not channel_key.startswith("target."):
                continue
            track_id, channel_part = channel_key[len("target."):].rsplit(".", 1)
            target_channel_parts.setdefault(track_id, set()).add(channel_part)
            profile_ref = bundle.get("visual_quality_profile_ref")
            if profile_ref is not None:
                target_channel_profiles.setdefault(track_id, set()).add(profile_ref)
            target_channel_sources.setdefault(track_id, set()).update(channel["source_refs"])
    reachable_target_refs = {
        f"{analysis_ref}:target-track:{track_id}"
        for track_id, parts in target_channel_parts.items()
        if {"position_x", "position_y"} <= parts
        and parts.intersection({"visible_radius", "hitbox"})
    }
    for bundle in value["event_bundles"]:
        for association in bundle["outcome_associations"]:
            target_ref = association["target_track_ref"]
            if (
                association["availability"] == "available"
                and target_ref not in reachable_target_refs
            ):
                raise ValueError("outcome association target track ref is not reachable")
            validation = association.get("validation")
            if association["association_kind"] != "validated_aligned":
                continue
            track_id = target_ref[len(f"{analysis_ref}:target-track:"):]
            if (
                validation["visual_quality_profile_ref"]
                not in target_channel_profiles.get(track_id, set())
                or validation["visual_source_ref"]
                not in target_channel_sources.get(track_id, set())
            ):
                raise ValueError("validated outcome association visual evidence is unreachable")
    for index, metric in enumerate(value["metric_records"]):
        validate_metric_record_v1(metric, registry=registry)
    segments_by_id: dict[str, dict] = {}
    for segment in value["evidence_segments"]:
        validate_evidence_segment_v1(segment, canonical_window=value["canonical_time_window"], registry=registry)
        if segment["analysis_ref"] != analysis_ref:
            raise ValueError("evidence segment is bound to another analysis")
        if segment["segment_id"] in segments_by_id:
            raise ValueError("duplicate evidence segment")
        segments_by_id[segment["segment_id"]] = segment
    events_by_id = {
        event["event_id"]: event
        for bundle in value["event_bundles"]
        for event in bundle["events"]
    }
    predictive_metric_keys = {
        "dynamic_clicking.predictive_lead",
        "continuous_tracking.predictive_lead_ms",
    }
    for metric in value["metric_records"]:
        if metric["metric_key"] not in predictive_metric_keys:
            continue
        predictability_events = [
            events_by_id.get(condition_ref)
            for condition_ref in metric["condition_refs"]
        ]
        accepted = []
        for event in predictability_events:
            if (
                event is None
                or event["event_kind"] != "motion_predictability_evidence"
                or event["attributes"]["acceptance"] != "accepted"
            ):
                continue
            if metric["metric_key"] == "continuous_tracking.predictive_lead_ms" and (
                event["attributes"].get("schema_version")
                != "motion_predictability_evidence.v1"
                or event["attributes"].get("availability") != "available"
                or event["confidence"] != 1.0
                or event["limitations"]
            ):
                continue
            accepted.append(event)
        if len(accepted) != 1:
            raise ValueError("predictive lead requires accepted predictability evidence")
        evidence_event = accepted[0]
        segment_ref = evidence_event["attributes"]["segment_ref"]
        segment = segments_by_id.get(segment_ref)
        if (
            metric["evidence_segment_refs"] != [segment_ref]
            or segment is None
            or evidence_event["event_id"] not in segment["event_refs"]
            or not segment["start_ms"] <= evidence_event["start_ms"] <= segment["end_ms"]
        ):
            raise ValueError("predictive lead evidence is not bound to its segment")
    sample_sets = value["sample_sets"]
    if not isinstance(sample_sets, list):
        raise ValueError("sample_sets must be a list")
    sample_ids: set[str] = set()
    for index, sample_set in enumerate(sample_sets):
        _expect_exact(sample_set, {"sample_set_id", "channel_key", "unit", "points"}, f"sample_sets[{index}]")
        sample_id = _safe_ref(f"sample_sets[{index}].sample_set_id", sample_set["sample_set_id"])
        if sample_id in sample_ids:
            raise ValueError("duplicate sample set")
        sample_ids.add(sample_id)
        _validate_channel_key(sample_set["channel_key"], registry, f"sample_sets[{index}].channel_key")
        _safe_token(f"sample_sets[{index}].unit", sample_set["unit"])
        points = sample_set["points"]
        if not isinstance(points, list):
            raise ValueError("sample set points must be a list")
        for point_index, point in enumerate(points):
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("sample point must be [time, value]")
            _finite_number(f"sample_sets[{index}].points[{point_index}][0]", point[0])
            _finite_number(f"sample_sets[{index}].points[{point_index}][1]", point[1])
    referenced_samples = {
        channel["samples_ref"]
        for bundle in value["signal_bundles"]
        for channel in bundle["channels"]
    }
    if not referenced_samples <= sample_ids:
        raise ValueError("signal bundle references a missing local sample set")

    samples_by_ref = {
        sample_set["sample_set_id"]: sample_set
        for sample_set in sample_sets
    }
    for event_bundle in value["event_bundles"]:
        if event_bundle["schema_version"] != "event_bundle.v2":
            continue
        bindings_by_ref = {
            binding["rule_ref"]: binding
            for binding in event_bundle["outcome_association_rule_bindings"]
        }
        for association in event_bundle["outcome_associations"]:
            if association["association_kind"] != "validated_aligned":
                continue
            validation = association["validation"]
            binding = bindings_by_ref[validation["rule_ref"]]
            if binding["canonical_timebase_version"] != value["canonical_time_window"]["timebase_version"]:
                raise ValueError("validated outcome association timebase version mismatch")
            stats_values = validation["stats_kill"]
            matching_stats_records = []
            for record in value["normalized_outcome_records"]:
                if (
                    record["source_priority"] != 10
                    or record["canonical_time_ms"] != validation["outcome_time_ms"]
                    or validation["stats_source_ref"] not in record["source_refs"]
                ):
                    continue
                values_by_key = {
                    item["metric_key"].removeprefix("stats.kill."): item["value"]
                    for item in record["values"]
                    if item["metric_key"].startswith("stats.kill.")
                }
                if all(values_by_key.get(key) == child for key, child in stats_values.items()):
                    matching_stats_records.append(record)
            if len(matching_stats_records) != 1:
                raise ValueError("validated outcome association Stats kill row is unreachable")
            facts = value["canonical_run_facts"]
            if facts is None:
                raise ValueError("validated outcome association canonical facts are required")
            if facts["scenario_profile_ref"] != validation["scenario_profile_ref"]:
                raise ValueError("validated outcome association scenario facts mismatch")
            stats_contracts = [
                contract
                for contract in facts["source_contracts"]
                if contract["source_kind"] == "stats"
                and contract["source_ref"] == validation["stats_source_ref"]
                and contract["parser_version"] == binding["stats_parser_version"]
            ]
            if len(stats_contracts) != 1:
                raise ValueError("validated outcome association Stats parser binding mismatch")

            track_id = association["target_track_ref"].rsplit(":target-track:", 1)[1]
            required_channels = {
                f"target.{track_id}.position_x",
                f"target.{track_id}.position_y",
                f"target.{track_id}.visible_radius",
            }
            matching_signal_bundles = [
                signal_bundle
                for signal_bundle in value["signal_bundles"]
                if signal_bundle["visual_quality_profile_ref"]
                == validation["visual_quality_profile_ref"]
                and required_channels <= {
                    channel["channel_key"] for channel in signal_bundle["channels"]
                }
            ]
            if len(matching_signal_bundles) != 1:
                raise ValueError("validated outcome association target samples are ambiguous")
            signal_bundle = matching_signal_bundles[0]
            selector = signal_bundle["observed_visual_domain"]
            resolution = selector.get("resolution") if isinstance(selector, dict) else None
            if (
                not isinstance(resolution, list)
                or len(resolution) != 2
                or any(
                    isinstance(size, bool) or not isinstance(size, int) or size <= 0
                    for size in resolution
                )
            ):
                raise ValueError("validated outcome association viewport resolution is unavailable")
            channels_by_key = {
                channel["channel_key"]: channel
                for channel in signal_bundle["channels"]
            }
            if any(
                channels_by_key[channel_key]["confidence_summary"]
                < binding["track_predicate"]["minimum_sample_confidence"]
                for channel_key in required_channels
            ):
                raise ValueError("validated outcome association visual confidence is insufficient")
            click_time = validation["click_time_ms"]
            selected_points: dict[str, tuple[float, float]] = {}
            for channel_key in required_channels:
                points = samples_by_ref[channels_by_key[channel_key]["samples_ref"]]["points"]
                ordered = sorted(
                    (
                        (abs(float(point[0]) - click_time), index, point)
                        for index, point in enumerate(points)
                    ),
                    key=lambda item: (item[0], item[1]),
                )
                if not ordered or (
                    len(ordered) > 1 and ordered[0][0] == ordered[1][0]
                ):
                    raise ValueError("validated outcome association target sample is unavailable")
                selected_points[channel_key] = (
                    float(ordered[0][2][0]), float(ordered[0][2][1]),
                )
            sample_times = {point[0] for point in selected_points.values()}
            if len(sample_times) != 1:
                raise ValueError("validated outcome association target sample times disagree")
            sample_time = next(iter(sample_times))
            sample_gap = int(abs(sample_time - click_time))
            target_x = selected_points[f"target.{track_id}.position_x"][1]
            target_y = selected_points[f"target.{track_id}.position_y"][1]
            observed_radius = selected_points[f"target.{track_id}.visible_radius"][1]
            effective_radius = observed_radius - binding["track_predicate"]["hitbox_inset_px"]
            center_distance = math.hypot(
                target_x - resolution[0] / 2.0,
                target_y - resolution[1] / 2.0,
            )
            track_check = validation["track_check"]
            if (
                sample_gap != track_check["sample_gap_ms"]
                or not math.isclose(
                    effective_radius,
                    float(track_check["effective_radius_px"]),
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                )
                or not math.isclose(
                    center_distance,
                    float(track_check["center_distance_px"]),
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                )
            ):
                raise ValueError("validated outcome association track calculation mismatch")
            geometric_candidate_refs: set[str] = set()
            channel_parts_by_track: dict[str, set[str]] = {}
            for channel_key in channels_by_key:
                if not channel_key.startswith("target."):
                    continue
                candidate_track_id, part = channel_key[len("target."):].rsplit(".", 1)
                channel_parts_by_track.setdefault(candidate_track_id, set()).add(part)
            for candidate_track_id, parts in channel_parts_by_track.items():
                if not {"position_x", "position_y", "visible_radius"} <= parts:
                    continue
                candidate_keys = {
                    part: f"target.{candidate_track_id}.{part}"
                    for part in ("position_x", "position_y", "visible_radius")
                }
                candidate_channels = [channels_by_key[key] for key in candidate_keys.values()]
                if any(
                    validation["visual_source_ref"] not in channel["source_refs"]
                    or channel["confidence_summary"]
                    < binding["track_predicate"]["minimum_sample_confidence"]
                    for channel in candidate_channels
                ):
                    continue
                candidate_points: dict[str, tuple[float, float]] = {}
                candidate_unavailable = False
                for part, key in candidate_keys.items():
                    points = samples_by_ref[channels_by_key[key]["samples_ref"]]["points"]
                    ordered = sorted(
                        (
                            (abs(float(point[0]) - click_time), index, point)
                            for index, point in enumerate(points)
                        ),
                        key=lambda item: (item[0], item[1]),
                    )
                    if not ordered or (
                        len(ordered) > 1 and ordered[0][0] == ordered[1][0]
                    ):
                        candidate_unavailable = True
                        break
                    candidate_points[part] = (
                        float(ordered[0][2][0]), float(ordered[0][2][1]),
                    )
                if candidate_unavailable or len({point[0] for point in candidate_points.values()}) != 1:
                    continue
                candidate_time = next(iter(candidate_points.values()))[0]
                if abs(candidate_time - click_time) > binding["track_predicate"]["max_sample_gap_ms"]:
                    continue
                candidate_radius = (
                    candidate_points["visible_radius"][1]
                    - binding["track_predicate"]["hitbox_inset_px"]
                )
                candidate_distance = math.hypot(
                    candidate_points["position_x"][1] - resolution[0] / 2.0,
                    candidate_points["position_y"][1] - resolution[1] / 2.0,
                )
                if candidate_radius > 0 and candidate_distance <= candidate_radius:
                    geometric_candidate_refs.add(
                        f"{analysis_ref}:target-track:{candidate_track_id}"
                    )
            if geometric_candidate_refs != {association["target_track_ref"]}:
                raise ValueError("validated outcome association geometric candidate is not unique")
    _safe_string_list("analysis_evidence_artifact.limitations", value["limitations"])
    _assert_safe_json(value, allow_samples=True)
    return copy.deepcopy(value)


def validate_analysis_evidence_artifact_v1(
    value: object, *, registry: EvidenceKeyRegistry | None = None,
) -> dict:
    return _validate_analysis_evidence_artifact(
        value,
        schema_version="analysis_evidence_artifact.v1",
        allow_event_bundle_v2=False,
        registry=registry,
    )


def validate_analysis_evidence_artifact_v2(
    value: object, *, registry: EvidenceKeyRegistry | None = None,
) -> dict:
    return _validate_analysis_evidence_artifact(
        value,
        schema_version="analysis_evidence_artifact.v2",
        allow_event_bundle_v2=True,
        registry=registry,
    )


def validate_analysis_evidence_artifact(
    value: object, *, registry: EvidenceKeyRegistry | None = None,
) -> dict:
    if not isinstance(value, dict):
        raise ValueError("analysis_evidence_artifact must be a dict")
    version = value.get("schema_version")
    if version == "analysis_evidence_artifact.v1":
        return validate_analysis_evidence_artifact_v1(value, registry=registry)
    if version == "analysis_evidence_artifact.v2":
        return validate_analysis_evidence_artifact_v2(value, registry=registry)
    raise UnsupportedEvidenceContractVersion(version)


def validate_processed_event_table_v1(value: object) -> dict:
    _expect_exact(
        value,
        {
            "schema_version", "table_ref", "analysis_ref", "analyzer_ref", "family",
            "event_kind", "row_count", "included_count", "excluded_count", "completeness",
            "field_catalog", "index_fields", "rows_ref", "limitations",
        },
        "processed_event_table",
    )
    if value["schema_version"] != "processed_event_table.v1":
        raise UnsupportedEvidenceContractVersion(value["schema_version"])
    analysis_ref = _safe_ref("processed_event_table.analysis_ref", value["analysis_ref"])
    table_ref = _safe_ref("processed_event_table.table_ref", value["table_ref"])
    if not table_ref.startswith(f"{analysis_ref}:table:") or value["rows_ref"] != table_ref:
        raise ValueError("processed event table refs are inconsistent")
    _safe_token("processed_event_table.analyzer_ref", value["analyzer_ref"])
    _safe_token("processed_event_table.family", value["family"])
    _safe_token("processed_event_table.event_kind", value["event_kind"])
    row_count = _finite_number("processed_event_table.row_count", value["row_count"], integer=True)
    included = _finite_number("processed_event_table.included_count", value["included_count"], integer=True)
    excluded = _finite_number("processed_event_table.excluded_count", value["excluded_count"], integer=True)
    if row_count < 0 or included < 0 or excluded < 0 or row_count != included:
        raise ValueError("processed event table counts are inconsistent")
    completeness = value["completeness"]
    if completeness not in {"complete", "partial", "unavailable"}:
        raise ValueError("processed event table completeness is invalid")
    if completeness == "complete" and excluded:
        raise ValueError("complete processed event table cannot exclude rows")
    catalog = value["field_catalog"]
    if not isinstance(catalog, list) or not catalog or len(catalog) > 64:
        raise ValueError("processed event field catalog must be bounded")
    field_keys: list[str] = []
    for index, field in enumerate(catalog):
        _expect_exact(
            field,
            {
                "field_key", "role", "value_type", "unit", "metric_key",
                "metric_version", "expected_direction", "limitations",
            },
            f"processed_event_table.field_catalog[{index}]",
        )
        field_key = _safe_token(f"processed_event_table.field_catalog[{index}].field_key", field["field_key"])
        field_keys.append(field_key)
        if field["role"] not in {"identity", "timing", "condition", "metric", "outcome", "quality"}:
            raise ValueError("processed event field role is invalid")
        if field["value_type"] not in {"number", "string", "ref", "string_list", "boolean"}:
            raise ValueError("processed event field value type is invalid")
        if field["unit"] is not None:
            _safe_token(f"processed_event_table.field_catalog[{index}].unit", field["unit"])
        if field["metric_key"] is not None:
            _safe_token(f"processed_event_table.field_catalog[{index}].metric_key", field["metric_key"])
        if field["metric_version"] is not None:
            version = _safe_token(
                f"processed_event_table.field_catalog[{index}].metric_version",
                field["metric_version"],
            )
            if not _VERSION_RE.fullmatch(version):
                raise ValueError("processed event metric version is invalid")
        if field["expected_direction"] not in {
            "lower_better", "higher_better", "target_band", "descriptive_only", "comparison_only",
        }:
            raise ValueError("processed event expected direction is invalid")
        _safe_string_list(
            f"processed_event_table.field_catalog[{index}].limitations",
            field["limitations"],
        )
    if len(set(field_keys)) != len(field_keys):
        raise ValueError("processed event field catalog contains duplicates")
    index_fields = _safe_string_list("processed_event_table.index_fields", value["index_fields"])
    if len(index_fields) > 8 or not set(index_fields) <= set(field_keys):
        raise ValueError("processed event index fields are invalid")
    _safe_string_list("processed_event_table.limitations", value["limitations"])
    _assert_safe_json(value)
    return copy.deepcopy(value)


def _static_processed_field_catalog_v1() -> list[dict]:
    limitations_by_field = {
        "reverse_ratio": ["reacceleration_ratio_is_discrete_speed_delta_sign"],
        "direction_reverse_ratio": ["direction_reverse_ratio_is_raw_path_sign_change"],
        "corrective_count": ["corrective_counts_use_discrete_direction_sign_runs"],
        "submovement_count": ["corrective_counts_use_discrete_direction_sign_runs"],
        "trough_depth_ratio": ["trough_depth_ratio_not_temporal_overlap"],
        "submovement_overlap": ["trough_depth_ratio_not_temporal_overlap"],
        "sparc": ["sparc_cross_polling_comparability_unverified"],
    }
    return [
        {
            "field_key": field_key,
            "role": role,
            "value_type": value_type,
            "unit": unit,
            "metric_key": metric_key,
            "metric_version": metric_version,
            "expected_direction": "comparison_only" if metric_key else "descriptive_only",
            "limitations": limitations_by_field.get(field_key, []),
        }
        for field_key, role, value_type, unit, metric_key, metric_version
        in _STATIC_PROCESSED_EVENT_FIELDS_V1
    ]


def dynamic_processed_field_catalog_v1() -> list[dict]:
    return [
        {
            "field_key": field_key,
            "role": role,
            "value_type": value_type,
            "unit": unit,
            "metric_key": metric_key,
            "metric_version": metric_version,
            "expected_direction": expected_direction,
            "limitations": [],
        }
        for (
            field_key, role, value_type, unit, metric_key, metric_version,
            expected_direction,
        ) in _DYNAMIC_PROCESSED_EVENT_FIELDS_V1
    ]


def tracking_processed_field_catalog_v1(event_kind: str) -> list[dict]:
    specific = _TRACKING_PROCESSED_EVENT_FIELDS_V1.get(event_kind)
    if specific is None:
        raise ValueError("continuous tracking processed event kind is unsupported")
    return [
        {
            "field_key": field_key,
            "role": role,
            "value_type": value_type,
            "unit": unit,
            "metric_key": metric_key,
            "metric_version": metric_version,
            "expected_direction": expected_direction,
            "limitations": [],
        }
        for (
            field_key, role, value_type, unit, metric_key, metric_version,
            expected_direction,
        ) in (*_TRACKING_COMMON_PROCESSED_EVENT_FIELDS_V1, *specific)
    ]


def target_switching_processed_field_catalog_v1(event_kind: str) -> list[dict]:
    if event_kind not in {"switch_chain", "unclassified_discrete_acquisition"}:
        raise ValueError("target switching processed event kind is unsupported")
    return [
        {
            "field_key": field_key,
            "role": role,
            "value_type": value_type,
            "unit": unit,
            "metric_key": metric_key,
            "metric_version": metric_version,
            "expected_direction": expected_direction,
            "limitations": [],
        }
        for (
            field_key, role, value_type, unit, metric_key, metric_version,
            expected_direction,
        ) in _TARGET_SWITCHING_PROCESSED_EVENT_FIELDS_V1
    ]


def build_processed_event_table_catalog_v1(artifact: object) -> list[dict]:
    if not isinstance(artifact, dict) or artifact.get("schema_version") != "analysis_evidence_artifact.v1":
        raise UnsupportedEvidenceContractVersion(
            artifact.get("schema_version") if isinstance(artifact, dict) else None
        )
    analysis_ref = _safe_ref("analysis_evidence_artifact.analysis_ref", artifact.get("analysis_ref"))
    bundles = artifact.get("event_bundles")
    if not isinstance(bundles, list):
        raise ValueError("analysis evidence event bundles must be a list")
    validated_bundles = []
    for bundle in bundles:
        validated_bundle = validate_event_bundle_v1(bundle)
        if validated_bundle["analysis_ref"] != analysis_ref:
            raise ValueError("event bundle is bound to another analysis")
        validated_bundles.append(validated_bundle)
    static_events = [
        event
        for bundle in validated_bundles
        for event in bundle["events"]
        if event["event_kind"] == "static_flick"
    ]
    dynamic_events = [
        event
        for bundle in validated_bundles
        for event in bundle["events"]
        if event["event_kind"] == "dynamic_click"
    ]
    tables: list[dict] = []
    table_specs = [
        (
            static_events, "static_flick", "native_flicking.v1",
            "static_clicking", _static_processed_field_catalog_v1(),
            list(_STATIC_PROCESSED_INDEX_FIELDS_V1),
        ),
        (
            dynamic_events, "dynamic_click", "dynamic_clicking.v1",
            "dynamic_clicking", dynamic_processed_field_catalog_v1(),
            ["click_ref", "target_track_ref", "click_time_ms", "change_state"],
        ),
    ]
    for event_kind in sorted(_TRACKING_PROCESSED_EVENT_FIELDS_V1):
        tracking_events = [
            event
            for bundle in validated_bundles
            for event in bundle["events"]
            if event["event_kind"] == event_kind
        ]
        table_specs.append((
            tracking_events,
            event_kind,
            "continuous_tracking.v1",
            "continuous_tracking",
            tracking_processed_field_catalog_v1(event_kind),
            ["start_ms", "end_ms", "target_track_ref"],
        ))
    for event_kind in ("switch_chain", "unclassified_discrete_acquisition"):
        switching_events = [
            event
            for bundle in validated_bundles
            for event in bundle["events"]
            if event["event_kind"] == event_kind
        ]
        table_specs.append((
            switching_events,
            event_kind,
            "target_switching.v1",
            "target_switching",
            target_switching_processed_field_catalog_v1(event_kind),
            ["start_ms", "end_ms", "previous_target_track_ref", "next_target_track_ref"],
        ))
    for events, event_kind, analyzer_ref, family, field_catalog, index_fields in table_specs:
        if not events:
            continue
        event_ids = [event["event_id"] for event in events]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("processed event table contains duplicate event refs")
        table_ref = f"{analysis_ref}:table:{event_kind}"
        limitations = sorted({
            limitation
            for event in events
            for limitation in event.get("limitations", [])
        })
        table = {
            "schema_version": "processed_event_table.v1",
            "table_ref": table_ref,
            "analysis_ref": analysis_ref,
            "analyzer_ref": analyzer_ref,
            "family": family,
            "event_kind": event_kind,
            "row_count": len(events),
            "included_count": len(events),
            "excluded_count": 0,
            "completeness": (
                "partial"
                if family in {"continuous_tracking", "target_switching"} and limitations
                else "complete"
            ),
            "field_catalog": field_catalog,
            "index_fields": index_fields,
            "rows_ref": table_ref,
            "limitations": limitations,
        }
        tables.append(validate_processed_event_table_v1(table))
    return tables


def build_processed_event_table_catalog(artifact: object) -> list[dict]:
    if not isinstance(artifact, dict):
        raise ValueError("analysis evidence artifact must be a dict")
    version = artifact.get("schema_version")
    if version == "analysis_evidence_artifact.v1":
        return build_processed_event_table_catalog_v1(artifact)
    if version != "analysis_evidence_artifact.v2":
        raise UnsupportedEvidenceContractVersion(version)
    validated = validate_analysis_evidence_artifact_v2(artifact)
    legacy_view = copy.deepcopy(validated)
    legacy_view["schema_version"] = "analysis_evidence_artifact.v1"
    legacy_view["event_bundles"] = [
        bundle
        for bundle in legacy_view["event_bundles"]
        if bundle["schema_version"] == "event_bundle.v1"
    ]
    return build_processed_event_table_catalog_v1(legacy_view)


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if type(value).__name__ in {"NAType", "NaTType"}:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def _normalized_field_value(field: dict, raw: object) -> tuple[object | None, str | None]:
    if _is_missing(raw):
        return None, "empty_or_invalid_value"
    value_type = field["value_type"]
    source_key = field["source_key"]
    try:
        if field["projection_policy"] == "allowlisted_fact" and source_key == "Crosshair":
            return bool(str(raw).strip()), None
        if source_key == "Challenge Start":
            parsed = datetime.strptime(str(raw).strip(), "%H:%M:%S.%f")
            return (
                ((parsed.hour * 60 + parsed.minute) * 60 + parsed.second) * 1000
                + parsed.microsecond // 1000,
                None,
            )
        if field["unit"] == "rgba_hex":
            text = str(raw).strip().lstrip("#")
            if not re.fullmatch(r"[0-9A-Fa-f]{8}", text):
                return None, "invalid_rgba"
            return text.upper(), None
        if value_type == "string":
            text = str(raw).strip()
            if not text:
                return None, "empty_value"
            return _safe_token(field["field_key"], text), None
        if value_type == "int":
            number = float(str(raw).strip())
            if not math.isfinite(number) or not number.is_integer():
                return None, "invalid_integer"
            integer = int(number)
            if field["unit"] in {"count", "counts_per_inch", "schema_version", "unix_epoch_milliseconds"} and integer < 0:
                return None, "negative_value"
            return integer, None
        if value_type == "float":
            text = str(raw).strip()
            if text.endswith("s"):
                text = text[:-1]
            number = float(text)
            if not math.isfinite(number):
                return None, "non_finite_value"
            if field["unit"] == "ratio" and not 0 <= number <= 1:
                return None, "ratio_out_of_range"
            return number, None
        if value_type == "bool":
            if isinstance(raw, bool):
                return raw, None
            text = str(raw).strip().casefold()
            if text in {"1", "true"}:
                return True, None
            if text in {"0", "false"}:
                return False, None
            return None, "invalid_boolean"
        if value_type == "resolution":
            match = re.fullmatch(r"\s*([1-9][0-9]*)\s*[xX]\s*([1-9][0-9]*)\s*", str(raw))
            if match is None:
                return None, "invalid_resolution"
            return {"resolution_width": int(match.group(1)), "resolution_height": int(match.group(2))}, None
        if value_type == "timestamp":
            text = str(raw).strip()
            datetime.strptime(text, "%H:%M:%S.%f")
            return text, None
        if value_type == "string_list":
            if not isinstance(raw, (list, tuple)):
                return None, "invalid_array"
            return [_safe_token(field["field_key"], str(item).strip()) for item in raw], None
        if value_type == "int_list":
            if not isinstance(raw, (list, tuple)):
                return None, "invalid_array"
            output: list[int] = []
            for item in raw:
                number = int(item)
                if isinstance(item, bool) or number != item or number < 0:
                    return None, "invalid_array_value"
                output.append(number)
            return output, None
    except (TypeError, ValueError, OverflowError):
        return None, "invalid_value"
    return None, "unsupported_field_type"


def _set_canonical_fact(section_facts: dict, canonical_key: str, value: object) -> None:
    _, leaf = canonical_key.split(".", 1)
    if canonical_key == "input_and_calibration.resolution_width/height":
        section_facts.update(value)
    else:
        section_facts[leaf] = value


def _new_section(key: str) -> dict:
    return {
        "section_key": key,
        "facts": {},
        "present_field_keys": [],
        "source_absent_field_keys": [],
        "omitted_known_fields": [],
        "completeness": "complete_allowlisted",
    }


def _mark_omitted(section: dict, field_key: str, reason: str) -> None:
    if any(
        item["field_key"] == field_key
        for item in section["omitted_known_fields"]
    ):
        section["completeness"] = "partial"
        return
    item = {"field_key": field_key, "reason": reason}
    section["omitted_known_fields"].append(item)
    section["completeness"] = "partial"


def _source_presence(
    presence: dict | None, source_key: str, raw_values: dict,
) -> str:
    # Parser dataclasses use default values for absent fields.  Once a parser
    # supplies presence metadata, it is authoritative and defaults are never
    # treated as source evidence.
    if presence is not None:
        return presence.get(source_key, "source_absent")
    return "present" if source_key in raw_values else "source_absent"


def _stats_rows(stats: object | None) -> list[dict]:
    if stats is None:
        return []
    frame = getattr(stats, "kills", None)
    if frame is None:
        return []
    try:
        return list(frame.to_dict(orient="records"))
    except (AttributeError, TypeError):
        return []


def _build_stats_outcome_records(
    stats: object,
    *, source_ref: str,
    window_start_ms: int,
    record_section: dict,
) -> list[dict]:
    fields = [
        field for field in source_field_registry_v1()["fields"]
        if field["source_group"] == "stats.kill_row"
    ]
    rows = _stats_rows(stats)
    if not rows:
        record_section["source_absent_field_keys"].extend(field["field_key"] for field in fields)
        return []
    valid_keys: set[str] = set()
    records: list[dict] = []
    for index, row in enumerate(rows):
        timestamp_field = next(field for field in fields if field["source_key"] == "Timestamp")
        timestamp, timestamp_error = _normalized_field_value(timestamp_field, row.get("Timestamp"))
        relative_s = row.get("time_s")
        if timestamp_error is not None or _is_missing(relative_s):
            for field in fields:
                _mark_omitted(record_section, field["field_key"], f"row_{index}_has_invalid_time")
            continue
        try:
            relative_ms = int(round(float(relative_s) * 1000))
        except (TypeError, ValueError, OverflowError):
            for field in fields:
                _mark_omitted(record_section, field["field_key"], f"row_{index}_has_invalid_time")
            continue
        values: list[dict] = []
        valid_keys.add(timestamp_field["field_key"])
        for field in fields:
            if field["source_key"] == "Timestamp":
                continue
            normalized, error = _normalized_field_value(field, row.get(field["source_key"]))
            if error is not None:
                _mark_omitted(record_section, field["field_key"], f"row_{index}_{error}")
                continue
            valid_keys.add(field["field_key"])
            values.append(
                {
                    "metric_key": f"stats.kill.{field['canonical_key']}",
                    "value": normalized,
                    "value_semantics": "aggregate_within_kill_row",
                    "unit": field["unit"],
                }
            )
        if values:
            records.append(
                {
                    "canonical_time_ms": window_start_ms + relative_ms,
                    "source_time": {
                        "clock_domain": "stats_local_time_of_day",
                        "value": timestamp,
                        "unit": "HH:MM:SS.mmm",
                        "precision": "milliseconds",
                    },
                    "source_priority": 10,
                    "source_event_index": index,
                    "values": values,
                    "source_refs": [source_ref],
                }
            )
    record_section["present_field_keys"].extend(
        field["field_key"] for field in fields if field["field_key"] in valid_keys
    )
    for field in fields:
        if field["field_key"] not in valid_keys and not any(
            item["field_key"] == field["field_key"]
            for item in record_section["omitted_known_fields"]
        ):
            record_section["source_absent_field_keys"].append(field["field_key"])
    return records


def _build_performance_outcome_records(
    performance: object,
    *, source_ref: str,
    window_start_ms: int,
    record_section: dict,
) -> list[dict]:
    fields = {
        field["source_key"]: field
        for field in source_field_registry_v1()["fields"]
        if field["source_group"] == "performance.metric_change"
    }
    events = list(getattr(performance, "events", ()) or ())
    if not events:
        record_section["source_absent_field_keys"].extend(field["field_key"] for field in fields.values())
        return []
    seen: set[str] = set()
    records: list[dict] = []
    for event in events:
        payload_type = getattr(event, "payload_type", "")
        field = fields.get(payload_type)
        if field is None:
            continue
        if field["value_semantics"] == "count_increment":
            raw_value = getattr(event, "count", None)
        elif field["value_semantics"] == "delta":
            raw_value = getattr(event, "delta", None)
        else:
            raw_value = getattr(event, "value", None)
        normalized, error = _normalized_field_value(field, raw_value)
        if error is not None:
            _mark_omitted(record_section, field["field_key"], f"event_{getattr(event, 'source_event_index', -1)}_{error}")
            continue
        seen.add(payload_type)
        timestamp = float(getattr(event, "timestamp"))
        records.append(
            {
                "canonical_time_ms": window_start_ms + int(getattr(event, "timestamp_ms", round(timestamp * 1000))),
                "source_time": {
                    "clock_domain": "performance_challenge_relative",
                    "value": timestamp,
                    "unit": "seconds",
                    "precision": "float32",
                },
                "source_priority": 20,
                "source_event_index": int(getattr(event, "source_event_index")),
                "values": [
                    {
                        "metric_key": f"performance.{payload_type}",
                        "value": normalized,
                        "value_semantics": field["value_semantics"],
                        "unit": field["unit"],
                    }
                ],
                "source_refs": [source_ref],
            }
        )
    record_section["present_field_keys"].extend(fields[key]["field_key"] for key in fields if key in seen)
    omitted_keys = {item["field_key"] for item in record_section["omitted_known_fields"]}
    record_section["source_absent_field_keys"].extend(
        fields[key]["field_key"]
        for key in fields
        if key not in seen and fields[key]["field_key"] not in omitted_keys
    )
    return records


def build_canonical_run_facts_v1(
    *,
    analysis_ref: str,
    canonical_time_window_ref: str,
    scenario_profile_ref: str | None,
    stats: object | None,
    performance: object | None,
    stats_source_ref: str | None,
    performance_source_ref: str | None,
    stats_parser_version: str = "kovaak_stats.v1",
    performance_parser_version: str = "kovaak_performance.v1",
) -> tuple[dict | None, list[dict]]:
    """Project parser objects into complete allow-listed facts and records."""
    if stats is None and performance is None:
        return None, []
    if stats is not None and stats_source_ref is None:
        raise ValueError("stats source ref is required")
    if performance is not None and performance_source_ref is None:
        raise ValueError("performance source ref is required")
    sections = {
        key: _new_section(key)
        for key in (
            "outcome_totals", "scenario", "source_quality", "challenge_configuration",
            "input_and_calibration", "weapon_aggregates", "outcome_records",
        )
    }
    contracts: list[dict] = []
    limitations: list[str] = []

    summary = dict(getattr(stats, "summary", {}) or {}) if stats is not None else {}
    config = dict(getattr(stats, "config", {}) or {}) if stats is not None else {}
    stats_presence = dict(getattr(stats, "field_presence", {}) or {}) if stats is not None else {}
    summary_presence = (
        {key: "present" for key in stats_presence.get("summary", ())}
        if stats is not None and "summary" in stats_presence
        else None
    )
    config_presence = (
        {key: "present" for key in stats_presence.get("config", ())}
        if stats is not None and "config" in stats_presence
        else None
    )
    if stats is not None:
        contracts.append(
            {
                "source_kind": "stats",
                "source_ref": stats_source_ref,
                "parser_version": stats_parser_version,
                "source_schema_version": "kovaak_stats_csv.v1",
                "recognized_schema_status": "recognized",
                "unknown_field_observability": "not_observable",
            }
        )
    header = getattr(performance, "header", None) if performance is not None else None
    profile = getattr(header, "challenge_profile", None) if header is not None else None
    header_values = vars(header) if header is not None else {}
    profile_values = vars(profile) if profile is not None else {}
    header_presence = (
        dict(getattr(header, "field_presence"))
        if header is not None and hasattr(header, "field_presence")
        else None
    )
    profile_presence = (
        dict(getattr(profile, "field_presence"))
        if profile is not None and hasattr(profile, "field_presence")
        else None
    )
    if performance is not None:
        schema_version = int(getattr(header, "schema_version", 0) or 0)
        recognized = "recognized" if schema_version == 1 else ("forward_compatible" if schema_version > 1 else "unrecognized")
        observability = getattr(performance, "unknown_field_observability", "none")
        observability = "none_detected" if observability == "none" else observability
        contracts.append(
            {
                "source_kind": "performance",
                "source_ref": performance_source_ref,
                "parser_version": performance_parser_version,
                "source_schema_version": str(schema_version) if schema_version else None,
                "recognized_schema_status": recognized,
                "unknown_field_observability": observability,
            }
        )
        if observability == "detected":
            limitations.append("performance_unknown_fields_excluded")
        if recognized == "unrecognized":
            limitations.append("performance_schema_unrecognized")

    registry_fields = source_field_registry_v1()["fields"]
    for field in registry_fields:
        group = field["source_group"]
        if group in {"stats.kill_row", "stats.weapon_aggregate", "performance.metric_change"}:
            continue
        if group == "stats.summary":
            raw_values, presence, source_exists = summary, summary_presence, stats is not None
        elif group == "stats.config":
            raw_values, presence, source_exists = config, config_presence, stats is not None
        elif group == "performance.header":
            raw_values, presence, source_exists = header_values, header_presence, performance is not None
        else:
            raw_values, presence, source_exists = profile_values, profile_presence, performance is not None
        section_key = field["canonical_key"].split(".", 1)[0]
        section = sections[section_key]
        status = _source_presence(presence, field["source_key"], raw_values) if source_exists else "source_absent"
        if status == "source_absent":
            section["source_absent_field_keys"].append(field["field_key"])
            continue
        normalized, error = _normalized_field_value(field, raw_values.get(field["source_key"]))
        if error is not None:
            _mark_omitted(section, field["field_key"], error)
            continue
        section["present_field_keys"].append(field["field_key"])
        _set_canonical_fact(section["facts"], field["canonical_key"], normalized)

    weapon_fields = [field for field in registry_fields if field["source_group"] == "stats.weapon_aggregate"]
    weapon_section = sections["weapon_aggregates"]
    aggregates = list(getattr(stats, "weapon_aggregates", ()) or ()) if stats is not None else []
    if not aggregates:
        weapon_section["source_absent_field_keys"].extend(field["field_key"] for field in weapon_fields)
    else:
        normalized_rows: list[dict] = []
        valid_weapon_keys: set[str] = set()
        for row_index, aggregate in enumerate(aggregates):
            normalized_row: dict[str, object] = {}
            for field in weapon_fields:
                normalized, error = _normalized_field_value(field, aggregate.get(field["source_key"]))
                if error is not None:
                    _mark_omitted(weapon_section, field["field_key"], f"row_{row_index}_{error}")
                    continue
                valid_weapon_keys.add(field["field_key"])
                normalized_row[field["canonical_key"]] = normalized
            normalized_rows.append(normalized_row)
        weapon_section["facts"]["records"] = normalized_rows
        weapon_section["present_field_keys"].extend(field["field_key"] for field in weapon_fields if field["field_key"] in valid_weapon_keys)
        weapon_section["source_absent_field_keys"].extend(field["field_key"] for field in weapon_fields if field["field_key"] not in valid_weapon_keys and not any(item["field_key"] == field["field_key"] for item in weapon_section["omitted_known_fields"]))

    # Arrays in the Performance profile are index-aligned to added_bots.
    if profile is not None:
        bot_count = len(getattr(profile, "added_bots", ()) or ())
        for source_key in ("bot_max_lives", "bot_teams"):
            values = getattr(profile, source_key, ()) or ()
            if profile_presence.get(source_key) == "present" and len(values) != bot_count:
                field = next(item for item in registry_fields if item["source_group"] == "performance.profile" and item["source_key"] == source_key)
                section = sections["challenge_configuration"]
                section["facts"].pop(field["canonical_key"].split(".", 1)[1], None)
                if field["field_key"] in section["present_field_keys"]:
                    section["present_field_keys"].remove(field["field_key"])
                _mark_omitted(section, field["field_key"], "array_alignment_mismatch")

    window_start_ms = 0
    record_section = sections["outcome_records"]
    records: list[dict] = []
    if stats is None:
        record_section["source_absent_field_keys"].extend(
            field["field_key"] for field in registry_fields if field["source_group"] == "stats.kill_row"
        )
    else:
        records.extend(_build_stats_outcome_records(stats, source_ref=stats_source_ref, window_start_ms=window_start_ms, record_section=record_section))
    if performance is None:
        record_section["source_absent_field_keys"].extend(
            field["field_key"] for field in registry_fields if field["source_group"] == "performance.metric_change"
        )
    else:
        records.extend(_build_performance_outcome_records(performance, source_ref=performance_source_ref, window_start_ms=window_start_ms, record_section=record_section))
    records.sort(key=lambda item: (item["canonical_time_ms"], item["source_priority"], item["source_event_index"]))

    for section in sections.values():
        section["present_field_keys"] = sorted(set(section["present_field_keys"]))
        section["source_absent_field_keys"] = sorted(set(section["source_absent_field_keys"]))
        section["omitted_known_fields"].sort(key=lambda item: (item["field_key"], item["reason"]))
    is_partial = bool(limitations) or any(section["completeness"] == "partial" for section in sections.values())
    facts = {
        "schema_version": "canonical_run_facts.v1",
        "analysis_ref": analysis_ref,
        "scenario_profile_ref": scenario_profile_ref,
        "canonical_time_window_ref": canonical_time_window_ref,
        "field_registry_version": FIELD_REGISTRY_VERSION,
        "source_contracts": contracts,
        "sections": list(sections.values()),
        "outcome_record_sets": {
            "stats_kill_rows_ref": f"{analysis_ref}:stats-kill-rows" if stats is not None else None,
            "performance_metric_changes_ref": f"{analysis_ref}:performance-metric-changes" if performance is not None else None,
        },
        "completeness": "partial" if is_partial else "complete_allowlisted",
        "unknown_field_policy": "excluded",
        "limitations": limitations,
    }
    validate_canonical_run_facts_v1(facts)
    return facts, records


def build_analysis_evidence_artifact_v1(
    *,
    analysis_ref: str,
    canonical_time_window: dict,
    scenario_profile_ref: str | None,
    stats: object | None,
    performance: object | None,
    stats_source_ref: str | None,
    performance_source_ref: str | None,
    stats_parser_version: str = "kovaak_stats.v1",
    performance_parser_version: str = "kovaak_performance.v1",
) -> dict:
    start_ms, end_ms = _window_bounds(canonical_time_window)
    window_ref = f"{analysis_ref}:canonical-window"
    facts, records = build_canonical_run_facts_v1(
        analysis_ref=analysis_ref,
        canonical_time_window_ref=window_ref,
        scenario_profile_ref=scenario_profile_ref,
        stats=stats,
        performance=performance,
        stats_source_ref=stats_source_ref,
        performance_source_ref=performance_source_ref,
        stats_parser_version=stats_parser_version,
        performance_parser_version=performance_parser_version,
    )
    if start_ms:
        for record in records:
            record["canonical_time_ms"] += start_ms
    excluded_field_keys: set[str] = set()
    bounded_records: list[dict] = []
    source_fields = source_field_registry_v1()["fields"]
    performance_field_keys = {
        f"performance.{field['source_key']}": field["field_key"]
        for field in source_fields
        if field["source_group"] == "performance.metric_change"
    }
    stats_field_keys = {
        f"stats.kill.{field['canonical_key']}": field["field_key"]
        for field in source_fields
        if field["source_group"] == "stats.kill_row"
        and field["source_key"] != "Timestamp"
    }
    stats_timestamp_key = next(
        field["field_key"]
        for field in source_fields
        if field["source_group"] == "stats.kill_row"
        and field["source_key"] == "Timestamp"
    )
    for record in records:
        if start_ms <= record["canonical_time_ms"] < end_ms:
            bounded_records.append(record)
            continue
        if record["source_priority"] == 10:
            excluded_field_keys.add(stats_timestamp_key)
            excluded_field_keys.update(
                stats_field_keys[value["metric_key"]]
                for value in record["values"]
            )
        else:
            excluded_field_keys.update(
                performance_field_keys[value["metric_key"]]
                for value in record["values"]
            )
    records = bounded_records
    artifact_limitations: list[str] = []
    if excluded_field_keys and facts is not None:
        record_section = next(
            section
            for section in facts["sections"]
            if section["section_key"] == "outcome_records"
        )
        for field_key in sorted(excluded_field_keys):
            _mark_omitted(
                record_section,
                field_key,
                "record_outside_canonical_window",
            )
        facts["completeness"] = "partial"
        if "outcome_records_outside_canonical_window" not in facts["limitations"]:
            facts["limitations"].append(
                "outcome_records_outside_canonical_window",
            )
        artifact_limitations.append("outcome_records_outside_canonical_window")
    artifact = {
        "schema_version": "analysis_evidence_artifact.v1",
        "analysis_ref": analysis_ref,
        "canonical_time_window": copy.deepcopy(canonical_time_window),
        "canonical_run_facts": facts,
        "normalized_outcome_records": records,
        "signal_bundles": [],
        "event_bundles": [],
        "metric_records": [],
        "evidence_segments": [],
        "sample_sets": [],
        "limitations": artifact_limitations,
    }
    return validate_analysis_evidence_artifact_v1(artifact)


def _query_digest(*, scope: str, segment_ref: str | None, selected_series: list[str]) -> str:
    payload = {"scope": scope, "segment_ref": segment_ref, "selected_series": selected_series}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_page_descriptor_v1(*, owner_id: str, analysis_ref: str, evidence_revision: str, scope: str, segment_ref: str | None, selected_series: list[str], offset: int) -> dict:
    _safe_ref("page.owner_id", owner_id)
    _safe_ref("page.analysis_ref", analysis_ref)
    _safe_ref("page.evidence_revision", evidence_revision)
    if scope not in {"whole_run", "evidence_segment"}:
        raise ValueError("invalid page scope")
    if (scope == "whole_run" and segment_ref is not None) or (
        scope == "evidence_segment" and segment_ref is None
    ):
        raise ValueError("page scope and segment ref are inconsistent")
    if segment_ref is not None:
        _safe_ref("page.segment_ref", segment_ref)
    selected_series = _safe_string_list(
        "page.selected_series", selected_series, allow_empty=False,
    )
    if len(selected_series) > 8 or not all(
        EvidenceKeyRegistry().allows_outcome_metric(item)
        for item in selected_series
    ):
        raise ValueError("page selected series is not registered")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("page offset must be non-negative")
    return {
        "schema_version": "evidence_page_descriptor.v1",
        "owner_id": owner_id,
        "analysis_ref": analysis_ref,
        "evidence_revision": evidence_revision,
        "scope": scope,
        "segment_ref": segment_ref,
        "selected_series": list(selected_series),
        "offset": offset,
        "query_digest": _query_digest(scope=scope, segment_ref=segment_ref, selected_series=selected_series),
        "sort_version": "canonical_outcome_sort.v1",
        "contract_version": "normalized_outcome_timeline.v1",
    }


def page_normalized_outcomes(
    records: Iterable[dict],
    *,
    analysis_ref: str,
    canonical_time_window_ref: str,
    descriptor: dict,
    byte_limit: int = 24 * 1024,
    page_size: int = 120,
    segment_bounds: tuple[int, int] | None = None,
) -> dict:
    if (
        isinstance(byte_limit, bool)
        or not isinstance(byte_limit, int)
        or not 0 < byte_limit <= 24 * 1024
        or isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= 120
    ):
        raise ValueError("outcome page budget is invalid")
    _expect_exact(
        descriptor,
        {
            "schema_version", "owner_id", "analysis_ref", "evidence_revision",
            "scope", "segment_ref", "selected_series", "offset", "query_digest",
            "sort_version", "contract_version",
        },
        "page descriptor",
    )
    if (
        descriptor["schema_version"] != "evidence_page_descriptor.v1"
        or descriptor["analysis_ref"] != analysis_ref
        or descriptor["contract_version"] != "normalized_outcome_timeline.v1"
    ):
        raise ValueError("page descriptor does not match analysis")
    expected_digest = _query_digest(
        scope=descriptor["scope"],
        segment_ref=descriptor["segment_ref"],
        selected_series=descriptor["selected_series"],
    )
    if (
        descriptor["query_digest"] != expected_digest
        or descriptor["sort_version"] != "canonical_outcome_sort.v1"
    ):
        raise ValueError("page descriptor query drift")
    build_page_descriptor_v1(
        owner_id=descriptor["owner_id"],
        analysis_ref=analysis_ref,
        evidence_revision=descriptor["evidence_revision"],
        scope=descriptor["scope"],
        segment_ref=descriptor["segment_ref"],
        selected_series=descriptor["selected_series"],
        offset=descriptor["offset"],
    )
    selected_series = set(descriptor["selected_series"])
    filtered_records: list[dict] = []
    if descriptor["scope"] == "evidence_segment":
        if (
            not isinstance(segment_bounds, tuple)
            or len(segment_bounds) != 2
            or any(
                isinstance(item, bool) or not isinstance(item, int)
                for item in segment_bounds
            )
            or segment_bounds[0] >= segment_bounds[1]
        ):
            raise ValueError("evidence segment page requires authorized bounds")
    elif segment_bounds is not None:
        raise ValueError("whole-run page cannot use segment bounds")
    for index, record in enumerate(records):
        _validate_outcome_record(record, index)
        if segment_bounds is not None and not (
            segment_bounds[0]
            <= record["canonical_time_ms"]
            < segment_bounds[1]
        ):
            continue
        projected = copy.deepcopy(record)
        projected["values"] = [
            value
            for value in projected["values"]
            if value["metric_key"] in selected_series
        ]
        if projected["values"]:
            filtered_records.append(projected)
    ordered = sorted(
        filtered_records,
        key=lambda item: (
            item["canonical_time_ms"],
            item["source_priority"],
            item["source_event_index"],
        ),
    )
    offset = descriptor["offset"]
    if offset > len(ordered):
        raise ValueError("page offset is outside record set")
    selected = ordered[offset : offset + min(page_size, 120)]

    def response_for(page_records: list[dict]) -> dict:
        has_more = offset + len(page_records) < len(ordered)
        timeline = {
            "schema_version": "normalized_outcome_timeline.v1",
            "analysis_ref": analysis_ref,
            "scope": descriptor["scope"],
            "segment_ref": descriptor["segment_ref"],
            "canonical_time_window_ref": canonical_time_window_ref,
            "mode": "exact_page",
            "resolution": "source_native",
            "selected_series": list(descriptor["selected_series"]),
            "overview_series": None,
            "records": page_records,
            "event_refs": [],
            "completeness": "paged" if has_more else "complete",
            "next_cursor": None,
            "limitations": [],
        }
        next_descriptor = None
        if has_more:
            next_descriptor = build_page_descriptor_v1(
                owner_id=descriptor["owner_id"],
                analysis_ref=analysis_ref,
                evidence_revision=descriptor["evidence_revision"],
                scope=descriptor["scope"],
                segment_ref=descriptor["segment_ref"],
                selected_series=descriptor["selected_series"],
                offset=offset + len(page_records),
            )
        return {
            "timeline": timeline,
            # Internal only. Task 4 wraps this in a bridge-bound opaque cursor.
            "next_page_descriptor": next_descriptor,
        }

    response = response_for(selected)
    while selected and len(
        json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ) > byte_limit:
        selected.pop()
        response = response_for(selected)
    if ordered[offset:] and not selected:
        raise ValueError("one normalized outcome record exceeds page byte limit")
    validate_normalized_outcome_timeline_v1(response["timeline"])
    return response


__all__ = [
    "EVIDENCE_CONTRACT_VERSION", "FIELD_REGISTRY_VERSION", "EvidenceKeyRegistry",
    "UnsupportedEvidenceContractVersion", "build_analysis_evidence_artifact_v1",
    "build_canonical_run_facts_v1", "build_page_descriptor_v1",
    "build_processed_event_table_catalog", "build_processed_event_table_catalog_v1",
    "dynamic_processed_field_catalog_v1",
    "tracking_processed_field_catalog_v1", "target_switching_processed_field_catalog_v1",
    "page_normalized_outcomes", "source_field_registry_v1",
    "validate_analysis_evidence_artifact", "validate_analysis_evidence_artifact_v1",
    "validate_analysis_evidence_artifact_v2", "validate_canonical_run_facts_v1",
    "validate_event_bundle", "validate_event_bundle_v1", "validate_event_bundle_v2",
    "validate_evidence_segment_v1",
    "validate_metric_record_v1", "validate_normalized_outcome_timeline_v1",
    "validate_processed_event_table_v1",
    "validate_outcome_association_rule_binding_v1",
    "validate_signal_bundle_v1", "validate_source_field_registry_v1",
]
