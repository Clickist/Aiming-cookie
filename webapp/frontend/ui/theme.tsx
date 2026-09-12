"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  THEME_STORAGE_KEY,
  applyThemeToDocument,
  createThemeScript,
  normalizeThemePreference,
  resolveTheme,
  type ThemeMode,
  type ThemePreference,
} from "./theme-core";

interface ThemeContextValue {
  preference: ThemePreference;
  resolvedTheme: ThemeMode;
  setPreference: (preference: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>("system");
  const [resolvedTheme, setResolvedTheme] = useState<ThemeMode>("light");
  const preferenceRef = useRef<ThemePreference>("system");
  const systemThemeRef = useRef<ThemeMode>("light");

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const stored = normalizeThemePreference(window.localStorage.getItem(THEME_STORAGE_KEY));
    const systemTheme: ThemeMode = media.matches ? "dark" : "light";
    preferenceRef.current = stored;
    systemThemeRef.current = systemTheme;
    setPreferenceState(stored);
    setResolvedTheme(resolveTheme(stored, systemTheme));
    applyThemeToDocument(resolveTheme(stored, systemTheme));
    const onChange = (event: MediaQueryListEvent) => {
      const nextSystemTheme: ThemeMode = event.matches ? "dark" : "light";
      // WebView2 在窗口/视图操作期间会重评估媒体查询并发出值未变的假 change
      // 事件：值没变就不重写令牌，防发送交接期整窗闪暗（0911 审计 §12.3）。
      if (nextSystemTheme === systemThemeRef.current) return;
      systemThemeRef.current = nextSystemTheme;
      if (preferenceRef.current === "system") {
        setResolvedTheme(nextSystemTheme);
        applyThemeToDocument(nextSystemTheme);
      }
    };
    media.addEventListener?.("change", onChange);
    return () => {
      media.removeEventListener?.("change", onChange);
    };
  }, []);

  const setPreference = useCallback((nextPreference: ThemePreference) => {
    const preference = normalizeThemePreference(nextPreference);
    const systemTheme: ThemeMode = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    const resolvedTheme = resolveTheme(preference, systemTheme);
    window.localStorage.setItem(THEME_STORAGE_KEY, preference);
    preferenceRef.current = preference;
    systemThemeRef.current = systemTheme;
    setPreferenceState(preference);
    setResolvedTheme(resolvedTheme);
    applyThemeToDocument(resolvedTheme);
  }, []);

  const value = useMemo(
    () => ({ preference, resolvedTheme, setPreference }),
    [preference, resolvedTheme, setPreference],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}

export function ThemeScript() {
  return <script id="aiming-cookie-theme-script" dangerouslySetInnerHTML={{ __html: createThemeScript() }} />;
}
