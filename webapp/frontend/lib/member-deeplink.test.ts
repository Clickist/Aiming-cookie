import assert from "node:assert/strict";
import { test } from "node:test";

import {
  clearPendingDeepLink,
  describeMemberDeepLinkUrls,
  peekPendingDeepLink,
  settleMemberPollResult,
  stagePendingDeepLink,
  type MemberDeepLinkHandlers,
} from "./member-deeplink";
import { MEMBER_EXCHANGED_EVENT } from "./contracts";
import type { MemberPollResponse } from "./types";

// 1018 野外案回归：deep-link 事件在 OS→webview 最后一跳可能整段丢失（报障
// 31f899ed：Rust 收到 8/8、JS 处理 0/8）。这里覆盖两件新能力——到达痕迹的
// 脱敏摘要（票据值不进日志）与轮询兜底的收口语义（done 与 exchange 成功
// 同形、no_pending 常态不刷屏）。

const LOGIN_URL = "aimingcookie://auth?scene=login&ticket=ticket-secret-value&dc=dc-1234";

/** 轮询响应工厂：只填 settleMemberPollResult 关心的字段。 */
function pollResponse(partial: MemberPollResponse): MemberPollResponse {
  return partial;
}

function recordingHandlers(): MemberDeepLinkHandlers & {
  refreshed: string[];
  connectionFailed: string[];
} {
  const refreshed: string[] = [];
  const connectionFailed: string[] = [];
  return {
    refreshed,
    connectionFailed,
    onRefreshed: (outcome) => refreshed.push(outcome.kind),
    onConnectionFailed: (message) => connectionFailed.push(message),
  };
}

/** 捕获 console.warn 的 noteMemberDeepLinkEvent 痕迹（含前端环形日志同文）。 */
async function captureNotes(run: () => Promise<void> | void): Promise<string[]> {
  const lines: string[] = [];
  const original = console.warn;
  console.warn = (message?: unknown) => {
    lines.push(String(message));
  };
  try {
    await run();
  } finally {
    console.warn = original;
  }
  return lines;
}

test("describeMemberDeepLinkUrls summarizes scene/ticket/dc presence without leaking values", () => {
  const line = describeMemberDeepLinkUrls([LOGIN_URL, "https://example.com/not-a-member-link"], "event");
  assert.match(line, /deep-link received \(event\): 2 url\(s\)/);
  assert.match(line, /scene=login ticket=yes dc=yes/);
  assert.match(line, /unparsed/);
  // 票据与 dc 值一律不落日志（与壳层 Rust 侧 log_deep_link_receipt 同口径）。
  assert.doesNotMatch(line, /ticket-secret-value|dc-1234/);
  assert.equal(describeMemberDeepLinkUrls(null, "cold-start"), "deep-link received (cold-start): 0 url(s) []");
});

test("settleMemberPollResult keeps waiting on pending/error without touching handlers", async () => {
  for (const result of [pollResponse({ outcome: "pending" }), pollResponse({ outcome: "error", code: "network_error", message: "x" })]) {
    const handlers = recordingHandlers();
    const notes = await captureNotes(() => settleMemberPollResult(result, handlers));
    assert.equal(handlers.refreshed.length, 0);
    assert.equal(handlers.connectionFailed.length, 0);
    assert.equal(notes.length, 0);
  }
});

test("settleMemberPollResult stays quiet for the idle no_pending norm", async () => {
  const handlers = recordingHandlers();
  const notes = await captureNotes(() =>
    settleMemberPollResult(pollResponse({ outcome: "stopped", code: "no_pending", message: "x" }), handlers));
  // no_pending 是无登录流时的常态返回（全局 3s 轮询会高频命中），不能刷屏。
  assert.equal(notes.length, 0);
  assert.equal(handlers.refreshed.length, 0);
});

test("settleMemberPollResult logs non-idle terminal stops once without refreshing", async () => {
  const handlers = recordingHandlers();
  const notes = await captureNotes(() =>
    settleMemberPollResult(pollResponse({ outcome: "stopped", code: "ticket_expired", message: "x" }), handlers));
  assert.equal(notes.filter((line) => line.includes("login poll stopped: ticket_expired")).length, 1);
  assert.equal(handlers.refreshed.length, 0);
});

test("settleMemberPollResult completes login on done: refresh, broadcast, staged-link cleanup", async () => {
  // 假 window：轮询收尾要清 localStorage 暂存并广播 MEMBER_EXCHANGED_EVENT。
  const dispatched: { type: string; member: unknown }[] = [];
  const store = new Map<string, string>();
  const previousWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => (store.has(key) ? store.get(key) ?? null : null),
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    },
    dispatchEvent: (event: { type: string; detail?: { member?: unknown } }) => {
      dispatched.push({ type: event.type, member: event.detail?.member });
      return true;
    },
  };
  try {
    // 预置一条暂存 deep-link（模拟事件被吞后 RC1 暂存的重试残留）。
    stagePendingDeepLink(LOGIN_URL);
    assert.ok(peekPendingDeepLink());

    const handlers = recordingHandlers();
    const notes = await captureNotes(() =>
      settleMemberPollResult(
        pollResponse({
          outcome: "done",
          ok: true,
          user: { id: "u1", email: "a@example.com", name: null },
          member: true,
          connection_ok: true,
          connection_code: null,
          connection_message: null,
        }),
        handlers,
      ));
    assert.deepEqual(handlers.refreshed, ["exchanged"]);
    assert.equal(handlers.connectionFailed.length, 0);
    // 换票已落库：暂存清理 + 成功广播各一次。
    assert.equal(peekPendingDeepLink(), null);
    assert.equal(dispatched.filter((event) => event.type === MEMBER_EXCHANGED_EVENT && event.member === true).length, 1);
    // 「事件被吞、轮询救回」的野外发生率就靠这行日志度量。
    assert.equal(notes.filter((line) => line.includes("login completed via poll fallback")).length, 1);
  } finally {
    (globalThis as { window?: unknown }).window = previousWindow;
    clearPendingDeepLink();
  }
});

test("settleMemberPollResult surfaces connection test failure on done", async () => {
  const handlers = recordingHandlers();
  await captureNotes(() =>
    settleMemberPollResult(
      pollResponse({
        outcome: "done",
        ok: true,
        user: { id: "u1", email: "a@example.com", name: null },
        member: true,
        connection_ok: false,
        connection_code: "unreachable",
        connection_message: "无法连接会员网关。",
      }),
      handlers,
    ));
  assert.deepEqual(handlers.refreshed, ["exchanged"]);
  assert.deepEqual(handlers.connectionFailed, ["无法连接会员网关。"]);
});
