/**
 * 客户端直推余量（usage-reporter）：会员 sidecar 主动把 sub 池余量推给 accounts。
 *
 * 背景（2026-09-27 拍板）：/api/me 的余量此前只由 ECS poller 推（/internal/usage）；
 * 展示数据改由客户端主动推送，poller 降频为退款对账兜底。推送时机两个：
 * a) Coach turn 成功结束后（有真实消耗），b) 60s 定时循环（与前端 /api/me 轮询同节奏）。
 *
 * - 余量来源：中转站 new-api 的 OpenAI 兼容计费端点
 *   `GET {base}/v1/dashboard/billing/subscription`（只读）。经会员网关透传
 *   （契约 §5.2-6：网关只透传 `/v1/...` 并按池注入令牌），凭据用会员 JWT——
 *   sk- 全程不出 ECS，客户端不直连中转站。响应 `hard_limit_usd` 按
 *   500,000 quota/单位换算回 quota（§0 换算常量）。
 * - 推送目标：`POST /api/me/usage-report`（jwt 鉴权，INTERFACE.md §7.1-17）。
 *   grant 不由客户端决定：服务端以 quota_grants 为权威并把超发余量夹回。
 * - best-effort：任何失败静默（只落一行脱敏日志，不打 JWT/sk-），绝不影响
 *   对话路径；30s 最小间隔 + in-flight 去重，turn 结束与定时循环重叠时只推一次。
 * - 仅会员档启用：无 relay JWT（BYOK/未登录/fixture 走查态）直接跳过，零网络。
 */

import { ACCOUNTS_BASE_URL, memberFixtureActive, storedMemberJwt } from "./member-auth.ts";
import { MEMBER_GATEWAY_BASE_URL } from "./provider-models.ts";

/** 中转站计费换算刻度（契约 §0：500,000 quota = 1 单位）。 */
export const QUOTA_PER_UNIT = 500_000;
/** 两次推送链的最小间隔：低于 60s 循环节奏，turn 结束与循环重叠时只推一次。 */
export const MIN_PUSH_INTERVAL_MS = 30_000;
/** 定时循环节奏（与前端 member-state 的 /api/me 轮询一致）。 */
export const USAGE_REPORT_INTERVAL_MS = 60_000;
const HTTP_TIMEOUT_MS = 10_000;

export type UsagePushResult =
  | { ok: true; remaining: number }
  | {
      ok: false;
      skipped: true;
      reason: "no_member_profile" | "throttled" | "billing_unavailable" | "push_failed";
    };

/**
 * 解析计费端点响应 → 剩余 quota。响应不合预期（缺字段/负数/非有限数）返回
 * null，由调用方静默放弃本轮；余量口径最终以服务端夹取为准，这里只管尽力取值。
 */
export function parseBillingRemaining(body: unknown): number | null {
  if (typeof body !== "object" || body === null) return null;
  const hardLimit = (body as { hard_limit_usd?: unknown }).hard_limit_usd;
  if (typeof hardLimit !== "number" || !Number.isFinite(hardLimit) || hardLimit < 0) return null;
  return Math.max(0, Math.round(hardLimit * QUOTA_PER_UNIT));
}

/** 节流判定：距上次调度不足 minIntervalMs 时跳过（首次恒放行）。 */
export function throttled(lastPushAt: number, now: number, minIntervalMs: number): boolean {
  return lastPushAt > 0 && now - lastPushAt < minIntervalMs;
}

function log(message: string): void {
  // 单行、脱敏：只有数量与 HTTP 状态，绝不打 JWT / sk-。
  console.log(`[usage-report] ${message}`);
}

async function fetchBillingRemaining(jwt: string): Promise<number | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), HTTP_TIMEOUT_MS);
  timeout.unref?.();
  try {
    // 计费查询走会员网关（member-gateway.example.invalid:8443/member/v1），JWT 是网关签发的：
    // relayBaseUrl() 被构建期 --define 内联为中转站直连地址，JWT 查它必 401（0928 实测）。
    const billingBase = (process.env.AC_MEMBER_GATEWAY_BASE_URL ?? MEMBER_GATEWAY_BASE_URL).replace(/\/+$/, "");
    const response = await fetch(`${billingBase}/dashboard/billing/subscription`, {
      headers: { Authorization: `Bearer ${jwt}` },
      signal: controller.signal,
    });
    if (response.status !== 200) return null;
    const body: unknown = await response.json().catch(() => null);
    return parseBillingRemaining(body);
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

let lastDispatchAt = 0;
let inFlight: Promise<UsagePushResult> | null = null;

/**
 * 推送一次（查中转站余量 → 推 accounts）。同链路并发调用合并为一次；
 * 距上次调度不足 30s 直接跳过。失败一律返回结构化结果，绝不抛出。
 */
export function pushMemberUsageOnce(): Promise<UsagePushResult> {
  if (inFlight) return inFlight;
  const now = Date.now();
  if (throttled(lastDispatchAt, now, MIN_PUSH_INTERVAL_MS)) {
    return Promise.resolve({ ok: false, skipped: true, reason: "throttled" });
  }
  const run = dispatch();
  inFlight = run;
  void run.finally(() => {
    if (inFlight === run) inFlight = null;
  });
  return run;
}

async function dispatch(): Promise<UsagePushResult> {
  lastDispatchAt = Date.now();
  if (memberFixtureActive()) return { ok: false, skipped: true, reason: "no_member_profile" };
  const jwt = storedMemberJwt();
  if (!jwt) return { ok: false, skipped: true, reason: "no_member_profile" };

  const remaining = await fetchBillingRemaining(jwt);
  if (remaining === null) {
    log("billing unavailable, skipped");
    return { ok: false, skipped: true, reason: "billing_unavailable" };
  }
  try {
    const response = await fetch(`${ACCOUNTS_BASE_URL}/api/me/usage-report`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${jwt}` },
      body: JSON.stringify({ remaining }),
    });
    if (response.status !== 200) {
      log(`push rejected (HTTP ${response.status})`);
      return { ok: false, skipped: true, reason: "push_failed" };
    }
    log(`pushed remaining=${remaining}`);
    return { ok: true, remaining };
  } catch {
    log("push failed (network)");
    return { ok: false, skipped: true, reason: "push_failed" };
  }
}

/** turn 结束钩子（sidecar-server 在成功 turn 后调用）：fire-and-forget，绝不阻塞回复。 */
export function notifyTurnConsumed(): void {
  void pushMemberUsageOnce().catch(() => {});
}

/** 60s 定时循环；返回 stop 函数（server close 时清理，测试/关停用）。 */
export function startUsageReportLoop(intervalMs: number = USAGE_REPORT_INTERVAL_MS): () => void {
  const timer = setInterval(() => {
    void pushMemberUsageOnce().catch(() => {});
  }, intervalMs);
  timer.unref?.();
  return () => clearInterval(timer);
}

/** 测试用：清空模块级节流与 in-flight 状态。 */
export function resetUsageReporterForTest(): void {
  lastDispatchAt = 0;
  inFlight = null;
}
