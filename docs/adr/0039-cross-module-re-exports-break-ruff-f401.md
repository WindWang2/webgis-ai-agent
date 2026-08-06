# 39. Cross-Module Re-Exports Break ruff F401

Date: 2026-08-06

## Status

Accepted

## Context

The architecture-deepening batch (PRs #301–#303) relocated symbols into deep modules and left backward-compat facade files behind that deliberately re-export those names so legacy `from <facade> import X` keeps working. ruff's F401 (unused-import) is **file-local**: it only sees whether the defining module references the import, not whether other modules import it from that module. Running `ruff check --fix` on such a facade therefore removed re-exported names that were actually consumed cross-module, silently breaking `from <facade> import X` call sites.

The breakage was silent in a specific way: CI runs the full test suite against real Postgres, while local development defaults to SQLite with a sync fallback. The removed re-exports failed only in the CI test run, not locally — a class of CI-only failure that is expensive to debug and easy to ship past.

A concrete example: `app/services/cartography_service.py` re-exports `COLOR_PALETTES` from `app/lib/cartography/palettes.py`, and the density module (`app/lib/geo_analysis/density.py`) imports it from the facade. ruff sees an "unused" import in `cartography_service.py` and would delete it under `--fix`; the density module then fails at import time.

## Decisions

1. **Per-file F401 ignores for backward-compat facades**: add `[tool.ruff.lint.per-file-ignores]` entries in `pyproject.toml` listing `F401` for the facade modules whose "unused" imports are the public re-export surface, not dead code: `app/services/chat_engine.py`, `app/services/chat/context_builder.py`, `app/services/mapspec_store.py`, `app/services/tool_dispatch_service.py`, `app/services/chat/execution_engine.py`, `app/tools/_utils.py`.

2. **Inline `# noqa: F401` for single-symbol re-exports**: when a facade re-exports only one or two symbols, annotate the import line directly instead of widening the per-file ignore. Example: `app/services/cartography_service.py` — `from app.lib.cartography.palettes import COLOR_PALETTES  # noqa: F401` — with a comment noting it is a deliberate cross-module re-export.

3. **Per-file F821 suppression for `from __future__ import annotations` files**: the stringified-annotation future import makes ruff's F821 (undefined-name) unable to see names referenced only in type hints, so it flags them as undefined even though they are imported under `TYPE_CHECKING` or used only in signatures. Suppress `F821` per-file for `app/agent_pi_bridge.py` and `app/services/chat/context_builder.py` (names affected: `ToolRegistry`, `ToolDispatchResult`, `PiRpcClient`, `ChatContextAssembler`). These were verified non-bugs by the passing test suite.

## Consequences

- **Backward-compat facades stay linted** while no longer producing false F401 hits; `ruff --fix` can no longer delete cross-module re-export surfaces.
- **Suppressions are scoped, not global**: only the facade files and the two future-annotations files are excepted; the rest of the codebase keeps full F401/F821 coverage.
- **Compile-time import safety**: the test suite remains the guard for the suppressed files (imports still resolved at module import time), which is exactly where the original silent CI failure surfaced.
- **Documented policy**: future facade files follow the same pattern — inline `# noqa: F401` for one-off re-exports, `per-file-ignores` for full facades, never blind `--fix`.
