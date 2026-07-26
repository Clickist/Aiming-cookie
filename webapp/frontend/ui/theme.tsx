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

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const stored = normalizeThemePreference(window.localStorage.getItem(THEME_STORAGE_KEY));
    const systemTheme: ThemeMode = media.matches ? "dark" : "light";
    preferenceRef.current = stored;
    setPreferenceState(stored);
    setResolvedTheme(resolveTheme(stored, systemTheme));
    applyThemeToDocument(resolveTheme(stored, systemTheme));
    const onChange = (event: MediaQueryListEvent) => {
      const nextSystemTheme: ThemeMode = event.matches ? "dark" : "light";
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
