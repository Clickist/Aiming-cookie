/**
 * Aiming Cookie 会员档（WP-C）：deep-link 换票、JWT 保管、`/api/me` 拉取、退出登录。
 *
 * 契约事实源：`Desktop\accounts\INTERFACE.md` v2（§1 JWT、§2 换票三步、§3 deep-link
 * 九条校验、§7.1-8 `/api/me` 冻结 schema）。本模块是客户端侧唯一的会员状态入口：
 *
 * - **权益永不从 URL 推断**（§3.3-6）：deep-link 只带一次性 ticket 与 device_code，
 *   会员/百分比一律来自 exchange 响应或 `/api/me`。
 * - **dc 绑定校验**（§3.3-4）：只有 URL 里的 `dc` 等于本地待用 device_code 才接受
 *   ticket；不匹配或本地无 dc（转发的链接）一律丢弃 ticket，降级为「无 ticket」。
 * - **一次性**（§3.3-5）：exchange 成功后立刻清空本地待用 dc；同一 ticket 再次到达
 *   （single-instance 转发、浏览器重试）不得重复 exchange，返回 `already_consumed`。
 * - JWT 存进 provider 凭据仓（`config/provider.json` 的 relay 档 credential，
 *   沿用既有明文本地合同），前端全程不接触 JWT 明文。
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { isRecord } from "./contracts.ts";
import { getConfigDir } from "./app-data.ts";
import {
  AIMING_COOKIE_RELAY_MODEL_ID,
  AIMING_COOKIE_RELAY_PROVIDER_ID,
  MEMBER_GATEWAY_BASE_URL,
  relayBaseUrl,
} from "./provider-models.ts";
import {
  findStoredProfile,
  loadProviderStore,
  saveProviderStore,
  type ProviderProfileStore,
  type StoredProviderProfile,
} from "./provider-store.ts";

/** accounts Worker 域名（契约 §0）。测试与本地 mock 经环境变量换基址。 */
export const ACCOUNTS_BASE_URL = (process.env.AC_ACCOUNTS_BASE_URL ?? "https://accounts.gearclickist.com").replace(/\/+$/, "");

/**
 * UI 态夹具（规划 §4.2 的「/api/me 三份 fixture」）：dev/联调期用
 * `AC_MEMBER_FIXTURE=<scenario>` 打开，会员读取全部走本地常量、零网络，
 * 让 ①②②c④⑧⑨ 各态可以在没有 accounts Worker 的情况下逐个走查。
 * 生产构建不带这个环境变量 → 行为与不存在完全一致。
 *
 * 场景：`member`（Standard 62%）· `canceled`（已取消未到期 + 加油包 100%）
 * · `booster`（已到期 + 加油包 45%）· `lost`（双池皆空）· `dunning`（扣款失败）
 * · `free`（已登录未订阅）· `byok`（未登录，纯 BYOK）。
 */
const MEMBER_FIXTURE = process.env.AC_MEMBER_FIXTURE ?? "";

function fixtureMe(): MemberMe | null {
  const base = {
    user: { id: "fixture-user", email: "u***@gmail.com", name: null },
    member: true,
    plan: "standard" as const,
    status: "active" as const,
    cancel_at_period_end: false,
    period_start: "2026-09-20T00:00:00.000Z",
    period_end: "2026-10-20T00:00:00.000Z",
    dunning: false,
    pools: {
      sub: { remaining: 3_875_000, grant: 6_250_000, pct: 62 },
      boost: null,
    },
    current_pool: "sub" as const,
    boost_buyable: true,
    server_time: new Date().toISOString(),
  };
  switch (MEMBER_FIXTURE) {
    case "member":
      return base;
    case "canceled":
      return {
        ...base,
        status: "canceled",
        cancel_at_period_end: true,
        pools: { sub: base.pools.sub, boost: { remaining: 6_250_000, grant: 6_250_000, pct: 100 } },
        boost_buyable: false,
      };
    case "booster":
      return {
        ...base,
        member: false,
        status: "expired",
        pools: { sub: { remaining: 0, grant: 6_250_000, pct: 0 }, boost: { remaining: 2_812_500, grant: 6_250_000, pct: 45 } },
        current_pool: "boost",
        boost_buyable: false,
      };
    case "lost":
      return {
        ...base,
        member: false,
        status: "expired",
        pools: { sub: { remaining: 0, grant: 6_250_000, pct: 0 }, boost: null },
        current_pool: null,
        boost_buyable: false,
      };
    case "dunning":
      return { ...base, dunning: true };
    case "free":
      return {
        ...base,
        member: false,
        status: "none",
        plan: null,
        period_start: null,
        period_end: null,
        pools: { sub: null, boost: null },
        current_pool: null,
        boost_buyable: false,
      };
    default:
      return null;
  }
}

const MEMBER_STATE_FILE = "member.json";
const MEMBER_STATE_SCHEMA = 1;
/** 已消费 ticket 的保留条数：只用于去重（§3.3-5），不参与任何权益判定。 */
const CONSUMED_TICKET_LIMIT = 20;
const HTTP_TIMEOUT_MS = 20_000;

export type MemberMe = {
  user: { id: string; email: string; name: string | null };
  member: boolean;
  plan: "standard" | "plus" | null;
  status: "active" | "canceled" | "expired" | "refunded" | "none";
  cancel_at_period_end: boolean;
  period_start: string | null;
  period_end: string | null;
  dunning: boolean;
  pools: {
    sub: { remaining: number; grant: number; pct: number } | null;
    boost: { remaining: number; grant: number; pct: number } | null;
  };
  current_pool: "sub" | "boost" | null;
  boost_buyable: boolean;
  server_time: string;
};

export type MemberExchangeResult =
  | { ok: true; jwt: string; user: { id: string; email: string; name: string | null }; member: boolean }
  | { ok: false; code: MemberExchangeErrorCode; message: string };

export type MemberExchangeErrorCode =
  /** ticket 与 dc 未成对出现，或本地无待用 dc（转发的链接）→ 按「无 ticket」处理。 */
  | "no_ticket"
  /** 本机保存的待用 device_code 与 URL 不符（§3.3-4）→ 丢弃 ticket。 */
  | "dc_mismatch"
  /** 同一 ticket 已经消费过（§3.3-5）→ 幂等，不重复 exchange。 */
  | "already_consumed"
  | "invalid_ticket"
  | "ticket_expired"
  | "device_code_invalid"
  | "device_code_claimed"
  | "device_code_expired"
  | "network_error";

type MemberStateFile = {
  schema_version: typeof MEMBER_STATE_SCHEMA;
  pending: { device_code: string; login_url: string; started_at: number } | null;
  consumed_tickets: string[];
};

function memberStatePath(): string {
  return join(getConfigDir(), MEMBER_STATE_FILE);
}

function emptyMemberState(): MemberStateFile {
  return { schema_version: MEMBER_STATE_SCHEMA, pending: null, consumed_tickets: [] };
}

/** 读取本地会员交互状态；缺失/损坏按空态自愈（deep-link 是常态丢事件，不报错）。 */
export function loadMemberState(): MemberStateFile {
  const path = memberStatePath();
  if (!existsSync(path)) return emptyMemberState();
  try {
    const raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
    if (!isRecord(raw)) return emptyMemberState();
    const pendingRaw = raw.pending;
    const pending = isRecord(pendingRaw) && typeof pendingRaw.device_code === "string" && pendingRaw.device_code
      ? {
          device_code: pendingRaw.device_code,
          login_url: typeof pendingRaw.login_url === "string" ? pendingRaw.login_url : "",
          started_at: typeof pendingRaw.started_at === "number" ? pendingRaw.started_at : 0,
        }
      : null;
    return {
      schema_version: MEMBER_STATE_SCHEMA,
      pending,
      consumed_tickets: Array.isArray(raw.consumed_tickets)
        ? raw.consumed_tickets.filter((item): item is string => typeof item === "string").slice(-CONSUMED_TICKET_LIMIT)
        : [],
    };
  } catch {
    return emptyMemberState();
  }
}

function saveMemberState(state: MemberStateFile): void {
  const dir = getConfigDir();
  mkdirSync(dir, { recursive: true });
  const path = memberStatePath();
  const tmpPath = join(dir, `.${MEMBER_STATE_FILE}.tmp`);
  writeFileSync(tmpPath, JSON.stringify({
    schema_version: state.schema_version,
    pending: state.pending,
    consumed_tickets: state.consumed_tickets.slice(-CONSUMED_TICKET_LIMIT),
  }, null, 2), "utf8");
  renameSync(tmpPath, path);
}

async function requestJson(
  path: string,
  init: { method: string; body?: unknown; headers?: Record<string, string>; baseUrl?: string },
): Promise<{ status: number; body: unknown }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), HTTP_TIMEOUT_MS);
  timeout.unref?.();
  try {
    const response = await fetch(`${init.baseUrl ?? ACCOUNTS_BASE_URL}${path}`, {
      method: init.method,
      headers: {
        ...(init.body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(init.headers ?? {}),
      },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: controller.signal,
    });
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    return { status: response.status, body };
  } finally {
    clearTimeout(timeout);
  }
}

/** 已存 JWT（relay 档 credential）。null = 未登录或凭据缺失。 */
export function storedMemberJwt(): string | null {
  if (fixtureMe()) return "fixture-member-jwt";
  const store = loadProviderStore();
  const entry = store.profiles.find((profile) => profile.provider_id === AIMING_COOKIE_RELAY_PROVIDER_ID);
  const credential = entry?.credential;
  return credential?.type === "api_key" && typeof credential.key === "string" && credential.key
    ? credential.key
    : null;
}

/** relay 档在档案库中的 id（不存在返回 null）。 */
export function relayProfileId(): number | null {
  const store = loadProviderStore();
  return store.profiles.find((profile) => profile.provider_id === AIMING_COOKIE_RELAY_PROVIDER_ID)?.id ?? null;
}

/** 档显示名兜底：与 provider-models 的目录名保持同源文案。 */
const AIMING_COOKIE_RELAY_PROVIDER_NAME_FALLBACK = "Aiming Cookie";

/**
 * 把 JWT 写进 relay 档凭据（不存在则建）。首个档案自动成为当前档；已有当前档
 * 时不动它——已配过 BYOK 的用户登录会员后仍按自己选的档跑（②c 退出登录切回）。
 * 返回档 id。
 */
export function storeMemberJwt(jwt: string): number {
  const store = loadProviderStore();
  const existing = store.profiles.find((profile) => profile.provider_id === AIMING_COOKIE_RELAY_PROVIDER_ID);
  if (existing) {
    existing.credential = { type: "api_key", key: jwt };
    if (!existing.model_id) existing.model_id = AIMING_COOKIE_RELAY_MODEL_ID;
    saveProviderStore(store);
    return existing.id;
  }
  const entry: StoredProviderProfile = {
    id: store.next_id,
    kind: "builtin",
    name: AIMING_COOKIE_RELAY_PROVIDER_NAME_FALLBACK,
    provider_id: AIMING_COOKIE_RELAY_PROVIDER_ID,
    model_id: AIMING_COOKIE_RELAY_MODEL_ID,
    credential: { type: "api_key", key: jwt },
  };
  store.profiles.push(entry);
  store.next_id = Math.max(store.next_id, entry.id + 1);
  if (store.active_id === null) store.active_id = entry.id;
  saveProviderStore(store);
  return entry.id;
}

/** 清掉 relay 档凭据；返回退出登录后的回落档（有 BYOK 时自动切它）。 */
export function clearMemberJwt(): { relay_id: number | null; fallback_profile_id: number | null } {
  const store = loadProviderStore();
  const relay = store.profiles.find((profile) => profile.provider_id === AIMING_COOKIE_RELAY_PROVIDER_ID);
  if (!relay) return { relay_id: null, fallback_profile_id: activeUsableProfileId(store, null) };
  delete relay.credential;
  const fallback = relay.id === store.active_id ? activeUsableProfileId(store, relay.id) : null;
  if (fallback !== null) store.active_id = fallback;
  saveProviderStore(store);
  return { relay_id: relay.id, fallback_profile_id: fallback };
}

/** 第一个仍带凭据的非 relay 档案（BYOK 回落目标）；没有则 null（Coach 置灰）。 */
function activeUsableProfileId(store: ProviderProfileStore, excludeId: number | null): number | null {
  const candidate = store.profiles.find(
    (profile) => profile.id !== excludeId
      && profile.provider_id !== AIMING_COOKIE_RELAY_PROVIDER_ID
      && profile.credential !== undefined,
  );
  return candidate?.id ?? null;
}

/**
 * 换票第一步（契约 §2）：起 device_code 并保存为「本地待用 dc」——
 * deep-link 的 dc 绑定校验（§3.3-4）就靠这份本地记录。
 */
export async function startMemberLogin(): Promise<
  { ok: true; device_code: string; login_url: string; expires_in: number } | { ok: false; message: string }
> {
  try {
    const { status, body } = await requestJson("/api/device/start", { method: "POST", body: {} });
    if (status !== 200 || !isRecord(body)
      || typeof body.device_code !== "string" || !body.device_code
      || typeof body.login_url !== "string" || !body.login_url) {
      return { ok: false, message: `登录会话创建失败（HTTP ${status}）` };
    }
    const state = loadMemberState();
    state.pending = {
      device_code: body.device_code,
      login_url: body.login_url,
      started_at: Date.now(),
    };
    saveMemberState(state);
    return {
      ok: true,
      device_code: body.device_code,
      login_url: body.login_url,
      expires_in: typeof body.expires_in === "number" ? body.expires_in : 600,
    };
  } catch {
    return { ok: false, message: "无法连接账号服务，请检查网络后重试。" };
  }
}

/**
 * 换票第二步（契约 §2/§3.3）：ticket + dc 换 JWT。
 *
 * 全部拒绝路径都返回结构化 code，绝不抛异常——收不到 deep-link、ticket 过期、
 * dc 不匹配都是常态（§3.3-7），调用方一律降级为「用本地 JWT 拉 /api/me」。
 */
export async function exchangeMemberTicket(input: {
  ticket?: string | null;
  dc?: string | null;
}): Promise<MemberExchangeResult> {
  const ticket = typeof input.ticket === "string" ? input.ticket.trim() : "";
  const dc = typeof input.dc === "string" ? input.dc.trim() : "";
  const state = loadMemberState();
  // §3.3-3：ticket 与 dc 必须成对；只有一个 → 忽略 ticket。
  if (!ticket || !dc) {
    return { ok: false, code: "no_ticket", message: "没有可用的登录票据。" };
  }
  // §3.3-5：同一 ticket 重复到达（single-instance 转发/浏览器重试）不重复 exchange。
  if (state.consumed_tickets.includes(ticket)) {
    return { ok: false, code: "already_consumed", message: "这条登录票据已经处理过。" };
  }
  // §3.3-4：dc 绑定校验（防会话固定）——本地没有待用 dc、或 dc 不符 → 丢弃 ticket。
  if (!state.pending || state.pending.device_code !== dc) {
    return { ok: false, code: "dc_mismatch", message: "登录票据与发起设备不匹配，已忽略。" };
  }

  let status: number;
  let body: unknown;
  try {
    const result = await requestJson("/api/device/exchange", {
      method: "POST",
      body: { device_code: dc, ticket },
    });
    status = result.status;
    body = result.body;
  } catch {
    return { ok: false, code: "network_error", message: "无法连接账号服务，请稍后重试。" };
  }

  if (status !== 200 || !isRecord(body) || typeof body.jwt !== "string" || !body.jwt) {
    // 失败也要把这条 ticket 记成已消费：浏览器的 410/401 重试不应连续打服务端。
    state.consumed_tickets = [...state.consumed_tickets, ticket].slice(-CONSUMED_TICKET_LIMIT);
    if (status === 404) {
      saveMemberState(state);
      return { ok: false, code: "device_code_invalid", message: "登录会话不存在，请重新发起登录。" };
    }
    if (status === 401) {
      saveMemberState(state);
      return { ok: false, code: "invalid_ticket", message: "登录票据无效，请重新发起登录。" };
    }
    if (status === 410) {
      // 410 既覆盖 device_code 过期/已 claim，也覆盖 ticket 过期；文案按客户端能
      // 采取的动作给（重新走一遍登录），不区分服务端细分原因。
      saveMemberState(state);
      return { ok: false, code: "ticket_expired", message: "登录票据已过期，请重新发起登录。" };
    }
    saveMemberState(state);
    return { ok: false, code: "network_error", message: `登录未完成（HTTP ${status}）。` };
  }

  const user = isRecord(body.user) ? body.user : {};
  const jwt = body.jwt;
  storeMemberJwt(jwt);
  // §3.3-5：exchange 成功即清空本地待用 dc，并记录 ticket 已消费。
  const next = loadMemberState();
  next.pending = null;
  next.consumed_tickets = [...next.consumed_tickets, ticket].slice(-CONSUMED_TICKET_LIMIT);
  saveMemberState(next);
  return {
    ok: true,
    jwt,
    user: {
      id: typeof user.id === "string" ? user.id : "",
      email: typeof user.email === "string" ? user.email : "",
      name: typeof user.name === "string" ? user.name : null,
    },
    member: body.member === true,
  };
}

/** `/api/me`（契约 §7.1-8）：会员状态的唯一数据源。401 → 静默降级未登录。 */
export async function fetchMemberMe(): Promise<
  { ok: true; me: MemberMe } | { ok: false; code: "unauthorized" | "unavailable"; message: string }
> {
  const fixture = fixtureMe();
  if (fixture) return { ok: true, me: fixture };
  const jwt = storedMemberJwt();
  if (!jwt) return { ok: false, code: "unauthorized", message: "尚未登录 Aiming Cookie。" };
  let status: number;
  let body: unknown;
  try {
    const result = await requestJson("/api/me", { method: "GET", headers: { Authorization: `Bearer ${jwt}` } });
    status = result.status;
    body = result.body;
  } catch {
    return { ok: false, code: "unavailable", message: "无法连接账号服务，请稍后重试。" };
  }
  if (status === 401) return { ok: false, code: "unauthorized", message: "登录状态已过期。" };
  if (status !== 200 || !isRecord(body)) {
    return { ok: false, code: "unavailable", message: `会员状态暂时不可用（HTTP ${status}）。` };
  }
  const poolsRaw = isRecord(body.pools) ? body.pools : {};
  const pool = (value: unknown): MemberMe["pools"]["sub"] => {
    if (!isRecord(value)) return null;
    const remaining = typeof value.remaining === "number" ? value.remaining : 0;
    const grant = typeof value.grant === "number" ? value.grant : 0;
    const pct = typeof value.pct === "number" ? value.pct : 0;
    return { remaining, grant, pct };
  };
  const user = isRecord(body.user) ? body.user : {};
  const plan = body.plan === "standard" || body.plan === "plus" ? body.plan : null;
  const statusValue = body.status === "active" || body.status === "canceled" || body.status === "expired"
    || body.status === "refunded" || body.status === "none"
    ? body.status
    : "none";
  return {
    ok: true,
    me: {
      user: {
        id: typeof user.id === "string" ? user.id : "",
        email: typeof user.email === "string" ? user.email : "",
        name: typeof user.name === "string" ? user.name : null,
      },
      member: body.member === true,
      plan,
      status: statusValue,
      cancel_at_period_end: body.cancel_at_period_end === true,
      period_start: typeof body.period_start === "string" ? body.period_start : null,
      period_end: typeof body.period_end === "string" ? body.period_end : null,
      dunning: body.dunning === true,
      pools: { sub: pool(poolsRaw.sub), boost: pool(poolsRaw.boost) },
      current_pool: body.current_pool === "sub" || body.current_pool === "boost" ? body.current_pool : null,
      boost_buyable: body.boost_buyable === true,
      server_time: typeof body.server_time === "string" ? body.server_time : new Date().toISOString(),
    },
  };
}

/**
 * 连通测试（契约 §3.2 触发 1 末步 / §2 网关）：拿 JWT 打网关 `/v1/models`。
 * 401 → JWT 失效（引导重登）；其余非 2xx → 连接失败（①b 态3，账号与订阅无损）。
 */
export async function testMemberConnection(): Promise<
  { ok: true } | { ok: false; code: "unauthorized" | "unreachable"; message: string }
> {
  if (fixtureMe()) return { ok: true };
  const jwt = storedMemberJwt();
  if (!jwt) return { ok: false, code: "unauthorized", message: "没有可用的登录凭证。" };
  try {
    const { status } = await requestJson("/models", {
      method: "GET",
      headers: { Authorization: `Bearer ${jwt}` },
      baseUrl: relayBaseUrl() || MEMBER_GATEWAY_BASE_URL,
    });
    if (status === 401) return { ok: false, code: "unauthorized", message: "登录凭证已失效，请重新登录。" };
    if (status < 200 || status >= 300) {
      return { ok: false, code: "unreachable", message: `连接测试未通过（HTTP ${status}）。` };
    }
    return { ok: true };
  } catch {
    return { ok: false, code: "unreachable", message: "网络异常或服务暂不可用。" };
  }
}

/** 退出登录（④b）：只清会员凭证，档案与其他设置全部保留。 */
export function logoutMember(): { ok: true; fallback_profile_id: number | null; relay_profile_id: number | null } {
  const result = clearMemberJwt();
  return { ok: true, fallback_profile_id: result.fallback_profile_id, relay_profile_id: result.relay_id };
}

/** 测试/诊断用：当前活跃档 id。 */
export function activeProfileId(): number | null {
  const store = loadProviderStore();
  return store.active_id !== null && findStoredProfile(store, store.active_id) ? store.active_id : null;
}
