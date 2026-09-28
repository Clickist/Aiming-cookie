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
  /** 服务端已给出确定答案（登录与否）。false + me=null = 还在加载，UI 应显示加载态。 */
  resolved: boolean;
  /** 最后一次读取是否失败（账号服务不可达）；UI 不必展示，供诊断。 */
  unavailable: boolean;
  /** 手动刷新（deep-link 回归、退出登录后、教练回合结束后调用）。 */
  refresh: () => Promise<MemberMe | null>;
}

/** 模块级缓存 + localStorage 持久化（点点 0928：用户中心打开即显示上次数据，不闪
 * 「未登录/未订阅」骨架——含应用重启后；本地单用户应用，member/plan/pct 无密钥，
 * 与会话 JSONL 同级敏感度）。所有 useMemberState 实例共享；损坏一律静默降级。 */
const MEMBER_CACHE_KEY = "aiming-cookie.member.cache";

function readMemberCache(): MemberMe | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(MEMBER_CACHE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || typeof (parsed as { member?: unknown }).member !== "boolean") return null;
    return parsed as MemberMe;
  } catch {
    return null;
  }
}

function writeMemberCache(me: MemberMe | null): void {
  if (typeof window === "undefined") return;
  try {
    if (me === null) window.localStorage.removeItem(MEMBER_CACHE_KEY);
    else window.localStorage.setItem(MEMBER_CACHE_KEY, JSON.stringify(me));
  } catch {
    // 写失败不影响展示（内存缓存仍在）。
  }
}

let memberMeCache: MemberMe | null = typeof window === "undefined" ? null : readMemberCache();

export function useMemberState(enabled = true): MemberState {
  const [me, setMe] = useState<MemberMe | null>(memberMeCache);
  // 服务端是否已给出确定答案（登录与否）：false 期间 UI 应显示加载态而非「未登录」。
  const [resolved, setResolved] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const mountedRef = useRef(true);

  const load = useCallback(async (): Promise<MemberMe | null> => {
    try {
      const status = await fetchMemberStatus();
      if (!mountedRef.current) return null;
      setResolved(true);
      if (status.ok && status.logged_in) {
        memberMeCache = status.me;
        writeMemberCache(status.me);
        setMe(status.me);
        setUnavailable(false);
        return status.me;
      }
      // 未登录（ok:false + unauthorized）与不可用（unavailable）都收敛为 null；
      // 两者的差别只在诊断字段，UI 一律降级未登录态。
      memberMeCache = null;
      writeMemberCache(null);
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

  return { me, resolved, unavailable, refresh: load };
}
