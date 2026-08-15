import { loadPiAi } from "./pi-source.ts";
import {
  claimRef,
  entryRef,
  loadKnowledgeRegistry,
  queryKnowledgeRegistry,
  type KnowledgeEntry,
  type KnowledgeEntryV2,
  type KnowledgeQuery,
  type KnowledgeSectionV2,
} from "./knowledge-registry.ts";

type TypeBuilder = {
  Array(schema: unknown, options?: Record<string, unknown>): unknown;
  Object(properties: Record<string, unknown>, options?: Record<string, unknown>): unknown;
  Optional(schema: unknown): unknown;
  String(options?: Record<string, unknown>): unknown;
  Union(schemas: unknown[]): unknown;
};
const { Type } = (await loadPiAi()) as { Type: TypeBuilder };

const REGISTRY = loadKnowledgeRegistry();
export const COACH_KNOWLEDGE_TOPICS = [...new Set(
  REGISTRY.entries.filter((entry) => entry.status === "active").flatMap((entry) => entry.topics),
)].sort();
export const KNOWLEDGE_VERSION = REGISTRY.registry_version;

type ToolKnowledgeEntry = {
  entry_ref: string;
  entry_version: number;
  topics: string[];
  signals: string[];
  metric_refs: string[];
  supported_uses: string[];
  limitations: string[];
  counterevidence: string[];
  source_refs: string[];
  sections: KnowledgeSectionV2[];
  max_claim_level: string;
  text?: string;
};
const CLAIM_RANK: Record<string, number> = {
  experimental: 0,
  community_practice: 1,
  community_consensus: 2,
  research_supported: 3,
  deterministic_rule: 4,
};

function isV2Entry(entry: KnowledgeEntry): entry is KnowledgeEntryV2 {
  return "family_scope" in entry;
}

function claimSections(entry: KnowledgeEntry): KnowledgeSectionV2[] {
  if (!isV2Entry(entry)) return [];
  const optional = (value: KnowledgeSectionV2 | "not_applicable" | undefined): KnowledgeSectionV2[] => (
    value === "not_applicable" || value === undefined ? [] : [value]
  );
  const repeated = (value: KnowledgeSectionV2[] | "not_applicable" | undefined): KnowledgeSectionV2[] => (
    value === "not_applicable" || value === undefined ? [] : value
  );
  return [
    entry.definition,
    entry.scope,
    entry.expected_direction,
    ...entry.mechanisms,
    ...optional(entry.cue),
    ...repeated(entry.dose_guardrail),
    ...optional(entry.matched_retest),
    ...optional(entry.near_transfer_retest),
    ...repeated(entry.stop_adjust_rule),
  ];
}

function maxClaimLevel(entry: KnowledgeEntry): string {
  if (!isV2Entry(entry)) return entry.max_claim_level;
  return claimSections(entry)
    .map((section) => section.claim_level)
    .sort((left, right) => (CLAIM_RANK[right] ?? -1) - (CLAIM_RANK[left] ?? -1))[0] ?? "experimental";
}

function projectedSections(entry: KnowledgeEntry, supportedUse: string | undefined): KnowledgeSectionV2[] {
  if (!isV2Entry(entry)) return [];
  const optional = (value: KnowledgeSectionV2 | "not_applicable" | undefined): KnowledgeSectionV2[] => (
    value === "not_applicable" || value === undefined ? [] : [value]
  );
  const repeated = (value: KnowledgeSectionV2[] | "not_applicable" | undefined): KnowledgeSectionV2[] => (
    value === "not_applicable" || value === undefined ? [] : value
  );
  const sections = [entry.definition, entry.scope, entry.expected_direction, ...entry.mechanisms];
  if (supportedUse === "candidate_experiment" || supportedUse === "scenario_prescription") {
    sections.push(...optional(entry.cue), ...repeated(entry.dose_guardrail), ...optional(entry.matched_retest), ...repeated(entry.stop_adjust_rule));
  }
  if (supportedUse === "scenario_prescription") {
    sections.push(...optional(entry.near_transfer_retest));
  }
  return sections;
}

function projectedSourceRefs(entry: KnowledgeEntry, sections: KnowledgeSectionV2[]): string[] {
  const sourceRefs = isV2Entry(entry)
    ? sections.flatMap((section) => section.source_refs)
    : entry.sources.map((source) => source.source_ref);
  return [...new Set(sourceRefs)];
}

function sourceLevels(registry: ReturnType<typeof loadKnowledgeRegistry>, entry: KnowledgeEntry, sourceRefs: string[]): string[] {
  const known = isV2Entry(entry)
    ? new Map((registry.sources ?? []).map((source) => [source.source_ref, source.source_level]))
    : new Map(entry.sources.map((source) => [source.source_ref, source.source_level]));
  return sourceRefs.map((sourceRef) => {
    const level = known.get(sourceRef);
    if (!level) throw new Error("knowledge entry source is unavailable");
    return level;
  });
}

function projectEntry(entry: KnowledgeEntry, supportedUse: string | undefined): ToolKnowledgeEntry {
  const sections = projectedSections(entry, supportedUse);
  return {
    entry_ref: entryRef(entry),
    entry_version: entry.entry_version,
    topics: entry.topics,
    signals: entry.signals,
    metric_refs: entry.metric_refs,
    supported_uses: entry.supported_uses,
    limitations: entry.limitations,
    counterevidence: entry.counterevidence,
    source_refs: projectedSourceRefs(entry, sections),
    sections,
    max_claim_level: maxClaimLevel(entry),
    ...(!isV2Entry(entry) ? { text: entry.text } : {}),
  };
}

export function getCoachKnowledge(query: KnowledgeQuery) {
  const registry = query.registry_version?.trim()
    ? loadKnowledgeRegistry(query.registry_version.trim())
    : REGISTRY;
  const canonicalSignal = query.issue_signal
    ? registry.signal_aliases[query.issue_signal.trim()] ?? query.issue_signal.trim()
    : null;
  const supportedUse = query.supported_use?.trim();
  const entries = queryKnowledgeRegistry(registry, query).map((entry) => projectEntry(entry, supportedUse));
  return {
    schema_version: "coach_knowledge_result.v1",
    registry_version: registry.registry_version,
    topic: query.topic?.trim() || null,
    issue_signal: canonicalSignal || null,
    entries,
  };
}

export function createCoachKnowledgeTool() {
  return {
    name: "get_coach_knowledge",
    label: "Get coaching knowledge",
    description: "优先按 registry version 加 exact entry ref 获取知识；否则按 topic、diagnostic signal 或 metric 受限检索最多三条投影。metric_refs 直接传分析里的指标名（bare、family 前缀或 metric: 前缀均可），分析没有诊断 signal（如 baseline 档）时用关键实测指标检索。知识不能替代测量或确定性诊断。",
    parameters: Type.Object({
      registry_version: Type.Optional(Type.String({ maxLength: 80 })),
      entry_ref: Type.Optional(Type.String({ maxLength: 200 })),
      topic: Type.Optional(Type.String({ maxLength: 160 })),
      issue_signal: Type.Optional(Type.String({ maxLength: 160 })),
      metric_refs: Type.Optional(Type.Array(Type.String({ maxLength: 160 }), { maxItems: 16 })),
      supported_use: Type.Optional(Type.String({ maxLength: 80 })),
    }, { additionalProperties: false }),
    async execute(_id: string, params: KnowledgeQuery) {
      const result = getCoachKnowledge(params);
      const registry = loadKnowledgeRegistry(result.registry_version);
      return {
        content: [{ type: "text", text: JSON.stringify(result) }],
        details: {
          event: {
            type: "knowledge",
            registry_version: result.registry_version,
            topic: result.topic,
            issue_signal: result.issue_signal,
            entry_refs: result.entries.map((entry) => entry.entry_ref),
            entry_versions: result.entries.map((entry) => entry.entry_version),
            source_refs: result.entries.flatMap((entry) => entry.source_refs),
            source_levels: result.entries.flatMap((entry) => {
              const original = queryKnowledgeRegistry(registry, { entry_ref: entry.entry_ref })[0];
              if (!original) throw new Error("knowledge entry is unavailable");
              return sourceLevels(registry, original, entry.source_refs);
            }),
            section_refs: result.entries.flatMap((entry) => entry.sections.map((section) => section.section_ref)),
            claim_refs: result.entries.flatMap((entry) => entry.sections.map(claimRef)),
            claim_levels: result.entries.flatMap((entry) => entry.sections.map((section) => section.claim_level)),
            max_claim_levels: result.entries.map((entry) => entry.max_claim_level),
          },
        },
      };
    },
  };
}
