/**
 * Coach sidecar routes for the persisted Provider profiles.
 *
 * `config/provider.json` stores the v2 multi-profile document (see
 * provider-store.ts). `{id}` path segments address one stored profile;
 * collection-level semantics (`/status`, `/model` without a `profile_id`)
 * fall back to the active profile, which is also what Coach turns resolve
 * against. Model and connection semantics reuse the Pi-backed
 * provider-profile.ts helpers.
 */

import http from "node:http";

import {
  PROVIDER_MODEL_SWITCH_SCHEMA,
  isRecord,
  type CoachReasoningEffort,
  type CoachRuntimeProviderProfile,
  type CustomProviderModel,
  type ProviderCredential,
  type ProviderProfileStatusResponse,
} from "./contracts.ts";
import {
  getProviderProfileStatus,
  parseProviderProfile,
  probeProviderConnection,
  ProviderProfileError,
  testProviderConnection,
} from "./provider-profile.ts";
import {
  AIMING_COOKIE_RELAY_PROVIDER_ID,
  fetchCustomProviderModels,
  resolveProviderModel,
} from "./provider-models.ts";
import {
  activeProfileId,
  exchangeMemberTicket,
  fetchMemberMe,
  logoutMember,
  relayProfileId,
  startMemberLogin,
  testMemberConnection,
} from "./member-auth.ts";
import {
  deleteProfileById,
  findStoredProfile,
  loadProviderStore,
  saveProviderStore,
  setActiveProfileId,
  type ProviderProfileStore,
  type StoredProviderProfile,
} from "./provider-store.ts";
import {
  ProviderAuthOperationManager,
  ProviderAuthRequestError,
} from "./provider-auth.ts";

export { DEFAULT_PROFILE_ID } from "./provider-store.ts";

export type ProviderProfileView = {
  id: number;
  name: string;
  provider_id: string;
  kind: "builtin" | "custom_openai_compatible" | "custom_anthropic_compatible";
  base_url: string | null;
  model_id: string;
  reasoning_effort: CoachReasoningEffort | null;
  context_window: number | null;
  max_tokens: number | null;
  /** 模型发现存档（点点 0912 拍板）：仅自定义档，null=尚未发现过。 */
  discovered_models: CustomProviderModel[] | null;
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
    // 显示名：用户命名优先，旧档案（无 name）回落 provider_id。
    return { base_url: null, context_window: null, max_tokens: null, provider_name: profile.name ?? profile.provider_id };
  }
  return {
    base_url: profile.base_url,
    context_window: profile.context_window ?? null,
    max_tokens: profile.max_tokens ?? null,
    provider_name: profile.provider_name,
  };
}

async function projectProfile(
  entry: StoredProviderProfile,
  isDefault: boolean,
): Promise<ProviderProfileView> {
  const status = await getProviderProfileStatus(entry);
  const fields = customFields(entry);
  const credential = entry.credential;
  return {
    id: entry.id,
    name: fields.provider_name,
    provider_id: entry.provider_id,
    kind: entry.kind,
    base_url: fields.base_url,
    model_id: entry.model_id,
    reasoning_effort: entry.reasoning_effort ?? null,
    context_window: fields.context_window,
    max_tokens: fields.max_tokens,
    // 模型发现存档（点点 0912 拍板）：settings 详情页免点「获取模型」直接显示。
    discovered_models: (entry.kind === "custom_openai_compatible" || entry.kind === "custom_anthropic_compatible")
      ? entry.discovered_models ?? null
      : null,
    is_default: isDefault,
    configured: configuredFromStatus(status),
    credential_configured: credential !== undefined,
    has_api_key: credential?.type === "api_key" && typeof credential.key === "string" && credential.key.length > 0,
    status: status.status,
    created_at: null,
    updated_at: null,
  };
}

function isActiveProfile(store: ProviderProfileStore, entry: StoredProviderProfile): boolean {
  return store.active_id === entry.id;
}

function activeStoredProfile(store: ProviderProfileStore): StoredProviderProfile | undefined {
  return store.active_id !== null ? findStoredProfile(store, store.active_id) : undefined;
}

function numericRouteId(id: string): number | null {
  const parsed = Number(id);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function routeStoredProfile(store: ProviderProfileStore, id: string): StoredProviderProfile | undefined {
  const numericId = numericRouteId(id);
  return numericId !== null ? findStoredProfile(store, numericId) : undefined;
}

/** Replace the stored entry with `id`, keeping its id and list position. */
function replaceStoredProfile(
  store: ProviderProfileStore,
  id: number,
  profile: CoachRuntimeProviderProfile,
): StoredProviderProfile {
  const updated: StoredProviderProfile = { id, ...profile };
  store.profiles = store.profiles.map((entry) => (entry.id === id ? updated : entry));
  return updated;
}

/** Translate the frontend create/update body into a validated Coach profile. */
function coachProfileFromCreate(raw: unknown): CoachRuntimeProviderProfile {
  if (!isRecord(raw)) {
    throw new ProviderProfileError("invalid_profile", "provider profile must be a JSON object");
  }
  if (raw.kind === "builtin") {
    return parseProviderProfile({
      kind: "builtin",
      // 内置档同样落用户显示名（点点 0911 拍板 #10）：缺省/空白按未命名，
      // 投影回落 provider_id；非字符串交给 parseProviderProfile 统一 400。
      ...(typeof raw.name === "string" && raw.name.trim() ? { name: raw.name.trim() } : {}),
      provider_id: typeof raw.provider_id === "string" ? raw.provider_id : "",
      model_id: typeof raw.model_id === "string" ? raw.model_id : "",
      // reasoning_effort 缺省/null 都按未设置处理；非法值交给
      // parseProviderProfile 的枚举校验统一 400。
      ...(raw.reasoning_effort === undefined || raw.reasoning_effort === null
        ? {}
        : { reasoning_effort: raw.reasoning_effort }),
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
      ...(raw.reasoning_effort === undefined || raw.reasoning_effort === null
        ? {}
        : { reasoning_effort: raw.reasoning_effort }),
      context_window: typeof raw.context_window === "number" ? raw.context_window : undefined,
      max_tokens: typeof raw.max_tokens === "number" ? raw.max_tokens : undefined,
      // 模型发现存档（点点 0912 拍板）：请求体带了才覆盖，否则由调用方与
      // 已存档合并（防止改名/换 URL 的整档更新把存档列表静默抹掉）。
      ...(Array.isArray(raw.discovered_models)
        ? {
            discovered_models: (raw.discovered_models as unknown[]).filter(isRecord).map((item) => ({
              model_id: typeof item.model_id === "string" ? item.model_id : "",
              context_window: typeof item.context_window === "number" ? item.context_window : null,
              max_tokens: typeof item.max_tokens === "number" ? item.max_tokens : null,
            })).filter((item) => item.model_id),
          }
        : {}),
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
      ...(profile.name !== undefined ? { name: profile.name } : {}),
      provider_id: profile.provider_id,
      model_id: profile.model_id,
      ...(profile.reasoning_effort !== undefined ? { reasoning_effort: profile.reasoning_effort } : {}),
      api_key: key,
    });
  }
  return parseProviderProfile({
    kind: profile.kind,
    provider_id: profile.provider_id,
    provider_name: profile.provider_name,
    base_url: profile.base_url,
    model_id: profile.model_id,
    ...(profile.reasoning_effort !== undefined ? { reasoning_effort: profile.reasoning_effort } : {}),
    context_window: profile.context_window,
    max_tokens: profile.max_tokens,
    api_key: key,
  });
}

function profileWithoutCredential(profile: CoachRuntimeProviderProfile): CoachRuntimeProviderProfile {
  if (profile.kind === "builtin") {
    return {
      kind: "builtin",
      ...(profile.name !== undefined ? { name: profile.name } : {}),
      provider_id: profile.provider_id,
      model_id: profile.model_id,
      ...(profile.reasoning_effort !== undefined ? { reasoning_effort: profile.reasoning_effort } : {}),
    };
  }
  return {
    kind: profile.kind,
    provider_id: profile.provider_id,
    provider_name: profile.provider_name,
    base_url: profile.base_url,
    model_id: profile.model_id,
    ...(profile.reasoning_effort !== undefined ? { reasoning_effort: profile.reasoning_effort } : {}),
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
      ...(profile.name !== undefined ? { name: profile.name } : {}),
      provider_id: profile.provider_id,
      model_id: profile.model_id,
      ...(profile.reasoning_effort !== undefined ? { reasoning_effort: profile.reasoning_effort } : {}),
      credential,
    });
  }
  return parseProviderProfile({
    kind: profile.kind,
    provider_id: profile.provider_id,
    provider_name: profile.provider_name,
    base_url: profile.base_url,
    model_id: profile.model_id,
    ...(profile.reasoning_effort !== undefined ? { reasoning_effort: profile.reasoning_effort } : {}),
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
      const store = loadProviderStore();
      const entry = activeStoredProfile(store);
      if (!entry) {
        writeJson(res, 200, projectStatus(
          { schema_version: "coach_provider_profile_status.v1", ok: false, status: "unconfigured", profile: null, model: null, credential_source: null, error: null },
          null,
        ));
        return true;
      }
      writeJson(res, 200, projectStatus(await getProviderProfileStatus(entry), entry.id));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  // ── Aiming Cookie 会员档（WP-C，契约 §2/§3/§7.1-8）─────────────────────────
  // 换票三步与会员状态全在 sidecar 内完成：JWT 只进 provider 档凭据仓，
  // 前端拿到的是结构化状态，永远不接触 JWT 明文（② chip / ②c 用户中心数据源）。

  /** 起 device_code：返回 login_url 供 Tauri 用系统浏览器打开。 */
  if (req.method === "POST" && pathname === "/v1/provider-profiles/member/login/start") {
    try {
      const result = await startMemberLogin();
      writeJson(res, result.ok ? 200 : 502, result);
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  /** deep-link 到达：ticket + dc 换 JWT + 连通测试。所有拒绝都是 200 + 结构化 code。 */
  if (req.method === "POST" && pathname === "/v1/provider-profiles/member/exchange") {
    try {
      const body = await readJsonBody(req);
      const exchange = await exchangeMemberTicket({
        ticket: isRecord(body) && typeof body.ticket === "string" ? body.ticket : null,
        dc: isRecord(body) && typeof body.dc === "string" ? body.dc : null,
      });
      if (!exchange.ok) {
        // failed 分支不是错误：caller 据此降级为「无 ticket」路径（§3.2 触发 2）。
        writeJson(res, 200, { ok: false, code: exchange.code, message: exchange.message });
        return true;
      }
      // exchange 成功时 JWT 已由 member-auth 写进 relay 档凭据（§3.2 触发 1 第 7 步），
      // 这里紧接着跑连通测试，把「登录 + 订阅 + 连通」三件事一次性告知 wizard。
      const connection = await testMemberConnection();
      writeJson(res, 200, {
        ok: true,
        user: exchange.user,
        member: exchange.member,
        profile_id: relayProfileId(),
        connection_ok: connection.ok,
        connection_code: connection.ok ? null : connection.code,
        connection_message: connection.ok ? null : connection.message,
      });
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  /** 会员状态（②/②c/④/⑨ 唯一数据源）；未登录 → 200 + logged_in:false。 */
  if (req.method === "GET" && pathname === "/v1/provider-profiles/member/me") {
    try {
      const result = await fetchMemberMe();
      if (!result.ok) {
        writeJson(res, 200, {
          ok: false,
          logged_in: result.code !== "unauthorized",
          code: result.code,
          message: result.message,
        });
        return true;
      }
      writeJson(res, 200, {
        ok: true,
        logged_in: true,
        profile_id: relayProfileId(),
        active_profile_id: activeProfileId(),
        me: result.me,
      });
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  /** 连通测试 / ①b 态2 直连快路径：拿已存 JWT 打网关。 */
  if (req.method === "POST" && pathname === "/v1/provider-profiles/member/test") {
    try {
      const result = await testMemberConnection();
      writeJson(res, 200, result);
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  /** 退出登录（④b）：只清会员凭据；有 BYOK 则自动切过去，否则 Coach 置灰。 */
  if (req.method === "POST" && pathname === "/v1/provider-profiles/member/logout") {
    try {
      const result = logoutMember();
      writeJson(res, 200, { ok: true, ...result, active_profile_id: activeProfileId() });
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (req.method === "POST" && pathname === "/v1/provider-profiles/custom/models") {
    try {
      const body = await readJsonBody(req);
      // 已存档模式（点点 09-08 拍板：Coach 模型菜单对 custom 档同样可用）：
      // key 不出 sidecar，用档内已存凭证就地发现；协议按档 kind 推断。
      if (isRecord(body) && body.protocol === undefined && body.base_url === undefined) {
        const profileIdRaw = body.profile_id;
        if (typeof profileIdRaw !== "number" || !Number.isSafeInteger(profileIdRaw) || profileIdRaw <= 0) {
          writeJson(res, 400, { detail: "profile_id must be a positive integer" });
          return true;
        }
        const store = loadProviderStore();
        const entry = findStoredProfile(store, profileIdRaw);
        if (!entry) {
          writeJson(res, 404, { detail: "Provider profile 不存在" });
          return true;
        }
        if (entry.kind !== "custom_openai_compatible" && entry.kind !== "custom_anthropic_compatible") {
          writeJson(res, 400, { detail: "只有自定义 Provider 支持模型发现" });
          return true;
        }
        const apiKey = entry.credential?.type === "api_key" && typeof entry.credential.key === "string"
          ? entry.credential.key
          : null;
        if (!apiKey) {
          writeJson(res, 400, { detail: "这个 Provider 还没有保存 API Key" });
          return true;
        }
        const protocol = entry.kind === "custom_anthropic_compatible" ? "anthropic-messages" : "openai-completions";
        const models: CustomProviderModel[] = await fetchCustomProviderModels(protocol, entry.base_url, apiKey);
        // 点点 0912 拍板：发现结果存档一份，详情页免点「获取模型」直接显示；
        // 之后的「获取模型」只是更新这份存档。
        entry.discovered_models = models;
        saveProviderStore(store);
        writeJson(res, 200, { models });
        return true;
      }
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

  // Raycast 式先验后存：对「尚未持久化的完整候选 profile」执行与
  // /{id}/test 相同的连通性/凭据校验。只读干跑——任何分支都不触碰
  // provider store；响应复用既有 status 投影（profile_id 恒为 null）。
  if (req.method === "POST" && pathname === "/v1/provider-profiles/test") {
    try {
      const body = await readJsonBody(req);
      // 免模型连通探测（点点 0911 两步式向导）：候选档尚未选定 model 时，
      // 只验证 Provider 存在性、凭据与端点连通；选定模型后仍走完整干跑。
      if (isRecord(body) && !(typeof body.model_id === "string" && body.model_id.trim())) {
        writeJson(res, 200, projectStatus(await probeProviderConnection(body), null));
        return true;
      }
      const profile = coachProfileFromCreate(body);
      writeJson(res, 200, projectStatus(await testProviderConnection(profile), null));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (req.method === "GET" && pathname === "/v1/provider-profiles") {
    try {
      const store = loadProviderStore();
      const views = await Promise.all(
        store.profiles.map((entry) => projectProfile(entry, isActiveProfile(store, entry))),
      );
      writeJson(res, 200, { profiles: views });
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (req.method === "POST" && pathname === "/v1/provider-profiles") {
    try {
      const body = await readJsonBody(req);
      const profile = coachProfileFromCreate(body);
      // Upsert: a body id addressing an existing profile updates that profile;
      // anything else appends a new one. Other stored profiles are untouched,
      // and appending never changes the active profile unless none is set.
      const requestedId = isRecord(body)
        && typeof body.id === "number"
        && Number.isSafeInteger(body.id)
        && body.id > 0
        ? body.id
        : null;
      const store = loadProviderStore();
      const existing = requestedId !== null ? findStoredProfile(store, requestedId) : undefined;
      if (existing) {
        // 整档 upsert 未携带模型存档时沿用已存列表（与 PUT 保留法一致）。
        const mergedProfile: CoachRuntimeProviderProfile
          = !Array.isArray((profile as { discovered_models?: unknown }).discovered_models)
            && (existing.kind === "custom_openai_compatible" || existing.kind === "custom_anthropic_compatible")
            && existing.discovered_models
            ? { ...profile, discovered_models: existing.discovered_models }
            : profile;
        const updated = replaceStoredProfile(store, existing.id, mergedProfile);
        saveProviderStore(store);
        writeJson(res, 200, await projectProfile(updated, isActiveProfile(store, updated)));
        return true;
      }
      const entry: StoredProviderProfile = { id: requestedId ?? store.next_id, ...profile };
      store.profiles.push(entry);
      store.next_id = Math.max(store.next_id, entry.id + 1);
      if (store.active_id === null) store.active_id = entry.id;
      saveProviderStore(store);
      writeJson(res, 201, await projectProfile(entry, isActiveProfile(store, entry)));
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
      // reasoning_effort 是档级旋钮：缺省=沿用已存值（切模型不动力度），
      // null=清除回「未设置」，其余必须落在五档枚举内。与 model_id 同门控、
      // 同落盘，对下一段回复生效。
      const effortRaw = body.reasoning_effort;
      if (effortRaw !== undefined && effortRaw !== null
        && effortRaw !== "minimal" && effortRaw !== "low"
        && effortRaw !== "medium" && effortRaw !== "high" && effortRaw !== "off") {
        writeJson(res, 400, { detail: "reasoning_effort must be minimal|low|medium|high|off or null when supplied" });
        return true;
      }
      // `profile_id` scopes the switch to one stored profile; without it the
      // active profile (the one Coach turns use) is switched.
      const profileIdRaw = body.profile_id;
      const requestedProfileId = profileIdRaw === undefined
        ? undefined
        : typeof profileIdRaw === "number" && Number.isSafeInteger(profileIdRaw) && profileIdRaw > 0
          ? profileIdRaw
          : null;
      if (requestedProfileId === null) {
        writeJson(res, 400, { detail: "profile_id must be a positive integer when supplied" });
        return true;
      }
      const store = loadProviderStore();
      const entry = requestedProfileId !== undefined
        ? findStoredProfile(store, requestedProfileId)
        : activeStoredProfile(store);
      if (!entry) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      // Switch within the current Provider: provider_id and credential are
      // preserved; only model_id changes.
      const updated = { ...entry, model_id: body.model_id.trim() };
      if (effortRaw === null) {
        delete updated.reasoning_effort;
      } else if (effortRaw !== undefined) {
        updated.reasoning_effort = effortRaw;
      }
      // Reject a model that cannot resolve (builtin: must exist in the pinned
      // catalog; custom: must still construct a resolvable provider) before
      // writing, so the UI capability stays consistent with what is persisted.
      try {
        await resolveProviderModel(updated);
      } catch (error) {
        if (error instanceof ProviderProfileError) {
          // 只有「模型/Provider 不在目录」才提示换模型；profile 状态或
          // 凭证问题直接透传底层原因，避免误导用户逐个换模型。
          if (error.code === "unknown_model" || error.code === "unknown_provider") {
            writeJson(res, 400, { detail: "所选模型不可用，请选择当前 Provider 目录中的模型" });
          } else {
            writeJson(res, 400, { detail: error.message });
          }
          return true;
        }
        throw error;
      }
      replaceStoredProfile(store, entry.id, updated);
      saveProviderStore(store);
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
      const store = loadProviderStore();
      const entry = routeStoredProfile(store, route.id);
      if (!entry) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      writeJson(res, 200, await projectProfile(entry, isActiveProfile(store, entry)));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "root" && req.method === "PUT") {
    try {
      const body = await readJsonBody(req);
      const store = loadProviderStore();
      const entry = routeStoredProfile(store, route.id);
      if (!entry) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      // PUT 是整档更新，但请求体未携带新凭证（无 api_key 字段或空白）时必须
      // 保留原 stored credential：OnboardingFlow 重跑连接步骤会预填现有档、
      // 不重发 key，盲目整档替换会把已存 key 静默抹掉（2026-08 内测实测）。
      // 显式换 key 走带 api_key 的请求体；清除凭证走 DELETE auth/credential。
      // OAuth credential 没有可回填的 key，直接按对象挂回。
      const suppliesApiKey = isRecord(body)
        && typeof body.api_key === "string"
        && body.api_key.trim().length > 0;
      // PUT 是整档更新：请求体未携带名称时保留已存内置显示名，避免改名
      // 链路之外的整档更新把用户命名静默抹回 provider_id（与凭据保留同法）。
      const suppliesName = isRecord(body)
        && typeof body.name === "string"
        && body.name.trim().length > 0;
      const requestBody: unknown = !suppliesName && entry.kind === "builtin" && entry.name
        ? { ...(isRecord(body) ? body : {}), name: entry.name }
        : body;
      // discovered_models 同credential 保留法（点点 0912 拍板）：整档更新未携带
      // 模型存档时沿用已存列表，改名/换 URL 不把模型列表静默抹掉。
      const suppliesDiscoveredModels = isRecord(body) && Array.isArray(body.discovered_models);
      const requestBodyWithModels: unknown = !suppliesDiscoveredModels
        && (entry.kind === "custom_openai_compatible" || entry.kind === "custom_anthropic_compatible")
        && entry.discovered_models
        ? { ...(isRecord(requestBody) ? requestBody : {}), discovered_models: entry.discovered_models }
        : requestBody;
      let profile: CoachRuntimeProviderProfile;
      if (!suppliesApiKey && entry.credential?.type === "api_key") {
        profile = coachProfileFromCreate({
          ...(isRecord(requestBodyWithModels) ? requestBodyWithModels : {}),
          api_key: entry.credential.key,
        });
      } else {
        profile = coachProfileFromCreate(requestBodyWithModels);
        if (!suppliesApiKey && entry.credential) {
          profile = profileWithCredential(profile, entry.credential);
        }
      }
      const updated = replaceStoredProfile(store, entry.id, profile);
      saveProviderStore(store);
      writeJson(res, 200, await projectProfile(updated, isActiveProfile(store, updated)));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "root" && req.method === "DELETE") {
    try {
      const numericId = numericRouteId(route.id);
      const store = loadProviderStore();
      const entry = numericId !== null ? findStoredProfile(store, numericId) : undefined;
      if (!entry) {
        writeJson(res, 200, { deleted: false, id: numericId ?? route.id });
        return true;
      }
      // Deleting the active profile promotes the first remaining one; deleting
      // the last profile clears the active selection.
      deleteProfileById(store, entry.id);
      saveProviderStore(store);
      writeJson(res, 200, { deleted: true, id: entry.id });
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "default" && req.method === "POST") {
    try {
      const store = loadProviderStore();
      const entry = routeStoredProfile(store, route.id);
      if (!entry) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      setActiveProfileId(store, entry.id);
      saveProviderStore(store);
      writeJson(res, 200, await projectProfile(entry, true));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "test" && req.method === "POST") {
    try {
      const store = loadProviderStore();
      const entry = routeStoredProfile(store, route.id);
      if (!entry) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      const result = await testProviderConnection(entry);
      writeJson(res, 200, projectStatus(result, entry.id));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "api-key" && req.method === "PUT") {
    try {
      const store = loadProviderStore();
      const entry = routeStoredProfile(store, route.id);
      if (!entry) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      const body = await readJsonBody(req);
      const apiKey = isRecord(body) && typeof body.api_key === "string" ? body.api_key : "";
      const updated = replaceStoredProfile(store, entry.id, profileWithApiKey(entry, apiKey));
      saveProviderStore(store);
      writeJson(res, 200, await projectProfile(updated, isActiveProfile(store, updated)));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  // 眼睛按钮的「显示 Key」（点点 0912 拍板）：测试阶段 key 对用户可见，
  // GET 返回存档明文；仅限本地 sidecar，走与删除同一套档案鉴权路径。
  if (route.action === "credential" && req.method === "GET") {
    try {
      const store = loadProviderStore();
      const entry = routeStoredProfile(store, route.id);
      if (!entry) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      const apiKey = entry.credential?.type === "api_key" && typeof entry.credential.key === "string"
        ? entry.credential.key
        : null;
      if (!apiKey) {
        writeJson(res, 404, { detail: "这个 Provider 还没有保存 API Key" });
        return true;
      }
      writeJson(res, 200, { schema_version: "coach_provider_credential.v1", api_key: apiKey });
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "credential" && req.method === "DELETE") {
    try {
      const store = loadProviderStore();
      const entry = routeStoredProfile(store, route.id);
      if (!entry) {
        writeJson(res, 404, { detail: "Provider profile 不存在" });
        return true;
      }
      const updated = replaceStoredProfile(store, entry.id, profileWithoutCredential(entry));
      saveProviderStore(store);
      writeJson(res, 200, await projectProfile(updated, isActiveProfile(store, updated)));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "authorize" && req.method === "POST") {
    try {
      const store = loadProviderStore();
      const entry = routeStoredProfile(store, route.id);
      if (!entry) {
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
        provider_id: entry.provider_id,
      });
      writeJson(res, 202, operation);
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  if (route.action === "take-result" && req.method === "POST") {
    try {
      const store = loadProviderStore();
      const entry = routeStoredProfile(store, route.id);
      if (!entry) {
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
      const updated = replaceStoredProfile(store, entry.id, profileWithCredential(entry, result.credential));
      saveProviderStore(store);
      writeJson(res, 200, await projectProfile(updated, isActiveProfile(store, updated)));
    } catch (error) {
      writeProfileError(res, error);
    }
    return true;
  }

  return false;
}
