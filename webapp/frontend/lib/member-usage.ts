/**
 * 「调用记录」卡的纯展示逻辑（无 React、无请求）。
 *
 * 数据源是本机 sidecar 的 GET /v1/usage/records（sidecar-coach-data.ts 的
 * listCoachUsageRecords）：Pi 会话 JSONL 里 assistant 消息自带的 usage 直读，
 * 纯本地、断网可用。这里的函数只做数字/时间格式化，不发请求、不碰凭据。
 *
 * 展示口径：
 * - usage 数值 null 表示 provider 没报（0 是真实计量），展示侧一律按 0 兜底；
 * - 时间戳是 ISO 串，按**本地时区**渲染（用户看的是自己机器上的日历）。
 */

import type { CoachUsageRecord } from "./api";

/** 千分位 token 数（1,683）。两语言统一千分位逗号形态（en-US 分组）。 */
export function formatTokenCount(n: number): string {
  if (!Number.isFinite(n)) return "0";
  return new Intl.NumberFormat("en-US").format(Math.trunc(n));
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

/** 同一年判断（本地时区）。 */
function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/**
 * 记录时间戳 → 列表展示时间（本地时区）：
 * 今天 → 「今天 HH:mm」/「Today HH:mm」；昨天 → 「昨天/Yesterday HH:mm」；
 * 今年内 → 「MM-DD HH:mm」；更早 → 「YYYY-MM-DD HH:mm」。
 * 非法时间戳回退空串（调用方所在行随之少一格，不编造时间）。
 */
export function formatUsageTime(iso: string, now: Date, locale: "zh-CN" | "en-US"): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const time = `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (isSameDay(date, now)) return `${locale === "en-US" ? "Today" : "今天"} ${time}`;
  if (isSameDay(date, yesterday)) return `${locale === "en-US" ? "Yesterday" : "昨天"} ${time}`;
  const day = `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
  if (date.getFullYear() === now.getFullYear()) return `${day} ${time}`;
  return `${date.getFullYear()}-${day} ${time}`;
}

/**
 * 缓存命中率（百分数，四舍五入到整数）：cache_read/(input+cache_read)。
 * 分母为 0（没有任何计量可算）时回 null——不是 0%，前端据此整格显示占位。
 */
export function cacheHitRate(input: number, cacheRead: number): number | null {
  const denominator = input + cacheRead;
  if (!Number.isFinite(denominator) || denominator <= 0) return null;
  return Math.round((cacheRead / denominator) * 100);
}

/** 单条记录的 usage 数值（null 按 0 兜底；缺字段同样按 0）。 */
export function usageNumbers(record: CoachUsageRecord): {
  input: number;
  output: number;
  cacheRead: number;
} {
  return {
    input: record.usage.input ?? 0,
    output: record.usage.output ?? 0,
    cacheRead: record.usage.cache_read ?? 0,
  };
}
