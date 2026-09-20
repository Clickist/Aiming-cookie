export {
  DEFAULT_LOCALE,
  DICTIONARIES,
  LOCALE_STORAGE_KEY,
  getLocale,
  interpolate,
  loadStoredLocale,
  normalizeLocale,
  setLocale,
  subscribeLocale,
  t,
  translate,
  type Locale,
  type MessageKey,
  type TranslateFn,
  type TranslateParams,
} from "./core";
export { useLocale, useT, type UseLocaleResult } from "./react";
export { enUS } from "./en-US";
export { zhCN } from "./zh-CN";
