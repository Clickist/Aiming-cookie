import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import {
  analyzeKovaakRun,
  completeOnboarding,
  createProviderProfile,
  discoverCustomProviderModels,
  deleteKovaaKConnection,
  getKovaaKConnection,
  getKovaaKScores,
  getCurrentTraining,
  getAnalysisFamilyData,
  getAnalysisVideoBlob,
  listCustomProviderModels,
  listSessions,
  refreshKovaaKConnection,
  retrySession,
  saveKovaaKConnection,
  startCoachAnalysisSoftStart,
  syncKovaaKScores,
} from "./api";
import { getManagedVideoUrl, openKovaakScenario } from "./desktop";

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

test("browser video bytes use the owner-scoped API fetch instead of a bare media URL", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(new Uint8Array([0, 1, 2]), {
      status: 200,
      headers: { "Content-Type": "video/mp4" },
    });
  }) as typeof fetch;

  const video = await getAnalysisVideoBlob(42);

  assert.equal(requests[0]?.input, "/api/sessions/42/video");
  assert.equal(requests[0]?.init?.method, "GET");
  assert.equal(new Headers(requests[0]?.init?.headers).get("X-User-Id"), "dev");
  assert.equal(video.type, "video/mp4");
  assert.equal(video.size, 3);
});

test("analysis soft start posts only the typed analysis trigger", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({ schema_version: "coach_agent_run.v1", run_ref: "coach-run:soft-start", parent_run_ref: null, attempt: 1, status: "succeeded", phase: "completed", partial_text: null, error: null, contexts: [], events: [], created_at: "2026-08-06T00:00:00Z", started_at: "2026-08-06T00:00:00Z", finished_at: "2026-08-06T00:00:00Z" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const run = await startCoachAnalysisSoftStart(42);

  assert.equal(requests[0]?.input, "/api/coach/analysis-soft-start");
  assert.equal(requests[0]?.init?.method, "POST");
  assert.deepEqual(JSON.parse(String(requests[0]?.init?.body)), {
    schema_version: "coach_analysis_soft_start_request.v1",
    analysis_session_id: 42,
  });
  assert.equal(run.run_ref, "coach-run:soft-start");
});

test("custom Provider model discovery submits URL and key once without retaining either", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({ models: [{
      model_id: "provider-model-a",
      context_window: 32768,
      max_tokens: 4096,
    }] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const models = await listCustomProviderModels({
    protocol: "openai-completions",
    base_url: "https://provider.example/v1",
    api_key: "request-only-secret",
  });

  assert.deepEqual(models.models, [{
    model_id: "provider-model-a",
    context_window: 32768,
    max_tokens: 4096,
  }]);
  assert.equal(requests[0]?.input, "/api/provider-profiles/custom/models");
  assert.equal(requests[0]?.init?.method, "POST");
  assert.deepEqual(JSON.parse(String(requests[0]?.init?.body)), {
    protocol: "openai-completions",
    base_url: "https://provider.example/v1",
    api_key: "request-only-secret",
  });
});

test("custom Provider discovery probes both protocols and selects the successful model list", async () => {
  const requests: Array<{ protocol: string; apiKey: string }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (_input: string | URL | Request, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as { protocol: string; api_key: string };
    requests.push({ protocol: body.protocol, apiKey: body.api_key });
    if (body.protocol === "openai-completions") {
      return new Response(JSON.stringify({ detail: "not supported" }), { status: 502 });
    }
    return new Response(JSON.stringify({ models: [{
      model_id: "claude-custom",
      context_window: 200000,
      max_tokens: 8192,
    }] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const discovered = await discoverCustomProviderModels({
    base_url: "https://provider.example",
    api_key: "request-only-secret",
  });

  assert.equal(discovered.protocol, "anthropic-messages");
  assert.deepEqual(discovered.models, [{
    model_id: "claude-custom",
    context_window: 200000,
    max_tokens: 8192,
  }]);
  assert.deepEqual(requests.map((request) => request.protocol).sort(), ["anthropic-messages", "openai-completions"]);
  assert.ok(requests.every((request) => request.apiKey === "request-only-secret"));
});

test("custom Provider discovery fails only when both protocols fail", async () => {
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async () => new Response(JSON.stringify({ detail: "unavailable" }), { status: 502 })) as typeof fetch;

  await assert.rejects(
    discoverCustomProviderModels({ base_url: "https://provider.example", api_key: "request-only-secret" }),
    /protocol discovery failed/,
  );
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

test("current training API helper reads the bounded read-only projection", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({
      schema_version: "current_training.v1",
      availability: "unavailable",
      reason: "no_current_plan",
      plan_status: null,
      total_item_count: 0,
      visible_item_count: 0,
      limitations: [],
      items: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;

  const training = await getCurrentTraining();

  assert.equal(requests[0]?.input, "/api/current-training");
  assert.equal(requests[0]?.init?.method, "GET");
  assert.equal(training.schema_version, "current_training.v1");
  assert.equal(training.reason, "no_current_plan");
  assert.deepEqual(training.items, []);
});

test("analysis family data helper preserves pagination and unavailable semantics", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    return new Response(JSON.stringify({
      schema_version: "frontend_analysis_family_data.v1",
      analysis_ref: "analysis:42",
      family: "flicking",
      availability: "unavailable",
      reason: "family_detail_requires_input_native_flicking",
      limitations: [],
      total_count: 0,
      next_offset: null,
      rows: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;

  const detail = await getAnalysisFamilyData(42, { limit: 25, offset: 50 });

  assert.equal(requests[0]?.input, "/api/sessions/42/analysis-data/family?limit=25&offset=50");
  assert.equal(requests[0]?.init?.method, "GET");
  assert.equal(detail.schema_version, "frontend_analysis_family_data.v1");
  assert.equal(detail.family, "flicking");
  assert.equal(detail.reason, "family_detail_requires_input_native_flicking");
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

test("KovaaK connection adapters use the identity-free connected-account contract", async () => {
  const requests: Array<{ input: string; init?: RequestInit }> = [];
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ input: String(input), init });
    const response = init?.method === "DELETE"
      ? { deleted: true }
      : init?.method === "POST"
        ? {
            schema_version: "kovaak_benchmark_sync_result.v1",
            imported_score_count: 78,
            difficulty_counts: { easier: 39, medium: 39 },
            observed_at: "2026-07-31T10:15:00Z",
          }
        : { connected: init?.method === "PUT" || false };
    return new Response(JSON.stringify(response), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const initial = await getKovaaKConnection();
  const saved = await saveKovaaKConnection({
    steam_profile: "https://steamcommunity.com/profiles/76561199033719938/",
    identity_consent: true,
  });
  const refreshed = await refreshKovaaKConnection();
  const removed = await deleteKovaaKConnection();

  assert.deepEqual(initial, { connected: false });
  assert.deepEqual(saved, { connected: true });
  assert.equal(refreshed.imported_score_count, 78);
  assert.deepEqual(removed, { deleted: true });
  assert.deepEqual(requests.map(({ input, init }) => ({
    input,
    method: init?.method,
  })), [
    { input: "/api/kovaak-connection", method: "GET" },
    { input: "/api/kovaak-connection", method: "PUT" },
    { input: "/api/kovaak-connection/refresh", method: "POST" },
    { input: "/api/kovaak-connection", method: "DELETE" },
  ]);
  assert.deepEqual(JSON.parse(String(requests[1]?.init?.body)), {
    steam_profile: "https://steamcommunity.com/profiles/76561199033719938/",
    identity_consent: true,
  });
  for (const response of [initial, saved]) {
    assert.equal("steam_id" in response, false);
    assert.equal("steam_profile" in response, false);
  }
});

test("KovaaK connection adapters preserve the API error projection without echoing input", async () => {
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});
  globalThis.fetch = (async () => new Response(JSON.stringify({
    detail: "没有识别到有效的 Steam 个人资料链接",
  }), {
    status: 422,
    headers: { "Content-Type": "application/json" },
  })) as typeof fetch;

  await assert.rejects(
    saveKovaaKConnection({
      steam_profile: "not-a-profile",
      identity_consent: true,
    }),
    (error: unknown) => error instanceof Error
      && error.name === "ApiError_422"
      && error.message === "没有识别到有效的 Steam 个人资料链接"
      && !error.message.includes("not-a-profile"),
  );
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

test("KovaaK scenario launch forwards only the reviewed profile ref", async () => {
  const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
  Reflect.set(globalThis, "isTauri", true);
  Reflect.set(globalThis, "window", {
    __TAURI_INTERNALS__: {
      invoke: async (command: string, args?: Record<string, unknown>) => {
        calls.push({ command, args });
        return {
          status: "scenario_dispatched",
          scenario_profile_ref: "scenario:static.1wall_6targets_small@1",
          display_name: "1wall 6targets small",
          message: "已请求打开 KovaaK，请确认目标场景已加载",
        };
      },
    },
  });

  const result = await openKovaakScenario("scenario:static.1wall_6targets_small@1");

  assert.equal(result.status, "scenario_dispatched");
  assert.deepEqual(calls, [{
    command: "scenario_open",
    args: { scenarioProfileRef: "scenario:static.1wall_6targets_small@1" },
  }]);
});

test("KovaaK scenario launch reports the browser limitation without invoking Tauri", async () => {
  Reflect.set(globalThis, "isTauri", false);
  Reflect.set(globalThis, "window", {});

  const result = await openKovaakScenario("scenario:static.1wall_6targets_small@1");

  assert.equal(result.status, "desktop_unavailable");
  assert.match(result.message, /网页预览不能启动 KovaaK/);
});

test("KovaaK scenario launch rejects arbitrary text before reaching the desktop bridge", async () => {
  let invoked = false;
  Reflect.set(globalThis, "isTauri", true);
  Reflect.set(globalThis, "window", {
    __TAURI_INTERNALS__: {
      invoke: async () => {
        invoked = true;
        return {};
      },
    },
  });

  const result = await openKovaakScenario("steam://run/824270/?name=untrusted");

  assert.equal(result.status, "scenario_unmapped");
  assert.equal(invoked, false);
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
