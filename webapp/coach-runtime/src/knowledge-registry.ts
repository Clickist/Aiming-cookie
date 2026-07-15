import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { isRecord } from "./contracts.ts";

export const REGISTRY_SCHEMA_VERSION = "coach_knowledge_registry.v1";
const REGISTRY_FILE = join(
  dirname(fileURLToPath(import.meta.url)),
  "..", "..", "..", "knowledge", "coach", "registry.v1.json",
);
const MAX_REGISTRY_BYTES = 512 * 1024;
const MAX_ENTRIES = 512;
const MAX_RESULTS = 3;
const MAX_TEXT_LENGTH = 4_000;
const MAX_LIST_LENGTH = 64;
const STATUSES = new Set(["active", "retired"]);
const CATEGORIES = new Set([
  "metric_definition", "kinematic_mechanism", "diagnostic_scope", "research",
  "training_cue", "prescription_verification", "practice_structure",
  "body_tension_hypothesis", "settings_experiment", "limitation_counterevidence",
]);
const SOURCE_LEVELS = new Set([
  "product_contract", "academic_peer_reviewed", "community_consensus",
  "personal_experience_unverified", "experimental",
]);
const CLAIM_LEVELS = new Set([
  "deterministic_rule", "research_supported", "community_consensus", "experimental",
]);
const SUPPORTED_USES = new Set([
  "definition", "mechanism", "diagnostic_scope", "research_context",
  "training_cue", "practice_structure", "candidate_hypothesis", "verification",
]);
const ENTRY_FIELDS = new Set([
  "entry_id", "entry_version", "status", "category", "topics", "signals",
  "metric_refs", "text", "sources", "max_claim_level", "limitations",
  "counterevidence", "supported_uses",
]);
const ENTRY_ID = /^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$/;
const TOKEN = /^[A-Za-z0-9][A-Za-z0-9._:/ -]{0,159}$/;
const PATH = /^(?:\/|\\|~\/|\.\.[/\\]|[A-Za-z]:[/\\]|file:\/\/)/i;
const SECRET = /(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)\s*[:=]|\bbearer\s+\S+|\bsk-[a-z0-9_-]{8,}/i;

export type KnowledgeSource = { source_ref: string; source_level: string };
export type KnowledgeEntry = {
  entry_id: string;
  entry_version: number;
  status: "active" | "retired";
  category: string;
  topics: string[];
  signals: string[];
  metric_refs: string[];
  text: string;
  sources: KnowledgeSource[];
  max_claim_level: string;
  limitations: string[];
  counterevidence: string[];
  supported_uses: string[];
};
export type KnowledgeRegistry = {
  schema_version: typeof REGISTRY_SCHEMA_VERSION;
  registry_version: string;
  signal_aliases: Record<string, string>;
  entries: KnowledgeEntry[];
};
export type KnowledgeQuery = {
  topic?: string;
  issue_signal?: string;
  metric_refs?: string[];
  supported_use?: string;
};

export class KnowledgeRegistryError extends Error {}

function keysEqual(value: Record<string, unknown>, expected: Set<string>): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}

function text(value: unknown, field: string, maxLength = 500): string {
  if (typeof value !== "string" || value.trim().length === 0 || value.trim().length > maxLength) {
    throw new KnowledgeRegistryError(`${field} must be bounded text`);
  }
  const result = value.trim();
  if (PATH.test(result) || SECRET.test(result)) throw new KnowledgeRegistryError(`${field} contains unsafe text`);
  return result;
}

function stringList(value: unknown, field: string, allowEmpty = true): string[] {
  if (!Array.isArray(value) || value.length > MAX_LIST_LENGTH || (!allowEmpty && value.length === 0)) {
    throw new KnowledgeRegistryError(`${field} has invalid length`);
  }
  const result = value.map((item) => text(item, field));
  if (new Set(result).size !== result.length) throw new KnowledgeRegistryError(`${field} contains duplicates`);
  return result;
}

function validateEntry(raw: unknown, index: number): KnowledgeEntry {
  if (!isRecord(raw) || !keysEqual(raw, ENTRY_FIELDS)) {
    throw new KnowledgeRegistryError(`entry[${index}] fields are invalid`);
  }
  const entryId = text(raw.entry_id, "entry_id", 160);
  if (!ENTRY_ID.test(entryId)) throw new KnowledgeRegistryError(`entry[${index}].entry_id is invalid`);
  if (!Number.isInteger(raw.entry_version) || Number(raw.entry_version) < 1) {
    throw new KnowledgeRegistryError(`entry[${index}].entry_version is invalid`);
  }
  if (typeof raw.status !== "string" || !STATUSES.has(raw.status)) throw new KnowledgeRegistryError("status is invalid");
  if (typeof raw.category !== "string" || !CATEGORIES.has(raw.category)) throw new KnowledgeRegistryError("category is invalid");
  const topics = stringList(raw.topics, "topics", false);
  const signals = stringList(raw.signals, "signals");
  const metricRefs = stringList(raw.metric_refs, "metric_refs");
  if (![...topics, ...signals, ...metricRefs].every((item) => TOKEN.test(item))) {
    throw new KnowledgeRegistryError("entry contains invalid token");
  }
  const content = text(raw.text, "text", MAX_TEXT_LENGTH);
  if (!Array.isArray(raw.sources) || raw.sources.length === 0 || raw.sources.length > 12) {
    throw new KnowledgeRegistryError("sources has invalid length");
  }
  const sources = raw.sources.map((item): KnowledgeSource => {
    if (!isRecord(item) || !keysEqual(item, new Set(["source_ref", "source_level"]))) {
      throw new KnowledgeRegistryError("source fields are invalid");
    }
    const sourceRef = text(item.source_ref, "source_ref", 240);
    if (typeof item.source_level !== "string" || !SOURCE_LEVELS.has(item.source_level)) {
      throw new KnowledgeRegistryError("source_level is invalid");
    }
    return { source_ref: sourceRef, source_level: item.source_level };
  });
  if (typeof raw.max_claim_level !== "string" || !CLAIM_LEVELS.has(raw.max_claim_level)) {
    throw new KnowledgeRegistryError("max_claim_level is invalid");
  }
  const limitations = stringList(raw.limitations, "limitations", false);
  const counterevidence = stringList(raw.counterevidence, "counterevidence");
  const supportedUses = stringList(raw.supported_uses, "supported_uses", false);
  if (supportedUses.some((item) => !SUPPORTED_USES.has(item))) throw new KnowledgeRegistryError("supported_uses is invalid");
  const levels = new Set(sources.map((source) => source.source_level));
  if (levels.has("community_consensus") && !["community_consensus", "experimental"].includes(raw.max_claim_level)) {
    throw new KnowledgeRegistryError("community source exceeds claim level");
  }
  if ([...levels].some((level) => level === "personal_experience_unverified" || level === "experimental") && raw.max_claim_level !== "experimental") {
    throw new KnowledgeRegistryError("personal source must stay experimental");
  }
  if (["body_tension_hypothesis", "settings_experiment"].includes(raw.category) && raw.max_claim_level !== "experimental") {
    throw new KnowledgeRegistryError("category must stay experimental");
  }
  return {
    entry_id: entryId,
    entry_version: Number(raw.entry_version),
    status: raw.status as "active" | "retired",
    category: raw.category,
    topics,
    signals,
    metric_refs: metricRefs,
    text: content,
    sources,
    max_claim_level: raw.max_claim_level,
    limitations,
    counterevidence,
    supported_uses: supportedUses,
  };
}

export function validateKnowledgeRegistry(raw: unknown): KnowledgeRegistry {
  if (!isRecord(raw) || !keysEqual(raw, new Set(["schema_version", "registry_version", "signal_aliases", "entries"]))) {
    throw new KnowledgeRegistryError("registry fields are invalid");
  }
  if (raw.schema_version !== REGISTRY_SCHEMA_VERSION) throw new KnowledgeRegistryError("schema_version is invalid");
  const registryVersion = text(raw.registry_version, "registry_version", 80);
  if (!isRecord(raw.signal_aliases) || Object.keys(raw.signal_aliases).length > 128) {
    throw new KnowledgeRegistryError("signal_aliases is invalid");
  }
  const aliases: Record<string, string> = {};
  for (const [alias, canonicalRaw] of Object.entries(raw.signal_aliases)) {
    const aliasText = text(alias, "signal alias", 120);
    const canonical = text(canonicalRaw, "canonical signal", 120);
    if (aliasText === canonical || Object.hasOwn(raw.signal_aliases, canonical)) throw new KnowledgeRegistryError("signal alias is invalid");
    aliases[aliasText] = canonical;
  }
  if (!Array.isArray(raw.entries) || raw.entries.length < 1 || raw.entries.length > MAX_ENTRIES) {
    throw new KnowledgeRegistryError("entries has invalid length");
  }
  const entries = raw.entries.map(validateEntry);
  const seen = new Set<string>();
  const active = new Set<string>();
  for (const entry of entries) {
    const key = `${entry.entry_id}@${entry.entry_version}`;
    if (seen.has(key)) throw new KnowledgeRegistryError("duplicate entry version");
    seen.add(key);
    if (entry.status === "active") {
      if (active.has(entry.entry_id)) throw new KnowledgeRegistryError("multiple active versions");
      active.add(entry.entry_id);
    }
  }
  return structuredClone({
    schema_version: REGISTRY_SCHEMA_VERSION,
    registry_version: registryVersion,
    signal_aliases: aliases,
    entries,
  });
}

let cached: KnowledgeRegistry | undefined;
export function loadKnowledgeRegistry(): KnowledgeRegistry {
  if (cached) return structuredClone(cached);
  const raw = readFileSync(REGISTRY_FILE);
  if (raw.byteLength > MAX_REGISTRY_BYTES) throw new KnowledgeRegistryError("registry exceeds size limit");
  try {
    cached = validateKnowledgeRegistry(JSON.parse(raw.toString("utf8")));
  } catch (error) {
    if (error instanceof KnowledgeRegistryError) throw error;
    throw new KnowledgeRegistryError("registry is invalid JSON");
  }
  return structuredClone(cached);
}

export function entryRef(entry: Pick<KnowledgeEntry, "entry_id" | "entry_version">): string {
  return `knowledge:${entry.entry_id}@${entry.entry_version}`;
}

function clean(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

export function queryKnowledgeRegistry(registry: KnowledgeRegistry, query: KnowledgeQuery): KnowledgeEntry[] {
  const topic = clean(query.topic);
  const signal = clean(query.issue_signal);
  const metrics = new Set((query.metric_refs ?? []).filter((item) => typeof item === "string" && item.trim()).map((item) => item.trim()));
  const supportedUse = clean(query.supported_use);
  if (!topic && !signal && metrics.size === 0 && !supportedUse) throw new KnowledgeRegistryError("at least one query condition is required");
  const canonical = signal ? registry.signal_aliases[signal] ?? signal : undefined;
  return registry.entries
    .filter((entry) => entry.status === "active")
    .map((entry) => {
      let score = 0;
      if (canonical && entry.signals.includes(canonical)) score += 16;
      if (metrics.size && entry.metric_refs.some((metric) => metrics.has(metric))) score += 8;
      if (topic && entry.topics.includes(topic)) score += 4;
      if (supportedUse && entry.supported_uses.includes(supportedUse)) score += 2;
      return { entry, score };
    })
    .filter(({ score }) => score > 0)
    .sort((left, right) => right.score - left.score || left.entry.entry_id.localeCompare(right.entry.entry_id) || right.entry.entry_version - left.entry.entry_version)
    .slice(0, MAX_RESULTS)
    .map(({ entry }) => structuredClone(entry));
}
