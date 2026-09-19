"use client";

/**
 * deep-link 监听（契约 §3.3-8/9）：**不只在 Onboarding 阶段监听**——已进主界面
 * 同样要处理 scene=subscribe/booster（刷新 /api/me）与 scene=login（exchange 后刷新）。
 *
 * 单实例去重（§3.3-5）分两层：
 * 1. 本 hook 对同一条 URL 字符串只在会话内处理一次（浏览器重试 / 插件重复广播）；
 * 2. sidecar 的 member-auth 记录已消费 ticket 并按 dc 绑定校验（§3.3-4），
 *    重复 exchange 会被结构化拒绝，不重复打服务端。
 *
 * 失败一律静默（§3.3-7：收不到 deep-link 是常态）：`no_ticket` / `dc_mismatch` /
 * `already_consumed` / 过期都只降级为「刷新会员状态」，绝不弹错误。
 */

import { useEffect, useRef } from "react";

import { exchangeMemberTicket, fetchMemberStatus } from "@/lib/api";
import { canExchange, firstMemberDeepLink, type MemberDeepLink } from "@/lib/member";
import { isDesktopRuntime } from "@/lib/desktop";

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
      // 校验未过一律降级为刷新，不报错（§3.3-3/4/5/7）。
      await fetchMemberStatus().catch(() => null);
      const outcome: MemberDeepLinkOutcome = { kind: "ignored", reason: result.code };
      handlers.onRefreshed(outcome);
      return outcome;
    }
    if (result.connection_ok === false && handlers.onConnectionFailed) {
      handlers.onConnectionFailed(result.connection_message ?? "连接测试未通过。");
    }
    const outcome: MemberDeepLinkOutcome = {
      kind: "exchanged",
      member: result.member,
      email: result.user.email,
    };
    handlers.onRefreshed(outcome);
    return outcome;
  } catch {
    // 桌面运行时/sidecar 暂不可达：保持现状，等下一次触发。
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
  // 同一条 URL 只处理一次：single-instance 与插件可能对同一 URL 各广播一次。
  const seenRef = useRef(new Set<string>());
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    if (!enabled || !isDesktopRuntime()) return undefined;
    let disposed = false;
    let unlisten: (() => void) | null = null;

    const process = async (urls: string[] | null) => {
      const fresh = (urls ?? []).filter((url) => !seenRef.current.has(url));
      if (!fresh.length) return;
      for (const url of fresh) seenRef.current.add(url);
      const link = firstMemberDeepLink(fresh);
      if (!link) return; // 其余 scheme/host/解析失败一律静默忽略（§3.3-1）。
      await handleMemberDeepLink(link, handlersRef.current);
    };

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
    };
  }, [enabled]);
}
