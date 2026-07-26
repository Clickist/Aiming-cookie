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
  background: "#12110f",
  "on-background": "#e9e4dd",
  surface: "#1e100b",
  "surface-dim": "#1e100b",
  "surface-bright": "#48352f",
  "surface-variant": "#43302a",
  "surface-container-lowest": "#180b06",
  "surface-container-low": "#1c1b18",
  "surface-container": "#1c1b18",
  "surface-container-high": "#372620",
  "surface-container-highest": "#43302a",
  "on-surface": "#e9e4dd",
  "on-surface-variant": "#b6ada2",
  primary: "#ff7a45",
  "on-primary": "#3a0d00",
  "primary-container": "#7e2500",
  "on-primary-container": "#ffe1d5",
  "primary-fixed": "#ffdbd0",
  "primary-fixed-dim": "#ffb59d",
  "on-primary-fixed": "#390b00",
  "on-primary-fixed-variant": "#832600",
  "surface-tint": "#ffb59d",
  secondary: "#c7c7c2",
  "on-secondary": "#30312e",
  "secondary-container": "#494946",
  "on-secondary-container": "#e3e2dd",
  "secondary-fixed": "#e3e2dd",
  "secondary-fixed-dim": "#c7c7c2",
  "on-secondary-fixed": "#1b1c19",
  "on-secondary-fixed-variant": "#464744",
  tertiary: "#a8c8ff",
  "on-tertiary": "#003061",
  "tertiary-container": "#155b9e",
  "on-tertiary-container": "#d7e7ff",
  "tertiary-fixed": "#d5e3ff",
  "tertiary-fixed-dim": "#a8c8ff",
  "on-tertiary-fixed": "#001b3c",
  "on-tertiary-fixed-variant": "#004689",
  error: "#ffb4ab",
  "on-error": "#690005",
  "error-container": "#93000a",
  "on-error-container": "#ffdad6",
  outline: "#635a52",
  "outline-variant": "#5c4038",
  "event-kill": "#39c98a",
  "event-miss": "#ff6b78",
  "event-corrective": "#70b7ff",
  "event-peak": "#ff7a45",
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
