"use client";

/**
 * deep-link 监听（契约 §3.3-8/9）：**不只在 Onboarding 阶段监听**——已进主界面
 * 同样要处理 scene=subscribe/booster（刷新 /api/me）与 scene=login（exchange 后刷新）。
 *
 * 单实例去重（§3.3-5）分两层：
 * 1. 本 hook 对同一条 URL 字符串只在**处理落定后**记入会话内 seen 集合（浏览器
 *    重试 / 插件重复广播）；
 * 2. sidecar 的 member-auth 记录已消费 ticket 并按 dc 绑定校验（§3.3-4），
 *    重复 exchange 会被结构化拒绝，不重复打服务端。
 *
 * 失败处置（RC1 修复后的语义）：拒绝与降级依旧不弹错误（§3.3-7），但**一次性
 * ticket 不再因瞬态失败而无声丢失**——sidecar 未就绪/网络瞬断时 exchange 抛
 * `DesktopRuntimeUnavailableError`（或返回 network_error），此时 ticket 尚未被
 * 消费，原始 URL 暂存进 localStorage 由监听器定时重试，成功或终态拒绝即清；
 * 终态拒绝（dc 不匹配/已消费/过期类）仍降级为「刷新会员状态」。
 */

import { useEffect, useRef } from "react";

import { exchangeMemberTicket, fetchMemberStatus } from "@/lib/api";
import { MEMBER_EXCHANGED_EVENT } from "@/lib/contracts";
import { isDesktopRuntime } from "@/lib/desktop";
import { logFrontendError } from "@/lib/frontend-log";
import { canExchange, parseMemberDeepLink, type MemberDeepLink } from "@/lib/member";

export type MemberDeepLinkOutcome =
  | { kind: "exchanged"; member: boolean; email: string }
  | { kind: "refreshed" }
  | { kind: "ignored"; reason: string };

export interface MemberDeepLinkHandlers {
  /** exchange 成功（或降级刷新）后调用：调用方重拉 /api/me 并刷新 UI。 */
  onRefreshed: (outcome: MemberDeepLinkOutcome) => void;
  /** 换票成功但连通测试没过 → ①b 态3（仅 Onboarding 关心）。 */
  onConnectionFailed?: (message: string) => void;
}

// ── 一次性 ticket 的暂存重试（RC1 修复）────────────────────────────────────
// 此前的实现把 URL 先记进 seen 再处理，sidecar 未就绪时换票失败被静默吞掉，
// 一次性 ticket 从此再也无法消费（付费用户注册后卡在未登录）。现在：只有处理
// 落定（成功 / 终态拒绝）才标记 seen；瞬态失败把原始 URL 暂存进 localStorage，
// 由监听器按固定间隔重试。暂存带 10 分钟过期——ticket 本身也会过期，过期后
// 的重试没有意义。

const PENDING_STORAGE_KEY = "aiming-cookie.member.deeplink.pending";
const PENDING_TTL_MS = 10 * 60_000;
/** 暂存重试节奏：挂载后首查 3s，此后每 5s。失败路径是本地快速拒绝，开销可忽略。 */
export const PENDING_FIRST_RETRY_DELAY_MS = 3_000;
export const PENDING_RETRY_INTERVAL_MS = 5_000;
/** 覆盖 device_code 前的暂存票消费重试上限（OnboardingFlow 使用），防死循环。 */
export const PENDING_MAX_ATTEMPTS = 3;

export interface PendingDeepLinkEntry {
  url: string;
  /** 首次暂存时间（TTL 基准；同一 URL 续暂存不刷新）。 */
  stagedAt: number;
  /** 瞬态失败累计次数（供覆盖前的消费尝试设上限）。 */
  attempts: number;
}

/** sidecar 的结构化拒绝码里，重试不会再有不同结果的终态。 */
export function isTerminalExchangeCode(code: string): boolean {
  return (
    code === "no_ticket"
    || code === "dc_mismatch"
    || code === "already_consumed"
    || code === "invalid_ticket"
    || code === "ticket_expired"
    || code === "device_code_invalid"
    || code === "device_code_claimed"
    || code === "device_code_expired"
  );
}

/**
 * 单次处理结果的处置：成功与终态拒绝都已落定（清暂存 + 标记 seen）；只有瞬态
 * 失败保留 URL 稍后重试。network_error 两种形态都安全：sidecar 连不上账号服务
 * 时 ticket 未被消费，重试有效；账号服务 5xx 时 ticket 已被记为已消费，重试会
 * 得到 already_consumed 终态、随即落定。
 */
export function pendingDispositionFor(outcome: MemberDeepLinkOutcome): "settled" | "retry" {
  if (outcome.kind !== "ignored") return "settled";
  return outcome.reason === "runtime_unavailable" || outcome.reason === "network_error"
    ? "retry"
    : "settled";
}

/** 静默丢弃路径的统一可观测痕迹：console 即时可见 + 前端环形日志（打包版落盘）。 */
export function noteMemberDeepLinkEvent(message: string): void {
  console.warn(`[member-deeplink] ${message}`);
  logFrontendError("member-deeplink", message);
}

function pendingStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** 读取未消费的暂存 deep-link；过期/损坏即清除并返回 null。 */
export function peekPendingDeepLink(): PendingDeepLinkEntry | null {
  const storage = pendingStorage();
  if (!storage) return null;
  let raw: string | null = null;
  try {
    raw = storage.getItem(PENDING_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  let entry: PendingDeepLinkEntry | null = null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      const candidate = parsed as Partial<PendingDeepLinkEntry>;
      if (typeof candidate.url === "string" && candidate.url) {
        entry = {
          url: candidate.url,
          stagedAt: typeof candidate.stagedAt === "number" ? candidate.stagedAt : 0,
          attempts: typeof candidate.attempts === "number" ? candidate.attempts : 0,
        };
      }
    }
  } catch {
    entry = null;
  }
  if (!entry || !entry.stagedAt || Date.now() - entry.stagedAt > PENDING_TTL_MS) {
    clearPendingDeepLink();
    return null;
  }
  return entry;
}

/** 清除暂存；传 url 时只清除同一 URL 的暂存（避免误清新进来的其他票）。 */
export function clearPendingDeepLink(url?: string): void {
  const storage = pendingStorage();
  if (!storage) return;
  if (url === undefined) {
    try {
      storage.removeItem(PENDING_STORAGE_KEY);
    } catch {
      /* 存储不可用：无需清理 */
    }
    return;
  }
  const entry = peekPendingDeepLink();
  if (entry && entry.url === url) {
    try {
      storage.removeItem(PENDING_STORAGE_KEY);
    } catch {
      /* 存储不可用：无需清理 */
    }
  }
}

/** 暂存一条未成功消费的 deep-link；同一 URL 续暂存只累计次数、不刷新 TTL 基准。 */
export function stagePendingDeepLink(url: string): void {
  const storage = pendingStorage();
  if (!storage) return;
  const current = peekPendingDeepLink();
  const entry: PendingDeepLinkEntry = current && current.url === url
    ? { url, stagedAt: current.stagedAt, attempts: current.attempts + 1 }
    : { url, stagedAt: Date.now(), attempts: 0 };
  try {
    storage.setItem(PENDING_STORAGE_KEY, JSON.stringify(entry));
  } catch {
    /* 存储不可用：退回「等浏览器重试 / single-instance 重播」的旧行为 */
  }
}

/**
 * 处理一条已解析的 deep-link。对调用方暴露的就是这个函数，便于单测与手动触发
 * （「点此重新打开」不重走本函数，只重开浏览器页面）。
 */
export async function handleMemberDeepLink(
  link: MemberDeepLink,
  handlers: MemberDeepLinkHandlers,
): Promise<MemberDeepLinkOutcome> {
  // §3.2 触发 2：无 ticket（支付成功回跳的兜底形态）或不可 exchange → 只刷新。
  if (link.scene === "open" || !canExchange(link)) {
    await fetchMemberStatus().catch(() => null);
    const outcome: MemberDeepLinkOutcome = { kind: "refreshed" };
    handlers.onRefreshed(outcome);
    return outcome;
  }
  try {
    const result = await exchangeMemberTicket({ ticket: link.ticket, dc: link.dc });
    if (!result.ok) {
      // 校验未过一律降级为刷新，不报错（§3.3-3/4/5/7）；但必须留观测痕迹，
      // 不再无声吞（RC1 修复要求）。
      noteMemberDeepLinkEvent(`exchange rejected: ${result.code}; downgrade to refresh`);
      await fetchMemberStatus().catch(() => null);
      const outcome: MemberDeepLinkOutcome = { kind: "ignored", reason: result.code };
      handlers.onRefreshed(outcome);
      return outcome;
    }
    if (result.connection_ok === false && handlers.onConnectionFailed) {
      handlers.onConnectionFailed(result.connection_message ?? "连接测试未通过。");
    }
    // 登录成功广播（窗口事件，同 lib/contracts 其它内部事件模式）：AppShell
    // 据此把未订阅用户直达设置页官方档详情。无 window 的测试环境静默跳过。
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(MEMBER_EXCHANGED_EVENT, { detail: { member: result.member } }));
    }
    const outcome: MemberDeepLinkOutcome = {
      kind: "exchanged",
      member: result.member,
      email: result.user.email,
    };
    handlers.onRefreshed(outcome);
    return outcome;
  } catch (error) {
    // 桌面运行时/sidecar 暂不可达：ticket 未被消费，留给调用方暂存重试。
    noteMemberDeepLinkEvent(
      `exchange deferred (runtime unavailable): ${error instanceof Error ? error.message : String(error)}`,
    );
    const outcome: MemberDeepLinkOutcome = { kind: "ignored", reason: "runtime_unavailable" };
    handlers.onRefreshed(outcome);
    return outcome;
  }
}

/**
 * 桌面端挂载即监听：冷启动 URL（插件 get_current）+ 运行中事件（single-instance
 * 转发的 argv 广播）。浏览器预览形态不装监听。
 */
export function useMemberDeepLinks(handlers: MemberDeepLinkHandlers, enabled = true): void {
  // 同一条 URL 只处理一次（处理落定后才标记）：single-instance 与插件可能对
  // 同一 URL 各广播一次；瞬态失败不标记，靠暂存重试与重播再次触发处理。
  const seenRef = useRef(new Set<string>());
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    if (!enabled || !isDesktopRuntime()) return undefined;
    let disposed = false;
    let unlisten: (() => void) | null = null;

    const process = async (urls: string[] | null): Promise<void> => {
      const fresh = (urls ?? []).filter((url) => !seenRef.current.has(url));
      if (!fresh.length) return;
      // 其余 scheme/host/解析失败一律静默忽略（§3.3-1）：不是一次性凭证，
      // 直接标记跳过。
      const sourceUrl = fresh.find((url) => parseMemberDeepLink(url) !== null);
      if (!sourceUrl) {
        for (const url of fresh) seenRef.current.add(url);
        return;
      }
      const link = parseMemberDeepLink(sourceUrl);
      if (!link) return; // 不可达：sourceUrl 已确保可解析
      const outcome = await handleMemberDeepLink(link, handlersRef.current);
      if (pendingDispositionFor(outcome) === "retry") {
        // RC1：ticket 未被消费，暂存等 sidecar 就绪后重试；不标记 seen，
        // 浏览器重试 / single-instance 重播同样会再触发一次处理。
        stagePendingDeepLink(sourceUrl);
        return;
      }
      for (const url of fresh) seenRef.current.add(url);
      clearPendingDeepLink(sourceUrl);
    };

    // 暂存重试（RC1）：sidecar 未就绪的首次失败靠这里兜住。轮询是对「就绪
    // 信号」的最稳替代（与捕获恢复的退避重试同思路），失败只是一次本地快速拒绝。
    const retryPending = () => {
      if (disposed) return;
      const entry = peekPendingDeepLink();
      if (!entry || seenRef.current.has(entry.url)) return;
      void process([entry.url]);
    };
    const firstRetry = window.setTimeout(retryPending, PENDING_FIRST_RETRY_DELAY_MS);
    const retryTimer = window.setInterval(retryPending, PENDING_RETRY_INTERVAL_MS);

    void (async () => {
      try {
        const { getCurrent, onOpenUrl } = await import("@tauri-apps/plugin-deep-link");
        if (disposed) return;
        unlisten = await onOpenUrl((urls) => {
          void process(urls);
        });
        if (disposed) {
          unlisten();
          unlisten = null;
          return;
        }
        // 冷启动由协议唤起：argv 里的 URL 在插件 setup 时已读入 current。
        const current = await getCurrent();
        if (!disposed) await process(current);
      } catch {
        // 插件不可用（浏览器预览 / 未注册）时静默：deep-link 本就是可失败路径。
      }
    })();

    return () => {
      disposed = true;
      unlisten?.();
      window.clearTimeout(firstRetry);
      window.clearInterval(retryTimer);
    };
  }, [enabled]);
}
