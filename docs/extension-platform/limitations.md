# V1 Limitations (honest list)

What the GIS Extension Platform (ADR-0104) deliberately does **not** do in
V1. Each item is a real boundary in the code on this branch, documented as
such — no aspiration, no marketing.

## Extension types

- **No `model_provider` extension type.** The word is reserved in the type
  vocabulary (`RESERVED_FUTURE_TYPES` in `manifest.py`) so manifests that
  try it get a precise typed rejection (`extension_type_unsupported` /
  parse error "not supported in api_version 1.x") instead of "unknown
  field". Actual model-provider extension support is future work.

## Security

- **No sandbox.** Extensions are imported in-process and run with the same
  interpreter, memory, and OS permissions as the core. Trust levels and the
  permission system make decisions explicit and gate the SDK-provided
  channels (tool dispatch wrapper, provider HTTP helpers); they do not
  confine arbitrary Python code. `EXTENSIONS_BLOCK` (quarantine before
  import) is the only hard containment control. Details in
  [permissions-and-trust.md](permissions-and-trust.md).
- **Extension code is trusted code.** `filesystem_read/write`,
  `external_process`, and similar grants constrain SDK helper channels
  only. Filesystem and process permissions are enforced at the OS level, by
  the user the server runs as — not by the platform.

## Ecosystem

- **No marketplace.** There is no discovery service, signing, distribution,
  or update channel. Extensions arrive as directories on the operator's
  filesystem, trusted via `EXTENSIONS_BUILTIN_IDS` / `EXTENSIONS_ALLOW` and
  content fingerprints (tamper detection, not provenance).
- **Recipe routing weights are not externalizable.** Recipe selection
  scoring in gis_harness is core policy. Extensions contribute recipes and
  a `priority` tie-break; they cannot tune routing/weighting.

## Provider API surface

- **No streaming or tile methods on the provider ABC.** The
  `GeospatialDataSourceAdapter` contract is exactly the seven sync methods
  (`probe`, `capabilities`, `list_datasets`, `describe`, `preview`,
  `query`, `health`); V1 adds no streaming fetch, tile-serving, or async
  methods. Raster-tile sources express themselves through
  `is_raster_tile` + honest empty query results (see
  [authoring-providers.md](authoring-providers.md)).

## Host model

- **Single-process host.** The host keeps no locks and no cross-process
  coordination. Activation happens in the startup lifespan (single-threaded)
  or the CLI (single process); concurrent, multi-threaded activation is not
  a supported V1 contract. Multiple server workers each discover and
  activate independently against the same read-only pack directories; a
  distributed activation/registry protocol does not exist.
- **Health checks are synchronous and unbounded.** The host invokes
  `diagnostics_entry` synchronously during activation and probes, with no
  timeout or budget. A hanging health check hangs activation — keep checks
  cheap and side-effect free.
- **Deactivate keeps code loaded.** `deactivate()` rolls back all
  projections (registries are clean), but the extension's modules stay in
  `sys.modules` until `unload()` (or `reload()`) purges them by the
  `webgis_ext_*` prefix. Stateful module-level objects therefore survive
  deactivation; nothing of the extension remains *registered*, but its
  code may remain *imported*.

## Related core gaps (documented, not hidden)

- WCS and OGC API Tiles have no data_fabric adapters; XYZ is frontend-only.
  See the status table in [ogc-stac.md](ogc-stac.md).
- STAC asset materialization and PMTiles/S3 content fetch remain
  metadata-only seams in the catalog.
- The SSRF gate validates before connection; full DNS-rebinding defense
  would additionally require IP pinning at connect time (noted in
  `validate_url`).

## What is explicitly stable, despite the above

The load-bearing invariants — atomic activation with zero zombie entries,
fail-closed manifest handling, namespace isolation, declaration ↔
registration reconciliation, and the deterministic compatibility
judgments — are pinned by the 2014-case conformance corpus and are not
listed as limitations. If you find a deviation, it is a bug, not a policy;
see [testing.md](testing.md) for the lane that proves it.

## Additions from the Round-1 review (2026-09-08)

- **Bytecode blind spot.** Fingerprints exclude `__pycache__`/`*.pyc`
  (import side effects). A writer with pack-dir write access could plant a
  crafted `.pyc` whose source header matches the untouched `.py`; the
  altered bytecode would execute with a stable fingerprint. Mitigation is
  OS-level pack-dir permissions; hash-pinned bytecode is future work.
- **Namespace ownership is per-id, not reserved.** Two extension ids may
  share a namespace (`foo.bar`, `foo.bar_baz`); all collisions fail closed
  (tools/algorithms/providers/recipes/themes and — since Round 1 —
  cartography `type` slots), but a namespace is not exclusively owned by
  one extension id.
- **`CORE_RELEASE_VERSION` is manual.** The core-version window compares
  against a constant in `app/extensions_platform/api_version.py` that must
  be bumped together with the app version on release, or windows silently
  shift.
- **Health checks are unbounded sync calls.** `diagnostics_entry` runs
  in-process with no timeout; a hostile diagnostics function of a
  *trusted* extension can hang the caller. Only enable packs you trust.
- **Deactivate keeps modules loaded.** `deactivate()` rolls back all
  projections but the imported module stays in `sys.modules` (fast
  re-activation); `unload()`/`reload()` actually purge it.
- **Post-startup lifecycle is diagnostic-only.** Deactivating an extension
  after startup does not recompile the cached runtime manifest; treat
  in-process activate/deactivate after boot as a developer/CLI-only
  capability until a manifest refresh hook lands.
