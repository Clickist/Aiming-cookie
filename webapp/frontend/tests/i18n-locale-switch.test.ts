import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

import { formatHistoryDate } from "../lib/contracts";
import { LOCALE_STORAGE_KEY, loadStoredLocale, setLocale, translate } from "../lib/i18n/core";

// i18n 收尾批（语言切换 UI + 日期 locale 化）的回归锁：
// 1) en 字典对三页代表文案给出真英文；2) 设置页语言行接线 useLocale→setLocale；
// 3) 四个日期展示点跟随 getLocale() 不再硬编码 zh-CN；
// 4) setLocale 后日期格式行为真的随 locale 变化且可恢复。
const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

// 设置页 / 首页 / 历史页各抽样的代表文案：en 侧必须与 zh 不同且不含汉字——
// 字典层面证明切到 en-US 后界面文案真的变英文。
const REPRESENTATIVE_KEYS = [
  "settings.page.title",        // 设置页标题「设置」
  "settings.nav.general",       // 设置页导航「通用」
  "settings.theme.title",       // 设置页主题小节
  "settings.language.title",    // 语言行标题（本批新增）
  "coach.composer.placeholder", // 首页教练输入框占位
  "coach.rail.history",         // 首页侧栏页脚「历史」
  "history.page.title",         // 历史页标题
  "history.status.queued",      // 历史列表状态词
] as const;

test("en-US 字典对设置页/首页/历史页代表文案给出英文（无汉字、与 zh 不同）", () => {
  for (const key of REPRESENTATIVE_KEYS) {
    const zh = translate("zh-CN", key);
    const en = translate("en-US", key);
    assert.notEqual(en, zh, `${key} 的 en 与 zh 相同——英文侧未翻译`);
    assert.doesNotMatch(en, /[\u4e00-\u9fff]/, `${key} 的 en 含汉字: ${en}`);
  }
});

test("语言行新键双侧入库，语言名是 locale 不变量", () => {
  for (const key of [
    "settings.language.title",
    "settings.language.desc",
    "settings.language.choiceAria",
  ] as const) {
    assert.notEqual(translate("en-US", key), key, `${key} en 缺失（回退成裸键）`);
    assert.notEqual(translate("zh-CN", key), key, `${key} zh 缺失（回退成裸键）`);
  }
  // 语言名固定用各自语言书写：两种界面语言下都显示「简体中文」/「English」。
  assert.equal(translate("zh-CN", "settings.language.zh"), translate("en-US", "settings.language.zh"));
  assert.equal(translate("zh-CN", "settings.language.en"), translate("en-US", "settings.language.en"));
});

test("设置页通用屏语言行接线 useLocale→setLocale（两档单选卡）", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /const \{ locale, setLocale: applyLocale \} = useLocale\(\);/);
  assert.match(settings, /onChange=\{\(\) => applyLocale\(option\.value\)\}/);
  assert.match(settings, /\{ value: "zh-CN", label: t\("settings\.language\.zh"\) \}/);
  assert.match(settings, /\{ value: "en-US", label: t\("settings\.language\.en"\) \}/);
  assert.match(settings, /checked=\{locale === option\.value\}/);
  assert.match(settings, /role="radiogroup" aria-label=\{t\("settings\.language\.choiceAria"\)\}/);
});

test("四个日期展示点跟随 getLocale()，不再硬编码 zh-CN", async () => {
  const dateSites = [
    "components/kovaak/KovaaKConnectionPanel.tsx",
    "components/task6/SettingsWorkspace.tsx",
    "components/task6/KnowledgeSettingsSection.tsx",
    "components/task4/HistoryClient.tsx",
  ] as const;
  for (const file of dateSites) {
    const value = await source(file);
    assert.match(value, /getLocale\(\) === "en-US" \? "en-US" : "zh-CN"/, `${file} 未跟随 getLocale`);
    assert.doesNotMatch(value, /Intl\.DateTimeFormat\("zh-CN"/, `${file} 仍硬编码 Intl zh-CN`);
    assert.doesNotMatch(value, /toLocaleTimeString\("zh-CN"/, `${file} 仍硬编码 toLocaleTimeString zh-CN`);
  }
});

test("setLocale 切换后日期/时间格式随 locale 变化并可切回恢复（行为）", () => {
  // 一分钟前的时间恒落「今天」桶：组头词本身也随 locale 翻译（今天 → Today）。
  const iso = new Date(Date.now() - 60_000).toISOString();
  const todayPrefix = (locale: "zh-CN" | "en-US"): string =>
    translate(locale, "history.time.today").replace(/\{time\}[\s\S]*$/, "").trim();
  try {
    setLocale("zh-CN");
    const zh = formatHistoryDate(iso);
    assert.ok(zh.startsWith(todayPrefix("zh-CN")), `zh 组头应为「今天」: ${zh}`);
    assert.doesNotMatch(zh, /AM|PM/, `zh 时间不应带 AM/PM: ${zh}`);

    setLocale("en-US");
    const en = formatHistoryDate(iso);
    assert.ok(en.startsWith(todayPrefix("en-US")), `en 组头应为 Today: ${en}`);
    assert.match(en, /AM|PM/, `en 时间应为 12 小时制英文格式: ${en}`);
    assert.notEqual(en, zh, "切语言后日期文案未变化");

    setLocale("zh-CN");
    assert.equal(formatHistoryDate(iso), zh, "切回 zh-CN 后未恢复原格式");
  } finally {
    setLocale("zh-CN");
  }
});

test("首次启动按系统语言预选；手动选择一经落库不再跟随系统", () => {
  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const store = new Map<string, string>();
  const localStorageMock = {
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  };
  const setNavigatorLanguage = (language: string | undefined): void => {
    Object.defineProperty(globalThis, "navigator", {
      value: language === undefined ? {} : { language },
      configurable: true,
    });
  };
  try {
    (globalThis as { window?: unknown }).window = { localStorage: localStorageMock };

    // 无存储偏好 + 中文系统（含繁体）→ 预选 zh-CN
    setLocale("zh-CN");
    store.clear();
    setNavigatorLanguage("zh-TW");
    assert.equal(loadStoredLocale(), "zh-CN", "繁体系统应预选中文");
    setLocale("zh-CN");
    store.clear();
    setNavigatorLanguage("zh-CN");
    assert.equal(loadStoredLocale(), "zh-CN", "简体系统应预选中文");

    // 无存储偏好 + 非中文系统（英/日）→ 预选 en-US
    setLocale("zh-CN");
    store.clear();
    setNavigatorLanguage("en-US");
    assert.equal(loadStoredLocale(), "en-US", "英文系统应预选英文");
    setLocale("zh-CN");
    store.clear();
    setNavigatorLanguage("ja-JP");
    assert.equal(loadStoredLocale(), "en-US", "日文系统应预选英文（无对应字典回落英语）");

    // 系统语言读不到 → 保守回默认 zh-CN
    setLocale("zh-CN");
    store.clear();
    setNavigatorLanguage(undefined);
    assert.equal(loadStoredLocale(), "zh-CN", "读不到系统语言应保守回默认");

    // 手动选择已落库：系统语言不再影响
    setLocale("zh-CN");
    store.set(LOCALE_STORAGE_KEY, "zh-CN");
    setNavigatorLanguage("en-US");
    assert.equal(loadStoredLocale(), "zh-CN", "已手动选择中文后不应被英文系统改写");
  } finally {
    delete (globalThis as { window?: unknown }).window;
    if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator);
    else delete (globalThis as { navigator?: unknown }).navigator;
    setLocale("zh-CN");
  }
});
