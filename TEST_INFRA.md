# Test Infrastructure: WebGIS AI Agent

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
- Next.js Production Build (`pnpm build`): Webpack production compilation (`build:turbopack` is an optional alternative).


## Backend verification

- Install the locked environment: `uv pip install -c requirements.lock -r requirements-dev.txt`.
- Fast regressions: `pytest tests/unit --no-cov -q`; complete backend: `pytest -m "not perf and not cartography and not real_services" --no-cov -q`.
- Conftest pins safe environment defaults and isolates each worker database in temporary SQLite storage. Set `TEST_DATABASE_URL` only for an intended disposable database.
- Markers: `cartography` owns deterministic map/compiler checks; `perf` owns isolated performance thresholds; `real_services` requires `REAL_SERVICES=1`, Postgres/PostGIS, Redis and Celery. Browser/LLM tests require their explicit runtime gates.
- Each of six CI shards uploads raw coverage plus commit/run metadata. `scripts/combine_backend_coverage.py` validates every shard and applies the 75% threshold to the union. Missing, stale or partial shards fail closed.
- `QUALITY_ORDER_SEED=<integer>` deterministically shuffles backend collection to expose global state leaks; nightly lanes cover the full backend suite.
- `scripts/ci-local.sh --fast` runs local lint, types, frontend tests and selected backend contracts; the full script adds build, backend, cartography and performance lanes. Docker/service lanes run separately in CI.
