import { DARK_TOKENS, LIGHT_TOKENS, type ThemeTokens } from "./tokens";

export const THEME_STORAGE_KEY = "aiming-cookie.ui.theme";
export type ThemePreference = "system" | "light" | "dark";
export type ThemeMode = "light" | "dark";

export function normalizeThemePreference(value: string | null | undefined): ThemePreference {
  return value === "light" || value === "dark" ? value : "system";
}

export function resolveTheme(preference: ThemePreference, systemTheme: ThemeMode): ThemeMode {
  return preference === "system" ? systemTheme : preference;
}

export interface ThemeEnvironment {
  readPreference: () => string | null;
  writePreference: (preference: ThemePreference) => void;
  readSystemTheme: () => ThemeMode;
  subscribeSystem: (listener: (theme: ThemeMode) => void) => () => void;
  applyTheme: (theme: ThemeMode) => void;
}

export interface ThemeState {
  preference: ThemePreference;
  resolvedTheme: ThemeMode;
}

export interface ThemeController {
  start: () => ThemeState;
  stop: () => void;
  setPreference: (preference: ThemePreference) => ThemeState;
  getState: () => ThemeState;
}

export function createThemeController(environment: ThemeEnvironment): ThemeController {
  let preference = normalizeThemePreference(environment.readPreference());
  let systemTheme = environment.readSystemTheme();
  let unsubscribe: (() => void) | undefined;
  let started = false;

  const getState = (): ThemeState => ({
    preference,
    resolvedTheme: resolveTheme(preference, systemTheme),
  });
  const apply = () => environment.applyTheme(getState().resolvedTheme);

  return {
    start() {
      if (!started) {
        started = true;
        unsubscribe = environment.subscribeSystem((nextTheme) => {
          systemTheme = nextTheme;
          apply();
        });
      }
      apply();
      return getState();
    },
    stop() {
      unsubscribe?.();
      unsubscribe = undefined;
      started = false;
    },
    setPreference(nextPreference) {
      preference = nextPreference;
      environment.writePreference(nextPreference);
      apply();
      return getState();
    },
    getState,
  };
}

export function applyThemeToDocument(theme: ThemeMode): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const tokens: ThemeTokens = theme === "dark" ? DARK_TOKENS : LIGHT_TOKENS;
  root.dataset.theme = theme;
  for (const [name, value] of Object.entries(tokens)) {
    root.style.setProperty(`--${name}`, value);
  }
}

export function createThemeScript(): string {
  const light = JSON.stringify(LIGHT_TOKENS);
  const dark = JSON.stringify(DARK_TOKENS);
  return `(function(){try{var key=${JSON.stringify(THEME_STORAGE_KEY)};var stored=localStorage.getItem(key);var preference=stored==='light'||stored==='dark'?stored:'system';var system=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';var theme=preference==='system'?system:preference;var tokens=theme==='dark'?${dark}:${light};var root=document.documentElement;root.dataset.theme=theme;Object.keys(tokens).forEach(function(name){root.style.setProperty('--'+name,tokens[name]);});}catch(error){document.documentElement.dataset.theme='light';}})();`;
}
