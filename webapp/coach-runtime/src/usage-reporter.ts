/**
 * 会员心跳推余量（usage-reporter）：sidecar 把 accounts 自己的快照真值回推，
 * 维持 usage_snapshots.updated_at 新鲜（退款闸 usage_stale 依赖，refund.ts 5 分钟窗）。
 *
 * 历史（2026-09-27 拍板）：/api/me 的余量改由客户端直推，poller（/internal/usage）
 * 保留为退款对账兜底。推送时机两个：a) Coach turn 成功结束后（有真实消耗），
 * b) 60s 定时循环（与前端 /api/me 轮询同节奏）。
 *
 * 2026-10-04 修正（R3）：原实现调中转站 `GET /v1/dashboard/billing/subscription`
 * 自算 remaining——该端点对 unlimited 令牌恒返 hard_limit_usd=1e8，且语义本是
 * "总额=remain+used" 而非剩余，推上去的 5e13 被 clamp+MIN 吞掉，直推实际只刷
 * 时间戳。现改为**真值回声**：GET /api/me 取服务端展示的 pools.sub.remaining
 * （ECS pusher 写入 usage_snapshots 的权威值；快照缺失时是服务端自己的满额
 * fallback），原样 POST 回 /api/me/usage-report。客户端不再自造任何数值；
 * MIN/clamp 兜底下回声对数据是无操作，净效果=心跳。
 *
 * - 推送目标：`POST /api/me/usage-report`（jwt 鉴权，INTERFACE.md §7.1-17）。
 *   grant 不由客户端决定：服务端以 quota_grants 为权威并把超发余量夹回。
 * - best-effort：任何失败静默（只落一行脱敏日志，不打 JWT），绝不影响
 *   对话路径；30s 最小间隔 + in-flight 去重，turn 结束与定时循环重叠时只推一次。
 * - 仅会员档启用：无 relay JWT（BYOK/未登录/fixture 走查态）直接跳过，零网络。
 */

import { ACCOUNTS_BASE_URL, memberFixtureActive, storedMemberJwt } from "./member-auth.ts";

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
      reason: "no_member_profile" | "throttled" | "snapshot_unavailable" | "push_failed";
    };

/**
 * 解析 GET /api/me 响应 → sub 池余量回声值。缺 sub 池（试用/无快照无发放）或
 * 值不是非负有限数返回 null，由调用方静默放弃本轮。客户端不改写数值：推的
 * 就是服务端展示的真值（或其满额 fallback），余量口径始终以服务端夹取为准。
 */
export function parseMeSubRemaining(body: unknown): number | null {
  if (typeof body !== "object" || body === null) return null;
  const pools = (body as { pools?: unknown }).pools;
  if (typeof pools !== "object" || pools === null) return null;
  const sub = (pools as { sub?: unknown }).sub;
  if (typeof sub !== "object" || sub === null) return null;
  const remaining = (sub as { remaining?: unknown }).remaining;
  if (typeof remaining !== "number" || !Number.isFinite(remaining) || remaining < 0) return null;
  return remaining;
}

/** 节流判定：距上次调度不足 minIntervalMs 时跳过（首次恒放行）。 */
export function throttled(lastPushAt: number, now: number, minIntervalMs: number): boolean {
  return lastPushAt > 0 && now - lastPushAt < minIntervalMs;
}

function log(message: string): void {
  // 单行、脱敏：只有数量与 HTTP 状态，绝不打 JWT / sk-。
  console.log(`[usage-report] ${message}`);
}

async function fetchSubRemainingEcho(jwt: string): Promise<number | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), HTTP_TIMEOUT_MS);
  timeout.unref?.();
  try {
    const response = await fetch(`${ACCOUNTS_BASE_URL}/api/me`, {
      headers: { Authorization: `Bearer ${jwt}` },
      signal: controller.signal,
    });
    if (response.status !== 200) return null;
    const body: unknown = await response.json().catch(() => null);
    return parseMeSubRemaining(body);
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

  const remaining = await fetchSubRemainingEcho(jwt);
  if (remaining === null) {
    log("sub snapshot unavailable, skipped");
    return { ok: false, skipped: true, reason: "snapshot_unavailable" };
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
    log(`echoed remaining=${remaining}`);
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
