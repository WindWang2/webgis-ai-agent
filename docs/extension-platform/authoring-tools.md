# Authoring Extension Tools

SDK surface: `app/extensions_platform/sdk/tool.py` (`ToolExtensionSpec`,
`extension_tool`). A worked example lives in the example pack
[`extensions/examples/extdemo-pack/main.py`](../../extensions/examples/extdemo-pack/main.py).

```python
from app.extensions_platform.sdk import ToolExtensionSpec

def _bbox_area_run(xmin: float, ymin: float, xmax: float, ymax: float) -> dict:
    if xmax < xmin or ymax < ymin:
        raise ValueError("degenerate bbox: require xmax >= xmin and ymax >= ymin")
    width = xmax - xmin
    height = ymax - ymin
    return {"area": width * height, "width": width, "height": height, "unit": "crs"}

BBOX_AREA_TOOL = ToolExtensionSpec(
    name="bbox_area",
    description="Compute the planar area of an axis-aligned bounding box in CRS units.",
    func=_bbox_area_run,
    summary="bbox area (example: pure computation tool)",
    tier=1,
    side_effect="pure",
    deterministic=True,
    idempotent=True,
    param_descriptions={
        "xmin": "West edge of the bounding box, in CRS units.",
        "ymin": "South edge of the bounding box, in CRS units.",
        "xmax": "East edge of the bounding box, in CRS units.",
        "ymax": "North edge of the bounding box, in CRS units.",
    },
    tags=["extdemo", "geometry"],
)

def activate(ctx) -> None:
    ctx.register_tool(BBOX_AREA_TOOL)
```

`ctx.register_tool` returns the **projected** tool name (`<ns>_<name>`, e.g.
`extdemo_bbox_area`); that is the id the rest of the platform sees.

## `ToolExtensionSpec` field reference

### Identity and core registration

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | `str` | required | snake_case identifier, no leading digit; declared in the manifest `tools` section under the same name; projected to `<ns>_<name>` |
| `description` | `str` | required | Shown to the LLM surface — write it for tool selection |
| `func` | `Callable` | required | Sync or async callable; see [below](#sync-vs-async) |
| `summary` | `str` | `""` | Short human-facing summary |
| `tier` | `int` | `1` | **Must be 1 or 2.** Tier 1 tools are always in the catalog. Tier 2 tools are domain-triggered and therefore **require at least one `domains` entry** (a tier-2 tool with no domains would never be visible to the model — rejected at registration). Tier 3 is the core security chokepoint and is closed to extensions |
| `domains` | `list[str]` | `[]` | Routing domains, same semantics as core tools |
| `execution_policy` | `Optional[str]` | `None` | One of `inline, thread, async, celery` |
| `timeout` | `Optional[float]` | `None` | Seconds |
| `version` | `str` | `"1.0"` | Tool version |
| `contract_version` | `int` | `1` | Contract version |
| `cost` | `str` | `"light"` | `light, medium, heavy` |

### Behavior semantics

| Field | Type | Default | Vocabulary |
| --- | --- | --- | --- |
| `side_effect` | `str` | `"unclassified"` | `unclassified, pure, deterministic_compute, cacheable_read, state_mutation, artifact_creation, external_side_effect, destructive` — **`destructive` is rejected for extension tools** (map that behavior to a tier-3 core tool instead) |
| `deterministic` | `Optional[bool]` | `None` | Set `True` for pure math; enables core caching semantics |
| `idempotent` | `Optional[bool]` | `None` | — |
| `network` | `Optional[bool]` | `None` | **`network=True` requires `"network"` in `required_permissions`** — otherwise a `PERMISSION_DECLARATION_INVALID` error blocks projection |
| `latency_class` | `Optional[str]` | `None` | `unknown, fast, medium, slow` |
| `memory_class` | `Optional[str]` | `None` | `unknown, light, medium, heavy` |
| `scale_class` | `Optional[str]` | `None` | `unknown, small, medium, large` |
| `result_size_policy` | `Optional[str]` | `None` | `unknown, bounded_small, bounded_medium, bounded_large, unbounded` |
| `crs_semantics`, `unit_semantics` | `Optional[str]` | `None` | Free-text CRS/unit statement for the descriptor |
| `security_tier` | `Optional[str]` | `None` | Descriptor-level annotation (not the registration `tier`) |

### Permissions

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `required_permissions` | `list[str]` | `[]` | Must be a subset of the manifest `permissions` list — a tool requiring an undeclared permission fails projection (`PERMISSION_DECLARATION_INVALID`, "tool surface inherits extension grants"). Grants are checked again **at every call** by the wrapper (see below) |
| `required_permission` | `Optional[str]` | `None` | Descriptor-level single-permission annotation |

### Schema hints for the LLM surface

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `param_descriptions` | `Optional[dict[str, str]]` | `None` | Per-parameter human text; forwarded to `ToolRegistry.register` |
| `args_model` | `Optional[type]` | `None` | Pydantic model class used by the core registry for argument validation |
| `parameters` | `Optional[dict]` | `None` | Explicit JSON-schema-ish parameter dict (overrides inference) |
| `field_extras` | `Optional[dict[str, dict]]` | `None` | Per-field extras merged into the generated schema |

### Descriptor extensions (ADR-0101/0103 vocabulary)

Passed through verbatim to the core registry descriptor kwargs when non-`None`
/non-empty: `status`, `deprecation_of`, `requires_credentials`,
`capabilities`, `algorithms`, `provider_dependencies`, `tags`,
`output_semantic_type`, `produced_refs`, `accepts_ref_types`,
`input_artifacts`, `required_context`, `map_mutations`, `data_mutations`,
`examples`, `anti_examples`, `failure_modes`, `fallback_tool`.

Two of them are existence-checked at projection time:

- `capabilities` — every id must exist in the authoritative
  `CapabilityRegistry` (a frozen seam; never invent ids);
- `algorithms` — every id must already be registered in the
  `AlgorithmRegistry`. Register namespaced algorithms **before** tools that
  cite them.

## Host rules enforced at projection

1. `tier` is an int in `[1, 2]` — never 3; tier 2 requires non-empty `domains`.
2. `side_effect == "destructive"` is banned.
3. `network=True` ⇒ `"network"` ∈ `required_permissions`.
4. `required_permissions` ⊆ manifest `permissions`.
5. `capabilities` / `algorithms` references must resolve against the live
   registries.
6. The projected name must not collide (`REGISTRY_PROJECTION_COLLISION`).

## Permission wrapper behavior

If `required_permissions` is non-empty, the SDK registers a wrapped function
instead of the raw one. Before each call it checks every required permission
against the extension's `PermissionGrantSet`; on the first missing grant it
raises `ExtensionPermissionDenied` (a typed `ExtensionPlatformError`) —
the function body never executes.

Sync and async callables get matching wrappers (`functools.wraps` preserved;
async tools are awaited inside the async wrapper). The exception surfaces
through the standard `ToolRegistry` error payload, so the LLM sees a normal
tool error rather than a crash:

```json
{"code": "TOOL_ERROR", "error_type": "ExtensionPermissionDenied", "...": "message names the extension and the missing permission"}
```

Declaration alone never satisfies the wrapper: the operator must grant the
permission via `EXTENSION_PERMISSION_GRANTS`. See
[permissions-and-trust.md](permissions-and-trust.md).

## Sync vs async functions

Both are supported. `ToolRegistry` routes execution by introspection exactly
as it does for core tools (`async def` runs on the event loop even when a
thread/celery policy is declared; sync functions follow the declared
`execution_policy`). The permission wrapper detects coroutine functions with
`inspect.iscoroutinefunction` and installs the corresponding async wrapper.

## Decorator form

```python
from app.extensions_platform.sdk import extension_tool

@extension_tool("my_reverse", "Reverse the input text.",
                side_effect="pure", deterministic=True)
def reverse_text(text: str) -> dict:
    return {"reversed": text[::-1], "length": len(text)}
```

## Checklist

1. Declare the tool in `manifest.json` (`tools` section) — undeclared
   registrations roll back the activation.
2. Keep `tier ≤ 2`, avoid `destructive`, declare `network` honestly.
3. Register tools **before** algorithms that cite them (algorithm projection
   validates `tool_candidates` against registered tools).
4. Test end-to-end; see [testing.md](testing.md).
