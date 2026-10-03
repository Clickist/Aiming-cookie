import assert from "node:assert/strict";
import { test } from "node:test";

import type { MemberMe } from "@/lib/types";

// readMemberCache 在模块加载期就会读一次缓存：先装 window/localStorage 再动态
// import（node 无 window，不装会走 SSR 空分支测不到持久化路径）。
type LocalStorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};
const store = new Map<string, string>();
(globalThis as unknown as { window: { localStorage: LocalStorageLike } }).window = {
  localStorage: {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
  },
};

const MEMBER_CACHE_KEY = "aiming-cookie.member.cache";

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

test("readMemberCache returns the stored object when the cache shape is intact", async () => {
  const { readMemberCache } = await import("@/lib/member-state");
  store.set(MEMBER_CACHE_KEY, JSON.stringify(goodMe));
  assert.deepEqual(readMemberCache(), goodMe);
});

test("readMemberCache silently discards corrupt caches (missing user / pools, member not boolean)", async () => {
  const { readMemberCache } = await import("@/lib/member-state");
  const cases: unknown[] = [];
  // 缺 user（member.ts:325 的 me.user.email 无守卫）。
  const { user: _user, ...withoutUser } = goodMe;
  cases.push(withoutUser);
  // 缺 pools（member.ts:111 的 me.pools.sub 无守卫）。
  const { pools: _pools, ...withoutPools } = goodMe;
  cases.push(withoutPools);
  // member 非布尔。
  cases.push({ ...goodMe, member: "yes" });
  // user 不是对象 / email 非字符串 / pools 不是对象。
  cases.push({ ...goodMe, user: "u1" });
  cases.push({ ...goodMe, user: { ...goodMe.user, email: 42 } });
  cases.push({ ...goodMe, pools: "oops" });
  for (const broken of cases) {
    store.set(MEMBER_CACHE_KEY, JSON.stringify(broken));
    assert.equal(readMemberCache(), null, JSON.stringify(broken));
  }
});

test("isMemberCacheShape rejects non-object payloads", async () => {
  const { isMemberCacheShape } = await import("@/lib/member-state");
  for (const value of [null, "x", 42, true, undefined]) {
    assert.equal(isMemberCacheShape(value), false, String(value));
  }
});
