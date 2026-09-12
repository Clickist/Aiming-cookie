/**
 * Native web tools for the Coach agent (no API key, no proxy).
 *
 * web_search — queries DuckDuckGo's HTML endpoint (html.duckduckgo.com/html),
 *   parses the result anchors, and unwraps DDG's `uddg=` redirect to the real
 *   URL. When DDG fails (non-200, zero results, exception) it degrades to the
 *   Wikipedia search API. `lite.duckduckgo.com` is deliberately unused: it
 *   answers 202 as a soft rate-limit.
 *
 * fetch_page — reads one page through r.jina.ai (free, keyless; ~20 req/min/IP)
 *   which returns clean markdown, and truncates it to a bounded size.
 *
 * Both tools never throw a bare network exception at the model: empty/failed
 * lookups come back as readable text so the model can retry or fall back.
 */
import { loadPiAi } from "./pi-source.ts";

// ── Types ──────────────────────────────────────────────────────────────

export type WebSearchItem = {
  title: string;
  url: string;
  snippet: string;
};

export type WebSearchOutcome = {
  status: "succeeded" | "failed";
  provider?: "duckduckgo" | "wikipedia";
  items: WebSearchItem[];
  message?: string;
};

// ── Constants ──────────────────────────────────────────────────────────

const BROWSER_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

const WEB_SEARCH_TIMEOUT_MS = 15_000;
const FETCH_PAGE_TIMEOUT_MS = 20_000;
const MAX_SEARCH_RESULTS = 8;
const MAX_WIKI_RESULTS = 5;
const MAX_PAGE_CHARS = 8_000;
const MAX_QUERY_CHARS = 400;

const SEARCH_REDIRECT_HOST = /(?:^|\/\/)(?:[a-z0-9-]+\.)?duckduckgo\.com\//i;

// ── HTML helpers ───────────────────────────────────────────────────────

const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
};

/** Decode the HTML entities DDG/Wikipedia actually emit in result text. */
export function decodeHtmlEntities(value: string): string {
  return value.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (match, entity: string) => {
    if (entity.startsWith("#x") || entity.startsWith("#X")) {
      const code = Number.parseInt(entity.slice(2), 16);
      return Number.isFinite(code) ? String.fromCodePoint(code) : match;
    }
    if (entity.startsWith("#")) {
      const code = Number.parseInt(entity.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : match;
    }
    return NAMED_ENTITIES[entity.toLowerCase()] ?? match;
  });
}

/** Strip tags and collapse whitespace into a single-line text snippet. */
export function stripHtml(value: string): string {
  return decodeHtmlEntities(value.replace(/<[^>]*>/g, " ")).replace(/\s+/g, " ").trim();
}

/**
 * Unwrap a DuckDuckGo redirect (`/l/?uddg=<encoded>`) or protocol-relative
 * href into the real destination URL. Returns "" when nothing usable remains.
 */
export function unwrapResultUrl(href: string): string {
  if (!href) return "";
  const unescaped = href.replace(/&amp;/g, "&");
  const uddg = /[?&]uddg=([^&]+)/.exec(unescaped);
  const candidate = uddg
    ? safeDecodeURIComponent(uddg[1])
    : unescaped.startsWith("//")
      ? `https:${unescaped}`
      : unescaped;
  if (!/^https?:\/\//i.test(candidate)) return "";
  // A remaining DDG host means the wrapper could not be decoded.
  if (!uddg && SEARCH_REDIRECT_HOST.test(candidate)) return "";
  return candidate;
}

function safeDecodeURIComponent(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

type Anchor = { href: string; html: string; index: number };

function findAnchors(html: string, className: string): Anchor[] {
  const classRe = new RegExp(`class="[^"]*\\b${className}\\b[^"]*"`, "i");
  const anchors: Anchor[] = [];
  const re = /<a\b([^>]*)>([\s\S]*?)<\/a>/gi;
  let match: RegExpExecArray | null;
  while ((match = re.exec(html)) !== null) {
    const attrs = match[1] ?? "";
    if (!classRe.test(attrs)) continue;
    const href = /href="([^"]*)"/i.exec(attrs)?.[1] ?? "";
    anchors.push({ href, html: match[2] ?? "", index: match.index });
  }
  return anchors;
}

/**
 * Parse DuckDuckGo's HTML result page. Pairs each `result__snippet` with the
 * nearest preceding `result__a` anchor so an anchor-less result cannot shift
 * every later snippet by one.
 */
export function parseDuckDuckGoHtml(html: string, limit = MAX_SEARCH_RESULTS): WebSearchItem[] {
  if (!html) return [];
  const titles = findAnchors(html, "result__a");
  const snippets = findAnchors(html, "result__snippet");
  const items: WebSearchItem[] = [];
  for (const title of titles) {
    const url = unwrapResultUrl(title.href);
    const titleText = stripHtml(title.html);
    if (!url && !titleText) continue;
    const snippet = snippets.find((entry) => entry.index > title.index);
    items.push({
      title: titleText,
      url,
      snippet: snippet ? stripHtml(snippet.html) : "",
    });
    if (items.length >= limit) break;
  }
  return items;
}

// ── Providers ──────────────────────────────────────────────────────────

function buildResult(
  status: "succeeded" | "failed",
  items: WebSearchItem[],
  provider?: "duckduckgo" | "wikipedia",
  message?: string,
): WebSearchOutcome {
  return { status, items, ...(provider ? { provider } : {}), ...(message ? { message } : {}) };
}

async function searchDuckDuckGo(query: string, fetchImpl: typeof fetch): Promise<WebSearchOutcome> {
  try {
    const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
    const response = await fetchImpl(url, {
      method: "GET",
      headers: {
        "User-Agent": BROWSER_UA,
        Accept: "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
      },
      signal: AbortSignal.timeout(WEB_SEARCH_TIMEOUT_MS),
    });
    if (!response.ok) {
      return buildResult("failed", [], undefined, `DuckDuckGo 返回 HTTP ${response.status}`);
    }
    const html = await response.text();
    const items = parseDuckDuckGoHtml(html);
    if (items.length === 0) {
      return buildResult("failed", [], undefined, "DuckDuckGo 没有返回可解析的结果");
    }
    return buildResult("succeeded", items, "duckduckgo");
  } catch (error) {
    return buildResult("failed", [], undefined, `DuckDuckGo 请求失败（${describeError(error)}）`);
  }
}

export function parseWikipediaSearch(payload: unknown, limit = MAX_WIKI_RESULTS): WebSearchItem[] {
  const search = (payload as { query?: { search?: unknown } } | null)?.query?.search;
  if (!Array.isArray(search)) return [];
  const items: WebSearchItem[] = [];
  for (const entry of search) {
    if (!entry || typeof entry !== "object") continue;
    const raw = entry as Record<string, unknown>;
    const title = typeof raw.title === "string" ? raw.title : "";
    if (!title) continue;
    items.push({
      title,
      url: `https://en.wikipedia.org/wiki/${encodeURIComponent(title.replace(/ /g, "_"))}`,
      snippet: typeof raw.snippet === "string" ? stripHtml(raw.snippet) : "",
    });
    if (items.length >= limit) break;
  }
  return items;
}

async function searchWikipedia(query: string, fetchImpl: typeof fetch): Promise<WebSearchOutcome> {
  try {
    const url = "https://en.wikipedia.org/w/api.php"
      + `?action=query&list=search&srsearch=${encodeURIComponent(query)}`
      + `&format=json&utf8=1&srlimit=${MAX_WIKI_RESULTS}`;
    const response = await fetchImpl(url, {
      method: "GET",
      headers: { "User-Agent": BROWSER_UA, Accept: "application/json" },
      signal: AbortSignal.timeout(WEB_SEARCH_TIMEOUT_MS),
    });
    if (!response.ok) {
      return buildResult("failed", [], undefined, `Wikipedia 返回 HTTP ${response.status}`);
    }
    const payload = await response.json();
    const items = parseWikipediaSearch(payload);
    if (items.length === 0) {
      return buildResult("failed", [], undefined, "Wikipedia 没有返回结果");
    }
    return buildResult("succeeded", items, "wikipedia");
  } catch (error) {
    return buildResult("failed", [], undefined, `Wikipedia 请求失败（${describeError(error)}）`);
  }
}

/**
 * web_search with the fixed degradation chain: DuckDuckGo → Wikipedia.
 * `fetchImpl` is a test seam only.
 */
export async function runWebSearch(query: string, fetchImpl: typeof fetch = fetch): Promise<WebSearchOutcome> {
  const trimmed = query.trim();
  if (!trimmed) {
    throw new Error("web_search 需要非空的 query");
  }
  const bounded = trimmed.slice(0, MAX_QUERY_CHARS);
  const duck = await searchDuckDuckGo(bounded, fetchImpl);
  if (duck.status === "succeeded") return duck;
  const wiki = await searchWikipedia(bounded, fetchImpl);
  if (wiki.status === "succeeded") {
    return { ...wiki, message: `DuckDuckGo 不可用（${duck.message ?? "未知原因"}），已回退 Wikipedia。` };
  }
  return buildResult(
    "failed",
    [],
    undefined,
    `联网搜索失败：DuckDuckGo（${duck.message ?? "未知原因"}）与 Wikipedia（${wiki.message ?? "未知原因"}）均未返回结果。`,
  );
}

// ── fetch_page ─────────────────────────────────────────────────────────

export function isValidHttpUrl(value: unknown): value is string {
  if (typeof value !== "string" || !value.trim()) return false;
  try {
    const parsed = new URL(value.trim());
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export function truncatePage(text: string, limit = MAX_PAGE_CHARS): string {
  if (text.length <= limit) return text;
  return `${text.slice(0, limit)}\n\n…[页面内容已截断：原文 ${text.length} 字符，仅保留前 ${limit} 字符]`;
}

/** fetch_page through r.jina.ai. `fetchImpl` is a test seam only. */
export async function runFetchPage(url: string, fetchImpl: typeof fetch = fetch): Promise<{
  status: "succeeded" | "failed";
  text: string;
  message?: string;
}> {
  const trimmed = url.trim();
  if (!isValidHttpUrl(trimmed)) {
    throw new Error("fetch_page 的 url 必须是 http 或 https 地址");
  }
  try {
    const response = await fetchImpl(`https://r.jina.ai/${trimmed}`, {
      method: "GET",
      headers: { "User-Agent": BROWSER_UA, Accept: "text/plain" },
      signal: AbortSignal.timeout(FETCH_PAGE_TIMEOUT_MS),
    });
    if (response.ok) {
      const body = (await response.text()).trim();
      if (body) return { status: "succeeded", text: truncatePage(body) };
    }
    // r.jina.ai now rate-limits/blocks keyless anonymous queries by IP
    // reputation (401/403 from a dev machine, 2026-09-13). Degrade to fetching
    // the page directly and stripping tags rather than returning nothing.
    return await fetchPageDirect(trimmed, fetchImpl, response.status);
  } catch (error) {
    return await fetchPageDirect(trimmed, fetchImpl, describeError(error));
  }
}

/** Direct fetch + tag strip fallback when r.jina.ai is unavailable. */
async function fetchPageDirect(
  url: string,
  fetchImpl: typeof fetch,
  readerFailure: number | string,
): Promise<{ status: "succeeded" | "failed"; text: string; message?: string }> {
  try {
    const response = await fetchImpl(url, {
      method: "GET",
      headers: {
        "User-Agent": BROWSER_UA,
        Accept: "text/html,application/xhtml+xml,text/plain",
        "Accept-Language": "en-US,en;q=0.9",
      },
      signal: AbortSignal.timeout(FETCH_PAGE_TIMEOUT_MS),
    });
    if (!response.ok) {
      return {
        status: "failed",
        text: "",
        message: `抓取页面失败：r.jina.ai 不可用（HTTP ${readerFailure}），直连目标页返回 HTTP ${response.status}`,
      };
    }
    const contentType = response.headers.get("content-type") ?? "";
    const raw = await response.text();
    const text = /text\/plain|application\/(?:json|xml)/i.test(contentType)
      ? raw.trim()
      : htmlToText(raw);
    if (!text) {
      return { status: "failed", text: "", message: "抓取页面失败：直连目标页未得到可读正文" };
    }
    return { status: "succeeded", text: truncatePage(text) };
  } catch (error) {
    return {
      status: "failed",
      text: "",
      message: `抓取页面失败：r.jina.ai 不可用（${readerFailure}），直连目标页也失败（${describeError(error)}）`,
    };
  }
}

/** Minimal readable-text extraction for the direct-fetch fallback. */
export function htmlToText(html: string): string {
  return decodeHtmlEntities(
    html
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<(?:br|\/p|\/div|\/li|\/h[1-6])\s*>/gi, "\n")
      .replace(/<[^>]*>/g, " "),
  )
    .replace(/[ \t\u00a0]+/g, " ")
    .replace(/\n\s*\n\s*\n+/g, "\n\n")
    .split("\n")
    .map((line) => line.trim())
    .join("\n")
    .trim();
}

function describeError(error: unknown): string {
  const name = error instanceof Error ? error.name : "";
  if (name === "TimeoutError") return "超时";
  const message = error instanceof Error ? error.message : String(error);
  return message.slice(0, 80);
}

// ── Tool registration ──────────────────────────────────────────────────

type TypeBuilder = {
  Object(properties: Record<string, unknown>, options?: Record<string, unknown>): unknown;
  String(options?: Record<string, unknown>): unknown;
};

function formatSearchText(query: string, outcome: WebSearchOutcome): string {
  if (outcome.status === "failed" || outcome.items.length === 0) {
    return `${outcome.message ?? "联网搜索没有返回结果。"}（query: ${query}）`;
  }
  const lines = outcome.items.map((item, index) => {
    const snippet = item.snippet ? `\n   ${item.snippet}` : "";
    return `${index + 1}. ${item.title}\n   ${item.url}${snippet}`;
  });
  const provider = outcome.provider === "wikipedia" ? "Wikipedia（DuckDuckGo 降级）" : "DuckDuckGo";
  return `搜索「${query}」结果（来源：${provider}，共 ${outcome.items.length} 条）：\n${lines.join("\n")}`;
}

/**
 * Build the two native web tools for the Coach harness. Loaded lazily so the
 * pure parsers above stay importable without the pinned Pi source.
 */
export async function createWebSearchTools(): Promise<Array<{
  name: string;
  label: string;
  description: string;
  parameters: unknown;
  execute: (id: string, args: Record<string, unknown>) => Promise<unknown>;
}>> {
  const { Type } = (await loadPiAi()) as { Type: TypeBuilder };

  const webSearch = {
    name: "web_search",
    label: "Search the web",
    description:
      "联网搜索公开资料，返回前 8 条标题、链接与摘要。用户问到本地知识库没有覆盖、或需要官方生态/最新资料（如某场景是否在 KovaaK 商店上架、外设官方参数、社区最新说法）时使用。" +
      "查询用简短关键词或一句具体问题；不要用它查用户自己的本地数据（用 read/产品命令）。搜索结果只是线索，引用时要注明来源链接。",
    parameters: Type.Object({
      query: Type.String({ description: "搜索关键词或问题，非空", minLength: 1, maxLength: MAX_QUERY_CHARS }),
    }),
    execute: async (_id: string, args: Record<string, unknown>) => {
      const query = typeof args.query === "string" ? args.query : "";
      const outcome = await runWebSearch(query);
      return {
        content: [{ type: "text", text: formatSearchText(query.trim(), outcome) }],
        details: { tool: "web_search", status: outcome.status, provider: outcome.provider ?? null },
      };
    },
  };

  const fetchPage = {
    name: "fetch_page",
    label: "Fetch a page",
    description:
      "抓取一个 http/https 网页并返回干净的正文文本（Markdown 形式，截断到约 8000 字符）。用于 web_search 命中后深入读官方页面/商店页/指南页。" +
      "只传完整 URL；抓取失败会给出可读原因，可换一个结果重试。",
    parameters: Type.Object({
      url: Type.String({ description: "完整的 http 或 https 网页地址", minLength: 1, maxLength: 2000 }),
    }),
    execute: async (_id: string, args: Record<string, unknown>) => {
      const url = typeof args.url === "string" ? args.url : "";
      const outcome = await runFetchPage(url);
      const text = outcome.status === "succeeded"
        ? `页面 ${url} 的正文（已清洗，可能截断）：\n\n${outcome.text}`
        : `${outcome.message ?? "抓取页面失败。"}（url: ${url}）`;
      return {
        content: [{ type: "text", text }],
        details: { tool: "fetch_page", status: outcome.status, url },
      };
    },
  };

  return [webSearch, fetchPage];
}

export const WEB_SEARCH_TOOL_NAMES = ["web_search", "fetch_page"] as const;
