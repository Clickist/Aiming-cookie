"use client";

import { useEffect, useState } from "react";

const THEME_STORAGE_KEY = "aiming-cookie-theme";
const THEME_CHANGE_EVENT = "aiming-cookie-theme-change";

export type ThemePreference = "system" | "light" | "dark";

function isThemePreference(value: string | null): value is ThemePreference {
  return value === "system" || value === "light" || value === "dark";
}

function readThemePreference(): ThemePreference {
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  return isThemePreference(stored) ? stored : "system";
}

function resolveTheme(preference: ThemePreference): "light" | "dark" {
  if (preference !== "system") return preference;

  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function applyTheme(preference: ThemePreference) {
  const theme = resolveTheme(preference);
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

/** Keeps the resolved document theme in sync on every route. */
export function ThemeController() {
  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

    const syncTheme = () => applyTheme(readThemePreference());
    const syncSystemTheme = () => {
      if (readThemePreference() === "system") syncTheme();
    };

    syncTheme();
    mediaQuery.addEventListener("change", syncSystemTheme);
    window.addEventListener("storage", syncTheme);
    window.addEventListener(THEME_CHANGE_EVENT, syncTheme);

    return () => {
      mediaQuery.removeEventListener("change", syncSystemTheme);
      window.removeEventListener("storage", syncTheme);
      window.removeEventListener(THEME_CHANGE_EVENT, syncTheme);
    };
  }, []);

  return null;
}

/** Accessible local-only theme preference control for the settings route. */
export function ThemePreferenceSelect() {
  const [preference, setPreference] = useState<ThemePreference>("system");

  useEffect(() => {
    const syncPreference = () => setPreference(readThemePreference());

    syncPreference();
    window.addEventListener("storage", syncPreference);
    window.addEventListener(THEME_CHANGE_EVENT, syncPreference);

    return () => {
      window.removeEventListener("storage", syncPreference);
      window.removeEventListener(THEME_CHANGE_EVENT, syncPreference);
    };
  }, []);

  const handleChange = (value: ThemePreference) => {
    window.localStorage.setItem(THEME_STORAGE_KEY, value);
    setPreference(value);
    applyTheme(value);
    window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
  };

  return (
    <div className="flex flex-col gap-xs">
      <label htmlFor="theme-preference" className="text-label-md text-on-surface">
        颜色主题
      </label>
      <select
        id="theme-preference"
        value={preference}
        onChange={(event) => handleChange(event.target.value as ThemePreference)}
        className="w-full bg-surface border border-outline rounded-md px-sm py-xs text-body-md text-on-surface focus:border-primary"
        aria-describedby="theme-preference-description"
      >
        <option value="system">System</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
      <p id="theme-preference-description" className="text-label-sm text-on-surface-variant">
        System 会跟随你的操作系统外观；此设置仅保存在本机。
      </p>
    </div>
  );
}
