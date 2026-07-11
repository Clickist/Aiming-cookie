/**
 * Single adapter: AnalysisResult v1 (wire) → CoachReport (UI view model).
 * Mirrors webapp/backend/contracts.py analysis_result_to_coach_report.
 */

import type { AnalysisResultV1, CoachReport } from "./types";

export function analysisResultToCoachReport(
  result: AnalysisResultV1,
): CoachReport {
  const narration = result.narration;
  const narrationOut: string | null =
    narration.status === "available" ? narration.text : null;

  return {
    diagnosis: result.deterministic.diagnosis,
    figures: result.deterministic.figures,
    narration: narrationOut,
    notes: [...(result.notes ?? [])],
  };
}