# Standardized Typography Design Scale & Component Font Audit Research Findings

## 1. Executive Summary

Wayfinder Ticket #231 requires auditing hardcoded micro-fonts (`text-[10px]`, `text-[11px]`, `text-[12px]`) across `frontend/components/` and designing a unified typography scale. Micro-fonts (< 12px) degrade readability, create UI fragmentations across high-DPI and standard displays, and violate accessibility standards (WCAG 2.1 SC 1.4.4).

This research paper defines the standardized typography scale for WebGIS AI Agent UI components, maps all hardcoded micro-fonts to standard Tailwind utility classes, and specifies line-height rules to ensure optimal legibility across spatial analysis result cards, map HUD panels, and system drawers.

---

## 2. Standardized Typography Design Scale Architecture

We establish a strict 4-level typography scale replacing arbitrary pixel values with standard Tailwind CSS utility classes:

### 2.1 Design Scale Matrix

| Tier | Tailwind Class | Font Size | Line Height | Weight | Typical Applications |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Badge / Meta** | `text-xs` | 12px (0.75rem) | `leading-normal` (1.25) | Medium / Bold | Status pills, tool badges, category tags, stat card labels, timestamp footers |
| **Body Text** | `text-sm` | 14px (0.875rem) | `leading-relaxed` (1.625) | Regular | Narrative summaries, description boxes, card commentary, parameter previews |
| **Section Header** | `text-base` | 16px (1.00rem) | `leading-normal` (1.50) | SemiBold / Bold | Analysis result card titles, panel section headings, table headers |
| **Panel Header** | `text-lg` | 18px (1.125rem) | `leading-snug` (1.375) | Bold | Main modal titles, primary drawer headers, top-level status bar |

### 2.2 Line Height Standard (`leading-relaxed`)
- All body text blocks, analysis commentary, and narrative explanations **must** use `leading-relaxed` (1.625 line height ratio) to maintain vertical spatial harmony and prevent line-clashing in dense spatial data views.

---

## 3. Component Font Audit & Migration Mapping

### 3.1 Analysis Result Cards (`frontend/components/chat/`)
- **`h3-lisa-result-card.tsx`**:
  - Badge (`H3 LISA`): `text-[12px]` -> `text-xs`
  - Stat Label (`cfg.label`): `text-[11px]` -> `text-xs`
  - Stat Description (`cfg.desc`): `text-[10px]` -> `text-xs`
  - Summary Paragraph: `text-[12px]` -> `text-sm leading-relaxed`
  - Card Footer: `text-[12px]` -> `text-xs`
- **`isochrone-result-card.tsx`**:
  - Badge (`等时圈分析`): `text-[12px]` -> `text-xs`
  - Stat Labels (`设施点数量`, `覆盖面积`): `text-[11px]` -> `text-xs`
  - Summary Paragraph: `text-[12px]` -> `text-sm leading-relaxed`
  - Card Footer: `text-[12px]` -> `text-xs`
- **`st-dbscan-result-card.tsx`**:
  - Badge (`ST-DBSCAN`): `text-[12px]` -> `text-xs`
  - Stat Labels (`聚类簇数`, `聚类点数`, `噪声点数`, `跨度`): `text-[11px]` -> `text-xs`
  - Timeline Header: `text-[11px]` -> `text-xs`
  - Summary Paragraph: `text-[12px]` -> `text-sm leading-relaxed`
  - Card Footer: `text-[12px]` -> `text-xs`

### 3.2 Drawers & Panels (`frontend/components/drawers/`, `frontend/components/map/`)
- **`template-gallery.tsx`**:
  - Badges & Tags: `text-[10px]` -> `text-xs font-mono`
  - Drawer Info: `text-[10px]` -> `text-xs`
- **`map-panel.tsx`**:
  - Watermark Subtext: `text-[10px]` -> `text-xs`

---

## 4. Verification Invariants

1. **Zero Hardcoded Pixel Fonts**: Search for `text-[10px]`, `text-[11px]`, and `text-[12px]` in `frontend/components/` must yield 0 results.
2. **Accessibility Compliance**: Minimum rendered font size across all UI components is strictly 12px (`text-xs`), satisfying WCAG SC 1.4.4 readable contrast & scale bounds.
