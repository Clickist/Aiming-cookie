/**
 * Native purchase-links command.
 *
 * purchase_links.lookup — 把推荐候选（品牌+型号）发给自建的 affiliate-links
 * Worker（CF Workers，affiliate.gearclickist.com），换取淘宝联盟/拼多多多多客
 * 的带佣金购买短链。联盟密钥只存 Worker Secrets，客户端只拿短链。
 *
 * 服务地址与 token 从配置目录 affiliate-service.json 读取（{service_url, token}）；
 * 未配置或服务失败时返回 unavailable，Coach 按外设 skill 的降级链退回
 * 京东静态直链（eloshapes.query 的 jd_canonical_url）或建议用户自行搜索。
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { getConfigDir } from "./app-data.ts";

// ── Types ──────────────────────────────────────────────────────────────

type AnyDict = Record<string, any>;

export type NativeAffiliateResult = {
  status: "succeeded" | "failed" | "unavailable";
  result?: unknown;
  result_ref?: string;
  warning_or_error?: { code: string; message: string };
};

// ── Config ─────────────────────────────────────────────────────────────

const AFFILIATE_CONFIG_FILE = "affiliate-service.json";
const MAX_ITEMS = 6;
const REQUEST_TIMEOUT_MS = 15_000;
const FIELD_MAX_CHARS = 60;

type AffiliateConfig = { service_url: string; token: string };

function loadAffiliateConfig(): AffiliateConfig | null {
  const path = join(getConfigDir(), AFFILIATE_CONFIG_FILE);
  if (!existsSync(path)) return null;
  try {
    const raw = JSON.parse(readFileSync(path, "utf-8")) as AnyDict;
    if (typeof raw.service_url !== "string" || typeof raw.token !== "string") return null;
    // 只接受 https 地址：token 会随请求发出，不允许落到明文信道。
    if (!/^https:\/\/[a-z0-9.-]+/i.test(raw.service_url)) return null;
    return { service_url: raw.service_url.replace(/\/+$/, ""), token: raw.token };
  } catch {
    return null;
  }
}

// ── Parameters ─────────────────────────────────────────────────────────

const ITEM_FIELDS = ["brand", "model", "variant", "q"] as const;

function normalizeItems(value: unknown): AnyDict[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_ITEMS) return null;
  const items: AnyDict[] = [];
  for (const entry of value) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) return null;
    const it = entry as AnyDict;
    const unknown = Object.keys(it).filter((k) => !(ITEM_FIELDS as readonly string[]).includes(k));
    if (unknown.length) return null;
    const hasName = (typeof it.brand === "string" && it.brand.trim().length > 0) ||
      (typeof it.model === "string" && it.model.trim().length > 0);
    const hasQ = typeof it.q === "string" && it.q.trim().length > 0;
    if (!hasName && !hasQ) return null;
    const clean: AnyDict = {};
    for (const field of ITEM_FIELDS) {
      if (typeof it[field] === "string" && it[field].trim()) clean[field] = it[field].trim().slice(0, FIELD_MAX_CHARS);
    }
    items.push(clean);
  }
  return items;
}

// ── Response projection ────────────────────────────────────────────────

function normalizePlatform(value: unknown): AnyDict | null {
  if (typeof value !== "object" || value === null) return null;
  const v = value as AnyDict;
  if (v.miss || typeof v.url !== "string" || !v.url) return null;
  return {
    title: typeof v.title === "string" ? v.title.slice(0, 120) : "",
    price: typeof v.price === "string" || typeof v.price === "number" ? String(v.price) : "",
    sales: typeof v.sales === "string" ? v.sales : "",
    url: v.url,
  };
}

// ── Public API ─────────────────────────────────────────────────────────

export function isNativeAffiliateCommand(commandName: string): boolean {
  return commandName === "purchase_links.lookup";
}

export async function executeNativeAffiliate(
  commandName: string,
  params: AnyDict,
): Promise<NativeAffiliateResult> {
  if (commandName !== "purchase_links.lookup") {
    return { status: "failed", warning_or_error: { code: "unknown_command", message: `${commandName} is not a purchase_links command` } };
  }
  const unknown = Object.keys(params).filter((k) => k !== "items");
  if (unknown.length) {
    return {
      status: "failed",
      warning_or_error: {
        code: "invalid_parameters",
        message: `purchase_links.lookup does not accept ${unknown.map((k) => `"${k}"`).join(", ")}; allowed fields: items`,
      },
    };
  }
  const items = normalizeItems(params.items);
  if (!items) {
    return {
      status: "failed",
      warning_or_error: {
        code: "invalid_parameters",
        message: "purchase_links.lookup requires items: 1-6 entries of {brand, model, variant?} or {q}",
      },
    };
  }

  const config = loadAffiliateConfig();
  if (!config) {
    return {
      status: "unavailable",
      warning_or_error: { code: "purchase_links_unavailable", message: "购买链接服务未配置" },
    };
  }

  let payload: AnyDict;
  try {
    const response = await fetch(`${config.service_url}/links`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-ac-token": config.token },
      body: JSON.stringify({ items }),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    payload = await response.json();
  } catch (error) {
    return {
      status: "unavailable",
      warning_or_error: { code: "purchase_links_unavailable", message: `购买链接服务暂时不可用（${String(error).slice(0, 80)}）` },
    };
  }

  const results = Array.isArray(payload?.results) ? payload.results : null;
  if (!results) {
    return {
      status: "failed",
      warning_or_error: { code: "purchase_links_invalid_response", message: "购买链接服务返回了无法解析的结果" },
    };
  }

  const links = results.map((entry: AnyDict, index: number) => ({
    query: typeof entry?.q === "string" ? entry.q : "",
    request: items[index] ?? {},
    direct: normalizePlatform(entry?.direct),
    taobao: normalizePlatform(entry?.taobao),
    pdd: normalizePlatform(entry?.pdd),
  }));

  return {
    status: "succeeded",
    result_ref: "purchase_links:lookup",
    result: {
      schema_version: "purchase_links.v1",
      availability: "available",
      links,
      note: "direct=品牌官方店直签链接（有则优先使用）。降级链：官方直签 → 淘宝 → 拼多多 → 京东直链（eloshapes.query 的 jd_canonical_url，未经联盟核验，放最后）→ 都没有时建议用户自行搜索。taobao/pdd/direct 为 null 表示该渠道未命中。",
    },
  };
}
