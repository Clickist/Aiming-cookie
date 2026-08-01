import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";
import vm from "node:vm";

import {
  DARK_TOKENS,
  LIGHT_TOKENS,
  TOKEN_NAMES,
  contrastRatio,
} from "../ui/tokens";
import {
  THEME_STORAGE_KEY,
  createThemeController,
  createThemeScript,
} from "../ui/theme-core";

const frontendRoot = resolve(import.meta.dirname, "..");

test("light and dark themes expose the same semantic token keys", () => {
  assert.deepEqual(Object.keys(LIGHT_TOKENS).sort(), Object.keys(DARK_TOKENS).sort());
  assert.deepEqual(TOKEN_NAMES.sort(), Object.keys(LIGHT_TOKENS).sort());
  for (const key of TOKEN_NAMES) {
    assert.match(LIGHT_TOKENS[key], /^#[0-9a-f]{6}$/i);
    assert.match(DARK_TOKENS[key], /^#[0-9a-f]{6}$/i);
  }
});

test("dark tokens match the approved OpenDesign core palette", () => {
  const approvedDarkRoles = {
    background: "#141413",
    surface: "#1c1c1a",
    "surface-dim": "#181816",
    "surface-bright": "#2a2a27",
    "surface-variant": "#33332f",
    "surface-container-lowest": "#181816",
    "surface-container-low": "#181816",
    "surface-container": "#222220",
    "surface-container-high": "#2a2a27",
    "surface-container-highest": "#33332f",
    "on-background": "#eae8e3",
    "on-surface": "#eae8e3",
    "on-surface-variant": "#9e9a92",
    primary: "#ff8a5c",
    "on-primary": "#1f0a00",
    "primary-container": "#4a220f",
    "on-primary-container": "#ffd9c7",
    tertiary: "#8ab4f2",
    "tertiary-container": "#1c3a5c",
    "on-tertiary-container": "#d2e4ff",
    error: "#ff9aa2",
    "error-container": "#5c1a20",
    "on-error-container": "#ffdce0",
    outline: "#6b6660",
    "outline-variant": "#3a3833",
    "event-kill": "#4fdca0",
    "event-miss": "#ff8792",
    "event-corrective": "#85c2ff",
    "event-peak": "#ff8a5c",
  } as const;

  for (const [role, value] of Object.entries(approvedDarkRoles)) {
    assert.equal(DARK_TOKENS[role as keyof typeof DARK_TOKENS], value, role);
  }
});

test("executable token names match the approved visual contract", () => {
  const visualContract = readFileSync(resolve(frontendRoot, "..", "..", "DESIGN-cursor.md"), "utf8");
  const documentedNames = Array.from(
    visualContract.matchAll(/^\| `([^`]+)` \| `#[0-9a-f]{6}` \| `#[0-9a-f]{6}`/gim),
  ).map((match) => match[1]);
  assert.deepEqual(documentedNames.sort(), TOKEN_NAMES.slice().sort());
});

test("primary text roles remain readable in both themes", () => {
  for (const tokens of [LIGHT_TOKENS, DARK_TOKENS]) {
    assert.ok(contrastRatio(tokens["on-background"], tokens.background) >= 4.5);
    assert.ok(contrastRatio(tokens["on-surface"], tokens.surface) >= 4.5);
    assert.ok(contrastRatio(tokens["on-surface-variant"], tokens.surface) >= 4.5);
    assert.ok(contrastRatio(tokens["on-primary"], tokens.primary) >= 4.5);
    assert.ok(contrastRatio(tokens["on-error"], tokens.error) >= 4.5);
  }
});

test("system preference follows the system and explicit preferences stay fixed", () => {
  let systemTheme: "light" | "dark" = "light";
  let onSystemChange: ((theme: "light" | "dark") => void) | undefined;
  const applied: Array<"light" | "dark"> = [];
  const controller = createThemeController({
    readPreference: () => null,
    writePreference: () => undefined,
    readSystemTheme: () => systemTheme,
    subscribeSystem: (listener) => {
      onSystemChange = listener;
      return () => {
        onSystemChange = undefined;
      };
    },
    applyTheme: (theme) => applied.push(theme),
  });

  assert.deepEqual(controller.start(), { preference: "system", resolvedTheme: "light" });
  systemTheme = "dark";
  onSystemChange?.(systemTheme);
  assert.equal(applied.at(-1), "dark");

  controller.setPreference("light");
  systemTheme = "dark";
  onSystemChange?.(systemTheme);
  assert.equal(applied.at(-1), "light");

  controller.setPreference("dark");
  systemTheme = "light";
  onSystemChange?.(systemTheme);
  assert.equal(applied.at(-1), "dark");
});

test("theme preference is local UI storage and the hydration script applies it before paint", () => {
  assert.equal(THEME_STORAGE_KEY, "aiming-cookie.ui.theme");
  const script = createThemeScript();
  assert.match(script, /localStorage/);
  assert.match(script, /prefers-color-scheme/);
  assert.match(script, /dataset\.theme/);

  const layout = readFileSync(join(frontendRoot, "app", "layout.tsx"), "utf8");
  assert.ok(layout.indexOf("ThemeScript") < layout.indexOf("<body"));

  const themeSource = readFileSync(join(frontendRoot, "ui", "theme.tsx"), "utf8");
  assert.doesNotMatch(themeSource, /fetch\(|Analysis|Coach|lib\/api/);
});

test("hydration script resolves stored preferences without waiting for React", () => {
  const script = createThemeScript();
  const applied: Record<string, string> = {};
  const sandbox = {
    localStorage: { getItem: () => "dark" },
    window: { matchMedia: () => ({ matches: false }) },
    document: {
      documentElement: {
        dataset: {} as Record<string, string>,
        style: { setProperty: (name: string, value: string) => { applied[name] = value; } },
      },
    },
  };
  vm.runInNewContext(script, sandbox);
  assert.equal(sandbox.document.documentElement.dataset.theme, "dark");
  assert.equal(applied["--background"], DARK_TOKENS.background);
});

test("components consume semantic CSS tokens and preserve accessibility states", () => {
  const uiRoot = join(frontendRoot, "ui");
  const sourceFiles: string[] = [];
  const visit = (directory: string) => {
    for (const entry of readdirSync(directory)) {
      const path = join(directory, entry);
      if (statSync(path).isDirectory()) visit(path);
      else if (/\.(css|ts|tsx)$/.test(entry)) sourceFiles.push(path);
    }
  };
  visit(uiRoot);

  for (const file of sourceFiles) {
    if (file.endsWith("tokens.ts")) continue;
    const source = readFileSync(file, "utf8");
    assert.doesNotMatch(source, /#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(/i, file);
  }

  const css = readFileSync(join(uiRoot, "theme.css"), "utf8");
  assert.match(css, /:focus-visible/);
  assert.match(css, /:disabled|\[aria-disabled="true"\]/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /transition-duration:\s*0/);
});
