"use client";

/**
 * 用户中心（线框 ②c，两态）——点击左下角账号卡后右侧主区域整体切换。
 *
 * 导航模式与训练历史页一致：顶部「← 返回」回对话。内容严格照线框：
 * 黑色余量大条（订阅池单条百分比 + 加油包小行＝两池全貌）→ 管理订阅/申请退款
 * → 加油包购买卡（余额未尽置灰）→ 账户卡（退出登录双去向说明）。
 *
 * 商业化红线：本页不出现价格档位选择、不出现升级按钮；「管理订阅 / 申请退款 /
 * 购买加油包」一律跳系统浏览器（铁律①：客户端内无支付界面）。
 */

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { logoutMemberAccount } from "@/lib/api";
import { openExternalUrl } from "@/lib/desktop";
import { notifyMemberStateChanged } from "@/lib/member-state";
import { MEMBER_COPY, formatMemberDate, planLabel } from "@/lib/member";
import type { MemberMe } from "@/lib/types";
import { IconChevronLeft } from "@/ui/icons";
import { Button, IconButton, Notice } from "@/ui/primitives";
import { startWindowDraggingOnBackground } from "@/components/task3/TauriWindowControls";

/** 账号中心网址（契约 §7.1-12；②c 的「管理订阅 / 申请退款」都跳这里）。 */
const ACCOUNT_CENTER_URL = "https://accounts.gearclickist.com/account";
const ACCOUNT_BILLING_URL = "https://accounts.gearclickist.com/account/billing";
const PAY_BOOSTER_URL = "https://accounts.gearclickist.com/pay#booster";
const PAY_URL = "https://accounts.gearclickist.com/pay";

export function MemberCenter({
  me,
  onLogout,
}: {
  me: MemberMe | null;
  /** 退出登录完成后回调（AppShell 负责刷新 Provider 能力状态）。 */
  onLogout?: () => Promise<void> | void;
}) {
  const router = useRouter();
  const [loggingOut, setLoggingOut] = useState(false);
  const [message, setMessage] = useState("");

  const goBilling = useCallback(() => {
    void openExternalUrl(ACCOUNT_BILLING_URL);
  }, []);

  const handleLogout = useCallback(async () => {
    setLoggingOut(true);
    setMessage("");
    try {
      const result = await logoutMemberAccount();
      // 广播给所有 useMemberState（含左下角 chip）立刻重拉。
      notifyMemberStateChanged();
      await onLogout?.();
      // ④b：不打回 Onboarding——有 BYOK 时自动切过去照常用；没有则主界面照常进、
      // Coach 置灰指路。这里只负责退出后回对话页。
      if (result.fallback_profile_id === null) {
        setMessage(MEMBER_COPY.noProvider);
      }
      router.push("/");
    } catch {
      setMessage("退出登录未完成，请稍后重试。");
    } finally {
      setLoggingOut(false);
    }
  }, [onLogout, router]);

  const plan = planLabel(me?.plan ?? null);
  // 三种订阅形态（线框 ②c 的两态 + ⑨ 的失效态）：生效中 / 已取消未到期 / 已结束。
  const ended = me !== null && (me.status === "expired" || me.status === "refunded");
  const canceled = me?.cancel_at_period_end === true || me?.status === "canceled";
  // 已登录但从未订阅（status=none）：不是会员态，走未订阅卡（①b 态1 的同一动作）。
  const unsubscribed = me !== null && !me.member && me.status === "none";
  const subPct = me?.pools.sub?.pct ?? 0;
  const boostPct = me?.pools.boost?.pct ?? 0;
  const boostRemaining = me?.pools.boost?.remaining ?? 0;
  const endDate = formatMemberDate(me?.period_end ?? null);

  return (
    <div className="task3-member-center">
      <div className="task3-member-center-head" onMouseDown={startWindowDraggingOnBackground}>
        <div className="task3-member-center-head-inner">
          <IconButton label="返回对话" onClick={() => router.push("/")} size="compact" title="返回对话">
            <IconChevronLeft />
          </IconButton>
          <strong>{MEMBER_COPY.centerTitle}</strong>
        </div>
      </div>

      <div className="task3-member-center-body">
        {me && unsubscribed ? (
          <div className="task3-member-card-row">
            <div>
              <strong>{MEMBER_COPY.notSubscribedTitle}</strong>
              <p>{MEMBER_COPY.notSubscribedCenterBody}</p>
            </div>
            <Button onClick={() => void openExternalUrl(PAY_URL)} size="compact" variant="primary">
              {MEMBER_COPY.openSubscribePage}
            </Button>
          </div>
        ) : me ? (
          <>
            <div className="task3-member-hero">
              <div className="task3-member-hero-head">
                <strong>
                  {ended
                    ? MEMBER_COPY.memberEnded(plan)
                    : canceled ? MEMBER_COPY.memberCanceled(plan) : MEMBER_COPY.memberActive(plan)}
                </strong>
                <span>
                  {ended
                    ? MEMBER_COPY.endedAt(endDate)
                    : canceled ? MEMBER_COPY.usableUntil(endDate) : MEMBER_COPY.autoRenew(endDate)}
                </span>
              </div>
              <div className="task3-member-hero-bar">
                <i style={{ width: `${Math.max(0, Math.min(100, subPct))}%` }} />
              </div>
              <div className="task3-member-hero-values">
                <b>{MEMBER_COPY.remainPrefix} {subPct}%</b>
                <span>
                  {ended
                    ? MEMBER_COPY.cycleEnded
                    : canceled ? MEMBER_COPY.cycleStillUsable(endDate) : MEMBER_COPY.quotaPerCycle}
                </span>
              </div>
              {boostRemaining > 0 ? (
                <div className="task3-member-hero-booster">
                  <span>{MEMBER_COPY.boosterRow}</span>
                  <span>
                    <span aria-hidden="true" className="task3-member-hero-mini">
                      <i style={{ width: `${Math.max(0, Math.min(100, boostPct))}%` }} />
                    </span>
                    {MEMBER_COPY.boosterRemain(boostPct)}
                  </span>
                </div>
              ) : null}
            </div>

            <div className="task3-member-actions">
              <Button onClick={() => (ended ? void openExternalUrl(PAY_URL) : goBilling())} variant="primary">
                {ended
                  ? MEMBER_COPY.resubscribe
                  : canceled ? MEMBER_COPY.resumeSubscription : MEMBER_COPY.manageSubscription}
              </Button>
              <Button onClick={goBilling} variant="secondary">{MEMBER_COPY.requestRefund}</Button>
            </div>
            <p className="task3-member-hint">
              {ended ? MEMBER_COPY.resubscribeHint : MEMBER_COPY.webHint}
            </p>

            <div className="task3-member-card-row">
              <div>
                <strong>{MEMBER_COPY.boosterBuy}</strong>
                <p>{MEMBER_COPY.boosterBuyBody}</p>
              </div>
              {me.boost_buyable ? (
                <Button onClick={() => void openExternalUrl(PAY_BOOSTER_URL)} size="compact" variant="secondary">
                  购买加油包
                </Button>
              ) : (
                <span className="task3-member-disabled" aria-disabled="true">{MEMBER_COPY.boosterBuyDisabled}</span>
              )}
            </div>

            <div className="task3-member-account-card">
              <div className="task3-member-account-main">
                <strong>{MEMBER_COPY.accountCard}</strong>
                <div className="task3-member-account-row">
                  <span>{MEMBER_COPY.accountEmail}</span>
                  <span>{me.user.email}</span>
                </div>
                <div className="task3-member-account-row">
                  <span>{MEMBER_COPY.accountPlan}</span>
                  <span>
                    {ended
                      ? MEMBER_COPY.planEnded(plan)
                      : canceled ? MEMBER_COPY.planEndingHere(plan) : MEMBER_COPY.planOngoing(plan)}
                  </span>
                </div>
              </div>
              {/* 退出登录：右侧次级按钮（描边、error 文字），说明在按钮下方小字。
                  不打回 Onboarding——有 BYOK 自动切、没有则 Coach 置灰指路（④b）。 */}
              <div className="task3-member-logout-block">
                <Button
                  disabled={loggingOut}
                  onClick={() => void handleLogout()}
                  size="compact"
                  variant="secondary"
                  className="task3-member-logout-button"
                >
                  {MEMBER_COPY.logoutButton}
                </Button>
                <p className="task3-member-logout-note">{MEMBER_COPY.logoutNote}</p>
              </div>
            </div>
          </>
        ) : (
          <div className="task3-member-empty">
            <p>还没有登录 Aiming Cookie。在下方设置里连接会员档或自定义 Provider 后即可使用 Coach。</p>
            <Button onClick={() => router.push("/settings")} variant="secondary">打开设置</Button>
          </div>
        )}
        {message ? <Notice tone="warning">{message}</Notice> : null}
      </div>
    </div>
  );
}
