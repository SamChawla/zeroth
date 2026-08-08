---
name: Zeroth Design System
colors:
  surface: '#051424'
  surface-dim: '#051424'
  surface-bright: '#2c3a4c'
  surface-container-lowest: '#010f1f'
  surface-container-low: '#0d1c2d'
  surface-container: '#122131'
  surface-container-high: '#1c2b3c'
  surface-container-highest: '#273647'
  on-surface: '#d4e4fa'
  on-surface-variant: '#c0c6d6'
  inverse-surface: '#d4e4fa'
  inverse-on-surface: '#233143'
  outline: '#8b91a0'
  outline-variant: '#414754'
  surface-tint: '#aac7ff'
  primary: '#aac7ff'
  on-primary: '#003064'
  primary-container: '#3e90ff'
  on-primary-container: '#002957'
  inverse-primary: '#005db8'
  secondary: '#c2c1ff'
  on-secondary: '#1800a7'
  secondary-container: '#3630bf'
  on-secondary-container: '#b1b1ff'
  tertiary: '#ffb691'
  on-tertiary: '#552000'
  tertiary-container: '#eb6a12'
  on-tertiary-container: '#4a1b00'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#d6e3ff'
  primary-fixed-dim: '#aac7ff'
  on-primary-fixed: '#001b3e'
  on-primary-fixed-variant: '#00468d'
  secondary-fixed: '#e2dfff'
  secondary-fixed-dim: '#c2c1ff'
  on-secondary-fixed: '#0c006b'
  on-secondary-fixed-variant: '#332dbc'
  tertiary-fixed: '#ffdbcb'
  tertiary-fixed-dim: '#ffb691'
  on-tertiary-fixed: '#341100'
  on-tertiary-fixed-variant: '#793100'
  background: '#051424'
  on-background: '#d4e4fa'
  surface-variant: '#273647'
  success-container: '#005335'
  on-success-container: '#8bf8b8'
typography:
  headline-xl:
    fontFamily: Geist
    fontSize: 40px
    fontWeight: '700'
    lineHeight: 48px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Geist
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Geist
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  code-md:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 22px
  code-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 18px
  label-caps:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  DEFAULT: 0.125rem
  lg: 0.25rem
  xl: 0.5rem
  full: 0.75rem
spacing:
  unit: 4px
  gutter: 16px
  margin-mobile: 16px
  margin-desktop: 32px
  max-width: 1440px
---

## Brand & style

Modern Corporate, Glassmorphism, Technical Minimalism. Deterministic and
robust, information-dense without sacrificing clarity. "Mission control":
reliable, real-time, authoritative.

## Colors

Deep-charcoal dark mode. Primary (tech blue) drives actions and focus
states. Secondary (indigo) is reserved for secondary technical categories.
Emerald green means verified/healthy; amber/tertiary means repairing or
pending. Never use success or failure color outside an actual verification
result — the whole product's claim rests on not faking that signal.

## Typography

Geist for headlines, Inter for body copy and documentation, JetBrains Mono
for anything technical: YAML, CLI output, repo URLs, status labels, logs.

## Elevation

Glassmorphism, not drop shadows: `rgba(255,255,255,0.03)` fill,
`backdrop-filter: blur(12px)`, 1px `rgba(255,255,255,0.1)` ghost borders.
Active/focused elements get a soft outer glow in primary at ~20% opacity.

## Shapes

Soft roundedness (`borderRadius.DEFAULT = 0.125rem`) — professional, not
brutalist. Status pills are fully rounded; cards use `rounded-lg`/`rounded-xl`.

## Implementation

`web/tailwind-config.js` is the single source of truth for these tokens
(loaded via the Tailwind CDN build) — every page includes it instead of
duplicating the config block. Shared non-utility CSS (`glass-panel`,
`pulse-dot`, `flow-line`, `terminal-cursor`, `tech-grid-bg`) lives in
`web/style.css`. `web/favicon.svg` is the mark, used as both the favicon and
the nav logo.
