import assert from "node:assert/strict";
import { test } from "node:test";

import { triggerIntroSession } from "./intro-session";
import type { IntroSessionCreated, IntroSessionStatus } from "./intro-session";

// 首启「开场分析」触发逻辑（PRD §6.1.1）纯行为锁定：有可见消息短路不重复创建；
// flag 未置或 flag 已置但会话空白（Provider 当时不可用被闸门拦下）都发幂等 POST
// 让 sidecar 补跑；失败静默降级。React 侧的 ref 守卫与路由复用由
// tests/intro-session-source.test.ts 做源码断言。

const created = (sessionId: number): IntroSessionCreated => ({
  session_id: sessionId,
  run_ref: `agent_run:${sessionId}`,
  provider_ready: true,
});

const status = (
  value: Partial<IntroSessionStatus> & Pick<IntroSessionStatus, "created" | "session_id">,
): IntroSessionStatus => ({ has_messages: false, ...value });

test("already created with messages: skips POST and reports the existing session id", async () => {
  let createCalls = 0;
  const result = await triggerIntroSession({
    getStatus: async () => status({ created: true, session_id: 12, has_messages: true }),
    create: async () => {
      createCalls += 1;
      return created(99);
    },
  });
  assert.deepEqual(result, { created: false, sessionId: 12 });
  assert.equal(createCalls, 0);
});

test("not created: posts once and reports the created session id", async () => {
  let createCalls = 0;
  const result = await triggerIntroSession({
    getStatus: async () => status({ created: false, session_id: null }),
    create: async () => {
      createCalls += 1;
      return created(7);
    },
  });
  assert.deepEqual(result, { created: true, sessionId: 7 });
  assert.equal(createCalls, 1);
});

test("created flag set but session still empty: re-posts for self-heal without re-announcing creation", async () => {
  let createCalls = 0;
  const result = await triggerIntroSession({
    getStatus: async () => status({ created: true, session_id: 12, has_messages: false }),
    create: async () => {
      createCalls += 1;
      return created(12);
    },
  });
  // 会话早已存在：不返回 created=true（否则前端会重复打开/闪一次旧会话），
  // 但确实发了 POST 让 sidecar 在 Provider 恢复后补跑 kickoff。
  assert.deepEqual(result, { created: false, sessionId: 12 });
  assert.equal(createCalls, 1);
});

test("created flag set but id missing never guesses a session", async () => {
  const result = await triggerIntroSession({
    getStatus: async () => status({ created: true, session_id: null, has_messages: true }),
    create: async () => created(7),
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
      return created(7);
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
    getStatus: async () => status({ created: false, session_id: null }),
    create: async () => {
      throw new Error("boom");
    },
    onError: (error) => errors.push(error),
  });
  assert.equal(failed, null);

  const malformed = await triggerIntroSession({
    getStatus: async () => status({ created: false, session_id: null }),
    create: async () => created(0),
    onError: (error) => errors.push(error),
  });
  assert.equal(malformed, null);
  assert.equal(errors.length, 2);
});
