import { randomUUID } from "node:crypto";

import {
  PROVIDER_AUTH_CAPABILITIES_SCHEMA,
  PROVIDER_AUTH_OPERATION_SCHEMA,
  PROVIDER_AUTH_RESULT_SCHEMA,
  isRecord,
  makeError,
  type ProviderAuthCapabilitiesResponse,
  type ProviderAuthCapability,
  type ProviderAuthEvent,
  type ProviderAuthMode,
  type ProviderAuthOperation,
  type ProviderAuthPrompt,
  type ProviderAuthResult,
  type ProviderCredential,
  type ApiKeyCredential,
  type OAuthCredential,
} from "./contracts.ts";
import { loadPiProvidersAll } from "./pi-source.ts";

const AUTHORIZE_TIMEOUT_MS = 15 * 60 * 1_000;
const REFRESH_TIMEOUT_MS = 30_000;
const TERMINAL_RETENTION_MS = 5 * 60 * 1_000;

export type PiAuthPrompt = { signal?: AbortSignal } & (
  | { type: "text"; message: string; placeholder?: string }
  | { type: "secret"; message: string; placeholder?: string }
  | {
      type: "select";
      message: string;
      options: readonly { id: string; label: string; description?: string }[];
    }
  | { type: "manual_code"; message: string; placeholder?: string }
);

export type PiAuthEvent =
  | { type: "auth_url"; url: string; instructions?: string }
  | {
      type: "device_code";
      userCode: string;
      verificationUri: string;
      intervalSeconds?: number;
      expiresInSeconds?: number;
    }
  | { type: "progress"; message: string };

export type PiAuthLoginCallbacks = {
  signal?: AbortSignal;
  prompt(prompt: PiAuthPrompt): Promise<string>;
  notify(event: PiAuthEvent): void;
};

export type PiApiKeyAuth = {
  name: string;
  login?(callbacks: PiAuthLoginCallbacks): Promise<ApiKeyCredential>;
  resolve(input: Record<string, unknown>): Promise<unknown>;
};

export type PiOAuthAuth = {
  name: string;
  login(callbacks: PiAuthLoginCallbacks): Promise<OAuthCredential>;
  refresh(credential: OAuthCredential): Promise<OAuthCredential>;
  toAuth(credential: OAuthCredential): Promise<Record<string, unknown>>;
};

export type PiAuthProvider = {
  id: string;
  name: string;
  baseUrl?: string;
  auth: {
    apiKey?: PiApiKeyAuth;
    oauth?: PiOAuthAuth;
  };
};

type CredentialStoreTask = (
  current: ProviderCredential | undefined,
) => Promise<ProviderCredential | undefined>;

function cloneCredential<T extends ProviderCredential | undefined>(credential: T): T {
  return (credential === undefined ? undefined : structuredClone(credential)) as T;
}

export function parseProviderCredential(
  raw: unknown,
  expectedType?: ProviderCredential["type"],
): ProviderCredential {
  if (!isRecord(raw) || (raw.type !== "api_key" && raw.type !== "oauth")) {
    throw new ProviderAuthRequestError(
      "invalid_credential",
      "credential must be a pinned Pi type-tagged credential",
      400,
    );
  }
  if (expectedType && raw.type !== expectedType) {
    throw new ProviderAuthRequestError(
      "invalid_credential",
      `credential.type must be ${expectedType}`,
      400,
    );
  }

  if (raw.type === "api_key") {
    if (raw.key !== undefined && (typeof raw.key !== "string" || raw.key.length === 0)) {
      throw new ProviderAuthRequestError(
        "invalid_credential",
        "api_key credential key must be a non-empty string when supplied",
        400,
      );
    }
    let env: Record<string, string> | undefined;
    if (raw.env !== undefined) {
      if (!isRecord(raw.env) || Object.values(raw.env).some((value) => typeof value !== "string")) {
        throw new ProviderAuthRequestError(
          "invalid_credential",
          "api_key credential env must contain string values",
          400,
        );
      }
      env = { ...(raw.env as Record<string, string>) };
    }
    return {
      type: "api_key",
      ...(typeof raw.key === "string" ? { key: raw.key } : {}),
      ...(env ? { env } : {}),
    };
  }

  if (
    typeof raw.access !== "string" ||
    raw.access.length === 0 ||
    typeof raw.refresh !== "string" ||
    raw.refresh.length === 0 ||
    typeof raw.expires !== "number" ||
    !Number.isFinite(raw.expires)
  ) {
    throw new ProviderAuthRequestError(
      "invalid_credential",
      "oauth credential requires access, refresh, and expires",
      400,
    );
  }
  return structuredClone(raw) as ProviderCredential;
}

/** Request/operation-scoped Pi CredentialStore with strict provider isolation. */
export class SnapshotCredentialStore {
  private credential: ProviderCredential | undefined;
  private chain: Promise<unknown> = Promise.resolve();

  constructor(
    readonly providerId: string,
    credential?: ProviderCredential,
  ) {
    this.credential = cloneCredential(credential);
  }

  private assertProvider(providerId: string): void {
    if (providerId !== this.providerId) {
      throw new Error(`CredentialStore is scoped to ${this.providerId}`);
    }
  }

  async read(providerId: string): Promise<ProviderCredential | undefined> {
    this.assertProvider(providerId);
    return cloneCredential(this.credential);
  }

  modify(providerId: string, task: CredentialStoreTask): Promise<ProviderCredential | undefined> {
    this.assertProvider(providerId);
    const next = (async () => {
      await this.chain.catch(() => {});
      const current = cloneCredential(this.credential);
      const updated = await task(current);
      if (updated !== undefined) this.credential = cloneCredential(updated);
      return cloneCredential(updated ?? this.credential);
    })();
    this.chain = next.catch(() => {});
    return next;
  }

  async delete(providerId: string): Promise<void> {
    this.assertProvider(providerId);
    await this.modify(providerId, async () => {
      this.credential = undefined;
      return undefined;
    });
  }

  snapshot(): ProviderCredential | undefined {
    return cloneCredential(this.credential);
  }

  take(): ProviderCredential | undefined {
    const credential = cloneCredential(this.credential);
    this.credential = undefined;
    return credential;
  }

  clear(): void {
    this.credential = undefined;
  }
}

export function projectProviderAuthCapability(provider: PiAuthProvider): ProviderAuthCapability {
  const authModes: ProviderAuthMode[] = [];
  if (provider.auth.apiKey?.login) authModes.push("api_key");
  if (provider.auth.apiKey) authModes.push("ambient");
  if (provider.auth.oauth) authModes.push("oauth");
  return {
    provider_id: provider.id,
    provider_name: provider.name,
    auth_modes: authModes,
    api_key_auth: provider.auth.apiKey
      ? {
          name: provider.auth.apiKey.name,
          interactive: typeof provider.auth.apiKey.login === "function",
        }
      : null,
    oauth_auth: provider.auth.oauth
      ? {
          name: provider.auth.oauth.name,
          refresh: true,
        }
      : null,
  };
}

async function loadBuiltinProviders(): Promise<PiAuthProvider[]> {
  const all = (await loadPiProvidersAll()) as { builtinProviders(): PiAuthProvider[] };
  return all.builtinProviders();
}

export async function listBuiltinProviderAuthCapabilities(
  loadProviders: () => Promise<PiAuthProvider[]> = loadBuiltinProviders,
): Promise<ProviderAuthCapabilitiesResponse> {
  return {
    schema_version: PROVIDER_AUTH_CAPABILITIES_SCHEMA,
    providers: (await loadProviders()).map(projectProviderAuthCapability),
  };
}

export class ProviderAuthRequestError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly statusCode: number,
  ) {
    super(message);
    this.name = "ProviderAuthRequestError";
  }
}

type PendingPrompt = {
  promptId: string;
  resolve(value: string): void;
  reject(error: Error): void;
  removeAbortListener(): void;
};

type InternalOperation = {
  public: ProviderAuthOperation;
  provider: PiAuthProvider;
  store: SnapshotCredentialStore;
  abortController: AbortController;
  pendingPrompt?: PendingPrompt;
  timeout?: ReturnType<typeof setTimeout>;
  retention?: ReturnType<typeof setTimeout>;
  resultTaken: boolean;
};

type ManagerOptions = {
  loadProviders?: () => Promise<PiAuthProvider[]>;
  terminalRetentionMs?: number;
};

function isTerminal(status: ProviderAuthOperation["status"]): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled" || status === "timed_out";
}

function abortError(): Error {
  const error = new Error("Authentication operation aborted");
  error.name = "AbortError";
  return error;
}

function requiredString(raw: Record<string, unknown>, field: string): string {
  const value = raw[field];
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new ProviderAuthRequestError("invalid_request", `${field} must be a non-empty string`, 400);
  }
  return value.trim();
}

function timeoutFor(raw: Record<string, unknown>, maximum: number): number {
  if (raw.timeout_ms === undefined) return maximum;
  if (!Number.isInteger(raw.timeout_ms) || (raw.timeout_ms as number) <= 0) {
    throw new ProviderAuthRequestError("invalid_request", "timeout_ms must be a positive integer", 400);
  }
  return Math.min(raw.timeout_ms as number, maximum);
}

function operationCopy(operation: ProviderAuthOperation): ProviderAuthOperation {
  return structuredClone(operation);
}

export class ProviderAuthOperationManager {
  private readonly operations = new Map<string, InternalOperation>();
  private readonly loadProviders: () => Promise<PiAuthProvider[]>;
  private readonly terminalRetentionMs: number;

  constructor(options: ManagerOptions = {}) {
    this.loadProviders = options.loadProviders ?? loadBuiltinProviders;
    this.terminalRetentionMs = options.terminalRetentionMs ?? TERMINAL_RETENTION_MS;
  }

  async capabilities(): Promise<ProviderAuthCapabilitiesResponse> {
    return listBuiltinProviderAuthCapabilities(this.loadProviders);
  }

  async start(raw: unknown): Promise<ProviderAuthOperation> {
    if (!isRecord(raw)) {
      throw new ProviderAuthRequestError("invalid_request", "request body must be a JSON object", 400);
    }
    const action = raw.action;
    if (action !== "login" && action !== "refresh") {
      throw new ProviderAuthRequestError("invalid_request", "action must be login or refresh", 400);
    }
    const providerId = requiredString(raw, "provider_id");
    const provider = (await this.loadProviders()).find((candidate) => candidate.id === providerId);
    if (!provider) {
      throw new ProviderAuthRequestError("unknown_provider", `Unknown provider: ${providerId}`, 400);
    }

    if (action === "login") {
      if (raw.mode !== "api_key" && raw.mode !== "oauth") {
        throw new ProviderAuthRequestError("invalid_request", "mode must be api_key or oauth", 400);
      }
      const login = raw.mode === "api_key" ? provider.auth.apiKey?.login : provider.auth.oauth?.login;
      if (!login) {
        throw new ProviderAuthRequestError(
          "auth_mode_unavailable",
          `Provider ${providerId} does not support interactive ${raw.mode} login`,
          400,
        );
      }
      const operation = this.createOperation(
        provider,
        "login",
        raw.mode,
        timeoutFor(raw, AUTHORIZE_TIMEOUT_MS),
      );
      void this.runLogin(operation, raw.mode, login.bind(raw.mode === "api_key" ? provider.auth.apiKey : provider.auth.oauth));
      return operationCopy(operation.public);
    }

    if (!provider.auth.oauth) {
      throw new ProviderAuthRequestError(
        "auth_mode_unavailable",
        `Provider ${providerId} does not support OAuth refresh`,
        400,
      );
    }
    const credential = parseProviderCredential(raw.credential, "oauth");
    const operation = this.createOperation(
      provider,
      "refresh",
      null,
      timeoutFor(raw, REFRESH_TIMEOUT_MS),
      credential,
    );
    void this.runRefresh(operation, provider.auth.oauth);
    return operationCopy(operation.public);
  }

  get(operationId: string): ProviderAuthOperation {
    return operationCopy(this.requireOperation(operationId).public);
  }

  submitInput(operationId: string, raw: unknown): ProviderAuthOperation {
    if (!isRecord(raw)) {
      throw new ProviderAuthRequestError("invalid_request", "request body must be a JSON object", 400);
    }
    const promptId = requiredString(raw, "prompt_id");
    if (typeof raw.value !== "string") {
      throw new ProviderAuthRequestError("invalid_request", "value must be a string", 400);
    }
    const operation = this.requireOperation(operationId);
    if (isTerminal(operation.public.status)) {
      throw new ProviderAuthRequestError("operation_terminal", "Authentication operation is already terminal", 409);
    }
    if (!operation.pendingPrompt || operation.pendingPrompt.promptId !== promptId) {
      throw new ProviderAuthRequestError("prompt_not_pending", "Prompt is not pending", 409);
    }

    const pending = operation.pendingPrompt;
    operation.pendingPrompt = undefined;
    pending.removeAbortListener();
    operation.public.prompt = null;
    operation.public.prompts = [];
    operation.public.status = "running";
    operation.public.updated_at = Date.now();
    pending.resolve(raw.value);
    return operationCopy(operation.public);
  }

  cancel(operationId: string): ProviderAuthOperation {
    const operation = this.requireOperation(operationId);
    if (!isTerminal(operation.public.status)) {
      this.finishTerminal(operation, "cancelled", "cancelled", "Authentication operation was cancelled", false);
      operation.abortController.abort();
    }
    return operationCopy(operation.public);
  }

  takeResult(operationId: string): ProviderAuthResult {
    const operation = this.requireOperation(operationId);
    if (operation.resultTaken) {
      throw new ProviderAuthRequestError("result_already_taken", "Authentication result was already taken", 409);
    }
    if (operation.public.status !== "succeeded" || !operation.public.result_available) {
      throw new ProviderAuthRequestError("result_unavailable", "Authentication operation has no result", 409);
    }
    const credential = operation.store.take();
    if (!credential) {
      throw new ProviderAuthRequestError("result_unavailable", "Authentication operation has no result", 409);
    }
    operation.resultTaken = true;
    operation.public.result_available = false;
    operation.public.updated_at = Date.now();
    return {
      schema_version: PROVIDER_AUTH_RESULT_SCHEMA,
      operation_id: operationId,
      credential,
    };
  }

  dispose(): void {
    for (const operation of this.operations.values()) {
      if (operation.timeout) clearTimeout(operation.timeout);
      if (operation.retention) clearTimeout(operation.retention);
      operation.abortController.abort();
      this.rejectPending(operation, abortError());
      operation.store.clear();
    }
    this.operations.clear();
  }

  private requireOperation(operationId: string): InternalOperation {
    const operation = this.operations.get(operationId);
    if (!operation) {
      throw new ProviderAuthRequestError("operation_not_found", "Authentication operation was not found", 404);
    }
    return operation;
  }

  private createOperation(
    provider: PiAuthProvider,
    action: "login" | "refresh",
    mode: "api_key" | "oauth" | null,
    timeoutMs: number,
    credential?: ProviderCredential,
  ): InternalOperation {
    const now = Date.now();
    const operation: InternalOperation = {
      public: {
        schema_version: PROVIDER_AUTH_OPERATION_SCHEMA,
        id: randomUUID(),
        action,
        provider_id: provider.id,
        mode,
        status: "running",
        prompt: null,
        prompts: [],
        events: [],
        result_available: false,
        created_at: now,
        updated_at: now,
        expires_at: now + timeoutMs,
        terminal_at: null,
        error: null,
      },
      provider,
      store: new SnapshotCredentialStore(provider.id, credential),
      abortController: new AbortController(),
      resultTaken: false,
    };
    this.operations.set(operation.public.id, operation);
    this.armTimeout(operation, operation.public.expires_at);
    return operation;
  }

  private armTimeout(operation: InternalOperation, expiresAt: number): void {
    if (operation.timeout) clearTimeout(operation.timeout);
    operation.public.expires_at = expiresAt;
    operation.timeout = setTimeout(() => {
      if (isTerminal(operation.public.status)) return;
      this.finishTerminal(operation, "timed_out", "timeout", "Authentication operation timed out", false);
      operation.abortController.abort();
    }, Math.max(0, expiresAt - Date.now()));
    operation.timeout.unref?.();
  }

  private async runLogin(
    operation: InternalOperation,
    mode: "api_key" | "oauth",
    login: (callbacks: PiAuthLoginCallbacks) => Promise<ProviderCredential>,
  ): Promise<void> {
    try {
      const credential = parseProviderCredential(
        await login({
          signal: operation.abortController.signal,
          prompt: (prompt) => this.requestPrompt(operation, prompt),
          notify: (event) => this.notify(operation, event),
        }),
        mode,
      );
      if (isTerminal(operation.public.status)) return;
      await operation.store.modify(operation.provider.id, async () => credential);
      if (isTerminal(operation.public.status)) {
        operation.store.clear();
        return;
      }
      this.finishSuccess(operation);
    } catch {
      if (!isTerminal(operation.public.status)) {
        this.finishTerminal(operation, "failed", "login_failed", "Authentication login failed", true);
      }
    }
  }

  private async runRefresh(operation: InternalOperation, oauth: PiOAuthAuth): Promise<void> {
    try {
      await operation.store.modify(operation.provider.id, async (current) => {
        if (!current || current.type !== "oauth") {
          throw new Error("OAuth credential unavailable");
        }
        return parseProviderCredential(await oauth.refresh(current), "oauth");
      });
      if (isTerminal(operation.public.status)) {
        operation.store.clear();
        return;
      }
      this.finishSuccess(operation);
    } catch {
      if (!isTerminal(operation.public.status)) {
        this.finishTerminal(operation, "failed", "refresh_failed", "OAuth refresh failed", true);
      }
    }
  }

  private requestPrompt(operation: InternalOperation, prompt: PiAuthPrompt): Promise<string> {
    if (
      isTerminal(operation.public.status) ||
      operation.abortController.signal.aborted ||
      prompt.signal?.aborted
    ) {
      return Promise.reject(abortError());
    }
    if (operation.pendingPrompt) {
      return Promise.reject(new Error("Provider requested overlapping auth prompts"));
    }

    const promptId = randomUUID();
    const publicPrompt: ProviderAuthPrompt = {
      prompt_id: promptId,
      type: prompt.type,
      message: prompt.message,
      ...(prompt.type !== "select" && prompt.placeholder !== undefined
        ? { placeholder: prompt.placeholder }
        : {}),
      ...(prompt.type === "select"
        ? { options: prompt.options.map((option) => ({ ...option })) }
        : {}),
    };
    operation.public.prompt = publicPrompt;
    operation.public.prompts = [publicPrompt];
    operation.public.status = "awaiting_input";
    operation.public.updated_at = Date.now();

    return new Promise<string>((resolve, reject) => {
      const onAbort = () => {
        if (operation.pendingPrompt?.promptId !== promptId) return;
        operation.pendingPrompt = undefined;
        operation.public.prompt = null;
        operation.public.prompts = [];
        if (!isTerminal(operation.public.status)) operation.public.status = "running";
        operation.public.updated_at = Date.now();
        reject(abortError());
      };
      prompt.signal?.addEventListener("abort", onAbort, { once: true });
      operation.pendingPrompt = {
        promptId,
        resolve,
        reject,
        removeAbortListener: () => prompt.signal?.removeEventListener("abort", onAbort),
      };
    });
  }

  private notify(operation: InternalOperation, event: PiAuthEvent): void {
    if (isTerminal(operation.public.status)) return;
    let projected: ProviderAuthEvent;
    if (event.type === "auth_url") {
      projected = {
        type: "auth_url",
        url: event.url,
        ...(event.instructions !== undefined ? { instructions: event.instructions } : {}),
      };
    } else if (event.type === "device_code") {
      projected = {
        type: "device_code",
        user_code: event.userCode,
        verification_uri: event.verificationUri,
        ...(event.intervalSeconds !== undefined ? { interval_seconds: event.intervalSeconds } : {}),
        ...(event.expiresInSeconds !== undefined ? { expires_in_seconds: event.expiresInSeconds } : {}),
      };
      if (event.expiresInSeconds !== undefined && event.expiresInSeconds > 0) {
        const providerExpiry = Date.now() + event.expiresInSeconds * 1_000;
        if (providerExpiry < operation.public.expires_at) this.armTimeout(operation, providerExpiry);
      }
    } else {
      projected = { type: "progress", message: event.message };
    }
    operation.public.events.push(projected);
    operation.public.updated_at = Date.now();
  }

  private finishSuccess(operation: InternalOperation): void {
    if (isTerminal(operation.public.status)) return;
    if (operation.timeout) clearTimeout(operation.timeout);
    operation.timeout = undefined;
    this.rejectPending(operation, abortError());
    const now = Date.now();
    operation.public.status = "succeeded";
    operation.public.result_available = true;
    operation.public.prompt = null;
    operation.public.prompts = [];
    operation.public.error = null;
    operation.public.updated_at = now;
    operation.public.terminal_at = now;
    this.armRetention(operation);
  }

  private finishTerminal(
    operation: InternalOperation,
    status: "failed" | "cancelled" | "timed_out",
    code: string,
    message: string,
    retryable: boolean,
  ): void {
    if (isTerminal(operation.public.status)) return;
    if (operation.timeout) clearTimeout(operation.timeout);
    operation.timeout = undefined;
    this.rejectPending(operation, abortError());
    operation.store.clear();
    const now = Date.now();
    operation.public.status = status;
    operation.public.result_available = false;
    operation.public.prompt = null;
    operation.public.prompts = [];
    operation.public.error = makeError({
      category: "provider_auth",
      code,
      message,
      retryable,
    });
    operation.public.updated_at = now;
    operation.public.terminal_at = now;
    this.armRetention(operation);
  }

  private rejectPending(operation: InternalOperation, error: Error): void {
    const pending = operation.pendingPrompt;
    if (!pending) return;
    operation.pendingPrompt = undefined;
    pending.removeAbortListener();
    pending.reject(error);
  }

  private armRetention(operation: InternalOperation): void {
    operation.retention = setTimeout(() => {
      operation.store.clear();
      this.operations.delete(operation.public.id);
    }, this.terminalRetentionMs);
    operation.retention.unref?.();
  }
}
