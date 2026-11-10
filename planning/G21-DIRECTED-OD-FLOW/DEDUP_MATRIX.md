# DEDUP_MATRIX — G21

Statuses: `present / partial / missing / owned-by-other / blocked`. Initial rows are scope/remote observations; source/test evidence is being added before implementation.

| Capability | Status | Current evidence / ownership | G21 action |
|---|---|---|---|
| Existing `od_flow_edges` tool and straight lines | partial | Snapshot points to `app/tools/flow_tools.py`; current source/tests pending | Preserve compatible defaults; extend only flow branch |
| `flow_od_arc` native model/export | partial | Snapshot points to `app/lib/cartography/model_library.py`; current tests pending | Reuse existing model and export path |
| Weight-driven line width/color | partial | Snapshot points to `app/services/analysis_cartography_converter.py`; custom-field consumer audit pending | Fix standard `weight` propagation end to end |
| Directed pairs, gross and net semantics | missing | No implementation claim accepted before current source audit | Implement in OD-local product contract/service |
| Reliable node/edge/member identity | missing | Coordinate/index/`a->b` shortcuts explicitly disallowed | Implement typed, scoped, unambiguous identity |
| Deterministic separated curves/arrows | missing | Existing output reported as two-vertex straight LineString | Add shared geometry/paint adapter |
| Node focus and edge details | partial | `frontend/lib/selection/selection-store.ts` is existing shared selection | Add OD adapter; no second global selection store |
| MapSpec CAS/checkpoint/undo authority | present | `CONTEXT.md` and merged #2034/#2035; source call chain pending | Consume existing lifecycle, no parallel store/undo |
| Single-frame SVG/PNG/PDF publication | present | Existing publication paths and merged #1975/#2015; flow semantics pending | Thin flow adapters and completeness checks |
| Pi-only conversation and registered dispatch | present | Merged #2036; exact runtime chain pending | Use existing Pi native surface/registry/dispatcher |
| OD recipe currently may prefer `od_matrix` | partial | `app/services/gis_harness/recipes.py` and planner snapshot | Correct only OD-local binding; regression without provider |
| G09 parameterized batch analysis | owned-by-other | Existing G09 worktree `webgis-wt-g09-workflow-batches-20261108T153411Z` | Exclude |
| G10 project data refresh | owned-by-other | Open #2022 | Consume refs; no refresh implementation |
| G11 evidence map briefs | owned-by-other | Open #2021 | Exclude |
| G12 Pi pin/bridge/native entry changes | owned-by-other | Ownership assigned even without open PR | Fixed-pin local validation only |
| G13 generic capability/qualification production wiring | owned-by-other | Ownership assigned | Only OD descriptor/recipe delta |
| G14 durable editing/stable global feature lineage | owned-by-other | Ownership assigned | Use existing IDs; OD-local identity only |
| G15 multi-frame/atlas | owned-by-other | Ownership assigned | Single-frame only |
| G16 label overrides/obstacles | owned-by-other | Ownership assigned | Existing automatic labels only |
| G17 staged import/container ingestion | owned-by-other | Ownership assigned | Consume already readable refs |
| G18 vector/table data-file export | owned-by-other | Ownership assigned | Visual PNG/SVG/PDF only |
| G19 typed calculated columns | owned-by-other | Ownership assigned | No generic field calculator or second writer |
| G20 raster style/tiles/pixel inspection | owned-by-other | Ownership assigned | No raster writes |
| #2032 global UI redesign | owned-by-other | Open #2032 | Feature-local components and narrow mounts only |

## Related issue ownership map
- #2004 selection truncation: relevant existing selection semantics; merged #2014 claims closure, verify current bounded predicate APIs rather than reopening global work.
- #2005 network-distance partition semantics: cost must not be presented as flow; relevant as a scientific-boundary regression, not ownership of network analytics.
- #2003/#1999/#2000/#2002: publication issues assigned to existing publication work; only exercise flow-specific export compatibility.
- Newly opened R1 audit issues #2037–#2072: no direct OD ownership observed in titles; inspect relevant body if source overlap emerges.
