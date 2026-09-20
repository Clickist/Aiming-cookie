import { useCallback, useEffect, useSyncExternalStore } from "react";

import {
  DEFAULT_LOCALE,
  getLocale,
  loadStoredLocale,
  setLocale,
  subscribeLocale,
  translate,
  type Locale,
  type MessageKey,
  type TranslateFn,
  type TranslateParams,
} from "./core";

// 静态导出下没有 SSR 快照可读，水合帧必须与预渲染一致：恒返回默认 zh-CN。
const getServerLocale = (): Locale => DEFAULT_LOCALE;

export interface UseLocaleResult {
  locale: Locale;
  setLocale: (locale: Locale) => void;
}

/**
 * locale 状态 hook：首帧（预渲染＋水合）恒为 zh-CN，挂载后 effect 从 localStorage
 * 同步已存偏好——现有 zh 用户与 e2e 不受惊扰，en 用户在挂载后切换。
 */
export function useLocale(): UseLocaleResult {
  useEffect(() => {
    loadStoredLocale();
  }, []);
  const locale = useSyncExternalStore(subscribeLocale, getLocale, getServerLocale);
  const updateLocale = useCallback((next: Locale) => setLocale(next), []);
  return { locale, setLocale: updateLocale };
}

/** 组件内取文案的标准入口：locale 变化时随订阅一起重渲染。 */
export function useT(): TranslateFn {
  const { locale } = useLocale();
  return useCallback(
    (key: MessageKey, params?: TranslateParams) => translate(locale, key, params),
    [locale],
  );
}
