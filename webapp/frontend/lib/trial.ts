/**
 * AC 客户端试用态（验证闸）：免费「一局分析 + 两问」让用户先证明 AC 在他机器上
 * 能正常使用，再放行订阅——防「付款后发现用不了 → 退款手续费白亏」。
 *
 * 数据源：`/api/me` 的 `trial` 字段（accounts 下发，服务端记账）。跨端契约明确：
 * 前端对这些字段做**宽松解析**——`trial` 缺失或形状不对一律视为无试用态（null），
 * fail-open 走现有路径，绝不因 /me 形状变化崩客户端。
 *
 * 上报：`POST /api/trial-events`（body `{ type }`，JWT 由代理附带，服务端幂等记账；
 * 契约不带客户端键）。本地只做去重（已上报标志）与失败补报（pending 队列，下次
 * 启动补报）；上报成功后广播会员态刷新，让剩余数字立刻跟上服务端账本。
 *
 * 纯逻辑（解析 / 过闸 / 去重计划 / 存储读写）与效应（网络）分离，前者可单测。
 */

import { postTrialEvent } from "./api";
import { notifyMemberStateChanged } from "./member-state";

/** 上报事件类型（跨端契约，不可单方面扩）。 */
export type TrialEventType = "analysis_done" | "question_answered";

/** 宽松解析后的试用态（仅当 /me 明确给出可用的 trial 态才非 null）。 */
export interface TrialState {
  active: true;
  analysesRemaining: number;
  questionsRemaining: number;
  verified: boolean;
}

function readTrialRemaining(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return Math.floor(value);
}

/**
 * `/api/me` → 试用态的宽松解析。任何形状疑点都收敛为 null（无试用态）：
 * 不是对象、缺 `trial`、`active !== true`、两个 remaining 缺失或非非负数字——
 * 一律 fail-open 走现有路径（不进试用模式、不上报、不付费墙）。
 * `verified` 缺失时按「分析余量已扣完」推导（验证闸的语义即「跑完一局」）。
 */
export function parseTrialState(me: unknown): TrialState | null {
  if (typeof me !== "object" || me === null) return null;
  const trial = (me as { trial?: unknown }).trial;
  if (typeof trial !== "object" || trial === null) return null;
  const raw = trial as Record<string, unknown>;
  if (raw.active !== true) return null;
  const analysesRemaining = readTrialRemaining(raw.analyses_remaining);
  const questionsRemaining = readTrialRemaining(raw.questions_remaining);
  if (analysesRemaining === null || questionsRemaining === null) return null;
  return {
    active: true,
    analysesRemaining,
    questionsRemaining,
    verified: typeof raw.verified === "boolean" ? raw.verified : analysesRemaining <= 0,
  };
}

/** 对应事件类型的剩余次数（无试用态 = 0，天然过不了闸）。 */
export function trialRemainingFor(type: TrialEventType, trial: TrialState | null): number {
  if (!trial || !trial.active) return 0;
  return type === "analysis_done" ? trial.analysesRemaining : trial.questionsRemaining;
}

/** 上报闸（纯逻辑）：仅试用态且对应 remaining > 0 时才发。 */
export function shouldReportTrialEvent(type: TrialEventType, trial: TrialState | null): boolean {
  return trialRemainingFor(type, trial) > 0;
}

// ── 本地去重 + 失败补报（localStorage；损坏/不可用一律静默降级）───────────────

export const TRIAL_REPORTED_KEY = "aiming-cookie.trial.reported";
export const TRIAL_PENDING_KEY = "aiming-cookie.trial.pending";

export interface PendingTrialEvent {
  type: TrialEventType;
  /** 去重键：analysis_done 用 `analysis:<id>`，question_answered 用 run_ref。 */
  key: string;
}

export function readReportedTrialKeys(storage: Storage | null | undefined): Set<string> {
  if (!storage) return new Set();
  try {
    const raw = JSON.parse(storage.getItem(TRIAL_REPORTED_KEY) ?? "[]");
    return new Set(Array.isArray(raw) ? raw.filter((item): item is string => typeof item === "string") : []);
  } catch {
    return new Set();
  }
}

export function markTrialReported(storage: Storage | null | undefined, key: string): void {
  if (!storage) return;
  const done = readReportedTrialKeys(storage);
  done.add(key);
  try {
    storage.setItem(TRIAL_REPORTED_KEY, JSON.stringify([...done]));
  } catch {
    // 本地去重标记写失败不影响上报本身（服务端幂等兜底）。
  }
}

export function readPendingTrialEvents(storage: Storage | null | undefined): PendingTrialEvent[] {
  if (!storage) return [];
  try {
    const raw = JSON.parse(storage.getItem(TRIAL_PENDING_KEY) ?? "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter(
      (item): item is PendingTrialEvent =>
        typeof item === "object" && item !== null
        && ((item as PendingTrialEvent).type === "analysis_done" || (item as PendingTrialEvent).type === "question_answered")
        && typeof (item as PendingTrialEvent).key === "string",
    );
  } catch {
    return [];
  }
}

export function enqueuePendingTrialEvent(storage: Storage | null | undefined, event: PendingTrialEvent): void {
  if (!storage) return;
  const queue = readPendingTrialEvents(storage);
  if (queue.some((item) => item.type === event.type && item.key === event.key)) return;
  queue.push(event);
  try {
    storage.setItem(TRIAL_PENDING_KEY, JSON.stringify(queue));
  } catch {
    // 队列写失败 = 放弃补报（尽力而为），当前上报不受影响。
  }
}

export function removePendingTrialEvent(storage: Storage | null | undefined, event: PendingTrialEvent): void {
  if (!storage) return;
  const queue = readPendingTrialEvents(storage).filter(
    (item) => !(item.type === event.type && item.key === event.key),
  );
  try {
    storage.setItem(TRIAL_PENDING_KEY, JSON.stringify(queue));
  } catch {
    // 写失败会留下已上报成功的残留项：flush 再遇它时服务端幂等，无害。
  }
}

// ── 效应：上报与启动补报（浏览器端调用；trial 快照由 AppShell 随 /me 推送）────

/** 当前试用态快照；undefined = /me 尚未取到（此时不上报，闸视为关闭）。 */
let activeTrialSnapshot: TrialState | null | undefined;

export function setActiveTrialState(trial: TrialState | null): void {
  activeTrialSnapshot = trial;
}

/** 上报计划（纯逻辑）：gate=闸关（无试用态/余量尽/未知），duplicate=本地已记，send=可发。 */
export function planTrialEvent(
  type: TrialEventType,
  key: string,
  trial: TrialState | null | undefined,
  reported: ReadonlySet<string>,
): "gate" | "duplicate" | "send" {
  if (!shouldReportTrialEvent(type, trial ?? null)) return "gate";
  if (reported.has(key)) return "duplicate";
  return "send";
}

/** 会话内重试退避（1004 死锁 C）：上报失败若只等"下次启动"补报，用户跑完一局
 *  马上去订阅会被 /pay 验证闸挡住（verified_at 未落）——大陆直连 CF 抖动是实况。
 *  30s/60s/120s/300s 共 4 次会话内重试，之后仍失败才交给下次启动的 flush。 */
const TRIAL_RETRY_DELAYS_MS = [30_000, 60_000, 120_000, 300_000];

function scheduleTrialRetry(type: TrialEventType, key: string, attempt: number): void {
  if (typeof window === "undefined" || attempt >= TRIAL_RETRY_DELAYS_MS.length) return;
  window.setTimeout(() => {
    void postTrialEvent(type)
      .then(() => {
        removePendingTrialEvent(window.localStorage, { type, key });
        notifyMemberStateChanged();
      })
      .catch(() => scheduleTrialRetry(type, key, attempt + 1));
  }, TRIAL_RETRY_DELAYS_MS[attempt]);
}

/**
 * 上报一条试用事件。成功路径：标记已上报 → 入 pending → POST → 出 pending →
 * 广播会员态刷新（剩余数字跟上服务端账本）。标记先行（并发双触发只发一次），
 * POST 失败/中断留在 pending：先会话内退避重试（死锁 C），再由下次启动的
 * flushPendingTrialEvents 兜底。
 */
export async function reportTrialEvent(type: TrialEventType, key: string): Promise<"sent" | "queued" | "duplicate" | "gate"> {
  if (typeof window === "undefined") return "gate";
  const plan = planTrialEvent(type, key, activeTrialSnapshot, readReportedTrialKeys(window.localStorage));
  if (plan !== "send") return plan;
  const storage = window.localStorage;
  markTrialReported(storage, key);
  enqueuePendingTrialEvent(storage, { type, key });
  try {
    await postTrialEvent(type);
  } catch {
    scheduleTrialRetry(type, key, 0);
    return "queued"; // pending 队列保底，重试成功即出队。
  }
  removePendingTrialEvent(storage, { type, key });
  notifyMemberStateChanged();
  return "sent";
}

/** 启动补报：逐条过闸（试用态仍在且余量 > 0）后重发 pending；仍失败留在队列。 */
export async function flushPendingTrialEvents(): Promise<void> {
  if (typeof window === "undefined") return;
  const storage = window.localStorage;
  for (const event of readPendingTrialEvents(storage)) {
    if (!shouldReportTrialEvent(event.type, activeTrialSnapshot ?? null)) continue;
    try {
      await postTrialEvent(event.type);
    } catch {
      continue;
    }
    removePendingTrialEvent(storage, event);
    notifyMemberStateChanged();
  }
}
