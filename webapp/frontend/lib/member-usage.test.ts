import assert from "node:assert/strict";
import { test } from "node:test";

import { cacheHitRate, formatTokenCount, formatUsageTime, usageNumbers } from "./member-usage";
import type { CoachUsageRecord } from "./api";

// 「调用记录」卡纯逻辑（用户中心线框）：token 千分位、本地时区时间标签、
// 缓存命中率（分母 0 → null）、usage 数值 null 按 0 兜底。

test("formatTokenCount adds thousands separators and truncates fractions", () => {
  assert.equal(formatTokenCount(0), "0");
  assert.equal(formatTokenCount(999), "999");
  assert.equal(formatTokenCount(1683), "1,683");
  assert.equal(formatTokenCount(9877), "9,877");
  assert.equal(formatTokenCount(1_234_567), "1,234,567");
  // 理论上的小数（provider 会计）落到整数展示。
  assert.equal(formatTokenCount(12.7), "12");
  // 非有限数不产出 NaN 字样。
  assert.equal(formatTokenCount(Number.NaN), "0");
  assert.equal(formatTokenCount(Number.POSITIVE_INFINITY), "0");
});

test("formatUsageTime: today / yesterday / this year / earlier, local time", () => {
  const now = new Date(2026, 8, 28, 15, 30, 0); // 2026-09-28 15:30 本地
  const at = (y: number, m: number, d: number, h: number, min: number) =>
    new Date(y, m, d, h, min, 0).toISOString();

  // 今天（含跨零点边界：同一本地日不同时刻都算今天）。
  assert.equal(formatUsageTime(at(2026, 8, 28, 2, 14), now, "zh-CN"), "今天 02:14");
  assert.equal(formatUsageTime(at(2026, 8, 28, 15, 30), now, "en-US"), "Today 15:30");
  // 昨天（now 前一日；跨月边界）。
  assert.equal(formatUsageTime(at(2026, 8, 27, 23, 59), now, "zh-CN"), "昨天 23:59");
  assert.equal(formatUsageTime(at(2026, 7, 31, 8, 0), new Date(2026, 8, 1, 12, 0), "en-US"), "Yesterday 08:00");
  // 今年内更早（含跨年边界：1 月的 now 看去年 12 月＝去年→带年份）。
  assert.equal(formatUsageTime(at(2026, 0, 5, 9, 5), now, "zh-CN"), "01-05 09:05");
  assert.equal(formatUsageTime(at(2026, 8, 1, 9, 5), now, "en-US"), "09-01 09:05");
  // 更早：带年份（跨年）。
  assert.equal(formatUsageTime(at(2025, 11, 31, 23, 0), now, "zh-CN"), "2025-12-31 23:00");
  assert.equal(formatUsageTime(at(2025, 0, 1, 0, 0), now, "en-US"), "2025-01-01 00:00");
  // 非法时间戳：空串（不编造）。
  assert.equal(formatUsageTime("not-a-date", now, "zh-CN"), "");
});

test("formatUsageTime单双位补零：小时/分钟/月/日都两位", () => {
  const now = new Date(2026, 0, 1, 0, 5, 0);
  assert.equal(formatUsageTime(new Date(2026, 0, 1, 0, 5, 0).toISOString(), now, "zh-CN"), "今天 00:05");
  // 去年同日不算今年（跨年后同一 MM-DD 仍带年份）。
  assert.equal(formatUsageTime(new Date(2025, 0, 1, 7, 0, 0).toISOString(), now, "zh-CN"), "2025-01-01 07:00");
});

test("cacheHitRate: rounded percentage, null on a zero denominator", () => {
  assert.equal(cacheHitRate(1000, 3000), 75);
  assert.equal(cacheHitRate(1683, 8192), 83); // 8192/9875 = 82.96…
  assert.equal(cacheHitRate(0, 100), 100); // 全缓存命中
  assert.equal(cacheHitRate(100, 0), 0); // 完全没命中（0 是真实计量）
  // 分母 0 → null：没有任何计量可算，不是 0%。
  assert.equal(cacheHitRate(0, 0), null);
  assert.equal(cacheHitRate(Number.NaN, 10), null);
});

test("usageNumbers falls back to 0 for unreported (null) fields", () => {
  const record: CoachUsageRecord = {
    session_id: 1,
    session_title: null,
    model: "deepseek-v4-flash",
    provider: "relay-station",
    timestamp: "2026-09-28T02:14:06.913Z",
    usage: { input: 1683, output: null, cache_read: 8192, cache_write: null, reasoning: null, total_tokens: null },
  };
  assert.deepEqual(usageNumbers(record), { input: 1683, output: 0, cacheRead: 8192 });
});
