# Keep MapSpecStore as a deep orchestrator — do not collapse the satellite modules

**Status:** accepted

We will **not** collapse `mapspec_layer_pipeline`, `mapspec_compile_coordinator`,
`mapspec_source`, and `mapspec_checkpoint_store` into a single `MapSpecPipeline` module.
`MapSpecStore` stays the deep orchestrator; the four satellites stay as separate concerns.

## Context

Architecture-review Candidate #1 (2026-08-01 report) framed `MapSpecStore` as a "shallow
pass-through facade forwarding calls to 4 micro-files," forcing callers and tests to bounce
across 5 files to understand a single MapSpec operation. The proposed fix was to collapse
ingestion, source shape classification, CLI compilation, and checkpoints into one deep
`MapSpecPipeline` engine.

A code investigation contradicted the premise on five dimensions.

## What the investigation found

### 1. The store is deep, not shallow

Tallying `MapSpecStore`'s 11 public methods (`app/services/mapspec_store.py`, 333 lines):

- **8 do real work:** `get_mapspec` (cache→file fallback + back-fill), `save_mapspec`
  (dual-write file + revision + Redis), `init_project` (builds the MapSpec skeleton),
  `set_view` (merges partial view), `source_profile` (profiles + strict url policy),
  `layer_upsert` (orchestrates ingestion→source-upsert→view-merge→layer-replace),
  `layer_remove` (companion-label filter + dual cleanup), `layout_set` (merge layout).
- **3 are delegations, but wrapped:** `compile_mapspec_cli`, `validate_mapspec`,
  `checkpoint`/`rollback` — each still loads the mapspec, guards the not-found case,
  computes `session_dir`, and (for rollback) re-persists. There are **zero** 1-line pure
  forwards.

The store owns the mapspec dict, the revision log, the dual-write invariant, and the
operation ordering (ingestion→apply→save). That is the definition of a deep orchestrator,
not a pass-through facade.

### 2. The four satellites are genuinely separate concerns

- `mapspec_layer_pipeline` — per-layer domain transforms (raster render, analysis→layer,
  profiling, auto-view). Pure function; returns values, writes nothing back.
- `mapspec_compile_coordinator` — spawns the TS CLI subprocess, reads back style.json.
  Owns `_CLI_PATH`, `subprocess.run`, timeouts. A process boundary, not a dict transform.
- `mapspec_source` — pure dict shape-classifier primitives (ADR-0008).
- `mapspec_checkpoint_store` — snapshot bytes + `ref:` materialization + rollback recovery.

Collapsing these merges a subprocess runner and a snapshot serializer into a CRUD store.

### 3. The "dynamic import" leak does not exist

The report's diagram shows `Pipeline -.dynamic import.-> Converter[analysis_converter]`.
There is **no** `importlib` / `__import__` / `import_module` anywhere in
`app/services/mapspec_*.py`. What the diagram labels "dynamic import" is ordinary
**function-scoped `from ... import`** statements (e.g. `mapspec_store.py:177`), used to
break the circular dependency created by `mapspec_layer_pipeline.py` importing back from
`mapspec_store`. This is a standard Python pattern, not a smell. No `analysis_converter`
runtime import exists.

### 4. This reverses documented decisions

- **ADR-0008** deliberately extracted `mapspec_source` as a pure-function module and
  explicitly rejected a typed value object. Its "Trigger to revisit" (a 4th caller
  appearing) is **not** met.
- The other three extractions are attributed in their docstrings to prior architecture
  review work (`mapspec_compile_coordinator.py:3`, `mapspec_checkpoint_store.py:3`,
  `mapspec_layer_pipeline.py:2-4`). The split was deliberate.

### 5. The test-pain claim has no support

`grep` for mocks/patches across the four mapspec test files returns **0**. The tests
exercise `MapSpecStore` end-to-end through its public interface (`layer_upsert`,
`checkpoint`, `rollback`, `compile_mapspec_cli`) against a real `clean_session` fixture —
no satellite mocking. The satellites have their own pure-function unit tests.

## Decision

Keep `MapSpecStore` as the deep orchestrator. Do not collapse the four satellite modules
into a `MapSpecPipeline`. The function-scoped imports used to break the circular
dependency stay as-is.

## What we are not doing

- No merger of `mapspec_*` files into one module.
- No change to the function-scoped import pattern (it is intentional circular-dependency
  avoidance).
- No revival of a typed `MapSpec` value object (still rejected by ADR-0008).

## Trigger to revisit

Reopen only if **ADR-0008's own trigger fires** — a 4th caller needing `mapspec_source`'s
shape primitives — or if a new concern genuinely belongs in the store's coordination layer
rather than behind its own seam. A re-suggestion framed as "facade collapse" or "too many
files" does not, by itself, meet this bar; it must show the store is actually shallow
(methods that do not coordinate) or that two satellites share one mutable concern.
