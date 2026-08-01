import assert from "node:assert/strict";
import test from "node:test";

import {
  createModelsStreamFn,
  listBuiltinProviderCatalog,
  resolveProviderModel,
  type PiModels,
} from "../src/provider-models.ts";
import {
  getProviderProfileStatus,
  parseProviderProfile,
  testProviderConnection,
  ProviderProfileError,
} from "../src/provider-profile.ts";
import { loadPiAi, loadPiProvidersAll } from "../src/pi-source.ts";

const SECRET = "task3-secret-sentinel-do-not-return";

test("catalog exposes the complete pinned Pi builtin provider/model catalog without product filtering", async () => {
  const all = (await loadPiProvidersAll()) as {
    builtinModels: () => {
      getProviders(): Array<{ id: string }>;
      getModels(provider?: string): Array<{ id: string; provider: string }>;
    };
  };
  const expected = all.builtinModels();
  const catalog = await listBuiltinProviderCatalog();

  assert.deepEqual(
    catalog.providers.map((provider) => provider.provider_id),
    expected.getProviders().map((provider) => provider.id),
  );
  assert.deepEqual(
    catalog.providers.flatMap((provider) =>
      provider.models.map((model) => `${model.provider_id}/${model.model_id}`),
    ),
    expected.getModels().map((model) => `${model.provider}/${model.id}`),
  );
  assert.ok(catalog.providers.some((provider) => provider.provider_id === "xiaomi-token-plan-sgp"));
  assert.ok(catalog.providers.length > 30);
  assert.ok(catalog.providers.reduce((count, provider) => count + provider.models.length, 0) > 1000);
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
  });
  const resolved = await resolveProviderModel(profile);
  const provider = resolved.models.getProvider("local-test-provider");

  assert.ok(provider);
  assert.equal(provider.name, "Local Test Provider");
  assert.equal(resolved.models.getModel("local-test-provider", "fixture-model"), resolved.model);
  assert.equal(resolved.model.api, "openai-completions");
  assert.equal(resolved.model.provider, "local-test-provider");
  assert.equal(resolved.model.baseUrl, "http://127.0.0.1:11434/v1");
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
  assert.deepEqual(vertex?.auth_modes, ["ambient"]);
  assert.equal(vertex?.api_key_auth?.interactive, false);
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
      expires: Date.now() + 60_000,
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
      expires: Date.now() + 60_000,
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
