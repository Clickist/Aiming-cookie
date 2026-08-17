/**
 * Coach sidecar routes for the single persisted Provider profile.
 *
 * The desktop product has one owner and one selected Provider, so all
 * `{id}` path segments are compatibility shims over the single stored
 * `config/provider.json`. Read/write goes through provider-store.ts; model and
 * connection semantics reuse the Pi-backed provider-profile.ts helpers.
 */

import http from "node:http";

import {
  PROVIDER_MODEL_SWITCH_SCHEMA,
  isRecord,
  type CoachRuntimeProviderProfile,
  type CustomProviderModel,
  type ProviderCredential,
  type ProviderProfileStatusResponse,
} from "./contracts.ts";
import {
  getProviderProfileStatus,
  parseProviderProfile,
  ProviderProfileError,
  testProviderConnection,
} from "./provider-profile.ts";
import { fetchCustomProviderModels, resolveProviderModel } from "./provider-models.ts";
import { deleteProfile, loadProfile, saveProfile } from "./provider-store.ts";
import {
  ProviderAuthOperationManager,
  ProviderAuthRequestError,
} from "./provider-auth.ts";

/** Stable pseudo-id for the single stored profile. */
export const DEFAULT_PROFILE_ID = 1;

export type ProviderProfileView = {
  id: number;
  name: string;
  provider_id: string;
  kind: "builtin" | "custom_openai_compatible" | "custom_anthropic_compatible";
  base_url: string | null;
  model_id: string;
  context_window: number | null;
  max_tokens: number | null;
  is_default: boolean;
  configured: boolean;
  credential_configured: boolean;
  has_api_key: boolean;
  status: ProviderProfileStatusResponse["status"];
  created_at: string | null;
  updated_at: string | null;
};

export type ProviderProfileStatusView = {
  profile_id: number | null;
  configured: boolean;
  status: ProviderProfileStatusResponse["status"];
  message: string;
};

function readJsonBody(req: http.IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      try {
        resolve(raw.trim() ? JSON.parse(raw) : null);
      } catch {
        reject(new Error("request body is not valid JSON"));
      }
    });
    req.on("error", reject);
  });
}

function writeJson(res: http.ServerResponse, statusCode: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(statusCode, {
    "Access-Control-Allow-Headers": "content-type,x-user-id",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

function writeProfileError(res: http.ServerResponse, error: unknown): void {
  if (error instanceof ProviderAuthRequestError) {
    writeJson(res, error.statusCode, { detail: error.message });
    return;
  }
  const message = error instanceof Error ? error.message : "Provider profile operation failed";
  writeJson(res, error instanceof ProviderProfileError ? 400 : 500, { detail: message });
}

function configuredFromStatus(status: ProviderProfileStatusResponse): boolean {
  return (
    status.ok
    && (status.status === "ready"
      || status.status === "auth_expired"
      || status.status === "model_unavailable"
      || status.status === "connection_failed")
  );
}

function statusMessage(status: ProviderProfileStatusResponse): string {
  if (status.status === "ready") return "Provider 连接成功";
  if (status.status === "auth_expired") return "Provider OAuth credential 已过期";
  if (status.status === "needs_reauth") return "Provider credential 需要重新认证";
  if (status.status === "model_unavailable") return "当前 Provider model 不可用，请重新选择";
  if (status.error?.message) return status.error.message;
  if (status.status === "connection_failed") return "Provider 连接测试失败，请检查设置后重试";
  return "Coach Provider 尚未就绪";
}

function projectStatus(
  result: ProviderProfileStatusResponse,
  profileId: number | null,
): ProviderProfileStatusView {
  return {
    profile_id: profileId,
    configured: configuredFromStatus(result),
    status: result.status,
    message: statusMessage(result),
  };
}

function customFields(profile: CoachRuntimeProviderProfile): {
  base_url: string | null;
  context_window: number | null;
  max_tokens: number | null;
  provider_name: string;
} {
  if (profile.kind === "builtin") {
    return { base_url: null, context_window: null, max_tokens: null, provider_name: profile.provider_id };
  }
  return {
    base_url: profile.base_url,
    context_window: profile.context_window ?? null,
    max_tokens: profile.max_tokens ?? null,
    provider_name: profile.provider_name,
  };
}

async function projectProfile(profile: CoachRuntimeProviderProfile): Promise<ProviderProfileView> {
  const status = await getProviderProfileStatus(profile);
  const fields = customFields(profile);
  const credential = profile.credential;
  return {
    id: DEFAULT_PROFILE_ID,
    name: fields.provider_name,
    provider_id: profile.provider_id,
    kind: profile.kind,
    base_url: fields.base_url,
    model_id: profile.model_id,
    context_window: fields.context_window,
    max_tokens: fields.max_tokens,
    is_default: true,
    configured: configuredFromStatus(status),
    credential_configured: credential !== undefined,
    has_api_key: credential?.type === "api_key" && typeof credential.key === "string" && credential.key.length > 0,
    status: status.status,
    created_at: null,
    updated_at: null,
  };
}

/** Translate the frontend create/update body into a validated Coach profile. */
function coachProfileFromCreate(raw: unknown): CoachRuntimeProviderProfile {
  if (!isRecord(raw)) {
    throw new ProviderProfileError("invalid_profile", "provider profile must be a JSON object");
  }
  if (raw.kind === "builtin") {
    return parseProviderProfile({
      kind: "builtin",
      provider_id: typeof raw.provider_id === "string" ? raw.provider_id : "",
      model_id: typeof raw.model_id === "string" ? raw.model_id : "",
      ...(typeof raw.api_key === "string" && raw.api_key.trim() ? { api_key: raw.api_key.trim() } : {}),
    });
  }
  if (raw.kind === "custom_openai_compatible" || raw.kind === "custom_anthropic_compatible") {
    return parseProviderProfile({
      kind: raw.kind,
      // The HTTP create contract uses `name` (frontend ProviderProfileCreate);
      // the runtime profile shape uses `provider_name`.
      provider_name: typeof raw.name === "string" && raw.name.trim() ? raw.name.trim() : "自定义 Provider",
      provider_id: typeof raw.provider_id === "string" && raw.provider_id.trim() ? raw.provider_id.trim() : undefined,
      base_url: typeof raw.base_url === "string" ? raw.base_url : "",
      model_id: typeof raw.model_id === "string" ? raw.model_id : "",
      context_window: typeof raw.context_window === "number" ? raw.context_window : undefined,
      max_tokens: typeof raw.max_tokens === "number" ? raw.max_tokens : undefined,
      api_key: typeof raw.api_key === "string" ? raw.api_key : "",
    });
  }
  throw new ProviderProfileError("invalid_profile", "model.kind must select a supported provider profile kind");
}

function profileWithApiKey(profile: CoachRuntimeProviderProfile, apiKey: string): CoachRuntimeProviderProfile {
  const key = apiKey.trim();
  if (!key) throw new ProviderProfileError("invalid_profile", "api_key must not be blank");
  if (profile.kind === "builtin") {
    return parseProviderProfile({
      kind: "builtin",
      provider_id: profile.provider_id,
      model_id: profile.model_id,
      api_key: key,
    });
  }
  return parseProviderProfile({
    kind: profile.kind,
    provider_id: profile.provider_id,
    provider_name: profile.provider_name,
    base_url: profile.base_url,
    model_id: profile.model_id,
    context_window: profile.context_window,
    max_tokens: profile.max_tokens,
    api_key: key,
  });
}

function profileWithoutCredential(profile: CoachRuntimeProviderProfile): CoachRuntimeProviderProfile {
  if (profile.kind === "builtin") {
    return { kind: "builtin", provider_id: profile.provider_id, model_id: profile.model_id };
  }
  return {
    kind: profile.kind,
    provider_id: profile.provider_id,
    provider_name: profile.provider_name,
    base_url: profile.base_url,
    model_id: profile.model_id,
    context_window: profile.context_window,
    max_tokens: profile.max_tokens,
  };
}

function profileWithCredential(
  profile: CoachRuntimeProviderProfile,
  credential: ProviderCredential,
): CoachRuntimeProviderProfile {
  if (profile.kind === "builtin") {
    return parseProviderProfile({
      kind: "builtin",
      provider_id: profile.provider_id,
      model_id: profile.model_id,
      credential,
    });
  }
  return parseProviderProfile({
    kind: profile.kind,
    provider_id: profile.provider_id,
    provider_name: profile.provider_name,
    base_url: profile.base_url,
    model_id: profile.model_id,
    context_window: profile.context_window,
    max_tokens: profile.max_tokens,
    credential,
  });
}

function profileIdAction(pathname: string):
  | { id: string; action: "root" | "default" | "test" | "api-key" | "credential" | "authorize" | "take-result" }
  | null {
  const match = /^\/v1\/provider-profiles\/([^/]+)(?:\/([^/]+)(?:\/([^/]+))?)?$/.exec(pathname);
  if (!match) return null;
  const id = decodeURIComponent(match[1]);
  const sub = match[2];
  const sub2 = match[3];
  if (!sub) return { id, action: "root" };
  if (sub === "default") return { id, action: "default" };
  if (sub === "test") return { id, action: "test" };
  if (sub === "auth" && sub2 === "api-key") return { id, action: "api-key" };
  if (sub === "auth" && sub2 === "credential") return { id, action: "credential" };
  if (sub === "auth" && sub2 === "authorize") return { id, action: "authorize" };
  if (sub === "auth" && sub2 === "take-result") return { id, action: "take-result" };
  return null;
}

export async function handleProviderProfileRequest(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  url: URL,
  authOperations: ProviderAuthOperationManager,
): Promise<boolean> {
  const pathname = url.pathname;
  if (!pathname.startsWith("/v1/provider-profiles")) return false;

  // Collection and default-status routes take precedence over {id} routes.
  if (req.method === "GET" && pathname === "/v1/provider-profiles/status") {
    try {
      const profile = loadProfile();
      if (!profile) {
        writeJson(res, 200, projectStatus(
          { schema_version: "coach_provider_profile_status.v1", ok: false, status: "unconfigured", profile: null, model: null, credential_source: null, error: null },
          null,
        ));
        return true;
      }
      writeJson(res, 200, projectStatus(await getProviderProfileStatus(profile), DEFAULT_PROFILE_ID));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (req.method === "POST" && pathname === "/v1/provider-profiles/custom/models") {
    try {
      const body = await readJsonBody(req);
      if (!isRecord(body)
        || (body.protocol !== "openai-completions" && body.protocol !== "anthropic-messages")
        || typeof body.base_url !== "string"
        || typeof body.api_key !== "string") {
        writeJson(res, 400, { detail: "custom model discovery input is invalid" });
        return true;
      }
      const models: CustomProviderModel[] = await fetchCustomProviderModels(
        body.protocol,
        body.base_url,
        body.api_key,
      );
      writeJson(res, 200, { models });
    } catch (error) {
      writeJson(res, 502, { detail: "无法读取这个 Provider 的模型列表" });
    }
    return true;
  }

  if (req.method === "GET" && pathname === "/v1/provider-profiles") {
    try {
      const profile = loadProfile();
      writeJson(res, 200, { profiles: profile ? [await projectProfile(profile)] : [] });
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (req.method === "POST" && pathname === "/v1/provider-profiles") {
    try {
      const profile = coachProfileFromCreate(await readJsonBody(req));
      saveProfile(profile);
      writeJson(res, 201, await projectProfile(profile));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (req.method === "POST" && pathname === "/v1/provider-profiles/model") {
    try {
      const body = await readJsonBody(req);
      if (!isRecord(body)
        || body.schema_version !== PROVIDER_MODEL_SWITCH_SCHEMA
        || typeof body.model_id !== "string"
        || !body.model_id.trim()) {
        writeJson(res, 400, { detail: "model switch body must include schema_version and a non-empty model_id" });
        return true;
      }
      const profile = loadProfile();
      if (!profile) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      // Switch within the current Provider: provider_id and credential are
      // preserved; only model_id changes.
      const updated = { ...profile, model_id: body.model_id.trim() };
      // Reject a model that cannot resolve (builtin: must exist in the pinned
      // catalog; custom: must still satisfy capability checks) before writing,
      // so the UI capability stays consistent with what is persisted.
      try {
        await resolveProviderModel(updated);
      } catch (error) {
        if (error instanceof ProviderProfileError) {
          // 只有「模型/Provider 不在目录」才提示换模型；profile 状态、
          // 凭据或能力问题直接透传底层原因，避免误导用户逐个换模型。
          if (error.code === "unknown_model" || error.code === "unknown_provider") {
            writeJson(res, 400, { detail: "所选模型不可用，请选择当前 Provider 目录中的模型" });
          } else {
            writeJson(res, 400, { detail: error.message });
          }
          return true;
        }
        throw error;
      }
      saveProfile(updated);
      writeJson(res, 200, await getProviderProfileStatus(updated));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  const route = profileIdAction(pathname);
  if (!route) return false;

  if (route.action === "root" && req.method === "GET") {
    try {
      const profile = loadProfile();
      if (!profile) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      writeJson(res, 200, await projectProfile(profile));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "root" && req.method === "PUT") {
    try {
      const profile = coachProfileFromCreate(await readJsonBody(req));
      saveProfile(profile);
      writeJson(res, 200, await projectProfile(profile));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "root" && req.method === "DELETE") {
    try {
      const deleted = deleteProfile();
      writeJson(res, 200, { deleted, id: DEFAULT_PROFILE_ID });
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "default" && req.method === "POST") {
    try {
      const profile = loadProfile();
      if (!profile) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      writeJson(res, 200, await projectProfile(profile));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "test" && req.method === "POST") {
    try {
      const profile = loadProfile();
      if (!profile) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      const result = await testProviderConnection(profile);
      writeJson(res, 200, projectStatus(result, DEFAULT_PROFILE_ID));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "api-key" && req.method === "PUT") {
    try {
      const profile = loadProfile();
      if (!profile) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      const body = await readJsonBody(req);
      const apiKey = isRecord(body) && typeof body.api_key === "string" ? body.api_key : "";
      const updated = profileWithApiKey(profile, apiKey);
      saveProfile(updated);
      writeJson(res, 200, await projectProfile(updated));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "credential" && req.method === "DELETE") {
    try {
      const profile = loadProfile();
      if (!profile) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      const updated = profileWithoutCredential(profile);
      saveProfile(updated);
      writeJson(res, 200, await projectProfile(updated));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "authorize" && req.method === "POST") {
    try {
      const profile = loadProfile();
      if (!profile) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      const body = await readJsonBody(req);
      const mode = isRecord(body) && (body.mode === "api_key" || body.mode === "oauth") ? body.mode : undefined;
      if (!mode) {
        writeJson(res, 400, { detail: "mode must be api_key or oauth" });
        return true;
      }
      const operation = await authOperations.start({
        action: "login",
        mode,
        provider_id: profile.provider_id,
      });
      writeJson(res, 202, operation);
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "take-result" && req.method === "POST") {
    try {
      const profile = loadProfile();
      if (!profile) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      const body = await readJsonBody(req);
      const operationId = isRecord(body) && typeof body.operation_id === "string" ? body.operation_id : "";
      if (!operationId) {
        writeJson(res, 400, { detail: "operation_id is required" });
        return true;
      }
      const result = authOperations.takeResult(operationId);
      const updated = profileWithCredential(profile, result.credential);
      saveProfile(updated);
      writeJson(res, 200, await projectProfile(updated));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  return false;
}
