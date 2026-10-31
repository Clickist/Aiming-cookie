import { logFrontendError } from "../frontend-log";
import type { CoachGenerationStatus, CoachPresentationCause, OpenUIErrorLike, OpenUIRenderState } from "./presentation";

export type OpenUIDiagnosticContext = {
  conversation_id: number | null;
  message_id: string | number | null;
  run_id: string | null;
  segment_id: string;
  revision: number;
  generation_status: CoachGenerationStatus;
};

const ERROR_CODES = new Set([
  "parse_error", "parse-failed", "parse-exception", "no-root", "missing-required",
  "null-required", "inline-reserved", "excess-args", "type-mismatch", "unknown-function",
  "unknown-component", "render-error", "runtime-error", "unresolved-ref", "forbidden-call",
]);

export function openUIDiagnosticLine(input: OpenUIDiagnosticContext & {
  state: OpenUIRenderState;
  cause?: CoachPresentationCause;
  library_id?: string;
  library_version?: string;
  source_length?: number;
  root_present?: boolean;
  statement_count?: number;
  errors?: OpenUIErrorLike[];
}): string {
  const safeId = (value: string | number | null) => typeof value === "string" ? value.slice(0, 256) : value;
  const codes = [...new Set((input.errors ?? []).map((error) =>
    ERROR_CODES.has(error.code ?? "") ? error.code : "unknown-error",
  ))].slice(0, 16);
  return JSON.stringify({
    event: "coach_openui",
    conversation_id: input.conversation_id,
    message_id: safeId(input.message_id),
    run_id: safeId(input.run_id),
    segment_id: safeId(input.segment_id),
    revision: input.revision,
    generation_status: input.generation_status,
    render_state: input.state,
    cause: input.cause ?? null,
    stage: input.cause === "render_error" ? "render"
      : input.cause === "budget_exceeded" ? "budget"
        : input.cause === "unsupported_library" ? "library"
          : input.cause === "stopped" || input.cause === "process_interrupted"
            || (input.generation_status === "failed" && !input.cause) ? "generation"
            : input.state === "ready" ? "complete" : "parse",
    library_id: input.library_id?.slice(0, 64),
    library_version: input.library_version?.slice(0, 32),
    source_length: input.source_length,
    root_present: input.root_present,
    statement_count: input.statement_count,
    codes,
  });
}

export function logOpenUIDiagnostic(line: string): void {
  logFrontendError("openui.presentation", line);
}
