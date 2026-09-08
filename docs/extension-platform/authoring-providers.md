# Authoring Data Providers

SDK surface: `app/extensions_platform/sdk/provider.py`
(`ProviderExtensionSpec`). A provider plugs an external GIS system into the
data fabric by registering into the **single** authoritative
`AdapterRegistry` (`app/services/data_fabric/registry.py`) — the same
registry core adapters (postgis, ogc_api, wfs, wms, arcgis, stac,
geoparquet, flatgeobuf, pmtiles, s3) live in.

Worked example: `DemoTileCatalogAdapter` in
[`extensions/examples/extdemo-pack/tile_catalog.py`](../../extensions/examples/extdemo-pack/tile_catalog.py),
wired up in [`main.py`](../../extensions/examples/extdemo-pack/main.py):

```python
DEMO_TILE_PROVIDER = ProviderExtensionSpec(
    source_type="demo_tile_catalog",
    description="Offline demo raster-tile catalog backed by a bundled static JSON file.",
    adapter_cls=tile_catalog.DemoTileCatalogAdapter,
    is_raster_tile=True,
    notes="demo raster-tile catalog; metadata-only, no vector feature query",
    requires_network=True,
)

def activate(ctx) -> None:
    ctx.register_data_provider(DEMO_TILE_PROVIDER)
```

`ctx.register_data_provider(spec)` returns the canonical source type
`<ns>_<source_type>` (e.g. `extdemo_demo_tile_catalog`).

## `ProviderExtensionSpec` field reference

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `source_type` | `str` | required | snake_case identifier; projected to `<ns>_<source_type>`; must be declared in the manifest `data_providers` section |
| `description` | `str` | required | — |
| `adapter_cls` | `Optional[type]` | `None` | **Required** and must be a subclass of `GeospatialDataSourceAdapter` (`app/services/data_fabric/base_adapter.py`), otherwise a typed error blocks projection |
| `aliases` | `Tuple[str, ...]` | `()` | Each alias is projected with the same namespace prefix (`<ns>_<alias>`) |
| `supports_bbox` | `bool` | `False` | Pushdown capability flags — inputs to the core query planner's negotiation. Defaults are the most conservative (all `False`); declare only what the adapter truly honors |
| `supports_filter` | `bool` | `False` | — |
| `supports_pagination` | `bool` | `False` | — |
| `supports_datetime` | `bool` | `False` | — |
| `supports_projection` | `bool` | `False` | — |
| `is_raster_tile` | `bool` | `False` | Raster/tile source semantics (see below) |
| `notes` | `str` | `""` | Free text; truncated to 200 chars in the registered `AdapterSpec` |
| `requires_network` | `bool` | **`True`** | **Default is True.** A network-requiring provider forces the manifest to declare the `network` permission, else `PERMISSION_DECLARATION_INVALID` blocks projection. Set `False` only for genuinely offline adapters |
| `credentials_ref` | `Optional[str]` | `None` | Label for the credential slot; the SDK provides no channel that returns secrets to the LLM (see below) |

## The adapter ABC

Subclass `GeospatialDataSourceAdapter`. The constructor receives a
`ConnectionProfile` (stored as `self.profile`). The seven abstract methods:

| Method | Returns | Contract |
| --- | --- | --- |
| `probe()` | `bool` | Lightweight reachability check; no heavy I/O |
| `capabilities()` | `List[str]` | Capability flags (e.g. `pushdown_bbox`, `raster_tile`, `vector_features`) |
| `list_datasets()` | `List[Dict[str, Any]]` | Discover collections / tables / layers; entries need at least `id` |
| `describe(dataset_id)` | `DatasetDescriptor` | Full metadata contract; **fail loud** on unknown ids (raise) — never fabricate an entry |
| `preview(dataset_id, limit=10)` | `Dict[str, Any]` | Bounded sample (`schema`, `bbox`, sample `features`) |
| `query(dataset_id, query_spec)` | `QueryResult` | Pushdown query or selective fetch per declared capabilities |
| `health()` | `DataFabricHealth` | Diagnostic health object (`status`, `source_type`, `adapter`, `reachable`, `message`) |

A non-abstract `sync(owner=None)` helper registers discovered datasets into
the `SpatialCatalogService`; inherit it as-is.

## Honesty rules for raster/tile sources

Mirror the core `WMSWMTSAdapter` semantics (the example adapter copies them
verbatim):

- A raster/tile source **does not answer vector feature queries**. `query`
  returns an honest empty result with reason metadata instead of fabricated
  features (excerpt from the example adapter's `DemoTileCatalogAdapter.query`):

  ```python
  def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
      self._entry(dataset_id)
      return QueryResult(
          dataset_id=dataset_id,
          features=[],
          total_count=0,
          schema_info={"geometry_type": "Raster", "layer": dataset_id},
          metadata={
              "reason": "raster_tile_source_has_no_vector_features",
              "pushdown_bbox": bool(query_spec.bbox),
              "offline": True,
          },
      )
  ```

- `feature_count` is `None` (honest unknown), never `0` pretending to be a
  vector count.
- `preview` returns raster metadata with an empty `features` list.
- `describe` on an unknown `dataset_id` raises (`ValueError`) — fail loud.

## Credentials

Credentials travel inside the `ConnectionProfile` that the data fabric
constructs when building the adapter (`registry.build_adapter(profile)`).
They are never returned to the LLM or included in traces: profile
sanitization (`DataFabricSecurity.sanitize_profile_dict`) and URL redaction
(`DataFabricSecurity.redact_url`) exist precisely so that secrets and
credential-bearing URLs do not leak into descriptors and metadata. Do not
echo profile contents from your adapter methods.

## Network sources and SSRF

If your adapter performs HTTP, route requests through the data fabric
security layer (`make_safe_session` / `bounded_get` /
`DataFabricSecurity.validate_url`) rather than raw clients; remote raster
opens must go through `RasterReader.open`, which validates every http(s)
href before GDAL sees it. See [ogc-stac.md](ogc-stac.md).

## Resolution from the fabric

After activation the source type resolves like any core one:

```python
from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.registry import get_registry

spec = get_registry().resolve("extdemo_demo_tile_catalog")   # AdapterSpec
adapter = get_registry().build_adapter(
    ConnectionProfile(source_type="extdemo_demo_tile_catalog")
)
```

`is_raster_tile`, the capability flags, and `notes` travel with the
registered `AdapterSpec` (with `is_demo=False` — extension providers are
never flagged as demo).
