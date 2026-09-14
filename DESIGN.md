---
version: alpha
colors:
  ink: "#111820"
  canvas: "#f4f6f5"
  surface: "#ffffff"
  primary: "#0f766e"
  accent: "#d97706"
  danger: "#b42318"
  muted: "#64748b"
  line: "#d9e1df"
typography:
  display:
    fontFamily: "Space Grotesk, Inter, system-ui, sans-serif"
    fontSize: "2.25rem"
    lineHeight: "1.05"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.9375rem"
    lineHeight: "1.5"
  data:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "0.8125rem"
    lineHeight: "1.45"
rounded:
  sm: "0.5rem"
  md: "0.75rem"
  lg: "1rem"
spacing:
  xs: "0.375rem"
  sm: "0.625rem"
  md: "1rem"
  lg: "1.5rem"
  xl: "2rem"
components:
  button:
    rounded: "0.75rem"
    height: "2.75rem"
  card:
    rounded: "1rem"
  field:
    rounded: "0.75rem"
    height: "2.75rem"
---

## Overview

Gmail Operations is an admin control room for people who prepare and operate a large, company-owned account fleet. The product surface should feel closer to a calm infrastructure console than a sales dashboard: clear state, reliable affordances, and enough density to scan hundreds of accounts without visual noise.

The signature is a thin amber “handoff rail” used for the current Phase 1 boundary: accounts are safely stored and ready for a later login/provisioning phase, but are not presented as authenticated or healthy before that work occurs.

The product register is operational and trustworthy. It must never resemble an anti-detection tool, growth-hacking console, or generic CRM.

## Colors

Ink and canvas carry most of the page. Teal is reserved for safe primary actions and confirmed readiness. Amber marks the Phase 1 hand-off and recoverable attention. Red is reserved for actual errors or destructive actions. Status is always paired with text, not color alone.

## Typography

Space Grotesk gives headings a technical, distinctive voice without becoming decorative. Inter handles instructions and controls. IBM Plex Mono is limited to account identifiers, proxy/profile references, timestamps, and other operational metadata. Typography uses metric-compatible system fallbacks.

## Layout

The desktop shell uses a compact dark navigation rail, a light work canvas, and a persistent group selector in the header. The account registry is the primary route. The top of the page carries a readiness summary and the current group boundary before the table begins. Tables remain bounded to their own scroll surface; the page and long forms keep document scrolling.

## Elevation & Depth

Surfaces use a one-pixel neutral border and a restrained, low-opacity shadow. Panels should feel placed on the canvas, never glassy or floating for decoration. The navigation rail is the single dark anchor.

## Shapes

Controls and cards use 12–16px radii. Status badges are compact rounded labels, not interactive pills. Focus uses a 3px teal/amber ring with a 2px offset. Dense data remains comfortable by default and may opt into a compact table density later.

## Components

The frontend owns shared `Button`, `IconButton`, `SearchField`, `StatusBadge`, `Modal`, `FormField`, and `ToastRegion` patterns. Runtime values are maintained in `frontend/src/styles.css`, which is the canonical token source for this new application; this document mirrors those values and records the intent. No component may render raw credential values.

## Do's and Don'ts

- Do lead with the group context and operational readiness boundary.
- Do show exact counts, status text, and recovery guidance.
- Do keep proxy/profile references visible as metadata while keeping secrets out of the registry.
- Do use a real button or link for every action and preserve keyboard focus.
- Don't use fake health labels before Phase 2 verification.
- Don't use gradients, glass panels, animated dashboards, or bot-evasion language.
- Don't display or export raw imported credential lines after processing.
