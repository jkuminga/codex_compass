---
name: Harness v2
colors:
  surface: '#faf8ff'
  surface-dim: '#d6d9ef'
  surface-bright: '#faf8ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f3f2ff'
  surface-container: '#ebedff'
  surface-container-high: '#e4e7fe'
  surface-container-highest: '#dee1f8'
  on-surface: '#171b2b'
  on-surface-variant: '#414844'
  inverse-surface: '#2c3041'
  inverse-on-surface: '#eff0ff'
  outline: '#717973'
  outline-variant: '#c1c8c2'
  surface-tint: '#3f6653'
  primary: '#012d1d'
  on-primary: '#ffffff'
  primary-container: '#1b4332'
  on-primary-container: '#86af99'
  inverse-primary: '#a5d0b9'
  secondary: '#5e5f5b'
  on-secondary: '#ffffff'
  secondary-container: '#e3e3de'
  on-secondary-container: '#646561'
  tertiary: '#240080'
  on-tertiary: '#ffffff'
  tertiary-container: '#3900bb'
  on-tertiary-container: '#a799ff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#c1ecd4'
  primary-fixed-dim: '#a5d0b9'
  on-primary-fixed: '#002114'
  on-primary-fixed-variant: '#274e3d'
  secondary-fixed: '#e3e3de'
  secondary-fixed-dim: '#c7c7c2'
  on-secondary-fixed: '#1b1c19'
  on-secondary-fixed-variant: '#464744'
  tertiary-fixed: '#e5deff'
  tertiary-fixed-dim: '#c9bfff'
  on-tertiary-fixed: '#1a0063'
  on-tertiary-fixed-variant: '#441cc8'
  background: '#faf8ff'
  on-background: '#171b2b'
  surface-variant: '#dee1f8'
typography:
  display-lg:
    fontFamily: Literata
    fontSize: 40px
    fontWeight: '700'
    lineHeight: '1.2'
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Literata
    fontSize: 32px
    fontWeight: '600'
    lineHeight: '1.3'
  headline-md:
    fontFamily: Literata
    fontSize: 24px
    fontWeight: '600'
    lineHeight: '1.4'
  body-lg:
    fontFamily: Hanken Grotesk
    fontSize: 18px
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: Hanken Grotesk
    fontSize: 15px
    fontWeight: '400'
    lineHeight: '1.5'
  body-sm:
    fontFamily: Hanken Grotesk
    fontSize: 13px
    fontWeight: '400'
    lineHeight: '1.5'
  code-md:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '400'
    lineHeight: '1.6'
  label-caps:
    fontFamily: Hanken Grotesk
    fontSize: 11px
    fontWeight: '700'
    lineHeight: '1'
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  container-margin: 32px
  gutter: 16px
  component-padding-x: 12px
  component-padding-y: 8px
  section-gap: 48px
---

## Brand & Style
The design system for this product is centered on a **Premium Technical Workspace** aesthetic. It moves away from the aggressive neon-on-black tropes of traditional developer tools in favor of a "Physical Studio" feel—one that is calm, precise, and highly legible.

The personality is authoritative yet quiet, prioritizing deep focus for complex AI orchestration. The style is a hybrid of **Minimalism** and **Tactile Modernism**, utilizing high-quality typography and substantial whitespace to balance high-density information. The goal is to evoke the feeling of a well-organized physical workshop where every tool has its place.

## Colors
The palette is rooted in organic, grounded tones to reduce cognitive fatigue during long sessions.

- **Primary (Deep Forest Green):** Used for primary actions, success states, and the core structural identity. It provides a sophisticated alternative to standard blue or black.
- **Background (Warm Off-White):** A "paper-like" base that feels premium and reduces eye strain compared to pure white.
- **Accent (Muted Violet):** Reserved exclusively for memory structures, graph nodes, and AI-generated connections.
- **Warning (Subtle Orange):** A soft, non-alarming tone for system notices and edge cases.
- **Text (Muted Charcoal):** Provides high legibility without the harshness of absolute black.

The dark mode should not be pitch black; use a deep charcoal (#1A1C23) base with primary green as a subtle tint for surfaces.

## Typography
The typographic strategy balances editorial elegance with technical precision. 

- **Headlines (Literata):** Used for titles, page headers, and significant UI markers. The serif provides a premium, "intellectual" feel that distinguishes this as a high-end tool.
- **Body & UI (Hanken Grotesk):** A clean, modern sans-serif for general interface elements, inputs, and descriptions.
- **Technical Data (JetBrains Mono):** Used for all code blocks, file paths, and metadata.

Korean characters in Literata should use a high-quality serif fallback like Noto Serif KR to maintain the editorial tone. For Hanken Grotesk, pair with Noto Sans KR for seamless UI legibility.

## Layout & Spacing
This design system utilizes a **Fluid Grid** with fixed-width sidebars for controls. The center workspace expands to accommodate complex code trees or graph views.

- **Rhythm:** Based on a 4px baseline. Most components should use 8px or 12px internal padding.
- **Density:** High density is achieved not by shrinking elements, but by reducing excessive decorative margins while maintaining clear alignment.
- **Sidebars:** Fixed at 280px or 320px depending on content.
- **Breakpoints:**
  - Mobile (Under 768px): Stacked layout, hidden sidebars (accessible via drawer).
  - Desktop (Over 1280px): Multi-pane view enabled (File tree + Code + AI Console).

## Elevation & Depth
Depth is created through **Tonal Layering** rather than traditional shadows.

- **Base Layer:** The warm off-white (#F8F7F2) acts as the desk surface.
- **Mid Layer:** Slightly darker or lighter panels (using #F2F1EC) create hierarchy for sidebars and toolbars.
- **Interactive Layer:** Subtle, low-opacity "ambient" shadows (0 4px 12px rgba(27, 67, 50, 0.05)) are used only for floating menus and modals to indicate they are above the workspace.
- **Separators:** 1px solid borders in #E5E4DE are the primary tool for defining regions. Avoid heavy shadows; rely on background color shifts.

## Shapes
The shape language is **Soft (0.25rem)**. This provides a balance between the rigid precision of code and a modern, approachable feel.

- **Buttons & Inputs:** Use the base 4px (0.25rem) radius.
- **Cards & Modals:** Use `rounded-lg` (8px / 0.5rem) to signify they are larger structural containers.
- **Status Pills:** Can use `rounded-xl` for a full pill-shape to distinguish them from interactive buttons.

## Components
- **Buttons:** 
  - *Primary:* Solid Deep Forest Green with white text. No gradient. 
  - *Secondary:* Ghost style with 1px Forest Green border.
- **Input Fields:** Flat appearance with a 1px bottom border by default, shifting to a full border on focus. Use JetBrains Mono for the text within code-related inputs.
- **Cards:** No border-shadow. Use a subtle background fill (#F2F1EC) and a 1px border (#E5E4DE).
- **AI Memory Nodes:** Use the Muted Violet (#7B61FF) as a glow-less stroke or subtle background tint.
- **Lists:** High-density lists with 1px dividers. Use `label-caps` for section headers within the file tree.
- **Control Center (Unique Component):** A bottom-docked status bar using the Primary color with white labels, providing a persistent "at-a-glance" view of AI agent status.