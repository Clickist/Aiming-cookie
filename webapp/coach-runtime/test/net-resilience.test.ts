// net-resilience 测试（2026-10-08 "涛"案）：韧性 fetch 的注入合同、连接级
// 判定、救援/粘性/节流行为与 [netprobe] 诊断行格式。依赖全部注入（假
// primary/forced4/dns/tcp/日志/时钟），单测零真实网络。
import assert from "node:assert/strict";
import test from "node:test";

const { createResilientFetch, forced4FetchDefault, wrapModelsWithResilientFetch, isConnectionLevelError } =
  await import("../src/net-resilience.ts");
const { classifyCoachFailureCode, isRetryableFailureCode } = await import("../src/turn.ts");

// 测试辅助：让 emit 的后台微任务链全部 settle。
async function settle(times = 6): Promise<void> {
  for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0));
}

const BUN_ERR = new Error("Unable to connect. Is the computer able to access the url?");
(BUN_ERR as Error & { code?: string }).code = "ConnectionRefused";
const SDK_ERR = new Error("Connection error.");

function okResponse(body = "ok", status = 200): Response {
  return new Response(body, { status, headers: { "content-type": "text/plain" } });
}

interface Harness {
  fetch: ReturnType<typeof createResilientFetch>;
  lines: string[];
  primaryCalls: number;
  forced4Calls: number;
  setClock(ms: number): void;
}

function makeHarness(opts?: {
  primaryImpl?: (input: string | URL, init?: RequestInit) => Promise<Response>;
  forced4Impl?: (input: string | URL, init?: RequestInit) => Promise<Response>;
}): Harness {
  const lines: string[] = [];
  let clock = 1_000_000;
  let primaryCalls = 0;
  let forced4Calls = 0;
  const fetch = createResilientFetch({
    primary: async (input, init) => {
      primaryCalls++;
      if (opts?.primaryImpl) return opts.primaryImpl(input, init);
      throw BUN_ERR;
    },
    forced4: async (input, init) => {
      forced4Calls++;
      if (opts?.forced4Impl) return opts.forced4Impl(input, init);
      return okResponse("rescued");
    },
    dns4: async () => ["203.0.113.10"],
    dns6: async () => [],
    tcpProbe: async () => "open",
    writeLog: (line) => lines.push(line),
    now: () => clock,
  });
  return {
    fetch,
    lines,
    get primaryCalls() { return primaryCalls; },
    get forced4Calls() { return forced4Calls; },
    setClock(ms: number) { clock = ms; },
  };
}

// ── A. 分类层（turn.ts 正则补漏）───────────────────────────────────────────

test("classify: OpenAI SDK 'Connection error.' maps to network_transient and is retryable", () => {
  assert.equal(classifyCoachFailureCode(SDK_ERR), "network_transient");
  assert.equal(isRetryableFailureCode("network_transient"), true);
});

test("classify: Bun native 'Unable to connect.' maps to network_transient", () => {
  assert.equal(classifyCoachFailureCode(BUN_ERR), "network_transient");
});

test("classify: existing families stay intact (order guard)", () => {
  assert.equal(classifyCoachFailureCode(new Error("预扣费额度失败")), "quota_exhausted");
  assert.equal(classifyCoachFailureCode(new Error("invalid api key")), "provider_auth_invalid");
  assert.equal(classifyCoachFailureCode(new Error("HTTP 429")), "service_overloaded");
  assert.equal(classifyCoachFailureCode(new Error("fetch failed")), "network_transient");
  assert.equal(classifyCoachFailureCode(new Error("Failed to append session entry x: EBUSY: resource busy")), "local_storage_busy");
  assert.equal(classifyCoachFailureCode(new Error("something exploded")), "turn_failed");
});

test("isConnectionLevelError: codes and message families, non-connection excluded", () => {
  assert.equal(isConnectionLevelError(BUN_ERR), true);
  assert.equal(isConnectionLevelError(SDK_ERR), true);
  assert.equal(isConnectionLevelError(new Error("ECONNREFUSED 1.2.3.4:443")), true);
  const tlsErr = new Error("unable to verify the first certificate");
  assert.equal(isConnectionLevelError(tlsErr), false);
  assert.equal(isConnectionLevelError(new Error("quota exceeded")), false);
});

// ── B. coachFetch 行为 ─────────────────────────────────────────────────────

test("primary success passes through with zero netprobe lines", async () => {
  const h = makeHarness({ primaryImpl: async () => okResponse("direct") });
  const res = await h.fetch("https://api.example.com/v1/x");
  assert.equal(res.status, 200);
  await settle();
  assert.equal(h.lines.length, 0);
  assert.equal(h.forced4Calls, 0);
});

test("connection-level failure + forced4 success: rescued, single parseable netprobe line", async () => {
  const h = makeHarness();
  const res = await h.fetch("https://api.example.com/v1/chat?secret=1", { method: "POST", body: '{"x":1}' });
  assert.equal(await res.text(), "rescued");
  await settle();
  assert.equal(h.lines.length, 1);
  assert.match(h.lines[0], /^\d{4}-\d{2}-\d{2}T.* \[netprobe\] /);
  const record = JSON.parse(h.lines[0].split("] ")[1]);
  assert.equal(record.phase, "rescue_ok");
  assert.equal(record.via, "forced4");
  assert.equal(record.v, 1);
  assert.equal(record.host, "api.example.com");
  assert.equal(record.port, 443);
  assert.ok(Array.isArray(record.dns4));
  assert.equal(record.tcp4, "open");
  assert.equal(record.tcp6, "skipped");
  assert.equal(record.forced4, "200");
  for (const v of Object.values(record.proxy_env)) assert.equal(typeof v, "boolean");
  const line = h.lines[0];
  assert.ok(!line.includes("secret=1"), "must not leak URL query");
  assert.ok(!line.toLowerCase().includes("authorization"), "must not leak headers");
});

test("non-connection error rethrows without rescue or netprobe", async () => {
  const boom = new TypeError("bad input shape");
  const h = makeHarness({ primaryImpl: async () => { throw boom; } });
  await assert.rejects(h.fetch("https://api.example.com/v1/x"), (e) => e === boom);
  await settle();
  assert.equal(h.lines.length, 0);
  assert.equal(h.forced4Calls, 0);
});

test("double death rethrows the ORIGINAL primary error object (identity)", async () => {
  const h = makeHarness({ forced4Impl: async () => { throw new Error("forced refused"); } });
  let caught: unknown;
  try { await h.fetch("https://api.example.com/v1/x"); } catch (e) { caught = e; }
  assert.equal(caught, BUN_ERR);
  await settle();
  assert.equal(h.lines.length, 1);
  const record = JSON.parse(h.lines[0].split("] ")[1]);
  assert.equal(record.phase, "rescue_fail");
});

test("HTTP 500 response (headers received) never triggers rescue", async () => {
  const h = makeHarness({ primaryImpl: async () => okResponse("err", 500) });
  const res = await h.fetch("https://api.example.com/v1/x");
  assert.equal(res.status, 500);
  await settle();
  assert.equal(h.lines.length, 0);
  assert.equal(h.forced4Calls, 0);
});

test("aborted signal skips rescue", async () => {
  const h = makeHarness();
  const ac = new AbortController();
  ac.abort();
  await assert.rejects(h.fetch("https://api.example.com/v1/x", { signal: ac.signal }));
  await settle();
  assert.equal(h.forced4Calls, 0);
  assert.equal(h.lines.length, 0);
});

test("stream body skips rescue (pi owns mid-stream interruptions)", async () => {
  const h = makeHarness();
  const streamBody = new ReadableStream({ start(c) { c.enqueue(new Uint8Array([1])); c.close(); } });
  await assert.rejects(h.fetch("https://api.example.com/v1/x", { body: streamBody as unknown as string, method: "POST" }));
  await settle();
  assert.equal(h.forced4Calls, 0);
});

test("sticky window: within TTL forced4 is used directly, after TTL back to primary", async () => {
  const h = makeHarness();
  await h.fetch("https://api.example.com/v1/a"); // rescue_ok → sticky on
  await settle();
  assert.equal(h.forced4Calls, 1);
  h.setClock(1_000_000 + 60_000); // +1min，TTL 10min 内
  const res = await h.fetch("https://api.example.com/v1/b");
  assert.equal(await res.text(), "rescued");
  assert.equal(h.primaryCalls, 1, "primary must be skipped during sticky window");
  assert.equal(h.forced4Calls, 2);
  await settle();
  const stickyLine = h.lines.map((l) => JSON.parse(l.split("] ")[1])).find((r) => r.phase === "sticky");
  assert.ok(stickyLine, "sticky hop logs a netprobe line");
  h.setClock(1_000_000 + 11 * 60_000); // TTL 过期
  await h.fetch("https://api.example.com/v1/c");
  assert.equal(h.primaryCalls, 2, "after TTL primary is retried again");
});

test("netprobe throttled to one line per host per 30s", async () => {
  const h = makeHarness({ forced4Impl: async () => { throw new Error("forced refused"); } });
  await assert.rejects(h.fetch("https://api.example.com/v1/a"));
  h.setClock(1_000_000 + 10_000);
  await assert.rejects(h.fetch("https://api.example.com/v1/b"));
  await settle();
  assert.equal(h.lines.length, 1, "second failure within 30s must not re-probe");
  h.setClock(1_000_000 + 31_000);
  await assert.rejects(h.fetch("https://api.example.com/v1/c"));
  await settle();
  assert.equal(h.lines.length, 2);
});

test("wrapModelsWithResilientFetch injects fetch but never overrides explicit one", async () => {
  const calls: Array<Record<string, unknown> | undefined> = [];
  const models = {
    streamSimple: (model: unknown, ctx: unknown, options?: Record<string, unknown>) => {
      calls.push(options);
      return "streamed";
    },
  };
  const wrapped = wrapModelsWithResilientFetch(models);
  const out1 = (wrapped.streamSimple as (m: unknown, c: unknown, o?: unknown) => unknown)({}, {});
  assert.equal(out1, "streamed");
  assert.equal(typeof (calls[0] as { fetch?: unknown })?.fetch, "function");
  const explicit = () => {};
  (wrapped.streamSimple as (m: unknown, c: unknown, o?: unknown) => unknown)({}, {}, { fetch: explicit });
  assert.equal((calls[1] as { fetch?: unknown })?.fetch, explicit);
  // 其余方法透传
  assert.equal(wrapped.other, undefined);
});

// ── C. forced4 真传输（loopback，无外网）────────────────────────────────────

test("forced4FetchDefault: loopback http via resolved IPv4 keeps Host header and streams body", async () => {
  const http = await import("node:http");
  let seenHost = "";
  const server = http.createServer((req, res) => {
    seenHost = String(req.headers.host);
    res.writeHead(200, { "content-type": "text/plain" });
    res.end("loopback-ok");
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const port = (server.address() as { port: number }).port;
  try {
    const res = await forced4FetchDefault(`http://localhost:${port}/v1/x?a=1`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: '{"ping":1}',
    });
    assert.equal(res.status, 200);
    assert.equal(await res.text(), "loopback-ok");
    assert.equal(seenHost, `localhost:${port}`);
  } finally {
    server.close();
  }
});
