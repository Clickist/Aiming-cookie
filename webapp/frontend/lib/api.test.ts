import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import {
  analyzeKovaakRun,
  completeOnboarding,
  createProviderProfile,
  getKovaaKScores,
  listSessions,
  retrySession,
  syncKovaaKScores,
} from "./api";
import { getManagedVideoUrl } from "./desktop";

const originalFetch = globalThis.fetch;
const originalWindow = Reflect.get(globalThis, "window");
const originalIsTauri = Reflect.get(globalThis, "isTauri");

function restoreGlobal(name: "window" | "isTauri", value: unknown): void {
  if (value === undefined) {
    Reflect.deleteProperty(globalThis, name);
  } else {
    Reflect.set(globalThis, name, value);
  }
}

afterEach(() => {
  globalThis.fetch = originalFetch;
  restoreGlobal("window", originalWindow);
  restoreGlobal("isTauri", originalIsTauri);
});

test("desktop API requests include the in-memory launch token by default", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", true);
  Reflect.set(globalThis, "window", {
    __TAURI_INTERNALS__: {
      invoke: async (command: string) => {
        assert.equal(command, "desktop_runtime_connection");
        return {
          baseUrl: "http://127.0.0.1:43127",
          token: "test-launch-token",
        };
      },
    },
  });
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({ sessions: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  await listSessions();

  assert.equal(requests[0]?.input, "http://127.0.0.1:43127/api/sessions");
  const headers = new Headers(requests[0]?.init?.headers);
  assert.equal(headers.get("X-User-Id"), "desktop-local");
  assert.equal(
    headers.get("X-Aiming-Cookie-Desktop-Token"),
    "test-launch-token",
  );
});

test("browser API requests stay relative and do not add a desktop token", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({ sessions: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  await listSessions();

  assert.equal(requests[0]?.input, "/api/sessions");
  const headers = new Headers(requests[0]?.init?.headers);
  assert.equal(headers.get("X-Aiming-Cookie-Desktop-Token"), null);
});

test("analysis write requests forward their stable idempotency keys", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", true);
  Reflect.set(globalThis, "window", {
    __TAURI_INTERNALS__: {
      invoke: async () => ({
        baseUrl: "http://127.0.0.1:43127",
        token: "test-launch-token",
      }),
    },
  });
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({ session_id: 11, id: 11 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  await analyzeKovaakRun(
    7,
    { input_mode: "input_native" },
    { idempotencyKey: "analyze-key" },
  );
  await retrySession(11, { idempotencyKey: "retry-key" });

  assert.equal(
    new Headers(requests[0]?.init?.headers).get("Idempotency-Key"),
    "analyze-key",
  );
  assert.equal(
    new Headers(requests[1]?.init?.headers).get("Idempotency-Key"),
    "retry-key",
  );
});

test("KovaaK scores API helper reads the neutral identity-free contract", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({
      schema_version: "kovaak_scores.v1",
      availability: "unavailable",
      observed_at: null,
      stages: [],
      items: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;

  const scores = await getKovaaKScores();

  assert.equal(requests[0]?.input, "/api/kovaak-scores");
  assert.equal(scores.availability, "unavailable");
  assert.equal(scores.observed_at, null);
});

test("KovaaK score sync helper forwards the stable input contract", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({
      schema_version: "kovaak_benchmark_sync_result.v1",
      imported_score_count: 78,
      difficulty_counts: { easier: 39, medium: 39 },
      observed_at: "2026-07-29T10:15:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;

  const result = await syncKovaaKScores({
    schema_version: "kovaak_benchmark_sync_request.v1",
    steam_id: "00000000000000000",
    identity_consent: true,
  });

  assert.equal(requests[0]?.input, "/api/benchmarks/sync/kovaaks");
  assert.deepEqual(JSON.parse(String(requests[0]?.init?.body)), {
    schema_version: "kovaak_benchmark_sync_request.v1",
    steam_id: "00000000000000000",
    identity_consent: true,
  });
  assert.equal(result.imported_score_count, 78);
});

test("desktop managed video URL survives Windows Tauri path encoding", async () => {
  const convertedPaths: string[] = [];
  Reflect.set(globalThis, "isTauri", true);
  Reflect.set(globalThis, "window", {
    __TAURI_INTERNALS__: {
      convertFileSrc: (path: string, protocol: string) => {
        assert.equal(protocol, "aiming-cookie-media");
        convertedPaths.push(path);
        return `http://${protocol}.localhost/${encodeURIComponent(path)}`;
      },
    },
  });

  const url = await getManagedVideoUrl(42);

  assert.deepEqual(convertedPaths, [""]);
  assert.equal(url, "http://aiming-cookie-media.localhost/analysis/42");
  assert.doesNotMatch(url ?? "", /%2F/i);
  assert.doesNotMatch(url ?? "", /Users|AppData|sessions|\\/);
});

test("onboarding completion persists an explicit completion kind", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({
      schema_version: "product_state.v1",
      availability: "available",
      onboarding_completed: true,
      onboarding_completion_kind: "skipped",
      has_pending_runs: false,
      has_runs: false,
      has_analyses: false,
      error: null,
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;

  await completeOnboarding("skipped");

  assert.equal(requests[0]?.input, "/api/product-state/onboarding");
  assert.deepEqual(JSON.parse(String(requests[0]?.init?.body)), {
    completed: true,
    completion_kind: "skipped",
  });
});

test("provider credential is write-only and is not copied into browser storage", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({
      id: 2,
      name: "OpenAI",
      provider_id: "openai",
      kind: "builtin",
      base_url: null,
      model_id: "gpt-test",
      is_default: true,
      configured: true,
      credential_configured: true,
      has_api_key: true,
      status: "ready",
      created_at: "2026-07-25T00:00:00Z",
      updated_at: "2026-07-25T00:00:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;

  const created = await createProviderProfile({
    name: "OpenAI",
    provider_id: "openai",
    kind: "builtin",
    model_id: "gpt-test",
    api_key: "write-only-secret",
    is_default: true,
  });

  assert.equal(created.has_api_key, true);
  assert.equal("api_key" in created, false);
  assert.equal(requests[0]?.input, "/api/provider-profiles");
});
