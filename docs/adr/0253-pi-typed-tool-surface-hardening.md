# 0180. Pi Typed Tool Surface Hardening — Surface Byte Budget, Pre-dispatch Input Gate, Surface Metrics

**Date:** 2026-09-13
**Status:** Accepted
**Branch:** `harness/pi-typed-tool-surface-v1`

## Context

ADR-0103 landed the dynamic Pi tool surface: the spawn dump registers every
model-visible, non-tier-3 tool (316 of 327 at baseline) with real registry
schemas, and per-turn `WEBGIS_ACTIVE_TOOLS` markers govern visibility via
`pi.setActiveTools`. Three hardening gaps remained:

1. **No schema-byte budget on the per-turn active set.** Selection capped
   tool *count* (k_max=30) but not *bytes*; measured median schema is 528
   chars, the largest 6,527 (`webgis_component_update`), and the native-7
   front door alone costs 17,277 — a large-schema turn could quietly double
   model context cost. The V3 projector's `byte_budget` was consumed only by
   the legacy V2 path, never by the Pi turn path.
2. **Schema-invalid arguments travelled the full execution pipeline.** The
   registry validates inside `_dispatch_impl` — *after* dedup-slot
   acquisition, wave-semaphore queuing and ref resolution — and returned
   validation failures shaped like tool business failures. At the Pi
   boundary (where the model can self-correct cheaply) no pre-dispatch gate
   and no machine-readable validation contract existed. Post-ADR-0103 the
   dynamic surface makes bare-name calls first-class, so argument garbage
   from the model now enters dispatch on two entry paths, not one.
3. **No surface-level metrics.** Invalid-tool-name rate, argument
   validation failure rate, proxy-fallback share and per-turn surface bytes
   were unobservable; the wave-specific regression gates in ADR-0103 did not
   cover them.

## Decisions

1. **Activation-set byte budget (default 32 KB).**
   `pi_native_surface.apply_surface_byte_budget` greedily fits dynamic
   candidates in selection order using the registry's `schema_size` cache
   (#1062). The native front door plus a documented proxy constant are
   always kept — the front door never fails closed. Trims are recorded as
   `byte_budget` drop reasons, flow into the turn `disclosure` dict, the
   `Stage.TOOL_SURFACE` chain emission (`surface_bytes` /
   `surface_budget` / `budget_dropped`) and the surface metrics snapshot.
   `PI_SURFACE_BYTE_BUDGET=0` reverts to the count-only behavior. The spawn
   dump is untouched: dormant schemas stay uncompressed per the existing
   single-truth discipline.
2. **Pre-dispatch input gate with zero false rejects.**
   `app/services/chat/pi_input_gate.py` runs in
   `_dispatch_tool_bound` after classification/existence/tier checks and
   *before* `ToolDispatchService.dispatch`. Its tiered design never rejects
   an input the registry would accept:
   - normalization via the same declarative tables (`normalize_tool_arguments`);
   - unknown-field rejection mirrors #828 semantics (including the
     `extra="allow"` exemption) — **and mirrors its placement**: the
     registry's #699 oversized bypass skips the #828 check entirely
     (kwargs-tolerant signatures such as `heatmap_data` execute with stray
     keys), so the gate exempts unknown-field rejection and the per-field
     probe round for oversized args too; required-presence and structural
     mismatch stay (the bypass enforces the former and subsumes the latter);
   - structural mismatch: container values for scalar-only annotations are
     certain errors (ref resolution replaces string leaves only and never
     turns a container into a scalar);
   - per-field TypeAdapter probes (the #1113 P3-3 helper — the same
     machinery as the registry's oversized bypass) run only for values whose
     subtree contains **no string leaves**; strings are skipped because
     `ref:` cursors and session aliases can rewrite them into any payload;
   - when the whole args tree has no string leaves, the gate runs the same
     Pydantic `model_validate` the registry runs — identical semantics by
     construction; oversized trees mirror the registry's bypass;
   - any gate-internal failure fails open to the registry's authoritative
     validation.
   Rejections return `details.code = SCHEMA_VALIDATION_REJECTED` with
   field-path issues, normalization evidence and `retryable: true` — a
   distinct contract from tool business failures: no dedup slot, no wave
   queueing, no `tool_failed` session event, no `harness_failure`
   classification. One existing v3-Phase-E contract is deliberately
   preserved: the rejection still marks the SessionPlan capability rows the
   tool would have served as `failed` (best-effort, same
   `apply_tool_result(success=False)` path as dispatch errors) — a
   schema-rejected call is visible plan accounting, not a silently-skipped
   attempt, so retry/recovery semantics are unchanged.
   `ToolRegistry.args_model()` is a new public read-only
   accessor so the gate and dispatch share one model object.
3. **Surface metrics + regression gates.**
   `app/services/chat/pi_surface_metrics.py` keeps process-level counters
   (invalid_tool_name, schema_validation_rejected, proxy_wrapped,
   direct_surface), the last surface projection (bytes/budget/dropped) and
   a bounded recent-reject window; `/api/v1/metrics/digest` gains an
   additive `pi_surface` section. Unit gates pin: golden-task surface
   quality (must-reach tools, zero tier-3 leakage, budget honored) and
   bounded gate latency (schema-invalid calls are rejected before wave
   queuing).

## Consequences

- Behavior is additive and kill-switched: `PI_SURFACE_BYTE_BUDGET=0` and
  gate fail-open paths revert to exact baseline behavior; `PI_DYNAMIC_TOOL_SURFACE`
  semantics are unchanged.
- The gate can only *miss* invalid inputs (the registry still rejects them,
  later) — never invent rejections; parity tests pin both directions against
  real registry dispatch.
- Legacy (ChatEngine) dispatch is untouched: the gate lives at the Pi bridge
  boundary, honoring ADR-0068 (all execution still flows through
  ToolDispatchService) and the tier-3 confirm chokepoint.
- The proxy (`webgis_execute`) remains the long-tail fallback; proxy
  fallback rate is now observable.
- Known limitation: alias-minted string payloads still bypass the gate's
  fast path by design; the registry's post-resolution validation remains the
  authority for them.
