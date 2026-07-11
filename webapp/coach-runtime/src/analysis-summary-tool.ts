import { loadPiAi } from "./pi-source.ts";

const { Type } = (await loadPiAi()) as {
  Type: {
    Object(properties: Record<string, unknown>, options?: Record<string, unknown>): unknown;
  };
};

export function createAnalysisSummaryTool(analysisSummary: string | null) {
  const summaryText =
    analysisSummary && analysisSummary.trim().length > 0
      ? analysisSummary.trim()
      : "当前没有可用的分析摘要。";

  return {
    name: "get_analysis_summary",
    label: "Get analysis summary",
    description: "返回本轮请求中已附带的只读分析摘要（不访问磁盘或数据库）。",
    parameters: Type.Object({}, { additionalProperties: false }),
    async execute() {
      return {
        content: [{ type: "text", text: summaryText }],
        details: { has_analysis: analysisSummary !== null && analysisSummary.trim().length > 0 },
      };
    },
  };
}