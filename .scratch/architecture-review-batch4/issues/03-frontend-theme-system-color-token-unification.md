# 03 — Unify frontend component theme system behind CSS tokens

**What to build:**
Eliminate hardcoded color values across frontend components (`chat-panel`, `tool-call-card`, `legend-overlay`, etc.) and adapt them to consume theme tokens from Zustand `theme` slice and CSS CSS variables (`var(--bg-...)`, `var(--text-...)`), ensuring 100% theme consistency across dark and light modes.

**Blocked by:** 01 — Delete vaporware RAGAdapter and consolidate OSM HTTP fetching.

**Status:** closed

- [x] Audit frontend components (`chat-panel`, `tool-call-card`, `hud`) for hardcoded hex/rgb color strings.
- [x] Refactor styles to use CSS design system tokens and theme-aware classes.
- [x] Ensure all components respond dynamically to `theme` state switches without page reload.
- [x] Add component tests or story checks for light/dark theme rendering.
