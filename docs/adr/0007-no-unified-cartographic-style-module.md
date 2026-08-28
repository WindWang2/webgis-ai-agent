# No unified cartographic-style module until MapSpec feeds the live map

**Status:** superseded by [ADR-0078](0078-unified-thematic-style-contract.md) (2026-08-12)

> **Superseded.** The revisit trigger below was satisfied by [ADR-0036](0036-mapspec-runtime.md):
> `applyMapSpecToMap` was deleted and replaced with `MapSpecRuntime.reconcile`, which IS the
> live map path (`map-panel.tsx` → `hudStateToMapSpec` → `runtime.reconcileAsync`). ADR-0078
> introduces the canonical thematic-style contract this ADR deferred. The text below is
> preserved as the historical rationale.

We will **not** introduce a unified `CartographicStyle` module (one canonical shape with adapters
emitting both `legend_spec` and MapSpec `paint.color: StyleMethod`) at this time. The two
"parallel legend pipelines" an architecture review surfaced are **not** both live: the
`legend_spec` → `<ThematicLegend>` path is what the running Next.js map renders, while the
MapSpec `paint.color` → compiler path is **headless-only** — it drives the Playwright runtime
validator, eval scoring, and the static exporter. `applyMapSpecToMap`, the function that would
feed the live map from compiled MapSpec, has zero non-test callers. With only one live consumer,
a shared internal representation would satisfy a *hypothetical* seam, not a real one (one adapter
= hypothetical seam).

The converter's `legend_spec → StyleMethod` bridge stays as-is: it earns partial keep because the
headless validator compiles it and MapLibre accepts it (it catches malformed stops and the
`method`/`type` discriminant drift). That its *thematic correctness* is never asserted (no
consumer checks the rendered colors are right; the exporter doesn't consume it) is a **feature
gap** — the deferred `webgis-visual-judge` — not an architecture flaw.

## Trigger to revisit

Revisit this decision when `applyMapSpecToMap` gains a live caller (i.e. when the dual-write
vision of spec #192 Story 6 — the live map renders from compiled MapSpec — actually lands). At
that point path ii becomes a second live consumer and a unified style module earns its keep.
Until then, classification triplication is illusory (`CartographyService.classify` is the only
real algorithm; the converter trusts pre-built breaks; inline emitters call `classify`), and
divergent `legend_spec` producer shapes are tolerated by the converter's defensive reads.
