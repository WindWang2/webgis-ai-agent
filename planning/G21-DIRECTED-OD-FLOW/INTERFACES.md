# INTERFACES — G21 shared wiring register

## Ownership boundary
G21 owns OD-local product semantics, identity, aggregation, geometry/paint adapters, focus adapters, and feature-specific UI. Shared registries/schemas/routes/compiler/i18n/Studio mounts receive only minimal additive wiring.

## Planned interface audit (not yet edits)
| Shared seam | Existing owner/contract | Planned G21 delta | Compatibility rule |
|---|---|---|---|
| `app/tools/flow_tools.py` | Existing OD tool | Extend arguments/result via existing registration | Keep legacy defaults and straight-line output |
| Analysis→MapSpec converter | Existing derived-layer converter | Flow-specific metadata/paint/product refs | No new layer-ID system or MapSpec store |
| MapSpec flow submodel | Backend-authoritative document | Additive flow-only fields if required | No frame/label/raster fields; preserve old docs |
| Python/TS SVG compilers | Existing single-frame exporters | Shared directed geometry and markers | Same geometry interpretation and units |
| SelectionContext | Existing bounded IDs/predicate/epoch | OD-local transient adapter | Hover/click never creates MapSpec revision |
| Studio/layer slot | Existing UI mount | Feature panel only | No global redesign; explicit one-mutation apply |
| GIS recipe/planner binding | Existing capability expansion | OD-local primary capability correction | No G13 generic qualification rewrite |
| Pi registry/dispatcher | Existing Pi-only host | Existing tool operations and bounded context | No new planner, loop, bridge, or pin change |
| Artifact/SessionStore refs | Existing session-scoped storage | Bounded OD products/index/member queries | Cross-session denial; source revision invalidation |

## Parallel-task conflict watch
- #2032: global chrome/theme/layout/sidebar/panel widths.
- G15: frame/PublicationIR orchestration.
- G16: labels and obstacle avoidance.
- G20: raster style submodel.
- G12/G13: Pi bridge and generic capability availability.

No shared file is authorized for wholesale formatting or renaming. Actual edited locations and conflict evidence will be recorded here before each edit.
