import { enUS } from "./en-US";
import { zhCN, type MessageKey } from "./zh-CN";

export type { MessageKey };

/** 支持的展示语言。zh-CN 是源语言与默认 locale：静态导出首帧（预渲染＋水合）恒为 zh-CN。 */
export type Locale = "zh-CN" | "en-US";

export const DEFAULT_LOCALE: Locale = "zh-CN";

// 持久化跟随 ui/theme-core.ts 的设置惯例（aiming-cookie.ui.theme）。
export const LOCALE_STORAGE_KEY = "aiming-cookie.ui.locale";

/** 插值参数：模板里 {name} 占位符的取值来源。 */
export type TranslateParams = Record<string, string | number>;

export type TranslateFn = (key: MessageKey, params?: TranslateParams) => string;

export const DICTIONARIES: Record<Locale, Record<MessageKey, string>> = {
  "zh-CN": zhCN,
  "en-US": enUS,
};

export function normalizeLocale(value: string | null | undefined): Locale {
  return value === "en-US" ? "en-US" : "zh-CN";
}

const PLACEHOLDER_RE = /\{(\w+)\}/g;

/**
 * {name} 风格插值：占位符在 params 里缺位时原样保留，让漏传参数在界面与测试里
 * 可见；replace 用函数形式，避免替换值里的 $& 等特殊串被二次解释。
 */
export function interpolate(template: string, params?: TranslateParams): string {
  if (!params) return template;
  return template.replace(PLACEHOLDER_RE, (placeholder, name: string) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : placeholder,
  );
}

/**
 * 显式 locale 的查表函数（t() 的底层）：当前语言字典 → zh-CN 源字典 → 原样返回 key。
 * 缺 key 时返回 key 本身是刻意设计——键形如 "update.title"，裸键上屏即可被测试
 * （tests/i18n-contract.test.ts）与肉眼捕获；也让「以后端原文当 key」的透传调用
 * 天然回落为原样输出。
 */
export function translate(locale: Locale, key: string, params?: TranslateParams): string {
  const template: string | undefined = DICTIONARIES[locale][key as MessageKey] ?? zhCN[key as MessageKey];
  return template === undefined ? key : interpolate(template, params);
}

// 模块级 locale 状态 + 订阅：静态导出单页没有 locale 路由，也不挂 Provider——
// 任何组件直接 useT()（见 react.ts），lib/ 纯函数用 t()。状态初始恒为 zh-CN，
// 挂载后由 loadStoredLocale() 从 localStorage 同步偏好，避免静态导出水合失配。
let currentLocale: Locale = DEFAULT_LOCALE;
const localeListeners = new Set<() => void>();

function notifyLocaleListeners(): void {
  for (const listener of localeListeners) listener();
}

export function getLocale(): Locale {
  return currentLocale;
}

/** 切换 locale：写入内存状态并持久化（localStorage 不可用时静默跳过，仅本次会话生效）。 */
export function setLocale(locale: Locale): void {
  const next = normalizeLocale(locale);
  currentLocale = next;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(LOCALE_STORAGE_KEY, next);
    } catch {
      // localStorage 被禁用/超限时放弃持久化，不影响本次切换。
    }
  }
  notifyLocaleListeners();
}

export function subscribeLocale(listener: () => void): () => void {
  localeListeners.add(listener);
  return () => {
    localeListeners.delete(listener);
  };
}

/** 从 localStorage 读回偏好（无 window/无存储/值非法时保持现状，幂等可重入）。 */
export function loadStoredLocale(): Locale {
  if (typeof window === "undefined") return currentLocale;
  try {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    if (stored === null) return currentLocale;
    const next = normalizeLocale(stored);
    if (next !== currentLocale) {
      currentLocale = next;
      notifyLocaleListeners();
    }
  } catch {
    // localStorage 不可用时保持默认。
  }
  return currentLocale;
}

/** 以当前 locale 查表：组件里优先 useT()，非 React 调用点（lib/ 纯函数）用它。 */
export function t(key: MessageKey, params?: TranslateParams): string {
  return translate(currentLocale, key, params);
}
