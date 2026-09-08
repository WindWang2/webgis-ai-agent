# Testing an Extension

The platform's own tests double as the executable specification. Copy the
fixture patterns below from
[`tests/unit/extensions_platform/test_example_pack.py`](../../tests/unit/extensions_platform/test_example_pack.py),
which drives the real example pack through a complete lifecycle.

## The end-to-end fixture pattern

The canonical sequence: **copy pack dir → build `HostPolicy` → discover →
activate → dispatch/assert → deactivate with zero-residue parity.**

```python
import shutil
from pathlib import Path

from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.extensions_platform.sdk import run_authoring_checks
from app.tools.registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_SOURCE = REPO_ROOT / "extensions" / "examples" / "extdemo-pack"
EXTENSION_ID = "extdemo.pack"


def _cleanup_leftovers() -> None:
    """Idempotent cleanup: singleton registries persist across tests, so a
    failure before deactivate would leave extdemo_* entries that collide on
    the next activation. Unregister through each registry's extension
    unload channel (returns False if absent)."""
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.services.data_fabric.registry import get_registry
    from app.services.gis_harness.recipes import get_recipe_registry

    get_algorithm_registry().unregister("extdemo.compactness")
    get_registry().unregister("extdemo_demo_tile_catalog")
    get_component_registry().unregister("extdemo_note_scale_bar")
    get_recipe_registry().unregister("extdemo_demo_overview")


def test_pack_lifecycle(tmp_path: Path):
    _cleanup_leftovers()
    pack_root = tmp_path / "root"
    pack_root.mkdir()
    shutil.copytree(PACK_SOURCE, pack_root / "extdemo-pack")

    tool_registry = ToolRegistry()          # fresh registry, host-injected
    host = ExtensionHost(
        tool_registry=tool_registry,
        policy=HostPolicy(
            roots=(pack_root,),
            builtin_ids=frozenset({EXTENSION_ID}),
            grants={EXTENSION_ID: frozenset({"network"})},
        ),
    )
    host.discover()
    diagnostics = host.activate(EXTENSION_ID)
    record = host.get_record(EXTENSION_ID)
    try:
        assert record.state is ExtensionState.ACTIVE
        assert diagnostics == []            # no warnings -> ACTIVE, not DEGRADED
        assert tool_registry.has("extdemo_bbox_area")
    finally:
        host.deactivate(EXTENSION_ID)
        _cleanup_leftovers()
```

Notes on the pattern:

- `ToolRegistry` is a fresh per-test instance (the host injection surface);
  the algorithm / provider / cartography / recipe registries are process
  singletons, so assert with `has` / `get` existence checks (order-robust)
  and always clean leftovers, as above.
- The `finally` block guarantees deactivation even when an assertion fails —
  "unload leaves no zombies" must hold for your extension too:

  ```python
  host.deactivate(EXTENSION_ID)
  assert record.state is ExtensionState.COMPATIBLE
  assert not tool_registry.has("extdemo_bbox_area")            # tools
  assert not get_algorithm_registry().has("extdemo.compactness")
  assert not get_registry().is_supported("extdemo_demo_tile_catalog")
  assert get_component_registry().get("extdemo_note_scale_bar") is None
  assert get_recipe_registry().get("extdemo_demo_overview") is None
  ```

## Dispatch and honesty assertions

```python
async def test_dispatch_math_and_honesty(tool_registry):
    result = await tool_registry.dispatch(
        "extdemo_bbox_area", {"xmin": 0, "ymin": 0, "xmax": 3, "ymax": 2}
    )
    assert result["area"] == pytest.approx(6.0)

    bad = await tool_registry.dispatch(
        "extdemo_bbox_area", {"xmin": 2, "ymin": 0, "xmax": 1, "ymax": 2}
    )
    assert bad.get("code") == "VALIDATION_ERROR"   # honest failure surfaces
    assert bad.get("error_type") == "ValueError"   # through the standard payload
```

Permission denial asserts the typed path:

```python
async def test_ungranted_permission_denied_typed(tool_registry):
    result = await tool_registry.dispatch("acme_synth_fetch", {"url": "https://x"})
    assert result.get("code") == "TOOL_ERROR"
    assert result.get("error_type") == "ExtensionPermissionDenied"
```

## Algorithm authoring checks

Drive `run_authoring_checks` against the module's spec and implementation
(no host needed):

```python
module = record.module
diagnostics = run_authoring_checks(
    module.COMPACTNESS_ALGORITHM, module._polygon_compactness_run
)
assert diagnostics == []          # all numerical smoke cases pass
square = module._polygon_compactness_run(module._SQUARE_GEOJSON)
assert square["compactness"] == pytest.approx(pi / 4, abs=1e-3)

descriptor = get_algorithm_registry().get("extdemo.compactness")
assert descriptor.scientific_status == "EXPERIMENTAL"
assert descriptor.tool_candidates == ["extdemo_polygon_compactness"]
```

Also mirror the recipe compile-time validation for workflow packs: `TaskType`
vocabulary membership, capability ids resolvable in the `CapabilityRegistry`,
cartography ids resolvable in the map model registry (see
`test_recipe_passes_compile_time_validation` in the example pack test).

## The conformance corpus as contract reference

`app/extensions_platform/conformance.py` generates **2014 deterministic
cases** (executed by `test_conformance_corpus.py`). If your question is "what
happens when …", find the case family and read the case — it is the
contract:

| Family | Covers |
| --- | --- |
| `manifest_valid` / `manifest_invalid` | manifest field matrix (names, versions, sizes, duplicates) |
| `compatibility` | api/core version windows |
| `lifecycle_activate` / `lifecycle_rollback` / `lifecycle_reload` | activation, atomic rollback, idempotent reload |
| `lifecycle_dependency` | required/optional deps, cycles |
| `lifecycle_trust` / `lifecycle_disabled` | quarantine, operator disable |
| `lifecycle_degraded` / `lifecycle_validate` | degraded activation, validation |
| `permission_matrix` / `host_permission_invalid` | grant matrix, typed denials |
| `tool_sdk_matrix` | tool spec validation rules |
| `provider_sdk` | provider rules (subclassing, network permission) |
| `cartography_honesty` | planned/native honesty rules |
| `policy_matrix` | allow/block/builtin/grant policy combinations |

Case expectations use the syntax `pass`, `fail:manifest_invalid`,
`fail:<DiagnosticCode>`, `state:<ExtensionState>`,
`diagnostic:<DiagnosticCode>`.

## Exact commands

```bash
# The whole extension-platform lane (2100+ tests, incl. the 2014-case corpus)
pytest tests/unit/extensions_platform/ -q --no-cov

# Just the example-pack end-to-end tests
pytest tests/unit/extensions_platform/test_example_pack.py -q --no-cov

# Just the conformance corpus
pytest tests/unit/extensions_platform/test_conformance_corpus.py -q --no-cov

# OGC/STAC hardening (data fabric)
pytest tests/unit/extensions_platform/test_ogc_stac_hardening.py -q --no-cov

# Lint (zero new warnings relative to master is the gate)
ruff check app/ tests/
```

Everything in the lane is offline, deterministic, and LLM-free.
