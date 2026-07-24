import { loadPiAi } from "./pi-source.ts";
import {
  claimRef,
  entryRef,
  loadKnowledgeRegistry,
  queryKnowledgeRegistry,
  type KnowledgeEntry,
  type KnowledgeEntryV1,
  type KnowledgeEntryV2,
  type KnowledgeQuery,
  type KnowledgeSectionV2,
  type KnowledgeSourceV1,
  type KnowledgeSourceV2,
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

type ToolKnowledgeEntry = (Omit<KnowledgeEntryV1, "sources"> | Omit<KnowledgeEntryV2, "sources">) & {
  entry_ref: string;
  sources: Array<KnowledgeSourceV1 | KnowledgeSourceV2>;
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

function sourceRecords(entry: KnowledgeEntry): Array<KnowledgeSourceV1 | KnowledgeSourceV2> {
  if (!isV2Entry(entry)) return entry.sources;
  const known = new Map((REGISTRY.sources ?? []).map((source) => [source.source_ref, source]));
  return entry.sources.map((sourceRef) => {
    const source = known.get(sourceRef);
    if (!source) throw new Error("knowledge entry source is unavailable");
    return source;
  });
}

function claimSections(entry: KnowledgeEntry): KnowledgeSectionV2[] {
  if (!isV2Entry(entry)) return [];
  const optional = (value: KnowledgeSectionV2 | "not_applicable"): KnowledgeSectionV2[] => (
    value === "not_applicable" ? [] : [value]
  );
  const repeated = (value: KnowledgeSectionV2[] | "not_applicable"): KnowledgeSectionV2[] => (
    value === "not_applicable" ? [] : value
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

function entryShape(entry: ToolKnowledgeEntry): KnowledgeEntry {
  return entry as unknown as KnowledgeEntry;
}

export function getCoachKnowledge(query: KnowledgeQuery) {
  const canonicalSignal = query.issue_signal
    ? REGISTRY.signal_aliases[query.issue_signal.trim()] ?? query.issue_signal.trim()
    : null;
  const entries: ToolKnowledgeEntry[] = queryKnowledgeRegistry(REGISTRY, query).map((entry) => ({
    ...entry,
    entry_ref: entryRef(entry),
    sources: sourceRecords(entry),
  }));
  return {
    schema_version: "coach_knowledge_result.v1",
    registry_version: REGISTRY.registry_version,
    topic: query.topic?.trim() || null,
    issue_signal: canonicalSignal || null,
    entries,
  };
}

export function createCoachKnowledgeTool() {
  return {
    name: "get_coach_knowledge",
    label: "Get coaching knowledge",
    description: "按当前 topic、diagnostic signal、metric 或用途从版本化 Registry 渐进获取最多三条知识。知识不能替代测量或确定性诊断。",
    parameters: Type.Object({
      topic: Type.Optional(Type.String({ maxLength: 160 })),
      issue_signal: Type.Optional(Type.String({ maxLength: 160 })),
      metric_refs: Type.Optional(Type.Array(Type.String({ maxLength: 160 }), { maxItems: 16 })),
      supported_use: Type.Optional(Type.String({ maxLength: 80 })),
    }, { additionalProperties: false }),
    async execute(_id: string, params: KnowledgeQuery) {
      const result = getCoachKnowledge(params);
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
            source_refs: result.entries.flatMap((entry) => entry.sources.map((source) => source.source_ref)),
            source_levels: result.entries.flatMap((entry) => entry.sources.map((source) => source.source_level)),
            section_refs: result.entries.flatMap((entry) => claimSections(entryShape(entry)).map((section) => section.section_ref)),
            claim_refs: result.entries.flatMap((entry) => claimSections(entryShape(entry)).map(claimRef)),
            claim_levels: result.entries.flatMap((entry) => claimSections(entryShape(entry)).map((section) => section.claim_level)),
            max_claim_levels: result.entries.map((entry) => maxClaimLevel(entryShape(entry))),
          },
        },
      };
    },
  };
}
