"use client";

/**
 * 会员状态的客户端单一入口（②/②b/②c/④/⑧/⑨ 共用）。
 *
 * 刷新时机（任务书 ⑥：余量轮询 60s，读 `/api/me`）：启动、窗口聚焦、60s 轮询、
 * deep-link 回归事件；401 一律静默降级未登录态（契约 §7.1-8），绝不弹错误。
 *
 * BYOK 用户全程不受影响：本 hook 只读会员态，不改任何 Provider 配置。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchMemberStatus } from "@/lib/api";
import type { MemberMe } from "@/lib/types";

/** 余量轮询周期（任务书 ⑥；契约要求展示源只有 /api/me）。 */
export const MEMBER_POLL_INTERVAL_MS = 60_000;

/**
 * 会员态变更广播（退出登录 / deep-link 换票成功 / 连通测试完成）：所有
 * `useMemberState` 实例立即重拉，不必等下一个 60s 周期。窗口事件而非模块状态，
 * 因为 sidecar 侧的变化（换票）发生在本进程之外。
 */
export const MEMBER_STATE_CHANGED_EVENT = "aiming-cookie:member-changed";

export function notifyMemberStateChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(MEMBER_STATE_CHANGED_EVENT));
}

export interface MemberState {
  /** null = 未登录 / 尚未取到（未登录是常态，不是错误）。 */
  me: MemberMe | null;
  /** 最后一次读取是否失败（账号服务不可达）；UI 不必展示，供诊断。 */
  unavailable: boolean;
  /** 手动刷新（deep-link 回归、退出登录后、教练回合结束后调用）。 */
  refresh: () => Promise<MemberMe | null>;
}

export function useMemberState(enabled = true): MemberState {
  const [me, setMe] = useState<MemberMe | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const mountedRef = useRef(true);

  const load = useCallback(async (): Promise<MemberMe | null> => {
    try {
      const status = await fetchMemberStatus();
      if (!mountedRef.current) return null;
      if (status.ok && status.logged_in) {
        setMe(status.me);
        setUnavailable(false);
        return status.me;
      }
      // 未登录（ok:false + unauthorized）与不可用（unavailable）都收敛为 null；
      // 两者的差别只在诊断字段，UI 一律降级未登录态。
      setMe(null);
      setUnavailable(!status.ok && status.logged_in);
      return null;
    } catch {
      // 桌面运行时/sidecar 未就绪：保持上一次的值，不把已登录状态闪成未登录。
      if (mountedRef.current) setUnavailable(true);
      return null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    void load();
    const timer = window.setInterval(() => {
      void load();
    }, MEMBER_POLL_INTERVAL_MS);
    const onFocus = () => {
      void load();
    };
    const onChanged = () => {
      void load();
    };
    window.addEventListener("focus", onFocus);
    window.addEventListener(MEMBER_STATE_CHANGED_EVENT, onChanged);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener(MEMBER_STATE_CHANGED_EVENT, onChanged);
    };
  }, [enabled, load]);

  return { me, unavailable, refresh: load };
}
