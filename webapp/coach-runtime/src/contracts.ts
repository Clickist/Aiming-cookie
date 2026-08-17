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
export const PROVIDER_MODEL_SWITCH_SCHEMA = "coach_provider_model_switch.v1" as const;

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

export const TEACHING_TURN_CONTRACT_SCHEMA = "coach_teaching_turn.v1" as const;

export type TeachingPhase =
  | "intake"
  | "hypothesize"
  | "teach"
  | "await_teach_back"
  | "teach_back_repair"
  | "practice_ready"
  | "await_execution_confirmation"
  | "retest_ready"
  | "await_retest_confirmation"
  | "revise"
  | "follow_up"
  | "paused"
  | "stopped_for_discomfort";

export type TeachingQuestionKind =
  | "none"
  | "discriminator"
  | "teach_back"
  | "teach_back_repair"
  | "follow_up";

export type TeachingAllowedCommand =
  | "training_plan.item.add"
  | "training_plan.execution.record"
  | "training_plan.retest.record";

export type TeachingRetestIntent = "none" | "immediate_matched" | "delayed_matched" | "near_transfer";
export type TeachingRetestComparability = "unresolved" | "comparable" | "not_comparable" | "not_requested";
export type TeachingRevisionDecision = "retain" | "lower" | "reject";
export type TeachingEvidenceStrength = "limited" | "supported" | "repeated";
export type TeachingEvidenceKind = "measured" | "self_reported" | "observed" | "inferred" | "external";
export type TeachingEvidence = { kind: TeachingEvidenceKind; text: string; refs: string[] };
export type TeachingCounterevidenceStatus = "not_observed" | "observed";
export type TeachingDiscriminator = {
  kind: "question" | "experiment";
  prompt: string;
};

export type TeachingPreparedPlanItem = {
  diagnosis_ref: string;
  knowledge_ref: string;
  scenario_profile_ref: string;
  baseline_metric_ref: string;
  expected_direction: "lower_better" | "higher_better" | "target_band" | "descriptive_only" | "comparison_only";
  practice_condition: string;
  cue: string;
  dose_guardrail: string;
  matched_retest_ref: string;
  near_transfer_retest_ref: string;
  review_date: string;
};

export type TeachingNextRecommendation = {
  scenario_name: string;
  scenario_profile_ref: string | null;
  message: string;
};

export type TeachingTurnContract = {
  schema_version: typeof TEACHING_TURN_CONTRACT_SCHEMA;
  session_ref: string;
  session_version: number;
  phase: TeachingPhase;
  problem_id: string | null;
  problem_label: string | null;
  evidence_strength: TeachingEvidenceStrength;
  supporting_evidence: Array<string | TeachingEvidence>;
  counterevidence_status: TeachingCounterevidenceStatus;
  counterevidence: string[];
  observation: string | null;
  primary_candidate: string | null;
  alternatives: string[];
  cue: string | null;
  changed_variable: string | null;
  active_item_ref: string | null;
  prepared_plan_ref: string | null;
  prepared_item: TeachingPreparedPlanItem | null;
  next_recommendation: TeachingNextRecommendation | null;
  question_kind: TeachingQuestionKind;
  question: string | null;
  allowed_command: TeachingAllowedCommand | null;
  confirmation_intent: "none" | "execution" | "retest";
  retest: {
    intent: TeachingRetestIntent;
    comparability_required: boolean;
    comparability: TeachingRetestComparability;
    revision_decision: TeachingRevisionDecision | null;
  };
  ratio_sources: Array<{ label: string; value: number }>;
  approved_dose: string | null;
  discriminator: TeachingDiscriminator | null;
  soft_start: boolean;
};

/** Legacy v0 OpenAI-compatible model shape, retained only for migration compatibility. */
export type CoachRuntimeModelConfig = {
  base_url: string;
  api_key_env: string;
  model_id: string;
  context_window: number;
  max_tokens: number;
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
  /** Limits returned by this Provider's model discovery response. */
  context_window?: number;
  max_tokens?: number;
  /** Custom providers require an api_key credential. */
  credential?: ApiKeyCredential;
  /** Migration compatibility; normalized to an api_key credential during parsing. */
  api_key?: string;
};

export type CustomAnthropicCompatibleProfile = {
  kind: "custom_anthropic_compatible";
  provider_id: string;
  provider_name: string;
  base_url: string;
  model_id: string;
  /** Limits returned by this Provider's model discovery response. */
  context_window?: number;
  max_tokens?: number;
  /** Custom providers require an api_key credential. */
  credential?: ApiKeyCredential;
  /** Migration compatibility; normalized to an api_key credential during parsing. */
  api_key?: string;
};

export type CoachRuntimeProviderProfile =
  | BuiltinProviderProfile
  | CustomOpenAiCompatibleProfile
  | CustomAnthropicCompatibleProfile;

export type CoachRuntimeTurnRequest = {
  schema_version: typeof COACH_RUNTIME_TURN_SCHEMA_V1;
  run_id: string;
  /** Stable opaque Coach thread identity forwarded to Pi for provider affinity. */
  session_id?: string;
  user_id: string;
  messages: CoachRuntimeMessage[];
  analysis_summary: string | null;
  system_prompt?: string;
  model: CoachRuntimeProviderProfile;
  tool_bridge?: CoachToolBridge;
  teaching_turn?: TeachingTurnContract;
};

export type CoachRuntimeTurnSchema = typeof COACH_RUNTIME_TURN_SCHEMA_V1;

export type CoachRuntimeError = {
  category: string;
  code: string;
  message: string;
  retryable: boolean;
};

export type CoachRuntimeTurnResponse = {
  schema_version: CoachRuntimeTurnSchema;
  run_id: string | null;
  ok: boolean;
  reply: string | null;
  partial_reply: string | null;
  error: CoachRuntimeError | null;
  notes: string[];
  tool_events: CoachRuntimeToolEvent[];
  /** Analysis ids the turn engaged with via file reads (`analysis:{id}`). */
  analysis_refs: string[];
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

export type CustomProviderModel = {
  model_id: string;
  context_window: number | null;
  max_tokens: number | null;
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
  runId: string | null = null,
  analysisRefs: string[] = [],
): CoachRuntimeTurnResponse {
  return {
    schema_version: schemaVersion,
    run_id: runId,
    ok: true,
    reply,
    partial_reply: null,
    error: null,
    notes,
    tool_events: toolEvents,
    analysis_refs: analysisRefs,
  };
}

export function failureResponse(
  error: CoachRuntimeError,
  notes: string[] = [],
  schemaVersion: CoachRuntimeTurnSchema = COACH_RUNTIME_TURN_SCHEMA,
  toolEvents: CoachRuntimeToolEvent[] = [],
  partialReply: string | null = null,
  runId: string | null = null,
  analysisRefs: string[] = [],
): CoachRuntimeTurnResponse {
  return {
    schema_version: schemaVersion,
    run_id: runId,
    ok: false,
    reply: null,
    partial_reply: partialReply,
    error,
    notes,
    tool_events: toolEvents,
    analysis_refs: analysisRefs,
  };
}
