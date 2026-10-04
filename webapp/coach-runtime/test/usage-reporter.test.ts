// usage-reporter：真值回声心跳的纯逻辑与推送链路。
// 网络全部走 fetch stub（member-auth.test.ts 同款）；DATA_ROOT 隔离 provider 档。
import assert from "node:assert/strict";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const testRoot = join(process.env.TEMP ?? process.env.TMP ?? ".", `ac-usage-reporter-${process.pid}-${Date.now()}`);
process.env.DATA_ROOT = testRoot;
process.env.AC_ACCOUNTS_BASE_URL = "http://accounts.test";

const reporter = await import("../src/usage-reporter.ts");
const memberAuth = await import("../src/member-auth.ts");
const { saveProviderStore } = await import("../src/provider-store.ts");

const JWT = "eyJhbGciOiJIUzI1NiJ9.member-jwt.signature";

function resetDataRoot(): void {
  if (existsSync(testRoot)) rmSync(testRoot, { recursive: true, force: true });
  mkdirSync(join(testRoot, "config"), { recursive: true });
  reporter.resetUsageReporterForTest();
}

/** fetch stub：按路径给 JSON，记录 url + init（含 headers/body）供断言。 */
function stubFetch(routes: Record<string, { status: number; body: unknown }>): {
  calls: Array<{ url: string; init: RequestInit | undefined }>;
  restore: () => void;
} {
  const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    calls.push({ url, init });
    const hit = routes[new URL(url).pathname];
    return new Response(JSON.stringify(hit ? hit.body : {}), {
      status: hit ? hit.status : 404,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  return { calls, restore: () => { globalThis.fetch = original; } };
}

test("parseMeSubRemaining extracts the sub pool remaining and rejects malformed payloads", () => {
  assert.equal(reporter.parseMeSubRemaining({ pools: { sub: { remaining: 3_750_000, grant: 6_250_000, pct: 60 } } }), 3_750_000);
  assert.equal(reporter.parseMeSubRemaining({ pools: { sub: { remaining: 0, grant: 6_250_000, pct: 0 } } }), 0);
  assert.equal(reporter.parseMeSubRemaining({ pools: { sub: null, boost: null } }), null);
  assert.equal(reporter.parseMeSubRemaining({ pools: {} }), null);
  assert.equal(reporter.parseMeSubRemaining({}), null);
  assert.equal(reporter.parseMeSubRemaining({ pools: { sub: { remaining: "5" } } }), null);
  assert.equal(reporter.parseMeSubRemaining({ pools: { sub: { remaining: -1 } } }), null);
  assert.equal(reporter.parseMeSubRemaining({ pools: { sub: { remaining: Number.NaN } } }), null);
  assert.equal(reporter.parseMeSubRemaining(null), null);
  assert.equal(reporter.parseMeSubRemaining("ok"), null);
});

test("throttled gates on the min interval but always lets the first push through", () => {
  assert.equal(reporter.throttled(0, 1_000, 30_000), false);
  assert.equal(reporter.throttled(1_000, 1_000 + 29_999, 30_000), true);
  assert.equal(reporter.throttled(1_000, 1_000 + 30_000, 30_000), false);
});

test("push skips without a member profile and never touches the network (BYOK included)", async () => {
  resetDataRoot();
  // 只有 BYOK 档：绝不能拿 BYOK 档去查会员余量或推 accounts。
  saveProviderStore({
    schema_version: 2,
    active_id: 1,
    next_id: 2,
    profiles: [{
      id: 1,
      kind: "custom_openai_compatible",
      provider_id: "byok",
      provider_name: "BYOK",
      base_url: "https://provider.example/v1",
      model_id: "m",
      context_window: 32768,
      max_tokens: 4096,
      credential: { type: "api_key", key: "sk-byok" },
    }],
  });
  const stub = stubFetch({});
  try {
    const result = await reporter.pushMemberUsageOnce();
    assert.equal(!result.ok && result.reason, "no_member_profile");
    assert.deepEqual(stub.calls, []);
  } finally {
    stub.restore();
  }
});

test("push reads /api/me with the member JWT and echoes the sub remaining back to accounts", async () => {
  resetDataRoot();
  memberAuth.storeMemberJwt(JWT);
  const stub = stubFetch({
    "/api/me": {
      status: 200,
      body: { member: true, pools: { sub: { remaining: 3_750_000, grant: 6_250_000, pct: 60 }, boost: null } },
    },
    "/api/me/usage-report": { status: 200, body: { ok: true, sub: { remaining: 3_750_000, grant: 6_250_000 } } },
  });
  try {
    const result = await reporter.pushMemberUsageOnce();
    assert.equal(result.ok, true);
    assert.equal(result.ok && result.remaining, 3_750_000);
    const me = stub.calls.find((call) => new URL(call.url).pathname === "/api/me");
    assert.ok(me);
    assert.equal(me.init?.method, undefined); // GET 缺省
    assert.equal(new URL(me.url).host, "accounts.test");
    assert.equal((me.init?.headers as Record<string, string>)?.Authorization, `Bearer ${JWT}`);
    const report = stub.calls.find((call) => new URL(call.url).pathname === "/api/me/usage-report");
    assert.ok(report);
    assert.equal(report.init?.method, "POST");
    assert.equal(new URL(report.url).host, "accounts.test");
    // 回声 = /api/me 展示的服务端真值，客户端不做任何换算/改写
    assert.deepEqual(JSON.parse(String(report.init?.body)), { remaining: 3_750_000 });
    assert.equal((report.init?.headers as Record<string, string>)?.Authorization, `Bearer ${JWT}`);
  } finally {
    stub.restore();
  }
});

test("an immediate second push is throttled into the same dispatch window", async () => {
  resetDataRoot();
  memberAuth.storeMemberJwt(JWT);
  const stub = stubFetch({
    "/api/me": { status: 200, body: { member: true, pools: { sub: { remaining: 3_750_000, grant: 6_250_000, pct: 60 }, boost: null } } },
    "/api/me/usage-report": { status: 200, body: { ok: true } },
  });
  try {
    const first = await reporter.pushMemberUsageOnce();
    assert.equal(first.ok, true);
    const second = await reporter.pushMemberUsageOnce();
    assert.equal(!second.ok && second.reason, "throttled");
    assert.equal(stub.calls.length, 2); // 一条 /api/me 读取 + 一条上报，没有重复
  } finally {
    stub.restore();
  }
});

test("missing sub pool reports snapshot_unavailable and never calls usage-report", async () => {
  resetDataRoot();
  memberAuth.storeMemberJwt(JWT);
  const stub = stubFetch({
    "/api/me": { status: 200, body: { member: true, pools: { sub: null, boost: null } } },
  });
  try {
    const result = await reporter.pushMemberUsageOnce();
    assert.equal(!result.ok && result.reason, "snapshot_unavailable");
    assert.deepEqual(
      stub.calls.map((call) => new URL(call.url).pathname),
      ["/api/me"],
    );
  } finally {
    stub.restore();
  }
});

test("/api/me failure reports snapshot_unavailable and never calls usage-report", async () => {
  resetDataRoot();
  memberAuth.storeMemberJwt(JWT);
  const stub = stubFetch({
    "/api/me": { status: 401, body: { error: { code: "jwt_expired", message: "expired" } } },
  });
  try {
    const result = await reporter.pushMemberUsageOnce();
    assert.equal(!result.ok && result.reason, "snapshot_unavailable");
    assert.deepEqual(
      stub.calls.map((call) => new URL(call.url).pathname),
      ["/api/me"],
    );
  } finally {
    stub.restore();
  }
});

test("accounts rejecting the push reports push_failed without throwing", async () => {
  resetDataRoot();
  memberAuth.storeMemberJwt(JWT);
  const stub = stubFetch({
    "/api/me": { status: 200, body: { member: true, pools: { sub: { remaining: 3_750_000, grant: 6_250_000, pct: 60 }, boost: null } } },
    "/api/me/usage-report": { status: 401, body: { error: { code: "jwt_expired", message: "expired" } } },
  });
  try {
    const result = await reporter.pushMemberUsageOnce();
    assert.equal(!result.ok && result.reason, "push_failed");
  } finally {
    stub.restore();
  }
});
