# G16-AUTHORABLE-LABELS · BASELINE

- Created: 2026-11-11 (UTC stamp branch: 20261110T163415Z)
- Repo: WindWang2/webgis-ai-agent (verified `git remote -v` → github.com/WindWang2/webgis-ai-agent.git)
- BASE_BRANCH: master; BASE_SHA: `816705218a5aa6202bf15d745cb31cddb3437f44` (== research snapshot; `origin/master` unmoved after `git fetch origin --prune`)
- Worktree: `/home/kevin/projects/webgis/webgis-worktrees/goal-g16-authorable-labels-20261110T163415Z`
- Branch: `zcode/goal-g16-authorable-labels-20261110T163415Z`
- Main workspace is read-only for this task; sibling worktrees (G12/G13/G14/G15/G21…) untouched.

## Open PRs at start (re-queried)

| PR | Branch | State | Relation to G16 |
|---|---|---|---|
| #2075 | zcode/goal-g14-durable-vector-editing | open | G14 owns stable feature identity (`_wfid` WFID, ADR-0277) + structural edits. G16 **consumes** existing stable ids (`FEATURE_ID_KEYS` chain, `feature.id` top-level first); never re-implements identity. No PR-file overlap expected (G14 touches feature_edit/*, layer-edit bridge FE; G16 touches label modules). |
| #2074 | zcode/goal-g19-typed-field-compute | open | Derived-attribute workbench; complementary, no overlap. |
| #2073 | zcode/goal-g13-capability-execution | open | Capability availability production wiring; G16 uses existing ToolRegistry/dispatch seams only. |
| #2032 | feat/ui-redesign-20261109 | open | Global UI redesign. G16 adds **new** label-editor component + minimal mount points (toolSlice union + toolbar button group + map-panel child), no theme/layout/global chrome edits. |
| #2022 | zcode/goal-g10-project-data-refresh | open | Data refresh; G16 records source `content_revision` per override for staleness — consumes existing field, no edits. |
| #2021 | zcode/goal-g11-evidence-map-briefs | open | Briefs; unrelated. |

Merged dedup list re-verified: #1973 (Pi v1.5.0), #1873 (feature editing), #1875 (linked views/label fidelity), #1975 (publication cartography), #2011–#2017, #2033–#2036 — all present in master history at BASE_SHA. #2036 = Pi-only conversation (no ChatEngine fallback; do not resurrect).

## Environment

- Python: run tests from worktree with main venv interpreter `/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest` (verified: tests/cartography/test_label_collision_export.py 10 passed).
- Frontend: pnpm; main workspace has frontend/node_modules; worktree needs its own install (pnpm store should be warm) — verify before FE tests.
- vendor/pi submodule NOT initialized in this worktree; per task §C only G12 touches the pin; backend label work does not import vendor/pi. Pi tool tests use the stub/dispatch seams like existing tools.
- Heavy-stage lock: `webgis-heavy-stage.lock` under shared git-common-dir; default `-j2`/workers 1–2 (§E resource discipline).

## Key source facts (all verified at BASE_SHA)

1. Layer label model: `app/lib/cartography/mapspec_schema.py:454-472` `MapSpecLayerLabel` — field/size/color/halo/mode/topN/priorityField/zoomBands/sizeRatio/haloMode. **No feature-level override slot.**
2. Export label channel: `mapspec_schema.py:~803` `MapLabelConfig` (collision: "deterministic", maxLabels) — the only label config root; G16 extends it (owner per §D table).
3. Live label runtime: `frontend/lib/mapspec-runtime/runtime.ts:1604-1758` `addLabelSublayerSafe` → `${layer.id}-label` symbol sublayer; strategy from `label-layout.ts`; identity via `ID_KEY_CANDIDATES` (frontend) / `FEATURE_ID_KEYS` (backend, `app/services/feature_edit/contracts.py`).
4. Export deterministic solver twins: `app/lib/cartography/label_collision.py` + `frontend/lib/mapspec-compiler/label-solver.ts` (greedy, MAX_LABELS_PER_EXPORT=400, MAX_OBSTACLES_PER_SOLVE=200, keep-first-200 obstacles, no obstacle identity). Python/TS parity frozen by `tests/cartography/golden_corpus/label_collision/`.
5. Obstacles today: circle-only AABB collection at `app/services/mapspec_to_svg.py:1093-1100,1516`; solve call :1869; label collection :1620-1835 (synthetic request ids, no feature identity). Line/polygon obstacles explicitly deferred (“后续波次”).
6. Stable identity: `app/services/feature_edit/identity.py` — `_wfid` `wf1-<sha256[:16]>` materialized on first edit; pre-materialization resolution via `FEATURE_ID_KEYS` chain, values coerced via `str()` (`1` and `"1"` collapse — existing contract, test it explicitly).
7. Mutation machinery: intent dataclass (`app/services/mapspec/intents.py`) + Body schema (`app/schemas/mapspec_mutation_schema.py`) + codec/descriptor (`mutation_registry.py`) + handler (`mutation_handlers.py`); route `app/api/routes/mapspec_mutations.py` → `body_to_intent` → `apply_gis_mutation(origin="user"|"agent", expected_revision CAS, client_mutation_id idempotency)`. FE client `frontend/lib/mapspec/user-mutation.ts`.
8. Pi tools: `app/tools/cartography_tools.py` `@tool(registry, ...)` pattern; evidence keys forwarded via `_forward_evidence`; mapspec mutations via `mapspec_store` adapter.
9. Map tool/mode system FE: `frontend/lib/store/slices/toolSlice.ts` `MapToolId` union + `map-toolbar-hud.tsx` `SketchToolGroup`; gesture precedent `sketch-editor.tsx` (dragPan disable, escape stack, commit-on-pointerup); mount sibling `<SketchEditor mapRef={mapRef}/>` in `map-panel.tsx` (~:1800).
10. FE undo: `frontend/lib/workbench/undo.ts` `recordCommand`; agent-commit undo `agent-commit-undo.ts`.
11. Sources carry `content_revision` (`GeoJSONMapSpecSource`) — reuse for override staleness; no new truth.

## Scope boundaries honored

- Single-map first: no frame layout/atlas work (G15 owns `MapSpecFrame`/PublicationIR frame fields; frameId only consumed if present).
- No source geometry/attribute edits (G14); label edits never rename data fields — only override label presentation text.
- No Pi vendor/bridge/extension changes (G12); standard @tool + result ABI only.
- UI: new subcomponent + minimal mount points; no theme/global-layout changes (#2032 conflict avoidance).
