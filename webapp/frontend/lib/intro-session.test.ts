import assert from "node:assert/strict";
import { test } from "node:test";

import { triggerIntroSession } from "./intro-session";

// 首启「开场分析」触发逻辑（PRD §6.1.1）纯行为锁定：created 短路不重复创建、
// 需要创建时只发一次 POST、失败静默降级。React 侧的 ref 守卫与路由复用
// 由 tests/intro-session-source.test.ts 做源码断言。

test("already created: skips POST and reports the existing session id", async () => {
  let createCalls = 0;
  const result = await triggerIntroSession({
    getStatus: async () => ({ created: true, session_id: 12 }),
    create: async () => {
      createCalls += 1;
      return { session_id: 99 };
    },
  });
  assert.deepEqual(result, { created: false, sessionId: 12 });
  assert.equal(createCalls, 0);
});

test("not created: posts once and reports the created session id", async () => {
  let createCalls = 0;
  const result = await triggerIntroSession({
    getStatus: async () => ({ created: false, session_id: null }),
    create: async () => {
      createCalls += 1;
      return { session_id: 7 };
    },
  });
  assert.deepEqual(result, { created: true, sessionId: 7 });
  assert.equal(createCalls, 1);
});

test("created flag set but id missing never guesses a session", async () => {
  const result = await triggerIntroSession({
    getStatus: async () => ({ created: true, session_id: null }),
    create: async () => ({ session_id: 7 }),
  });
  assert.deepEqual(result, { created: false, sessionId: null });
});

test("GET failure degrades silently: no POST, error reported through the channel", async () => {
  const errors: unknown[] = [];
  let createCalls = 0;
  const result = await triggerIntroSession({
    getStatus: async () => {
      throw new Error("sidecar down");
    },
    create: async () => {
      createCalls += 1;
      return { session_id: 7 };
    },
    onError: (error) => errors.push(error),
  });
  assert.equal(result, null);
  assert.equal(createCalls, 0);
  assert.equal(errors.length, 1);
});

test("POST failure or an invalid session id degrades silently", async () => {
  const errors: unknown[] = [];
  const failed = await triggerIntroSession({
    getStatus: async () => ({ created: false, session_id: null }),
    create: async () => {
      throw new Error("boom");
    },
    onError: (error) => errors.push(error),
  });
  assert.equal(failed, null);

  const malformed = await triggerIntroSession({
    getStatus: async () => ({ created: false, session_id: null }),
    create: async () => ({ session_id: 0 }),
    onError: (error) => errors.push(error),
  });
  assert.equal(malformed, null);
  assert.equal(errors.length, 2);
});
