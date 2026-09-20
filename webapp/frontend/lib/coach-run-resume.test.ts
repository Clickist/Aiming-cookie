import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ACTIVE_RUN_STORAGE_KEY,
  activeRunRefForSession,
  clearActiveRunRef,
  pinActiveRunRef,
  readActiveRunPins,
} from "./coach-run-resume";

// 0919 P2 缺陷（发送后刷新 → run 从界面消失、服务端照跑）的在途 run 钉扎。
// 行为契约：受理即落盘、同会话覆盖、终态解除、损坏/不可用存储静默降级。

function fakeStorage(entries: Map<string, string> = new Map()): Storage {
  return {
    length: entries.size,
    clear: () => entries.clear(),
    getItem: (key: string) => (entries.has(key) ? (entries.get(key) as string) : null),
    key: () => null,
    removeItem: (key: string) => void entries.delete(key),
    setItem: (key: string, value: string) => void entries.set(key, value),
  } as Storage;
}

function brokenStorage(): Storage {
  return {
    length: 0,
    clear: () => {},
    getItem: () => null,
    key: () => null,
    removeItem: () => {},
    setItem: () => {
      throw new Error("storage disabled");
    },
  } as Storage;
}

test("在途 run 钉扎：受理落盘、按会话回读、终态解除", () => {
  const store = fakeStorage();
  assert.equal(activeRunRefForSession(store, 173), null);

  pinActiveRunRef(store, 173, "agent_run:abc", 1_700_000_000_000);
  assert.equal(activeRunRefForSession(store, 173), "agent_run:abc");
  assert.deepEqual(readActiveRunPins(store), [
    { sessionId: 173, runRef: "agent_run:abc", pinnedAt: 1_700_000_000_000 },
  ]);

  // 同会话再钉＝覆盖旧值（首条消息重发/重试产生新 run_ref），不堆叠。
  pinActiveRunRef(store, 173, "agent_run:def");
  assert.equal(activeRunRefForSession(store, 173), "agent_run:def");
  assert.equal(readActiveRunPins(store).length, 1);

  // 其他会话不受影响（多会话各钉各的）。
  pinActiveRunRef(store, 174, "agent_run:ghi");
  assert.equal(activeRunRefForSession(store, 174), "agent_run:ghi");
  assert.equal(activeRunRefForSession(store, 173), "agent_run:def");

  // 终态解除只清本会话；清空后键也被移除（不留空数组垃圾）。
  clearActiveRunRef(store, 173);
  assert.equal(activeRunRefForSession(store, 173), null);
  assert.equal(activeRunRefForSession(store, 174), "agent_run:ghi");
  clearActiveRunRef(store, 174);
  assert.equal(store.getItem(ACTIVE_RUN_STORAGE_KEY), null);
});

test("在途 run 钉扎：非法入参不落盘，损坏数据静默降级", () => {
  const store = fakeStorage();
  // 非正会话 id / 非 agent_run 前缀一律拒绝（避免把垃圾写进存储）。
  pinActiveRunRef(store, 0, "agent_run:abc");
  pinActiveRunRef(store, -1, "agent_run:abc");
  pinActiveRunRef(store, 173, "run:abc");
  pinActiveRunRef(store, 173.5, "agent_run:abc");
  assert.equal(store.getItem(ACTIVE_RUN_STORAGE_KEY), null);

  // 损坏 JSON / 非数组 / 数组里的坏条目：坏数据不牵连好条目。
  store.setItem(ACTIVE_RUN_STORAGE_KEY, "{ not json");
  assert.deepEqual(readActiveRunPins(store), []);
  store.setItem(ACTIVE_RUN_STORAGE_KEY, JSON.stringify({ sessionId: 173, runRef: "agent_run:abc" }));
  assert.deepEqual(readActiveRunPins(store), []);
  store.setItem(ACTIVE_RUN_STORAGE_KEY, JSON.stringify([
    { sessionId: "173", runRef: "agent_run:abc", pinnedAt: 1 },
    { sessionId: 174, runRef: "agent_run:ok", pinnedAt: 2 },
  ]));
  assert.deepEqual(readActiveRunPins(store).map((pin) => pin.runRef), ["agent_run:ok"]);
  assert.equal(activeRunRefForSession(store, 174), "agent_run:ok");
});

test("在途 run 钉扎：不可用存储与条数上限的降级契约", () => {
  // 隐私模式（setItem 抛错）：读写全静默，不抛出。
  const broken = brokenStorage();
  pinActiveRunRef(broken, 173, "agent_run:abc");
  assert.equal(activeRunRefForSession(broken, 173), null);
  clearActiveRunRef(broken, 173);
  assert.deepEqual(readActiveRunPins(broken), []);
  assert.doesNotThrow(() => pinActiveRunRef(null, 173, "agent_run:abc"));

  // 条数上限：最新 8 条保留，最旧会话的钉扎被自然淘汰。
  const store = fakeStorage();
  for (let i = 1; i <= 10; i += 1) pinActiveRunRef(store, i, `agent_run:r${i}`, i);
  const pins = readActiveRunPins(store);
  assert.equal(pins.length, 8);
  assert.deepEqual(pins.map((pin) => pin.sessionId), [3, 4, 5, 6, 7, 8, 9, 10]);
  assert.equal(activeRunRefForSession(store, 2), null);
  assert.equal(activeRunRefForSession(store, 10), "agent_run:r10");
});
