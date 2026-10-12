"use client";

/**
 * 试用/会员额度在途的等待编排（2026-10-11 竞态修复，点点拍板）：
 * run 失败且 code=trial_provisioning 时，错误卡位置改装「初始化卡」，轮询
 * /api/me（经 useMemberState().refresh，即 sidecar 代理的 accounts /api/me），
 * 到账后自动 retry 原回合；墙钟超时降级为明确文案（禁止静默卡死/静默放行）。
 *
 * 到达判定不依赖 trial.provisioning（additive 字段，Worker 未发时 undefined）：
 * pools 任一非空或 member=true 即到账。provisioning 字段只用于「提前终止」
 * （在途已消失且未到账 = failed），缺字段时不提前终止、等满超时——fail-open。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { MemberMe } from "./types";

/** 轮询间隔：立即一次，之后每 5s（方案 §10-Q4 拍板默认值）。 */
export const TRIAL_WAIT_POLL_INTERVAL_MS = 5_000;
/** 总超时（墙钟）：120s，覆盖门铃关闭时的 60s 轮询降级路径（方案 §10-Q4）。 */
export const TRIAL_WAIT_TIMEOUT_MS = 120_000;

export type TrialWaitPhase = "idle" | "waiting" | "arrived" | "timed_out";

/** 到达：任一池非空（发货 complete 写 grant 后 /me 按 100% 显示）或已是会员。 */
export function trialWaitArrived(me: MemberMe | null): boolean {
  if (!me) return false;
  return Boolean(me.pools.sub ?? me.pools.boost) || me.member;
}

/**
 * 提前终止（需 trial.provisioning 字段）：试用视图仍 active、但已无在途单、
 * 又没有任何池与会员 → 发放已 failed，等不出结果。字段缺省（undefined）时
 * 一律 false（等满超时，fail-open）。
 */
export function trialWaitVanished(me: MemberMe | null): boolean {
  if (!me) return false;
  const trial = me.trial;
  if (!trial || trial.active !== true) return false;
  if (trial.provisioning !== false) return false;
  return !me.member && me.pools.sub == null && me.pools.boost == null;
}

/** 轮询计划（纯逻辑，可测）：返回下次轮询延迟序列的步进。 */
export function nextTrialWaitDelay(elapsedMs: number, intervalMs = TRIAL_WAIT_POLL_INTERVAL_MS): number | null {
  if (elapsedMs >= TRIAL_WAIT_TIMEOUT_MS) return null;
  return Math.min(intervalMs, TRIAL_WAIT_TIMEOUT_MS - elapsedMs);
}

export interface TrialProvisioningWaitHandle {
  phase: TrialWaitPhase;
  /** 当前等待绑定的 runRef（等待中切换 run 自动重置）。 */
  runRef: string | null;
  /** 已等待毫秒（渲染秒数用）。 */
  elapsedMs: number;
  /** 开始等待（绑定 runRef，进入 waiting）。 */
  begin: (runRef: string) => void;
  /** 复位回 idle。 */
  reset: () => void;
}

/**
 * 等待编排 hook。deps 全注入便于测试与复用：
 * - refresh：useMemberState().refresh（拉 /api/me 并更新全局缓存）
 * - onArrived：到账回调（CoachPanel 传 () => retry(runRef)）
 * - onTimedOut：超时回调（渲染降级卡；日志由调用方打）
 */
export function useTrialProvisioningWait(deps: {
  refresh: () => Promise<MemberMe | null>;
  onArrived: (runRef: string) => void;
  onTimedOut: (runRef: string) => void;
  intervalMs?: number;
  timeoutMs?: number;
  now?: () => number;
}): TrialProvisioningWaitHandle {
  const { refresh, intervalMs = TRIAL_WAIT_POLL_INTERVAL_MS, timeoutMs = TRIAL_WAIT_TIMEOUT_MS, now = Date.now } = deps;
  const [state, setState] = useState<{ phase: TrialWaitPhase; runRef: string | null; elapsedMs: number }>({
    phase: "idle",
    runRef: null,
    elapsedMs: 0,
  });
  const callbacks = useRef(deps);
  callbacks.current = deps;

  const begin = useCallback((runRef: string) => {
    setState({ phase: "waiting", runRef, elapsedMs: 0 });
  }, []);

  const reset = useCallback(() => {
    setState({ phase: "idle", runRef: null, elapsedMs: 0 });
  }, []);

  useEffect(() => {
    if (state.phase !== "waiting" || !state.runRef) return;
    const runRef = state.runRef;
    const startedAt = now();
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      const me = await callbacks.current.refresh().catch(() => null);
      if (cancelled) return;
      const elapsed = now() - startedAt;
      if (trialWaitArrived(me)) {
        setState((s) => (s.runRef === runRef && s.phase === "waiting" ? { ...s, phase: "arrived" } : s));
        callbacks.current.onArrived(runRef);
        return;
      }
      if (trialWaitVanished(me) || elapsed >= timeoutMs) {
        setState((s) => (s.runRef === runRef && s.phase === "waiting" ? { ...s, phase: "timed_out", elapsedMs: elapsed } : s));
        callbacks.current.onTimedOut(runRef);
        return;
      }
      setState((s) => (s.runRef === runRef && s.phase === "waiting" ? { ...s, elapsedMs: elapsed } : s));
      const delay = nextTrialWaitDelay(elapsed, intervalMs);
      if (delay !== null) timer = setTimeout(() => void tick(), delay);
    };
    let timer = setTimeout(() => void tick(), 0); // 立即首查
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [state.phase, state.runRef, intervalMs, timeoutMs, now]);

  return { ...state, begin, reset };
}
