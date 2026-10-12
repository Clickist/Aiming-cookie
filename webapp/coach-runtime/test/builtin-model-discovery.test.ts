import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-builtin-discovery-"));
process.env.DATA_ROOT = dataRoot;
// 官方档（aiming-cookie-relay）的 base_url 走环境变量注入（源码不含真实地址）。
process.env.AC_RELAY_BASE_URL = "http://127.0.0.1:3000/v1";

import {
  discoverBuiltinProviderLive,
  kickBuiltinCatalogRefresh,
  listBuiltinProviderCatalog,
  probeBuiltinProvider,
  refreshConfiguredBuiltinProviders,
  resetBuiltinCatalogRefreshForTest,
  resolveProviderModel,
} from "../src/provider-models.ts";
import { loadProviderStore, saveProviderStore } from "../src/provider-store.ts";
import {
  AIMING_COOKIE_RELAY_MODEL_ID,
  AIMING_COOKIE_RELAY_PROVIDER_ID,
  type ProviderCredential,
} from "../src/contracts.ts";

const DISCOVERY_FILE = join(dataRoot, "config", "discovered-builtin-models.json");
const DEEPSEEK_CREDENTIAL: ProviderCredential = { type: "api_key", key: "sk-discovery-test" };

/** 发现层文档形状（测试断言用）。 */
type DiscoveryDocument = {
  providers: Record<string, { models: Array<{ model_id: string }>; base_url: string | null; api: string }>;
};

function readDiscoveryDocument(): DiscoveryDocument {
  assert.ok(existsSync(DISCOVERY_FILE), "discovery file should exist");
  return JSON.parse(readFileSync(DISCOVERY_FILE, "utf8")) as DiscoveryDocument;
}

function resetStore(): void {
  saveProviderStore({ schema_version: 2, active_id: null, next_id: 1, profiles: [] });
}

function seedBuiltinProfile(providerId: string, modelId: string, credential?: ProviderCredential): void {
  const store = loadProviderStore();
  store.profiles.push({
    id: store.next_id++,
    kind: "builtin",
    provider_id: providerId,
    model_id: modelId,
    ...(credential ? { credential } : {}),
  });
  saveProviderStore(store);
}

type FetchCall = { url: string; authorization: string | null };

/** 只拦截 deepseek /models 的 fetch 桩；其余请求放行真实 fetch。 */
function stubDeepseekModelsFetch(
  payload: Array<Record<string, unknown>>,
  status = 200,
): { calls: FetchCall[]; restore: () => void } {
  const calls: FetchCall[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const headers = (init?.headers ?? {}) as Record<string, unknown>;
    calls.push({ url, authorization: typeof headers.Authorization === "string" ? headers.Authorization : null });
    if (url.startsWith("https://api.deepseek.com")) {
      return new Response(JSON.stringify({ data: payload, object: "list" }), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }
    return realFetch(input, init);
  }) as typeof fetch;
  return { calls, restore: () => { globalThis.fetch = realFetch; } };
}

const LIVE_MODELS = [
  { id: "deepseek-chat" },
  { id: "deepseek-live-new", context_window: 262144, max_tokens: 32768 },
];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test("discover persists the live list and the catalog merge appends unseen ids with default metadata", async () => {
  resetStore();
  rmSync(DISCOVERY_FILE, { force: true });
  const stub = stubDeepseekModelsFetch(LIVE_MODELS);
  try {
    const models = await discoverBuiltinProviderLive("deepseek", DEEPSEEK_CREDENTIAL);
    assert.equal(models.length, 2);
    assert.equal(stub.calls.length, 1);
    assert.equal(stub.calls[0].url, "https://api.deepseek.com/models");
    assert.equal(stub.calls[0].authorization, "Bearer sk-discovery-test");

    const document = readDiscoveryDocument();
    assert.equal(document.providers.deepseek.models.length, 2);
    assert.equal(document.providers.deepseek.base_url, "https://api.deepseek.com");
    assert.equal(document.providers.deepseek.api, "openai-completions");

    const catalog = await listBuiltinProviderCatalog();
    const deepseek = catalog.providers.find((provider) => provider.provider_id === "deepseek");
    assert.ok(deepseek);
    // 快照模型在前、发现的目录外 id 追加在后，且不重复。
    assert.equal(deepseek.models[0].model_id, "deepseek-v4-flash");
    assert.equal(deepseek.models.filter((model) => model.model_id === "deepseek-chat").length, 1);
    const appended = deepseek.models.find((model) => model.model_id === "deepseek-live-new");
    assert.ok(appended);
    assert.equal(appended.model_name, "deepseek-live-new");
    assert.equal(appended.context_window, 262144);
    assert.equal(appended.max_tokens, 32768);
    assert.equal(appended.reasoning, true);
    assert.deepEqual(appended.reasoning_efforts, ["minimal", "low", "medium", "high", "off"]);
    assert.equal(appended.base_url, "https://api.deepseek.com");
    assert.equal(appended.api, "openai-completions");
  } finally {
    stub.restore();
  }
});

test("the builtin gate accepts discovered ids and rejects unknown ids again once the overlay is gone", async () => {
  resetStore();
  rmSync(DISCOVERY_FILE, { force: true });
  const profile = {
    kind: "builtin" as const,
    provider_id: "deepseek",
    model_id: "deepseek-live-new",
    credential: DEEPSEEK_CREDENTIAL,
  };
  const stub = stubDeepseekModelsFetch(LIVE_MODELS);
  try {
    await discoverBuiltinProviderLive("deepseek", DEEPSEEK_CREDENTIAL);
  } finally {
    stub.restore();
  }
  const resolved = await resolveProviderModel(profile);
  assert.equal(resolved.model.baseUrl, "https://api.deepseek.com");
  assert.equal(resolved.model.contextWindow, 262144);
  // 未命中目录按默认能力兜底（与自定义档同语义）。
  assert.equal(resolved.model.reasoning, true);

  rmSync(DISCOVERY_FILE, { force: true });
  await assert.rejects(
    resolveProviderModel(profile),
    (error: unknown) => error instanceof Error && error.message.includes("Unknown model"),
  );
});

test("probe persists discovery on success and passes through upstream status text on failure", async () => {
  resetStore();
  rmSync(DISCOVERY_FILE, { force: true });
  const stub = stubDeepseekModelsFetch(LIVE_MODELS);
  try {
    const models = await probeBuiltinProvider("deepseek", DEEPSEEK_CREDENTIAL);
    assert.equal(models.length, 2);
    assert.ok(existsSync(DISCOVERY_FILE));
  } finally {
    stub.restore();
  }

  const failing = stubDeepseekModelsFetch([], 401);
  try {
    rmSync(DISCOVERY_FILE, { force: true });
    await assert.rejects(
      probeBuiltinProvider("deepseek", DEEPSEEK_CREDENTIAL),
      (error: unknown) => error instanceof Error && error.message.includes("(HTTP 401)"),
    );
    assert.ok(!existsSync(DISCOVERY_FILE), "failed discovery must not create the overlay");
  } finally {
    failing.restore();
  }
});

test("an empty endpoint response keeps the previous overlay", async () => {
  resetStore();
  rmSync(DISCOVERY_FILE, { force: true });
  const first = stubDeepseekModelsFetch(LIVE_MODELS);
  try {
    await discoverBuiltinProviderLive("deepseek", DEEPSEEK_CREDENTIAL);
  } finally {
    first.restore();
  }
  const empty = stubDeepseekModelsFetch([]);
  try {
    const models = await discoverBuiltinProviderLive("deepseek", DEEPSEEK_CREDENTIAL);
    assert.equal(models.length, 0);
    assert.equal(readDiscoveryDocument().providers.deepseek.models.length, 2);
  } finally {
    empty.restore();
  }
});

test("refresh discovers configured builtin providers only, skipping the relay tier and custom profiles", async () => {
  resetStore();
  rmSync(DISCOVERY_FILE, { force: true });
  seedBuiltinProfile("deepseek", "deepseek-v4-flash", DEEPSEEK_CREDENTIAL);
  seedBuiltinProfile(AIMING_COOKIE_RELAY_PROVIDER_ID, AIMING_COOKIE_RELAY_MODEL_ID, DEEPSEEK_CREDENTIAL);
  seedBuiltinProfile("deepseek", "deepseek-v4-pro"); // 无凭证：不可发现
  const stub = stubDeepseekModelsFetch(LIVE_MODELS);
  try {
    const results = await refreshConfiguredBuiltinProviders();
    assert.deepEqual(results, [{ provider_id: "deepseek", ok: true, detail: null }]);
    assert.ok(stub.calls.every((call) => call.url.startsWith("https://api.deepseek.com")));
  } finally {
    stub.restore();
  }

  const failing = stubDeepseekModelsFetch([], 500);
  try {
    const results = await refreshConfiguredBuiltinProviders();
    assert.equal(results.length, 1);
    assert.equal(results[0].provider_id, "deepseek");
    assert.equal(results[0].ok, false);
    assert.ok(results[0].detail?.includes("HTTP 500"));
  } finally {
    failing.restore();
  }
});

test("kick throttles the background refresh within the TTL window", async () => {
  resetBuiltinCatalogRefreshForTest();
  resetStore();
  rmSync(DISCOVERY_FILE, { force: true });
  seedBuiltinProfile("deepseek", "deepseek-v4-flash", DEEPSEEK_CREDENTIAL);
  const stub = stubDeepseekModelsFetch(LIVE_MODELS);
  try {
    const now = Date.now();
    const hour = 60 * 60 * 1000;
    kickBuiltinCatalogRefresh(now);
    await sleep(30);
    const afterFirst = stub.calls.length;
    assert.ok(afterFirst > 0, "first kick should trigger discovery");
    kickBuiltinCatalogRefresh(now + hour);
    await sleep(30);
    assert.equal(stub.calls.length, afterFirst, "kick within TTL must not refetch");
    kickBuiltinCatalogRefresh(now + 7 * hour);
    await sleep(30);
    assert.ok(stub.calls.length > afterFirst, "kick past TTL refreshes again");
  } finally {
    stub.restore();
    resetBuiltinCatalogRefreshForTest();
  }
});
