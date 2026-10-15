---
version: alpha
name: "Northstar Operations"
description: "A precise operations workspace with a bright paper-like canvas, compact navigation, orange action cues, and fast-scanning account data."
colors:
  ink: "#182127"
  canvas: "#f0f2f3"
  surface: "#ffffff"
  primary: "#ed7623"
  accent: "#f2a529"
  success: "#16845f"
  danger: "#c83a37"
  muted: "#718087"
  line: "#dce1e4"
typography:
  display:
    fontFamily: "Space Grotesk, Inter, system-ui, sans-serif"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
  data:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
rounded:
  sm: "0.4rem"
  md: "0.6rem"
  lg: "0.8rem"
spacing:
  page-gutter: "2.25rem"
  card-gap: "0.8125rem"
  sidebar-width: "15.75rem"
components:
  button:
    rounded: "0.5rem"
    height: "2.625rem"
  card:
    rounded: "0.5625rem"
  field:
    rounded: "0.4375rem"
---

## Overview

### Creative North Star

Northstar borrows the visual grammar of a well-made operations ledger: a pale paper workspace, graphite navigation labels, hairline row dividers, and a restrained orange signal for current focus and primary action. The reference is the supplied Conceptzilla CRM workspace, translated into account operations rather than sales data. Its distinctive move is a bright, compact navigation rail that keeps a large registry surface open for scanning.

### Product context and register

- **Audience and primary job:** Administrators and scoped operators manage account groups, account state, browser sessions, newsletters, proxies, and infrastructure.
- **Target markets and evidence:** Existing SaaS workflows and persisted records; the interface locale is English (`en-US`).
- **Locale policy:** English UI with system-font fallbacks; dates and numbers use locale-aware browser formatters.
- **Usage scene:** Desktop-first operational work with dense tables, frequent filtering, and occasional narrow-screen review.
- **Register:** Product/admin across all authenticated routes.
- **Memorable signature:** The warm orange focus and action signal runs from selected navigation through selected filters and primary controls.
- **Restraint:** Account status, scope, exact counts, and existing operations take priority over decorative charts or motion.
- **Anti-references:** Dark teal-only admin consoles, generic CRM lead content, glass effects, fake trend charts, and status represented by color alone.
- **Token ownership/runtime mapping:** `frontend/src/styles.css` is canonical. The semantic `--color-*`, `--radius-*`, and `--shadow-card` variables drive shared shell, cards, tables, controls, and themes; this document mirrors their light-theme values. The `html[data-theme="dark"]` adapter remaps the same semantic variables. No duplicated component-local theme tokens should be introduced.

## Colors

The light theme uses graphite ink (`colors.ink`) on a cool paper canvas (`colors.canvas`) with white work surfaces (`colors.surface`). Orange (`colors.primary`) marks primary actions, selected navigation, and current filters; amber (`colors.accent`) supports secondary attention. Green is reserved for successful or healthy state and red for errors, risk, and destructive actions. Counts and statuses retain text labels and sufficient contrast. Dark mode remaps semantic values to charcoal surfaces and a lighter orange while preserving each role.

## Typography

Space Grotesk is used sparingly for display headings, Inter for controls and readable prose, and IBM Plex Mono for account identifiers, proxy references, and compact operational metadata. Use sentence case in navigation and controls; reserve uppercase tracking for short section labels. Numeric values remain aligned and easy to compare.

## Layout

The desktop shell pairs a narrow, pale sidebar with a sticky white header and a wide work canvas. Page content is centered up to 1580px with responsive gutters. The account workspace puts live summary counts above filters and its data table; the account selection action rail stays visible near the viewport edge while scrolling. Tables keep their own horizontal scroll surface; the page remains the vertical scroll owner. Account details open as a right-side profile drawer with its own scroll area and persistent header/actions. At mobile widths the navigation becomes a dismissible drawer, account details become full-screen, actions wrap, and tables retain access to all columns through horizontal scrolling.

## Elevation & Depth

Thin neutral borders establish most hierarchy. Cards use minimal shadow; the selected navigation state and modal backdrop provide the strongest visual separation. Avoid gradients, glass, and decorative blur. Dark mode uses tonal contrast and the same border-first grammar.

## Shapes

Controls use 7–8px radii, cards use 9px radii, and compact badges use smaller rounded corners. Focus is a visible orange-tinted ring. Tables use crisp horizontal dividers with no decorative container within a container.

## Components

### Foundational visual states

Buttons, links, fields, filters, and menu items expose hover, focus-visible, pressed/selected, disabled, and busy states. Loading, empty, and error states keep the existing API-backed recovery guidance. Status badges pair shape/color with a text label.

### Buttons and actions

Orange is the primary action; white bordered controls are secondary; red is reserved for destructive actions. Selected-account controls remain scoped and explicit. Busy states keep button dimensions stable.

### Navigation and data display

The compact light sidebar has labeled navigation sections and orange selected state. The sticky header retains current page, OVH status, theme, sync, and user controls. Tables prioritize identifiers, group, status, and controls with legible row dividers, visible count badges, and responsive horizontal access. Selected records use a dark floating action surface; account identity and session evidence live in the profile drawer.

### Forms and overlays

Forms retain their existing labels, validation, retry behavior, and native select ownership. Dialogs and detail surfaces preserve their current focus and keyboard behavior. Theme changes recolor shared semantic tokens rather than duplicating route styles.

### Iconography

Lucide icons use consistent 15–18px outline strokes. Icon-only actions keep accessible names; section labels remain visible where meaning might otherwise be ambiguous.

### Motion

Transitions are short and state-oriented. Reduced-motion preferences disable nonessential animation. Routine page content does not animate into view.

### Content and data visualization

Use real account and operations data only. Numeric cards summarize persisted counts; no invented sparkline or trend values. Errors name the failed operation and offer a concrete retry or correction.

## Do's and Don'ts

- **Do:** Preserve every existing route, permission, data state, and operational action while changing shared presentation.
- **Do:** Use orange consistently for focus, selection, and primary action.
- **Don't:** Replace the operational account model with CRM leads or fabricated analytics.
- **Don't:** hide status meaning in color or weaken contrast in dark mode.
