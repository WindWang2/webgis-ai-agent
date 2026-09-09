# GIS Extension Platform Architecture

Authoritative decision records: `docs/adr/0104-gis-extension-platform-v1.md`
(V1) and `docs/adr/0105-gis-extension-platform-v2.md` (V2).
This page describes what the platform **is** on branch
`feat/extensions-v2-isolated-runtime`, not aspirations.

## The projection principle

The platform never creates a second source of truth. There is exactly one
authoritative registry per capability kind, and extensions are *projected*
into them at activation time:

| Authoritative registry | Owner module | Extension entry point | Collision policy on projection |
| --- | --- | --- | --- |
| `ToolRegistry` | `app/tools/registry.py` | `ExtensionContext.register_tool(spec)` | pre-checked `has()`; collision = typed error (core tools can never be shadowed) |
| `AlgorithmRegistry` | `app/lib/gis/algorithm_registry.py` | `ExtensionContext.register_algorithm(spec)` | pre-checked `has()`; duplicate id = typed error |
| `AdapterRegistry` (data fabric) | `app/services/data_fabric/registry.py` | `ExtensionContext.register_data_provider(spec)` | pre-checked `supported_source_types()`; append-only, alias rebinding = typed error |
| `RecipeRegistry` (gis_harness) | `app/services/gis_harness/recipes.py` | `ExtensionContext.register_workflow_pack(spec)` | seed recipes always win (keep-first); ext collision = typed error |
| Cartography registries | `app/lib/cartography/component_registry.py`, `model_library.py`, `themes.py` | `ExtensionContext.register_cartography_item(spec)` | pre-checked `get()`; duplicate = typed error |

An extension's `activate(ctx)` receives an `ExtensionContext` facade, not the
registries themselves. Every registration follows one pipeline:

```
SDK spec validation -> declaration check (manifest promised it)
  -> namespacing -> collision check against the live registry
  -> write into the authoritative registry -> record undo in ProjectionLedger
```

Any failure raises a typed `ExtensionPlatformError`; the host rolls the whole
activation back. Extensions cannot bypass the facade because they never
receive the registry objects.

## Module map (`app/extensions_platform/`)

| Module | Responsibility |
| --- | --- |
| `manifest.py` | `GisExtensionManifest` — the single extension contract (pydantic v2, `extra="forbid"`, fail closed). See [manifest-reference.md](manifest-reference.md). |
| `api_version.py` | Three version axes: `CORE_API_VERSION` ("1.1.0"), `CORE_RELEASE_VERSION` ("0.1.3"), `MANIFEST_SCHEMA_VERSION` (1); pure compatibility functions plus the V2 feature floor (`V2_FEATURE_API_FLOOR`). See [compatibility.md](compatibility.md). |
| `discovery.py` | Bounded scan of `<root>/<ext>/manifest.json` (one level deep), content fingerprint (sha256), duplicate-id handling. |
| `trust.py` | `TrustLevel` enum (core / trusted_builtin / trusted_extension / local_untrusted / blocked) and deterministic `resolve_trust`. |
| `permissions.py` | The fixed 9-permission vocabulary, `PermissionGrantSet`, `parse_grants_config`, `ExtensionPermissionDenied`. |
| `diagnostics.py` | `DiagnosticCode`, `DiagnosticSeverity`, `ExtensionDiagnostic`, `ExtensionPlatformError`. |
| `ledger.py` | `ProjectionLedger` — reversible journal of every registry write; `rollback()` replays undos in reverse order. |
| `context.py` | `ExtensionContext` — the projection facade extensions call in `activate(ctx)` (tools / algorithms / providers / cartography / workflow packs). |
| `host.py` | `ExtensionHost` + `ExtensionState` state machine + `HostPolicy`; discover / validate / activate / deactivate / unload / reload / health; singleton via `configure_extension_host` / `get_extension_host`. |
| `settings_bridge.py` | Resolves the `EXTENSIONS_*` settings block into a `HostPolicy` (fail closed on malformed config). |
| `sdk/` | The only import surface for extension authors: `ToolExtensionSpec`, `AlgorithmExtensionSpec`, `ProviderExtensionSpec`, `CartographyItemSpec`, `WorkflowPackSpec`, `run_authoring_checks`. |
| `conformance.py` | Deterministic corpus generator + executor: 2032 cases (2034 tests incl. 2 structural meta tests) pinning manifest, permission, policy, lifecycle, and V2-contract behavior. |
| `version_constraints.py` / `resolver.py` | V2 dependency version-constraint syntax (pure leaf functions) and the deterministic resolver: constraint validation, Kahn activation order, upgrade-conflict preflight. |
| `signing.py` | V2 pack content signing (`signature.json`, HMAC-SHA256 over the domain-separated fingerprint) and deterministic verification verdicts. |
| `sbom.py` | V2 deterministic software bill of materials: file inventory, python imports, dependency mirror, secret-shape scan. |
| `certification.py` | V2 certification suite (contract / supply chain / dependencies / lifecycle smoke) consumed by the CLI `certify` command. |
| `refresh.py` | V2 projection-change refresher wired into the host hook: rebuilds tool args + runtime manifest after every projection commit. |
| `broker.py` | V2 default-deny capability broker for worker extensions (network / artifacts / secrets), with a bounded per-extension audit ring. See [security-boundary.md](security-boundary.md). |
| `worker/` | V2 isolated execution: `protocol.py` (framed JSON-lines RPC), `server.py` (worker entry + event loop), `client.py` (host-side `WorkerProcess`), `context.py` (worker-side activation facade + broker facade), `spawn.py` (rlimits applied in-process at worker entry). |
| `cli.py` / `__main__.py` | Read-only developer CLI (`python -m app.extensions_platform`) + V2 supply-chain commands `package` / `verify` / `sbom` / `certify`. See [diagnostics-cli.md](diagnostics-cli.md). |

## Lifecycle state machine

`ExtensionState` has exactly nine states:

```
                      discover + validate
   (pack on disk) ──────────────────────────────► discovered
                                                      │ validate_extension()
                              ┌───────────────────────┼──────────────────────┐
                              ▼                       ▼                      ▼
                        compatible              incompatible          quarantined
                              │                  (errors; retry      (trust BLOCKED or
                              │ activate()        re-runs validate)  id collision; never
                              ▼                                      imported)
                          loading ── any projection/health error ──► failed
                              │                    (ledger rolled back;
                              ▼                      registries clean)
                  active ⇄ degraded
                  (warnings or absent optional
                   dependency ⇒ degraded)
```

Transitions implemented in `host.py`:

- `discovered → compatible | incompatible` — `validate_extension()` (API/core
  version windows, permission vocabulary, dependency resolution + cycle
  detection, entry-point existence). Any error-severity diagnostic ⇒
  `incompatible`.
- `compatible → loading → active | degraded | failed` — `activate()`:
  - required dependency must be active/degraded, else `failed`
    (`DEPENDENCY_MISSING`);
  - absent optional dependency ⇒ activation continues, state `degraded`;
  - `activate(ctx)` runs; any exception ⇒ ledger rollback, `failed`;
  - declaration ↔ registration reconciliation: undeclared registration is an
    error (rollback); declared-but-unregistered is a warning (`degraded`);
  - post-activation health gate: `unhealthy` ⇒ rollback + `failed`;
    `degraded` ⇒ warning, state `degraded`;
  - warnings (feature-flag unresolved, fingerprint drift) ⇒ `degraded`.
- `active | degraded → compatible` — `deactivate()` (runs optional
  `deactivate(ctx)` hook, then ledger rollback).
- `compatible → discovered` — `unload()` purges `webgis_ext_*` modules; must
  be deactivated first.
- `reload()` = deactivate? → unload → re-read manifest → re-fingerprint →
  validate → activate? Reload is idempotent (pinned by the conformance corpus).
- `disable()` puts an operator hold on the extension: active packs are
  deactivated first, then state `DISABLED`; `enable()` returns it to
  `discovered` and revalidates.
- `quarantined` is set at discovery when `resolve_trust()` returns `BLOCKED`
  (`EXTENSIONS_BLOCK`) or the id was already discovered under another root
  (first sorted path wins). Quarantined extensions are never imported.

## Atomic activation via `ProjectionLedger`

Every projected entry is journaled as a `ProjectionRecord(kind, projected_id,
undo, extension_id)` where `undo` is the registry's additive
`unregister_*` closure. On any activation failure, `_fail_activation()`
calls `ledger.rollback()`, which replays the undo closures in **reverse
registration order**. A single failing undo does not abort the rest (best
effort cleanup); each failure emits `REGISTRY_ROLLBACK_INCOMPLETE`. This is
the only mechanism behind the "no zombie entries after unload" and
"activation is atomic" invariants; the core registries themselves remain
unaware of extensions.

## Fingerprint and module naming (anti-staleness)

`discovery.compute_fingerprint()` hashes every file in the extension
directory (sorted relative paths, domain-separated sha256), excluding
`__pycache__/` and `*.pyc`. The fingerprint becomes part of the module name:

```
webgis_ext_<namespace>_<name>_<fingerprint[:12]>
```

Consequences:

- changed content ⇒ new module namespace ⇒ Python can never execute stale
  code from `sys.modules`;
- `unload()` / `_purge_modules()` removes **all** generations by common
  prefix (`webgis_ext_<ns>_<name>`), including modules the extension loaded
  for its own siblings;
- `activate()` re-fingerprints and warns `FINGERPRINT_CHANGED` if content
  drifted between discovery and activation, then adopts the new fingerprint;
- directories over 512 files or 8 MiB refuse fingerprinting
  (`FINGERPRINT_CHANGED` error diagnostic).

## Where the lifespan hook runs

In `app/main.py` lifespan, after `init_tools(registry)` and **before** the
runtime GIS manifest is compiled:

```python
if settings.EXTENSIONS_ENABLED:
    configure_extension_host(ExtensionHost.from_settings(tool_registry=registry))
    _ext_host = get_extension_host()
    if _ext_host is not None:
        # V2 (ADR-0105): refresh the authoritative runtime view after
        # every projection commit (see the V2 section below).
        _ext_host.set_projection_change_hook(make_projection_refresher(registry))
        _ext_host.discover()
        _results = _ext_host.activate_all()
```

Ordering matters: projected tools/algorithms enter the same compiled runtime
manifest and the same cross-registry validation as core entries. Any
extension failure is a typed diagnostic logged at WARNING level — a broken
extension degrades itself, never the server startup. With
`EXTENSIONS_ENABLED=false` (the default) and no roots configured, startup is
byte-for-byte the pre-platform behavior. `activate_all()` activates
`compatible` extensions in Kahn topological order over required-dependency
edges.

## What is frozen

These seams are core-owned; extensions may reference them but never modify
or extend their schema:

- **`CapabilityRegistry`** — extension tools/algorithms may only cite
  existing capability ids; unknown ids are a typed error at projection.
- **`MapSpec`**, **`SessionPlan`**, **`ArtifactContract`**,
  **`ExecutionPlan`** — the GIS Harness contract objects; the platform
  performs zero changes to them on this branch.

Also fixed: the extension type vocabulary (V1:
`tools | algorithms | data_providers | cartography | workflow`; V2 adds
`model_provider`), the 9-permission vocabulary (unchanged in V2 — secrets
are provisioning-gated, not a new permission word), and the recipe routing
weights (core policy).

## V2 additions (ADR-0105)

Everything below is additive; the V1 sections above describe it unchanged.

### Isolated worker execution

An extension declaring `execution.mode=worker` runs in a subprocess the
host never imports:

```
activate(id)
  └─ WorkerProcess.start()                     worker/client.py
       spawn: sanitized env (PATH/PYTHONPATH/LANG/HOME/PYTHONHASHSEED),
             own process group, stdin/stdout pipe pair
       handshake frame  ─────────────────────►  worker/server.py
             protocol / id / namespace / name / worker-mode /
             api-compat / content-fingerprint verified (fail closed;
             mismatch ⇒ package_tampered quarantine path)
       ◄─────────────────  handshake_ok {tools: [{name, description,
                             kwargs}], resource_limits}
  declaration reconciliation vs manifest (undeclared = error)
  for each offered tool:
       ToolRegistry.register(name, description, proxy, **kwargs)
       ledger.record("tool", name, undo=unregister)
  health gate: bounded RPC (call_timeout_s)
  ── record.worker = process; state ACTIVE/DEGRADED ──
dispatch:
  registry.dispatch(ns_tool) → proxy → worker.call() → result frame
  crash / timeout ⇒ typed worker_crashed / worker_call_timeout
    ⇒ ledger rollback ⇒ COMPATIBLE (retryable)
    ⇒ N consecutive crashes ⇒ QUARANTINED
```

The worker's functions never leave its process; the host projects *proxies*
whose bodies are one RPC. The worker-side `WorkerContext` collects and
validates registrations (no authoritative registries exist in the worker)
and every host capability goes through `ctx.broker.*`, which is a
`broker_request` frame answered by the host-side
[capability broker](security-boundary.md).

### Capability broker

`CapabilityBroker` (one instance per worker activation) executes network /
artifact / secret operations inside the host process under default-deny
policy: grants + per-extension network allowlist + the authoritative SSRF
gate, artifact-root confinement, and provisioning-as-authorization for
secrets. Every attempt (allow or deny) lands in a 256-entry audit ring
consumable via `host.broker_audit(id)`. In-process extensions bypass it —
their boundary statement is unchanged.

### Model provider projection

`model_providers` declarations project like tools, with a fixed shape:

```
manifest.model_providers[{id, capabilities, credentials_ref}]
  └─ activate(ctx): ctx.register_model_provider(ModelProviderSpec)
       └─ projected invoke tool "<ns>_<pid>_invoke" in ToolRegistry
            (the real agent dispatch path; aggregate form)
host.invoke_model_provider(tool, request, stream=)
  in_process: stream=True returns the raw event iterator
              (cooperative cancellation = early close())
  worker:     single-frame aggregate only; stream=True is a typed refusal
host.model_provider_inventory(): derived from manifests — no second store
```

This is the GIS domain-inference surface (segmentation / detection /
classification services). LLM chat transport stays closed behind
ADR-0102's `ModelDescriptorRegistry`. The SDK also exports optional
provider capability mixins (`StreamingVectorProvider`, `TileProvider`,
`RasterWindowProvider`, detection via `extended_provider_capabilities()`);
data-fabric dispatch to them is an explicit follow-up. Provider/manifest
field details live in [manifest-reference.md](manifest-reference.md);
`extensions/examples/extdemo-ml-pack` is a working offline example.

### Projection-change refresh (Wave 9)

`host.set_projection_change_hook()` fires on **every** projection commit —
`activate`, `deactivate`, `rollback`, `failed`, including the automatic
deactivation after a worker crash. The lifespan wires it to
`refresh.make_projection_refresher(registry)`, which re-runs
`refresh_list_available_tools_args`, recompiles the runtime manifest,
swaps the cache, and runs strict validation (downgraded to a warning
post-startup, so operator actions never become runtime errors). This is
what eliminated the V1 "post-startup deactivate leaves the manifest
stale" limitation. Hook exceptions are swallowed with a warning — a
refresh failure never breaks the lifecycle.

### Supply chain surface

`signing.py` (HMAC pack signatures + deterministic verify verdicts),
`sbom.py` (deterministic inventory + secret-shape scan), and
`certification.py` (fixed-order check suite with a boolean `certified`
verdict) are pure library modules surfaced through the CLI `package` /
`verify` / `sbom` / `certify` commands. They are developer/operator
tooling: they never activate extensions and never print key or secret
material. Usage in [packaging.md](packaging.md); the trust elevation
policy (`EXTENSIONS_TRUST_SIGNED`) in
[permissions-and-trust.md](permissions-and-trust.md).
