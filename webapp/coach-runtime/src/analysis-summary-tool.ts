import { loadPiAi } from "./pi-source.ts";

const { Type } = (await loadPiAi()) as {
  Type: {
    Object(properties: Record<string, unknown>, options?: Record<string, unknown>): unknown;
  };
};

export function createAnalysisSummaryTool(analysisSummary: string | null) {
  let summaryText = "当前没有可用的分析摘要。";
  let hasAnalysis = false;
  if (analysisSummary && analysisSummary.trim().length > 0 && Buffer.byteLength(analysisSummary, "utf8") <= 64 * 1024) {
    try {
      const parsed = JSON.parse(analysisSummary);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && parsed.schema_version === "coach_diagnostic_context.v1") {
        summaryText = JSON.stringify(parsed);
        hasAnalysis = true;
      }
    } catch {
      // Fail closed; invalid input never becomes model-visible content.
    }
  }

  return {
    name: "get_analysis_summary",
    label: "Get diagnostic context",
    description: "返回本轮请求中已附带的 coach_diagnostic_context.v1 JSON（只读，不访问磁盘或数据库）。",
    parameters: Type.Object({}, { additionalProperties: false }),
    async execute() {
      return {
        content: [{ type: "text", text: summaryText }],
        details: {
          has_analysis: hasAnalysis,
          context_schema: "coach_diagnostic_context.v1",
        },
      };
    },
  };
}
