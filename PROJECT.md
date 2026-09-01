# Project: WebGIS AI Agent UI/UX Overhaul

## Architecture
The WebGIS AI Agent frontend is a Next.js 16 (App Router) + React 19 application utilizing Tailwind CSS, MapLibre GL JS, Recharts, and Framer Motion.
- **Design System & Theming**: Tailwind semantic tokens (`surface.*`, `edge.*`, `ink.*`, `status.*`, `map-chrome.*`), CSS variables with automatic dark/light class toggling and pre-hydration localStorage theme bootstrapping.
- **AI Chat & Streaming**: Server-Sent Events via `useSseStream`, rAF token batching (`TokenBatcher`), O(N) incremental thought parsing (`IncrementalThinkParser`), Markdown rendering (`MiniMd`), interactive tool call cards and spatial analytical cards.
- **Map Workspace & HUD**: Decoupled uncontrolled MapLibre camera with debounced state synchronization, declarative MapSpec overlay compiler, floating draggable/resizable HUD widgets (`FloatingChrome`), dynamic slot collision avoidance, layer management drawer, and spatial inspection popovers.
- **Spatial Explorer & Analytics**: Master-detail results registry, 5-stage task progress tracker, catalog dataset materialization, Recharts analytics integration, and dataset inspection modals.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Unified Design Tokens & Theming | Purge legacy hardcoded colors, unify light/dark theme contrast across all panels | M1 | ORIGINAL_REQUEST §R1 |
| 2 | Component Token Cleansing | Cleanse `glass-panel.tsx`, `suggested-prompts.tsx`, `upload-zone.tsx`, `layer-style-panel.tsx`, delete dead `settings-panel.tsx` | M1 | ORIGINAL_REQUEST §R1 |
| 3 | Rich Markdown Code Blocks & Copy | Integrate syntax highlighting, copy button, language badges, and dark/light styling into `MiniMd` | M2 | ORIGINAL_REQUEST §R2 |
| 4 | Collapsible Reasoning & Tool Execution Cards | Polish thought accordion and step-by-step tool execution state cards with glassmorphism and status badges | M2 | ORIGINAL_REQUEST §R2 |
| 5 | Interactive Map Toolbar HUD | On-canvas floating toolbar for manual zoom, 2D/3D toggle, reset north/pitch, and measurement tools | M3 | ORIGINAL_REQUEST §R3 |
| 6 | Spatial Inspection Popover Polish | Framer-motion transitions, coordinate copy-to-clipboard, zoom-to-feature action in `PoiInfoPanel` | M3 | ORIGINAL_REQUEST §R3 |
| 7 | Interactive Tabular Data Grid | Replace raw JSON in dataset preview modal with an interactive, sortable, paginated attribute table | M4 | ORIGINAL_REQUEST §R4 |
| 8 | Fluid Transitions & Responsive Layout | Framer-motion tab transitions, responsive HUD widget stacking, TopBar compact collapsing | M4 | ORIGINAL_REQUEST §R5 |
| 9 | Full Automated Verification & Hardening | Pass 100% typecheck, lint, test (2080+ tests), and production build with zero regressions | M5 | ORIGINAL_REQUEST §Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Unified Design System & Token Cleansing | Cleanse all legacy styling classes, unify surface/border/ink tokens, light/dark contrast | Survey | DONE (Planned) |
| M2 | AI Chat & Agent Interaction Upgrade | Rich syntax-highlighted code blocks, copy action in `MiniMd`, polished thought & tool cards | M1 | PLANNED |
| M3 | Map Workspace, Interactive Toolbar HUD & Popovers | Floating GIS toolbar HUD (zoom, 2D/3D, reset, measurement), elevated POI inspection popup | M1 | PLANNED |
| M4 | Spatial Explorer Tabular Grid & Fluid Transitions | Interactive attribute table grid in preview modal, Framer Motion transitions, responsive polish | M1, M2, M3 | PLANNED |
| M5 | Final Integration Verification & Audit | Full automated test suite pass, typecheck, lint, build, Reviewer approval & Forensic Audit | M1, M2, M3, M4 | PLANNED |

## Interface Contracts
### MiniMd ↔ CodeBlock
- `MiniMd` uses `components: { code: ({ inline, className, children, ...props }) => ... }`
- Code blocks render language pill, copy button with animated checkmark feedback, scrollable container, and token styling.

### MapCanvas ↔ MapToolbarHUD
- `MapToolbarHUD` mounts as a floating HUD control on the map viewport.
- Triggers camera operations (zoomIn, zoomOut, resetNorthPitch, toggle3D) and tool toggles (measurement mode, clear annotations).

### DataPreviewModal ↔ TabularDataGrid
- `preview-modal.tsx` accepts GeoJSON / FeatureCollection / tabular rows and renders a structured table with sortable columns, row numbering, and pagination.

## Code Layout
- `frontend/components/shared/` - Common UI primitives (buttons, badges, inputs, dialogs, notices)
- `frontend/components/chat/` - Chat tab, `MiniMd`, `CollapsibleThink`, `ToolCallCard`, specialized result cards
- `frontend/components/code-highlight/` - Syntax highlighting & code block components
- `frontend/components/map/` - Map panel, floating HUD widgets, POI popups, GIS toolbars
- `frontend/components/hud/` - Telemetry HUD, Layer style panel
- `frontend/components/sidebar/` - Navigation rail, Layers tab, Results tab, Data sources tab
- `frontend/components/explorer/` - Spatial explorer progress panel, dataset preview modals
