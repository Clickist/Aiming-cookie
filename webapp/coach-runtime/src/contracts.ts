export const COACH_RUNTIME_TURN_SCHEMA_V0 = "coach_runtime_turn.v0" as const;
export const COACH_RUNTIME_TURN_SCHEMA_V1 = "coach_runtime_turn.v1" as const;
export const COACH_RUNTIME_TURN_SCHEMA = COACH_RUNTIME_TURN_SCHEMA_V1;
export const PROVIDER_CATALOG_SCHEMA = "coach_provider_catalog.v1" as const;
export const PROVIDER_PROFILE_STATUS_SCHEMA = "coach_provider_profile_status.v1" as const;
export const PROVIDER_AUTH_CAPABILITIES_SCHEMA = "coach_provider_auth_capabilities.v1" as const;
export const PROVIDER_AUTH_OPERATION_SCHEMA = "coach_provider_auth_operation.v1" as const;
export const PROVIDER_AUTH_RESULT_SCHEMA = "coach_provider_auth_result.v1" as const;
export const COACH_DIAGNOSTIC_CONTEXT_V1_SCHEMA = "coach_diagnostic_context.v1" as const;
export const COACH_DIAGNOSTIC_CONTEXT_V2_SCHEMA = "coach_diagnostic_context.v2" as const;
export const COACH_DIAGNOSTIC_CONTEXT_V3_SCHEMA = "coach_diagnostic_context.v3" as const;

export const CODING_AGENT_DEFAULT_PROMPT_MARKER =
  "expert coding assistant operating inside pi, a coding agent harness";

export const FORBIDDEN_TOOL_NAMES = new Set([
  "bash", "shell", "exec", "read", "write", "edit", "read_file",
  "write_file", "apply_patch", "filesystem", "coding-agent", "coding_agent",
]);

export type CoachToolBridge = {
  schema_version: "coach_tool_bridge.v1";
  turn_id: string;
  endpoint: string;
  bearer_token: string;
  desktop_token?: string;
  expires_at: string;
  user_message_ref: string;
};

export type CoachRuntimeToolEvent =
  | {
      type: "knowledge";
      registry_version: string;
      topic: string | null;
      issue_signal: string | null;
      entry_refs: string[];
      entry_versions: number[];
      source_refs: string[];
      source_levels: string[];
      section_refs: string[];
      claim_refs: string[];
      claim_levels: string[];
      max_claim_levels: string[];
    }
  | {
      type: "product_command";
      command_id: string;
      command_name: string;
      status: string;
      result_ref: string | null;
      audit_ref: string;
      ui_event: Record<string, unknown> | null;
      warning_or_error: Record<string, unknown> | null;
    };

export type CoachRuntimeMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};

/** Legacy v0 OpenAI-compatible model shape, retained only for migration compatibility. */
export type CoachRuntimeModelConfig = {
  base_url: string;
  api_key_env: string;
  model_id: string;
};

export type ApiKeyCredential = {
  type: "api_key";
  key?: string;
  env?: Record<string, string>;
};

export type OAuthCredential = {
  type: "oauth";
  access: string;
  refresh: string;
  expires: number;
  [key: string]: unknown;
};

/** Pinned Pi's current type-tagged provider credential union. */
export type ProviderCredential = ApiKeyCredential | OAuthCredential;

export type BuiltinProviderProfile = {
  kind: "builtin";
  provider_id: string;
  model_id: string;
  /** Runtime-only credential injected into a request-scoped Pi CredentialStore. */
  credential?: ProviderCredential;
  /** Migration compatibility; normalized to an api_key credential during parsing. */
  api_key?: string;
};

export type CustomOpenAiCompatibleProfile = {
  kind: "custom_openai_compatible";
  provider_id: string;
  provider_name: string;
  base_url: string;
  model_id: string;
  /** Custom providers require an api_key credential. */
  credential?: ApiKeyCredential;
  /** Migration compatibility; normalized to an api_key credential during parsing. */
  api_key?: string;
};

export type CoachRuntimeProviderProfile = BuiltinProviderProfile | CustomOpenAiCompatibleProfile;

export type CoachRuntimeTurnRequest = {
  schema_version: typeof COACH_RUNTIME_TURN_SCHEMA_V1;
  run_id: string;
  user_id: string;
  messages: CoachRuntimeMessage[];
  analysis_summary: string | null;
  system_prompt?: string;
  model: CoachRuntimeProviderProfile;
  tool_bridge?: CoachToolBridge;
};

export type CoachRuntimeTurnSchema =
  | typeof COACH_RUNTIME_TURN_SCHEMA_V0
  | typeof COACH_RUNTIME_TURN_SCHEMA_V1;

export type CoachRuntimeError = {
  category: string;
  code: string;
  message: string;
  retryable: boolean;
};

export type CoachRuntimeTurnResponse = {
  schema_version: CoachRuntimeTurnSchema;
  ok: boolean;
  reply: string | null;
  partial_reply: string | null;
  error: CoachRuntimeError | null;
  notes: string[];
  tool_events: CoachRuntimeToolEvent[];
};

export type ProviderAuthMode = "api_key" | "oauth" | "ambient";

export type ProviderApiKeyAuthCapability = {
  name: string;
  interactive: boolean;
};

export type ProviderOAuthAuthCapability = {
  name: string;
  refresh: true;
};

export type ProviderAuthCapability = {
  provider_id: string;
  provider_name: string;
  auth_modes: ProviderAuthMode[];
  api_key_auth: ProviderApiKeyAuthCapability | null;
  oauth_auth: ProviderOAuthAuthCapability | null;
};

export type ProviderAuthCapabilitiesResponse = {
  schema_version: typeof PROVIDER_AUTH_CAPABILITIES_SCHEMA;
  providers: ProviderAuthCapability[];
};

export type ProviderCatalogModel = {
  model_id: string;
  model_name: string;
  api: string;
  provider_id: string;
  base_url: string;
  reasoning: boolean;
  input: string[];
  context_window: number;
  max_tokens: number;
};

export type ProviderCatalogEntry = ProviderAuthCapability & {
  base_url: string | null;
  models: ProviderCatalogModel[];
};

export type ProviderCatalogResponse = {
  schema_version: typeof PROVIDER_CATALOG_SCHEMA;
  providers: ProviderCatalogEntry[];
};

export type ProviderProfileStatus =
  | "unconfigured"
  | "ready"
  | "auth_expired"
  | "needs_reauth"
  | "model_unavailable"
  | "connection_failed";

export type ProviderProfileStatusResponse = {
  schema_version: typeof PROVIDER_PROFILE_STATUS_SCHEMA;
  ok: boolean;
  status: ProviderProfileStatus;
  profile: Record<string, unknown> | null;
  model: ProviderCatalogModel | null;
  credential_source: string | null;
  error: CoachRuntimeError | null;
};

export type ProviderAuthPrompt = {
  prompt_id: string;
  type: "text" | "secret" | "select" | "manual_code";
  message: string;
  placeholder?: string;
  options?: Array<{ id: string; label: string; description?: string }>;
};

export type ProviderAuthEvent =
  | { type: "auth_url"; url: string; instructions?: string }
  | {
      type: "device_code";
      user_code: string;
      verification_uri: string;
      interval_seconds?: number;
      expires_in_seconds?: number;
    }
  | { type: "progress"; message: string };

export type ProviderAuthOperationStatus =
  | "running"
  | "awaiting_input"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "timed_out";

export type ProviderAuthOperation = {
  schema_version: typeof PROVIDER_AUTH_OPERATION_SCHEMA;
  id: string;
  action: "login" | "refresh";
  provider_id: string;
  mode: "api_key" | "oauth" | null;
  status: ProviderAuthOperationStatus;
  prompt: ProviderAuthPrompt | null;
  prompts: ProviderAuthPrompt[];
  events: ProviderAuthEvent[];
  result_available: boolean;
  created_at: number;
  updated_at: number;
  expires_at: number;
  terminal_at: number | null;
  error: CoachRuntimeError | null;
};

export type ProviderAuthResult = {
  schema_version: typeof PROVIDER_AUTH_RESULT_SCHEMA;
  operation_id: string;
  credential: ProviderCredential;
};

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function makeError(partial: CoachRuntimeError): CoachRuntimeError {
  return partial;
}

export function successResponse(
  reply: string,
  notes: string[] = [],
  schemaVersion: CoachRuntimeTurnSchema = COACH_RUNTIME_TURN_SCHEMA,
  toolEvents: CoachRuntimeToolEvent[] = [],
): CoachRuntimeTurnResponse {
  return {
    schema_version: schemaVersion,
    ok: true,
    reply,
    partial_reply: null,
    error: null,
    notes,
    tool_events: toolEvents,
  };
}

export function failureResponse(
  error: CoachRuntimeError,
  notes: string[] = [],
  schemaVersion: CoachRuntimeTurnSchema = COACH_RUNTIME_TURN_SCHEMA,
  toolEvents: CoachRuntimeToolEvent[] = [],
  partialReply: string | null = null,
): CoachRuntimeTurnResponse {
  return {
    schema_version: schemaVersion,
    ok: false,
    reply: null,
    partial_reply: partialReply,
    error,
    notes,
    tool_events: toolEvents,
  };
}
