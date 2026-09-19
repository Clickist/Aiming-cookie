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

// ── 展示文案（照抄线框，不自行发明）────────────────────────────────────────
//
// 用词红线：全表禁用「云教练」（教练跑在用户本地，订阅卖的是模型额度）；
// 金额只出现在加油包固定标价「加油包 ¥10」（线框原文），其余一律只出百分比。

export const MEMBER_COPY = {
  /** ① 下拉里的会员档条目（线框 ① 的菜单项文案拆成主名 + 副行）。 */
  providerDropdownLabel: "Aiming Cookie（推荐）",
  providerDropdownHint: "账号订阅，登录即用",
  /** ①a 等待页。 */
  waitingTitle: "正在浏览器中打开登录与订阅页面…",
  waitingBody: "在网页里完成登录和套餐订阅后，本页会自动继续。",
  waitingReopenHint: "没有自动跳转？",
  reopenBrowser: "重新打开浏览器页面",
  /** ①a 连通成功行（模型名固定，来自模型锁）。 */
  connected: "订阅完成 · 已连接 Aiming Cookie · deepseek-v4-flash",
  /** ①b 态1 未订阅。 */
  notSubscribedPrefix: "已登录",
  notSubscribedSuffix: "还差订阅",
  notSubscribedBody: "检测到账号暂无有效订阅。",
  notSubscribedBodyLine2: "完成订阅后本页自动继续。",
  openSubscribePage: "打开订阅页面（Standard / Plus）",
  /** ①b 态2 老会员直连。 */
  alreadyMember: "已是 Standard 会员 · 直接连接",
  alreadyMemberBody: "检测到有效订阅，跳过购买步骤。",
  alreadyMemberBodyLine2: "换机 / 重装同样走这里。",
  /** ①b 态3 连通失败。 */
  testFailed: "登录成功，但连接测试未通过",
  testFailedBody: "网络异常或服务暂不可用。",
  testFailedBodyLine2: "账号与订阅不受影响，稍后重试即可。",
  retryConnect: "重试连接",
  /** 通用。 */
  continueLabel: "继续 →",
  useByok: "改用自定义 Provider（BYOK）",
  /** ⑨ 两态。 */
  boosterActive: (date: string, pct: number) => `订阅已于 ${date} 到期。正在使用加油包余量（${pct}%）；重新订阅后自动回到订阅额度。`,
  connectionLost: (date: string) =>
    `Aiming Cookie 连接已断开：订阅已结束且加油包已用完（${date}）。历史与设置全部保留。点击左下角账户可重新订阅；也可以在设置中改用 BYOK。`,
  /** ⑧ 扣款失败黄条。 */
  dunning: (date: string) => `本次自动续费未成功（${date}）：7 天内会自动重试，期间一切照常。可更新支付方式。`,
  /** ④ 双池皆空。 */
  quotaExhausted: "本期额度已用完。点击左下角账户可充值或管理订阅；也可以在设置中改用 BYOK。",
  /** ④b 没配过 BYOK：与④同一模式的指路。 */
  noProvider: "未连接模型服务。在 设置 → 模型服务 中连接 Aiming Cookie 或自定义 Provider 后即可使用 Coach。",
  /** chip 行的短形（②b 尺寸内）。 */
  noProviderShort: "未连接模型服务",
  remainPrefix: "余量",
  endedChipLabel: "订阅已到期",
  endedChip: "订阅已结束 · 余量 0%",
  /** ②c 用户中心（线框照抄）。 */
  centerTitle: "用户中心",
  memberActive: (plan: string) => `${plan} 会员 · 生效中`,
  memberCanceled: (plan: string) => `${plan} 会员 · 已取消`,
  autoRenew: (date: string) => `下期自动续费 ${date}`,
  usableUntil: (date: string) => `额度可用至 ${date}`,
  quotaPerCycle: "额度每周期发放 · 连续包月",
  cycleStillUsable: (date: string) => `本期仍可正常使用 · ${date} 后不再续费`,
  boosterRow: "🍪 加油包 · 永不过期",
  boosterRemain: (pct: number) => `余量 ${pct}%`,
  manageSubscription: "管理订阅 / 取消自动续费",
  resumeSubscription: "恢复自动续费",
  requestRefund: "申请退款",
  boosterBuy: "加油包 ¥10",
  boosterBuyBody: "订阅额度不够时加购；烧完才可买下一包，买过的永不过期。",
  boosterBuyDisabled: "当前加油包还有余量",
  accountCard: "账户",
  accountEmail: "邮箱",
  accountPlan: "套餐",
  planOngoing: (plan: string) => `${plan} · 连续包月`,
  planEndingHere: (plan: string) => `${plan} · 本期结束后终止`,
  /** ④b 退出登录：按钮 + 下方说明小字（订阅额度停用，BYOK 自动接管）。 */
  logoutButton: "退出登录",
  logoutNote: "订阅额度停用，自动转用已配置的 BYOK",
  /** JWT 失效（契约 §5.3：401 jwt_expired → 静默降级未登录 + Coach 引导重登）。 */
  jwtExpired: "登录状态已过期，请在左下角账户重新登录后继续使用 Coach。",
  /** ②c 失效态（⑨ 同源：订阅已结束，加油包仍可烧完）与未订阅态。 */
  memberEnded: (plan: string) => `${plan} 会员 · 已结束`,
  endedAt: (date: string) => `结束于 ${date}`,
  cycleEnded: "订阅额度已停用 · 加油包余额仍可用",
  resubscribe: "重新订阅",
  resubscribeHint: "↑ 在系统浏览器打开订阅页（重新订阅后自动回到订阅额度）",
  webHint: "↑ 两项在系统浏览器打开（账号中心 · 账单子页）",
  planEnded: (plan: string) => `${plan} · 已结束`,
  notSubscribedTitle: "已登录 · 未订阅",
  notSubscribedCenterBody: "完成订阅后本客户端自动接入教练额度；也可以继续用自定义 Provider（BYOK）。",
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
 * （契约 §5.3 兼容段）：新值 `jwt_expired` / `member_required` / `quota_exhausted`，
 * 旧值 `auth_expired` / `ac_member_required`。
 */
export type MemberGatewayError = "jwt_expired" | "member_required" | "quota_exhausted";

const GATEWAY_ERROR_ALIASES: Record<string, MemberGatewayError> = {
  jwt_expired: "jwt_expired",
  auth_expired: "jwt_expired",
  member_required: "member_required",
  ac_member_required: "member_required",
  quota_exhausted: "quota_exhausted",
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
        status: `${MEMBER_COPY.endedChipLabel} · 加油包 ${boostPct}%`,
        tone: "warn",
        relink: false,
        kind: "member",
      };
    }
    if (!me.member) {
      // 已登录但从未订阅（①b 态1 之后没有再买）：与 BYOK 同样只显示身份。
      return {
        email: maskEmail(me.user.email),
        status: byok ? `⚙ ${byok.providerName}${byok.connected ? " · 已连接" : ""}` : MEMBER_COPY.noProviderShort,
        tone: "default",
        relink: false,
        kind: "byok",
      };
    }
    return {
      email: maskEmail(me.user.email),
      status: `${planLabel(me.plan)} · ${MEMBER_COPY.remainPrefix} ${pct}%`,
      tone: poolTier(pct) === "low" ? "error" : poolTier(pct) === "warn" ? "warn" : "default",
      relink: false,
      kind: "member",
    };
  }
  if (byok) {
    return {
      email: null,
      status: `⚙ ${byok.providerName}${byok.connected ? " · 已连接" : ""}`,
      tone: "default",
      relink: false,
      kind: "byok",
    };
  }
  return { email: null, status: null, tone: "default", relink: false, kind: "none" };
}
