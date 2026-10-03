import assert from "node:assert/strict";
import http from "node:http";
import type { AddressInfo } from "node:net";
import test from "node:test";

import {
  createModelsStreamFn,
  listBuiltinProviderCatalog,
  projectCustomModelReasoning,
  resolveProviderModel,
  type PiModels,
} from "../src/provider-models.ts";
import {
  getProviderProfileStatus,
  parseProviderProfile,
  probeProviderConnection,
  testProviderConnection,
  ProviderProfileError,
} from "../src/provider-profile.ts";
import { loadPiAi, loadPiProvidersAll } from "../src/pi-source.ts";
import { SnapshotCredentialStore } from "../src/provider-auth.ts";

// 仓库/源码不含真实中转站地址（构建期注入，见 provider-models.ts）；
// 测试用占位地址验证注入链路，须在模块函数被调用前设好环境变量。
const RELAY_BASE_URL = "http://127.0.0.1:3000/v1";
process.env.AC_RELAY_BASE_URL = RELAY_BASE_URL;

const SECRET = "task3-secret-sentinel-do-not-return";

test("catalog exposes the complete pinned Pi builtin provider/model catalog without product filtering", async () => {
  const all = (await loadPiProvidersAll()) as {
    builtinModels: () => {
      getProviders(): Array<{ id: string }>;
      getModels(provider?: string): Array<{ id: string; provider: string }>;
      getModel(provider: string, modelId: string): { contextWindow?: number; reasoning?: boolean } | undefined;
    };
  };
  const expected = all.builtinModels();
  const catalog = await listBuiltinProviderCatalog();

  // Pi 内建目录完整透出（无产品过滤），注入的 aiming-cookie-relay 是唯一追加项。
  assert.deepEqual(
    catalog.providers
      .map((provider) => provider.provider_id)
      .filter((id) => id !== "aiming-cookie-relay"),
    expected.getProviders().map((provider) => provider.id),
  );
  assert.deepEqual(
    catalog.providers
      .flatMap((provider) =>
        provider.provider_id === "aiming-cookie-relay"
          ? []
          : provider.models.map((model) => `${model.provider_id}/${model.model_id}`),
      ),
    expected.getModels().map((model) => `${model.provider}/${model.id}`),
  );
  assert.ok(catalog.providers.some((provider) => provider.provider_id === "xiaomi-token-plan-sgp"));
  assert.ok(catalog.providers.length > 30);
  assert.ok(catalog.providers.reduce((count, provider) => count + provider.models.length, 0) > 1000);
});

test("aiming-cookie-relay injects as the recommended member tier with one locked model", async () => {
  // WP-C（2026-09-19 契约冻结）：这条档从内测中转升级为账号订阅档——
  // 显示名挂「（推荐）」，模型锁 deepseek-v4-flash（sub/boost 两令牌同款）。
  const catalog = await listBuiltinProviderCatalog();
  const relay = catalog.providers.find((provider) => provider.provider_id === "aiming-cookie-relay");
  assert.ok(relay, "relay provider present in catalog");
  assert.equal(relay.provider_name, "Aiming Cookie（推荐）");
  assert.equal(relay.base_url, RELAY_BASE_URL);
  assert.ok(relay.auth_modes.includes("api_key"), "stored JWT credential is an api_key credential");

  assert.deepEqual(relay.models.map((model) => model.model_id), ["deepseek-v4-flash"]);

  // 方言继承：flash 与 Pi 内建 deepseek 的同名模型共享能力（contextWindow/reasoning），
  // 缺失即 thinkingFormat 方言没带上（"说出声"复发根因）。
  const all = (await loadPiProvidersAll()) as {
    builtinModels: () => {
      getModel(provider: string, modelId: string): { contextWindow?: number; reasoning?: boolean } | undefined;
    };
  };
  const builtinFlash = all.builtinModels().getModel("deepseek", "deepseek-v4-flash");
  assert.ok(builtinFlash);
  const relayFlash = relay.models[0];
  assert.equal(relayFlash.context_window, builtinFlash.contextWindow);
  assert.equal(relayFlash.reasoning, builtinFlash.reasoning);
});

test("builtin profile aiming-cookie-relay/deepseek-v4-flash resolves with the stored token", async () => {
  const resolved = await resolveProviderModel({
    kind: "builtin",
    provider_id: "aiming-cookie-relay",
    model_id: "deepseek-v4-flash",
    api_key: "relay-test-token",
  });
  assert.equal(resolved.model.id, "deepseek-v4-flash");
  assert.equal(resolved.model.provider, "aiming-cookie-relay");
  assert.equal(resolved.model.baseUrl, RELAY_BASE_URL);
  assert.equal(resolved.hasRuntimeCredential, true);
});

test("builtin selection resolves through Pi Models.getModel and preserves api/provider/baseUrl", async () => {
  const all = (await loadPiProvidersAll()) as {
    builtinModels: () => {
      getModel(provider: string, modelId: string): Record<string, unknown> | undefined;
    };
  };
  const expected = all.builtinModels().getModel("anthropic", "claude-haiku-4-5");
  assert.ok(expected);

  const resolved = await resolveProviderModel({
    kind: "builtin",
    provider_id: "anthropic",
    model_id: "claude-haiku-4-5",
  });

  assert.equal(resolved.models.getModel("anthropic", "claude-haiku-4-5"), resolved.model);
  assert.equal(resolved.model.api, expected.api);
  assert.equal(resolved.model.provider, expected.provider);
  assert.equal(resolved.model.baseUrl, expected.baseUrl);
  assert.notEqual(resolved.model.api, "openai-completions");
});


test("production stream adapter delegates the selected model to Pi Models.streamSimple", () => {
  const marker = { stream: "marker" };
  const model = { id: "selected-model" };
  const context = { messages: [] };
  const options = { temperature: 0.2 };
  let received: unknown[] | undefined;
  const models = {
    streamSimple: (...args: unknown[]) => {
      received = args;
      return marker;
    },
  } as unknown as PiModels;

  const stream = createModelsStreamFn(models);
  assert.equal(stream(model, context, options), marker);
  assert.deepEqual(received, [model, context, options]);
});

test("custom OpenAI-compatible profile validates and constructs a Pi provider/model", async () => {
  const profile = parseProviderProfile({
    kind: "custom_openai_compatible",
    provider_id: "local-test-provider",
    provider_name: "Local Test Provider",
    base_url: "http://127.0.0.1:11434/v1/",
    api_key: SECRET,
    model_id: "fixture-model",
    context_window: 32768,
    max_tokens: 4096,
  });
  const resolved = await resolveProviderModel(profile);
  const provider = resolved.models.getProvider("local-test-provider");

  assert.ok(provider);
  assert.equal(provider.name, "Local Test Provider");
  assert.equal(resolved.models.getModel("local-test-provider", "fixture-model"), resolved.model);
  assert.equal(resolved.model.api, "openai-completions");
  assert.equal(resolved.model.provider, "local-test-provider");
  assert.equal(resolved.model.baseUrl, "http://127.0.0.1:11434/v1");
  assert.equal(resolved.model.contextWindow, 32768);
  assert.equal(resolved.model.maxTokens, 4096);
  const auth = await resolved.models.getAuth(resolved.model);
  assert.equal(auth?.auth.apiKey, SECRET);
  assert.equal(typeof provider.stream, "function");
  assert.equal(typeof provider.streamSimple, "function");
});

test("custom Anthropic-compatible profile uses Pi's Anthropic Messages adapter without exposing its key", async () => {
  const profile = parseProviderProfile({
    kind: "custom_anthropic_compatible",
    provider_id: "anthropic-gateway",
    provider_name: "Anthropic Gateway",
    base_url: "https://example.invalid/anthropic/v1/",
    credential: { type: "api_key", key: SECRET },
    model_id: "claude-compatible-model",
    context_window: 200000,
    max_tokens: 8192,
  });
  const resolved = await resolveProviderModel(profile);
  const provider = resolved.models.getProvider("anthropic-gateway");
  const status = await getProviderProfileStatus(profile);

  assert.ok(provider);
  assert.equal(provider.name, "Anthropic Gateway");
  assert.equal(resolved.models.getModel("anthropic-gateway", "claude-compatible-model"), resolved.model);
  assert.equal(resolved.model.api, "anthropic-messages");
  assert.equal(resolved.model.provider, "anthropic-gateway");
  assert.equal(resolved.model.baseUrl, "https://example.invalid/anthropic");
  assert.equal((await resolved.models.getAuth(resolved.model))?.auth.apiKey, SECRET);
  assert.equal(status.status, "ready");
  assert.ok(!JSON.stringify(status).includes(SECRET));
  assert.ok(!JSON.stringify(status).includes('"credential"'));
});

test("custom profiles without discovered limits resolve with the Pi default capabilities", async () => {
  const resolved = await resolveProviderModel({
    kind: "custom_openai_compatible",
    provider_id: "unknown-limits-provider",
    provider_name: "Unknown Limits Provider",
    base_url: "https://provider.example/v1",
    credential: { type: "api_key", key: SECRET },
    model_id: "manual-model-id",
  });

  // 与 vendored Pi coding-agent 对自定义 provider 的默认能力对保持一致。
  assert.equal(resolved.model.contextWindow, 128000);
  assert.equal(resolved.model.maxTokens, 16384);
});

test("custom profile capability defaults apply per field when only one limit is discovered", async () => {
  const resolved = await resolveProviderModel({
    kind: "custom_openai_compatible",
    provider_id: "partial-limits-provider",
    provider_name: "Partial Limits Provider",
    base_url: "https://provider.example/v1",
    credential: { type: "api_key", key: SECRET },
    model_id: "manual-model-id",
    context_window: 65536,
  });

  assert.equal(resolved.model.contextWindow, 65536);
  assert.equal(resolved.model.maxTokens, 16384);
});

test("invalid custom profiles and client-controlled api_key_env fail closed", () => {
  assert.throws(
    () =>
      parseProviderProfile({
        kind: "custom_openai_compatible",
        provider_name: "Bad Provider",
        base_url: "file:///tmp/not-http",
        api_key: SECRET,
        model_id: "fixture-model",
      }),
    (error: unknown) => error instanceof ProviderProfileError && error.code === "invalid_profile",
  );
  assert.throws(
    () =>
      parseProviderProfile({
        kind: "builtin",
        provider_id: "anthropic",
        model_id: "claude-haiku-4-5",
        api_key_env: "SECRET_ENV_NAME",
      }),
    (error: unknown) => error instanceof ProviderProfileError && error.code === "invalid_profile",
  );
});

test("unknown builtin provider and model fail closed", async () => {
  await assert.rejects(
    resolveProviderModel({ kind: "builtin", provider_id: "not-a-provider", model_id: "anything" }),
    (error: unknown) => error instanceof ProviderProfileError && error.code === "unknown_provider",
  );
  await assert.rejects(
    resolveProviderModel({ kind: "builtin", provider_id: "anthropic", model_id: "not-a-model" }),
    (error: unknown) => error instanceof ProviderProfileError && error.code === "unknown_model",
  );
});

test("profile status is non-secret and reports runtime credential source only", async () => {
  const status = await getProviderProfileStatus({
    kind: "custom_openai_compatible",
    provider_name: "Secret Test Provider",
    base_url: "https://example.invalid/v1",
    credential: { type: "api_key", key: SECRET },
    model_id: "secret-model",
    context_window: 32768,
    max_tokens: 4096,
  });
  const serialized = JSON.stringify(status);

  assert.equal(status.ok, true);
  assert.equal(status.status, "ready");
  assert.equal(status.credential_source, "runtime_profile");
  assert.ok(!serialized.includes(SECRET));
  assert.ok(!serialized.includes(SECRET));
});

test("catalog projects dynamic Pi auth modes and labels without credential data", async () => {
  const catalog = await listBuiltinProviderCatalog();
  const anthropic = catalog.providers.find((provider) => provider.provider_id === "anthropic");
  const vertex = catalog.providers.find((provider) => provider.provider_id === "google-vertex");

  assert.deepEqual(anthropic?.auth_modes, ["api_key", "ambient", "oauth"]);
  assert.equal(anthropic?.api_key_auth?.name, "Anthropic API key");
  assert.equal(anthropic?.oauth_auth?.name, "Anthropic (Claude Pro/Max)");
  assert.deepEqual(vertex?.auth_modes, ["api_key", "ambient"]);
  // 0.83.0 起 Vertex 的 api_key 通道升级为可交互登录。
  assert.equal(vertex?.api_key_auth?.interactive, true);
  assert.ok(!JSON.stringify(catalog).includes(SECRET));
});

test("generic type-tagged OAuth credential is injected into pinned Pi Models auth and never sanitized back", async () => {
  const resolved = await resolveProviderModel({
    kind: "builtin",
    provider_id: "anthropic",
    model_id: "claude-haiku-4-5",
    credential: {
      type: "oauth",
      access: SECRET,
      refresh: "refresh-secret",
      // 0.83.0 起 Pi 在解析期会主动刷新剩余有效期 <5 分钟的 OAuth token；
      // 夹具放远期有效期，避免测试触发真实刷新网络请求。
      expires: Date.now() + 30 * 60_000,
      accountId: "extra-field",
    },
  });
  const auth = await resolved.models.getAuth(resolved.model);

  assert.equal(auth?.auth.apiKey, SECRET);
  const status = await getProviderProfileStatus({
    kind: "builtin",
    provider_id: "anthropic",
    model_id: "claude-haiku-4-5",
    credential: {
      type: "oauth",
      access: SECRET,
      refresh: "refresh-secret",
      expires: Date.now() + 30 * 60_000,
      accountId: "extra-field",
    },
  });
  assert.equal(status.status, "ready");
  assert.ok(!JSON.stringify(status).includes(SECRET));
  assert.ok(!JSON.stringify(status).includes("refresh-secret"));
  assert.ok(!JSON.stringify(status).includes('"credential":'));
});

test("profile status resolves readiness through Models.getAuth without issuing a completion", async () => {
  let streamCalls = 0;
  const status = await getProviderProfileStatus(
    {
      kind: "custom_openai_compatible",
      provider_name: "Readiness only",
      base_url: "https://example.invalid/v1",
      api_key: SECRET,
      model_id: "readiness-model",
      context_window: 32768,
      max_tokens: 4096,
    },
    {
      resolveProviderModel: async () => ({
        model: {
          id: "readiness-model",
          name: "readiness-model",
          api: "openai-completions",
          provider: "readiness-only",
          baseUrl: "https://example.invalid/v1",
          reasoning: false,
          input: ["text"],
          contextWindow: 1,
          maxTokens: 1,
        },
        models: {
          getAuth: async () => ({ auth: { apiKey: SECRET }, source: "stored credential" }),
          streamSimple: () => {
            streamCalls += 1;
            throw new Error("status must not complete");
          },
        },
        hasRuntimeCredential: true,
      }) as never,
    },
  );

  assert.equal(status.status, "ready");
  assert.equal(streamCalls, 0);
  assert.ok(!JSON.stringify(status).includes(SECRET));
});

for (const code of ["auth", "oauth"] as const) {
  test(`profile status maps Pi ModelsError ${code} to needs_reauth`, async () => {
    const { ModelsError } = (await loadPiAi()) as {
      ModelsError: new (code: "auth" | "oauth", message: string) => Error;
    };
    const status = await getProviderProfileStatus(
      {
        kind: "custom_openai_compatible",
        provider_name: "Reauth Test",
        base_url: "https://example.invalid/v1",
        credential: { type: "api_key", key: SECRET },
        model_id: "reauth-model",
        context_window: 32768,
        max_tokens: 4096,
      },
      {
        resolveProviderModel: async () => ({
          model: {
            id: "reauth-model",
            name: "reauth-model",
            api: "openai-completions",
            provider: "reauth-provider",
            baseUrl: "https://example.invalid/v1",
            reasoning: false,
            input: ["text"],
            contextWindow: 1,
            maxTokens: 1,
          },
          models: {
            getAuth: async () => {
              throw new ModelsError(code, `${code} failed for ${SECRET}`);
            },
            streamSimple: () => {
              throw new Error("status must not complete");
            },
          },
          hasRuntimeCredential: true,
        }) as never,
      },
    );

    assert.equal(status.ok, false);
    assert.equal(status.status, "needs_reauth");
    assert.equal(status.error?.category, "provider_auth");
    assert.equal(status.error?.code, code);
    assert.equal(status.error?.retryable, false);
    assert.equal(status.profile?.model_id, "reauth-model");
    assert.equal(status.model?.model_id, "reauth-model");
    assert.ok(!JSON.stringify(status).includes(SECRET));
    assert.ok(!JSON.stringify(status).includes('"credential"'));
  });
}

test("explicit provider connection test aborts at the 30-second ceiling (using a shorter test timeout)", async () => {
  let receivedSignal: AbortSignal | undefined;
  const started = Date.now();
  const status = await testProviderConnection(
    {
      kind: "custom_openai_compatible",
      provider_name: "Timeout provider",
      base_url: "https://example.invalid/v1",
      api_key: SECRET,
      model_id: "timeout-model",
      context_window: 32768,
      max_tokens: 4096,
    },
    {
      timeoutMs: 10,
      resolveProviderModel: async () => ({
        model: {
          id: "timeout-model",
          name: "timeout-model",
          api: "openai-completions",
          provider: "timeout-provider",
          baseUrl: "https://example.invalid/v1",
          reasoning: false,
          input: ["text"],
          contextWindow: 1,
          maxTokens: 1,
        },
        models: {
          getAuth: async () => ({ auth: { apiKey: SECRET }, source: "stored credential" }),
          streamSimple: (_model: unknown, _context: unknown, options?: { signal?: AbortSignal }) => {
            receivedSignal = options?.signal;
            return { result: () => new Promise(() => {}) };
          },
        },
        hasRuntimeCredential: true,
      }) as never,
    },
  );

  assert.equal(status.status, "connection_failed");
  assert.equal(receivedSignal?.aborted, true);
  // 超时语义不丢：固定超时文案必须保留（不被吞成通用失败文案）。
  assert.equal(status.error?.message, "Provider connection test timed out");
  assert.ok(Date.now() - started < 500);
  assert.ok(!JSON.stringify(status).includes(SECRET));
});

test("expired OAuth profile status reports readiness state without refreshing or completing", async () => {
  let getAuthCalls = 0;
  let streamCalls = 0;
  const status = await getProviderProfileStatus(
    {
      kind: "builtin",
      provider_id: "fixture-provider",
      model_id: "fixture-model",
      credential: {
        type: "oauth",
        access: SECRET,
        refresh: "refresh-secret",
        expires: Date.now() - 1,
      },
    },
    {
      resolveProviderModel: async () => ({
        model: {
          id: "fixture-model",
          name: "fixture-model",
          api: "openai-completions",
          provider: "fixture-provider",
          baseUrl: "https://example.invalid/v1",
          reasoning: false,
          input: ["text"],
          contextWindow: 1,
          maxTokens: 1,
        },
        models: {
          getAuth: async () => {
            getAuthCalls += 1;
            throw new Error("status must not refresh");
          },
          streamSimple: () => {
            streamCalls += 1;
            throw new Error("status must not complete");
          },
        },
        hasRuntimeCredential: true,
      }) as never,
    },
  );

  assert.equal(status.status, "auth_expired");
  assert.equal(getAuthCalls, 0);
  assert.equal(streamCalls, 0);
  assert.ok(!JSON.stringify(status).includes(SECRET));
  assert.ok(!JSON.stringify(status).includes("refresh-secret"));
});

test("custom profile capabilities resolve from the pi catalog by model_id, defaulting to reasoning", async () => {
  // 目录命中：deepseek-v4-flash 在 vendored 目录里标 reasoning:true，并整体
  // 继承方言与回传合同（thinkingFormat:"deepseek" + reasoning_content 回传）
  // ——缺失正是"思考说出声"复发的根因（审计#24）。
  const catalogHit = await resolveProviderModel({
    kind: "custom_openai_compatible",
    provider_id: "reasoning-catalog-provider",
    provider_name: "Reasoning Catalog Provider",
    base_url: "https://provider.example/v1",
    credential: { type: "api_key", key: SECRET },
    model_id: "deepseek-v4-flash",
  });
  assert.equal(catalogHit.model.reasoning, true);
  const compat = (catalogHit.model as { compat?: Record<string, unknown> }).compat;
  assert.equal(compat?.thinkingFormat, "deepseek");
  assert.equal(compat?.requiresReasoningContentOnAssistantMessages, true);
  assert.equal(
    (catalogHit.model as { thinkingLevelMap?: unknown }).thinkingLevelMap !== undefined,
    true,
  );

  // 目录未收录：按"会思考"兜底（默认开，参数报错的降级重试留待真实案例）。
  const unknown = await resolveProviderModel({
    kind: "custom_openai_compatible",
    provider_id: "unknown-custom-provider",
    provider_name: "Unknown Custom Provider",
    base_url: "https://provider.example/v1",
    credential: { type: "api_key", key: SECRET },
    model_id: "totally-unknown-private-model",
  });
  assert.equal(unknown.model.reasoning, true);

  // 目录命中但标注不会思考的模型维持 false（如 gpt-4o-mini 系）。
  const catalogPlain = await resolveProviderModel({
    kind: "custom_openai_compatible",
    provider_id: "plain-catalog-provider",
    provider_name: "Plain Catalog Provider",
    base_url: "https://provider.example/v1",
    credential: { type: "api_key", key: SECRET },
    model_id: "gpt-4o-mini",
  });
  assert.equal(catalogPlain.model.reasoning, false);
});

test("catalog-miss custom models default to compat.supportsDeveloperRole:false (no role:developer to strict gateways)", async () => {
  // 粉丝案例（0924 核实）：目录外模型（deepseek-flash）此前 reasoning:true 且不带
  // compat → vendored pi 对非 OpenRouter 端点兜底 supportsDeveloperRole:true →
  // 推理模型发 role:"developer" → DeepSeek 等严格网关 422 unknown variant 'developer'。
  const unknown = await resolveProviderModel({
    kind: "custom_openai_compatible",
    provider_id: "unknown-custom-provider",
    provider_name: "Unknown Custom Provider",
    base_url: "https://provider.example/v1",
    credential: { type: "api_key", key: SECRET },
    model_id: "deepseek-flash",
  });
  assert.equal(unknown.model.reasoning, true);
  const compat = (unknown.model as { compat?: Record<string, unknown> }).compat;
  assert.equal(compat?.supportsDeveloperRole, false);

  // 目录命中仍整体继承目录 compat（deepseek-v4-flash 目录自带
  // supportsDeveloperRole:false 与 thinkingFormat），不被兜底覆盖或收窄。
  const catalogHit = await resolveProviderModel({
    kind: "custom_openai_compatible",
    provider_id: "reasoning-catalog-provider",
    provider_name: "Reasoning Catalog Provider",
    base_url: "https://provider.example/v1",
    credential: { type: "api_key", key: SECRET },
    model_id: "deepseek-v4-flash",
  });
  const catalogCompat = (catalogHit.model as { compat?: Record<string, unknown> }).compat;
  assert.equal(catalogCompat?.supportsDeveloperRole, false);
  assert.equal(catalogCompat?.thinkingFormat, "deepseek");
});

/** OpenAI-compatible endpoint whose /models always answers with the given HTTP status. */
function withFakeOpenAIModelsStatus(
  status: number,
  run: (baseUrl: string) => Promise<void>,
): Promise<void> {
  const server = http.createServer((_req, res) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: { message: "gateway rejected the request" } }));
  });
  return new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", async () => {
      const port = (server.address() as AddressInfo).port;
      try {
        await run(`http://127.0.0.1:${port}/v1`);
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        await new Promise<void>((closeResolve) => server.close(() => closeResolve()));
      }
    });
  });
}

function strictGatewayMock(stopReason: string, errorMessage?: string) {
  return {
    resolveProviderModel: async () => ({
      model: {
        id: "deepseek-flash",
        name: "deepseek-flash",
        api: "openai-completions",
        provider: "strict-gateway",
        baseUrl: "https://api.deepseek.com/v1",
        reasoning: true,
        input: ["text"],
        contextWindow: 1,
        maxTokens: 1,
      },
      models: {
        getAuth: async () => ({ auth: { apiKey: SECRET }, source: "stored credential" }),
        streamSimple: () => ({
          result: async () => ({ stopReason, ...(errorMessage !== undefined ? { errorMessage } : {}) }),
        }),
      },
      hasRuntimeCredential: true,
    }) as never,
  };
}

for (const [httpStatus, expectedStatus] of [
  ["401", "needs_reauth"],
  ["403", "needs_reauth"],
  ["404", "model_unavailable"],
] as const) {
  test(`connection test surfaces pi's underlying error and maps HTTP ${httpStatus} to ${expectedStatus}`, async () => {
    const status = await testProviderConnection(
      {
        kind: "custom_openai_compatible",
        provider_name: "Strict Gateway",
        base_url: "https://api.deepseek.com/v1",
        api_key: SECRET,
        model_id: "deepseek-flash",
      },
      strictGatewayMock("error", `${httpStatus}: {"error":{"message":"Authentication Fails, your api key: ${SECRET} is invalid"}}`),
    );

    assert.equal(status.ok, false);
    assert.equal(status.status, expectedStatus);
    // code 语义保持现状：状态分类只改 status 与 message。
    assert.equal(status.error?.code, "connection_failed");
    // 底层错误文本带出（经脱敏：key 换 [REDACTED]，非密文本保留）。
    assert.ok(status.error?.message.includes(httpStatus));
    assert.ok(status.error?.message.includes("Authentication Fails"));
    assert.ok(!JSON.stringify(status).includes(SECRET));
  });
}

test("connection test failure without an HTTP status stays connection_failed with redacted text", async () => {
  const status = await testProviderConnection(
    {
      kind: "custom_openai_compatible",
      provider_name: "Strict Gateway",
      base_url: "https://api.deepseek.com/v1",
      api_key: SECRET,
      model_id: "deepseek-flash",
    },
    strictGatewayMock("error", `upstream exploded for key ${SECRET}`),
  );

  assert.equal(status.status, "connection_failed");
  assert.ok(status.error?.message.includes("upstream exploded for key"));
  assert.ok(!JSON.stringify(status).includes(SECRET));
});

test("aborted stopReason keeps the existing connection-failed semantics", async () => {
  const status = await testProviderConnection(
    {
      kind: "custom_openai_compatible",
      provider_name: "Strict Gateway",
      base_url: "https://api.deepseek.com/v1",
      api_key: SECRET,
      model_id: "deepseek-flash",
    },
    strictGatewayMock("aborted"),
  );

  assert.equal(status.status, "connection_failed");
  assert.equal(status.error?.message, "Provider connection test failed");
});

test("connection test failure logs provider/model/host/duration/status without secrets", async () => {
  const logs: string[] = [];
  const originalError = console.error;
  console.error = (...args: unknown[]) => {
    logs.push(args.map(String).join(" "));
  };
  try {
    const status = await testProviderConnection(
      {
        kind: "custom_openai_compatible",
        provider_id: "strict-gateway",
        provider_name: "Strict Gateway",
        base_url: "https://api.deepseek.com/v1",
        api_key: SECRET,
        model_id: "deepseek-flash",
      },
      strictGatewayMock("error", `401: your api key: ${SECRET} is invalid`),
    );
    assert.equal(status.status, "needs_reauth");
  } finally {
    console.error = originalError;
  }

  const line = logs.find((entry) => entry.includes("[provider-probe]"));
  assert.ok(line, "probe failure log line emitted");
  assert.ok(line.includes("provider_id=strict-gateway"), line);
  assert.ok(line.includes("model_id=deepseek-flash"), line);
  assert.ok(line.includes("host=api.deepseek.com"), line);
  assert.ok(line.includes("duration_ms="), line);
  assert.ok(line.includes("status=needs_reauth"), line);
  assert.ok(!line.includes(SECRET), line);
});

test("model-less probe maps HTTP 401/403 to needs_reauth and 404 to model_unavailable instead of swallowing", async () => {
  for (const [httpStatus, expectedStatus] of [
    [401, "needs_reauth"],
    [403, "needs_reauth"],
    [404, "model_unavailable"],
  ] as const) {
    await withFakeOpenAIModelsStatus(httpStatus, async (baseUrl) => {
      const status = await probeProviderConnection({
        kind: "custom_openai_compatible",
        provider_name: "Strict Gateway",
        base_url: baseUrl,
        api_key: SECRET,
        model_id: "",
      });
      assert.equal(status.status, expectedStatus, `HTTP ${httpStatus}`);
      assert.equal(status.error?.code, "connection_failed");
      assert.ok(status.error?.message.includes(String(httpStatus)), status.error?.message);
      assert.ok(!JSON.stringify(status).includes(SECRET));
    });
  }
});

// 1003 修复：发现端点的 reasoning 元数据投影——前端力度菜单显隐依赖它，
// 与 resolveCustomProfile 的运行时目录查询同源（命中继承、未命中默认 true）。
test("projectCustomModelReasoning inherits catalog reasoning and defaults unknown ids to reasoning", async () => {
  const all = (await loadPiProvidersAll()) as {
    builtinModels: () => { getModels(): Array<{ id: string; reasoning?: boolean }> };
  };
  const catalog = all.builtinModels().getModels();
  const reasoningHit = catalog.find((model) => model.reasoning === true);
  const plainHit = catalog.find((model) => model.reasoning === false);

  const unknownId = "definitely-not-in-any-catalog-xyz";
  const models = await projectCustomModelReasoning([
    ...(reasoningHit ? [{ model_id: reasoningHit.id, context_window: null, max_tokens: null }] : []),
    ...(plainHit ? [{ model_id: plainHit.id, context_window: null, max_tokens: null }] : []),
    { model_id: unknownId, context_window: null, max_tokens: null },
  ]);

  if (reasoningHit) assert.equal(models.find((m) => m.model_id === reasoningHit.id)?.reasoning, true);
  if (plainHit) assert.equal(models.find((m) => m.model_id === plainHit.id)?.reasoning, false);
  // 目录未收录默认"会思考"，与 resolveCustomProfile 的运行时兜底一字不差。
  assert.equal(models.find((m) => m.model_id === unknownId)?.reasoning, true);
  // 档位表：命中→pi 真支持档 ∩ UI 白名单（可为空——kimi-k3 类只留 max 的
  // 模型，UI 只出「默认」不谎称可关）；未命中→全五档（clamp 运行时兜底）。
  const whitelist = ["minimal", "low", "medium", "high", "off"];
  const hitEfforts = reasoningHit ? models.find((m) => m.model_id === reasoningHit.id)?.reasoning_efforts : undefined;
  if (hitEfforts) {
    for (const level of hitEfforts) assert.ok(whitelist.includes(level), `unexpected level ${level}`);
  }
  assert.deepEqual(models.find((m) => m.model_id === unknownId)?.reasoning_efforts, whitelist);
});

// UI 档位表的同源锁：supportedUiEffortLevels 语义复制 pi
// getSupportedThinkingLevels（同步函数不能走异步 loadPiAi），这里逐模型对账
// vendored pi 真函数——pi 侧实现一变，本测试即挂，杜绝勾选档≠运行档。
test("catalog reasoning_efforts stay in lockstep with pi getSupportedThinkingLevels", async () => {
  const ai = (await loadPiAi()) as {
    getSupportedThinkingLevels: (model: unknown) => string[];
  };
  const catalog = await listBuiltinProviderCatalog();
  const all = (await loadPiProvidersAll()) as {
    builtinModels: (options?: { credentials?: unknown }) => { getModels(): Array<{ id: string; provider: string }> };
  };
  // 对照表必须与目录同参构建（带 credentials）：OAuth 类 provider 两种调用的
  // 模型 variant 不同，裸调用会产生假分歧（kimi-k3 实测）。
  const credentials = new SnapshotCredentialStore("__catalog__");
  const raw = all.builtinModels({ credentials }).getModels();
  const whitelist = ["minimal", "low", "medium", "high", "off"];
  let checked = 0;
  for (const provider of catalog.providers) {
    for (const model of provider.models) {
      // 同名模型挂多 provider 下 variant 各异（如 gpt-5.1 的 openai/azure 条目
      // thinkingLevelMap 不同），必须 provider+id 双字段匹配。
      const hit = raw.find((candidate) => candidate.id === model.model_id && candidate.provider === provider.provider_id);
      if (!hit) continue;
      checked += 1;
      const expected = new Set(ai.getSupportedThinkingLevels(hit));
      const efforts = model.reasoning_efforts;
      // 可为空：五档全 null 只留 max（白名单外）的模型，UI 只出「默认」。
      assert.ok(Array.isArray(efforts), `${model.model_id}: efforts missing`);
      for (const level of efforts) {
        assert.ok(expected.has(level), `${model.model_id}: UI exposes ${level} but pi does not support it`);
      }
      for (const level of whitelist) {
        if (expected.has(level)) {
          assert.ok(efforts.includes(level as (typeof efforts)[number]), `${model.model_id}: pi supports ${level} but UI hides it`);
        }
      }
    }
  }
  assert.ok(checked > 0, "expected at least one catalog model checked against pi");
});
