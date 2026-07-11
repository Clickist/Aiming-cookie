# Aiming Cookie Desktop Design System

This document is the frontend implementation contract for the design authority in [`DESIGN-cursor.md`](../DESIGN-cursor.md).

## Authority and scope

The design fact hierarchy is:

1. [`DESIGN-cursor.md`](../DESIGN-cursor.md) — highest authority.
2. This document — implementation contract.
3. [`webapp/frontend/app/globals.css`](../webapp/frontend/app/globals.css) — executable token values.

`globals.css` is not the highest design authority. It must faithfully implement the approved semantic roles and shared foundations; it does not create product visual direction on its own.

The scope is the existing Aiming Cookie Desktop editorial/precision-tool UI. Do not use this system as authorization for a page redesign or for unrelated marketing visuals.

## Semantic-token contract

`globals.css` defines the complete token set from `DESIGN-cursor.md` through Tailwind-compatible `--color-*` names. Both `data-theme="light"` and `data-theme="dark"` provide a value for every role:

- surfaces: `background`, `on-background`, `surface`, `surface-dim`, `surface-bright`, `surface-variant`, `surface-container-lowest`, `surface-container-low`, `surface-container`, `surface-container-high`, `surface-container-highest`, `on-surface`, `on-surface-variant`;
- primary: `primary`, `on-primary`, `primary-container`, `on-primary-container`, `primary-fixed`, `primary-fixed-dim`, `on-primary-fixed`, `on-primary-fixed-variant`, `surface-tint`;
- secondary: `secondary`, `on-secondary`, `secondary-container`, `on-secondary-container`, `secondary-fixed`, `secondary-fixed-dim`, `on-secondary-fixed`, `on-secondary-fixed-variant`;
- tertiary: `tertiary`, `on-tertiary`, `tertiary-container`, `on-tertiary-container`, `tertiary-fixed`, `tertiary-fixed-dim`, `on-tertiary-fixed`, `on-tertiary-fixed-variant`;
- status and structure: `error`, `on-error`, `error-container`, `on-error-container`, `outline`, `outline-variant`, `event-kill`, `event-miss`, `event-corrective`, `event-peak`, `inverse-surface`, `inverse-on-surface`, `inverse-primary`.

Components consume these semantic roles via existing Tailwind classes or `var(--color-*)`. They must not add raw color values or light/dark branches. When a semantic need is missing, update the design source and both CSS palettes before using it.

## Theme contract

- Accepted preference values: `system`, `light`, `dark`.
- Storage key: `aiming-cookie-theme` in browser `localStorage` only.
- No stored preference means `system`.
- `system` resolves from `prefers-color-scheme` and updates live when that media query changes.
- Explicit `light` and `dark` choices do not follow the system.
- A small inline bootstrap in the root layout resolves the preference before React hydration and writes the resolved `light`/`dark` value to `document.documentElement.dataset.theme` and `colorScheme`.
- The root `ThemeController` owns live system synchronization. `/settings` only changes the preference through its accessible System/Light/Dark select.

## Shared foundations

- **Typography:** retain the existing Inter UI, JetBrains Mono data, and Outfit display font tokens and the current Chinese fallback stack.
- **Space:** retain the current editorial spacing rhythm and container scale.
- **Geometry:** retain the current compact, machined radius scale.
- **Depth:** use the semantic surface ladder and outlines; avoid decorative shadows.
- **Motion:** the existing processing pulse derives from `primary` with `color-mix`, so it automatically tracks themes without component-specific values.

## Review checklist

Before release, Sol reviews screenshots of the existing relevant pages in both light and dark modes and confirms readable text, outlines, status colors, and primary-action contrast. Verify all three preference paths: a first-run System setting, a live system change while System is active, and explicit Light/Dark settings that remain fixed.
