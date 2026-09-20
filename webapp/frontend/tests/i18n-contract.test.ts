import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

import {
  DEFAULT_LOCALE,
  DICTIONARIES,
  LOCALE_STORAGE_KEY,
  getLocale,
  interpolate,
  normalizeLocale,
  setLocale,
  subscribeLocale,
  t,
  translate,
  type Locale,
} from "../lib/i18n/core";
import { enUS } from "../lib/i18n/en-US";
import { zhCN } from "../lib/i18n/zh-CN";

// i18n 基建契约（批 0 建立，后续每个抽取批都要保持全绿）：
// 键集一致、键全局唯一、键名规范、缺失 key 回退、插值行为、持久化与订阅语义。
// 施工口径见 .zcode/i18n-conventions.md。

const frontendRoot = path.resolve(import.meta.dirname, "..");
const LOCALES = Object.keys(DICTIONARIES) as Locale[];

test("所有 locale 的键集合与 zh-CN 源字典完全一致（无缺漏、无多余）", () => {
  const sourceKeys = Object.keys(zhCN).sort();
  assert.ok(sourceKeys.length > 0, "zh-CN 字典为空——至少要保留样板条目");
  for (const locale of LOCALES) {
    const keys = Object.keys(DICTIONARIES[locale]).sort();
    assert.deepEqual(
      keys,
      sourceKeys,
      `${locale} 与 zh-CN 键集不一致：缺失 [${sourceKeys.filter((key) => !keys.includes(key)).join(", ")}]，多余 [${keys.filter((key) => !sourceKeys.includes(key)).join(", ")}]`,
    );
  }
});

test("字典键全局唯一（防后续拆分/合并字典文件时键被静默覆盖）", () => {
  for (const locale of LOCALES) {
    const keys = Object.keys(DICTIONARIES[locale]);
    assert.equal(new Set(keys).size, keys.length, `${locale} 存在重复键`);
  }
});

test("键名遵循小写点分命名空间规范（至少两段，段以小写字母开头）", () => {
  for (const key of Object.keys(zhCN)) {
    assert.match(key, /^[a-z][a-zA-Z0-9]*(\.[a-z][a-zA-Z0-9]*)+$/, key);
  }
});

test("字典值不允许空串（缺译应表现为缺键，而不是空文案）", () => {
  for (const locale of LOCALES) {
    for (const [key, value] of Object.entries(DICTIONARIES[locale])) {
      assert.ok(value.trim().length > 0, `${locale} 的 ${key} 是空串`);
    }
  }
});

test("查表缺 key 时原样返回键本身（缺失可被测试与肉眼捕获）", () => {
  assert.equal(translate("zh-CN", "missing.key"), "missing.key");
  assert.equal(translate("en-US", "missing.key"), "missing.key");
  // 键不做任何加工，带奇怪字符也原样透出。
  assert.equal(translate("zh-CN", "missing.{weird"), "missing.{weird");
});

test("插值：{name} 占位替换、缺参原样保留、不传参不动模板、$ 特殊串不二次解释", () => {
  setLocale("zh-CN");
  assert.equal(t("update.prompt.title", { version: "1.2.5" }), "发现新版本 1.2.5");
  assert.equal(t("update.prompt.title", { other: "x" }), "发现新版本 {version}");
  assert.equal(t("update.prompt.title"), "发现新版本 {version}");
  assert.equal(interpolate("a {v} b", { v: "$&$`$'" }), "a $&$`$' b");
});

test("t() 跟随当前 locale，en-US 走 en 字典", () => {
  setLocale("en-US");
  try {
    assert.equal(getLocale(), "en-US");
    assert.equal(t("update.prompt.installNow"), "Update now");
    assert.equal(t("update.prompt.title", { version: "1.2.5" }), "Version 1.2.5 is available");
  } finally {
    setLocale(DEFAULT_LOCALE);
  }
});

test("locale 归一化与默认值：非法/缺失一律回退 zh-CN", () => {
  assert.equal(DEFAULT_LOCALE, "zh-CN");
  assert.equal(normalizeLocale(null), "zh-CN");
  assert.equal(normalizeLocale(undefined), "zh-CN");
  assert.equal(normalizeLocale("en"), "zh-CN");
  assert.equal(normalizeLocale(""), "zh-CN");
  assert.equal(normalizeLocale("en-US"), "en-US");
  assert.equal(normalizeLocale("zh-CN"), "zh-CN");
});

test("locale 持久化键跟随 theme 惯例；setLocale 通知订阅者且 getLocale 可读回", () => {
  assert.equal(LOCALE_STORAGE_KEY, "aiming-cookie.ui.locale");
  let notified = 0;
  const unsubscribe = subscribeLocale(() => {
    notified += 1;
  });
  try {
    setLocale("en-US");
    assert.equal(getLocale(), "en-US");
    assert.equal(notified, 1);
    setLocale("zh-CN");
    assert.equal(notified, 2);
  } finally {
    unsubscribe();
    setLocale(DEFAULT_LOCALE);
  }
});

test("en-US 字典以 satisfies 锁键集（运行时复核 tsc 的编译期约束）", () => {
  // en-US.ts 的 satisfies 只在 tsc（npm run lint）里生效；这里保证绕过编译
  // （如 tsx 直跑测试）时字典仍然逐键一致。
  assert.deepEqual(Object.keys(enUS).sort(), Object.keys(zhCN).sort());
});

test("样板组件 UpdatePrompt 已接字典：无裸中文字面量（注释除外）且经 useT 取文案", async () => {
  const source = await readFile(path.join(frontendRoot, "components", "task3", "UpdatePrompt.tsx"), "utf8");
  const withoutComments = source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "");
  assert.doesNotMatch(withoutComments, /[\u4e00-\u9fff]/, "UpdatePrompt 存在裸中文（应进 lib/i18n/zh-CN.ts 字典）");
  assert.match(source, /useT\(\)/);
});
