---
name: Aiming Cookie
colors:
  surface: '#131210'
  surface-dim: '#15140c'
  surface-bright: '#3b3930'
  surface-container-lowest: '#0f0e07'
  surface-container-low: '#1d1c14'
  surface-container: '#212018'
  surface-container-high: '#2c2a22'
  surface-container-highest: '#37352c'
  on-surface: '#e7e2d5'
  on-surface-variant: '#e5beb2'
  inverse-surface: '#e7e2d5'
  inverse-on-surface: '#323128'
  outline: '#ac897e'
  outline-variant: '#5c4038'
  surface-tint: '#ffb59d'
  primary: '#ffb59d'
  on-primary: '#5d1800'
  primary-container: '#ff5712'
  on-primary-container: '#511400'
  inverse-primary: '#ac3400'
  secondary: '#cac6c2'
  on-secondary: '#32302e'
  secondary-container: '#4b4946'
  on-secondary-container: '#bcb8b4'
  tertiary: '#a8c8ff'
  on-tertiary: '#003061'
  tertiary-container: '#3491ff'
  on-tertiary-container: '#002955'
  error: '#ef4444'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#ffdbd0'
  primary-fixed-dim: '#ffb59d'
  on-primary-fixed: '#390b00'
  on-primary-fixed-variant: '#832600'
  secondary-fixed: '#e6e2de'
  secondary-fixed-dim: '#cac6c2'
  on-secondary-fixed: '#1d1b19'
  on-secondary-fixed-variant: '#484644'
  tertiary-fixed: '#d5e3ff'
  tertiary-fixed-dim: '#a8c8ff'
  on-tertiary-fixed: '#001b3c'
  on-tertiary-fixed-variant: '#004689'
  background: '#15140c'
  on-background: '#e7e2d5'
  surface-variant: '#37352c'
  bg: '#0d0d0c'
  surface-hi: '#1c1b18'
  border: '#272520'
  border-hi: '#3a3632'
  text-primary: '#f3f0ea'
  text-secondary: '#807d72'
  text-muted: '#4a4845'
  accent-dim: '#2a1500'
  success: '#22c55e'
  warning: '#f59e0b'
  info: '#3b82f6'
typography:
  hero-heading:
    fontFamily: Inter
    fontSize: 64px
    fontWeight: '500'
    lineHeight: '1.1'
    letterSpacing: -0.035em
  hero-heading-mobile:
    fontFamily: Inter
    fontSize: 42px
    fontWeight: '500'
    lineHeight: '1.2'
    letterSpacing: -0.035em
  section-heading:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  subheading:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.65'
  metric-lg:
    fontFamily: JetBrains Mono
    fontSize: 48px
    fontWeight: '600'
    lineHeight: '1'
  metric-sm:
    fontFamily: JetBrains Mono
    fontSize: 28px
    fontWeight: '600'
    lineHeight: '1'
  label-caps:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.08em
  meta-data:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 8px
  gap-grid: 24px
  margin-page: 32px
  card-padding: 24px
  section-gap: 64px
  hero-gap: 96px
---

> **状态**：设计参考资产，不是视觉方向、设计 token 或前端实现的事实源。视觉语义见 [`../DESIGN-cursor.md`](../DESIGN-cursor.md)，实现治理见 [`../docs/design-system.md`](../docs/design-system.md)，当前可执行状态以现有前端代码为准。

## Brand & Style

The design system is engineered for the elite competitive performance space, moving away from "gamer" tropes of neon glows and complex textures toward a **Professional Performance Tool** aesthetic. It is deeply rooted in **Minimalism** with a heavy influence from **Modern Developer Tools** (like Linear and Vercel). 

The brand personality is technical, utilitarian, and high-fidelity. It evokes a sense of "Information Density & Trust," where data is prioritized over decoration. The visual language uses flat, layered surfaces defined by precise 1px borders rather than shadows, creating a rigorous and disciplined environment for performance analysis.

**Key Stylistic Tenets:**
- **Density over Air:** Information is compact and technical, allowing for high-speed scanning of performance metrics.
- **Precision:** The UI relies on monospaced fonts for machine-generated data and strict 8px grid alignments.
- **Focused Accents:** Use the vibrant "Cursor Orange" sparingly to direct attention only to critical actions or active states.

## Colors

This design system utilizes a high-contrast **Dark Mode** by default. The palette is built on a foundation of "near-black and warm gray" tones, which reduces eye strain during long analytical sessions while maintaining a sophisticated, premium feel.

- **Primary Accent:** `#f54e00` (Cursor Orange) is the primary CTA color and indicator for active focus.
- **Surface Tiers:** Depth is created through color shifts. Use `--bg` for the canvas and `--surface` for cards. Use `--surface-hi` exclusively for interaction states like hover or active selections.
- **Typography Colors:** Use `--text-primary` for all high-priority content. Secondary information and labels must use `--text-secondary` or `--text-muted` to establish a clear content hierarchy.
- **Semantic Status:** Success (green), Error (red), and Warning (amber) are reserved for diagnostics, verdicts, and severity badges.

## Typography

The system employs a dual-font strategy to differentiate between human-readable narratives and machine-processed data.

1.  **Inter (UI & Brand):** Used for all headings and body copy. It is legible and neutral. Use negative letter-spacing for large headlines to maintain a tight, professional look.
2.  **JetBrains Mono (Technical Data):** Used for metrics, labels, and timestamps. The monospaced nature of the font communicates technical accuracy and "pro tool" performance. 

**Application Rules:**
- Use **Label Caps** for "eyebrow" text or table headers.
- **Metric Values** should be significantly larger than surrounding text to emphasize data-driven insights.
- Maintain a generous **1.65 line-height** for body text to ensure readability amidst high-density data.

## Layout & Spacing

This design system uses a **Fixed Grid** layout for desktop to ensure a controlled analytical environment, transitioning to a fluid model for smaller viewports.

- **Grid:** A 12-column grid with a 24px gutter and a maximum content width of 1200px.
- **Vertical Rhythm:** Built on an 8px base unit. Component heights and internal spacing should always be multiples of 8 (e.g., 8, 16, 24, 32, 64).
- **Sectioning:** Use a 64px gap between major logical sections. Transitions from Hero sections to content areas require a larger 96px gap.
- **Breakpoints:**
  - **Desktop (1200px+):** 12 columns, 24px gutters.
  - **Tablet (768px - 1199px):** 8 columns, 20px gutters, fluid width.
  - **Mobile (<767px):** 4 columns, 16px gutters, fluid width.

## Elevation & Depth

Hierarchy in this design system is achieved through **Tonal Layering** and **1px Borders** rather than shadows. 

- **Surface Tiers:** The background is the lowest level (`--bg`). Content lives on `--surface`. Elements that need to "pop" or indicate hover states use `--surface-hi`.
- **Borders:** All cards and containers must use a 1px solid border (`--border`). On hover, the border color transitions to `--border-hi` to indicate interactivity.
- **Shadow Exception:** To maintain the "pro tool" aesthetic, shadows are strictly forbidden, with one exception: a subtle orange glow (`rgba(245,78,0,0.25)`) is used only for "Active" status icons in processing pipelines.
- **Header/Footer:** Use a fixed header with a 1px bottom border. Bottom action bars should use a 90% opacity backdrop-blur for a "glass" effect that maintains focus on the background data.

## Shapes

The shape language is structured and professional, using moderate rounding to balance the technical "monospaced" aesthetic with modern UI friendliness.

- **Cards/Containers:** 8px radius (`rounded-lg`).
- **Standard Buttons:** 6px radius for a sharper, more precise feel.
- **Technical Chips/Badges:** 4px radius to match the blocky nature of the monospaced font.
- **Status Tags/Pills:** 99px (full pill) to distinguish them from functional UI components.
- **Special Accent:** Content generated by AI or Coaching Narration should feature a **3px or 4px solid left-border** in `--accent`.

## Components

### Buttons
- **Primary:** Background `--accent`, text `--bg`. 6px radius.
- **Secondary:** Background transparent, border 1px `--border`, text `--text-primary`.
- **States:** Hover should brighten the background slightly or transition border to `--border-hi`.

### Cards
- Background: `--surface`.
- Border: 1px `--border`.
- Padding: 24px (standard) or 32px (large/hero).
- Style: Completely flat. No shadows.

### Inputs & Fields
- Inactive: 1px border `--border`, background `--bg`.
- Focus: 1px border `--accent` or `--border-hi`.
- Font: Use `Meta-data` (JetBrains Mono) for input text and placeholders to maintain the technical feel.

### Chips & Badges
- **Severity Badges:** Background `--accent-dim` (for warnings) or similar tinted variations of success/error colors. 
- **Typography:** Always use `Label-caps` (JetBrains Mono) for badges.
- **Radii:** 4px for technical data, 99px for status tags.

### Lists & Dividers
- Use 1px hairline dividers (`--border`) between list items.
- Active items should use `--surface-hi` background and a 3px `--accent` left-border marker.

### Iconography
- **Stroke:** 1.5px or 2px consistent line weight. No fills.
- **Color:** Use `--text-muted` for utility icons and `--accent` for primary features.