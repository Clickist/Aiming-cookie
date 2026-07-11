export const COACH_RUNTIME_TURN_SCHEMA = "coach_runtime_turn.v0" as const;

export const CODING_AGENT_DEFAULT_PROMPT_MARKER =
  "expert coding assistant operating inside pi, a coding agent harness";

export const FORBIDDEN_TOOL_NAMES = new Set(["bash", "read", "write", "edit"]);

export type CoachRuntimeMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};

export type CoachRuntimeModelConfig = {
  base_url: string;
  api_key_env: string;
  model_id: string;
};

export type CoachRuntimeTurnRequest = {
  schema_version: typeof COACH_RUNTIME_TURN_SCHEMA;
  run_id: string;
  user_id: string;
  messages: CoachRuntimeMessage[];
  analysis_summary: string | null;
  system_prompt?: string;
  model: CoachRuntimeModelConfig;
};

export type CoachRuntimeError = {
  category: string;
  code: string;
  message: string;
  retryable: boolean;
};

export type CoachRuntimeTurnResponse = {
  schema_version: typeof COACH_RUNTIME_TURN_SCHEMA;
  ok: boolean;
  reply: string | null;
  error: CoachRuntimeError | null;
  notes: string[];
};

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function makeError(partial: CoachRuntimeError): CoachRuntimeError {
  return partial;
}

export function successResponse(reply: string, notes: string[] = []): CoachRuntimeTurnResponse {
  return {
    schema_version: COACH_RUNTIME_TURN_SCHEMA,
    ok: true,
    reply,
    error: null,
    notes,
  };
}

export function failureResponse(error: CoachRuntimeError, notes: string[] = []): CoachRuntimeTurnResponse {
  return {
    schema_version: COACH_RUNTIME_TURN_SCHEMA,
    ok: false,
    reply: null,
    error,
    notes,
  };
}