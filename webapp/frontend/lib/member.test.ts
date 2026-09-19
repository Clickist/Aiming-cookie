import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MEMBER_COPY,
  MEMBER_DEEP_LINK_MAX_LENGTH,
  POOL_TIER_COLORS,
  bothPoolsEmpty,
  canExchange,
  classifyMemberGatewayError,
  currentPool,
  firstMemberDeepLink,
  formatMemberDate,
  gatewayErrorNotice,
  maskEmail,
  memberChipView,
  parseMemberDeepLink,
  planLabel,
  poolTier,
  subscriptionEnded,
} from "./member";
import type { MemberMe } from "./types";

function me(overrides: Partial<MemberMe> = {}): MemberMe {
  return {
    user: { id: "u1", email: "u***@gmail.com", name: null },
    member: true,
    plan: "standard",
    status: "active",
    cancel_at_period_end: false,
    period_start: "2026-09-20T00:00:00.000Z",
    period_end: "2026-10-20T00:00:00.000Z",
    dunning: false,
    pools: {
      sub: { remaining: 3_875_000, grant: 6_250_000, pct: 62 },
      boost: null,
    },
    current_pool: "sub",
    boost_buyable: false,
    server_time: "2026-09-19T09:59:30.000Z",
    ...overrides,
  };
}

// ── 契约 §3.3 九条校验（deep-link）────────────────────────────────────────

test("parses the frozen deep-link format and keeps only the four scenes", () => {
  const link = parseMemberDeepLink("aimingcookie://auth?scene=login&ticket=" + "a".repeat(64) + "&dc=dc-1");
  assert.ok(link);
  assert.equal(link.scene, "login");
  assert.equal(link.ticket, "a".repeat(64));
  assert.equal(link.dc, "dc-1");

  // §3.3-2：scene 必须是四个枚举值之一，否则按 open 处理（不是忽略）。
  assert.equal(parseMemberDeepLink("aimingcookie://auth?scene=nonsense")?.scene, "open");
  assert.equal(parseMemberDeepLink("aimingcookie://auth")?.scene, "open");

  // item 只用于文案（§3.1）。
  assert.equal(parseMemberDeepLink("aimingcookie://auth?scene=subscribe&item=plus")?.item, "plus");
  assert.equal(parseMemberDeepLink("aimingcookie://auth?scene=subscribe&item=bogus")?.item, null);
});

test("rule 1: only scheme=aimingcookie + host=auth is accepted, everything else is silently ignored", () => {
  assert.equal(parseMemberDeepLink("https://accounts.gearclickist.com/login?dc=x"), null);
  assert.equal(parseMemberDeepLink("aimingcookie://other?scene=login"), null);
  assert.equal(parseMemberDeepLink("aimingcookie://auth.evil.com?scene=login"), null);
  assert.equal(parseMemberDeepLink("not a url"), null);
  assert.equal(parseMemberDeepLink(""), null);
  assert.equal(parseMemberDeepLink(null), null);
  // 长度上限 2048（§3.1）。
  assert.equal(parseMemberDeepLink(`aimingcookie://auth?scene=login&x=${"z".repeat(MEMBER_DEEP_LINK_MAX_LENGTH)}`), null);
});

test("rule 3: ticket and dc must arrive as a pair; a lone ticket degrades to no-ticket", () => {
  const withTicketOnly = parseMemberDeepLink("aimingcookie://auth?scene=login&ticket=" + "a".repeat(64));
  assert.ok(withTicketOnly);
  assert.equal(withTicketOnly.dc, null);
  assert.equal(canExchange(withTicketOnly), false);

  const withDcOnly = parseMemberDeepLink("aimingcookie://auth?scene=login&dc=dc-1");
  assert.equal(withDcOnly?.ticket, null);
  assert.equal(canExchange(withDcOnly), false);

  // 支付成功那次触发没有 ticket（§3.2 触发 2），必须可解析且不可 exchange。
  const subscribe = parseMemberDeepLink("aimingcookie://auth?scene=subscribe&item=standard");
  assert.ok(subscribe);
  assert.equal(canExchange(subscribe), false);
});

test("firstMemberDeepLink skips unparsable entries and takes the first valid one", () => {
  assert.equal(firstMemberDeepLink(null), null);
  assert.equal(firstMemberDeepLink([]), null);
  const link = firstMemberDeepLink(["https://x", "aimingcookie://auth?scene=open"]);
  assert.equal(link?.scene, "open");
});

// ── 余量分档与双池口径 ────────────────────────────────────────────────────

test("pool tiers follow the wireframe cutoffs (green >30%, orange 10-30%, red <10%)", () => {
  assert.equal(poolTier(100), "good");
  assert.equal(poolTier(31), "good");
  assert.equal(poolTier(30), "warn");
  assert.equal(poolTier(10), "warn");
  assert.equal(poolTier(9.9), "low");
  assert.equal(poolTier(0), "low");
  // 分档色是线框冻结值（亮模式）。
  assert.equal(POOL_TIER_COLORS.good, "#16875b");
  assert.equal(POOL_TIER_COLORS.warn, "#e8930c");
  assert.equal(POOL_TIER_COLORS.low, "#c53442");
});

test("currentPool follows the server's current_pool field, never a local guess", () => {
  assert.equal(currentPool(me())?.pct, 62);
  assert.equal(currentPool(me({ current_pool: "boost", pools: { sub: null, boost: { remaining: 5, grant: 10, pct: 50 } } }))?.pct, 50);
  assert.equal(currentPool(me({ current_pool: null })), null);
  assert.equal(currentPool(null), null);
});

test("bothPoolsEmpty only fires when both pools are truly drained (④ trigger)", () => {
  assert.equal(bothPoolsEmpty(me()), false);
  const drained = me({
    member: false,
    status: "expired",
    current_pool: null,
    pools: { sub: { remaining: 0, grant: 6_250_000, pct: 0 }, boost: { remaining: 0, grant: 6_250_000, pct: 0 } },
  });
  assert.equal(bothPoolsEmpty(drained), true);
  // 订阅池空但加油包还有余额 → 不触发 ④（自动换池继续用）。
  const boosterAlive = me({
    member: false,
    status: "expired",
    pools: { sub: { remaining: 0, grant: 6_250_000, pct: 0 }, boost: { remaining: 2_812_500, grant: 6_250_000, pct: 45 } },
  });
  assert.equal(bothPoolsEmpty(boosterAlive), false);
  assert.equal(subscriptionEnded(boosterAlive), true);
});

// ── ②b chip 两态投影 ──────────────────────────────────────────────────────

test("member chip shows plan + current pool percentage and colors by tier", () => {
  assert.deepEqual(memberChipView(me(), null), {
    email: "u***@gmail.com",
    status: "Standard · 余量 62%",
    tone: "default",
    relink: false,
    kind: "member",
  });
  const lowTier = memberChipView(me({ pools: { sub: { remaining: 1, grant: 100, pct: 1 }, boost: null } }), null);
  assert.equal(lowTier.tone, "error");
});

test("member chip switches to the booster line when the subscription ended but the booster lives", () => {
  const view = memberChipView(me({
    member: false,
    status: "expired",
    current_pool: "boost",
    pools: { sub: { remaining: 0, grant: 6_250_000, pct: 0 }, boost: { remaining: 2_812_500, grant: 6_250_000, pct: 45 } },
  }), null);
  assert.equal(view.status, "订阅已到期 · 加油包 45%");
  assert.equal(view.tone, "warn");
  assert.equal(view.relink, false);
});

test("member chip shows the disconnected line and a re-subscribe entry when everything is drained", () => {
  const view = memberChipView(me({
    member: false,
    status: "expired",
    current_pool: null,
    pools: { sub: { remaining: 0, grant: 6_250_000, pct: 0 }, boost: null },
  }), null);
  assert.equal(view.status, MEMBER_COPY.endedChip);
  assert.equal(view.tone, "error");
  assert.equal(view.relink, true);
});

test("BYOK chip shows the selected provider name and never a model name", () => {
  const view = memberChipView(null, { providerName: "DeepSeek 官方", connected: true });
  assert.equal(view.status, "⚙ DeepSeek 官方 · 已连接");
  assert.equal(view.kind, "byok");
  assert.equal(view.email, null);
  assert.doesNotMatch(view.status ?? "", /deepseek-v4|模型|model/i);

  // 未登录的 BYOK：引擎行在，身份行是登录引导（线框 ②b 状态 B1）。
  const guest = memberChipView(null, { providerName: "DeepSeek 官方", connected: true });
  assert.equal(guest.email, null);
  // 什么都没配：④b 右态。
  assert.equal(memberChipView(null, null).status, null);
});

// ── ⑧/⑨ 提示与网关错误码 ─────────────────────────────────────────────────

test("gateway error codes accept both the v2 and v1 spellings (dual-recognition table)", () => {
  assert.equal(classifyMemberGatewayError('{"error":{"type":"quota_exhausted"}}'), "quota_exhausted");
  assert.equal(classifyMemberGatewayError('{"error":{"type":"member_required"}}'), "member_required");
  assert.equal(classifyMemberGatewayError('{"error":{"type":"ac_member_required"}}'), "member_required");
  assert.equal(classifyMemberGatewayError('{"error":{"type":"jwt_expired"}}'), "jwt_expired");
  assert.equal(classifyMemberGatewayError('{"error":{"type":"auth_expired"}}'), "jwt_expired");
  assert.equal(classifyMemberGatewayError("something else"), null);
  assert.equal(classifyMemberGatewayError(null), null);
});

test("gateway notice copy stays inside the zero-commercialization rule", () => {
  for (const code of ["quota_exhausted", "member_required", "jwt_expired"] as const) {
    const notice = gatewayErrorNotice(code);
    // 零商业化：不提价格、不提升级、不提「云教练」。
    assert.doesNotMatch(notice.text, /¥|\d+ 元|升级|套餐价格|云教练/);
  }
});

// ── 文案与格式化 ─────────────────────────────────────────────────────────

test("member copy never says 云教练 and never quotes a price", () => {
  const flat = JSON.stringify(MEMBER_COPY);
  assert.doesNotMatch(flat, /云教练/);
  // ②c 里唯一的金额型文案是加油包固定 ¥10 标价（线框原文），其余不出现价格。
  const withoutBoosterLabel = flat.replace(/加油包 ¥10/g, "");
  assert.doesNotMatch(withoutBoosterLabel, /¥/);
});

test("email masking and date formatting match the wireframe shapes", () => {
  assert.equal(maskEmail("user@gmail.com"), "u***@gmail.com");
  assert.equal(maskEmail("a@b.co"), "a***@b.co");
  assert.equal(maskEmail("no-at-sign"), "no-at-sign");
  assert.equal(formatMemberDate("2026-10-20T08:00:00.000Z"), "10-20");
  assert.equal(formatMemberDate(null), "—");
  assert.equal(planLabel("standard"), "Standard");
  assert.equal(planLabel("plus"), "Plus");
  assert.equal(planLabel(null), "—");
});
