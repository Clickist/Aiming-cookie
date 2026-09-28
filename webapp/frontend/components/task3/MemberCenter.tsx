"use client";

/**
 * 用户中心（线框 ②c，两态）——点击左下角账号卡后右侧主区域整体切换。
 *
 * 导航模式与训练历史页一致：顶部「← 返回」回对话。内容严格照线框：
 * 黑色余量大条（订阅池单条百分比 + 加油包小行＝两池全貌）→ 管理订阅/申请退款
 * → 加油包购买卡（余额未尽置灰）→ 调用记录卡 → 账户卡（退出登录双去向说明）。
 *
 * 商业化红线：本页不出现价格档位选择、不出现升级按钮；「管理订阅 / 申请退款 /
 * 购买加油包」一律跳系统浏览器（铁律①：客户端内无支付界面）。
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { getCoachUsageRecords, logoutMemberAccount, type CoachUsageRecordsResponse } from "@/lib/api";
import { openExternalUrl } from "@/lib/desktop";
import { useLocale, useT } from "@/lib/i18n";
import { ACCOUNTS_BASE_URL } from "@/lib/infra-urls";
import { notifyMemberStateChanged } from "@/lib/member-state";
import { MEMBER_COPY, formatMemberDate, planLabel } from "@/lib/member";
import { cacheHitRate, formatTokenCount, formatUsageTime, usageNumbers } from "@/lib/member-usage";
import { parseTrialState } from "@/lib/trial";
import type { MemberMe } from "@/lib/types";
import { IconChevronDown, IconChevronLeft } from "@/ui/icons";
import { Button, IconButton, Notice } from "@/ui/primitives";
import { startWindowDraggingOnBackground } from "@/components/task3/TauriWindowControls";

/** 账号中心网址（契约 §7.1-12；②c 的「管理订阅 / 申请退款」都跳这里）。域名构建期注入。 */
const ACCOUNT_CENTER_URL = `${ACCOUNTS_BASE_URL}/account`;
const ACCOUNT_BILLING_URL = `${ACCOUNTS_BASE_URL}/account/billing`;
const PAY_BOOSTER_URL = `${ACCOUNTS_BASE_URL}/pay#booster`;
const PAY_URL = `${ACCOUNTS_BASE_URL}/pay`;

/** 调用记录默认显示前 5 条（更多记录由「查看更多」展开，服务端最多给 200 条）。 */
const USAGE_VISIBLE_ROWS = 5;

/**
 * 调用记录卡（用户中心线框）：本机 Coach AI 调用的逐笔明细 + 本月汇总。
 *
 * 数据源＝sidecar 的 GET /v1/usage/records（Pi 会话 JSONL 里 assistant 消息自带的
 * model/provider/usage，纯本地读取，不依赖任何服务端）。挂载时取数、卸载时中止；
 * 取数失败整卡不渲染（fail-soft——浏览器 mock 环境没有 sidecar，不显示报错）。
 */
function MemberUsageCard() {
  const t = useT();
  const { locale } = useLocale();
  const [data, setData] = useState<CoachUsageRecordsResponse | null>(null);
  // expanded 不随数据刷新复位是有意的：本卡挂载即一次性取数（无刷新/retry 路径），
  // 若未来加入刷新逻辑需重审（expanded=true 且新数据不足 5 条时按钮会消失）。
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getCoachUsageRecords({ limit: 50, signal: controller.signal })
      .then((next) => {
        if (!controller.signal.aborted) setData(next);
      })
      .catch(() => {
        // fail-soft：整卡不渲染。
      });
    return () => controller.abort();
  }, []);

  if (!data) return null;
  const month = data.month;
  // 命中率与 sidecar 同一口径（cache_read/(input+cache_read)，分母 0 → null），
  // 用本地助手重算一遍，保证 chip 与服务端汇总逐字一致。
  const hitRate = cacheHitRate(month.input_tokens, month.cache_read_tokens);
  // 当月月份取自服务端 key（"2026-09"），只用于 zh 文案的「N月」。
  const monthNumber = Number(month.key.slice(5)) || 0;
  const now = new Date();
  const visible = expanded ? data.records : data.records.slice(0, USAGE_VISIBLE_ROWS);

  return (
    <section className="task3-member-usage-card">
      <div className="task3-member-usage-head">
        <strong>{t("member.center.usage.title")}</strong>
        <span>{t("member.center.usage.monthCount", { count: month.count, month: monthNumber })}</span>
      </div>
      <div className="task3-member-usage-chips">
        <span className="task3-member-usage-chip">
          {t("member.center.usage.statInput", { tokens: formatTokenCount(month.input_tokens) })}
        </span>
        <span className="task3-member-usage-chip">
          {t("member.center.usage.statOutput", { tokens: formatTokenCount(month.output_tokens) })}
        </span>
        <span className="task3-member-usage-chip">
          {t("member.center.usage.statCacheHit", { rate: hitRate ?? 0 })}
        </span>
      </div>
      {data.records.length === 0 ? (
        <p className="task3-member-usage-empty">{t("member.center.usage.empty")}</p>
      ) : (
        <>
          <ul className="task3-member-usage-list">
            {visible.map((record, index) => {
              const numbers = usageNumbers(record);
              return (
                <li className="task3-member-usage-row" key={`${record.session_id}-${record.timestamp}-${index}`}>
                  <span className="task3-member-usage-time">{formatUsageTime(record.timestamp, now, locale)}</span>
                  <span className="task3-member-usage-session">
                    {record.session_title ?? t("member.center.usage.untitled")}
                  </span>
                  <span className="task3-member-usage-model">{record.model ?? "—"}</span>
                  <span className="task3-member-usage-tokens">
                    {t("member.center.usage.rowTokens", {
                      input: formatTokenCount(numbers.input),
                      output: formatTokenCount(numbers.output),
                      cache: formatTokenCount(numbers.cacheRead),
                    })}
                  </span>
                </li>
              );
            })}
          </ul>
          {data.records.length > USAGE_VISIBLE_ROWS ? (
            <button
              className="task3-member-usage-toggle"
              onClick={() => setExpanded((current) => !current)}
              type="button"
            >
              {expanded ? t("member.center.usage.less") : t("member.center.usage.more")}
              <IconChevronDown className={expanded ? "task3-member-usage-toggle-icon is-open" : "task3-member-usage-toggle-icon"} />
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}

export function MemberCenter({
  me,
  resolved = true,
  onLogout,
}: {
  me: MemberMe | null;
  /** 服务端已给出确定答案；false + me=null = 加载中，显示获取态而非「未登录」引导（点点 0928）。 */
  resolved?: boolean;
  /** 退出登录完成后回调（AppShell 负责刷新 Provider 能力状态）。 */
  onLogout?: () => Promise<void> | void;
}) {
  const router = useRouter();
  const t = useT();
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
      setMessage(t("member.center.logoutFailed"));
    } finally {
      setLoggingOut(false);
    }
  }, [onLogout, router, t]);

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
  // AC 验证闸试用卡（宽松解析，缺失=不渲染；verified=「已验证 ✓ 可订阅」）。
  const trial = parseTrialState(me);

  return (
    <div className="task3-member-center">
      <div className="task3-member-center-head" onMouseDown={startWindowDraggingOnBackground}>
        <div className="task3-member-center-head-inner">
          <IconButton label={t("member.center.backToChat")} onClick={() => router.push("/")} size="compact" title={t("member.center.backToChat")}>
            <IconChevronLeft />
          </IconButton>
          <strong>{MEMBER_COPY.centerTitle}</strong>
        </div>
      </div>

      <div className="task3-member-center-body">
        {me && unsubscribed ? (
          <>
            {trial ? (
              <div className="task3-member-card-row" data-trial-verified={trial.verified || undefined}>
                <div>
                  <strong>{t("trial.center.title")}</strong>
                  <p>
                    {trial.verified
                      ? t("trial.center.verified")
                      : t("trial.center.remaining", { analyses: trial.analysesRemaining, questions: trial.questionsRemaining })}
                  </p>
                  <p>{trial.verified ? t("trial.center.verifiedHint") : t("trial.center.hint")}</p>
                </div>
              </div>
            ) : null}
            <div className="task3-member-card-row">
              <div>
                <strong>{MEMBER_COPY.notSubscribedTitle}</strong>
                <p>{MEMBER_COPY.notSubscribedCenterBody}</p>
              </div>
              <Button onClick={() => void openExternalUrl(PAY_URL)} size="compact" variant="primary">
                {MEMBER_COPY.openSubscribePage}
              </Button>
            </div>
            {/* 未订阅分支同样渲染调用记录卡：数据是纯本地调用明细，与订阅态无关
               （0928 真机实测点点当前账号即未订阅态，只放会员分支会整卡不可见）。 */}
            <MemberUsageCard />
          </>
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
                  {t("member.center.boosterBuyAction")}
                </Button>
              ) : (
                <span className="task3-member-disabled" aria-disabled="true">{MEMBER_COPY.boosterBuyDisabled}</span>
              )}
            </div>

            <MemberUsageCard />

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
        ) : resolved ? (
          <div className="task3-member-empty">
            <p>{t("member.center.emptyBody")}</p>
            <Button onClick={() => router.push("/settings")} variant="secondary">{t("member.center.openSettings")}</Button>
          </div>
        ) : (
          <div className="task3-member-empty">
            <p>{t("member.center.loading")}</p>
          </div>
        )}
        {message ? <Notice tone="warning">{message}</Notice> : null}
      </div>
    </div>
  );
}
