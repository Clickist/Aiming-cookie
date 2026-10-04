import assert from "node:assert/strict";
import { test } from "node:test";

import type { MemberMe, MemberStatusResponse } from "@/lib/types";
import { reduceMemberStatus } from "@/lib/member-state";

const goodMe = {
  user: { id: "u1", email: "user@example.com", name: null },
  member: true,
  plan: "standard",
  status: "active",
  cancel_at_period_end: false,
  period_start: null,
  period_end: null,
  dunning: false,
  pools: { sub: null, boost: null },
  current_pool: null,
  boost_buyable: false,
  server_time: "2026-10-03T00:00:00Z",
} satisfies MemberMe;

const signedIn: MemberStatusResponse = {
  ok: true,
  logged_in: true,
  profile_id: 7,
  active_profile_id: 7,
  me: goodMe,
};
const unavailable: MemberStatusResponse = {
  ok: false,
  logged_in: true,
  code: "unavailable",
  message: "账号服务暂时不可达。",
};
const signedOut: MemberStatusResponse = {
  ok: false,
  logged_in: false,
  code: "unauthorized",
  message: "尚未登录 Aiming Cookie。",
};

test("登录成功：换新 me（reduceMemberStatus 只裁决，不碰缓存）", () => {
  const action = reduceMemberStatus(null, signedIn);
  assert.deepEqual(action, { kind: "signed-in", me: goodMe });
});

test("账号服务不可达：保留上次值——「未知」绝不渲染成「未登录」（1003 掉登录反馈）", () => {
  const kept = reduceMemberStatus(goodMe, unavailable);
  assert.deepEqual(kept, { kind: "unavailable", keep: goodMe });
  // 冷启动无缓存：keep=null，UI 侧显示不可达/加载，而不是清场后的未登录。
  const cold = reduceMemberStatus(null, unavailable);
  assert.deepEqual(cold, { kind: "unavailable", keep: null });
});

test("服务端确定未登录（unauthorized）：才允许清场", () => {
  const action = reduceMemberStatus(goodMe, signedOut);
  assert.deepEqual(action, { kind: "signed-out" });
});
