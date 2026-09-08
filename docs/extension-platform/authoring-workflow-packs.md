# Authoring Workflow Packs

SDK surface: `app/extensions_platform/sdk/declarations.py`
(`WorkflowPackSpec`). A workflow pack carries a list of `CartographyRecipe`
objects — the gis_harness V2 DSL models defined in
`app/services/gis_harness/recipes.py` — and projects them into the
authoritative `RecipeRegistry` (`get_recipe_registry()`).

Worked example: `DEMO_WORKFLOW_PACK` in
[`extensions/examples/extdemo-pack/main.py`](../../extensions/examples/extdemo-pack/main.py).

```python
from app.services.gis_harness.recipes import CartographyRecipe
from app.extensions_platform.sdk import WorkflowPackSpec

DEMO_RECIPE = CartographyRecipe(
    id="extdemo_demo_overview",          # must start with the "extdemo_" prefix
    name="ExtDemo 概览图",
    description="示例 recipe：轻量点图产品",
    intent_tasks=["simple_view"],        # TaskType vocabulary from intent.py
    intent_cartography=["simple_point_map"],
    allowed_geometry=["Point", "MultiPoint"],
    preferred_analysis=["poi_query", "point_profile"],   # existing capability ids
    primary_cartography="simple_point_map",
    secondary_cartography=["point_overlay"],
    default_components=["title", "north_arrow", "scale_bar"],
    export_profile={"formats": ["png"]},
    priority=90,
)

DEMO_WORKFLOW_PACK = WorkflowPackSpec(
    pack_id="demo_overview",
    description="示例 recipe 包：一个最小可编译的 CartographyRecipe。",
    recipes=[DEMO_RECIPE],
)

def activate(ctx) -> None:
    ctx.register_workflow_pack(DEMO_WORKFLOW_PACK)
```

`ctx.register_workflow_pack(spec)` returns the projected pack id
`<ns>_<pack_id>` (e.g. `extdemo_demo_overview`).

## `WorkflowPackSpec` field reference

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `pack_id` | `str` | required | Un-namespaced pack id; declared in the manifest `workflow_packs` section; projected to `<ns>_<pack_id>` |
| `description` | `str` | `""` | — |
| `recipes` | `list[CartographyRecipe]` | `[]` | Real gis_harness DSL models, not SDK wrappers. Empty list ⇒ warning ("declares no recipes"); a recipe without an `id` ⇒ error |

## Recipe id prefix enforcement

Every recipe id **must already start with `<ns>_`** before registration. An
un-namespaced id is a typed error (`MANIFEST_INVALID` diagnostic: "must be
prefixed with `<ns>_` (namespace isolation)"). Unlike tools/algorithms
(where the host applies the prefix), recipe ids are written by you in
prefixed form because the id participates in the harness DSL and routing
indexes. The manifest declares the `pack_id` without the prefix; the host
reconciles the projection as `<ns>_<pack_id>`.

## Seed precedence: keep-first

The `RecipeRegistry` is keep-first: `register()` on an existing id logs a
warning and **keeps the seed entry**, ignoring the newcomer ("seed recipes
always win"). The platform turns this into a hard error for extensions:
`register_workflow_pack` pre-checks `registry.get(recipe_id)` and refuses
with `REGISTRY_PROJECTION_COLLISION` if a core seed recipe already owns the
id. Extensions can therefore never shadow a seed recipe, and deactivate-time
`unregister` only removes entries the extension itself projected.

## Compile-time reference validation (fatal)

Recipes are validated by the harness's own compile-time checks — these are
**fatal** for extension packs, and the example pack's test mirrors them
exactly:

- `intent_tasks` must be members of the `TaskType` vocabulary
  (`app/services/gis_harness/intent.py`);
- every id in `preferred_analysis` / `optional_analysis`
  / `task_optional_analysis` must exist in the authoritative
  `CapabilityRegistry` (a frozen seam — never invent capability ids);
- `primary_cartography` / `secondary_cartography` entries must resolve in
  the map model registry (`get_map_model_registry().resolve(...)`).

A projection that references unknown ids fails activation and rolls back.

## Routing weights are core policy

Recipe selection scoring and routing weights live in gis_harness core code
(`CartographyRecipe.priority` is only a stable tie-break among equally
matching recipes). **Routing weights are not externalizable in V1**: an
extension cannot tune how the planner weighs intent matches, keyword hits,
or analysis capabilities — it can only contribute well-formed candidate
recipes and set `priority`.

## Deactivation

Each projected recipe is journaled in the `ProjectionLedger`; deactivation
calls the registry's additive `unregister(recipe_id)`, which cleans the
by-id index, by-task index, domain/keyword inverted indexes, and content
fingerprint cache. Seed recipes are never unregistered.
