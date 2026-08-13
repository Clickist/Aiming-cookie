import { createHash } from "node:crypto";
import {
  closeSync,
  fstatSync,
  openSync,
  readFileSync,
  readSync,
  statSync,
} from "node:fs";
import type { BigIntStats } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { SqliteDb } from "./db.ts";

type AnyDict = Record<string, any>;

type RawJsonInteger = { readonly rawJsonInteger: string };
type FrozenFingerprint = {
  sha256: string;
  size: number;
  mtime_ns?: RawJsonInteger;
};

export type NativeAnalysisInput = {
  analysisType: string;
  limitations: string[];
  selectedMode: "multimodal" | "input_native" | "video_fallback";
  snapshot: AnyDict;
  snapshotJson: string;
  statsPath: string;
  statsFingerprint: FrozenFingerprint;
  videoPath: string | null;
  videoFingerprint: FrozenFingerprint | null;
};

export class NativeAnalysisInputError extends Error {}

const SOURCE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const SHA256_RE = /^[0-9a-f]{64}$/i;
const MAX_TRACE_BYTES = 32 * 1024 * 1024;
const TRACE_HEADER_BYTES = 12;
const TRACE_RECORD_BYTES = 20;
const MAX_TRACE_POINTS = 1_000_000;
const MAX_TRACE_SPAN_MS = 10 * 60 * 1000;
const MAX_SCENARIO_DEFINITION_BYTES = 2 * 1024 * 1024;
const MAX_SCENARIO_DEFINITION_LINES = 65_536;
const MAX_SCENARIO_LIST = 64;

function parseObject(value: unknown): AnyDict | null {
  if (typeof value !== "string" || !value) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as AnyDict
      : null;
  } catch {
    return null;
  }
}

function rawInteger(value: unknown): RawJsonInteger {
  if (typeof value !== "string" || !/^(?:0|[1-9]\d*)$/.test(value)) {
    throw new NativeAnalysisInputError("source_unavailable: source identity missing");
  }
  return { rawJsonInteger: value };
}

function stringifyWithRawIntegers(value: unknown): string {
  if (
    value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === 1
    && typeof (value as RawJsonInteger).rawJsonInteger === "string"
  ) {
    return (value as RawJsonInteger).rawJsonInteger;
  }
  if (Array.isArray(value)) {
    return `[${value.map(stringifyWithRawIntegers).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value).map(([key, child]) => (
      `${JSON.stringify(key)}:${stringifyWithRawIntegers(child)}`
    )).join(",")}}`;
  }
  const serialized = JSON.stringify(value);
  if (serialized === undefined) throw new NativeAnalysisInputError("input snapshot is not JSON-safe");
  return serialized;
}

function sameRevision(left: BigIntStats, right: BigIntStats): boolean {
  return left.dev === right.dev
    && left.ino === right.ino
    && left.size === right.size
    && left.mtimeNs === right.mtimeNs
    && left.ctimeNs === right.ctimeNs;
}

function fingerprintFile(path: string): { sha256: string; size: number; mtimeNs: bigint; dev: bigint; ino: bigint } {
  let fd: number;
  try {
    fd = openSync(path, "r");
  } catch (error) {
    throw new NativeAnalysisInputError("source_unavailable: source is unavailable", { cause: error });
  }
  try {
    const before = fstatSync(fd, { bigint: true });
    if (!before.isFile()) throw new NativeAnalysisInputError("source_unavailable: source is not a file");
    const digest = createHash("sha256");
    const buffer = Buffer.allocUnsafe(1024 * 1024);
    let size = 0;
    for (;;) {
      const bytesRead = readSync(fd, buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      digest.update(buffer.subarray(0, bytesRead));
      size += bytesRead;
    }
    const after = fstatSync(fd, { bigint: true });
    if (!sameRevision(before, after) || BigInt(size) !== after.size) {
      throw new NativeAnalysisInputError("source_unavailable: source changed while fingerprinting");
    }
    return {
      sha256: digest.digest("hex"),
      size,
      mtimeNs: after.mtimeNs,
      dev: after.dev,
      ino: after.ino,
    };
  } finally {
    closeSync(fd);
  }
}

function pathsEqual(left: string, right: string): boolean {
  const a = resolve(left);
  const b = resolve(right);
  return process.platform === "win32" ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function isRegularFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

function freezeSummarySource(
  runId: number,
  kind: "stats" | "performance",
  path: unknown,
  summary: AnyDict | null,
  exactMtimeNs: unknown,
): AnyDict | null {
  if (typeof path !== "string" || !path) return null;
  if (!isRegularFile(path)) return null;
  const source = summary?.source;
  if (source === null || typeof source !== "object" || Array.isArray(source)) {
    throw new NativeAnalysisInputError(`source_unavailable: ${kind} identity missing`);
  }
  const expectedKeys = new Set([
    "path", "basename", "sha256", "size", "mtime_ns", "parser_version", "availability",
  ]);
  if (Object.keys(source).length !== expectedKeys.size || Object.keys(source).some((key) => !expectedKeys.has(key))) {
    throw new NativeAnalysisInputError(`source_unavailable: ${kind} identity missing`);
  }
  if (
    typeof source.path !== "string" || !pathsEqual(source.path, path)
    || source.basename !== basename(path)
    || typeof source.sha256 !== "string" || !SHA256_RE.test(source.sha256)
    || !Number.isSafeInteger(source.size) || source.size < 0
    || typeof source.parser_version !== "string" || !source.parser_version
    || source.availability !== "available"
  ) {
    throw new NativeAnalysisInputError(`source_unavailable: ${kind} identity missing`);
  }
  const mtime = rawInteger(exactMtimeNs);
  const observed = fingerprintFile(resolve(path));
  if (
    observed.sha256 !== source.sha256.toLowerCase()
    || observed.size !== source.size
    || observed.mtimeNs.toString() !== mtime.rawJsonInteger
  ) {
    throw new NativeAnalysisInputError(`source_unavailable: ${kind} revision changed`);
  }
  return {
    artifact_ref: `run:${runId}:${kind}:${observed.sha256.slice(0, 16)}`,
    basename: source.basename,
    fingerprint: {
      sha256: observed.sha256,
      size: observed.size,
      mtime_ns: mtime,
    },
    path: resolve(path),
    availability: "available",
    parser_version: source.parser_version,
  };
}

function freezeTrace(runId: number, path: unknown, state: unknown): AnyDict | null {
  if (state !== "attached" || typeof path !== "string" || !path) return null;
  if (!isRegularFile(path)) return null;
  let data: Buffer;
  try {
    const metadata = statSync(path);
    if (!metadata.isFile() || metadata.size < TRACE_HEADER_BYTES || metadata.size > MAX_TRACE_BYTES) {
      throw new NativeAnalysisInputError("source_unavailable: raw input snapshot is invalid");
    }
    data = readFileSync(path);
  } catch (error) {
    if (error instanceof NativeAnalysisInputError) throw error;
    throw new NativeAnalysisInputError("source_unavailable: raw input is unavailable", { cause: error });
  }
  if (
    data.length < TRACE_HEADER_BYTES || data.length > MAX_TRACE_BYTES
    || data.subarray(0, 4).toString("ascii") !== "ACRI"
    || ![1, 2].includes(data[4])
    || data[5] !== 0 || data[6] !== 0 || data[7] !== 0
  ) {
    throw new NativeAnalysisInputError("source_unavailable: raw input snapshot is invalid");
  }
  const count = data.readUInt32LE(8);
  if (count > MAX_TRACE_POINTS || data.length !== TRACE_HEADER_BYTES + count * TRACE_RECORD_BYTES) {
    throw new NativeAnalysisInputError("source_unavailable: raw input snapshot is invalid");
  }
  let firstTimestamp: bigint | null = null;
  let previousTimestamp: bigint | null = null;
  for (let index = 0; index < count; index += 1) {
    const offset = TRACE_HEADER_BYTES + index * TRACE_RECORD_BYTES;
    const timestamp = data.readBigInt64LE(offset);
    const buttons = data.readUInt32LE(offset + 16);
    if (
      (previousTimestamp !== null && timestamp < previousTimestamp)
      || (firstTimestamp !== null && timestamp - firstTimestamp > BigInt(MAX_TRACE_SPAN_MS))
      || (buttons & ~0b111) !== 0
    ) {
      throw new NativeAnalysisInputError("source_unavailable: raw input snapshot is invalid");
    }
    firstTimestamp ??= timestamp;
    previousTimestamp = timestamp;
  }
  const observed = fingerprintFile(resolve(path));
  return {
    artifact_ref: `run:${runId}:trace`,
    path: resolve(path),
    availability: "available",
    format_version: data[4],
    fingerprint: {
      sha256: observed.sha256,
      size: observed.size,
      mtime_ns: rawInteger(observed.mtimeNs.toString()),
    },
  };
}

function freezeVideo(runId: number, path: unknown, state: unknown, summary: AnyDict | null): AnyDict | null {
  if (state !== "attached" || typeof path !== "string" || !path) return null;
  if (!isRegularFile(path)) return null;
  const expected = summary?.fingerprint;
  if (
    expected === null || typeof expected !== "object" || Array.isArray(expected)
    || typeof expected.sha256 !== "string" || !SHA256_RE.test(expected.sha256)
    || !Number.isSafeInteger(expected.size) || expected.size < 0
  ) {
    throw new NativeAnalysisInputError("source_unavailable: video identity missing");
  }
  const observed = fingerprintFile(resolve(path));
  if (observed.sha256 !== expected.sha256.toLowerCase() || observed.size !== expected.size) {
    throw new NativeAnalysisInputError("source_unavailable: video revision changed");
  }
  return {
    artifact_ref: `run:${runId}:video:${observed.sha256.slice(0, 16)}`,
    basename: basename(path),
    fingerprint: { sha256: observed.sha256, size: observed.size },
    path: resolve(path),
    availability: "available",
    format_version: "mp4",
    ownership: "run",
  };
}

function canonicalWindow(run: AnyDict, summary: AnyDict | null): AnyDict | null {
  if (run.alignment_state !== "resolved") return null;
  const start = run.window_start_epoch_ms;
  const end = run.window_end_epoch_ms;
  if (
    !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || end <= start
    || summary === null
    || (summary.start_ms ?? start) !== start
    || (summary.end_ms ?? end) !== end
    || summary.duration_ms !== end - start
    || typeof summary.start_source !== "string" || !summary.start_source
    || typeof summary.end_source !== "string" || !summary.end_source
    || !Array.isArray(summary.warnings)
    || summary.warnings.some((item: unknown) => typeof item !== "string" || !item)
  ) {
    throw new NativeAnalysisInputError("source_unavailable: canonical time window is invalid");
  }
  return {
    schema_version: "canonical_time_window.v1",
    timebase_version: summary.timebase_version ?? "time_alignment.v2",
    start_ms: start,
    end_ms: end,
    duration_ms: end - start,
    start_source: summary.start_source,
    end_source: summary.end_source,
    stats_anchor_status: summary.stats_anchor_status ?? "missing",
    stats_time_of_day_ms: summary.stats_time_of_day_ms ?? null,
    stats_local_to_utc_mapping: summary.stats_local_to_utc_mapping ?? null,
    warnings: [...summary.warnings],
    window_semantics: "half_open",
  };
}

function scenarioAssets(): { registry: AnyDict; manifest: AnyDict } {
  const configured = process.env.AIMING_COOKIE_RESOURCE_ROOT?.trim();
  const root = configured ? resolve(configured) : SOURCE_ROOT;
  try {
    return {
      registry: JSON.parse(readFileSync(join(root, "knowledge", "scenarios", "registry.v1.json"), "utf8")),
      manifest: JSON.parse(readFileSync(join(root, "knowledge", "scenarios", "launch-manifest.v1.json"), "utf8")),
    };
  } catch (error) {
    throw new NativeAnalysisInputError("source_unavailable: scenario registry is unavailable", { cause: error });
  }
}

function normalizedName(value: string): string {
  return value.trim().split(/\s+/).join(" ").toLowerCase();
}

function scenarioDefinitionPath(scenario: string, statsPath: string): string | null {
  if (!/^[A-Za-z0-9][A-Za-z0-9 _.-]{0,159}$/.test(scenario)) return null;
  const configured = process.env.KOVAAK_INSTALL_DIR?.trim();
  const fpsAimTrainer = configured
    ? join(resolve(configured), "FPSAimTrainer")
    : resolve(dirname(statsPath), "..");
  return join(fpsAimTrainer, "Saved", "SaveGames", "Scenarios", `${scenario}.sce`);
}

function parseScenarioBehavior(scenario: unknown, statsPath: string): AnyDict | null {
  if (typeof scenario !== "string") return null;
  const path = scenarioDefinitionPath(scenario, statsPath);
  if (!path) return null;
  let data: Buffer;
  try {
    data = readFileSync(path);
  } catch {
    return null;
  }
  if (data.length === 0 || data.length > MAX_SCENARIO_DEFINITION_BYTES || data.includes(0)) return null;
  const text = data.toString("utf8");
  if (Buffer.from(text, "utf8").length !== data.length) return null;
  const lines = text.split(/\r?\n/);
  if (lines.length > MAX_SCENARIO_DEFINITION_LINES) return null;
  const root: Record<string, string> = {};
  const sections = new Map<string, Array<Record<string, string>>>();
  let current: Record<string, string> | null = null;
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith(";") || line.startsWith("#") || line.startsWith("//")) continue;
    if (line.startsWith("[") && line.endsWith("]")) {
      const kind = line.slice(1, -1).trim().toLowerCase();
      if (!kind || kind.length > 80) return null;
      const entries = sections.get(kind) ?? [];
      if (entries.length >= MAX_SCENARIO_LIST) return null;
      current = {};
      entries.push(current);
      sections.set(kind, entries);
      continue;
    }
    const separator = line.indexOf("=");
    if (separator < 0) {
      if (current) continue;
      return null;
    }
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim();
    if (!key || key.length > 80 || value.length > 2_000) return null;
    const target = current ?? root;
    if (Object.hasOwn(target, key)) return null;
    target[key] = value;
  }
  if (normalizedName(root.Name ?? "") !== normalizedName(scenario)) return null;
  const bots = (root.AddedBots ?? "").split(";").map((item) => item.trim()).filter(Boolean);
  if (bots.length < 2 || bots.length > MAX_SCENARIO_LIST) return null;
  const botProfiles = new Set(bots.map((item) => item.includes(".") ? item.slice(0, item.lastIndexOf(".")) : item));
  if (botProfiles.size !== 1) return null;

  function namedSection(kind: string, name: string | undefined): Record<string, string> | null {
    if (!name) return null;
    const matches = (sections.get(kind) ?? []).filter((item) => normalizedName(item.Name ?? "") === normalizedName(name));
    return matches.length === 1 ? matches[0] : null;
  }
  function number(value: string | undefined): number | null {
    if (value === undefined || !value.trim()) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  const botProfile = namedSection("bot profile", [...botProfiles][0]);
  if (!botProfile) return null;
  const character = namedSection("character profile", botProfile.CharacterProfile);
  const dodgeName = botProfile.DodgeProfileNames?.split(";").map((item) => item.trim()).find(Boolean);
  const dodge = namedSection("dodge profile", dodgeName);
  const maxSpeed = number(character?.MaxSpeed);
  if (!character || maxSpeed === null || maxSpeed < 0) return null;
  const axes: string[] = [];
  if (dodge?.ToggleLeftRight?.trim().toLowerCase() === "true") axes.push("horizontal");
  if (dodge?.ToggleForwardBack?.trim().toLowerCase() === "true") axes.push("depth");
  const reactive = maxSpeed > 0 && axes.length > 0;
  if (maxSpeed > 0 && !reactive) return null;

  const player = namedSection("character profile", root.PlayerCharacters);
  const weaponName = player?.WeaponProfileNames?.split(";").map((item) => item.trim()).find(Boolean);
  const weapon = namedSection("weapon profile", weaponName);
  const shots = number(weapon?.ShotsPerClick);
  const damage = number(weapon?.DamagePerShot);
  if (
    !weapon || weapon.Type?.toLowerCase() !== "hitscan" || weapon.Category?.toLowerCase() !== "semiauto"
    || shots !== 1 || damage === null || damage <= 0
  ) return null;
  return {
    schema_version: "scenario_behavior_descriptor.v1",
    display_name: scenario.trim(),
    source_sha256: createHash("sha256").update(data).digest("hex"),
    bot_count: bots.length,
    reactive_bot_count: reactive ? bots.length : 0,
    dodge_axes: reactive ? axes : [],
    weapon: {
      delivery: "hitscan",
      fire_mode: "semi_auto",
      shots_per_click: 1,
      damage_per_shot: damage,
    },
  };
}

function outcomeOnlyResolution(
  scenarioHash: string | null,
  displayName: string | null,
  registryVersion: string,
  manifestVersion: string,
  classificationSource = "unknown",
  classificationConfidence = "unknown",
  limitations = ["No reviewed exact scenario hash is available."],
): AnyDict {
  return {
    schema_version: "scenario_resolution.v1",
    scenario_hash: scenarioHash,
    display_name: displayName,
    registry_version: registryVersion,
    manifest_version: manifestVersion,
    scenario_profile_ref: null,
    classification_source: classificationSource,
    classification_confidence: classificationConfidence,
    profile_status: "unknown",
    reviewed_at: null,
    source_refs: [],
    supersedes: [],
    manifest_status: "unlisted",
    fixture_ref: null,
    review_source_ref: null,
    manifest_reviewed_at: null,
    family_gate_refs: [],
    aim_family: "unknown",
    subdomains: [],
    target_motion: { model: "unknown", target_count_model: "unknown" },
    allowed_analyzers: [],
    allowed_metric_families: [],
    claim_ceiling: "outcome_only",
    family_analyzer_dispatch: "none",
    limitations,
  };
}

function resolveScenario(scenarioHash: unknown, displayName: unknown, behaviorDescriptor: AnyDict | null): AnyDict {
  const { registry, manifest } = scenarioAssets();
  if (
    registry.schema_version !== "scenario_profile_registry.v1" || !Array.isArray(registry.entries)
    || manifest.schema_version !== "launch_scenario_manifest.v1" || !Array.isArray(manifest.entries)
    || typeof registry.registry_version !== "string" || typeof manifest.manifest_version !== "string"
  ) {
    throw new NativeAnalysisInputError("source_unavailable: scenario registry is invalid");
  }
  const safeHash = typeof scenarioHash === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(scenarioHash)
    ? scenarioHash
    : null;
  const safeName = typeof displayName === "string" && displayName.trim() && displayName.trim().length <= 240
    ? displayName.trim()
    : null;
  const profilesByHash = new Map<string, AnyDict>();
  for (const entry of [...registry.entries].sort((a, b) => Number(a.entry_version) - Number(b.entry_version))) {
    if (entry === null || typeof entry !== "object" || typeof entry.scenario_hash !== "string") continue;
    const previous = profilesByHash.get(entry.scenario_hash);
    if (!previous || entry.status === "active" || (previous.status !== "active" && entry.entry_version > previous.entry_version)) {
      profilesByHash.set(entry.scenario_hash, entry);
    }
  }
  const profile = safeHash ? profilesByHash.get(safeHash) : undefined;
  if (!profile) {
    if (behaviorDescriptor) {
      const aimFamily = behaviorDescriptor.reactive_bot_count === behaviorDescriptor.bot_count
        ? "dynamic_clicking"
        : "static_clicking";
      const motion = aimFamily === "dynamic_clicking" ? "reactive" : "static";
      return {
        ...outcomeOnlyResolution(
          safeHash, safeName, registry.registry_version, manifest.manifest_version,
          "local_scenario_definition", "confirmed",
          [
            "exact_visual_profile_unavailable",
            "target_relative_facts_unavailable",
            "outcome_association_unavailable",
            "scenario_prescription_unavailable",
          ],
        ),
        aim_family: aimFamily,
        subdomains: aimFamily === "dynamic_clicking" ? ["reactive", "control"] : ["precision", "control"],
        target_motion: {
          model: motion,
          target_count_model: behaviorDescriptor.bot_count === 1 ? "single" : "concurrent",
        },
        allowed_analyzers: [`${aimFamily}.baseline.v1`],
        allowed_metric_families: ["outcome", "input_kinematics"],
        claim_ceiling: "descriptive_only",
        family_analyzer_dispatch: "allowed",
      };
    }
    const candidates = registry.entries.filter((entry: AnyDict) => (
      entry?.status === "active" && safeName && typeof entry.display_name === "string"
      && entry.display_name.toLowerCase() === safeName.toLowerCase()
    ));
    return outcomeOnlyResolution(
      safeHash,
      safeName,
      registry.registry_version,
      manifest.manifest_version,
      candidates.length === 1 ? "name_heuristic" : "unknown",
      candidates.length === 1 ? "candidate" : "unknown",
      candidates.length === 1
        ? ["Display-name matching is only a review candidate, not a scenario identity."]
        : ["No reviewed exact scenario hash is available."],
    );
  }
  const profileRef = `scenario:${profile.entry_id}@${profile.entry_version}`;
  const manifestEntry = manifest.entries.find((entry: AnyDict) => entry?.scenario_hash === profile.scenario_hash);
  const manifestStatus = manifestEntry?.status ?? "unlisted";
  const dispatchAllowed = profile.status === "active" && manifestStatus === "active"
    && manifestEntry?.scenario_profile_ref === profileRef;
  const limitations = Array.isArray(profile.limitations) ? [...profile.limitations] : [];
  if (!dispatchAllowed) limitations.push("The launch manifest is not active; family-specific analysis is unavailable.");
  return {
    schema_version: "scenario_resolution.v1",
    scenario_hash: profile.scenario_hash,
    display_name: safeName ?? profile.display_name,
    registry_version: registry.registry_version,
    manifest_version: manifest.manifest_version,
    scenario_profile_ref: profileRef,
    classification_source: profile.taxonomy_source,
    classification_confidence: "confirmed",
    profile_status: profile.status,
    reviewed_at: profile.reviewed_at,
    source_refs: [...profile.source_refs],
    supersedes: [...profile.supersedes],
    manifest_status: manifestStatus,
    fixture_ref: manifestEntry?.fixture_ref ?? null,
    review_source_ref: manifestEntry?.review_source_ref ?? null,
    manifest_reviewed_at: manifestEntry?.reviewed_at ?? null,
    family_gate_refs: manifestEntry ? [...manifestEntry.family_gate_refs] : [],
    aim_family: profile.aim_family,
    subdomains: [...profile.subdomains],
    target_motion: { ...profile.target_motion },
    allowed_analyzers: [...profile.allowed_analyzers],
    allowed_metric_families: [...profile.allowed_metric_families],
    claim_ceiling: dispatchAllowed ? "family_specific" : "outcome_only",
    family_analyzer_dispatch: dispatchAllowed ? "allowed" : "none",
    limitations,
  };
}

function sourceGate(snapshot: AnyDict): {
  selectedMode: NativeAnalysisInput["selectedMode"] | null;
  missing: string[];
} {
  const window = snapshot.canonical_time_window;
  const canonical = window !== null && typeof window === "object" && !Array.isArray(window)
    && window.schema_version === "canonical_time_window.v1"
    && Number.isSafeInteger(window.start_ms) && window.start_ms >= 0
    && Number.isSafeInteger(window.end_ms) && window.end_ms > window.start_ms
    && Number.isSafeInteger(window.duration_ms) && window.duration_ms === window.end_ms - window.start_ms
    && window.window_semantics === "half_open"
    && ["time_alignment.v1", "time_alignment.v2"].includes(window.timebase_version)
    && [window.start_source, window.end_source].every((value) => (
      typeof value === "string" && value.length > 0 && value.length <= 80
    ))
    && Array.isArray(window.warnings)
    && window.warnings.every((value: unknown) => (
      typeof value === "string" && value.length > 0 && value.length <= 160
    ));
  const available = {
    stats: snapshot.sources.stats?.availability === "available",
    performance: snapshot.sources.performance?.availability === "available",
    raw: snapshot.trace?.availability === "available",
    video: snapshot.sources.video?.availability === "available",
    canonical,
  };
  const selectedMode = available.stats && available.performance && available.raw && available.video && available.canonical
    ? "multimodal"
    : available.stats && available.performance && available.raw && available.canonical
      ? "input_native"
      : available.stats && available.video
        ? "video_fallback"
        : null;
  const missing = [
    !available.stats && "stats_missing",
    !available.performance && "performance_missing",
    !available.raw && "raw_input_missing",
    !available.video && "video_missing",
    !available.canonical && "canonical_window_missing",
  ].filter((item): item is string => typeof item === "string");
  return { selectedMode, missing };
}

export function buildNativeAnalysisInput(db: SqliteDb, runId: number, ownerId: string): NativeAnalysisInput {
  const run = db.prepare(
    "SELECT kr.*, "
    + "CAST(json_extract(kr.stats_summary, '$.source.mtime_ns') AS TEXT) AS stats_mtime_ns_exact, "
    + "CAST(json_extract(kr.performance_summary, '$.source.mtime_ns') AS TEXT) AS performance_mtime_ns_exact "
    + "FROM kovaak_runs AS kr WHERE kr.id=? AND kr.user_id=?",
  ).get(runId, ownerId) as AnyDict | undefined;
  if (!run) throw new NativeAnalysisInputError("not_found:KovaaK run does not exist");

  const statsSummary = parseObject(run.stats_summary);
  const performanceSummary = parseObject(run.performance_summary);
  const sources: AnyDict = {};
  const stats = freezeSummarySource(
    runId, "stats", run.stats_path, statsSummary, run.stats_mtime_ns_exact,
  );
  if (stats) sources.stats = stats;
  const performance = freezeSummarySource(
    runId, "performance", run.performance_path, performanceSummary, run.performance_mtime_ns_exact,
  );
  if (performance) sources.performance = performance;
  const video = freezeVideo(runId, run.video_path, run.video_state, parseObject(run.video_summary_json));
  if (video) sources.video = video;
  const trace = freezeTrace(runId, run.mouse_trace_path, run.trace_state);
  const window = canonicalWindow(run, parseObject(run.alignment_summary));
  const scenarioHash = performanceSummary?.header?.scenario_hash;
  const behaviorDescriptor = parseScenarioBehavior(
    run.scenario,
    typeof run.stats_path === "string" ? run.stats_path : "",
  );
  const scenarioResolution = resolveScenario(scenarioHash, run.scenario, behaviorDescriptor);
  const snapshot: AnyDict = {
    schema_version: "analysis_input_snapshot.v3",
    run_id: runId,
    scenario: typeof run.scenario === "string" ? run.scenario : null,
    scenario_identity_version: "kovaak_scenario.v1",
    scenario_resolution: scenarioResolution,
    scenario_behavior_descriptor: behaviorDescriptor,
    sources,
    trace,
    canonical_time_window: window,
    source_requirements_version: "automatic_quality_tier.v1",
  };
  const gate = sourceGate(snapshot);
  if (!gate.selectedMode || !stats) {
    throw new NativeAnalysisInputError(`input_unavailable: required Run sources are unavailable: ${gate.missing.join(", ")}`);
  }
  const analysisType = ({
    dynamic_clicking: "dynamic_clicking",
    continuous_tracking: "continuous_tracking",
    target_switching: "target_switching",
  } as Record<string, string>)[scenarioResolution.aim_family]
    ?? (
      scenarioResolution.aim_family === "static_clicking"
      && scenarioResolution.allowed_analyzers.includes("static_clicking.baseline.v1")
        ? "static_clicking"
        : "flicking"
    );
  return {
    analysisType,
    limitations: gate.missing,
    selectedMode: gate.selectedMode,
    snapshot,
    snapshotJson: stringifyWithRawIntegers(snapshot),
    statsPath: stats.path,
    statsFingerprint: stats.fingerprint,
    videoPath: video?.path ?? null,
    videoFingerprint: video?.fingerprint ?? null,
  };
}

export function assertFrozenCopy(path: string, fingerprint: FrozenFingerprint): void {
  const observed = fingerprintFile(path);
  if (observed.sha256 !== fingerprint.sha256 || observed.size !== fingerprint.size) {
    throw new NativeAnalysisInputError("source_unavailable: managed copy does not match the frozen source");
  }
}

export function assertSameFile(leftPath: string, rightPath: string): void {
  const left = statSync(leftPath, { bigint: true });
  const right = statSync(rightPath, { bigint: true });
  if (left.dev !== right.dev || left.ino !== right.ino) {
    throw new NativeAnalysisInputError("source_unavailable: Run video changed before managed link");
  }
}
