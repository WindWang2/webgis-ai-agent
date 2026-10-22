# 0101. Agent Tooling & Model Runtime Foundation V2

**Date:** 2026-09-06
**Status:** Accepted
**Branch:** `feat/agent-tool-model-runtime-v2` (baseline `544a09c`)

## Context

The repository already owns the hard seams this platform must keep:

- Pi as the default agent host (`USE_NEW_AGENT`), dispatching tools back into the
  Python process over the bridge-secret HTTP seam (`/pi-tools/execute`);
- `ToolRegistry` as the single execution truth (`_dispatch_impl`), including the
  tier-3 confirmation ContextVar chokepoint (SEC-F1);
- `ToolDispatchService` as the dispatch choke point (dedup, waves, ref minting,
  LLM slimming, event log);
- `ToolCatalog` tier/keyword selection on legacy paths and a frozen 7-tool native
  surface + `webgis_execute` proxy on the Pi path;
- `ToolExecutionPolicy` (INLINE/ASYNC/THREAD/CELERY, ADR-0043), tool cost priors
  (#996), schema byte cache (#1062);
- provider-health tracking for **geodata** providers only (ADR-0027) — no LLM
  provider health exists;
- raw-httpx OpenAI-compatible LLM client with EXECUTION/PLANNER/TITLE/SPATIAL
  role resolution, connect-phase retries only;
- context assembly with a 6000-token **history** budget; system blocks unbounded;
- `app/evaluation` deterministic benchmark runner (ADR-0092) and the perf harness
  (ADR-0046).

Forensic audit found structural limits this branch addresses without moving any
truth: tool metadata is decorator-args only (no lifecycle, no side-effect class,
no fingerprints); tool-name/argument aliasing is a hand-grown chain of
tool-specific `if` blocks; Pi and legacy surfaces diverge (frozen 7 vs
per-round catalog); model/provider knowledge is three env-var strings; no
bounded trace schema exists; replay infra does not exist.

## Decisions

1. **Pi remains the agent host.** No replacement agent loop. V2 ships
   adapters/projections consumed *by* Pi and the legacy engine.
2. **`ToolRegistry` remains execution truth.** `ToolDescriptor` (V2) is a
   *derived read-only projection* stored alongside the same registration
   metadata (`registry._metadata`), constructed by `registry.descriptor(name)`.
   No second registry may execute tools.
3. **ToolDescriptor V2** (`app/tools/descriptor.py`): lifecycle status
   (stable/experimental/deprecated/hidden/external_unavailable/planned),
   side-effect class (pure → destructive) driving derived retry-safe /
   cacheable / replay-safe flags, capability/algorithm ids **by reference**
   (never copies of GIS semantic truth), I/O contract declarations, and free
   retrieval tags. All fields optional with derived defaults — existing tools
   register unchanged. tier ≥ 3 forces `side_effect=destructive` (a missing
   annotation can never downgrade a dangerous tool to "pure").
4. **Registration gates harden.** Unknown register kwargs now raise
   `ValueError` (previously silently swallowed — a typo'd descriptor field
   never took effect; this is a deliberate, test-pinned contract change).
   Invalid status / deprecation combinations / result-size policies fail
   registration. PLANNED tools are refused at dispatch (`TOOL_NOT_EXECUTABLE`).
5. **Deterministic fingerprints** (canonical JSON → SHA-256, 16-hex):
   - `schema_fingerprint` — model-visible *input contract* only (function name
     + parameters). Description edits never move it.
   - `descriptor_fingerprint` — full descriptor contract + schema fingerprint.
     A description-only change moves this but not the schema fingerprint —
     the two classes of change are distinguishable.
   - `registry_fingerprint` / `manifest_fingerprint` — order-independent
     content fingerprints for surface-projection cache invalidation and replay
     compatibility checks. Cached on the registry, invalidated by
     `register()` / `update_args_model()`.
6. **`tags` promoted to a descriptor field.** Three tool modules already passed
   an informal `tags=` kwarg that was silently dropped; it is now first-class
   validated metadata and the lexical seed for Wave-3 tool retrieval.

## Consequences

- All 200+ existing tools register unchanged (defaults derived); descriptor
  enrichment is incremental and additive.
- Downstream waves build on the descriptor: alias/argument normalization as a
  declarative layer (Wave 2), unified ToolSurface projection + retrieval
  (Wave 3), model/provider descriptors and routing (Wave 4), context budgets
  (Wave 5), no-progress V2 signatures (Wave 6), replay compatibility via
  fingerprints (Wave 8).
- Strict kwarg rejection turns registration typos into startup failures —
  consistent with the existing `cost`/`execution_policy` registration gates.
