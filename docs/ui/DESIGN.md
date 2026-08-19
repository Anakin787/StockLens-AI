---
name: StockLens-AI Narrative
colors:
  surface: '#0b1326'
  surface-dim: '#0b1326'
  surface-bright: '#31394d'
  surface-container-lowest: '#060e20'
  surface-container-low: '#131b2e'
  surface-container: '#171f33'
  surface-container-high: '#222a3d'
  surface-container-highest: '#2d3449'
  on-surface: '#dae2fd'
  on-surface-variant: '#c3c6d7'
  inverse-surface: '#dae2fd'
  inverse-on-surface: '#283044'
  outline: '#8d90a0'
  outline-variant: '#434655'
  surface-tint: '#b4c5ff'
  primary: '#b4c5ff'
  on-primary: '#002a78'
  primary-container: '#2563eb'
  on-primary-container: '#eeefff'
  inverse-primary: '#0053db'
  secondary: '#4edea3'
  on-secondary: '#003824'
  secondary-container: '#00a572'
  on-secondary-container: '#00311f'
  tertiary: '#ffb2b7'
  on-tertiary: '#67001b'
  tertiary-container: '#d22348'
  on-tertiary-container: '#ffecec'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#dbe1ff'
  primary-fixed-dim: '#b4c5ff'
  on-primary-fixed: '#00174b'
  on-primary-fixed-variant: '#003ea8'
  secondary-fixed: '#6ffbbe'
  secondary-fixed-dim: '#4edea3'
  on-secondary-fixed: '#002113'
  on-secondary-fixed-variant: '#005236'
  tertiary-fixed: '#ffdadb'
  tertiary-fixed-dim: '#ffb2b7'
  on-tertiary-fixed: '#40000d'
  on-tertiary-fixed-variant: '#92002a'
  background: '#0b1326'
  on-background: '#dae2fd'
  surface-variant: '#2d3449'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  data-mono:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: -0.01em
  label-caps:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.05em
  headline-md-mobile:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  gutter: 12px
  margin: 24px
  container-max: 1600px
---

## Brand & Style
The design system is engineered for high-performance quantitative analysis. It adopts a **Corporate / Modern** aesthetic with **Glassmorphic** overlays to signify AI-driven "layers" of intelligence. The visual language balances the density of a Bloomberg Terminal with the refinement of modern SaaS, evoking a sense of algorithmic precision and absolute reliability.

- **Minimalist Density:** Maximum information per square inch without visual clutter.
- **Data-First:** Visual weight is prioritized for market movements and AI signals.
- **Sophisticated Trust:** A darker, focused environment that reduces eye strain during long-term monitoring.

## Colors
The palette is centered on a "Deep Charcoal" ecosystem to provide a high-contrast foundation for data.
- **Primary (Electric Blue):** Used exclusively for AI insights, active trading states, and primary actions.
- **Success (Emerald) & Danger (Crimson):** High-saturation tones for immediate recognition of market direction. 
- **Warning (Amber):** Reserved for volatility alerts and liquidity risks.
- **Neutral (Slate/Charcoal):** Multiple tiers of slate define the hierarchy of "Surface," "Container," and "Overlay."

## Typography
This design system utilizes **Inter** for its exceptional legibility in UI and **JetBrains Mono** for numerical data and tickers. 

- **Tabular Figures:** All numerical data must use the monospaced font or the `tnum` (tabular numbers) OpenType feature in Inter to ensure column alignment in financial tables.
- **Hierarchy:** Use `label-caps` for table headers and metadata to differentiate from actionable content.
- **Density:** Line heights are kept tight (1.2x to 1.4x) to facilitate high-density information display.

## Layout & Spacing
The layout follows a **Fixed Grid** model on desktop to maintain data structure, transitioning to a fluid stack on mobile. 
- **Grid:** A 12-column system with tight 12px gutters.
- **Density:** A 4px base unit ensures tight alignment of tickers and data cells.
- **Breakpoints:** 
  - Mobile: < 768px (Single column, hidden sidebars)
  - Tablet: 768px - 1280px (Collapsible sidebars, 2-column widgets)
  - Desktop: > 1280px (Full dashboard, multi-pane view)

## Elevation & Depth
Depth is signaled through **Tonal Layers** and **Glassmorphism**, avoiding traditional heavy shadows which clutter dense layouts.
- **Level 0 (Base):** Deep Slate (`#0F172A`).
- **Level 1 (Cards):** Slightly lighter Slate (`#1E293B`) with a 1px border (`#334155`).
- **Level 2 (Overlays):** Glassmorphic panels with 12px backdrop-blur and 40% opacity primary tinting for AI suggestions.
- **Outlines:** Subtle, low-contrast borders are the primary separator, creating a "blueprint" feel.

## Shapes
The design system employs a **Soft** shape language (4px radius) to maintain a professional, systematic appearance. 
- **Standard Radius:** 4px for buttons, inputs, and small widgets.
- **Large Radius:** 8px for main dashboard cards.
- **Interactive States:** Use sharp transitions for hover states to emphasize mechanical precision.

## Components
- **Data Grids:** High-density rows (32px height) with zebra striping on hover. Numerical columns are right-aligned.
- **Trading Buttons:** Large, high-contrast blocks. "Buy" uses a subtle green glow on hover; "Sell" uses a red glow.
- **AI Insight Chips:** Glassmorphic capsules with an electric blue border and pulsing "live" indicator.
- **Input Fields:** Inset style with 1px border. Focus state uses a 2px primary blue outer ring.
- **Sparklines:** Compact, monochromatic line charts integrated directly into table cells or card headers to show 24h trends.
- **Tab Switchers:** Segmented controls with no background, using a bottom-accent bar for the active state to keep the UI "light."