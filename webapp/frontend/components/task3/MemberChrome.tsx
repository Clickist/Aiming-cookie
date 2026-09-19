"use client";

/**
 * Aiming Cookie 会员档 — 共享 UI 片段（②/②b/④/⑧/⑨ 在 AppleShell 与 CoachPanel
 * 两处复用，抽成一份；文案全部照抄 `design/member-pay/wireframes.html` v3.3）。
 *
 * 商业化红线（铁律②③④）：本文件不含任何登录表单、套餐选择、支付入口或价格；
 * ④/⑨ 只陈述事实 + 指路（左下角账户 / 设置），零升级按钮、零金额。
 */

import { MEMBER_COPY, formatMemberDate } from "@/lib/member";
import type { MemberMe } from "@/lib/types";

/**
 * ⑨ 订阅失效两态 / ⑧ 扣款失败：教练页与主区共用的 inline 提示条。
 * 一次性、可关闭（✕），关闭后本轮不再出现（状态由左下角 chip 常驻承载）。
 */
export function MemberNotice({
  tone,
  onDismiss,
  children,
}: {
  tone: "warn" | "error";
  onDismiss: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="task3-member-notice" data-tone={tone} role="status">
      <span className="task3-member-notice-text">{children}</span>
      <button
        aria-label="关闭提示"
        className="task3-member-notice-close"
        onClick={onDismiss}
        type="button"
      >
        ✕
      </button>
    </div>
  );
}

/**
 * ⑨/⑧ 提示内容推导：按优先级返回当前该展示的一条提示（无则 null）。
 *
 * 依据线框：订阅到期但有加油包 → ⑨ 左（黄，续命）；双池皆空 → ⑨ 右（红）；
 * 扣款失败 → ⑧（黄）。提示是一次性、可关闭的。
 */
export type MemberNoticeKey = "dunning" | "booster" | "lost";

export function memberNotice(me: MemberMe | null): MemberNoticeKey | null {
  if (!me) return null;
  if (me.status === "expired" || me.status === "refunded" || (!me.member && me.status !== "none")) {
    return (me.pools.boost?.remaining ?? 0) > 0 ? "booster" : "lost";
  }
  if (me.dunning) return "dunning";
  return null;
}

/** 提示文案（线框照抄；日期用 period_end 的月-日）。 */
export function memberNoticeText(key: MemberNoticeKey, me: MemberMe, endDate: string): string {
  if (key === "dunning") return MEMBER_COPY.dunning(endDate);
  if (key === "booster") return MEMBER_COPY.boosterActive(endDate, me.pools.boost?.pct ?? 0);
  return MEMBER_COPY.connectionLost(endDate);
}

/** `period_end` → 线框口径的月-日（提示条与 chip 共用）。 */
export function memberEndDate(me: MemberMe | null): string {
  return formatMemberDate(me?.period_end ?? null);
}
