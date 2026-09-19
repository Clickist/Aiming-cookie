import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

// member-auth 的本地状态（member.json）与 provider 档都落在 DATA_ROOT 下，
// 每个用例换一个隔离 root；账号服务基址指向本地 stub。
const testRoot = join(process.env.TEMP ?? process.env.TMP ?? ".", `ac-member-auth-${process.pid}-${Date.now()}`);
process.env.DATA_ROOT = testRoot;
process.env.AC_ACCOUNTS_BASE_URL = "http://127.0.0.1:1"; // 默认不可达，用例各自改
process.env.AC_MEMBER_GATEWAY_BASE_URL = "http://127.0.0.1:1/v1";

const memberAuth = await import("../src/member-auth.ts");
const { loadProviderStore } = await import("../src/provider-store.ts");

function resetDataRoot(): void {
  if (existsSync(testRoot)) rmSync(testRoot, { recursive: true, force: true });
  mkdirSync(join(testRoot, "config"), { recursive: true });
}

function writeMemberState(pending: { device_code: string; login_url: string; started_at: number } | null, consumed: string[] = []): void {
  writeFileSync(join(testRoot, "config", "member.json"), JSON.stringify({
    schema_version: 1,
    pending,
    consumed_tickets: consumed,
  }), "utf8");
}

const JWT = "eyJhbGciOiJIUzI1NiJ9.stub.signature";

/** 账号服务 stub：按路径给 JSON，记录调用次数。 */
function stubAccounts(routes: Record<string, { status: number; body: unknown }>): {
  calls: string[];
  restore: () => void;
} {
  const calls: string[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url).pathname;
    calls.push(path);
    const hit = routes[path];
    const status = hit ? hit.status : 404;
    const body = hit ? hit.body : { error: { code: "not_found", message: "no route" } };
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  return { calls, restore: () => { globalThis.fetch = original; } };
}

const START_BODY = {
  device_code: "dc-abc",
  login_url: "https://accounts.example/login?dc=dc-abc",
  expires_in: 600,
};

test("startMemberLogin stores the pending device_code used by the dc binding check", async () => {
  resetDataRoot();
  const stub = stubAccounts({ "/api/device/start": { status: 200, body: START_BODY } });
  try {
    const result = await memberAuth.startMemberLogin();
    assert.equal(result.ok, true);
    assert.equal(result.ok && result.device_code, "dc-abc");
    const state = memberAuth.loadMemberState();
    assert.equal(state.pending?.device_code, "dc-abc");
  } finally {
    stub.restore();
  }
});

test("exchange rejects a mismatched device_code without touching the network", async () => {
  resetDataRoot();
  writeMemberState({ device_code: "dc-local", login_url: "u", started_at: Date.now() });
  const stub = stubAccounts({});
  try {
    const result = await memberAuth.exchangeMemberTicket({ ticket: "t".repeat(64), dc: "dc-other" });
    assert.equal(result.ok, false);
    assert.equal(!result.ok && result.code, "dc_mismatch");
    // §3.3-4：dc 不匹配时不打服务端。
    assert.deepEqual(stub.calls, []);
    // 本地 pending dc 保留（不因一条坏链接被清掉）。
    assert.equal(memberAuth.loadMemberState().pending?.device_code, "dc-local");
  } finally {
    stub.restore();
  }
});

test("exchange without a ticket pair is a no-op (secondary deep link must not error)", async () => {
  resetDataRoot();
  writeMemberState({ device_code: "dc-local", login_url: "u", started_at: Date.now() });
  const stub = stubAccounts({});
  try {
    for (const input of [{ ticket: null, dc: "dc-local" }, { ticket: "t", dc: null }, { ticket: null, dc: null }]) {
      const result = await memberAuth.exchangeMemberTicket(input);
      assert.equal(!result.ok && result.code, "no_ticket");
    }
    assert.deepEqual(stub.calls, []);
  } finally {
    stub.restore();
  }
});

test("exchange stores the JWT in the relay profile and clears the pending device_code", async () => {
  resetDataRoot();
  writeMemberState({ device_code: "dc-abc", login_url: "u", started_at: Date.now() });
  const stub = stubAccounts({
    "/api/device/exchange": {
      status: 200,
      body: { jwt: JWT, user: { id: "u1", email: "u***@gmail.com", name: "U" }, member: true },
    },
  });
  try {
    const ticket = "a".repeat(64);
    const result = await memberAuth.exchangeMemberTicket({ ticket, dc: "dc-abc" });
    assert.equal(result.ok, true);
    assert.equal(result.ok && result.member, true);
    assert.equal(memberAuth.storedMemberJwt(), JWT);
    const state = memberAuth.loadMemberState();
    assert.equal(state.pending, null);
    assert.ok(state.consumed_tickets.includes(ticket));
    // 第二轮同 ticket（single-instance 转发 / 浏览器重试）不重复打服务端。
    const replay = await memberAuth.exchangeMemberTicket({ ticket, dc: "dc-abc" });
    assert.equal(!replay.ok && replay.code, "already_consumed");
    assert.equal(stub.calls.filter((path) => path === "/api/device/exchange").length, 1);
  } finally {
    stub.restore();
  }
});

test("exchange maps 404/401/410 to structured codes and never throws", async () => {
  resetDataRoot();
  for (const [status, code] of [[404, "device_code_invalid"], [401, "invalid_ticket"], [410, "ticket_expired"]] as const) {
    resetDataRoot();
    writeMemberState({ device_code: "dc-abc", login_url: "u", started_at: Date.now() });
    const stub = stubAccounts({ "/api/device/exchange": { status, body: { error: { code: "x", message: "y" } } } });
    try {
      const result = await memberAuth.exchangeMemberTicket({ ticket: `${status}`.padEnd(64, "0"), dc: "dc-abc" });
      assert.equal(!result.ok && result.code, code, `status ${status}`);
      assert.equal(memberAuth.storedMemberJwt(), null);
    } finally {
      stub.restore();
    }
  }
});

test("fetchMemberMe reads the frozen /api/me schema and accepts the pct floor", async () => {
  resetDataRoot();
  memberAuth.storeMemberJwt(JWT);
  const stub = stubAccounts({
    "/api/me": {
      status: 200,
      body: {
        user: { id: "u1", email: "u1@example.com", name: null },
        member: true,
        plan: "standard",
        status: "active",
        cancel_at_period_end: false,
        period_start: "2026-09-20T00:00:00.000Z",
        period_end: "2026-10-20T00:00:00.000Z",
        dunning: false,
        pools: {
          sub: { remaining: 3_750_000, grant: 6_250_000, pct: 60 },
          boost: { remaining: 6_250_000, grant: 6_250_000, pct: 100 },
        },
        current_pool: "sub",
        boost_buyable: false,
        server_time: "2026-09-19T09:59:30.000Z",
      },
    },
  });
  try {
    const result = await memberAuth.fetchMemberMe();
    assert.equal(result.ok, true);
    assert.equal(result.ok && result.me.pools.sub?.pct, 60);
    assert.equal(result.ok && result.me.current_pool, "sub");
    assert.equal(result.ok && result.me.boost_buyable, false);
  } finally {
    stub.restore();
  }
});

test("fetchMemberMe degrades on 401 instead of throwing", async () => {
  resetDataRoot();
  memberAuth.storeMemberJwt(JWT);
  const stub = stubAccounts({ "/api/me": { status: 401, body: { error: { code: "jwt_expired", message: "expired" } } } });
  try {
    const result = await memberAuth.fetchMemberMe();
    assert.equal(result.ok, false);
    assert.equal(!result.ok && result.code, "unauthorized");
  } finally {
    stub.restore();
  }
});

test("fetchMemberMe reports no credential as unauthorized without a network call", async () => {
  resetDataRoot();
  const stub = stubAccounts({});
  try {
    const result = await memberAuth.fetchMemberMe();
    assert.equal(!result.ok && result.code, "unauthorized");
    assert.deepEqual(stub.calls, []);
  } finally {
    stub.restore();
  }
});

test("testMemberConnection maps 401 to the re-login branch and 5xx to unreachable", async () => {
  resetDataRoot();
  memberAuth.storeMemberJwt(JWT);
  // 网关 base_url 带 /v1（契约 §0：客户端 AI base_url = .../member，路径透传 /v1/...）。
  for (const [status, code] of [[401, "unauthorized"], [503, "unreachable"]] as const) {
    const stub = stubAccounts({ "/v1/models": { status, body: {} } });
    try {
      const result = await memberAuth.testMemberConnection();
      assert.equal(!result.ok && result.code, code, `status ${status}`);
    } finally {
      stub.restore();
    }
  }
});

test("logout clears the member credential and promotes a configured BYOK profile", async () => {
  resetDataRoot();
  // 会员档 + 一个 BYOK 档：会员档在前（首个档案 → 自动成为当前档）。
  const memberId = memberAuth.storeMemberJwt(JWT);
  const store = loadProviderStore();
  const byok = {
    id: store.next_id,
    kind: "custom_openai_compatible" as const,
    provider_id: "byok",
    provider_name: "BYOK",
    base_url: "https://provider.example/v1",
    model_id: "m",
    context_window: 32768,
    max_tokens: 4096,
    credential: { type: "api_key" as const, key: "sk-byok" },
  };
  store.profiles.push(byok);
  store.next_id = byok.id + 1;
  (await import("../src/provider-store.ts")).saveProviderStore(store);

  const result = memberAuth.logoutMember();
  assert.equal(result.relay_profile_id, memberId);
  assert.equal(result.fallback_profile_id, byok.id);
  assert.equal(memberAuth.storedMemberJwt(), null);
  // 档本身保留（只是没有凭据）。
  const after = loadProviderStore();
  assert.ok(after.profiles.some((profile) => profile.id === memberId));
});

test("logout with no BYOK reports a null fallback (Coach greys out instead of returning to onboarding)", async () => {
  resetDataRoot();
  memberAuth.storeMemberJwt(JWT);
  const result = memberAuth.logoutMember();
  assert.equal(result.fallback_profile_id, null);
});
