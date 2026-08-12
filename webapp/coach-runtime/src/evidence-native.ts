/**
 * Native evidence artifact reader + 13 evidence query commands.
 *
 * Reads evidence artifacts directly from the filesystem and implements
 * all evidence query commands in-process, eliminating the HTTP tool
 * bridge for evidence operations.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { SqliteDb } from "./db.ts";
import { buildProcessedEventTableCatalog, type EvidenceKeyRegistry } from "./evidence-catalogs.ts";

// ── Types ──

type AnyDict = Record<string, any>;
type NativeResult = {
  status: "succeeded" | "failed";
  result?: AnyDict;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
  signal_points?: number;
};

// ── Constants ──

const OUTCOME_SERIES_MAX = 8;
const METRIC_KEYS_MAX = 8;
const LIST_MAX = 200;
const SIGNAL_CHANNEL_MAX = 4;
const SIGNAL_POINTS_PER_CHANNEL = 600;
const FACT_SECTION_MAX = 8;

const HEADLINE_METRIC_KEYS = new Set([
  "accuracy", "ttk", "ttk_ms", "time_to_kill", "path_efficiency",
  "sparc", "overshoot", "reaction_time", "smoothness",
  "reverse_ratio", "tracking_accuracy", "click_accuracy",
  "target_switch_time", "precision", "consistency",
  "score", "kills_per_minute", "kpm", "dps",
]);

// ── EvidenceKeyRegistry singleton ──

let registryInstance: EvidenceKeyRegistry | undefined;
function registry(): EvidenceKeyRegistry {
  if (!registryInstance) registryInstance = new EvidenceKeyRegistry();
  return registryInstance;
}

// ── Artifact loading ──

let dataRoot: string | null | undefined;
function getDataRoot(): string | null {
  if (dataRoot !== undefined) return dataRoot;
  dataRoot = process.env.DATA_ROOT || null;
  return dataRoot;
}

type LoadedArtifact = { artifact: AnyDict; analysisRef: string; derivedArtifact: AnyDict };

export function loadArtifact(db: SqliteDb, analysisRef: string, ownerId: string): LoadedArtifact | null {
  const match = analysisRef.match(/^analysis:(\d+)$/);
  if (!match) return null;
  const sessionId = parseInt(match[1], 10);

  const row = db.prepare(
    "SELECT user_id, status, result FROM sessions WHERE id = ?",
  ).get(sessionId) as { user_id: string; status: string; result: string } | undefined;

  if (!row || row.user_id !== ownerId || row.status !== "done") return null;

  let result: AnyDict;
  try {
    result = JSON.parse(row.result);
  } catch {
    return null;
  }

  const safeRef = result?.evidence?.derived_artifact;
  if (!safeRef?.artifact_ref || !safeRef?.evidence_revision) return null;

  const root = getDataRoot();
  if (!root) return null;

  const revisionMatch = safeRef.evidence_revision.match(/^sha256:([0-9a-f]{64})$/i);
  if (!revisionMatch) return null;

  const artifactPath = resolve(
    root, "sessions", String(sessionId),
    "derived", "analysis_evidence", "revisions",
    revisionMatch[1], "artifact.json",
  );

  try {
    const raw = readFileSync(artifactPath, "utf-8");
    return { artifact: JSON.parse(raw), analysisRef, derivedArtifact: safeRef };
  } catch {
    return null;
  }
}

// ── Analysis brief builder (ported from coach_context.build_analysis_brief) ──

export function buildAnalysisBrief(
  artifact: AnyDict,
  diagnosis?: AnyDict | null,
): AnyDict | null {
  if (!artifact || typeof artifact !== "object") return null;

  const brief: AnyDict = {};

  // Compute the training-start offset so segment times are relative.
  const window = artifact.canonical_time_window;
  let windowStartMs = 0;
  if (window && typeof window === "object" && typeof window.start_ms === "number") {
    windowStartMs = window.start_ms;
  }

  // Evidence segments — compact playable time ranges (relative to video start).
  const segments = artifact.evidence_segments;
  if (Array.isArray(segments)) {
    const compactSegments: AnyDict[] = [];
    for (const segment of segments) {
      if (!segment || typeof segment !== "object") continue;
      const compact: AnyDict = {};
      for (const key of ["segment_id", "segment_kind", "title_key"]) {
        if (key in segment && segment[key] != null) compact[key] = segment[key];
      }
      const focusStart = segment.focus_start_ms;
      const focusEnd = segment.focus_end_ms;
      if (typeof focusStart === "number") compact.relative_start_ms = focusStart - windowStartMs;
      if (typeof focusEnd === "number") compact.relative_end_ms = focusEnd - windowStartMs;
      if (Array.isArray(segment.issue_refs)) {
        compact.issue_refs = segment.issue_refs.filter((r: unknown) => typeof r === "string").slice(0, 3);
      }
      if (compact.segment_id != null) compactSegments.push(compact);
    }
    if (compactSegments.length > 0) {
      brief.evidence_segments = compactSegments.slice(0, 24);
      brief.video_time_note =
        "relative_start_ms/relative_end_ms 是视频播放时间（从 0 开始）。" +
        "你没有视频帧读取能力，只能说'在这个时间段请重点观察什么'，" +
        "不能说'我看到画面里……'。";
    }
  }

  // Key metrics — headline computed values only.
  const metricRecords = artifact.metric_records;
  if (Array.isArray(metricRecords)) {
    const compactMetrics: AnyDict[] = [];
    for (const metric of metricRecords) {
      if (!metric || typeof metric !== "object") continue;
      const key = metric.metric_key;
      if (typeof key !== "string" || !HEADLINE_METRIC_KEYS.has(key)) continue;
      compactMetrics.push({
        metric_key: key,
        value: metric.value,
        unit: metric.unit,
        classification: metric.classification,
      });
    }
    if (compactMetrics.length > 0) brief.key_metrics = compactMetrics;
  }

  // Kill summary — aggregate stats from event bundles.
  const eventBundles = artifact.event_bundles;
  if (Array.isArray(eventBundles)) {
    const killEvents: AnyDict[] = [];
    for (const bundle of eventBundles) {
      if (!bundle || typeof bundle !== "object") continue;
      for (const event of bundle.events || []) {
        if (
          event && typeof event === "object" &&
          (event.event_kind === "kill" || event.event_kind === "elimination")
        ) {
          killEvents.push(event);
        }
      }
    }
    if (killEvents.length > 0) {
      const weaponCounts: Record<string, number> = {};
      const accuracies: number[] = [];
      const ttks: number[] = [];
      for (const event of killEvents) {
        const attrs = event.attributes;
        if (attrs && typeof attrs === "object") {
          if (typeof attrs.weapon === "string") {
            weaponCounts[attrs.weapon] = (weaponCounts[attrs.weapon] || 0) + 1;
          }
          if (typeof attrs.accuracy === "number") accuracies.push(attrs.accuracy);
          let ttk: unknown = attrs.ttk_ms;
          if (ttk == null) ttk = attrs.ttk;
          if (typeof ttk === "number") ttks.push(ttk);
        }
      }
      const killSummary: AnyDict = { total_kills: killEvents.length };
      if (accuracies.length > 0) killSummary.avg_accuracy = accuracies.reduce((a, b) => a + b, 0) / accuracies.length;
      if (ttks.length > 0) killSummary.avg_ttk_ms = ttks.reduce((a, b) => a + b, 0) / ttks.length;
      if (Object.keys(weaponCounts).length > 0) {
        const sorted = Object.entries(weaponCounts).sort((a, b) => b[1] - a[1]).slice(0, 16);
        killSummary.weapon_breakdown = Object.fromEntries(sorted);
      }
      brief.kill_summary = killSummary;
    }
  }

  // Diagnosis issues — connect each issue to relative video times.
  const eventTimes: Record<string, number> = {};
  if (Array.isArray(eventBundles)) {
    for (const bundle of eventBundles) {
      if (!bundle || typeof bundle !== "object") continue;
      for (const event of bundle.events || []) {
        if (
          event && typeof event === "object" &&
          typeof event.event_id === "string" &&
          typeof event.start_ms === "number"
        ) {
          eventTimes[event.event_id] = event.start_ms - windowStartMs;
        }
      }
    }
  }

  const diag = diagnosis ?? artifact.diagnosis;
  if (diag && typeof diag === "object") {
    const issues = (diag as AnyDict).issues;
    if (Array.isArray(issues)) {
      const compactIssues: AnyDict[] = [];
      for (const issue of issues) {
        if (!issue || typeof issue !== "object") continue;
        const compactIssue: AnyDict = {};
        for (const key of ["signal", "plain_language_meaning", "metric_refs"]) {
          if (key in issue && issue[key] != null) compactIssue[key] = issue[key];
        }
        const eventRefs = issue.event_refs;
        if (Array.isArray(eventRefs)) {
          const eventTimesOut: AnyDict[] = [];
          for (const ref of eventRefs.slice(0, 6)) {
            if (typeof ref === "string" && ref in eventTimes) {
              eventTimesOut.push({ event_ref: ref, relative_time_ms: eventTimes[ref] });
            }
          }
          if (eventTimesOut.length > 0) compactIssue.event_times = eventTimesOut;
        }
        if (compactIssue.signal != null) compactIssues.push(compactIssue);
      }
      if (compactIssues.length > 0) brief.diagnosis_issues = compactIssues;
    }
  }

  return Object.keys(brief).length > 0 ? brief : null;
}

// ── Ref parsing helpers ──

function analysisRefFromSegment(segmentRef: string): string {
  const match = segmentRef.match(/^(analysis:\d+):segment:/);
  return match ? match[1] : segmentRef;
}

function analysisRefFromEvent(eventRef: string): string {
  const match = eventRef.match(/^(analysis:\d+):event:/);
  return match ? match[1] : eventRef;
}

function analysisRefFromTable(tableRef: string): string {
  const match = tableRef.match(/^(analysis:\d+):table:/);
  return match ? match[1] : tableRef;
}

// ── Segment / event / metric projection ──

const SEGMENT_FIELDS = [
  "segment_id", "analysis_ref", "analyzer_ref", "segment_kind",
  "start_ms", "end_ms", "focus_start_ms", "focus_end_ms",
  "title_key", "rank_reason", "issue_refs", "metric_refs",
  "event_refs", "available_channels", "source_coverage", "confidence", "limitations",
];

function safeSegment(seg: AnyDict): AnyDict {
  const out: AnyDict = {};
  for (const key of SEGMENT_FIELDS) {
    if (key in seg) out[key] = seg[key];
  }
  return out;
}

const EVENT_FIELDS = [
  "event_id", "event_kind", "start_ms", "end_ms",
  "actor_refs", "source_refs", "confidence", "attributes", "limitations",
];

function safeEvent(event: AnyDict): AnyDict {
  const out: AnyDict = {};
  for (const key of EVENT_FIELDS) {
    if (key in event) out[key] = event[key];
  }
  return out;
}

const METRIC_FIELDS = [
  "metric_key", "metric_version", "value", "unit", "availability",
  "classification", "provenance", "population", "distribution",
  "condition_refs", "event_refs", "evidence_segment_refs",
  "coverage", "confidence", "limitations",
];

function safeMetric(metric: AnyDict): AnyDict {
  const out: AnyDict = {};
  for (const key of METRIC_FIELDS) {
    if (key in metric) out[key] = metric[key];
  }
  if (out.distribution && Array.isArray(out.distribution.histogram_bins) && out.distribution.histogram_bins.length > 16) {
    out.distribution = { ...out.distribution, histogram_bins: out.distribution.histogram_bins.slice(0, 16) };
  }
  return out;
}

function findSegment(artifact: AnyDict, segmentRef: string): AnyDict {
  const seg = artifact.evidence_segments?.find((s: AnyDict) => s.segment_id === segmentRef);
  if (!seg) throw new Error("segment not found");
  return seg;
}

// ── Downsample (ported from _downsample_points) ──

function downsamplePoints(points: number[][], limit: number): number[][] {
  if (points.length <= limit) return points.map((p) => [p[0], p[1]]);
  if (limit <= 0) return [];
  const lastIndex = points.length - 1;
  const globalMin = points.reduce((best, p, i) => (p[1] < points[best][1] ? i : best), 0);
  const globalMax = points.reduce((best, p, i) => (p[1] > points[best][1] ? i : best), 0);
  const mandatory = new Set([0, lastIndex, globalMin, globalMax]);
  if (limit < mandatory.size) throw new Error("point limit cannot preserve endpoints and extrema");
  if (limit <= 2) return [[points[0][0], points[0][1]], [points[lastIndex][0], points[lastIndex][1]]].slice(0, limit);
  if (limit === 3) {
    const midpoint = (points[0][1] + points[lastIndex][1]) / 2;
    const candidates = [globalMin, globalMax].filter((i) => i !== 0 && i !== lastIndex);
    const middle = (candidates.length > 0 ? candidates : [Math.floor(lastIndex / 2)]).reduce((best, i) =>
      Math.abs(points[i][1] - midpoint) > Math.abs(points[best][1] - midpoint) ? i : best,
    );
    return [0, middle, lastIndex].sort((a, b) => a - b).map((i) => [points[i][0], points[i][1]]);
  }
  const selected = new Set(mandatory);
  const interiorCount = Math.max(1, Math.floor((limit - 2) / 2));
  for (let bucket = 0; bucket < interiorCount; bucket++) {
    const start = 1 + Math.floor((bucket * (points.length - 2)) / interiorCount);
    const end = 1 + Math.floor(((bucket + 1) * (points.length - 2)) / interiorCount);
    if (start >= end) continue;
    let minIdx = start, maxIdx = start;
    for (let i = start + 1; i < end; i++) {
      if (points[i][1] < points[minIdx][1]) minIdx = i;
      if (points[i][1] > points[maxIdx][1]) maxIdx = i;
    }
    selected.add(minIdx);
    selected.add(maxIdx);
  }
  if (selected.size > limit) {
    const optional = [...selected].filter((i) => !mandatory.has(i)).sort((a, b) => a - b);
    const remaining = Math.max(0, limit - mandatory.size);
    const kept = remaining < optional.length
      ? Array.from({ length: remaining }, (_, i) => optional[Math.round((i * (optional.length - 1)) / Math.max(1, remaining - 1))])
      : optional;
    selected.clear();
    mandatory.forEach((i) => selected.add(i));
    kept.forEach((i) => selected.add(i));
  }
  if (selected.size < limit) {
    const available = [];
    for (let i = 1; i < lastIndex; i++) { if (!selected.has(i)) available.push(i); }
    const remaining = Math.min(limit - selected.size, available.length);
    for (let i = 0; i < remaining; i++) {
      selected.add(available[Math.round((i * (available.length - 1)) / Math.max(1, remaining - 1))]);
    }
  }
  return [...selected].sort((a, b) => a - b).slice(0, limit).map((i) => [points[i][0], points[i][1]]);
}

// ── Predicate matching (ported from _predicate_matches / _matching_events) ──

const EVENT_VALUE_MISSING = Symbol("missing");

function eventFieldValue(event: AnyDict, field: string): unknown {
  if (["event_id", "start_ms", "end_ms", "confidence", "limitations"].includes(field)) {
    return field in event ? event[field] : EVENT_VALUE_MISSING;
  }
  const attrs = event.attributes;
  return attrs && typeof attrs === "object" && field in attrs ? attrs[field] : EVENT_VALUE_MISSING;
}

function predicateMatches(event: AnyDict, predicate: AnyDict): boolean {
  const current = eventFieldValue(event, String(predicate.field));
  const operator = predicate.operator;
  if (operator === "available") return current !== EVENT_VALUE_MISSING && current !== null;
  if (operator === "unavailable") return current === EVENT_VALUE_MISSING || current === null;
  if (current === EVENT_VALUE_MISSING || current === null) return false;
  const operand = predicate.value;
  if (operator === "eq") return current === operand;
  if (typeof current === "boolean" || typeof current !== "number" || !Number.isFinite(current)) return false;
  const num = current;
  if (operator === "lt") return num < Number(operand);
  if (operator === "lte") return num <= Number(operand);
  if (operator === "gt") return num > Number(operand);
  if (operator === "gte") return num >= Number(operand);
  return Number(operand[0]) <= num && num <= Number(operand[1]); // between
}

function matchingEvents(events: AnyDict[], predicates: AnyDict[]): AnyDict[] {
  if (!predicates || predicates.length === 0) return events;
  return events.filter((event) => predicates.every((pred) => predicateMatches(event, pred)));
}

// ── Distribution stats (ported from _event_distribution / _nearest_rank) ──

function nearestRank(values: number[], quantile: number): number {
  const ordered = [...values].sort((a, b) => a - b);
  if (!ordered.length) throw new Error("nearest rank requires values");
  const index = Math.max(0, Math.ceil(quantile * ordered.length) - 1);
  return ordered[index];
}

function eventDistribution(events: AnyDict[], field: string): AnyDict {
  const values: number[] = [];
  for (const event of events) {
    const value = eventFieldValue(event, field);
    if (typeof value === "number" && !Number.isNaN(value) && Number.isFinite(value)) {
      values.push(value);
    }
  }
  if (!values.length) {
    return { count: events.length, valid_count: 0, excluded_count: events.length, availability: "unavailable" };
  }
  return {
    count: events.length,
    valid_count: values.length,
    excluded_count: events.length - values.length,
    availability: "available",
    min: Math.min(...values),
    p10: nearestRank(values, 0.1),
    p25: nearestRank(values, 0.25),
    median: nearestRank(values, 0.5),
    p75: nearestRank(values, 0.75),
    p90: nearestRank(values, 0.9),
    max: Math.max(...values),
    mean: values.reduce((a, b) => a + b, 0) / values.length,
  };
}

// ── Run phase grouping ──

function runPhaseGroups(events: AnyDict[]): Array<[string, AnyDict[]]> {
  const groups: Record<string, AnyDict[]> = { early: [], middle: [], late: [] };
  const total = events.length;
  events.forEach((event, index) => {
    const phaseIndex = Math.min(2, Math.floor((index * 3) / Math.max(1, total)));
    (["early", "middle", "late"] as const)[phaseIndex].length; // type guard
    groups[["early", "middle", "late"][phaseIndex]].push(event);
  });
  return ([["early", groups.early], ["middle", groups.middle], ["late", groups.late]] as const)
    .filter(([, rows]) => rows.length > 0);
}

// ── Aggregate groups ──

function aggregateGroups(
  events: AnyDict[], fields: string[], groupBy: string | null, table: AnyDict,
): AnyDict[] {
  let grouped: Array<[any, AnyDict[]]>;
  if (groupBy === null) {
    grouped = [["all", events]];
  } else if (groupBy === "run_phase") {
    grouped = runPhaseGroups(events);
  } else {
    const groupValues: Record<string, [any, AnyDict[]]> = {};
    for (const event of events) {
      let value = eventFieldValue(event, groupBy);
      if (value === EVENT_VALUE_MISSING || value === null) value = "unavailable";
      const key = JSON.stringify(value);
      if (!(key in groupValues)) groupValues[key] = [value, []];
      groupValues[key][1].push(event);
    }
    grouped = Object.keys(groupValues).sort().map((key) => groupValues[key]);
  }
  return grouped.map(([label, rows]) => {
    const item: AnyDict = {
      count: rows.length,
      fields: Object.fromEntries(fields.map((f) => [f, eventDistribution(rows, f)])),
    };
    if (groupBy === "run_phase") item.phase = label;
    else item.group_value = label;
    return item;
  });
}

// ── Processed table events ──

function processedTableEvents(artifact: AnyDict, tableRef: string): [AnyDict, AnyDict[]] {
  const tables = buildProcessedEventTableCatalog(artifact);
  const table = tables.find((t) => t.table_ref === tableRef);
  if (!table) throw new Error("table not found");
  const events = artifact.event_bundles
    .flatMap((bundle: AnyDict) => bundle.events || [])
    .filter((event: AnyDict) => event.event_kind === table.event_kind)
    .sort((a: AnyDict, b: AnyDict) =>
      (a.start_ms ?? 0) - (b.start_ms ?? 0) || (a.end_ms ?? 0) - (b.end_ms ?? 0) || String(a.event_id).localeCompare(String(b.event_id)),
    );
  return [table, events];
}

function tableField(table: AnyDict, field: string): AnyDict {
  const def = table.field_catalog?.find((f: AnyDict) => f.field_key === field);
  if (!def) throw new Error(`field "${field}" not in table field_catalog`);
  return def;
}

function tableMetricField(table: AnyDict, metricKey: string): AnyDict {
  const matches = (table.field_catalog || []).filter((f: AnyDict) => f.metric_key === metricKey);
  if (matches.length !== 1) throw new Error("requested metric does not map to one processed event field");
  return matches[0];
}

function processedMetricRecord(
  table: AnyDict,
  events: AnyDict[],
  metricKey: string,
  evidenceRef: string,
): AnyDict {
  const definition = tableMetricField(table, metricKey);
  const field = definition.field_key;
  const values: number[] = [];
  for (const event of events) {
    const value = eventFieldValue(event, field);
    if (typeof value === "number" && !Number.isNaN(value) && Number.isFinite(value)) {
      values.push(value);
    }
  }
  if (!values.length) throw new Error("processed event metric is unavailable");
  const value = values.length === 1 ? values[0] : nearestRank(values, 0.5);
  const eventRefs = events.map((e) => e.event_id);
  const sourceRefs = new Set<string>();
  for (const event of events) {
    for (const ref of event.source_refs || []) sourceRefs.add(ref as string);
  }
  const minConfidence = events.reduce((min, e) => Math.min(min, e.confidence ?? 0), 0);
  return {
    schema_version: "metric_record.v1",
    metric_key: metricKey,
    metric_version: definition.metric_version,
    value,
    unit: definition.unit,
    availability: "available",
    classification: "deterministic",
    provenance: {
      kind: "derived",
      source_refs: [...sourceRefs].sort(),
    },
    population: {
      sample_count: events.length,
      valid_count: values.length,
      excluded_count: events.length - values.length,
    },
    condition_refs: [],
    event_refs: eventRefs,
    evidence_segment_refs: evidenceRef.includes(":segment:") ? [evidenceRef] : [],
    coverage: minConfidence,
    confidence: minConfidence,
    limitations: [
      ...(definition.limitations || []),
      ...(values.length === 1 ? [] : ["segment_value_is_median_of_processed_rows"]),
    ],
  };
}

// ── Evidence result wrapper ──

function evidenceResult(resultRef: string, result: AnyDict, signalPoints = 0): NativeResult {
  return {
    status: "succeeded",
    result_ref: resultRef,
    result: { schema_version: "coach_evidence_query_result.v1", ...result },
    signal_points: signalPoints,
  };
}

function evidenceFailed(code: string, message: string): NativeResult {
  return { status: "failed", warning_or_error: { code, message } };
}

// ── Command implementations ──

type EvidenceCtx = { db: SqliteDb; ownerId: string };

function requireArtifact(ctx: EvidenceCtx, analysisRef: string): LoadedArtifact {
  const loaded = loadArtifact(ctx.db, analysisRef, ctx.ownerId);
  if (!loaded) throw new Error("evidence is unavailable");
  return loaded;
}

// 1. analysis.metrics.distribution
function cmdMetricsDistribution(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const analysisRef = params.analysis_ref;
  const { artifact, analysisRef: ref } = requireArtifact(ctx, analysisRef);
  const metricKeys: string[] = params.metric_keys ?? [];
  if (!Array.isArray(metricKeys) || metricKeys.length < 1 || metricKeys.length > METRIC_KEYS_MAX) {
    return evidenceFailed("invalid_parameters", "metric_keys must contain 1 to 8 keys");
  }
  const metrics = (artifact.metric_records || [])
    .filter((m: AnyDict) => metricKeys.includes(m.metric_key))
    .map(safeMetric);
  if (metrics.length !== metricKeys.length) {
    return evidenceFailed("not_found", "one or more metric keys not found");
  }
  return evidenceResult(`${ref}:metrics`, { analysis_ref: ref, metrics });
}

// 2. analysis.evidence.list
function cmdEvidenceList(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const analysisRef = params.analysis_ref;
  const { artifact, analysisRef: ref } = requireArtifact(ctx, analysisRef);
  const segmentKinds: string[] | undefined = params.segment_kinds;
  const issueRefs: string[] | undefined = params.issue_refs;
  const limit = Math.min(Math.max(params.limit ?? LIST_MAX, 1), LIST_MAX);
  const reg = registry();
  let segments = (artifact.evidence_segments || []).filter((seg: AnyDict) => {
    if (segmentKinds && segmentKinds.length > 0) {
      if (!segmentKinds.every((k) => reg.allowsSegment(k))) return false;
      if (!segmentKinds.includes(seg.segment_kind)) return false;
    }
    if (issueRefs && issueRefs.length > 0) {
      const segIssues = seg.issue_refs || [];
      if (!issueRefs.some((r: string) => segIssues.includes(r))) return false;
    }
    return true;
  });
  segments = segments.slice(0, limit);
  return evidenceResult(`${ref}:evidence:list`, {
    analysis_ref: ref,
    segments: segments.map(safeSegment),
  });
}

// 3. analysis.evidence.signal_window
function cmdSignalWindow(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const segmentRef = params.segment_ref;
  const channelKeys: string[] = params.channel_keys ?? [];
  if (!Array.isArray(channelKeys) || channelKeys.length < 1 || channelKeys.length > SIGNAL_CHANNEL_MAX) {
    return evidenceFailed("invalid_parameters", "channel_keys must contain 1 to 4 keys");
  }
  const analysisRef = analysisRefFromSegment(segmentRef);
  const { artifact } = requireArtifact(ctx, analysisRef);
  const segment = findSegment(artifact, segmentRef);
  const focusStart = segment.focus_start_ms;
  const focusEnd = segment.focus_end_ms;
  if (typeof focusStart !== "number" || typeof focusEnd !== "number" || focusStart >= focusEnd || focusEnd - focusStart > 12000) {
    return evidenceFailed("signal_window_unavailable", "segment focus window exceeds the 12 second limit");
  }
  const allowedChannels = new Set(segment.available_channels || []);
  if (!channelKeys.every((k) => allowedChannels.has(k))) {
    return evidenceFailed("invalid_parameters", "channel is not available in this segment");
  }
  const samplesByChannel: Record<string, AnyDict> = {};
  for (const sample of artifact.sample_sets || []) {
    if (channelKeys.includes(sample.channel_key)) samplesByChannel[sample.channel_key] = sample;
  }
  const channelMetadata: Record<string, AnyDict> = {};
  for (const bundle of artifact.signal_bundles || []) {
    for (const ch of bundle.channels || []) {
      if (channelKeys.includes(ch.channel_key)) channelMetadata[ch.channel_key] = ch;
    }
  }

  const channels: AnyDict[] = [];
  let pointCount = 0;
  let truncated = false;
  for (const channelKey of channelKeys) {
    const sample = samplesByChannel[channelKey];
    const metadata = channelMetadata[channelKey];
    if (!sample || !metadata) return evidenceFailed("evidence_unavailable", "signal channel samples are unavailable");
    const points = (sample.points || []).filter((p: number[]) => focusStart <= p[0] && p[0] < focusEnd);
    if (!points.length) return evidenceFailed("evidence_unavailable", "signal channel has no samples in the segment focus window");
    const sampled = downsamplePoints(points, SIGNAL_POINTS_PER_CHANNEL);
    pointCount += sampled.length;
    truncated = truncated || sampled.length < points.length;
    channels.push({
      channel_key: channelKey,
      unit: sample.unit,
      points: sampled,
      source_coverage: metadata.coverage,
      confidence: metadata.confidence_summary,
    });
  }

  return evidenceResult(`${segmentRef}:signal-window`, {
    schema_version: "signal_window.v1",
    analysis_ref: analysisRef,
    segment_ref: segmentRef,
    focus_range_ms: [focusStart, focusEnd],
    channels,
    downsample_version: "deterministic_extrema.v1",
    point_count: pointCount,
    truncated,
    limitations: truncated ? ["deterministic_extrema_downsampled"] : [],
  }, pointCount);
}

// 4. analysis.run_facts.get
function cmdRunFacts(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const analysisRef = params.analysis_ref;
  const { artifact, analysisRef: ref } = requireArtifact(ctx, analysisRef);
  let facts = artifact.canonical_run_facts;
  if (!facts || typeof facts !== "object") {
    return evidenceFailed("facts_unavailable", "CanonicalRunFacts 不可用");
  }
  const requested = params.sections ?? "all";
  if (requested !== "all") {
    const requestedKeys: string[] = requested;
    if (!Array.isArray(requestedKeys) || requestedKeys.length < 1 || requestedKeys.length > FACT_SECTION_MAX) {
      return evidenceFailed("invalid_parameters", "sections must contain 1 to 8 keys");
    }
    const selected = (facts.sections || []).filter((s: AnyDict) => requestedKeys.includes(s.section_key));
    if (selected.length !== requestedKeys.length) return evidenceFailed("invalid_parameters", "unknown facts section");
    facts = { ...facts, sections: selected };
  }
  const wireSize = Buffer.byteLength(JSON.stringify(facts), "utf8");
  const runFacts = wireSize <= 8 * 1024
    ? { mode: "inline", field_registry_version: facts.field_registry_version, facts, section_summaries: [], limitations: facts.limitations || [] }
    : { mode: "section_refs", field_registry_version: facts.field_registry_version, section_summaries: (facts.sections || []).map((s: AnyDict) => ({ section_ref: s.section_ref, section_key: s.section_key, title: s.title })), limitations: ["facts_over_inline_budget"] };
  return evidenceResult(`${ref}:facts`, { analysis_ref: ref, run_facts });
}

// 5. analysis.outcomes.timeline
function cmdOutcomesTimeline(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const analysisRef = params.analysis_ref;
  const scope = params.scope;
  const segmentRef = params.segment_ref;
  const mode = params.mode;
  const series: string[] = params.series ?? [];
  if (!Array.isArray(series) || series.length < 1 || series.length > OUTCOME_SERIES_MAX) {
    return evidenceFailed("invalid_parameters", "series must contain 1 to 8 keys");
  }
  if (scope !== "whole_run" && scope !== "evidence_segment") {
    return evidenceFailed("invalid_parameters", "timeline requires a bounded scope");
  }
  if (mode !== "overview" && mode !== "exact_page") {
    return evidenceFailed("invalid_parameters", "timeline requires overview/exact_page mode");
  }
  const { artifact, analysisRef: ref } = requireArtifact(ctx, analysisRef);

  let segmentBounds: [number, number] | null = null;
  if (scope === "evidence_segment") {
    if (typeof segmentRef !== "string") return evidenceFailed("invalid_reference", "segment_ref is required");
    const segment = findSegment(artifact, segmentRef);
    segmentBounds = [segment.start_ms, segment.end_ms];
  }

  const records = artifact.normalized_outcome_records || [];
  const bySeries: Record<string, AnyDict[]> = {};
  for (const key of series) bySeries[key] = [];

  for (const record of records) {
    const timeMs = record.canonical_time_ms;
    if (segmentBounds && !(segmentBounds[0] <= timeMs && timeMs < segmentBounds[1])) continue;
    for (const value of record.values || []) {
      const metricKey = value.metric_key;
      if (!(metricKey in bySeries)) continue;
      const numeric = value.value;
      if (typeof numeric !== "number") continue;
      bySeries[metricKey].push({
        time_ms: timeMs,
        value: numeric,
        semantics: value.value_semantics,
        unit: value.unit,
        source_refs: record.source_refs || [],
      });
    }
  }

  const overviewSeries: AnyDict[] = [];
  for (const metricKey of series) {
    const values = bySeries[metricKey].sort((a, b) => a.time_ms - b.time_ms);
    if (!values.length) return evidenceFailed("overview_unavailable", `outcome series "${metricKey}" has no records`);
    const bucketCount = Math.min(120, values.length);
    const points: number[][] = [];
    const sourceRefs = new Set<string>();
    for (let bucket = 0; bucket < bucketCount; bucket++) {
      const start = Math.floor((bucket * values.length) / bucketCount);
      const end = Math.floor(((bucket + 1) * values.length) / bucketCount);
      const bucketValues = values.slice(start, end);
      bucketValues.forEach((item) => (item.source_refs || []).forEach((r: string) => sourceRefs.add(r)));
      const semantics = bucketValues[0].semantics;
      let bucketValue: number;
      if (semantics === "count_increment" || semantics === "delta") {
        bucketValue = bucketValues.reduce((sum, item) => sum + item.value, 0);
      } else if (semantics === "instantaneous") {
        bucketValue = bucketValues[bucketValues.length - 1].value;
      } else {
        bucketValue = bucketValues.reduce((sum, item) => sum + item.value, 0) / bucketValues.length;
      }
      points.push([bucketValues[bucketValues.length - 1].time_ms, bucketValue]);
    }
    overviewSeries.push({ metric_key: metricKey, unit: values[0].unit, points, source_refs: [...sourceRefs].sort() });
  }

  return evidenceResult(`${ref}:timeline:overview`, {
    analysis_ref: ref,
    timeline: {
      schema_version: "normalized_outcome_timeline.v1",
      analysis_ref: ref,
      scope,
      segment_ref: segmentRef ?? null,
      canonical_time_window_ref: `${ref}:canonical-window`,
      mode: "overview",
      resolution: "deterministic_binned",
      selected_series: series,
      overview_series: overviewSeries,
      records: null,
      event_refs: [],
      completeness: "downsampled",
      limitations: ["deterministic_binned_overview"],
    },
  });
}

// 6. analysis.events.list
function cmdEventsList(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const analysisRef = params.analysis_ref;
  const scope = params.scope;
  const segmentRef = params.segment_ref;
  const eventKinds: string[] | undefined = params.event_kinds;
  const limit = Math.min(Math.max(params.limit ?? LIST_MAX, 1), LIST_MAX);
  const { artifact, analysisRef: ref } = requireArtifact(ctx, analysisRef);

  const reg = registry();
  if (eventKinds && !eventKinds.every((k) => reg.allowsEvent(k))) {
    return evidenceFailed("invalid_parameters", "unsupported event kind");
  }

  let events: AnyDict[] = artifact.event_bundles
    .flatMap((bundle: AnyDict) => bundle.events || [])
    .filter((event: AnyDict) => !eventKinds || eventKinds.length === 0 || eventKinds.includes(event.event_kind));

  // Also synthesize events from normalized_outcome_records (performance metric changes)
  const outcomeEvents = synthesizeOutcomeEvents(artifact, ref);
  events = deduplicateEvents([...events, ...outcomeEvents]);

  if (scope === "evidence_segment" && typeof segmentRef === "string") {
    const segment = findSegment(artifact, segmentRef);
    const segStart = segment.start_ms;
    const segEnd = segment.end_ms;
    events = events.filter((e) => (e.start_ms ?? 0) >= segStart && (e.start_ms ?? 0) < segEnd);
  }

  events.sort((a, b) =>
    (a.start_ms ?? 0) - (b.start_ms ?? 0) || (a.end_ms ?? 0) - (b.end_ms ?? 0) || String(a.event_id).localeCompare(String(b.event_id)),
  );
  events = events.slice(0, limit);

  return evidenceResult(`${ref}:events:0`, {
    analysis_ref: ref,
    scope,
    records: events.map(safeEvent),
    event_refs: events.map((e) => e.event_id),
  });
}

// 7. analysis.events.get
function cmdEventsGet(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const tableRef = params.table_ref;
  const eventRef = params.event_ref;
  const analysisRef = analysisRefFromTable(tableRef);
  const { artifact } = requireArtifact(ctx, analysisRef);
  try {
    const [table, events] = processedTableEvents(artifact, tableRef);
    const event = events.find((e) => e.event_id === eventRef);
    if (!event) return evidenceFailed("not_found", "event not found in table");
    return evidenceResult(`${eventRef}:detail`, { table, event: safeEvent(event) });
  } catch (e) {
    return evidenceFailed("table_not_found", (e as Error).message);
  }
}

// 8. analysis.events.rank
function cmdEventsRank(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const tableRef = params.table_ref;
  const field = params.field;
  const direction = params.direction;
  const predicates: AnyDict[] = params.predicates ?? [];
  const limit = Math.min(Math.max(params.limit ?? LIST_MAX, 1), LIST_MAX);
  const analysisRef = analysisRefFromTable(tableRef);
  const { artifact } = requireArtifact(ctx, analysisRef);
  try {
    const [table, events] = processedTableEvents(artifact, tableRef);
    const fieldDef = tableField(table, field);
    if (fieldDef.value_type !== "number") return evidenceFailed("invalid_parameters", "rank field must be numeric");

    let filtered = predicates.length > 0 ? matchingEvents(events, predicates) : events;
    const predicateMatchCount = filtered.length;

    filtered = filtered.filter((e) => {
      const v = eventFieldValue(e, field);
      return typeof v === "number" && Number.isFinite(v);
    });

    filtered.sort((a, b) => {
      const av = eventFieldValue(a, field) as number;
      const bv = eventFieldValue(b, field) as number;
      const diff = direction === "asc" ? av - bv : bv - av;
      return diff !== 0 ? diff : (a.start_ms ?? 0) - (b.start_ms ?? 0) || String(a.event_id).localeCompare(String(b.event_id));
    });
    filtered = filtered.slice(0, limit);

    return evidenceResult(`${tableRef}:rank:${field}:${direction}`, {
      table_ref: tableRef,
      field,
      direction,
      evaluated_count: events.length,
      predicate_match_count: predicateMatchCount,
      included_count: filtered.length,
      excluded_count: events.length - filtered.length,
      rows: filtered.map(safeEvent),
      event_refs: filtered.map((e) => e.event_id),
      completeness: table.completeness,
      limitations: table.limitations,
    });
  } catch (e) {
    return evidenceFailed("table_not_found", (e as Error).message);
  }
}

// 9. analysis.events.filter
function cmdEventsFilter(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const tableRef = params.table_ref;
  const predicates: AnyDict[] = params.predicates ?? [];
  const limit = Math.min(Math.max(params.limit ?? LIST_MAX, 1), LIST_MAX);
  const analysisRef = analysisRefFromTable(tableRef);
  const { artifact } = requireArtifact(ctx, analysisRef);
  try {
    const [table, events] = processedTableEvents(artifact, tableRef);
    const matched = matchingEvents(events, predicates).slice(0, limit);
    return evidenceResult(`${tableRef}:filter:0`, {
      table_ref: tableRef,
      evaluated_count: events.length,
      matched_count: matched.length,
      excluded_count: events.length - matched.length,
      rows: matched.map(safeEvent),
      event_refs: matched.map((e) => e.event_id),
      completeness: table.completeness,
      limitations: table.limitations,
    });
  } catch (e) {
    return evidenceFailed("table_not_found", (e as Error).message);
  }
}

// 10. analysis.events.aggregate
function cmdEventsAggregate(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const tableRef = params.table_ref;
  const fields: string[] = params.fields ?? [];
  const groupBy: string | null = params.group_by ?? null;
  if (!Array.isArray(fields) || fields.length < 1 || fields.length > 8) {
    return evidenceFailed("invalid_parameters", "fields must contain 1 to 8 keys");
  }
  const analysisRef = analysisRefFromTable(tableRef);
  const { artifact } = requireArtifact(ctx, analysisRef);
  try {
    const [table, events] = processedTableEvents(artifact, tableRef);
    for (const f of fields) {
      if (tableField(table, f).value_type !== "number") {
        return evidenceFailed("invalid_parameters", "aggregate fields must be numeric");
      }
    }
    const groups = aggregateGroups(events, fields, groupBy, table);
    return evidenceResult(`${tableRef}:aggregate`, {
      table_ref: tableRef,
      evaluated_count: events.length,
      group_by: groupBy,
      groups,
      completeness: table.completeness,
      limitations: table.limitations,
    });
  } catch (e) {
    return evidenceFailed("table_not_found", (e as Error).message);
  }
}

// 11. analysis.events.co_occurrence
function cmdEventsCoOccurrence(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const tableRef = params.table_ref;
  const left: AnyDict = params.left;
  const right: AnyDict = params.right;
  const relation = params.relation;
  if (relation !== "same_event") return evidenceFailed("invalid_parameters", "relation must be same_event");
  const analysisRef = analysisRefFromTable(tableRef);
  const { artifact } = requireArtifact(ctx, analysisRef);
  try {
    const [table, events] = processedTableEvents(artifact, tableRef);
    let both = 0, leftOnly = 0, rightOnly = 0, neither = 0;
    const supporting: string[] = [];
    const counterexamples: string[] = [];
    for (const event of events) {
      const leftMatch = predicateMatches(event, left);
      const rightMatch = predicateMatches(event, right);
      if (leftMatch && rightMatch) { both++; if (supporting.length < 20) supporting.push(event.event_id); }
      else if (leftMatch) { leftOnly++; if (counterexamples.length < 20) counterexamples.push(event.event_id); }
      else if (rightMatch) { rightOnly++; }
      else { neither++; }
    }
    return evidenceResult(`${tableRef}:co-occurrence`, {
      table_ref: tableRef,
      relation: "same_event",
      evaluated_count: events.length,
      counts: { both, left_only: leftOnly, right_only: rightOnly, neither },
      rates: {
        right_given_left: leftOnly + both > 0 ? both / (leftOnly + both) : null,
        left_given_right: rightOnly + both > 0 ? both / (rightOnly + both) : null,
      },
      supporting_event_refs: supporting,
      counterexample_event_refs: counterexamples,
      completeness: table.completeness,
      limitations: [...(table.limitations || []), "co_occurrence_does_not_establish_causation"],
    });
  } catch (e) {
    return evidenceFailed("table_not_found", (e as Error).message);
  }
}

// 12. analysis.events.sequence
function cmdEventsSequence(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const tableRef = params.table_ref;
  const fields: string[] = params.fields ?? [];
  const mode = params.mode;
  if (!Array.isArray(fields) || fields.length < 1 || fields.length > 4) {
    return evidenceFailed("invalid_parameters", "fields must contain 1 to 4 keys");
  }
  if (!["early_middle_late", "run_decile", "adjacent"].includes(mode)) {
    return evidenceFailed("invalid_parameters", "mode must be early_middle_late, run_decile, or adjacent");
  }
  const analysisRef = analysisRefFromTable(tableRef);
  const { artifact } = requireArtifact(ctx, analysisRef);
  try {
    const [table, events] = processedTableEvents(artifact, tableRef);
    let groups: AnyDict[];
    if (mode === "early_middle_late") {
      groups = aggregateGroups(events, fields, "run_phase", table);
    } else if (mode === "run_decile") {
      groups = [];
      const total = events.length;
      for (let decile = 0; decile < 10; decile++) {
        const start = Math.floor((decile * total) / 10);
        const end = Math.floor(((decile + 1) * total) / 10);
        const decileEvents = events.slice(start, end);
        if (!decileEvents.length) continue;
        groups.push({
          count: decileEvents.length,
          phase: `decile_${decile}`,
          fields: Object.fromEntries(fields.map((f) => [f, eventDistribution(decileEvents, f)])),
        });
      }
    } else {
      // adjacent: compute deltas between consecutive events
      const adjacentFields: AnyDict = {};
      for (const field of fields) {
        const deltas: number[] = [];
        for (let i = 1; i < events.length; i++) {
          const prev = eventFieldValue(events[i - 1], field);
          const curr = eventFieldValue(events[i], field);
          if (typeof prev === "number" && typeof curr === "number" && Number.isFinite(prev) && Number.isFinite(curr)) {
            deltas.push(curr - prev);
          }
        }
        adjacentFields[field] = deltas.length > 0
          ? { pair_count: deltas.length, min: Math.min(...deltas), median: nearestRank(deltas, 0.5), max: Math.max(...deltas), mean: deltas.reduce((a, b) => a + b, 0) / deltas.length }
          : { pair_count: 0, min: null, median: null, max: null, mean: null };
      }
      return evidenceResult(`${tableRef}:sequence:${mode}`, {
        table_ref: tableRef,
        mode,
        evaluated_count: events.length,
        adjacent_fields: adjacentFields,
        completeness: table.completeness,
        limitations: [...(table.limitations || []), "chronological_pattern_does_not_establish_learning_or_causation"],
      });
    }
    return evidenceResult(`${tableRef}:sequence:${mode}`, {
      table_ref: tableRef,
      mode,
      evaluated_count: events.length,
      groups,
      completeness: table.completeness,
      limitations: [...(table.limitations || []), "chronological_pattern_does_not_establish_learning_or_causation"],
    });
  } catch (e) {
    return evidenceFailed("table_not_found", (e as Error).message);
  }
}

// 13. analysis.evidence.compare
function cmdEvidenceCompare(ctx: EvidenceCtx, params: AnyDict): NativeResult {
  const refs: string[] = params.evidence_refs ?? [];
  const metricKeys: string[] = params.metric_keys ?? [];
  if (!Array.isArray(refs) || refs.length < 2 || refs.length > 4) {
    return evidenceFailed("invalid_parameters", "evidence_refs must contain 2 to 4 refs");
  }
  if (!Array.isArray(metricKeys) || metricKeys.length < 1 || metricKeys.length > METRIC_KEYS_MAX) {
    return evidenceFailed("invalid_parameters", "metric_keys must contain 1 to 8 keys");
  }

  // All refs must be the same scope.
  let comparisonScope: string | null = null;
  for (const ref of refs) {
    let refScope: string;
    if (ref.includes(":segment:")) refScope = "segment";
    else if (ref.includes(":event:")) refScope = "event";
    else if (/^analysis:\d+$/.test(ref)) refScope = "analysis";
    else return evidenceFailed("invalid_reference", "comparison refs must be analysis, segment or processed event refs");
    if (comparisonScope === null) comparisonScope = refScope;
    else if (comparisonScope !== refScope) return evidenceFailed("not_comparable", "analysis and segment evidence cannot be mixed");
  }

  const comparisonContracts: AnyDict[] = [];
  const comparisonLimitations: string[] = [];
  const rows: AnyDict[] = [];

  for (const ref of refs) {
    const loaded = requireArtifact(ctx, ref);
    const { artifact, derivedArtifact } = loaded;

    let segment: AnyDict | null = null;
    let event: AnyDict | null = null;
    let table: AnyDict | null = null;

    if (comparisonScope === "segment") {
      try {
        segment = findSegment(artifact, ref);
      } catch {
        return evidenceFailed("not_found", "EvidenceSegment 不存在");
      }
    }

    // All requested keys must exist in metric_records.
    const availableKeys = new Set((artifact.metric_records || []).map((m: AnyDict) => m.metric_key));
    if (!metricKeys.every((k) => availableKeys.has(k))) {
      return evidenceFailed("invalid_parameters", "metric is not available for comparison");
    }

    let predicateVersion: string;
    let metrics: AnyDict[];

    if (comparisonScope === "analysis") {
      predicateVersion = "analysis_metric_comparability.v1";
      metrics = (artifact.metric_records || [])
        .filter((m: AnyDict) => metricKeys.includes(m.metric_key))
        .map(safeMetric);
    } else {
      const processedTables = buildProcessedEventTableCatalog(artifact);

      if (comparisonScope === "segment" && processedTables.length === 0) {
        // Legacy: use metric_records linked to the segment.
        predicateVersion = "legacy_segment_metric_comparability.v1";
        metrics = (artifact.metric_records || [])
          .filter((m: AnyDict) =>
            metricKeys.includes(m.metric_key) &&
            Array.isArray(m.evidence_segment_refs) &&
            m.evidence_segment_refs.includes(ref),
          )
          .map(safeMetric);
        comparisonLimitations.push("legacy_segment_compare_uses_linked_metric_record");
      } else if (comparisonScope === "event") {
        // Find the unique table containing the event.
        predicateVersion = "processed_event_metric_comparability.v1";
        const matches: Array<[AnyDict, AnyDict[]]> = [];
        for (const candidate of processedTables) {
          const [, candidateEvents] = processedTableEvents(artifact, candidate.table_ref);
          const candidateEvent = candidateEvents.find((e: AnyDict) => e.event_id === ref);
          if (candidateEvent) matches.push([candidate, [candidateEvent]]);
        }
        if (matches.length !== 1) {
          return evidenceFailed("not_comparable", "event ref is not a unique processed event");
        }
        table = matches[0][0];
        const selectedEvents = matches[0][1];
        event = selectedEvents[0];
        metrics = metricKeys.map((k) => safeMetric(processedMetricRecord(table, selectedEvents, k, ref)));
      } else {
        // Segment scope with processed tables: find the unique table covering the segment.
        predicateVersion = "processed_event_metric_comparability.v1";
        const matches: Array<[AnyDict, AnyDict[]]> = [];
        for (const candidate of processedTables) {
          if (!metricKeys.every((key) =>
            (candidate.field_catalog || []).some((f: AnyDict) => f.metric_key === key),
          )) continue;
          const [, candidateEvents] = processedTableEvents(artifact, candidate.table_ref);
          const selectedEvents = candidateEvents.filter(
            (e: AnyDict) => segment!.start_ms <= (e.start_ms ?? -1) && (e.start_ms ?? -1) < segment!.end_ms,
          );
          if (selectedEvents.length > 0) matches.push([candidate, selectedEvents]);
        }
        if (matches.length !== 1) {
          return evidenceFailed("not_comparable", "segment does not resolve to one processed event table");
        }
        table = matches[0][0];
        const selectedEvents = matches[0][1];
        metrics = metricKeys.map((k) => safeMetric(processedMetricRecord(table, selectedEvents, k, ref)));
      }
    }

    // All requested metrics must be present.
    const metricKeySet = new Set(metrics.map((m: AnyDict) => m.metric_key));
    if (metricKeySet.size !== metricKeys.length || !metricKeys.every((k) => metricKeySet.has(k))) {
      return evidenceFailed("not_comparable", "requested metrics are not linked to every comparison ref");
    }

    // Build the comparison contract.
    const facts = artifact.canonical_run_facts || {};
    const rawMetrics: Record<string, AnyDict> = {};
    for (const m of metrics) rawMetrics[m.metric_key] = m;

    const contract: AnyDict = {
      predicate_version: predicateVersion,
      artifact_contract_version: derivedArtifact.contract_version,
      scenario_profile_ref: facts.scenario_profile_ref ?? null,
      timebase_version: artifact.canonical_time_window?.timebase_version ?? null,
      analyzer_ref: segment?.analyzer_ref ?? table?.analyzer_ref ?? null,
      metrics: Object.fromEntries(metricKeys.map((key) => {
        const rm = rawMetrics[key] || {};
        return [key, {
          metric_version: rm.metric_version ?? null,
          unit: rm.unit ?? null,
          classification: rm.classification ?? null,
          provenance_kind: (rm.provenance || {}).kind ?? null,
          condition_refs: Array.isArray(rm.condition_refs) ? [...rm.condition_refs].sort() : [],
        }];
      })),
    };
    comparisonContracts.push(contract);

    rows.push({
      evidence_ref: ref,
      scope: comparisonScope,
      segment: segment ? safeSegment(segment) : null,
      event: event ? safeEvent(event) : null,
      metrics,
    });
  }

  // All contracts must match.
  const firstContract = JSON.stringify(comparisonContracts[0]);
  for (let i = 1; i < comparisonContracts.length; i++) {
    if (JSON.stringify(comparisonContracts[i]) !== firstContract) {
      return evidenceFailed("not_comparable", "versioned comparison contracts do not match");
    }
  }

  // Compute deltas from the first row.
  const baseline: Record<string, unknown> = {};
  for (const m of rows[0].metrics) {
    baseline[m.metric_key] = m.value;
  }
  for (const row of rows) {
    row.deltas_from_first = Object.fromEntries(
      row.metrics.map((m: AnyDict) => {
        const bv = baseline[m.metric_key];
        return [
          m.metric_key,
          typeof m.value === "number" && typeof bv === "number"
            ? m.value - bv
            : null,
        ];
      }),
    );
  }

  const resultRef = `evidence:compare:${refs.join(",")}`.slice(0, 80);
  return evidenceResult(resultRef, {
    scope: comparisonScope,
    comparability: "comparable",
    comparability_predicate_version: comparisonContracts[0].predicate_version,
    metric_keys: metricKeys,
    comparisons: rows,
    limitations: [...new Set(comparisonLimitations)],
  });
}

// ── Helpers for events.list ──

function synthesizeOutcomeEvents(artifact: AnyDict, analysisRef: string): AnyDict[] {
  const events: AnyDict[] = [];
  for (const record of artifact.normalized_outcome_records || []) {
    for (const value of record.values || []) {
      const semantics = value.value_semantics;
      if (semantics === "count_increment" && typeof value.value === "number") {
        const metricKey = value.metric_key;
        let eventKind: string | null = null;
        if (metricKey.includes("shot")) eventKind = "shot";
        else if (metricKey.includes("hit")) eventKind = "hit";
        else if (metricKey.includes("miss")) eventKind = "miss";
        else if (metricKey.includes("kill")) eventKind = "kill";
        if (eventKind) {
          events.push({
            event_id: `event:${analysisRef}:outcome:${record.canonical_time_ms}:${metricKey}`,
            event_kind: eventKind,
            start_ms: record.canonical_time_ms,
            end_ms: record.canonical_time_ms,
            actor_refs: [],
            source_refs: record.source_refs || [],
            confidence: null,
            attributes: {},
            limitations: [],
          });
        }
      }
    }
  }
  return events;
}

function deduplicateEvents(events: AnyDict[]): AnyDict[] {
  const seen = new Set<string>();
  return events.filter((e) => {
    const key = `${e.start_ms}|${(e.source_refs || []).sort().join(",")}|${e.event_kind}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// ── Dispatch ──

const EVIDENCE_COMMANDS = new Set([
  "analysis.metrics.distribution",
  "analysis.evidence.list",
  "analysis.evidence.signal_window",
  "analysis.evidence.compare",
  "analysis.run_facts.get",
  "analysis.outcomes.timeline",
  "analysis.events.list",
  "analysis.events.get",
  "analysis.events.rank",
  "analysis.events.filter",
  "analysis.events.aggregate",
  "analysis.events.co_occurrence",
  "analysis.events.sequence",
]);

const HANDLERS: Record<string, (ctx: EvidenceCtx, params: AnyDict) => NativeResult> = {
  "analysis.metrics.distribution": cmdMetricsDistribution,
  "analysis.evidence.list": cmdEvidenceList,
  "analysis.evidence.signal_window": cmdSignalWindow,
  "analysis.evidence.compare": cmdEvidenceCompare,
  "analysis.run_facts.get": cmdRunFacts,
  "analysis.outcomes.timeline": cmdOutcomesTimeline,
  "analysis.events.list": cmdEventsList,
  "analysis.events.get": cmdEventsGet,
  "analysis.events.rank": cmdEventsRank,
  "analysis.events.filter": cmdEventsFilter,
  "analysis.events.aggregate": cmdEventsAggregate,
  "analysis.events.co_occurrence": cmdEventsCoOccurrence,
  "analysis.events.sequence": cmdEventsSequence,
};

export function isNativeEvidenceCommand(commandName: string): boolean {
  return EVIDENCE_COMMANDS.has(commandName);
}

export function executeNativeEvidence(
  db: SqliteDb,
  commandName: string,
  params: AnyDict,
  ownerId: string,
): NativeResult {
  const handler = HANDLERS[commandName];
  if (!handler) return evidenceFailed("unknown_command", `${commandName} is not a native evidence command`);
  const ctx: EvidenceCtx = { db, ownerId };
  try {
    return handler(ctx, params);
  } catch (error) {
    const message = error instanceof Error ? error.message : "native evidence command failed";
    return evidenceFailed("native_error", message);
  }
}
