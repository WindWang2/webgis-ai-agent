# Authoring Cartography Extensions

SDK surface: `app/extensions_platform/sdk/declarations.py`
(`CartographyItemSpec`). Projection lives in
`ExtensionContext.register_cartography_item` and writes into the three
authoritative cartography registries — the SDK never re-implements their
schemas.

Worked example: `NOTE_SCALE_BAR` in
[`extensions/examples/extdemo-pack/main.py`](../../extensions/examples/extdemo-pack/main.py).

## The three kinds and their targets

| `kind` | Target registry | Descriptor built from `payload` |
| --- | --- | --- |
| `component` | `app/lib/cartography/component_registry.py` (`get_component_registry`) | `MapComponentDescriptor.model_validate(payload)` |
| `model` | `app/lib/cartography/model_library.py` (`get_map_model_registry`) | `MapModel.model_validate(payload)` |
| `theme` | `app/lib/cartography/themes.py` (`get_cartographic_theme_registry`) | `CartographicThemeDescriptor.model_validate(payload)`, registered via `register_theme` |

**`payload` is the target registry's own constructor kwargs.** The SDK does
not duplicate a second schema: field-level validation is performed by the
target registry's own pydantic model during projection. If the payload does
not validate, the item is rejected with `REGISTRY_PROJECTION_FAILED` and the
activation rolls back. Copy field names from the core descriptors, not from
this document.

## Namespacing rules enforced at projection

- The registered id is always `<ns>_<id>`; the SDK overwrites
  `payload["id"]` with the projected value. If the payload carries an `id`,
  it must equal the declaration id (mismatch = typed error).
- **Component `type` prefix rule:** the payload `type` must start with
  `<ns>_`. If it does not (or is absent), the host rewrites it to
  `<ns>_<type or declaration id>`. This keeps extension component types
  disjoint from the core component taxonomy.
- Collisions with existing ids are typed errors
  (`REGISTRY_PROJECTION_COLLISION`) — cartography registries have mixed
  duplicate policies internally, so the platform pre-checks every projection.

## `runtime_status`: planned, never native

Extensions cannot honestly claim renderer evidence, so:

- `CartographyItemSpec.runtime_status` defaults to `"planned"`;
- claiming `"native"` is a **validation error** ("no renderer evidence; use
  'planned'");
- at projection, component `runtime_status` is forced to
  `planned` or `unavailable` (anything else, including `native`, is a typed
  error).

**"planned ≠ rendered"** is the standing honesty rule: `native` is reserved
for items with a real frontend renderer implementation. Leave
renderer/exporter support fields empty when you cannot back them.

## Honesty fields (declaration/catalog metadata)

| Field | Type | Default | Vocabulary |
| --- | --- | --- | --- |
| `runtime_status` | `str` | `"planned"` | `planned`, `unavailable` for extensions (`native` rejected) |
| `supported_renderers` | `tuple[str, ...]` | `()` | Renderer ids that actually render this item |
| `export_behavior` | `str` | `"degraded"` | `degraded`, `unsupported`, `native_export` |
| `legend_behavior` | `str` | `"auto"` | Free annotation for legend rendering |
| `accessibility_notes` | `str` | `""` | A11y annotation for the catalog |
| `degradation_policy` | `str` | `"omit_with_disclosure"` | `omit_with_disclosure`, `render_placeholder`, `block` |

These fields document what really happens at export/degradation time; they
feed the generated catalog. The example component declares
`export_behavior="degraded"` and `degradation_policy="omit_with_disclosure"`
precisely because it has no renderer.

## Example

```python
NOTE_SCALE_BAR = CartographyItemSpec(
    kind="component",
    id="note_scale_bar",
    description="Annotation-style scale bar variant; planned (no renderer evidence yet).",
    runtime_status="planned",
    export_behavior="degraded",
    degradation_policy="omit_with_disclosure",
    supported_renderers=(),
    payload={                      # MapComponentDescriptor kwargs
        "type": "extdemo_note_scale_bar",   # <ns>_ prefix (enforced)
        "category": "navigation.scale_bar",
        "name": "Note Scale Bar (extdemo)",
        "name_zh": "注记比例尺（示例）",
        "description": "示例组件：注记式比例尺变体（仅目录声明，无前端实现）。",
        "placement_domain": "overlay",
        "default_variant": "default",
        "variants": ["default", "minimal"],
        "default_position": "bottom-left",
        "allowed_positions": ["bottom-left", "bottom-right", "none"],
        "cardinality": "single",
        "priority": 21,
        "states": ["visible", "hidden"],
        "collision_class": "chrome",
        "tags": ["extdemo", "navigation"],
        "accessibility": {"role": "img", "label_zh": "注记比例尺"},
    },
)

def activate(ctx) -> None:
    ctx.register_cartography_item(NOTE_SCALE_BAR)   # registers "extdemo_note_scale_bar"
```

Manifest declaration (matching, un-namespaced):

```json
{
  "cartography_items": [
    {
      "kind": "component",
      "id": "note_scale_bar",
      "description": "Annotation-style scale bar variant; planned (no renderer evidence).",
      "runtime_status": "planned"
    }
  ]
}
```

`MapModel` payloads follow the same pattern (`kind="model"`); when you add an
extension map model, declare a sensible fallback/degradation policy in the
payload so consumers know how to degrade it honestly.
