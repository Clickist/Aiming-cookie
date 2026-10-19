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

/** 账号服务 stub：按路径给 JSON，记录调用次数与完整 URL（供基址断言）。 */
function stubAccounts(routes: Record<string, { status: number; body: unknown }>): {
  calls: string[];
  urls: string[];
  restore: () => void;
} {
  const calls: string[] = [];
  const urls: string[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url).pathname;
    calls.push(path);
    urls.push(url);
    const hit = routes[path];
    const status = hit ? hit.status : 404;
    const body = hit ? hit.body : { error: { code: "not_found", message: "no route" } };
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  return { calls, urls, restore: () => { globalThis.fetch = original; } };
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

test("fetchMemberMe passes trial.provisioning through loosely (§3.4 透传补齐)", async () => {
  // accounts Worker 的 trial 视图带 provisioning（在途试用发货单）；sidecar 不
  // 透传则前端永远看不到。宽松口径：true/false 原样、非布尔/缺失 = undefined
  // （fail-open 不注入，前端按 undefined 容错），trial 整体不活跃时恒 null。
  const meBody = (provisioning: unknown) => ({
    user: { id: "u1", email: "u1@example.com", name: null },
    member: false,
    plan: null,
    status: "none",
    cancel_at_period_end: false,
    period_start: null,
    period_end: null,
    dunning: false,
    pools: { sub: null, boost: null },
    current_pool: null,
    boost_buyable: false,
    server_time: "2026-10-11T00:00:00.000Z",
    trial: {
      active: true,
      analyses_remaining: 3,
      questions_remaining: 5,
      verified_at: null,
      ...(provisioning === undefined ? {} : { provisioning }),
    },
  });
  const cases: Array<{ input: unknown; expected: boolean | undefined }> = [
    { input: true, expected: true },
    { input: false, expected: false },
    { input: "yes", expected: undefined },
    { input: undefined, expected: undefined },
  ];
  for (const { input, expected } of cases) {
    resetDataRoot();
    memberAuth.storeMemberJwt(JWT);
    const stub = stubAccounts({ "/api/me": { status: 200, body: meBody(input) } });
    try {
      const result = await memberAuth.fetchMemberMe();
      assert.equal(result.ok, true);
      const trial = result.ok ? result.me.trial : null;
      assert.ok(trial && trial.active === true);
      assert.equal(trial?.provisioning, expected, `provisioning=${String(input)} must map to ${String(expected)}`);
      // JSON 序列化合同：undefined 不得出现在响应体里（字段缺失而非 null）。
      const serialized = JSON.stringify(result.ok ? result.me.trial : null) ?? "";
      assert.equal(serialized.includes('"provisioning"'), expected !== undefined);
    } finally {
      stub.restore();
    }
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

test("testMemberConnection must hit the member gateway, never the relay base", async () => {
  resetDataRoot();
  memberAuth.storeMemberJwt(JWT);
  // 模拟未注入的打包构建：AC_MEMBER_GATEWAY_BASE_URL 未注入、AC_RELAY_BASE_URL 被
  // --define 内联为中转站直连地址。历史上 relayBaseUrl() 兜底让连通测试把网关
  // JWT 打到中转站、恒 401 误报「凭证已失效」——普通测试环境两个 base 同值测不
  // 出分歧，故此处改写 env 并锁完整 URL（fetch 已被 stub 替换，无真实网络）。
  // 期望基址 = 网关占位回落（provider-models.ts 常量）：真实域名由打包构建读
  // infra-urls.local.txt 经 --define 注入，不入库；锁占位基址同时证明没打 relay 内联值。
  const savedGateway = process.env.AC_MEMBER_GATEWAY_BASE_URL;
  const savedRelay = process.env.AC_RELAY_BASE_URL;
  delete process.env.AC_MEMBER_GATEWAY_BASE_URL;
  process.env.AC_RELAY_BASE_URL = "http://relay-station-direct.example";
  const stub = stubAccounts({ "/member/v1/models": { status: 200, body: { data: [] } } });
  try {
    const result = await memberAuth.testMemberConnection();
    assert.equal(result.ok, true);
    assert.ok(
      stub.urls[0]!.startsWith("https://member-gateway.example.invalid:8443/member/v1/"),
      `member gateway base expected, got: ${stub.urls[0]}`,
    );
  } finally {
    stub.restore();
    if (savedGateway === undefined) delete process.env.AC_MEMBER_GATEWAY_BASE_URL;
    else process.env.AC_MEMBER_GATEWAY_BASE_URL = savedGateway;
    if (savedRelay === undefined) delete process.env.AC_RELAY_BASE_URL;
    else process.env.AC_RELAY_BASE_URL = savedRelay;
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

// ── 轮询兜底（1009 野外案：deep-link 被浏览器吞时等待页的自救通道）─────────────

test("poll returns pending while the site login has not claimed yet, and keeps the pending dc", async () => {
  resetDataRoot();
  writeMemberState({ device_code: "dc-poll", login_url: "u", started_at: Date.now() });
  const stub = stubAccounts({
    "/api/device/poll": { status: 200, body: { status: "pending" } },
  });
  try {
    const result = await memberAuth.pollMemberTicket();
    assert.deepEqual(result, { outcome: "pending" });
    assert.equal(memberAuth.storedMemberJwt(), null, "pending 不得落 JWT");
    assert.equal(memberAuth.loadMemberState().pending?.device_code, "dc-poll", "pending 保留待用 dc");
  } finally {
    stub.restore();
  }
});

test("poll on claimed stores the JWT, clears the pending dc, and never throws", async () => {
  resetDataRoot();
  writeMemberState({ device_code: "dc-poll", login_url: "u", started_at: Date.now() });
  const stub = stubAccounts({
    "/api/device/poll": {
      status: 200,
      body: { jwt: JWT, user: { id: "u1", email: "u***@gmail.com", name: "U" }, member: true },
    },
  });
  try {
    const result = await memberAuth.pollMemberTicket();
    assert.equal(result.outcome, "done");
    assert.equal(result.outcome === "done" && result.member, true);
    assert.equal(memberAuth.storedMemberJwt(), JWT);
    assert.equal(memberAuth.loadMemberState().pending, null);
  } finally {
    stub.restore();
  }
});

test("poll maps 404/410 to stopped outcomes so the wizard can stop the loop", async () => {
  resetDataRoot();
  writeMemberState({ device_code: "dc-poll", login_url: "u", started_at: Date.now() });
  const stub = stubAccounts({
    "/api/device/poll": { status: 404, body: { error: "invalid device_code" } },
  });
  try {
    const result = await memberAuth.pollMemberTicket();
    assert.equal(result.outcome, "stopped");
    assert.equal(result.outcome === "stopped" && result.code, "device_code_invalid");
    // 1018：终态即清 pending——全局轮询兜底常驻后，死 dc 不清会让每次轮询都出网。
    assert.equal(memberAuth.loadMemberState().pending, null, "404 终态后 pending 必须清掉");
  } finally {
    stub.restore();
  }
  // pending 已被上一段清掉：后续轮询本地短路 no_pending，不再出网打服务端。
  const stubIdle = stubAccounts({});
  try {
    const idle = await memberAuth.pollMemberTicket();
    assert.equal(idle.outcome === "stopped" && idle.code, "no_pending");
    assert.equal(stubIdle.calls.length, 0, "no_pending 必须本地短路，零网络");
  } finally {
    stubIdle.restore();
  }

  // 410 同样清 pending：重置状态后单独验证。
  writeMemberState({ device_code: "dc-poll", login_url: "u", started_at: Date.now() });
  const stub410 = stubAccounts({
    "/api/device/poll": { status: 410, body: { error: "ticket expired" } },
  });
  try {
    const result = await memberAuth.pollMemberTicket();
    assert.equal(result.outcome, "stopped");
    assert.equal(result.outcome === "stopped" && result.code, "ticket_expired");
    assert.equal(memberAuth.loadMemberState().pending, null, "410 终态后 pending 必须清掉");
  } finally {
    stub410.restore();
  }
});

test("poll keeps polling on network errors (transient flakiness is the case this saves)", async () => {
  resetDataRoot();
  writeMemberState({ device_code: "dc-poll", login_url: "u", started_at: Date.now() });
  // stub 不挂 /api/device/poll → fetch 打到不可达基址 → requestJson 抛错
  const result = await memberAuth.pollMemberTicket();
  assert.equal(result.outcome, "error");
  assert.equal(result.outcome === "error" && result.code, "network_error");
});

test("poll with no pending dc stops immediately without touching the network", async () => {
  resetDataRoot();
  const result = await memberAuth.pollMemberTicket();
  assert.equal(result.outcome, "stopped");
  assert.equal(result.outcome === "stopped" && result.code, "no_pending");
});
