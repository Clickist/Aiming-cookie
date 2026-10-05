/**
 * Aiming Cookie 会员档的纯逻辑（无 React、无请求）。
 *
 * 契约事实源：`accounts/INTERFACE.md` v2 §3（deep-link 九条校验 + 两次触发差异）
 * 与 §7.1-8（`/api/me` 冻结 schema）。线框文案事实源：
 * `design/member-pay/wireframes.html`（屏 ①②②b②c④④b⑨，v3.3，照抄不发明）。
 *
 * 本模块只做解析与状态推导，不发请求、不碰凭据：
 * - deep-link 解析严格遵守 §3.3 九条（其余 scheme/host/解析失败静默忽略）；
 * - 会员/百分比永不从 URL 推断（§3.3-6），只接受调用方传入的 `/api/me` 结果；
 * - 余量只出百分比，分档阈值绿 #16875b / 橙 #e8930c / 红 #c53442（<10% 红，10~30% 橙）。
 */

import { t } from "./i18n/core";
import type { MemberMe, MemberPool } from "./types";

/** deep-link scheme（契约 §3.1，固定小写）。 */
export const MEMBER_DEEP_LINK_SCHEME = "aimingcookie";
/** v2 只有 auth 这一个 host（契约 §3.1）。 */
export const MEMBER_DEEP_LINK_HOST = "auth";
/** scene 枚举（契约 §3.1）；未知值按 `open` 处理（§3.3-2）。 */
export const MEMBER_SCENES = ["login", "subscribe", "booster", "open"] as const;
export type MemberScene = (typeof MEMBER_SCENES)[number];
/** 整串长度上限（契约 §3.1）。 */
export const MEMBER_DEEP_LINK_MAX_LENGTH = 2048;

export interface MemberDeepLink {
  scene: MemberScene;
  ticket: string | null;
  dc: string | null;
  item: "standard" | "plus" | "booster" | null;
}

/**
 * 解析 deep-link（契约 §3.3-1/2）。
 *
 * 返回 `null` 表示「静默忽略」：scheme/host 不符、解析失败、超长、无 URL——
 * 一律不弹错误。`scene` 非法时按 `open` 处理（不是忽略）。
 */
export function parseMemberDeepLink(raw: string | null | undefined): MemberDeepLink | null {
  const value = typeof raw === "string" ? raw.trim() : "";
  if (!value || value.length > MEMBER_DEEP_LINK_MAX_LENGTH) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  // URL 解析把小写化了 scheme，但非特殊 scheme 的 host 保留原样——大小写
  // 不敏感比较，`AUTH` 也认（协议名大小写不敏感是平台惯例）。
  if (url.protocol !== `${MEMBER_DEEP_LINK_SCHEME}:`) return null;
  if (url.hostname.toLowerCase() !== MEMBER_DEEP_LINK_HOST) return null;
  const sceneRaw = url.searchParams.get("scene") ?? "";
  const scene: MemberScene = (MEMBER_SCENES as readonly string[]).includes(sceneRaw)
    ? (sceneRaw as MemberScene)
    : "open";
  const itemRaw = url.searchParams.get("item");
  const item = itemRaw === "standard" || itemRaw === "plus" || itemRaw === "booster" ? itemRaw : null;
  const ticket = url.searchParams.get("ticket");
  const dc = url.searchParams.get("dc");
  return {
    scene,
    ticket: ticket && ticket.trim() ? ticket.trim() : null,
    // §3.3-3：ticket 与 dc 必须同时存在；只有一个 → 整个 ticket 降级为「无 ticket」。
    dc: ticket && ticket.trim() && dc && dc.trim() ? dc.trim() : null,
    item,
  };
}

/** 从深链批次里取第一条可解析的 URL（single-instance 可能一次送多条）。 */
export function firstMemberDeepLink(urls: readonly string[] | null | undefined): MemberDeepLink | null {
  if (!urls) return null;
  for (const url of urls) {
    const parsed = parseMemberDeepLink(url);
    if (parsed) return parsed;
  }
  return null;
}

/** 是否可尝试 exchange（§3.3-3：ticket + dc 成对）。 */
export function canExchange(link: MemberDeepLink | null): link is MemberDeepLink & { ticket: string; dc: string } {
  return Boolean(link && link.ticket && link.dc);
}

/** 余量分档（线框：绿 / 橙 / 红）。<10% 红、10~30% 橙、其余绿。 */
export type PoolTier = "good" | "warn" | "low";

export function poolTier(pct: number): PoolTier {
  const value = Number.isFinite(pct) ? Math.max(0, Math.min(100, pct)) : 0;
  if (value < 10) return "low";
  if (value <= 30) return "warn";
  return "good";
}

/** 分档色（LIGHT_TOKENS 之外的三档语义色，线框与 token 双处同值）。 */
export const POOL_TIER_COLORS: Record<PoolTier, string> = {
  good: "#16875b",
  warn: "#e8930c",
  low: "#c53442",
};

/** 当前池单条（chip 只显示这一条）——`current_pool` 为空则 null。 */
export function currentPool(me: MemberMe | null): MemberPool | null {
  if (!me) return null;
  return me.current_pool === "sub" ? me.pools.sub : me.current_pool === "boost" ? me.pools.boost : null;
}

/** 双池皆空（④/⑨ 右态触发条件）：两池皆无或 remaining 全为 0。 */
export function bothPoolsEmpty(me: MemberMe | null): boolean {
  if (!me) return false;
  const sub = me.pools.sub?.remaining ?? 0;
  const boost = me.pools.boost?.remaining ?? 0;
  return sub <= 0 && boost <= 0;
}

/** 加油包是否还有余量（⑨ 左态：订阅失效但加油包没烧完，服务不断）。 */
export function boosterAlive(me: MemberMe | null): boolean {
  return (me?.pools.boost?.remaining ?? 0) > 0;
}

/** 订阅是否已失效（⑨ 触发条件：非 member 但凭证在 = 到期/退款/断订）。 */
export function subscriptionEnded(me: MemberMe | null): boolean {
  if (!me) return false;
  return me.status === "expired" || me.status === "refunded" || (!me.member && me.status !== "none");
}

/**
 * 是否为 Stripe 连续订阅（recurring）。按月一次性购买（mode=payment）为 false。
 * 老 Worker 响应缺 `recurring` 字段时按 true 处理：维持既有「连续包月」文案不炸（向后兼容）。
 */
export function isRecurring(me: MemberMe | null): boolean {
  return me?.recurring ?? true;
}

// ── 展示文案（照抄线框，不自行发明）────────────────────────────────────────
//
// 用词红线：全表禁用「云教练」（教练跑在用户本地，订阅卖的是模型额度）；
// 金额只出现在加油包固定标价「加油包 ¥10」（线框原文），其余一律只出百分比。
//
// i18n 批 1：文案本体已入 lib/i18n/zh-CN.ts（member.* 命名空间）。本对象保留
// 原属性名做兼容门面——纯文本属性用 getter、模板属性保持函数，取值时经 t()
// 解析当前 locale，不在模块加载期固化。

export const MEMBER_COPY = {
  /** ① 下拉里的会员档条目（线框 ① 的菜单项文案拆成主名 + 副行）。 */
  get providerDropdownLabel() { return t("member.provider.relayLabel"); },
  get providerDropdownHint() { return t("member.dropdown.hint"); },
  /** ①a 等待页。 */
  get waitingTitle() { return t("member.waiting.title"); },
  get waitingBody() { return t("member.waiting.body"); },
  get waitingReopenHint() { return t("member.waiting.reopenHint"); },
  get reopenBrowser() { return t("member.waiting.reopenBrowser"); },
  /** ①a 连通成功行（模型名固定，来自模型锁）。 */
  get connected() { return t("member.waiting.connected"); },
  /** ①b 态1 未订阅。 */
  get notSubscribedPrefix() { return t("member.notSubscribed.prefix"); },
  get notSubscribedSuffix() { return t("member.notSubscribed.suffix"); },
  get notSubscribedBody() { return t("member.notSubscribed.body"); },
  get notSubscribedBodyLine2() { return t("member.notSubscribed.bodyLine2"); },
  get openSubscribePage() { return t("member.notSubscribed.openPage"); },
  /** ①b 态2 老会员直连。 */
  get alreadyMember() { return t("member.already.headline"); },
  get alreadyMemberBody() { return t("member.already.body"); },
  get alreadyMemberBodyLine2() { return t("member.already.bodyLine2"); },
  /** ①b 态3 连通失败。 */
  get testFailed() { return t("member.testFailed.headline"); },
  get testFailedBody() { return t("member.testFailed.body"); },
  get testFailedBodyLine2() { return t("member.testFailed.bodyLine2"); },
  get retryConnect() { return t("member.testFailed.retry"); },
  /** 通用。 */
  get continueLabel() { return t("member.common.continue"); },
  get useByok() { return t("member.common.useByok"); },
  /** ⑨ 两态。 */
  boosterActive: (date: string, pct: number) => t("member.notice.boosterActive", { date, pct }),
  connectionLost: (date: string) => t("member.notice.connectionLost", { date }),
  /** ⑧ 扣款失败黄条。 */
  dunning: (date: string) => t("member.notice.dunning", { date }),
  /** ④ 双池皆空。 */
  get quotaExhausted() { return t("member.quota.exhausted"); },
  /** 网关 403 quota_prehold_insufficient：本回合预算预扣超过剩余额度。 */
  get quotaPreholdInsufficient() { return t("member.quota.preholdInsufficient"); },
  /** ④b 没配过 BYOK：与④同一模式的指路。 */
  get noProvider() { return t("member.noProvider.full"); },
  /** chip 行的短形（②b 尺寸内）。 */
  get noProviderShort() { return t("member.noProvider.short"); },
  get remainPrefix() { return t("member.chip.remain"); },
  get endedChipLabel() { return t("member.chip.endedLabel"); },
  get endedChip() { return t("member.chip.ended"); },
  /** ②c 用户中心（线框照抄）。 */
  get centerTitle() { return t("member.center.title"); },
  memberActive: (plan: string) => t("member.center.memberActive", { plan }),
  memberCanceled: (plan: string) => t("member.center.memberCanceled", { plan }),
  autoRenew: (date: string) => t("member.center.autoRenew", { date }),
  /** 非 recurring（按月一次性购买）的到期行。 */
  expiresOn: (date: string) => t("member.center.expiresOn", { date }),
  usableUntil: (date: string) => t("member.center.usableUntil", { date }),
  get quotaPerCycle() { return t("member.center.quotaPerCycle"); },
  /** 非 recurring（按月一次性购买）的周期额度行。 */
  get quotaPerCycleOnce() { return t("member.center.quotaPerCycleOnce"); },
  cycleStillUsable: (date: string) => t("member.center.cycleStillUsable", { date }),
  get boosterRow() { return t("member.center.boosterRow"); },
  boosterRemain: (pct: number) => t("member.center.boosterRemain", { pct }),
  get manageSubscription() { return t("member.center.manageSubscription"); },
  /** 非 recurring（按月一次性购买）的主按钮（点击行为不变，仍开账单页）。 */
  get renewOrRepurchase() { return t("member.center.renewOrRepurchase"); },
  get resumeSubscription() { return t("member.center.resumeSubscription"); },
  get requestRefund() { return t("member.center.requestRefund"); },
  get boosterBuy() { return t("member.center.boosterBuy"); },
  get boosterBuyBody() { return t("member.center.boosterBuyBody"); },
  get boosterBuyDisabled() { return t("member.center.boosterBuyDisabled"); },
  get accountCard() { return t("member.center.accountCard"); },
  get accountEmail() { return t("member.center.accountEmail"); },
  get accountPlan() { return t("member.center.accountPlan"); },
  planOngoing: (plan: string) => t("member.center.planOngoing", { plan }),
  /** 非 recurring（按月一次性购买）的账户卡套餐行。 */
  planOneTime: (plan: string) => t("member.center.planOneTime", { plan }),
  planEndingHere: (plan: string) => t("member.center.planEndingHere", { plan }),
  /** ④b 退出登录：按钮 + 下方说明小字（订阅额度停用，BYOK 自动接管）。 */
  get logoutButton() { return t("member.logout.button"); },
  get logoutNote() { return t("member.logout.note"); },
  /** JWT 失效（契约 §5.3：401 jwt_expired → 静默降级未登录 + Coach 引导重登）。 */
  get jwtExpired() { return t("member.jwt.expired"); },
  /** ②c 失效态（⑨ 同源：订阅已结束，加油包仍可烧完）与未订阅态。 */
  memberEnded: (plan: string) => t("member.ended.headline", { plan }),
  endedAt: (date: string) => t("member.ended.at", { date }),
  get cycleEnded() { return t("member.ended.cycleEnded"); },
  get resubscribe() { return t("member.ended.resubscribe"); },
  get resubscribeHint() { return t("member.ended.resubscribeHint"); },
  get webHint() { return t("member.ended.webHint"); },
  planEnded: (plan: string) => t("member.ended.planEnded", { plan }),
  get notSubscribedTitle() { return t("member.center.notSubscribedTitle"); },
  get notSubscribedCenterBody() { return t("member.center.notSubscribedBody"); },
} as const;

/** 邮箱掩码（线框 `u***@gmail.com`）：保留首字符与域名。 */
export function maskEmail(email: string): string {
  const value = email.trim();
  const at = value.indexOf("@");
  if (at <= 0) return value || "—";
  const local = value.slice(0, at);
  const domain = value.slice(at);
  const head = local.slice(0, 1);
  return `${head}***${domain}`;
}

/** `2026-10-20T...Z` → `10-20`（线框口径：月-日）。 */
export function formatMemberDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${month}-${day}`;
}

/** 档位显示名（②c 横幅 / 账户行）。 */
export function planLabel(plan: MemberMe["plan"]): string {
  if (plan === "standard") return "Standard";
  if (plan === "plus") return "Plus";
  return "—";
}

// ── 网关错误码分流（契约 §5.3 双认表）──────────────────────────────────

/**
 * ac-gateway 错误 `type` → 客户端动作。**两个版本内新旧值都要认**
 * （契约 §5.3 兼容段）：新值 `jwt_expired` / `member_required` / `quota_exhausted`
 * / `quota_prehold_insufficient`，旧值 `auth_expired` / `ac_member_required`。
 */
export type MemberGatewayError = "jwt_expired" | "member_required" | "quota_exhausted" | "quota_prehold_insufficient";

const GATEWAY_ERROR_ALIASES: Record<string, MemberGatewayError> = {
  jwt_expired: "jwt_expired",
  auth_expired: "jwt_expired",
  member_required: "member_required",
  ac_member_required: "member_required",
  quota_exhausted: "quota_exhausted",
  // 2026-10-01 新增（服务端网关 403）：语义＝本回合预算预扣超过剩余额度，
  // 与「本期额度用完」（quota_exhausted）是两种口径，文案分列。
  quota_prehold_insufficient: "quota_prehold_insufficient",
};

/**
 * 从任意错误文本里识别网关错误码。
 *
 * 网关响应体固定 `{"error":{"message":"<中文>","type":"<上表>"}}`，经 Pi 的
 * 错误体透传后会出现在 run 的 error.message 里；这里对明文做子串匹配，
 * 不依赖 JSON 可解析（上游可能把 body 折进 message 或截断）。
 */
export function classifyMemberGatewayError(text: string | null | undefined): MemberGatewayError | null {
  const value = typeof text === "string" ? text : "";
  if (!value) return null;
  for (const [alias, code] of Object.entries(GATEWAY_ERROR_ALIASES)) {
    if (value.includes(alias)) return code;
  }
  return null;
}

/** 网关错误 → 客户端提示文案（④ / ⑨ / 重登录引导；零商业化）。 */
export function gatewayErrorNotice(code: MemberGatewayError): { tone: "warn" | "error"; text: string } {
  if (code === "quota_exhausted") return { tone: "error", text: MEMBER_COPY.quotaExhausted };
  if (code === "quota_prehold_insufficient") return { tone: "error", text: MEMBER_COPY.quotaPreholdInsufficient };
  if (code === "member_required") return { tone: "error", text: MEMBER_COPY.noProvider };
  return { tone: "warn", text: MEMBER_COPY.jwtExpired };
}

// ── 左下角 chip（②/②b/⑨ 常驻态）与用户中心（②c）的展示投影 ──────────────

export interface MemberChipView {
  /** 上行：身份（会员/BYOK 已登录显示邮箱，未登录显示登录入口）。 */
  email: string | null;
  /** 下行：会员状态 / 引擎行。 */
  status: string | null;
  tone: "default" | "warn" | "error";
  /** 订阅彻底失效且无加油包（⑨ 右）：显示「重新订阅 ›」。 */
  relink: boolean;
  kind: "member" | "byok" | "none";
}

/**
 * 左下角账号卡的单一投影（线框 ②b 规则）：
 * - 会员：`Standard · 余量 62%`，分档着色；订阅到期但有加油包 → `订阅已到期 · 加油包 45%`（橙）；
 *   双池皆空 → `订阅已结束 · 余量 0%`（红）+ 重新订阅入口；
 * - BYOK：`⚙ <所选 Provider 名> · 已连接`（不显示模型名）；未登录 → `登录 / 注册 ›`；
 * - 都没配：`未连接模型服务`（④b 右态）。
 */
/** BYOK 引擎行整句进字典（两形态：已连接 / 未连接），不做后缀拼接手术。 */
function byokEngineLine(byok: { providerName: string; connected: boolean }): string {
  return t(byok.connected ? "member.chip.byokConnected" : "member.chip.byok", { provider: byok.providerName });
}

export function memberChipView(
  me: MemberMe | null,
  byok: { providerName: string; connected: boolean } | null,
): MemberChipView {
  if (me) {
    const pool = currentPool(me);
    const pct = pool?.pct ?? 0;
    if (bothPoolsEmpty(me) && subscriptionEnded(me)) {
      return {
        email: maskEmail(me.user.email),
        status: MEMBER_COPY.endedChip,
        tone: "error",
        relink: true,
        kind: "member",
      };
    }
    if (subscriptionEnded(me) && boosterAlive(me)) {
      const boostPct = me.pools.boost?.pct ?? 0;
      return {
        email: maskEmail(me.user.email),
        status: t("member.chip.endedBooster", { pct: boostPct }),
        tone: "warn",
        relink: false,
        kind: "member",
      };
    }
    if (!me.member) {
      // 已登录但从未订阅（①b 态1 之后没有再买）：与 BYOK 同样只显示身份。
      return {
        email: maskEmail(me.user.email),
        status: byok ? byokEngineLine(byok) : MEMBER_COPY.noProviderShort,
        tone: "default",
        relink: false,
        kind: "byok",
      };
    }
    return {
      email: maskEmail(me.user.email),
      status: t("member.chip.planRemain", { plan: planLabel(me.plan), pct }),
      tone: poolTier(pct) === "low" ? "error" : poolTier(pct) === "warn" ? "warn" : "default",
      relink: false,
      kind: "member",
    };
  }
  if (byok) {
    return {
      email: null,
      status: byokEngineLine(byok),
      tone: "default",
      relink: false,
      kind: "byok",
    };
  }
  return { email: null, status: null, tone: "default", relink: false, kind: "none" };
}
