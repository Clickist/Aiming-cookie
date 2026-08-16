/**
 * Evidence field catalogs and processed event table builders.
 *
 * Ported from kovaak_tracker/analysis_evidence.py to provide typed
 * field catalogs, catalog builder functions, and a simplified
 * EvidenceKeyRegistry for the coach-runtime evidence commands.
 */

// ── Types ──

export type FieldRole =
  | "identity"
  | "timing"
  | "condition"
  | "metric"
  | "outcome"
  | "quality";

export type ValueType =
  | "number"
  | "string"
  | "ref"
  | "string_list"
  | "boolean";

export type ExpectedDirection =
  | "lower_better"
  | "higher_better"
  | "target_band"
  | "descriptive_only"
  | "comparison_only";

/** Raw 6-tuple entry for static processed event fields. */
type StaticFieldTuple = readonly [
  field_key: string,
  role: FieldRole,
  value_type: ValueType,
  unit: string | null,
  metric_key: string | null,
  metric_version: string | null,
];

/** Raw 7-tuple entry for other processed event fields. */
type FieldTuple = readonly [
  field_key: string,
  role: FieldRole,
  value_type: ValueType,
  unit: string | null,
  metric_key: string | null,
  metric_version: string | null,
  expected_direction: ExpectedDirection,
];

/** Output catalog entry shared by all families. */
export type ProcessedFieldCatalogEntry = {
  field_key: string;
  role: FieldRole;
  value_type: ValueType;
  unit: string | null;
  metric_key: string | null;
  metric_version: string | null;
  expected_direction: ExpectedDirection;
  limitations: string[];
};

// ── Static processed event fields (6-tuple) ──

export const STATIC_PROCESSED_EVENT_FIELDS_V1: readonly StaticFieldTuple[] = [
  ["event_id", "identity", "ref", null, null, null],
  ["start_ms", "timing", "number", "ms", null, null],
  ["end_ms", "timing", "number", "ms", null, null],
  ["confidence", "quality", "number", "ratio", null, null],
  ["limitations", "quality", "string_list", null, null, null],
  ["legacy_event_ref", "identity", "ref", null, null, null],
  ["peak_ms", "timing", "number", "ms", null, null],
  ["settle_end_ms", "timing", "number", "ms", null, null],
  ["quality", "quality", "string", null, null, null],
  ["movement_duration_ms", "metric", "number", "ms", "static_clicking.movement_duration_ms", "native_flicking.v1"],
  ["time_to_peak_ms", "metric", "number", "ms", "static_clicking.time_to_peak_ms", "native_flicking.v1"],
  ["accel_duration_ms", "metric", "number", "ms", "static_clicking.accel_duration_ms", "native_flicking.v1"],
  ["decel_duration_ms", "metric", "number", "ms", "static_clicking.decel_duration_ms", "native_flicking.v1"],
  ["settle_duration_ms", "metric", "number", "ms", "static_clicking.settle_duration_ms", "native_flicking.v1"],
  ["decel_frac", "metric", "number", "dimensionless", "static_clicking.decel_frac", "native_flicking.v1"],
  ["peak_position_pct", "metric", "number", "percent", "static_clicking.peak_position_pct", "native_flicking.v1"],
  ["peak_speed", "metric", "number", "raw_counts_per_second", "static_clicking.peak_speed", "native_flicking.v1"],
  ["path_length", "metric", "number", "raw_counts", "static_clicking.flick_path_length", "native_flicking.v1"],
  ["displacement", "metric", "number", "raw_counts", "static_clicking.displacement", "native_flicking.v1"],
  ["path_efficiency", "metric", "number", "dimensionless", "static_clicking.path_efficiency", "native_flicking.v1"],
  ["straightness", "metric", "number", "dimensionless", "static_clicking.straightness", "native_flicking.v1"],
  ["reverse_ratio", "metric", "number", "dimensionless", "static_clicking.reverse_ratio", "native_flicking.v1"],
  ["direction_reverse_ratio", "metric", "number", "dimensionless", "static_clicking.direction_reverse_ratio", "native_flicking.v1"],
  ["corrective_count", "metric", "number", "count", "static_clicking.corrective_count", "native_flicking.v1"],
  ["submovement_count", "metric", "number", "count", "static_clicking.submovement_count", "native_flicking.v1"],
  ["trough_depth_ratio", "metric", "number", "dimensionless", "static_clicking.trough_depth_ratio", "native_flicking.v1"],
  ["submovement_overlap", "metric", "number", "dimensionless", "static_clicking.submovement_overlap", "native_flicking.v1"],
  ["sparc", "metric", "number", "dimensionless", "static_clicking.sparc", "native_flicking.sparc.v2"],
] as const;

export const STATIC_PROCESSED_INDEX_FIELDS_V1: readonly string[] = [
  "movement_duration_ms", "peak_speed", "path_efficiency", "reverse_ratio",
  "corrective_count", "submovement_count", "sparc", "quality",
] as const;

const STATIC_FIELD_LIMITATIONS: Record<string, string[]> = {
  reverse_ratio: ["reacceleration_ratio_is_discrete_speed_delta_sign"],
  direction_reverse_ratio: ["direction_reverse_ratio_is_raw_path_sign_change"],
  corrective_count: ["corrective_counts_use_discrete_direction_sign_runs"],
  submovement_count: ["corrective_counts_use_discrete_direction_sign_runs"],
  trough_depth_ratio: ["trough_depth_ratio_not_temporal_overlap"],
  submovement_overlap: ["trough_depth_ratio_not_temporal_overlap"],
  sparc: ["sparc_cross_polling_comparability_unverified"],
};

// ── Dynamic processed event fields (7-tuple) ──

export const DYNAMIC_PROCESSED_EVENT_FIELDS_V1: readonly FieldTuple[] = [
  ["event_id", "identity", "ref", null, null, null, "descriptive_only"],
  ["start_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["end_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["confidence", "quality", "number", "ratio", null, null, "descriptive_only"],
  ["limitations", "quality", "string_list", null, null, null, "descriptive_only"],
  ["click_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["target_track_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["click_time_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["target_radius", "condition", "number", "px", null, null, "descriptive_only"],
  ["target_association_basis", "quality", "string", null, null, null, "descriptive_only"],
  ["normalized_click_error", "metric", "number", "visible_radius", "dynamic_clicking.normalized_click_error", "dynamic_clicking.normalized_click_error.v1", "comparison_only"],
  ["miss_vector_x", "metric", "number", "px", null, null, "descriptive_only"],
  ["miss_vector_y", "metric", "number", "px", null, null, "descriptive_only"],
  ["target_speed", "condition", "number", "px_per_ms", null, null, "descriptive_only"],
  ["target_acceleration", "condition", "number", "px_per_ms2", null, null, "descriptive_only"],
  ["relative_velocity_x", "metric", "number", "px_per_ms", null, null, "comparison_only"],
  ["relative_velocity_y", "metric", "number", "px_per_ms", null, null, "comparison_only"],
  ["relative_velocity_magnitude", "metric", "number", "px_per_ms", "dynamic_clicking.relative_velocity", "dynamic_clicking.relative_velocity.v1", "target_band"],
  ["signed_lead_lag", "metric", "number", "visible_radius", null, null, "descriptive_only"],
  ["lead_lag_descriptor", "condition", "string", null, null, null, "descriptive_only"],
  ["acquisition_start_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["acquisition_time_ms", "timing", "number", "ms", "dynamic_clicking.acquisition_time_ms", "dynamic_clicking.acquisition_time_ms.v1", "comparison_only"],
  ["change_state", "condition", "string", null, null, null, "descriptive_only"],
  ["target_motion_class", "condition", "string", null, null, null, "descriptive_only"],
  ["association_availability", "quality", "string", null, null, null, "descriptive_only"],
  ["outcome_available", "quality", "boolean", null, null, null, "descriptive_only"],
  ["outcome_success", "outcome", "number", "ratio", "dynamic_clicking.target_state_accuracy", "dynamic_clicking.target_state_accuracy.v1", "higher_better"],
  ["condition_ref", "condition", "ref", null, null, null, "descriptive_only"],
] as const;

// ── Tracking processed event fields (7-tuple) ──

export const TRACKING_COMMON_PROCESSED_EVENT_FIELDS_V1: readonly FieldTuple[] = [
  ["event_id", "identity", "ref", null, null, null, "descriptive_only"],
  ["start_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["end_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["confidence", "quality", "number", "ratio", null, null, "descriptive_only"],
  ["limitations", "quality", "string_list", null, null, null, "descriptive_only"],
  ["target_track_ref", "identity", "ref", null, null, null, "descriptive_only"],
] as const;

export const TRACKING_PROCESSED_EVENT_FIELDS_V1: Readonly<Record<string, readonly FieldTuple[]>> = {
  tracking_episode: [
    ["condition_ref", "condition", "ref", null, null, null, "descriptive_only"],
    ["sample_count", "quality", "number", "count", null, null, "descriptive_only"],
    ["usable_sample_count", "quality", "number", "count", null, null, "descriptive_only"],
    ["target_relative_error_px", "metric", "number", "px", "continuous_tracking.target_relative_error_px", "continuous_tracking.target_relative_error_px.v1", "comparison_only"],
    ["time_in_radius_ratio", "metric", "number", "ratio", "continuous_tracking.time_in_radius_ratio", "continuous_tracking.time_in_radius_ratio.v1", "higher_better"],
    ["loss_count", "metric", "number", "count", "continuous_tracking.loss_count", "continuous_tracking.loss_count.v1", "comparison_only"],
    ["correction_burden", "metric", "number", "count", "continuous_tracking.correction_direction_reversal_count", "continuous_tracking.correction_direction_reversal_count.v1", "comparison_only"],
    ["sparc", "metric", "number", "dimensionless", "continuous_tracking.sparc", "continuous_tracking.sparc.v1", "comparison_only"],
    ["phase_lag_ms", "metric", "number", "ms", "continuous_tracking.phase_lag_ms", "continuous_tracking.phase_lag_ms.v1", "comparison_only"],
    ["velocity_gain", "metric", "number", "ratio", "continuous_tracking.velocity_gain", "continuous_tracking.velocity_gain.v1", "target_band"],
    ["coherence", "quality", "number", "ratio", "continuous_tracking.coherence", "continuous_tracking.coherence.v1", "descriptive_only"],
  ],
  tracking_fixed_window: [
    ["condition_ref", "condition", "ref", null, null, null, "descriptive_only"],
    ["sample_count", "quality", "number", "count", null, null, "descriptive_only"],
    ["usable_sample_count", "quality", "number", "count", null, null, "descriptive_only"],
    ["target_relative_error_px", "metric", "number", "px", "continuous_tracking.target_relative_error_px", "continuous_tracking.target_relative_error_px.v1", "comparison_only"],
    ["time_in_radius_ratio", "metric", "number", "ratio", "continuous_tracking.time_in_radius_ratio", "continuous_tracking.time_in_radius_ratio.v1", "higher_better"],
    ["correction_burden", "metric", "number", "count", "continuous_tracking.correction_direction_reversal_count", "continuous_tracking.correction_direction_reversal_count.v1", "comparison_only"],
    ["sparc", "metric", "number", "dimensionless", "continuous_tracking.sparc", "continuous_tracking.sparc.v1", "comparison_only"],
  ],
  tracking_loss: [
    ["duration_ms", "timing", "number", "ms", "continuous_tracking.loss_duration_ms", "continuous_tracking.loss_duration_ms.v1", "comparison_only"],
  ],
  tracking_reacquisition: [
    ["loss_ref", "identity", "ref", null, null, null, "descriptive_only"],
    ["reacquisition_latency_ms", "timing", "number", "ms", "continuous_tracking.reacquisition_latency_ms", "continuous_tracking.reacquisition_latency_ms.v1", "comparison_only"],
  ],
  tracking_change_response: [
    ["change_ref", "identity", "ref", null, null, null, "descriptive_only"],
    ["observed_change_response_ms", "timing", "number", "ms", "continuous_tracking.observed_change_response_ms", "continuous_tracking.observed_change_response_ms.v1", "comparison_only"],
    ["alignment_latency_ms", "quality", "number", "ms", "continuous_tracking.alignment_latency_ms", "continuous_tracking.alignment_latency_ms.v1", "descriptive_only"],
    ["post_change_error_px", "metric", "number", "px", null, null, "comparison_only"],
  ],
};

// ── Target switching processed event fields (7-tuple) ──

export const TARGET_SWITCHING_PROCESSED_EVENT_FIELDS_V1: readonly FieldTuple[] = [
  ["event_id", "identity", "ref", null, null, null, "descriptive_only"],
  ["start_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["end_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["confidence", "quality", "number", "ratio", null, null, "descriptive_only"],
  ["limitations", "quality", "string_list", null, null, null, "descriptive_only"],
  ["chain_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["classification", "quality", "string", null, null, null, "descriptive_only"],
  ["previous_outcome_association_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["previous_target_track_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["previous_outcome_time_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["leave_time_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["candidate_count", "condition", "number", "count", null, null, "descriptive_only"],
  ["selection_observation_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["selected_target_track_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["next_target_track_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["acquire_time_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["settle_time_ms", "timing", "number", "ms", null, null, "descriptive_only"],
  ["transition_time_ms", "metric", "number", "ms", "target_switching.transition_time_ms", "target_switching.transition_time_ms.v1", "comparison_only"],
  ["transition_distance_px", "condition", "number", "px", "target_switching.transition_distance_px", "target_switching.transition_distance_px.v1", "comparison_only"],
  ["transition_direction_deg", "condition", "number", "deg", null, null, "descriptive_only"],
  ["transition_path_length_px", "metric", "number", "px", null, null, "comparison_only"],
  ["path_efficiency", "metric", "number", "ratio", "target_switching.path_efficiency", "target_switching.path_efficiency.v1", "comparison_only"],
  ["settle_duration_ms", "metric", "number", "ms", "target_switching.settle_duration_ms", "target_switching.settle_duration_ms.v1", "comparison_only"],
  ["first_shot_event_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["first_shot_latency_ms", "metric", "number", "ms", "target_switching.first_shot_latency_ms", "target_switching.first_shot_latency_ms.v1", "comparison_only"],
  ["first_damage_event_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["first_damage_latency_ms", "metric", "number", "ms", "target_switching.first_damage_latency_ms", "target_switching.first_damage_latency_ms.v1", "comparison_only"],
  ["carry_over_overshoot", "metric", "boolean", null, "target_switching.carry_over_overshoot_ratio", "target_switching.carry_over_overshoot_ratio.v1", "comparison_only"],
  ["carry_over_overshoot_observation_ref", "identity", "ref", null, null, null, "descriptive_only"],
  ["terminal_correction_observed", "metric", "boolean", null, "target_switching.terminal_correction_ratio", "target_switching.terminal_correction_ratio.v1", "comparison_only"],
  ["terminal_correction_observation_ref", "identity", "ref", null, null, null, "descriptive_only"],
] as const;

// ── Field catalog functions ──

export function staticProcessedFieldCatalogV1(): ProcessedFieldCatalogEntry[] {
  return STATIC_PROCESSED_EVENT_FIELDS_V1.map(
    ([field_key, role, value_type, unit, metric_key, metric_version]) => ({
      field_key,
      role,
      value_type,
      unit,
      metric_key,
      metric_version,
      expected_direction: (metric_key ? "comparison_only" : "descriptive_only") as ExpectedDirection,
      limitations: STATIC_FIELD_LIMITATIONS[field_key] ?? [],
    }),
  );
}

export function dynamicProcessedFieldCatalogV1(): ProcessedFieldCatalogEntry[] {
  return DYNAMIC_PROCESSED_EVENT_FIELDS_V1.map(
    ([field_key, role, value_type, unit, metric_key, metric_version, expected_direction]) => ({
      field_key,
      role,
      value_type,
      unit,
      metric_key,
      metric_version,
      expected_direction,
      limitations: [],
    }),
  );
}

export function trackingProcessedFieldCatalogV1(
  eventKind: string,
): ProcessedFieldCatalogEntry[] {
  const specific = TRACKING_PROCESSED_EVENT_FIELDS_V1[eventKind];
  if (!specific) {
    throw new Error("continuous tracking processed event kind is unsupported");
  }
  return [...TRACKING_COMMON_PROCESSED_EVENT_FIELDS_V1, ...specific].map(
    ([field_key, role, value_type, unit, metric_key, metric_version, expected_direction]) => ({
      field_key,
      role,
      value_type,
      unit,
      metric_key,
      metric_version,
      expected_direction,
      limitations: [],
    }),
  );
}

export function targetSwitchingProcessedFieldCatalogV1(
  eventKind: string,
): ProcessedFieldCatalogEntry[] {
  if (eventKind !== "switch_chain" && eventKind !== "unclassified_discrete_acquisition") {
    throw new Error("target switching processed event kind is unsupported");
  }
  return TARGET_SWITCHING_PROCESSED_EVENT_FIELDS_V1.map(
    ([field_key, role, value_type, unit, metric_key, metric_version, expected_direction]) => ({
      field_key,
      role,
      value_type,
      unit,
      metric_key,
      metric_version,
      expected_direction,
      limitations: [],
    }),
  );
}

// ── Processed event table types ──

export type ProcessedEventTableV1 = {
  schema_version: "processed_event_table.v1";
  table_ref: string;
  analysis_ref: string;
  analyzer_ref: string;
  family: string;
  event_kind: string;
  row_count: number;
  included_count: number;
  excluded_count: number;
  completeness: "complete" | "partial" | "unavailable";
  field_catalog: ProcessedFieldCatalogEntry[];
  index_fields: string[];
  rows_ref: string;
  limitations: string[];
};

type ArtifactEvent = {
  event_id: string;
  event_kind: string;
  start_ms: number;
  end_ms: number;
  confidence: number;
  attributes: Record<string, unknown>;
  limitations: string[];
  [key: string]: unknown;
};

type EventBundle = {
  schema_version: string;
  analysis_ref: string;
  events: ArtifactEvent[];
  outcome_associations: unknown[];
  [key: string]: unknown;
};

type EvidenceArtifact = {
  schema_version: string;
  analysis_ref: string;
  event_bundles: EventBundle[];
  [key: string]: unknown;
};

// ── Table catalog builder (v1) ──

type TableSpec = {
  events: ArtifactEvent[];
  eventKind: string;
  analyzerRef: string;
  family: string;
  fieldCatalog: ProcessedFieldCatalogEntry[];
  indexFields: string[];
};

export function buildProcessedEventTableCatalogV1(
  artifact: EvidenceArtifact,
): ProcessedEventTableV1[] {
  if (artifact.schema_version !== "analysis_evidence_artifact.v1") {
    throw new Error(
      `Unsupported evidence contract version: ${artifact.schema_version}`,
    );
  }
  const analysisRef = artifact.analysis_ref;
  const bundles = artifact.event_bundles;
  if (!Array.isArray(bundles)) {
    throw new Error("analysis evidence event bundles must be a list");
  }

  // Filter bundles to those bound to this analysis
  const validatedBundles = bundles.filter(
    (b) => b.analysis_ref === analysisRef,
  );

  // Collect all events
  const allEvents: ArtifactEvent[] = [];
  for (const bundle of validatedBundles) {
    allEvents.push(...bundle.events);
  }

  const eventsByKind = (kind: string): ArtifactEvent[] =>
    allEvents.filter((e) => e.event_kind === kind);

  const tableSpecs: TableSpec[] = [];

  // Static flick
  const staticEvents = eventsByKind("static_flick");
  if (staticEvents.length) {
    tableSpecs.push({
      events: staticEvents,
      eventKind: "static_flick",
      analyzerRef: "native_flicking.v1",
      family: "static_clicking",
      fieldCatalog: staticProcessedFieldCatalogV1(),
      indexFields: [...STATIC_PROCESSED_INDEX_FIELDS_V1],
    });
  }

  // Dynamic click
  const dynamicEvents = eventsByKind("dynamic_click");
  if (dynamicEvents.length) {
    tableSpecs.push({
      events: dynamicEvents,
      eventKind: "dynamic_click",
      analyzerRef: "dynamic_clicking.v1",
      family: "dynamic_clicking",
      fieldCatalog: dynamicProcessedFieldCatalogV1(),
      indexFields: ["click_ref", "target_track_ref", "click_time_ms", "change_state"],
    });
  }

  // Continuous tracking — iterate kinds in sorted order
  for (const eventKind of Object.keys(TRACKING_PROCESSED_EVENT_FIELDS_V1).sort()) {
    const trackingEvents = eventsByKind(eventKind);
    if (!trackingEvents.length) continue;
    tableSpecs.push({
      events: trackingEvents,
      eventKind,
      analyzerRef: "continuous_tracking.v1",
      family: "continuous_tracking",
      fieldCatalog: trackingProcessedFieldCatalogV1(eventKind),
      indexFields: ["start_ms", "end_ms", "target_track_ref"],
    });
  }

  // Target switching
  for (const eventKind of ["switch_chain", "unclassified_discrete_acquisition"] as const) {
    const switchingEvents = eventsByKind(eventKind);
    if (!switchingEvents.length) continue;
    tableSpecs.push({
      events: switchingEvents,
      eventKind,
      analyzerRef: "target_switching.v1",
      family: "target_switching",
      fieldCatalog: targetSwitchingProcessedFieldCatalogV1(eventKind),
      indexFields: ["start_ms", "end_ms", "previous_target_track_ref", "next_target_track_ref"],
    });
  }

  // Build tables
  const tables: ProcessedEventTableV1[] = [];
  for (const spec of tableSpecs) {
    const { events, eventKind, analyzerRef, family, fieldCatalog, indexFields } = spec;

    // Check for duplicate event ids
    const eventIds = events.map((e) => e.event_id);
    if (new Set(eventIds).size !== eventIds.length) {
      throw new Error("processed event table contains duplicate event refs");
    }

    const tableRef = `${analysisRef}:table:${eventKind}`;
    const limitations = [...new Set(
      events.flatMap((e) => e.limitations ?? []),
    )].sort();

    const completeness: ProcessedEventTableV1["completeness"] =
      (family === "continuous_tracking" || family === "target_switching") && limitations.length
        ? "partial"
        : "complete";

    tables.push({
      schema_version: "processed_event_table.v1",
      table_ref: tableRef,
      analysis_ref: analysisRef,
      analyzer_ref: analyzerRef,
      family,
      event_kind: eventKind,
      row_count: events.length,
      included_count: events.length,
      excluded_count: 0,
      completeness,
      field_catalog: fieldCatalog,
      index_fields: indexFields,
      rows_ref: tableRef,
      limitations,
    });
  }

  return tables;
}

// ── Table catalog builder (v1 + v2 dispatcher) ──

export function buildProcessedEventTableCatalog(
  artifact: EvidenceArtifact,
): ProcessedEventTableV1[] {
  const version = artifact.schema_version;

  if (version === "analysis_evidence_artifact.v1") {
    return buildProcessedEventTableCatalogV1(artifact);
  }

  if (version !== "analysis_evidence_artifact.v2") {
    throw new Error(`Unsupported evidence contract version: ${version}`);
  }

  // V2: filter to event_bundle.v1 only, then delegate to v1 builder
  const legacyView: EvidenceArtifact = {
    ...structuredClone(artifact),
    schema_version: "analysis_evidence_artifact.v1",
    event_bundles: artifact.event_bundles.filter(
      (b) => b.schema_version === "event_bundle.v1",
    ),
  };
  return buildProcessedEventTableCatalogV1(legacyView);
}

// ── Evidence key registry ──

export type EvidenceExtension = {
  schema_version: string;
  extension_ref: string;
  channel_keys: string[];
  event_kinds: Record<string, string[]>;
  metric_keys: string[];
  segment_kinds: string[];
};

const CHANNEL_RE =
  /^target\.[A-Za-z0-9._:-]+\.(?:position_[xy]|velocity_[xy]|acceleration_[xy]|visible_radius|hitbox)$/;

// ── Extension definitions ──

export const NATIVE_STATIC_EVIDENCE_EXTENSION_V1: EvidenceExtension = {
  schema_version: "analysis_evidence_extension.v1",
  extension_ref: "native-static-clicking@1",
  channel_keys: [],
  event_kinds: {
    static_flick: [
      "legacy_event_ref", "peak_ms", "settle_end_ms", "quality",
      "movement_duration_ms", "time_to_peak_ms", "accel_duration_ms",
      "decel_duration_ms", "settle_duration_ms", "decel_frac",
      "peak_position_pct", "peak_speed", "path_length", "displacement",
      "path_efficiency", "straightness", "reverse_ratio",
      "direction_reverse_ratio", "corrective_count", "submovement_count",
      "trough_depth_ratio", "submovement_overlap", "sparc",
    ],
  },
  metric_keys: [
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
  segment_kinds: [],
};

export const DYNAMIC_CLICKING_EVIDENCE_EXTENSION_V1: EvidenceExtension = {
  schema_version: "analysis_evidence_extension.v1",
  extension_ref: "dynamic-clicking@1",
  channel_keys: [],
  event_kinds: {
    dynamic_click: [
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
    motion_predictability_evidence: [
      "segment_ref", "model_ref", "model_version", "fit_metric",
      "fit_value", "threshold_ref", "acceptance",
    ],
  },
  metric_keys: [
    "dynamic_clicking.normalized_click_error",
    "dynamic_clicking.acquisition_time_ms",
    "dynamic_clicking.relative_velocity",
    "dynamic_clicking.target_state_accuracy",
    "dynamic_clicking.predictive_lead",
  ],
  segment_kinds: [],
};

export const CONTINUOUS_TRACKING_EVIDENCE_EXTENSION_V1: EvidenceExtension = {
  schema_version: "analysis_evidence_extension.v1",
  extension_ref: "continuous-tracking@1",
  channel_keys: [],
  event_kinds: {
    tracking_episode: [
      "target_track_ref", "condition_ref", "sample_count",
      "usable_sample_count", "target_relative_error_px",
      "time_in_radius_ratio", "loss_count", "correction_burden",
      "sparc", "phase_lag_ms", "velocity_gain", "coherence",
    ],
    tracking_fixed_window: [
      "target_track_ref", "condition_ref", "sample_count",
      "usable_sample_count", "target_relative_error_px",
      "time_in_radius_ratio", "correction_burden", "sparc",
    ],
    tracking_loss: ["target_track_ref", "duration_ms"],
    tracking_reacquisition: [
      "target_track_ref", "loss_ref", "reacquisition_latency_ms",
    ],
    tracking_change_response: [
      "target_track_ref", "change_ref", "observed_change_response_ms",
      "alignment_latency_ms", "post_change_error_px",
    ],
    motion_predictability_evidence: [
      "schema_version", "kind", "fit_metric_version", "availability",
    ],
  },
  metric_keys: [
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
  segment_kinds: [],
};

const TARGET_SWITCHING_ROW_ATTRIBUTES_V1: string[] = [
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
];

export const TARGET_SWITCHING_EVIDENCE_EXTENSION_V1: EvidenceExtension = {
  schema_version: "analysis_evidence_extension.v1",
  extension_ref: "target-switching@1",
  channel_keys: [],
  event_kinds: {
    switch_chain: TARGET_SWITCHING_ROW_ATTRIBUTES_V1,
    unclassified_discrete_acquisition: TARGET_SWITCHING_ROW_ATTRIBUTES_V1,
    switch_previous_outcome: ["row_ref"],
    leave_previous: ["row_ref"],
    candidate_visible: ["row_ref"],
    target_selected: ["row_ref"],
    transition: ["row_ref"],
    next_target_acquired: ["row_ref"],
    settle: ["row_ref"],
    switch_first_shot: ["row_ref"],
    first_damage: ["row_ref"],
  },
  metric_keys: [
    "target_switching.transition_time_ms",
    "target_switching.transition_distance_px",
    "target_switching.path_efficiency",
    "target_switching.settle_duration_ms",
    "target_switching.first_shot_latency_ms",
    "target_switching.first_damage_latency_ms",
    "target_switching.carry_over_overshoot_ratio",
    "target_switching.terminal_correction_ratio",
  ],
  segment_kinds: [],
};

export const GENERIC_STATIC_EVIDENCE_EXTENSION_V1: EvidenceExtension = {
  schema_version: "analysis_evidence_extension.v1",
  extension_ref: "generic-static-clicking@1",
  channel_keys: [],
  event_kinds: {
    generic_target_track: [
      "birth_ms", "death_ms", "shape", "median_x_px", "median_y_px",
      "half_width_px", "half_height_px", "sample_count",
    ],
    generic_click_outcome: [
      "click_time_ms", "outcome", "target_track_ref",
      "miss_vector_x_px", "miss_vector_y_px", "miss_distance_px",
      "miss_vector_x_deg", "miss_vector_y_deg", "kill_ref",
    ],
    generic_kill_residual: [
      "kill_time_ms", "kill_index", "target_track_ref",
      "residual_x_px", "residual_y_px", "residual_distance_px",
      "residual_x_deg", "residual_y_deg",
    ],
  },
  metric_keys: [
    "static_clicking.generic.click_count",
    "static_clicking.generic.hit_clicks",
    "static_clicking.generic.miss_clicks",
    "static_clicking.generic.no_target_clicks",
    "static_clicking.generic.miss_distance_deg",
    "static_clicking.generic.miss_vector_x_deg",
    "static_clicking.generic.miss_vector_y_deg",
    "static_clicking.generic.kill_residual_distance_deg",
    "static_clicking.generic.kill_pairing_rate",
    "static_clicking.generic.frame_coverage",
  ],
  segment_kinds: [],
};

const GENERIC_AIM_FAMILY_METRIC_KEYS = [
  "click_count", "hit_clicks", "miss_clicks", "no_target_clicks",
  "miss_distance_deg", "miss_vector_x_deg", "miss_vector_y_deg",
  "kill_residual_distance_deg", "kill_pairing_rate", "frame_coverage",
];

export const GENERIC_AIM_FAMILIES_EVIDENCE_EXTENSION_V1: EvidenceExtension = {
  schema_version: "analysis_evidence_extension.v1",
  extension_ref: "generic-aim-families@1",
  channel_keys: [],
  event_kinds: {
    generic_switch_episode: [
      "from_kill_ms", "kill_index", "first_click_ms", "transition_ms",
      "target_track_ref",
    ],
  },
  metric_keys: [
    ...GENERIC_AIM_FAMILY_METRIC_KEYS.map(
      (key) => `dynamic_clicking.generic.${key}`,
    ),
    "dynamic_clicking.generic.target_speed_deg_per_s",
    ...GENERIC_AIM_FAMILY_METRIC_KEYS.map(
      (key) => `switching.generic.${key}`,
    ),
    "switching.generic.episode_count",
    "switching.generic.transition_time_ms",
    "tracking.generic.coverage",
    "tracking.generic.error_median_deg",
    "tracking.generic.error_p90_deg",
    "tracking.generic.in_target_ratio",
    "tracking.generic.loss_count",
  ],
  segment_kinds: [],
};

// ── Core sets ──

const CORE_CHANNELS = new Set([
  "mouse.delta_x", "mouse.delta_y", "mouse.position_x", "mouse.position_y",
  "mouse.speed", "mouse.acceleration", "crosshair.position_x",
  "crosshair.position_y", "crosshair.velocity_x", "crosshair.velocity_y",
  "aim_error.x", "aim_error.y", "aim_error.radial", "aim_error.normalized_radius",
  "target_relative.crosshair_velocity_projection", "outcome.score_rate",
  "outcome.damage_rate",
]);

const CORE_EVENTS = new Set([
  "shot", "hit", "miss", "kill", "movement_start", "movement_end",
  "low_confidence", "target_available", "acquire", "settle", "click_anchor",
  "tracking_episode", "off_target_start", "off_target_end", "reacquired",
  "target_change_point", "leave_previous", "candidate_visible", "target_selected",
  "transition", "next_target_acquired", "first_damage",
  "unclassified_discrete_acquisition",
]);

const CORE_METRICS = new Set([
  "outcome.score_rate", "outcome.damage_rate",
]);

const CORE_SEGMENTS = new Set([
  "typical", "worst", "improved", "comparison", "low_confidence",
]);

const ALL_EXTENSIONS: EvidenceExtension[] = [
  NATIVE_STATIC_EVIDENCE_EXTENSION_V1,
  DYNAMIC_CLICKING_EVIDENCE_EXTENSION_V1,
  CONTINUOUS_TRACKING_EVIDENCE_EXTENSION_V1,
  TARGET_SWITCHING_EVIDENCE_EXTENSION_V1,
  GENERIC_STATIC_EVIDENCE_EXTENSION_V1,
  GENERIC_AIM_FAMILIES_EVIDENCE_EXTENSION_V1,
];

export class EvidenceKeyRegistry {
  readonly channels: Set<string>;
  readonly events: Set<string>;
  readonly eventAttributes: Map<string, Set<string>>;
  readonly metrics: Set<string>;
  readonly segmentKinds: Set<string>;
  readonly extensions: Map<string, EvidenceExtension>;

  constructor() {
    this.channels = new Set(CORE_CHANNELS);
    this.events = new Set(CORE_EVENTS);
    this.eventAttributes = new Map<string, Set<string>>();
    this.metrics = new Set(CORE_METRICS);
    this.segmentKinds = new Set(CORE_SEGMENTS);
    this.extensions = new Map<string, EvidenceExtension>();

    // Initialize event attributes for core events
    for (const event of this.events) {
      this.eventAttributes.set(event, new Set());
    }

    // kill attributes
    const killAttrs = this.eventAttributes.get("kill")!;
    for (const attr of [
      "kill_index", "bot_name", "weapon_name", "ttk_s", "shots", "hits",
      "accuracy", "damage_done", "damage_possible", "efficiency", "overshots",
    ]) {
      killAttrs.add(attr);
    }

    // target_change_point attributes
    this.eventAttributes.get("target_change_point")!.add("change_kind");

    // Register all known extensions
    for (const ext of ALL_EXTENSIONS) {
      this.registerExtension(ext);
    }
  }

  registerExtension(extension: EvidenceExtension): void {
    const extensionRef = extension.extension_ref;
    if (this.extensions.has(extensionRef)) return;

    const channels = extension.channel_keys ?? [];
    const metrics = extension.metric_keys ?? [];
    const segments = extension.segment_kinds ?? [];
    const eventKinds = extension.event_kinds ?? {};

    for (const ch of channels) this.channels.add(ch);
    for (const m of metrics) this.metrics.add(m);
    for (const s of segments) this.segmentKinds.add(s);

    for (const [eventName, attributes] of Object.entries(eventKinds)) {
      this.events.add(eventName);
      const existing = this.eventAttributes.get(eventName) ?? new Set<string>();
      for (const attr of attributes) existing.add(attr);
      this.eventAttributes.set(eventName, existing);
    }

    this.extensions.set(extensionRef, extension);
  }

  allowsChannel(key: string): boolean {
    return this.channels.has(key) || CHANNEL_RE.test(key);
  }

  allowsEvent(key: string): boolean {
    return this.events.has(key);
  }

  allowsMetric(key: string): boolean {
    return this.metrics.has(key);
  }

  allowsSegment(key: string): boolean {
    return this.segmentKinds.has(key);
  }
}
