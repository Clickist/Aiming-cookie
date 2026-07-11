import { loadPiAi } from "./pi-source.ts";
import type { AnalysisSummary, PythonAnalysisClient } from "./python-analysis-client.ts";

const { Type } = await loadPiAi() as { Type: { Object(properties: Record<string, unknown>, options?: Record<string, unknown>): unknown; String(): unknown } };

export type { AnalysisSummary } from "./python-analysis-client.ts";

type AnalysisSummaryToolOptions = {
  client: PythonAnalysisClient;
  fail?: boolean;
};

export function createAnalysisSummaryTool({ client, fail = false }: AnalysisSummaryToolOptions) {
  return {
    name: "get_analysis_summary",
    label: "Get analysis summary",
    description: "Read the fixed fixture analysis summary.",
    parameters: Type.Object({ analysis_id: Type.String() }, { additionalProperties: false }),
    async execute(
      toolCallId: string,
      params: { analysis_id: string },
      signal?: AbortSignal,
      onUpdate?: (result: { details: { stage: string } }) => void,
    ) {
      const summary: AnalysisSummary = await client.getAnalysisSummary({
        requestId: toolCallId,
        analysisId: params.analysis_id,
        signal,
        onProgress: ({ stage }) => onUpdate?.({ details: { stage } }),
      });
      if (fail) throw new Error("Fixture tool execution failed");
      return {
        content: [{ type: "text", text: JSON.stringify(summary) }],
        details: summary,
      };
    },
  };
}
