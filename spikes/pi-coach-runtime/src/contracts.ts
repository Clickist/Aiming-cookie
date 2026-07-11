export type SpikeErrorCategory =
  | "input_validation"
  | "local_cv_runtime"
  | "llm_provider"
  | "network_cloud"
  | "storage_disk"
  | "internal_unknown";

export type SpikeErrorV1 = {
  schema_version: "error.v1";
  category: SpikeErrorCategory;
  code: string;
  message: string;
  retryable: boolean;
  trace_id: string | null;
  details: unknown | null;
};

export type SpikeRuntimeEvent = {
  schema_version: "coach_runtime_event.v0";
  run_id: string;
  sequence: number;
  emitted_at: string;
  type: string;
  payload: Record<string, unknown>;
};

const errorCategories = new Set<SpikeErrorCategory>([
  "input_validation",
  "local_cv_runtime",
  "llm_provider",
  "network_cloud",
  "storage_disk",
  "internal_unknown",
]);

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function makeSpikeError(input: Omit<SpikeErrorV1, "schema_version">): SpikeErrorV1 {
  if (!errorCategories.has(input.category)) {
    throw new Error("Invalid Spike error category");
  }
  if (typeof input.code !== "string" || input.code.length === 0) {
    throw new Error("Spike error code is required");
  }
  if (typeof input.message !== "string" || input.message.length === 0) {
    throw new Error("Spike error message is required");
  }
  if (typeof input.retryable !== "boolean" || (input.trace_id !== null && typeof input.trace_id !== "string")) {
    throw new Error("Invalid Spike error fields");
  }
  return { schema_version: "error.v1", ...input };
}

export function isSpikeErrorV1(value: unknown): value is SpikeErrorV1 {
  return (
    isRecord(value) &&
    value.schema_version === "error.v1" &&
    typeof value.category === "string" &&
    errorCategories.has(value.category as SpikeErrorCategory) &&
    typeof value.code === "string" &&
    typeof value.message === "string" &&
    typeof value.retryable === "boolean" &&
    (value.trace_id === null || typeof value.trace_id === "string") &&
    ("details" in value)
  );
}
