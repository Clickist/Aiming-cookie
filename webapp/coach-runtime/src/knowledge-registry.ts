import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { isRecord } from "./contracts.ts";

export const REGISTRY_SCHEMA_VERSION_V1 = "coach_knowledge_registry.v1";
export const REGISTRY_SCHEMA_VERSION_V2 = "coach_knowledge_registry.v2";
export const REGISTRY_SCHEMA_VERSION_V3 = "coach_knowledge_registry.v3";
export const REGISTRY_SCHEMA_VERSION = REGISTRY_SCHEMA_VERSION_V3;
const SOURCE_KNOWLEDGE_ROOT = join(
  dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "knowledge",
);
function knowledgeRoots(): { registry: string; scenarios: string } {
  const resourceRoot = process.env.AIMING_COOKIE_RESOURCE_ROOT?.trim();
  const root = resourceRoot
    ? resolve(resourceRoot, "knowledge")
    : SOURCE_KNOWLEDGE_ROOT;
  return { registry: join(root, "coach"), scenarios: join(root, "scenarios") };
}

function registryFiles(): Map<string, string> {
  const { registry } = knowledgeRoots();
  return new Map([
    ["2026-07-14.v1", join(registry, "registry.v1.json")],
    ["2026-07-22.v2", join(registry, "registry.v2.json")],
    ["2026-07-28.v3", join(registry, "registry.v3.json")],
    ["2026-07-29.v4", join(registry, "registry.v4.json")],
    ["2026-08-06.v5", join(registry, "registry.v5.json")],
    ["2026-08-06.v6", join(registry, "registry.v6.json")],
  ]);
}
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
const SOURCE_LEVELS_V2 = new Set([
  "product_contract", "academic_peer_reviewed", "community_organization",
  "coach_first_party", "community_consensus", "personal_experience_unverified",
  "experimental",
]);
const CLAIM_LEVELS_V2 = new Set([
  "deterministic_rule", "research_supported", "community_practice",
  "community_consensus", "experimental",
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
const SUPPORTED_USES_V3 = new Set([
  "explanation_only", "diagnosis_support", "candidate_experiment", "scenario_prescription",
]);
const CAPABILITY_PREFIXES_V3 = [
  ["explanation_only"],
  ["explanation_only", "diagnosis_support"],
  ["explanation_only", "diagnosis_support", "candidate_experiment"],
  ["explanation_only", "diagnosis_support", "candidate_experiment", "scenario_prescription"],
];
const ENTRY_FIELDS_V2 = new Set([
  "entry_id", "entry_version", "status", "category", "topics", "signals",
  "metric_refs", "family_scope", "observation_refs", "quality_prerequisites",
  "definition", "scope", "expected_direction", "mechanisms",
  "alternative_explanations", "forbidden_inferences", "limitations",
  "counterevidence", "cue", "dose_guardrail", "matched_retest",
  "near_transfer_retest", "stop_adjust_rule", "sources", "supported_uses",
]);
const SCENARIO_PRESCRIPTION_FIELD = "scenario_prescription";
const SCENARIO_PROFILE_REF = /^scenario:[A-Za-z0-9._:@-]+$/;
const SCENARIO_REVIEW_AFTER = new Set([
  "next comparable practice session", "next matched retest", "after one comparable practice block",
]);

export function activeScenarioProfileRefs(
  scenarioRegistryRaw: unknown = JSON.parse(
    readFileSync(join(knowledgeRoots().scenarios, "registry.v1.json"), "utf8"),
  ),
  scenarioManifestRaw: unknown = JSON.parse(
    readFileSync(join(knowledgeRoots().scenarios, "launch-manifest.v1.json"), "utf8"),
  ),
): Set<string> {
  if (!isRecord(scenarioRegistryRaw) || !Array.isArray(scenarioRegistryRaw.entries)) {
    throw new KnowledgeRegistryError("scenario registry is invalid");
  }
  if (!isRecord(scenarioManifestRaw) || !Array.isArray(scenarioManifestRaw.entries)) {
    throw new KnowledgeRegistryError("scenario manifest is invalid");
  }
  const registryRefs = new Set<string>();
  for (const entry of scenarioRegistryRaw.entries) {
    if (!isRecord(entry) || entry.status !== "active") continue;
    const entryId = text(entry.entry_id, "scenario entry_id", 160);
    if (!Number.isInteger(entry.entry_version) || Number(entry.entry_version) < 1) {
      throw new KnowledgeRegistryError("scenario entry_version is invalid");
    }
    const ref = `scenario:${entryId}@${entry.entry_version}`;
    if (!SCENARIO_PROFILE_REF.test(ref)) {
      throw new KnowledgeRegistryError("scenario profile ref is invalid");
    }
    registryRefs.add(ref);
  }
  const manifestRefs = new Set<string>();
  for (const entry of scenarioManifestRaw.entries) {
    if (!isRecord(entry) || entry.status !== "active") continue;
    const ref = text(entry.scenario_profile_ref, "scenario_profile_ref", 200);
    if (!SCENARIO_PROFILE_REF.test(ref)) {
      throw new KnowledgeRegistryError("scenario profile ref is invalid");
    }
    manifestRefs.add(ref);
  }
  return new Set([...registryRefs].filter((ref) => manifestRefs.has(ref)));
}
const SOURCE_FIELDS_V2 = new Set([
  "source_ref", "source_level", "title", "author_or_org", "published_at",
  "retrieved_at", "locator", "applicability", "supports_sections",
]);
const SECTION_FIELDS_V2 = new Set(["section_ref", "claim_level", "source_refs", "text"]);
const CATEGORIES_V2 = new Set([
  "observation_definition", "mechanism", "training_cue",
  "prescription_verification", "limitation", "outcome_only",
]);
const FAMILIES_V2 = new Set([
  "static_clicking", "dynamic_clicking", "predictable_tracking",
  "reactive_tracking", "control_tracking", "target_switching", "movement_aiming",
]);
const DIRECTIONS_V2 = new Set([
  "lower_better", "higher_better", "target_band", "descriptive_only", "comparison_only",
]);
const ENTRY_ID = /^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$/;
const TOKEN = /^[A-Za-z0-9][A-Za-z0-9._:/ -]{0,159}$/;
const PATH = /^(?:\/|\\|~\/|\.\.[/\\]|[A-Za-z]:[/\\]|file:\/\/)/i;
const SECRET = /(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)\s*[:=]|\bbearer\s+\S+|\bsk-[a-z0-9_-]{8,}/i;

export type KnowledgeSourceV1 = { source_ref: string; source_level: string };
export type KnowledgeEntryV1 = {
  entry_id: string;
  entry_version: number;
  status: "active" | "retired";
  category: string;
  topics: string[];
  signals: string[];
  metric_refs: string[];
  text: string;
  sources: KnowledgeSourceV1[];
  max_claim_level: string;
  limitations: string[];
  counterevidence: string[];
  supported_uses: string[];
};
export type KnowledgeSourceV2 = {
  source_ref: string;
  source_level: string;
  title: string;
  author_or_org: string;
  published_at: string | null;
  retrieved_at: string;
  locator: string;
  applicability: string[];
  supports_sections: string[];
};
export type KnowledgeSectionV2 = {
  section_ref: string;
  claim_level: string;
  source_refs: string[];
  text: string;
};
export type ScenarioPrescriptionV2 = {
  scenario_profile_ref: string;
  practice_condition: string;
  review_after: string;
  source_refs: string[];
  claim_level: string;
};
export type KnowledgeEntryV2 = {
  entry_id: string;
  entry_version: number;
  status: "active" | "retired";
  category: string;
  topics: string[];
  signals: string[];
  metric_refs: string[];
  family_scope: string[];
  observation_refs: string[];
  quality_prerequisites: string[];
  definition: KnowledgeSectionV2;
  scope: KnowledgeSectionV2;
  expected_direction: KnowledgeSectionV2;
  mechanisms: KnowledgeSectionV2[];
  alternative_explanations: string[];
  forbidden_inferences: string[];
  limitations: string[];
  counterevidence: string[];
  cue?: KnowledgeSectionV2 | "not_applicable";
  dose_guardrail?: KnowledgeSectionV2[] | "not_applicable";
  matched_retest?: KnowledgeSectionV2 | "not_applicable";
  near_transfer_retest?: KnowledgeSectionV2 | "not_applicable";
  stop_adjust_rule?: KnowledgeSectionV2[] | "not_applicable";
  scenario_prescription?: ScenarioPrescriptionV2 | "not_applicable";
  sources: string[];
  supported_uses: string[];
};
export type KnowledgeEntry = KnowledgeEntryV1 | KnowledgeEntryV2;
export type KnowledgeRegistry = {
  schema_version: typeof REGISTRY_SCHEMA_VERSION_V1 | typeof REGISTRY_SCHEMA_VERSION_V2 | typeof REGISTRY_SCHEMA_VERSION_V3;
  registry_version: string;
  signal_aliases: Record<string, string>;
  sources?: KnowledgeSourceV2[];
  entries: KnowledgeEntry[];
};
export type KnowledgeQuery = {
  registry_version?: string;
  entry_ref?: string;
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

function validateEntryV1(raw: unknown, index: number): KnowledgeEntryV1 {
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
  const sources = raw.sources.map((item): KnowledgeSourceV1 => {
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

function validateKnowledgeRegistryV1(raw: unknown): KnowledgeRegistry {
  if (!isRecord(raw) || !keysEqual(raw, new Set(["schema_version", "registry_version", "signal_aliases", "entries"]))) {
    throw new KnowledgeRegistryError("registry fields are invalid");
  }
  if (raw.schema_version !== REGISTRY_SCHEMA_VERSION_V1) throw new KnowledgeRegistryError("schema_version is invalid");
  const registryVersion = text(raw.registry_version, "registry_version", 80);
  if (!isRecord(raw.signal_aliases) || Object.keys(raw.signal_aliases).length > 128) {
    throw new KnowledgeRegistryError("signal_aliases is invalid");
  }
  const aliases: Record<string, string> = {};
  for (const [alias, canonicalRaw] of Object.entries(raw.signal_aliases)) {
    const aliasText = text(alias, "signal alias", 120);
    const canonical = text(canonicalRaw, "canonical signal", 120);
    if (!TOKEN.test(aliasText) || !TOKEN.test(canonical) || aliasText === canonical || Object.hasOwn(raw.signal_aliases, canonical)) {
      throw new KnowledgeRegistryError("signal alias is invalid");
    }
    aliases[aliasText] = canonical;
  }
  if (!Array.isArray(raw.entries) || raw.entries.length < 1 || raw.entries.length > MAX_ENTRIES) {
    throw new KnowledgeRegistryError("entries has invalid length");
  }
  const entries = raw.entries.map(validateEntryV1);
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
    schema_version: REGISTRY_SCHEMA_VERSION_V1,
    registry_version: registryVersion,
    signal_aliases: aliases,
    entries,
  });
}

function validateSourceV2(raw: unknown, index: number): KnowledgeSourceV2 {
  if (!isRecord(raw) || !keysEqual(raw, SOURCE_FIELDS_V2)) {
    throw new KnowledgeRegistryError(`source[${index}] fields are invalid`);
  }
  const sourceRef = text(raw.source_ref, `source[${index}].source_ref`, 160);
  if (!TOKEN.test(sourceRef)) throw new KnowledgeRegistryError("source_ref is invalid");
  if (typeof raw.source_level !== "string" || !SOURCE_LEVELS_V2.has(raw.source_level)) {
    throw new KnowledgeRegistryError("source_level is invalid");
  }
  const publishedAt = raw.published_at === null ? null : text(raw.published_at, "published_at", 32);
  const retrievedAt = text(raw.retrieved_at, "retrieved_at", 32);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(retrievedAt)) throw new KnowledgeRegistryError("retrieved_at is invalid");
  const applicability = stringList(raw.applicability, "applicability", false);
  const supportsSections = stringList(raw.supports_sections, "supports_sections", false);
  if (![...applicability, ...supportsSections].every((item) => TOKEN.test(item))) {
    throw new KnowledgeRegistryError("source contains invalid token");
  }
  return {
    source_ref: sourceRef,
    source_level: raw.source_level,
    title: text(raw.title, "title", 1200),
    author_or_org: text(raw.author_or_org, "author_or_org", 1200),
    published_at: publishedAt,
    retrieved_at: retrievedAt,
    locator: text(raw.locator, "locator", 1200),
    applicability,
    supports_sections: supportsSections,
  };
}

const CLAIM_RANK: Record<string, number> = {
  experimental: 0,
  community_practice: 1,
  community_consensus: 2,
  research_supported: 3,
  deterministic_rule: 4,
};
const SOURCE_CEILING: Record<string, string> = {
  experimental: "experimental",
  personal_experience_unverified: "experimental",
  coach_first_party: "community_practice",
  community_organization: "community_practice",
  community_consensus: "community_consensus",
  academic_peer_reviewed: "research_supported",
  product_contract: "deterministic_rule",
};

function validateSectionV2(
  raw: unknown,
  entryId: string,
  entryFamilyScope: Set<string>,
  sectionName: string,
  entrySources: Set<string>,
  sources: Map<string, KnowledgeSourceV2>,
): KnowledgeSectionV2 {
  if (!isRecord(raw) || !keysEqual(raw, SECTION_FIELDS_V2)) {
    throw new KnowledgeRegistryError(`${sectionName} fields are invalid`);
  }
  const sectionRef = text(raw.section_ref, `${sectionName}.section_ref`, 160);
  if (!TOKEN.test(sectionRef) || !sectionRef.startsWith(`${entryId}.`)) {
    throw new KnowledgeRegistryError(`${sectionName}.section_ref is invalid`);
  }
  if (typeof raw.claim_level !== "string" || !CLAIM_LEVELS_V2.has(raw.claim_level)) {
    throw new KnowledgeRegistryError(`${sectionName}.claim_level is invalid`);
  }
  const sourceRefs = stringList(raw.source_refs, `${sectionName}.source_refs`, false);
  for (const sourceRef of sourceRefs) {
    const source = sources.get(sourceRef);
    if (!entrySources.has(sourceRef) || !source) throw new KnowledgeRegistryError("unknown section source");
    if (!source.supports_sections.includes(sectionName)) throw new KnowledgeRegistryError("unsupported section source");
    const applicability = new Set(source.applicability);
    if (!applicability.has("all_families") && [...entryFamilyScope].some((family) => !applicability.has(family))) {
      throw new KnowledgeRegistryError("section source does not cover the entry family scope");
    }
    if ((CLAIM_RANK[raw.claim_level] ?? 99) > (CLAIM_RANK[SOURCE_CEILING[source.source_level] ?? ""] ?? -1)) {
      throw new KnowledgeRegistryError("claim level exceeds source ceiling");
    }
  }
  return {
    section_ref: sectionRef,
    claim_level: raw.claim_level,
    source_refs: sourceRefs,
    text: text(raw.text, `${sectionName}.text`, 1200),
  };
}

function validateScenarioPrescriptionV2(
  raw: unknown,
  entryFamilyScope: Set<string>,
  entrySources: Set<string>,
  sources: Map<string, KnowledgeSourceV2>,
  activeScenarioRefs: Set<string>,
): ScenarioPrescriptionV2 | "not_applicable" {
  if (raw === "not_applicable") return raw;
  const fields = new Set([
    "scenario_profile_ref", "practice_condition", "review_after", "source_refs", "claim_level",
  ]);
  if (!isRecord(raw) || !keysEqual(raw, fields)) {
    throw new KnowledgeRegistryError("scenario_prescription fields are invalid");
  }
  const scenarioProfileRef = text(raw.scenario_profile_ref, "scenario_prescription.scenario_profile_ref", 200);
  if (!SCENARIO_PROFILE_REF.test(scenarioProfileRef)) {
    throw new KnowledgeRegistryError("scenario_prescription.scenario_profile_ref is invalid");
  }
  if (!activeScenarioRefs.has(scenarioProfileRef)) {
    throw new KnowledgeRegistryError("scenario_prescription.scenario_profile_ref is not an active scenario");
  }
  const practiceCondition = text(raw.practice_condition, "scenario_prescription.practice_condition", 500);
  if (typeof raw.review_after !== "string" || !SCENARIO_REVIEW_AFTER.has(raw.review_after)) {
    throw new KnowledgeRegistryError("scenario_prescription.review_after is invalid");
  }
  if (typeof raw.claim_level !== "string" || !CLAIM_LEVELS_V2.has(raw.claim_level)) {
    throw new KnowledgeRegistryError("scenario_prescription.claim_level is invalid");
  }
  const sourceRefs = stringList(raw.source_refs, "scenario_prescription.source_refs", false);
  for (const sourceRef of sourceRefs) {
    const source = sources.get(sourceRef);
    if (!entrySources.has(sourceRef) || !source) throw new KnowledgeRegistryError("scenario_prescription source_refs are invalid");
    if (!source.supports_sections.includes(SCENARIO_PRESCRIPTION_FIELD)) {
      throw new KnowledgeRegistryError("scenario_prescription source lacks support");
    }
    const applicability = new Set(source.applicability);
    if (!applicability.has("all_families") && [...entryFamilyScope].some((family) => !applicability.has(family))) {
      throw new KnowledgeRegistryError("scenario_prescription source does not cover the entry family scope");
    }
    if ((CLAIM_RANK[raw.claim_level] ?? 99) > (CLAIM_RANK[SOURCE_CEILING[source.source_level] ?? ""] ?? -1)) {
      throw new KnowledgeRegistryError("scenario_prescription claim level exceeds source ceiling");
    }
  }
  return {
    scenario_profile_ref: scenarioProfileRef,
    practice_condition: practiceCondition,
    review_after: raw.review_after,
    source_refs: sourceRefs,
    claim_level: raw.claim_level,
  };
}

function validateEntryV2(
  raw: unknown,
  index: number,
  sources: Map<string, KnowledgeSourceV2>,
  requiresScenarioPrescription: boolean,
  activeScenarioRefs: Set<string>,
  supportedUsesAllowed = SUPPORTED_USES,
  allowNonOutcomeNotApplicable = false,
  allowEmptyObservationContext = false,
): KnowledgeEntryV2 {
  const entryFields = new Set(ENTRY_FIELDS_V2);
  if (isRecord(raw) && SCENARIO_PRESCRIPTION_FIELD in raw) entryFields.add(SCENARIO_PRESCRIPTION_FIELD);
  if (!isRecord(raw) || !keysEqual(raw, entryFields)) {
    throw new KnowledgeRegistryError(`entry[${index}] fields are invalid`);
  }
  if (requiresScenarioPrescription && !(SCENARIO_PRESCRIPTION_FIELD in raw)) {
    throw new KnowledgeRegistryError("scenario_prescription is required");
  }
  const entryId = text(raw.entry_id, "entry_id", 160);
  if (!ENTRY_ID.test(entryId)) throw new KnowledgeRegistryError("entry_id is invalid");
  if (!Number.isInteger(raw.entry_version) || Number(raw.entry_version) < 1) throw new KnowledgeRegistryError("entry_version is invalid");
  if (typeof raw.status !== "string" || !STATUSES.has(raw.status)) throw new KnowledgeRegistryError("status is invalid");
  if (typeof raw.category !== "string" || !CATEGORIES_V2.has(raw.category)) throw new KnowledgeRegistryError("category is invalid");
  const lists: Record<string, string[]> = {};
  for (const [name, allowEmpty] of [
    ["topics", false], ["signals", true], ["metric_refs", true],
    ["family_scope", false], ["observation_refs", allowEmptyObservationContext],
    ["quality_prerequisites", allowEmptyObservationContext], ["sources", false], ["supported_uses", false],
  ] as const) {
    lists[name] = stringList(raw[name], name, allowEmpty);
    if (!lists[name].every((item) => TOKEN.test(item))) throw new KnowledgeRegistryError(`${name} contains invalid token`);
  }
  if (lists.family_scope.some((item) => !FAMILIES_V2.has(item))) throw new KnowledgeRegistryError("family_scope is invalid");
  if (lists.sources.some((item) => !sources.has(item))) throw new KnowledgeRegistryError("entry source is unknown");
  if (lists.supported_uses.some((item) => !supportedUsesAllowed.has(item))) throw new KnowledgeRegistryError("supported_uses is invalid");
  const entrySources = new Set(lists.sources);
  const entryFamilyScope = new Set(lists.family_scope);
  const section = (name: string, value: unknown = raw[name]) => (
    validateSectionV2(value, entryId, entryFamilyScope, name, entrySources, sources)
  );
  const definition = section("definition");
  const scope = section("scope");
  const expectedDirection = section("expected_direction");
  if (!DIRECTIONS_V2.has(expectedDirection.text)) throw new KnowledgeRegistryError("expected_direction is invalid");
  if (!Array.isArray(raw.mechanisms) || raw.mechanisms.length === 0) throw new KnowledgeRegistryError("mechanisms is invalid");
  const mechanisms = raw.mechanisms.map((item) => section("mechanisms", item));
  const prose: Record<string, string[]> = {};
  for (const name of ["alternative_explanations", "forbidden_inferences", "limitations", "counterevidence"]) {
    prose[name] = stringList(raw[name], name, false);
  }
  const outcomeOnly = raw.category === "outcome_only";
  const singular = (name: string): KnowledgeSectionV2 | "not_applicable" => {
    if (raw[name] === "not_applicable") {
      if (!outcomeOnly && !allowNonOutcomeNotApplicable) throw new KnowledgeRegistryError(`${name} cannot be not_applicable`);
      return "not_applicable";
    }
    return section(name);
  };
  const repeated = (name: string): KnowledgeSectionV2[] | "not_applicable" => {
    if (raw[name] === "not_applicable") {
      if (!outcomeOnly && !allowNonOutcomeNotApplicable) throw new KnowledgeRegistryError(`${name} cannot be not_applicable`);
      return "not_applicable";
    }
    if (!Array.isArray(raw[name]) || raw[name].length === 0) throw new KnowledgeRegistryError(`${name} is invalid`);
    return raw[name].map((item) => section(name, item));
  };
  if (outcomeOnly && (lists.family_scope.length !== 1 || lists.family_scope[0] !== "movement_aiming")) {
    throw new KnowledgeRegistryError("outcome_only scope is invalid");
  }
  const scenarioPrescription = SCENARIO_PRESCRIPTION_FIELD in raw
    ? validateScenarioPrescriptionV2(
      raw.scenario_prescription, entryFamilyScope, entrySources, sources, activeScenarioRefs,
    )
    : undefined;
  const entry: KnowledgeEntryV2 = {
    entry_id: entryId,
    entry_version: Number(raw.entry_version),
    status: raw.status as "active" | "retired",
    category: raw.category,
    topics: lists.topics,
    signals: lists.signals,
    metric_refs: lists.metric_refs,
    family_scope: lists.family_scope,
    observation_refs: lists.observation_refs,
    quality_prerequisites: lists.quality_prerequisites,
    definition,
    scope,
    expected_direction: expectedDirection,
    mechanisms,
    alternative_explanations: prose.alternative_explanations,
    forbidden_inferences: prose.forbidden_inferences,
    limitations: prose.limitations,
    counterevidence: prose.counterevidence,
    cue: singular("cue"),
    dose_guardrail: repeated("dose_guardrail"),
    matched_retest: singular("matched_retest"),
    near_transfer_retest: singular("near_transfer_retest"),
    stop_adjust_rule: repeated("stop_adjust_rule"),
    sources: lists.sources,
    supported_uses: lists.supported_uses,
  };
  if (scenarioPrescription !== undefined) entry.scenario_prescription = scenarioPrescription;
  return entry;
}

function validateEntryV3(
  raw: unknown,
  index: number,
  sources: Map<string, KnowledgeSourceV2>,
  activeScenarioRefs: Set<string>,
): KnowledgeEntryV2 {
  const optionalFields = new Set([
    "cue", "dose_guardrail", "matched_retest", "near_transfer_retest",
    "stop_adjust_rule", SCENARIO_PRESCRIPTION_FIELD,
  ]);
  const baseFields = new Set([...ENTRY_FIELDS_V2].filter((field) => !optionalFields.has(field)));
  if (!isRecord(raw) || ![...baseFields].every((field) => field in raw)
    || Object.keys(raw).some((field) => !baseFields.has(field) && !optionalFields.has(field))) {
    throw new KnowledgeRegistryError(`entry[${index}] fields are invalid`);
  }
  const supportedUses = stringList(raw.supported_uses, "supported_uses", false);
  const validPrefix = CAPABILITY_PREFIXES_V3.some((prefix) => (
    prefix.length === supportedUses.length && prefix.every((value, offset) => supportedUses[offset] === value)
  ));
  if (!validPrefix || supportedUses.some((item) => !SUPPORTED_USES_V3.has(item))) {
    throw new KnowledgeRegistryError("supported_uses capability prefix is invalid");
  }
  const hasDiagnosis = supportedUses.includes("diagnosis_support");
  const hasExperiment = supportedUses.includes("candidate_experiment");
  const hasScenario = supportedUses.includes("scenario_prescription");
  const requiredFields = new Set<string>();
  if (hasExperiment) {
    for (const field of ["cue", "dose_guardrail", "matched_retest", "stop_adjust_rule"]) requiredFields.add(field);
  }
  if (hasScenario) {
    requiredFields.add("near_transfer_retest");
    requiredFields.add(SCENARIO_PRESCRIPTION_FIELD);
  }
  if ([...requiredFields].some((field) => !(field in raw))) {
    throw new KnowledgeRegistryError("capability required fields are missing");
  }
  if ([...optionalFields].some((field) => !requiredFields.has(field) && field in raw)) {
    throw new KnowledgeRegistryError("capability forbidden fields are present");
  }
  if (hasDiagnosis && (
    !Array.isArray(raw.observation_refs) || raw.observation_refs.length === 0
    || !Array.isArray(raw.quality_prerequisites) || raw.quality_prerequisites.length === 0
  )) {
    throw new KnowledgeRegistryError("diagnosis_support context is required");
  }
  const normalized: Record<string, unknown> = { ...raw };
  for (const field of optionalFields) {
    if (!(field in normalized)) normalized[field] = "not_applicable";
  }
  const result = validateEntryV2(
    normalized,
    index,
    sources,
    true,
    activeScenarioRefs,
    SUPPORTED_USES_V3,
    true,
    true,
  );
  for (const field of optionalFields) {
    if (!requiredFields.has(field)) delete (result as unknown as Record<string, unknown>)[field];
  }
  return result;
}

function validateKnowledgeRegistryV2(raw: unknown): KnowledgeRegistry {
  if (!isRecord(raw) || !keysEqual(raw, new Set(["schema_version", "registry_version", "signal_aliases", "sources", "entries"]))) {
    throw new KnowledgeRegistryError("registry fields are invalid");
  }
  if (raw.schema_version !== REGISTRY_SCHEMA_VERSION_V2) throw new KnowledgeRegistryError("schema_version is invalid");
  const registryVersion = text(raw.registry_version, "registry_version", 80);
  if (!isRecord(raw.signal_aliases) || Object.keys(raw.signal_aliases).length > 128) throw new KnowledgeRegistryError("signal_aliases is invalid");
  const aliases: Record<string, string> = {};
  for (const [alias, canonicalRaw] of Object.entries(raw.signal_aliases)) {
    const aliasText = text(alias, "signal alias", 120);
    const canonical = text(canonicalRaw, "canonical signal", 120);
    if (!TOKEN.test(aliasText) || !TOKEN.test(canonical) || aliasText === canonical || Object.hasOwn(raw.signal_aliases, canonical)) {
      throw new KnowledgeRegistryError("signal alias is invalid");
    }
    aliases[aliasText] = canonical;
  }
  if (!Array.isArray(raw.sources) || raw.sources.length === 0) throw new KnowledgeRegistryError("sources is invalid");
  const sourceList = raw.sources.map(validateSourceV2);
  const sources = new Map(sourceList.map((source) => [source.source_ref, source]));
  if (sources.size !== sourceList.length) throw new KnowledgeRegistryError("duplicate source_ref");
  if (!Array.isArray(raw.entries) || raw.entries.length < 1 || raw.entries.length > MAX_ENTRIES) throw new KnowledgeRegistryError("entries is invalid");
  const requiresScenarioPrescription = registryVersion === "2026-07-28.v3";
  const activeScenarioRefs = activeScenarioProfileRefs();
  const entries = raw.entries.map((entry, index) => (
    validateEntryV2(entry, index, sources, requiresScenarioPrescription, activeScenarioRefs)
  ));
  const refs = new Set<string>();
  const active = new Set<string>();
  const sections = new Set<string>();
  for (const entry of entries) {
    const ref = `${entry.entry_id}@${entry.entry_version}`;
    if (refs.has(ref)) throw new KnowledgeRegistryError("duplicate entry version");
    refs.add(ref);
    if (entry.status === "active") {
      if (active.has(entry.entry_id)) throw new KnowledgeRegistryError("multiple active versions");
      active.add(entry.entry_id);
    }
    for (const section of [
      entry.definition, entry.scope, entry.expected_direction, ...entry.mechanisms,
      ...(entry.cue === undefined || entry.cue === "not_applicable" ? [] : [entry.cue]),
      ...(entry.dose_guardrail === undefined || entry.dose_guardrail === "not_applicable" ? [] : entry.dose_guardrail),
      ...(entry.matched_retest === undefined || entry.matched_retest === "not_applicable" ? [] : [entry.matched_retest]),
      ...(entry.near_transfer_retest === undefined || entry.near_transfer_retest === "not_applicable" ? [] : [entry.near_transfer_retest]),
      ...(entry.stop_adjust_rule === undefined || entry.stop_adjust_rule === "not_applicable" ? [] : entry.stop_adjust_rule),
    ]) {
      if (sections.has(section.section_ref)) throw new KnowledgeRegistryError("duplicate section_ref");
      sections.add(section.section_ref);
    }
  }
  return structuredClone({
    schema_version: REGISTRY_SCHEMA_VERSION_V2,
    registry_version: registryVersion,
    signal_aliases: aliases,
    sources: sourceList,
    entries,
  });
}

function validateKnowledgeRegistryV3(raw: unknown): KnowledgeRegistry {
  if (!isRecord(raw) || !keysEqual(raw, new Set(["schema_version", "registry_version", "signal_aliases", "sources", "entries"]))) {
    throw new KnowledgeRegistryError("registry fields are invalid");
  }
  if (raw.schema_version !== REGISTRY_SCHEMA_VERSION_V3) throw new KnowledgeRegistryError("schema_version is invalid");
  const registryVersion = text(raw.registry_version, "registry_version", 80);
  if (!isRecord(raw.signal_aliases) || Object.keys(raw.signal_aliases).length > 128) throw new KnowledgeRegistryError("signal_aliases is invalid");
  const aliases: Record<string, string> = {};
  for (const [alias, canonicalRaw] of Object.entries(raw.signal_aliases)) {
    const aliasText = text(alias, "signal alias", 120);
    const canonical = text(canonicalRaw, "canonical signal", 120);
    if (!TOKEN.test(aliasText) || !TOKEN.test(canonical) || aliasText === canonical || Object.hasOwn(raw.signal_aliases, canonical)) {
      throw new KnowledgeRegistryError("signal alias is invalid");
    }
    aliases[aliasText] = canonical;
  }
  if (!Array.isArray(raw.sources) || raw.sources.length === 0) throw new KnowledgeRegistryError("sources is invalid");
  const sourceList = raw.sources.map(validateSourceV2);
  const sources = new Map(sourceList.map((source) => [source.source_ref, source]));
  if (sources.size !== sourceList.length) throw new KnowledgeRegistryError("duplicate source_ref");
  if (!Array.isArray(raw.entries) || raw.entries.length < 1 || raw.entries.length > MAX_ENTRIES) throw new KnowledgeRegistryError("entries is invalid");
  const activeScenarioRefs = activeScenarioProfileRefs();
  const entries = raw.entries.map((entry, index) => (
    validateEntryV3(entry, index, sources, activeScenarioRefs)
  ));
  const refs = new Set<string>();
  const active = new Set<string>();
  const sections = new Set<string>();
  for (const entry of entries) {
    const ref = `${entry.entry_id}@${entry.entry_version}`;
    if (refs.has(ref)) throw new KnowledgeRegistryError("duplicate entry version");
    refs.add(ref);
    if (entry.status === "active") {
      if (active.has(entry.entry_id)) throw new KnowledgeRegistryError("multiple active versions");
      active.add(entry.entry_id);
    }
    for (const section of [
      entry.definition, entry.scope, entry.expected_direction, ...entry.mechanisms,
      ...(entry.cue === undefined || entry.cue === "not_applicable" ? [] : [entry.cue]),
      ...(entry.dose_guardrail === undefined || entry.dose_guardrail === "not_applicable" ? [] : entry.dose_guardrail),
      ...(entry.matched_retest === undefined || entry.matched_retest === "not_applicable" ? [] : [entry.matched_retest]),
      ...(entry.near_transfer_retest === undefined || entry.near_transfer_retest === "not_applicable" ? [] : [entry.near_transfer_retest]),
      ...(entry.stop_adjust_rule === undefined || entry.stop_adjust_rule === "not_applicable" ? [] : entry.stop_adjust_rule),
    ]) {
      if (sections.has(section.section_ref)) throw new KnowledgeRegistryError("duplicate section_ref");
      sections.add(section.section_ref);
    }
  }
  return structuredClone({
    schema_version: REGISTRY_SCHEMA_VERSION_V3,
    registry_version: registryVersion,
    signal_aliases: aliases,
    sources: sourceList,
    entries,
  });
}

export function validateKnowledgeRegistry(raw: unknown): KnowledgeRegistry {
  if (!isRecord(raw)) throw new KnowledgeRegistryError("registry must be an object");
  if (raw.schema_version === REGISTRY_SCHEMA_VERSION_V1) return validateKnowledgeRegistryV1(raw);
  if (raw.schema_version === REGISTRY_SCHEMA_VERSION_V2) return validateKnowledgeRegistryV2(raw);
  if (raw.schema_version === REGISTRY_SCHEMA_VERSION_V3) return validateKnowledgeRegistryV3(raw);
  throw new KnowledgeRegistryError("schema_version is invalid");
}

const cached = new Map<string, KnowledgeRegistry>();
export function loadKnowledgeRegistry(registryVersion = "2026-08-06.v6"): KnowledgeRegistry {
  const existing = cached.get(registryVersion);
  if (existing) return structuredClone(existing);
  const registryFile = registryFiles().get(registryVersion);
  if (!registryFile) throw new KnowledgeRegistryError("unknown registry version");
  const raw = readFileSync(registryFile);
  if (raw.byteLength > MAX_REGISTRY_BYTES) throw new KnowledgeRegistryError("registry exceeds size limit");
  try {
    const loaded = validateKnowledgeRegistry(JSON.parse(raw.toString("utf8")));
    if (loaded.registry_version !== registryVersion) throw new KnowledgeRegistryError("registry version does not match asset");
    cached.set(registryVersion, loaded);
  } catch (error) {
    if (error instanceof KnowledgeRegistryError) throw error;
    throw new KnowledgeRegistryError("registry is invalid JSON");
  }
  return structuredClone(cached.get(registryVersion)!);
}

export function entryRef(entry: Pick<KnowledgeEntry, "entry_id" | "entry_version">): string {
  return `knowledge:${entry.entry_id}@${entry.entry_version}`;
}

export function claimRef(section: Pick<KnowledgeSectionV2, "section_ref">): string {
  if (!TOKEN.test(section.section_ref)) throw new KnowledgeRegistryError("section_ref is invalid");
  return `claim:${section.section_ref}`;
}

export function resolveKnowledgeEntry(registryVersion: string, reference: string): KnowledgeEntry {
  const entry = loadKnowledgeRegistry(registryVersion).entries.find((item) => entryRef(item) === reference);
  if (!entry) throw new KnowledgeRegistryError("unknown knowledge entry");
  return structuredClone(entry);
}

function clean(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

export function queryKnowledgeRegistry(registry: KnowledgeRegistry, query: KnowledgeQuery): KnowledgeEntry[] {
  const requestedRegistryVersion = clean(query.registry_version);
  if (requestedRegistryVersion && requestedRegistryVersion !== registry.registry_version) {
    throw new KnowledgeRegistryError("registry version does not match loaded registry");
  }
  const reference = clean(query.entry_ref);
  if (reference) {
    const entry = registry.entries.find((item) => entryRef(item) === reference);
    if (!entry) throw new KnowledgeRegistryError("unknown knowledge entry");
    return [structuredClone(entry)];
  }
  const topic = clean(query.topic);
  const signal = clean(query.issue_signal);
  const metrics = new Set((query.metric_refs ?? []).filter((item) => typeof item === "string" && item.trim()).map((item) => item.trim()));
  const supportedUse = clean(query.supported_use);
  if (!topic && !signal && metrics.size === 0 && !supportedUse) throw new KnowledgeRegistryError("at least one query condition is required");
  if (!topic && !signal && metrics.size === 0) return [];
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
