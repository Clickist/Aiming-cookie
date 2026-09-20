import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { ProviderAuthOperationManager, type PiAuthProvider } from "../src/provider-auth.ts";
import { createAgentRun, stopAgentRun } from "../src/agent-runs.ts";
import { loadKnowledgeRegistry } from "../src/knowledge-registry.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";
import { waitForTask } from "../src/task-manager.ts";

// Knowledge materialization binds to DATA_ROOT on first use (getDataRoot
// caches); point it at a throwaway directory before any test can trigger it.
const dataRoot = mkdtempSync(join(tmpdir(), "coach-sidecar-data-"));
process.env.DATA_ROOT = dataRoot;

function writeConfig(doc: unknown): void {
  mkdirSync(join(dataRoot, "config"), { recursive: true });
  writeFileSync(join(dataRoot, "config", "knowledge.json"), JSON.stringify(doc, null, 2), "utf-8");
}

function installPack(packId: string, registryRaw: unknown): void {
  const registryPath = join(dataRoot, "knowledge-packs", packId, "knowledge", "registry.json");
  mkdirSync(join(registryPath, ".."), { recursive: true });
  writeFileSync(
    registryPath,
    typeof registryRaw === "string" ? registryRaw : JSON.stringify(registryRaw, null, 2),
    "utf-8",
  );
}

function packConfig(active: string): Record<string, unknown> {
  return {
    schema_version: "knowledge_config.v1",
    active,
    installed: [
      {
        pack_id: active,
        pack_version: "1.0.0",
        display_name: "示例知识包",
        author: "Example community",
        installed_at: "2026-09-20T00:00:00Z",
        has_mapping: false,
      },
    ],
  };
}

/** Minimal valid v3-shaped third-party pack registry (inline fixture). */
function packRegistryFixture(registryVersion: string): Record<string, unknown> {
  return {
    schema_version: "coach_knowledge_registry.v3",
    registry_version: registryVersion,
    signal_aliases: {},
    sources: [
      {
        source_ref: "community.example-guide",
        source_level: "community_consensus",
        title: "Example community guide",
        author_or_org: "Example community",
        published_at: null,
        retrieved_at: "2026-09-20",
        locator: "https://example.invalid/guide",
        applicability: ["all_families"],
        supports_sections: ["definition", "scope", "expected_direction", "mechanisms"],
      },
    ],
    entries: [
      {
        entry_id: "community.example-note",
        entry_version: 1,
        status: "active",
        category: "mechanism",
        topics: ["example.topic"],
        signals: ["sparc low"],
        metric_refs: ["metric:sparc"],
        family_scope: ["static_clicking"],
        observation_refs: [],
        quality_prerequisites: [],
        definition: {
          section_ref: "community.example-note.definition",
          claim_level: "community_consensus",
          source_refs: ["community.example-guide"],
          text: "Example definition text.",
        },
        scope: {
          section_ref: "community.example-note.scope",
          claim_level: "community_consensus",
          source_refs: ["community.example-guide"],
          text: "Scope text.",
        },
        expected_direction: {
          section_ref: "community.example-note.expected-direction",
          claim_level: "community_consensus",
          source_refs: ["community.example-guide"],
          text: "higher_better",
        },
        mechanisms: [
          {
            section_ref: "community.example-note.mechanisms",
            claim_level: "community_consensus",
            source_refs: ["community.example-guide"],
            text: "Example mechanism.",
          },
        ],
        alternative_explanations: ["Alternative explanation."],
        forbidden_inferences: ["Forbidden inference."],
        limitations: ["Example limitation."],
        counterevidence: ["Example counterevidence."],
        sources: ["community.example-guide"],
        supported_uses: ["explanation_only"],
      },
    ],
  };
}

function request(
  server: http.Server,
  method: string,
  path: string,
  body?: string,
  headers?: http.OutgoingHttpHeaders,
): Promise<{ statusCode: number; json: unknown }> {
  return new Promise((resolve, reject) => {
    const address = server.address();
    if (!address || typeof address === "string") {
      reject(new Error("server not listening"));
      return;
    }
    const req = http.request(
      {
        host: "127.0.0.1",
        port: address.port,
        method,
        path,
        headers: body
          ? {
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(body),
              ...headers,
            }
          : headers,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolve({
            statusCode: res.statusCode ?? 0,
            json: raw ? JSON.parse(raw) : null,
          });
        });
      },
    );
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

test("GET /healthz returns ok", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "GET", "/healthz");
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.json, { ok: true });
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /v1/turn with invalid JSON returns 400", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/v1/turn", "{not-json");
    assert.equal(res.statusCode, 400);
    assert.equal((res.json as { ok: boolean }).ok, false);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /v1/turn sends a partial NDJSON frame before the final frame when explicitly accepted", async () => {
  let releaseFinal!: () => void;
  const finalAllowed = new Promise<void>((resolve) => { releaseFinal = resolve; });
  const server = createSidecarServer({
    turnRunner: async (_request, options) => {
      await options?.onPartial?.({
        revision: 1,
        text: "先显示这段。",
        thinking_text: null,
        elapsed_ms: 18,
        provider_rounds: 1,
      });
      await finalAllowed;
      await options?.onComplete?.({
        total_ms: 30,
        first_provider_event_ms: 5,
        first_text_delta_ms: 10,
        first_safe_text_ms: 18,
        provider_rounds: 1,
        provider_ms: 25,
        provider_round_ms: [25],
        tool_ms: 0,
        repair_ms: 0,
      });
      return {
        schema_version: "coach_runtime_turn.v1",
        run_id: "agent_run:stream-test",
        ok: true,
        reply: "先显示这段。最后完成。",
        partial_reply: null,
        error: null,
        notes: [],
        tool_events: [],
      };
    },
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    let firstFrame!: (value: Record<string, unknown>) => void;
    const first = new Promise<Record<string, unknown>>((resolve) => { firstFrame = resolve; });
    const frames: Array<Record<string, unknown>> = [];
    let buffer = "";
    const completed = new Promise<void>((resolve, reject) => {
      const req = http.request({
        host: "127.0.0.1",
        port: address.port,
        method: "POST",
        path: "/v1/turn",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/x-ndjson",
        },
      }, (res) => {
        assert.match(String(res.headers["content-type"]), /application\/x-ndjson/);
        res.on("data", (chunk: Buffer) => {
          buffer += chunk.toString("utf8");
          for (;;) {
            const newline = buffer.indexOf("\n");
            if (newline < 0) break;
            const frame = JSON.parse(buffer.slice(0, newline)) as Record<string, unknown>;
            buffer = buffer.slice(newline + 1);
            frames.push(frame);
            if (frames.length === 1) firstFrame(frame);
          }
        });
        res.on("end", resolve);
      });
      req.on("error", reject);
      req.end("{}");
    });

    const partial = await first;
    assert.equal(partial.type, "partial");
    assert.equal(partial.text, "先显示这段。");
    assert.equal(frames.length, 1);
    releaseFinal();
    await completed;
    assert.deepEqual(frames.map((frame) => frame.type), ["partial", "final"]);
    assert.equal((frames[1].response as { ok: boolean }).ok, true);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /v1/turn without NDJSON acceptance keeps the JSON response contract", async () => {
  const response = {
    schema_version: "coach_runtime_turn.v1" as const,
    run_id: "agent_run:json-test",
    ok: true,
    reply: "完整 JSON 回复。",
    partial_reply: null,
    error: null,
    notes: [],
    tool_events: [],
  };
  const server = createSidecarServer({
    turnRunner: async (_request, options) => {
      assert.equal(options, undefined);
      return response;
    },
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/v1/turn", "{}");
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.json, response);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /v1/turn reports provider profile errors as a client error", async () => {
  const response = {
    schema_version: "coach_runtime_turn.v1" as const,
    run_id: null,
    ok: false,
    reply: null,
    partial_reply: null,
    error: {
      category: "provider_profile",
      code: "unknown_model",
      message: "Provider 配置不可用，请在设置中检查后重试。",
      retryable: false,
    },
    notes: [],
    tool_events: [],
  };
  const server = createSidecarServer({ turnRunner: async () => response });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/v1/turn", "{}");
    assert.equal(res.statusCode, 400);
    assert.deepEqual(res.json, response);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /v1/turn fail-closes an invalid partial after a valid NDJSON frame", async () => {
  const server = createSidecarServer({
    turnRunner: async (_request, options) => {
      await options?.onPartial?.({
        revision: 1,
        text: "有效片段。",
        thinking_text: null,
        elapsed_ms: 10,
        provider_rounds: 1,
      });
      await options?.onPartial?.({
        revision: 3,
        text: "乱序片段。",
        thinking_text: null,
        elapsed_ms: 11,
        provider_rounds: 1,
      });
      throw new Error("the invalid partial must reject before this runner error");
    },
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const frames = await new Promise<Array<Record<string, unknown>>>((resolve, reject) => {
      const req = http.request({
        host: "127.0.0.1",
        port: address.port,
        method: "POST",
        path: "/v1/turn",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/x-ndjson",
        },
      }, (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          try {
            resolve(Buffer.concat(chunks).toString("utf8").trim().split("\n")
              .map((line) => JSON.parse(line) as Record<string, unknown>));
          } catch (error) {
            reject(error);
          }
        });
      });
      req.on("error", reject);
      req.end("{}");
    });

    assert.deepEqual(frames.map((frame) => frame.type), ["partial", "final"]);
    assert.equal((frames[1].response as { ok: boolean }).ok, false);
    assert.equal(((frames[1].response as { error: { code: string } }).error).code, "unhandled");
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /v1/turn/:runId/stop is a versioned idempotent runtime control", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/v1/turn/agent_run%3Atest/stop");
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.json, {
      schema_version: "coach_runtime_stop.v1",
      stopped: false,
    });
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("GET /v1/catalog exposes the full non-secret Pi catalog", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "GET", "/v1/catalog");
    assert.equal(res.statusCode, 200);
    const body = res.json as { providers: Array<{ provider_id: string; models: unknown[] }> };
    assert.ok(body.providers.length > 30);
    assert.ok(body.providers.reduce((count, provider) => count + provider.models.length, 0) > 1000);
    assert.ok(body.providers.every((provider) => Array.isArray((provider as { auth_modes?: unknown }).auth_modes)));
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /v1/profile/status never returns the runtime api key", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const secret = "sidecar-secret-sentinel-do-not-return";
  try {
    const res = await request(
      server,
      "POST",
      "/v1/profile/status",
      JSON.stringify({
        profile: {
          kind: "custom_openai_compatible",
          provider_name: "Sidecar Test",
          base_url: "https://example.invalid/v1",
          api_key: secret,
          model_id: "sidecar-model",
          context_window: 32768,
          max_tokens: 4096,
        },
      }),
    );
    assert.equal(res.statusCode, 200);
    assert.equal((res.json as { status: string }).status, "ready");
    assert.ok(!JSON.stringify(res.json).includes(secret));
    assert.ok(!JSON.stringify(res.json).includes("api_key"));
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});


test("v0 Python provider catalog and connection-test aliases remain compatible", async () => {
  let receivedAuthorization: string | undefined;
  const providerServer = http.createServer(async (req, res) => {
    receivedAuthorization = req.headers.authorization;
    for await (const _chunk of req) {
      // Drain the request body before replying.
    }
    if (req.url?.startsWith("/fail/")) {
      res.writeHead(401, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: { message: `bad credential ${receivedAuthorization}` } }));
      return;
    }
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    res.write(
      `data: ${JSON.stringify({
        id: "chatcmpl-test",
        object: "chat.completion.chunk",
        created: 0,
        model: "qwen2.5",
        choices: [{ index: 0, delta: { role: "assistant", content: "OK" }, finish_reason: null }],
      })}\n\n`,
    );
    res.write(
      `data: ${JSON.stringify({
        id: "chatcmpl-test",
        object: "chat.completion.chunk",
        created: 0,
        model: "qwen2.5",
        choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
        usage: { prompt_tokens: 1, completion_tokens: 1 },
      })}\n\n`,
    );
    res.end("data: [DONE]\n\n");
  });
  await new Promise<void>((resolve) => providerServer.listen(0, "127.0.0.1", () => resolve()));
  const providerAddress = providerServer.address();
  assert.ok(providerAddress && typeof providerAddress !== "string");

  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const secret = "python-alias-secret-do-not-return";
  try {
    const catalog = await request(server, "GET", "/v0/providers/catalog");
    assert.equal(catalog.statusCode, 200);
    assert.ok((catalog.json as { providers: unknown[] }).providers.length > 30);

    const tested = await request(
      server,
      "POST",
      "/v0/providers/test",
      JSON.stringify({
        profile: {
          provider_id: "local-openai",
          provider_name: "Local OpenAI",
          kind: "custom_openai_compatible",
          base_url: `http://127.0.0.1:${providerAddress.port}/v1`,
          model_id: "qwen2.5",
          api_key: secret,
          context_window: 32768,
          max_tokens: 4096,
        },
      }),
    );
    assert.equal(tested.statusCode, 200);
    assert.equal((tested.json as { status: string }).status, "ready");
    assert.equal(receivedAuthorization, `Bearer ${secret}`);
    assert.ok(!JSON.stringify(tested.json).includes(secret));
    assert.ok(!JSON.stringify(tested.json).includes("api_key"));

    const failureSecret = "connection-failure-secret-do-not-return";
    const failed = await request(
      server,
      "POST",
      "/v0/providers/test",
      JSON.stringify({
        profile: {
          provider_id: "failing-openai",
          provider_name: "Failing OpenAI",
          kind: "custom_openai_compatible",
          base_url: `http://127.0.0.1:${providerAddress.port}/fail`,
          model_id: "failing-model",
          api_key: failureSecret,
          context_window: 32768,
          max_tokens: 4096,
        },
      }),
    );
    assert.equal(failed.statusCode, 200);
    assert.equal((failed.json as { status: string }).status, "connection_failed");
    assert.ok(!JSON.stringify(failed.json).includes(failureSecret));
    assert.ok(!JSON.stringify(failed.json).includes("api_key"));
  } finally {
    await Promise.all([
      new Promise<void>((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      }),
      new Promise<void>((resolve, reject) => {
        providerServer.close((err) => (err ? reject(err) : resolve()));
      }),
    ]);
  }
});

test("auth sidecar endpoints keep credentials private and take the result only once", async () => {
  const secret = "sidecar-auth-secret-do-not-return";
  const provider: PiAuthProvider = {
    id: "sidecar-auth-provider",
    name: "Sidecar Auth Provider",
    auth: {
      apiKey: {
        name: "Sidecar key",
        login: async (callbacks) => ({
          type: "api_key",
          key: await callbacks.prompt({ type: "secret", message: "Enter key" }),
        }),
        resolve: async () => undefined,
      },
    },
  };
  const authOperations = new ProviderAuthOperationManager({ loadProviders: async () => [provider] });
  const server = createSidecarServer({ authOperations });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const capabilities = await request(server, "GET", "/v1/auth/capabilities");
    assert.equal(capabilities.statusCode, 200);
    assert.deepEqual(
      (capabilities.json as { providers: Array<{ provider_id: string }> }).providers.map((item) => item.provider_id),
      [provider.id],
    );

    const started = await request(
      server,
      "POST",
      "/v1/auth/operations",
      JSON.stringify({ action: "login", provider_id: provider.id, mode: "api_key", timeout_ms: 1_000 }),
    );
    assert.equal(started.statusCode, 202);
    const operationId = (started.json as { id: string }).id;

    let operation = started.json as { status: string; prompt?: { prompt_id: string } | null };
    for (let index = 0; index < 100 && !operation.prompt; index += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2));
      const polled = await request(server, "GET", `/v1/auth/operations/${operationId}`);
      operation = polled.json as typeof operation;
    }
    assert.ok(operation.prompt);
    assert.equal(operation.status, "awaiting_input");

    const publicWaiting = await request(server, "GET", `/v1/auth/operations/${operationId}`);
    assert.equal(
      (publicWaiting.json as { prompts: Array<{ prompt_id: string }> }).prompts[0].prompt_id,
      operation.prompt.prompt_id,
    );

    const input = await request(
      server,
      "POST",
      `/v1/auth/operations/${operationId}/input`,
      JSON.stringify({ prompt_id: operation.prompt.prompt_id, value: secret }),
    );
    assert.equal(input.statusCode, 200);
    assert.ok(!JSON.stringify(input.json).includes(secret));

    for (let index = 0; index < 100 && operation.status !== "succeeded"; index += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2));
      const polled = await request(server, "GET", `/v1/auth/operations/${operationId}`);
      operation = polled.json as typeof operation;
    }
    assert.equal(operation.status, "succeeded");
    assert.ok(!JSON.stringify(operation).includes(secret));
    assert.ok(!JSON.stringify(operation).includes('"credential"'));

    const taken = await request(server, "POST", `/v1/auth/operations/${operationId}/take-result`, "{}");
    assert.equal(taken.statusCode, 200);
    assert.equal((taken.json as { credential: { key: string } }).credential.key, secret);

    const takenAgain = await request(server, "POST", `/v1/auth/operations/${operationId}/take-result`, "{}");
    assert.equal(takenAgain.statusCode, 409);
    assert.ok(!JSON.stringify(takenAgain.json).includes(secret));

    const cancelled = await request(server, "DELETE", `/v1/auth/operations/${operationId}`);
    assert.equal(cancelled.statusCode, 200);
    assert.ok(!JSON.stringify(cancelled.json).includes(secret));
  } finally {
    authOperations.dispose();
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("GET /v1/agent-runs/:ref/stream returns 404 for an unknown run", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "GET", "/v1/agent-runs/agent_run%3Astream-unknown/stream");
    assert.equal(res.statusCode, 404);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("GET /v1/agent-runs/:ref/stream emits a done event for a stopped run and closes", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const run = createAgentRun("test-owner", "sse terminal", { sessionId: 31 });
    await waitForTask(run.run_ref);
    const stopped = await stopAgentRun("test-owner", run.run_ref);
    assert.equal(stopped.status, "stopped");

    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const body = await new Promise<string>((resolve, reject) => {
      const req = http.request({
        host: "127.0.0.1",
        port: address.port,
        method: "GET",
        path: `/v1/agent-runs/${encodeURIComponent(run.run_ref)}/stream`,
        headers: { "Connection": "close", "X-User-Id": "test-owner" },
      }, (res) => {
        assert.equal(res.statusCode, 200);
        assert.match(String(res.headers["content-type"]), /text\/event-stream/);
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
      });
      req.on("error", reject);
      req.end();
    });
    assert.match(body, /event: done/);
    assert.match(body, /"status":"stopped"/);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /knowledge/validate accepts a minimal valid pack registry", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(
      server,
      "POST",
      "/knowledge/validate",
      JSON.stringify(packRegistryFixture("com.example.good@1.0.0")),
    );
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.json, { valid: true });
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /knowledge/validate reports validator errors without failing the request", async () => {
  const broken = packRegistryFixture("com.example.bad@1.0.0");
  broken.schema_version = "coach_knowledge_registry.v9";
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/knowledge/validate", JSON.stringify(broken));
    assert.equal(res.statusCode, 200);
    const body = res.json as { valid: boolean; errors: string[] };
    assert.equal(body.valid, false);
    assert.ok(Array.isArray(body.errors) && body.errors.length > 0);
    assert.ok(typeof body.errors[0] === "string" && body.errors[0].length > 0);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("POST /knowledge/validate returns 400 for a non-JSON body", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/knowledge/validate", "{not-json");
    assert.equal(res.statusCode, 400);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
});

test("startSidecarServer materializes the active pack registry with its display name", async () => {
  installPack("com.example.good", packRegistryFixture("com.example.good@1.0.0"));
  writeConfig(packConfig("com.example.good"));
  const { startSidecarServer } = await import("../src/sidecar-server.ts");
  const server = startSidecarServer({ port: 0 });
  try {
    const index = JSON.parse(
      readFileSync(join(dataRoot, "knowledge", "index.json"), "utf-8"),
    ) as { registry_version: string; pack_display_name?: string };
    assert.equal(index.registry_version, "com.example.good@1.0.0");
    assert.equal(index.pack_display_name, "示例知识包");
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("startSidecarServer falls back to the official registry when the active pack is broken", async () => {
  installPack("com.example.broken", "{ broken json");
  writeConfig(packConfig("com.example.broken"));
  const errors: string[] = [];
  const originalError = console.error;
  console.error = (...args: unknown[]) => { errors.push(args.map(String).join(" ")); };
  const { startSidecarServer } = await import("../src/sidecar-server.ts");
  const server = startSidecarServer({ port: 0 });
  try {
    const index = JSON.parse(
      readFileSync(join(dataRoot, "knowledge", "index.json"), "utf-8"),
    ) as { registry_version: string; pack_display_name?: string };
    assert.equal(index.registry_version, loadKnowledgeRegistry().registry_version);
    assert.ok(!("pack_display_name" in index));
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    console.error = originalError;
  }
  assert.ok(
    errors.some((line) => line.includes("com.example.broken")),
    `bad-pack fallback must be logged with the pack id: ${JSON.stringify(errors)}`,
  );
});

test("POST /knowledge/validate rejects a v1-shaped pack registry before dispatch", async () => {
  const legacy = packRegistryFixture("com.example.legacy@1.0.0");
  // v1 shape: no top-level sources, so the pack source ceiling would never
  // apply — exactly why the route must gate packs to v3 before dispatch.
  delete legacy.sources;
  legacy.schema_version = "coach_knowledge_registry.v1";
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/knowledge/validate", JSON.stringify(legacy));
    assert.equal(res.statusCode, 200);
    const body = res.json as { valid: boolean; errors: string[] };
    assert.equal(body.valid, false);
    assert.match(body.errors[0], /coach_knowledge_registry\.v3/);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("POST /knowledge/rematerialize re-materializes the active pack immediately", async () => {
  installPack("com.example.good", packRegistryFixture("com.example.good@1.0.0"));
  writeConfig(packConfig("com.example.good"));
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/knowledge/rematerialize", "{}");
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.json, { ok: true, mode: "pack", packId: "com.example.good" });
    const index = JSON.parse(
      readFileSync(join(dataRoot, "knowledge", "index.json"), "utf-8"),
    ) as { registry_version: string };
    assert.equal(index.registry_version, "com.example.good@1.0.0");
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("POST /knowledge/rematerialize reports the official fallback for a broken pack", async () => {
  installPack("com.example.broken", "{ broken json");
  writeConfig(packConfig("com.example.broken"));
  const errors: string[] = [];
  const originalError = console.error;
  console.error = (...args: unknown[]) => { errors.push(args.map(String).join(" ")); };
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/knowledge/rematerialize", "{}");
    assert.equal(res.statusCode, 200);
    const body = res.json as { ok: boolean; mode: string; fallbackReason?: string };
    assert.equal(body.ok, true);
    assert.equal(body.mode, "official");
    assert.ok(typeof body.fallbackReason === "string" && body.fallbackReason.length > 0);
    const index = JSON.parse(
      readFileSync(join(dataRoot, "knowledge", "index.json"), "utf-8"),
    ) as { registry_version: string };
    assert.equal(index.registry_version, loadKnowledgeRegistry().registry_version);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    console.error = originalError;
  }
});
