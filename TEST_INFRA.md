# E2E Test Infra: WebGIS AI Agent UI/UX Overhaul

## Test Philosophy
- Opaque-box, requirement-driven. Verifies user-visible behavior across light/dark themes, chat streaming markdown & code copy, map GIS toolbar & spatial popovers, tabular data exploration, and responsive layouts.
- Methodology: Category-Partition + Boundary Value Analysis + Pairwise Combinatorial Testing + Workload Testing.

## Feature Inventory
| # | Feature | Source | Tier 1 | Tier 2 | Tier 3 |
|---|---------|--------|:------:|:------:|:------:|
| 1 | Unified Design System & Dark/Light Tokens | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| 2 | Component Token Cleansing & Contrast | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| 3 | Rich Markdown Code Blocks & Copy Action | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 4 | Collapsible Reasoning & Tool Cards | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 5 | Interactive Map Toolbar HUD & 2D/3D | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 6 | Spatial Inspection Popovers & Actions | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 7 | Interactive Tabular Data Grid Preview | ORIGINAL_REQUEST §R4 | 5 | 5 | ✓ |
| 8 | Fluid Transitions & Responsive Viewports | ORIGINAL_REQUEST §R5 | 5 | 5 | ✓ |

## Test Architecture
- Vitest + Testing Library React (`frontend/tests/`): Component unit and integration tests.
- TypeScript compiler (`pnpm typecheck`): Strict type validation.
- ESLint (`pnpm lint`): Code hygiene and design token rule verification.
- Next.js Production Build (`pnpm build`): Turbopack production compilation.
