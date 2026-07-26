import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { ProviderAuthOperationManager, type PiAuthProvider } from "../src/provider-auth.ts";
import { createSidecarServer } from "../src/sidecar-server.ts";

function request(
  server: http.Server,
  method: string,
  path: string,
  body?: string,
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
            }
          : undefined,
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

test("POST /v0/turn with invalid JSON returns 400", async () => {
  const server = createSidecarServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  try {
    const res = await request(server, "POST", "/v0/turn", "{not-json");
    assert.equal(res.statusCode, 400);
    assert.equal((res.json as { ok: boolean }).ok, false);
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
