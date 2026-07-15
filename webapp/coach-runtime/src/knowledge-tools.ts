import { loadPiAi } from "./pi-source.ts";
import {
  entryRef,
  loadKnowledgeRegistry,
  queryKnowledgeRegistry,
  type KnowledgeQuery,
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

export function getCoachKnowledge(query: KnowledgeQuery) {
  const canonicalSignal = query.issue_signal
    ? REGISTRY.signal_aliases[query.issue_signal.trim()] ?? query.issue_signal.trim()
    : null;
  const entries = queryKnowledgeRegistry(REGISTRY, query).map((entry) => ({
    ...entry,
    entry_ref: entryRef(entry),
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
            max_claim_levels: result.entries.map((entry) => entry.max_claim_level),
          },
        },
      };
    },
  };
}
