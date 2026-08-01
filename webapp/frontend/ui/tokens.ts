export const LIGHT_TOKENS = {
  background: "#f7f5f0",
  "on-background": "#24211d",
  surface: "#fffdf8",
  "surface-dim": "#ebe7df",
  "surface-bright": "#ffffff",
  "surface-variant": "#e8e1d7",
  "surface-container-lowest": "#ffffff",
  "surface-container-low": "#f1ede6",
  "surface-container": "#ebe6de",
  "surface-container-high": "#e4ded5",
  "surface-container-highest": "#dcd5cb",
  "on-surface": "#24211d",
  "on-surface-variant": "#625c54",
  primary: "#c83d00",
  "on-primary": "#ffffff",
  "primary-container": "#ffe1d5",
  "on-primary-container": "#4a1300",
  "primary-fixed": "#ffdbd0",
  "primary-fixed-dim": "#ffb59d",
  "on-primary-fixed": "#390b00",
  "on-primary-fixed-variant": "#832600",
  "surface-tint": "#c83d00",
  secondary: "#5f625c",
  "on-secondary": "#ffffff",
  "secondary-container": "#e3e3dd",
  "on-secondary-container": "#2c2f2a",
  "secondary-fixed": "#e3e2dd",
  "secondary-fixed-dim": "#c7c7c2",
  "on-secondary-fixed": "#1b1c19",
  "on-secondary-fixed-variant": "#464744",
  tertiary: "#005fae",
  "on-tertiary": "#ffffff",
  "tertiary-container": "#d7e7ff",
  "on-tertiary-container": "#003461",
  "tertiary-fixed": "#d5e3ff",
  "tertiary-fixed-dim": "#a8c8ff",
  "on-tertiary-fixed": "#001b3c",
  "on-tertiary-fixed-variant": "#004689",
  error: "#ba1a1a",
  "on-error": "#ffffff",
  "error-container": "#ffdad6",
  "on-error-container": "#410002",
  outline: "#7b746b",
  "outline-variant": "#cec6bc",
  "event-kill": "#16875b",
  "event-miss": "#c53442",
  "event-corrective": "#1769c2",
  "event-peak": "#c83d00",
  "inverse-surface": "#312b25",
  "inverse-on-surface": "#f9eee8",
  "inverse-primary": "#ffb59d",
} as const;

export const DARK_TOKENS: { [K in keyof typeof LIGHT_TOKENS]: string } = {
  background: "#141413",
  "on-background": "#eae8e3",
  surface: "#1c1c1a",
  "surface-dim": "#181816",
  "surface-bright": "#2a2a27",
  "surface-variant": "#33332f",
  "surface-container-lowest": "#181816",
  "surface-container-low": "#181816",
  "surface-container": "#222220",
  "surface-container-high": "#2a2a27",
  "surface-container-highest": "#33332f",
  "on-surface": "#eae8e3",
  "on-surface-variant": "#9e9a92",
  primary: "#ff8a5c",
  "on-primary": "#1f0a00",
  "primary-container": "#4a220f",
  "on-primary-container": "#ffd9c7",
  "primary-fixed": "#ffdbd0",
  "primary-fixed-dim": "#ffb59d",
  "on-primary-fixed": "#390b00",
  "on-primary-fixed-variant": "#832600",
  "surface-tint": "#ff8a5c",
  secondary: "#c7c7c2",
  "on-secondary": "#30312e",
  "secondary-container": "#494946",
  "on-secondary-container": "#e3e2dd",
  "secondary-fixed": "#e3e2dd",
  "secondary-fixed-dim": "#c7c7c2",
  "on-secondary-fixed": "#1b1c19",
  "on-secondary-fixed-variant": "#464744",
  tertiary: "#8ab4f2",
  "on-tertiary": "#003061",
  "tertiary-container": "#1c3a5c",
  "on-tertiary-container": "#d2e4ff",
  "tertiary-fixed": "#d5e3ff",
  "tertiary-fixed-dim": "#a8c8ff",
  "on-tertiary-fixed": "#001b3c",
  "on-tertiary-fixed-variant": "#004689",
  error: "#ff9aa2",
  "on-error": "#690005",
  "error-container": "#5c1a20",
  "on-error-container": "#ffdce0",
  outline: "#6b6660",
  "outline-variant": "#3a3833",
  "event-kill": "#4fdca0",
  "event-miss": "#ff8792",
  "event-corrective": "#85c2ff",
  "event-peak": "#ff8a5c",
  "inverse-surface": "#e9e4dd",
  "inverse-on-surface": "#3e2c26",
  "inverse-primary": "#ac3400",
};

export type TokenName = keyof typeof LIGHT_TOKENS;
export type ThemeTokens = Record<TokenName, string>;
export const TOKEN_NAMES = Object.keys(LIGHT_TOKENS) as TokenName[];

function channel(hex: string, offset: number): number {
  return Number.parseInt(hex.slice(offset, offset + 2), 16) / 255;
}

function luminance(hex: string): number {
  const channels = [channel(hex, 1), channel(hex, 3), channel(hex, 5)].map((value) =>
    value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
  );
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

export function contrastRatio(foreground: string, background: string): number {
  const lighter = Math.max(luminance(foreground), luminance(background));
  const darker = Math.min(luminance(foreground), luminance(background));
  return (lighter + 0.05) / (darker + 0.05);
}
