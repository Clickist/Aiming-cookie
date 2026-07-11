import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";

import { createProxyStreamFn } from "../src/proxy-stream.ts";

function makeModel() {
  return {
    id: "fixture-model",
    name: "fixture model",
    api: "openai-responses",
    provider: "aiming-cookie-proxy-fixture",
    baseUrl: "http://127.0.0.1/fixture",
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 8192,
    maxTokens: 2048,
  };
}

async function withServer(
  handler: Parameters<typeof createServer>[0],
  run: (endpoint: string) => Promise<void>,
) {
  const server = createServer(handler);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  try {
    await run(`http://127.0.0.1:${address.port}/fixture`);
  } finally {
    await new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  }
}

async function collect(endpoint: string, signal?: AbortSignal) {
  const streamFn = createProxyStreamFn({ endpoint, runId: "proxy-run-1" });
  const stream = await streamFn(makeModel(), { messages: [], tools: [] }, { signal });
  const events: Array<Record<string, unknown>> = [];
  for await (const event of stream as AsyncIterable<Record<string, unknown>>) events.push(event);
  return events;
}

test("proxy adapter maps NDJSON text and usage into a Pi assistant stream", async () => {
  await withServer((request, response) => {
    assert.equal(request.method, "POST");
    assert.equal(request.headers["content-type"], "application/json");
    assert.equal(request.headers.accept, "application/x-ndjson");
    assert.equal(request.headers.authorization, undefined);
    assert.equal(request.headers["x-api-key"], undefined);
    response.writeHead(200, { "content-type": "application/x-ndjson" });
    response.end('{"type":"start"}\n{"type":"text_delta","delta":"hello"}\n{"type":"done","stop_reason":"stop","usage":{"input":10,"output":4,"total_tokens":14}}\n');
  }, async (endpoint) => {
    const events = await collect(endpoint);
    const delta = events.find((event) => event.type === "text_delta");
    const done = events.find((event) => event.type === "done");
    assert.equal(delta?.delta, "hello");
    assert.equal((done?.message as { content: Array<{ text: string }>; usage: { input: number; output: number; totalTokens: number } }).content[0]?.text, "hello");
    assert.deepEqual((done?.message as { usage: unknown }).usage, {
      input: 10,
      output: 4,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 14,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    });
  });
});

test("proxy adapter maps one tool call into Pi toolUse without argument loss", async () => {
  await withServer((_request, response) => {
    response.writeHead(200, { "content-type": "application/x-ndjson" });
    response.end('{"type":"start"}\n{"type":"tool_call","id":"tool-1","name":"get_analysis_summary","arguments":{"analysis_id":"analysis-fixture-1","nested":{"keep":true}}}\n{"type":"done","stop_reason":"toolUse","usage":{"input":10,"output":4,"total_tokens":14}}\n');
  }, async (endpoint) => {
    const events = await collect(endpoint);
    const done = events.find((event) => event.type === "done") as { message: { stopReason: string; content: Array<{ arguments: unknown }> } };
    assert.equal(done.message.stopReason, "toolUse");
    assert.deepEqual(done.message.content[0]?.arguments, { analysis_id: "analysis-fixture-1", nested: { keep: true } });
  });
});

test("proxy adapter aborts the single fetch and emits proxy_aborted", async () => {
  let requests = 0;
  let markRequestReceived: (() => void) | undefined;
  const requestReceived = new Promise<void>((resolve) => {
    markRequestReceived = resolve;
  });
  await withServer((_request, _response) => {
    requests += 1;
    markRequestReceived?.();
  }, async (endpoint) => {
    const controller = new AbortController();
    const streamFn = createProxyStreamFn({ endpoint, runId: "proxy-run-abort" });
    const stream = await streamFn(makeModel(), { messages: [], tools: [] }, { signal: controller.signal });
    await requestReceived;
    controller.abort();
    const events: Array<Record<string, unknown>> = [];
    for await (const event of stream as AsyncIterable<Record<string, unknown>>) events.push(event);
    const error = events.find((event) => event.type === "error") as { error: { errorMessage?: string } };
    assert.equal(error.error.errorMessage, "proxy_aborted");
    assert.equal(requests, 1);
  });
});

test("proxy adapter performs zero retries after HTTP failure", async () => {
  let requests = 0;
  await withServer((_request, response) => {
    requests += 1;
    response.writeHead(502, { "content-type": "text/plain" });
    response.end("fixture failure");
  }, async (endpoint) => {
    const events = await collect(endpoint);
    const error = events.find((event) => event.type === "error") as { error: { errorMessage?: string } };
    assert.equal(error.error.errorMessage, "proxy_http_error");
    assert.equal(requests, 1);
  });
});

test("proxy adapter rejects malformed or unterminated NDJSON with proxy_protocol_error", async () => {
  for (const responseBody of ['{"type":"start"}\nnot-json\n', '{"type":"start"}\n{"type":"text_delta","delta":"no terminal"}\n']) {
    await withServer((_request, response) => {
      response.writeHead(200, { "content-type": "application/x-ndjson" });
      response.end(responseBody);
    }, async (endpoint) => {
      const events = await collect(endpoint);
      const error = events.find((event) => event.type === "error") as { error: { errorMessage?: string } };
      assert.equal(error.error.errorMessage, "proxy_protocol_error");
    });
  }
});
