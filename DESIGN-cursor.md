# Aiming Cookie Desktop — Design Source

**Status:** visual-direction authority for Aiming Cookie Desktop
**Scope:** the desktop product surfaces defined by `docs/frontend-uiux-design.md`: import, analysis workspace, Coach sidebar, history, settings, and supporting states.
**Visual direction:** editorial precision tool — warm, measured, information-dense, and quietly technical. It is not a marketing site. Structural page/IA changes come from the UI/UX contract; this file governs how approved structures should feel, not whether they exist.

## Governance

Design responsibilities are layered rather than competing:

1. `docs/frontend-uiux-design.md` — product skeleton, information architecture, and interaction relationships.
2. `DESIGN-cursor.md` — visual intent, semantic meaning, shared foundations, and approved palettes.
3. `docs/design-system.md` — token, theme, and component implementation contract.
4. Current frontend code — executable implementation of the approved contracts.

The current `webapp/frontend/app/globals.css` belongs to a disposable History / Run / Evidence prototype and is not an approved executable token entry. Until the reconstruction plan establishes a replacement token module, this document and `docs/design-system.md` define the intended contract but do not prove that the running prototype implements it.

Mockups, Stitch output, root `DESIGN.md`, design HTML, and other drafts are references only. They do not override the UI/UX or visual contracts.

## Shared foundations

### Editorial precision

- A warm neutral canvas and paper-like surfaces keep video analysis readable over extended sessions.
- Orange is the single high-voltage action and active-analysis signal. It must stay scarce.
- Blue, green, and red event colors identify analysis states only; they do not replace primary action semantics.
- Elevation comes from surface steps and hairline outlines, not from decorative drop shadows.
- Spacing is deliberate and roomy around analysis controls; dense data can remain compact inside session views.

### Typography and geometry

- UI: Inter with Chinese system fallbacks.
- Data and timing: JetBrains Mono.
- Display: Outfit with the existing Chinese system fallbacks.
- Use a consistent editorial rhythm, container scale, and compact machined radii across themes.
- Semantic role names should remain stable once the new executable token layer is established. A theme changes token values, not component structure.

### Theme behavior

- The only user-facing choices are **System**, **Light**, and **Dark**.
- First use is System. The preference is local-only and never enters the backend, auth token, or product configuration.
- System resolves from `prefers-color-scheme` and changes live with the operating-system preference.
- Components consume semantic tokens only: no raw colors and no light/dark conditional styling in components.

## Semantic palette

All themes expose the identical token names below. Values are intentionally semantic rather than component-specific; components choose role names, never palette literals.

| Token | Light | Dark | Role |
| --- | --- | --- | --- |
| `background` | `#f7f5f0` | `#12110f` | App canvas |
| `on-background` | `#24211d` | `#fadcd3` | Canvas text |
| `surface` | `#fffdf8` | `#1e100b` | Base surface |
| `surface-dim` | `#ebe7df` | `#1e100b` | Recessed surface |
| `surface-bright` | `#ffffff` | `#48352f` | Raised surface |
| `surface-variant` | `#e8e1d7` | `#43302a` | Alternate surface |
| `surface-container-lowest` | `#ffffff` | `#180b06` | Strongest raised surface |
| `surface-container-low` | `#f1ede6` | `#1c1b18` | Low container |
| `surface-container` | `#ebe6de` | `#1c1b18` | Default container |
| `surface-container-high` | `#e4ded5` | `#372620` | High container |
| `surface-container-highest` | `#dcd5cb` | `#43302a` | Highest container |
| `on-surface` | `#24211d` | `#fadcd3` | Primary surface text |
| `on-surface-variant` | `#625c54` | `#c9beb4` | Secondary text |
| `primary` | `#c83d00` | `#ff7a45` | Primary action and active signal |
| `on-primary` | `#ffffff` | `#3a0d00` | Text on primary |
| `primary-container` | `#ffe1d5` | `#7e2500` | Low-emphasis primary surface |
| `on-primary-container` | `#4a1300` | `#ffe1d5` | Text on primary container |
| `primary-fixed` | `#ffdbd0` | `#ffdbd0` | Theme-invariant primary reference |
| `primary-fixed-dim` | `#ffb59d` | `#ffb59d` | Dim fixed primary reference |
| `on-primary-fixed` | `#390b00` | `#390b00` | Text on fixed primary |
| `on-primary-fixed-variant` | `#832600` | `#832600` | Secondary text on fixed primary |
| `surface-tint` | `#c83d00` | `#ffb59d` | Surface tint |
| `secondary` | `#5f625c` | `#c7c7c2` | Secondary action/state |
| `on-secondary` | `#ffffff` | `#30312e` | Text on secondary |
| `secondary-container` | `#e3e3dd` | `#494946` | Secondary container |
| `on-secondary-container` | `#2c2f2a` | `#e3e2dd` | Text on secondary container |
| `secondary-fixed` | `#e3e2dd` | `#e3e2dd` | Theme-invariant secondary reference |
| `secondary-fixed-dim` | `#c7c7c2` | `#c7c7c2` | Dim fixed secondary reference |
| `on-secondary-fixed` | `#1b1c19` | `#1b1c19` | Text on fixed secondary |
| `on-secondary-fixed-variant` | `#464744` | `#464744` | Secondary text on fixed secondary |
| `tertiary` | `#005fae` | `#a8c8ff` | Informational accent |
| `on-tertiary` | `#ffffff` | `#003061` | Text on tertiary |
| `tertiary-container` | `#d7e7ff` | `#155b9e` | Informational container |
| `on-tertiary-container` | `#003461` | `#d7e7ff` | Text on tertiary container |
| `tertiary-fixed` | `#d5e3ff` | `#d5e3ff` | Theme-invariant tertiary reference |
| `tertiary-fixed-dim` | `#a8c8ff` | `#a8c8ff` | Dim fixed tertiary reference |
| `on-tertiary-fixed` | `#001b3c` | `#001b3c` | Text on fixed tertiary |
| `on-tertiary-fixed-variant` | `#004689` | `#004689` | Secondary text on fixed tertiary |
| `error` | `#ba1a1a` | `#ffb4ab` | Error action/status |
| `on-error` | `#ffffff` | `#690005` | Text on error |
| `error-container` | `#ffdad6` | `#93000a` | Error container |
| `on-error-container` | `#410002` | `#ffdad6` | Text on error container |
| `outline` | `#7b746b` | `#635a52` | Strong hairline |
| `outline-variant` | `#cec6bc` | `#5c4038` | Soft hairline |
| `event-kill` | `#16875b` | `#39c98a` | Confirmed/kill event |
| `event-miss` | `#c53442` | `#ff6b78` | Miss event |
| `event-corrective` | `#1769c2` | `#70b7ff` | Corrective event |
| `event-peak` | `#c83d00` | `#ff7a45` | Peak-tension event |
| `inverse-surface` | `#312b25` | `#fadcd3` | Inverted surface |
| `inverse-on-surface` | `#f9eee8` | `#3e2c26` | Text on inverted surface |
| `inverse-primary` | `#ffb59d` | `#ac3400` | Action on inverted surface |

## Component rules

- Use semantic classes/tokens such as `bg-surface-container`, `text-on-surface`, `border-outline`, and `text-primary` once the new frontend foundation defines them.
- Never introduce raw hex/RGB values in components, and never branch a component on the active theme.
- New visual needs require a semantic token defined in this document and implemented in both palettes before use.
- The only global active-state glow is the token-derived primary pulse used by the processing pipeline.
