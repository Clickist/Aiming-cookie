import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

import { DICTIONARIES, type Locale } from "../lib/i18n/core";

// 调用记录（用户中心新卡 + sidecar 新端点）的源码断言，与
// tests/coach-send-poll-resilience.test.ts 同款风格：锁接线与顺序，
// 行为断言在 lib/member-usage.test.ts 与 coach-runtime 侧。
const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

const USAGE_KEYS = [
  "member.center.usage.title",
  "member.center.usage.monthCount",
  "member.center.usage.statInput",
  "member.center.usage.statOutput",
  "member.center.usage.statCacheHit",
  "member.center.usage.empty",
  "member.center.usage.more",
  "member.center.usage.less",
  "member.center.usage.untitled",
  "member.center.usage.rowTokens",
];

test("api client exposes getCoachUsageRecords hitting /v1/usage/records", async () => {
  const api = await source("lib/api.ts");
  assert.match(api, /export async function getCoachUsageRecords\(/);
  assert.match(api, /`\/v1\/usage\/records\$\{query \? `\?\$\{query\}` : ""\}`/);
  // limit 走 query 参数（服务端 clamp [1,200]）。
  assert.match(api, /params\.set\("limit"/);
  // 非 ok 抛 apiError（与其它 sidecar 端点同一错误口径）。
  const fnChunk = api.slice(
    api.indexOf("export async function getCoachUsageRecords("),
    api.indexOf("export async function getCoachSession("),
  );
  assert.match(fnChunk, /if \(!res\.ok\) throw await apiError\(res\);/);
  // 契约类型是文件内局部类型（未进后端 OpenAPI 前的先例口径）。
  assert.match(api, /export interface CoachUsageRecordsResponse/);
  assert.match(api, /export interface CoachUsageRecord /);
});

test("MemberCenter renders the usage card between the booster card and the account card", async () => {
  const center = await source("components/task3/MemberCenter.tsx");
  const boosterAt = center.indexOf("MEMBER_COPY.boosterBuyBody");
  const accountAt = center.indexOf('className="task3-member-account-card"');
  assert.ok(boosterAt !== -1, "booster card must exist");
  assert.ok(accountAt !== -1, "account card must exist");
  // 两处渲染：未订阅分支（未订阅卡之后）与会员分支（加油包卡之后、账户卡之前）——
  // 0928 真机实测点点当前账号即未订阅态，只放会员分支会整卡不可见。
  const occurrences = center.split("<MemberUsageCard />").length - 1;
  assert.equal(occurrences, 2, "usage card must render in both logged-in branches");
  const unsubscribedAt = center.indexOf("MEMBER_COPY.notSubscribedCenterBody");
  const firstUsageAt = center.indexOf("<MemberUsageCard />");
  const memberBranchAt = center.indexOf(") : me ? (");
  const secondUsageAt = center.indexOf("<MemberUsageCard />", memberBranchAt);
  assert.ok(unsubscribedAt !== -1 && firstUsageAt > unsubscribedAt, "unsubscribed branch must render the usage card after the upsell card");
  // 线框顺序（会员分支）：加油包卡 → 调用记录卡 → 账户卡。
  assert.ok(secondUsageAt !== -1, "member branch must render the usage card");
  assert.ok(boosterAt < secondUsageAt, "member branch: usage card must come after the booster card");
  assert.ok(secondUsageAt < accountAt, "member branch: usage card must come before the account card");
});

test("usage card fetches on mount with an AbortController and fails soft", async () => {
  const center = await source("components/task3/MemberCenter.tsx");
  assert.match(center, /getCoachUsageRecords\(\{ limit: 50, signal: controller\.signal \}\)/);
  assert.match(center, /return \(\) => controller\.abort\(\);/);
  // 取数失败整卡不渲染（浏览器 mock 环境没有 sidecar，不显示报错）。
  assert.match(center, /if \(!data\) return null;/);
  // 默认前 5 条 + 「查看更多」展开。
  assert.match(center, /const USAGE_VISIBLE_ROWS = 5;/);
  assert.match(center, /data\.records\.slice\(0, USAGE_VISIBLE_ROWS\)/);
  assert.match(center, /member\.center\.usage\.more/);
  assert.match(center, /member\.center\.usage\.less/);
  // 空态文案（records 为空仍渲染卡片）。
  assert.match(center, /member\.center\.usage\.empty/);
});

test("usage card styles live in task3.css and use CSS variables only (no hard-coded colors)", async () => {
  const css = await source("components/task3/task3.css");
  assert.match(css, /\.task3-member-usage-card \{/);
  assert.match(css, /\.task3-member-usage-chip \{/);
  assert.match(css, /\.task3-member-usage-row \{/);
  // 该区块不得写死色值（暗浅两色主题只靠变量）。
  const blockStart = css.indexOf(".task3-member-usage-card {");
  const block = css.slice(blockStart, css.length);
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,6}\b/, "调用记录样式区块不得写死 hex 色值");
  assert.doesNotMatch(block, /rgb\(|hsl\(/, "调用记录样式区块不得写死 rgb/hsl 色值");
  // 时间列固定宽 + tabular-nums（线框：各行数字对齐）。
  assert.match(block, /\.task3-member-usage-time \{[\s\S]*?font-variant-numeric: tabular-nums;/);
});

test("usage keys exist in both dictionaries (zh-CN source and en-US)", () => {
  for (const locale of Object.keys(DICTIONARIES) as Locale[]) {
    for (const key of USAGE_KEYS) {
      assert.ok(
        Object.prototype.hasOwnProperty.call(DICTIONARIES[locale], key),
        `${locale} 缺少 ${key}`,
      );
    }
  }
});

test("usage keys interpolate their placeholders in both locales", async () => {
  const { translate } = await import("../lib/i18n/core");
  for (const locale of Object.keys(DICTIONARIES) as Locale[]) {
    assert.doesNotMatch(
      translate(locale, "member.center.usage.monthCount", { count: 12, month: 9 }),
      /\{(?:count|month)\}/,
      `${locale} monthCount 未替换占位符`,
    );
    assert.doesNotMatch(
      translate(locale, "member.center.usage.rowTokens", { input: "1,683", output: 2, cache: "8,192" }),
      /\{(?:input|output|cache)\}/,
      `${locale} rowTokens 未替换占位符`,
    );
  }
});
