# CURRENT ARCHITECTURE — extension platform, registries, sandbox, skills

All paths relative to repo root `C:/Users/wangj.KEVIN/projects/webgis-wt-extension-sdk-v2`. Line numbers at `faa453a8`.

## 1. The extension platform (`app/extensions_platform/`, ~13.8k LOC)

The platform already implements "projection, not a second registry" (ADR-0104 §Decisions 1). There is exactly one authoritative registry per capability kind; extensions are *projected* into them at activation, with a per-pack undo ledger.

### 1.1 Manifest — the single pack contract

`app/extensions_platform/manifest.py`
- `GisExtensionManifest` (`manifest.py:276`): pydantic v2, `extra="forbid"`, frozen, fail-closed. Key fields: `schema_version` (host knows 1, `MANIFEST_SCHEMA_VERSION`), `id` (`<namespace>.<name>` enforced), `namespace` (regex `^[a-z][a-z0-9_]{1,31}$`, `RESERVED_NAMESPACES` at `manifest.py:39` = core/webgis/app/pi/builtin/internal/gis/lib/tools/vendor), `version`/`api_version`/`minimum_core_version`/`maximum_core_version` (semver), `extension_types` (vocab `manifest.py:43`: tools/algorithms/data_providers/cartography/workflow/model_provider), `capabilities` (**informational labels, explicitly not authorization**, `manifest.py:291`), `permissions`, `trust` (self-declared, no authority), `dependencies`/`optional_dependencies` (`DependencyDeclaration` `manifest.py:77`, with optional version constraint strings), `feature_flags`, `settings_schema`, `execution` (see 1.6), `entry_point` (identifier segments only, `manifest.py:333`), `diagnostics_entry` (`module:fn`).
- **Declared sections** (declaration = promise of what activate() will register, each ≤ `MAX_DECLARED_ITEMS=128` total):
  - `tools: list[ToolDeclaration]` (`manifest.py:112`): name/description/summary/tier(1|2 — extension tools barred from tier 3)/domains (tier 2 requires ≥1 domain)/side_effect.
  - `algorithms: list[AlgorithmDeclaration]` (`manifest.py:139`): id/description/scientific_status.
  - `data_providers: list[DataProviderDeclaration]` (`manifest.py:152`).
  - `cartography_items: list[CartographyItemDeclaration]` (`manifest.py:165`): kind ∈ component|model|theme.
  - `workflow_packs: list[WorkflowPackDeclaration]` (`manifest.py:193`).
  - `model_providers: list[ModelProviderDeclaration]` (`manifest.py:235`): GIS inference models only — LLM chat transport is a closed surface (ADR-0102), typed rejection.
- Duplicate ids within a section rejected in `_cross_field` (`manifest.py:362`); cross-section projection-name collision tool `<pid>_invoke` vs provider checked at `manifest.py:489`.
- Version gating `_validate_v2_features` (`manifest.py:424`): V2 features (execution/model_providers/dep version constraints) require `api_version >= 1.1.0`; V3 features (worker class-instance sections, streaming) require `>= 1.2.0`. `api_version.py` holds `CORE_API_VERSION` ("1.1.0" per docs; actual value in module), `check_extension_api_compatibility`, `meets_api_floor`.
- Parser entry: `manifest_from_dict` (`manifest.py:534`) → `(manifest|None, error_str)`.

### 1.2 Discovery + fingerprint

`app/extensions_platform/discovery.py`
- `discover_extensions(roots, max_extensions=64)` (`discovery.py:150`): scans `<root>/<ext>/manifest.json` one level deep, bounded; per-extension fail-closed; duplicate id across roots → later copy quarantined as `DiscoveryFailure` (`discovery.py:193`).
- `compute_fingerprint(ext_dir)` (`discovery.py:72`): deterministic sha256 over sorted relative paths + bytes; excludes `__pycache__`, `*.pyc`, `signature.json`; caps 512 files / 8 MiB.
- `SIGNATURE_FILENAME = "signature.json"` (`discovery.py:42`).

### 1.3 Loading

`app/extensions_platform/loader.py`
- `load_entry_module(...)` (`loader.py:50`): importlib file-location load under fingerprinted name `webgis_ext_<ns>_<name>_<fp12>` (`MODULE_PREFIX` `loader.py:20`, `module_name_for` `loader.py:45`); entry must resolve inside pack dir (`resolve_entry_path` `loader.py:23`); `purge_modules` (`loader.py:95`) clears all generations by prefix. Same rules used by in-process host and worker server ("one loading fact source").
- `resolve_health_report` (`loader.py:106`): runs `diagnostics_entry`; any failure degrades, never raises.

### 1.4 Host — lifecycle state machine

`app/extensions_platform/host.py`
- `ExtensionState` (`host.py:84`): discovered → compatible → (loading) → active | degraded; failed (rollback); incompatible; disabled; quarantined.
- `ExtensionRecord` (`host.py:97`), `HostPolicy` (`host.py:125`): roots/allow/block/builtin_ids, grants, feature_flags, extension_settings, `allow_local_untrusted_activation`, `secrets`, `network_allow`, `artifact_roots`, `trusted_publishers`, `trust_signed`, `allow_unsigned_dev`, `max_worker_crashes`, `trust_store`, `isolation_backend`, `stream_window`, `max_stream_events`, `version_pins`.
- `ExtensionHost` (`host.py:171`): `discover` (`:211`, re-reads trust store first), `_apply_signature_verdict` (`:299`: tampered/invalid/revoked → QUARANTINED; signed_verified + trust_signed → elevate local_untrusted→trusted_extension; retired key never elevates), `validate_extension` (`:403`: API/core compat, permission vocab, high-risk-vs-trust, dependency graph + cycle detection, entry existence; idempotent from `baseline_diagnostics`), `activate` (`:610`), `_reconcile_declarations` (`:848`: UNDECLARED_REGISTRATION = error→rollback, DECLARED_BUT_UNREGISTERED = warning→degraded), post-activate health gate (`:777`), trusted-pack post-discovery tamper check (`:811`), `_activate_worker` (`:931`), `invoke_model_provider` (`:1176`), `disable/enable` (`:1398/:1425`), `deactivate` (`:1447`), `unload` (`:1549`), `reload` (`:1573`), `upgrade` (`:1701`), `_version_pin_error` (`:1776`), `refresh_revocations` (`:1787`), `activate_all` (`:1900`), `health` (`:1915`), `status_report` (`:1943`).
- Singleton wiring: `configure_extension_host` / `get_extension_host` (`host.py:2002-2012`); `ExtensionHost.from_settings` (`:186`) via `settings_bridge.host_policy_from_settings`.
- Process integration: `app/main.py:151-197` — lifespan, gated by settings `EXTENSIONS_ENABLED` (default **False**; `app/core/config.py:171`). Wires `configure_extension_host(ExtensionHost.from_settings(registry))`, projection refresher hook (`app/extensions_platform/refresh.py:26` → rebuilds `list_available_tools` args model + recompiles runtime manifest `app/lib/gis/runtime_manifest.py` after every projection commit), marketplace bootstrap, and a revocation refresh tick (`app/main.py:68`).
- Trust: `app/extensions_platform/trust.py` — `TrustLevel` (core / trusted_builtin / trusted_extension / local_untrusted / blocked), `resolve_trust` (`trust.py:42`): block > allow > builtin > local_untrusted. Docstring is explicit: **in-process import is trusted-code boundary, not a sandbox**.
- Permissions: `app/extensions_platform/permissions.py` — fixed vocabulary (`Permission` `:25`, 9 perms incl. network/artifact_read/artifact_write/secrets/db_write/external_process/model_provider...), `PermissionGrantSet` (`:69`), grants only via `EXTENSION_PERMISSION_GRANTS` (`parse_grants_config` `:97`), `validate_declared_permissions` (`:134`), `HIGH_RISK_PERMISSIONS`. Declaration ≠ authorization.

### 1.5 ExtensionContext — the projection facade (extensions never see registries)

`app/extensions_platform/context.py`
- `ExtensionContext` (`context.py:29`). Every registration runs: SDK spec validation → `_require_declared` (`context.py:60`, manifest promised it else `UNDECLARED_REGISTRATION`) → namespacing (`manifest.namespaced_*` helpers `manifest.py:520-531`: tools `<ns>_<name>`, algorithms `<ns>.<id>`, source types `<ns>_<st>`, cartography/recipes `<ns>_<id>`) → live-registry collision pre-check (`REGISTRY_PROJECTION_COLLISION`) → write → `ProjectionLedger.record` undo (`ledger.py`).
- Entry points: `register_tool` (`context.py:84`; also checks tool perms ⊆ manifest perms, and `_check_references` `:135` — tool `capabilities` must exist in CapabilityRegistry, `algorithms` must already be registered), `register_algorithm` (`:170`; capability/tool existence re-check, builds core `AlgorithmDescriptor` via `sdk/algorithm.py:155`), `register_data_provider` (`:221` → data-fabric `AdapterSpec`), `register_cartography_item` (`:279` → component/map-model/theme registries; component type slots exclusive per `:322`), `register_workflow_pack` (`:459` → RecipeRegistry; recipe capability refs validated to prevent fatal runtime-manifest compile failures `:478`; recipe ids must be `<ns>_` prefixed), `register_model_provider` (`:530` → typed invoke tool `<ns>_<pid>_invoke`, tier 1, `external_side_effect`), `get_secret` (`:617`, provisioning = authorization, default deny), `load_sibling` (`:638`, sibling modules under entry module namespace).
- `registered_ids` (`:635`) feeds host reconciliation.

### 1.6 Execution models / sandbox reality

- **in_process** (default): import into host interpreter. Trusted-code boundary; only SDK channels permission-gated (`docs/extension-platform/limitations.md`).
- **worker** (`manifest.execution.mode="worker"`, `ExecutionDeclaration` `manifest.py:206`): separate interpreter via `app/extensions_platform/worker/`:
  - `protocol.py` framed JSON-lines RPC (V3 streaming with credit window), `server.py` (worker entry, event loop, loads pack with the same `loader.py` rules), `client.py` (`WorkerProcess`, host side; frame budgets, crash counting → quarantine at `max_worker_crashes`), `context.py` (worker-side activation facade + broker facade), `spawn.py` (POSIX rlimits + sanitized env + process group; **rlimits are POSIX-only**), `isolation.py` (`:164` `build_bwrap_command`: bwrap `--unshare-all` + ro-bind of `<repo>/app` + pack only — repo root invisible; per-spawn failure = typed `ISOLATION_UNAVAILABLE`, never silent fallback), `projection_v3.py` (`project_worker_algorithms/providers/cartography_and_recipes`: host-side proxies so all declared sections are worker-capable; provider mixin → `WorkerDataProviderAdapter` at `:168`).
- Resource budgets declared per-pack (`max_memory_mb` ≤8192, `max_cpu_seconds` ≤86400, `call_timeout_s` ≤3600, `max_output_bytes` ≤64MiB, `max_stream_events`, `stream_window`) — enforced on the **worker path only**; in-process tools get only `ToolRegistry` timeout/cost.
- Capability broker: `app/extensions_platform/broker.py` — default-deny network (host allowlist `EXTENSION_NETWORK_ALLOW`), artifact roots (`EXTENSION_ARTIFACT_ROOTS`), secrets; per-extension bounded audit ring (`host.broker_audit` `host.py:1338`). Worker-only; in-process extensions do NOT pass through the broker.

### 1.7 Supply chain / distribution / marketplace (V3)

- `signing.py`: HMAC-SHA256 v1 (payload frozen byte-for-byte) + Ed25519 v2 (`SIGNATURE_DOMAIN_V2`, binds publisher+key_id+fingerprint); `verify_pack_signature` → closed status vocab (`signed_verified|signed_untrusted|signed_retired|invalid|tampered|missing|revoked`).
- `trust_store.py`: publisher keys active|retired|revoked, revoked fingerprints/packages; revocation precedes verification (quarantine).
- `sbom.py`: deterministic SBOM + secret-shape scan. `metrics.py`: Prometheus counters (ADR-0131). `distribution.py`: `ExtensionInstaller` — unified preflight (digest, trust-store verify, revocation incl. rollback, version pins, resolver conflicts, downgrade gate) → bounded streaming download → whitelist tarfile extraction (regular files only, ≤512 entries/8MiB) → staging → atomic swap → `versions/` + `.refresh` signal. `marketplace/`: content-addressed store (`store.py`, ≤4096 packs × 256 versions, file-lock serialized publish), read-only HTTP routes (`app/api/routes/extensions_marketplace.py`, mounted `app/main.py:821`), writes are operator CLI only. `resolver.py` + `version_constraints.py`: deterministic dependency resolution/Kahn order/upgrade conflict preflight.

### 1.8 CLI

`app/extensions_platform/cli.py` (`python -m app.extensions_platform`): `list/inspect/validate/doctor/scaffold/catalog/package/verify/sbom/certify/keygen/sign/publish/search/deprecate/revoke/install/rollback` (`cli.py:1207-1352`).

### 1.9 Conformance corpus

`app/extensions_platform/conformance.py`: deterministic generator + executor, 2000+ cases (`state:`, `fail:<DiagnosticCode>`, `diagnostic:<Code>` expectation grammar). Test entry `tests/unit/extensions_platform/test_conformance_corpus.py`. This is the house style for pinning platform behavior.

## 2. Authoritative registries (the only write targets)

| Registry | File | Register API | Collision policy | Extension undo |
|---|---|---|---|---|
| `ToolRegistry` | `app/tools/registry.py:387` | `register` `:459` (43+ descriptor kwargs, closed vocab `_DESCRIPTOR_KWARGS` `:442`; unknown kwarg = ValueError), `unregister` `:785` | same-name overwrite w/ warning for core; extensions pre-check `has()` — core never shadowed | yes |
| `AlgorithmRegistry` | `app/lib/gis/algorithm_registry.py:378` | `register` `:393`, `unregister` `:411`; `AlgorithmDescriptor` `:242` (scientific_status, conformance_tests, `BackendVariant`, `ResourceEnvelope`) | duplicate id raises | yes |
| `CapabilityRegistry` | `app/lib/gis/capability_registry.py:75` | `register` `:88` (dup raises); `register_dynamic` `:93` — **data-only** `induced.*` declarations, status ∈ planned|unavailable only, ≤128, no purpose_template (ADR-0191); `load_dynamic_capabilities` `:168` (yaml.safe_load, fail-closed) | frozen seam for extensions: tools may only *reference* capability ids | dynamic only |
| `AdapterRegistry` (Data Fabric) | `app/services/data_fabric/registry.py` | `AdapterSpec` dataclass; `get_registry().register/unregister`; capability flags for pushdown negotiation | append-only; alias rebinding raises; unknown source type → `UnsupportedSourceError` (never mock fallback) | yes |
| `RecipeRegistry` (gis_harness) | `app/services/gis_harness/recipes.py` (`get_recipe_registry`) | register/unregister | seed recipes keep-first; ext collision typed error | yes |
| Cartography | `app/lib/cartography/component_registry.py`, `model_library.py`, `themes.py` | register/unregister each | pre-checked get(); component type slots exclusive | yes |

### 2.1 ToolSurface projection to the LLM

- Tier system: tier 1 always in catalog; tier 2 loaded on domain keyword/recent-hit; tier 3 only via `list_available_tools` and admin-gated (`tier3_confirmed` `registry.py:86`; tier ≥3 forced `side_effect=destructive` `registry.py:629`). Extensions cannot publish tier 3 (`manifest.py:116`).
- `app/tools/meta_tools.py`: `list_available_tools` (`:51`) + `refresh_list_available_tools_args` (`:126`) — the schema-enum refresher the projection hook calls.
- Descriptors: `app/tools/descriptor.py` (`ToolDescriptor`, `validate_descriptor_fields`), fingerprints `registry.py:1006-1047`. Dispatch goes through `ToolDispatchService` (ADR-0068: all tool execution through dispatch). Pi-side surface: `app/services/chat/pi_native_surface.py`.

### 2.2 CapabilityGraph (V8)

`app/services/gis_harness/capability_graph.py` (ADR-0137): **read-only derived graph** over registries; nodes store identity + bounded summary only (no content copy), closed relation vocab, fingerprint-cached, `GIS_CAPABILITY_GRAPH_V8=0` kill switch. Node kinds: capability/algorithm/tool/model/workflow/methodology/template/component/artifact_type/execution_backend/provider. Consumers: `capability_resolution.py`, `candidate_planner_v8.py`, `qualification_v8.py`, `registry_validation.py`. **This is where "what capabilities exist" is projected for planning — a certified pack should appear here through the registries, not through a new graph.**

### 2.3 Algorithms / geocompute today

- Seeds: `app/lib/gis/algorithms/` (domain packs) + `app/lib/gis/capabilities/` (seed capability packs via `iter_capability_packs`). `algorithm_resolver.py` resolves capability→algorithm→tool; `backend_selection.py` cost-aware (ADR-0083); `runtime_manifest.py` compiles the authoritative runtime view (refreshed by the projection hook).
- Geocompute data plane: `app/services/geocompute/` (executor/graph/plan/budgets/resource_counter/reuse_index/run_evidence), exposed to the LLM via `app/tools/geocompute_tools.py` (`validate_execution_plan/execute_execution_plan/get_execution_run/cancel_execution_run`, `:51-197`). Extension algorithms bind to tools (must ship their own tool); they do not get their own geocompute backend slot (backend_variants ≤ 4 in descriptor).

## 3. What "certification" exists today

1. **Extension certification harness** — `app/extensions_platform/certification.py`, `certify_extension(host, extension_id)` (`certification.py:25`). Deterministic check list: `manifest_contract`, `api_compatible`, `dependency_constraints`, `signature` (incl. revocation), `sbom_secret_scan`, `package_layout` (symlink/irregular entry rejection `:164`), `resource_budget_declared` (`:191`), `protocol_compat` (`:225`), `provider_conformance` (`:242`), `execution_mode`, `lifecycle_smoke` (real activate→health→deactivate; `:124-153`), `deactivate_clean`. `certified = no fail checks`. Consumed ONLY by CLI `certify` (`cli.py:1282`). **No persistence, no gating of ToolSurface/CapabilityGraph, no per-capability runtime probe beyond health.**
2. **Activation-time gates** (always on): manifest fail-closed parse, declaration↔registration reconciliation, post-activate health gate, signature verdicts/quarantine, version pins, worker budget caps.
3. **Conformance corpus** — platform behavior pinning (not per-pack certification).
4. **Unrelated module with same name**: `app/lib/quality/certification.py` = cancellation & resource-safety *tables* for heavy compute modules (Quality platform); different axis, do not conflate.
5. **Algorithm scientific status**: `AlgorithmRegistry` enforces VALIDATED/PRODUCTION ⇒ `conformance_tests` non-empty (`sdk/algorithm.py:138` mirrors core rule); `run_authoring_checks` (`sdk/algorithm.py:198`) runs numerical smoke cases authoring-time only — **not** wired into certification.

## 4. Skills — three distinct systems (do not merge)

1. **Chat prompt skills / dynamic code skills** — `app/tools/skills.py`: `.md` skills with YAML frontmatter in `app/skills/` (`_md_skills` `:103`); `create_new_skill` dynamic tool gated by env `ALLOW_DYNAMIC_SKILLS` (`:309`); execution boundary = AST deny-list (`_BLOCKED_IMPORTS/_BLOCKED_BUILTINS/_BLOCKED_ATTRS/_BLOCKED_CHAIN_SEGMENTS` `:12-100`) + restricted builtins at exec (`:381+`), self-declared "NOT a security boundary" (`:302`). Audit issues **#1337** (runtime loader ignores skills-lock.json — no integrity check) and **#1338** (deny-list + env flag ⇒ in-process ACE if misconfigured) target exactly this file.
2. **GIS Skill / Procedure Library (ADR-0182)** — `app/services/gis_harness/skills/`: versioned YAML contracts in `library/<pack>/*.yaml` (core pack: cartography, data_preparation, network, raster_terrain, remote_sensing, spatial_statistics, temporal, vector). `SkillContract` (`contract.py`), fail-loud loader (`loader.py:24`, `LIBRARY_DIR` `:30`, unknown fields rejected, dangling refs = `SkillLibraryError`), `SkillPack`/`SkillPackRegistry` (`packs.py` — V1 has only builtin `core`; "pack is a governance boundary"), `SkillPolicy` (`policy.py`: modes none|guide|execute_guided|shadow|blocked|fallback; trust tiers core|candidate|experimental|quarantined|deprecated; `TRUSTED_PACKS=("core",)`; `GIS_SKILL_POLICY=0` kill switch), `promotion.py` (promotion pipeline), `resolver.py`/`retrieval.py`/`composition.py`. Runtime never mutates core skill assets (SkillPolicy红线). Tools: `app/tools/skill_library_tools.py` (gis_skill_search/detail/replay_check/policy), refresh `app/tools/skill_surface_refresh.py`.
3. **Developer skills** — `agent/skills/`, `.agents/skills/`, `skills-lock.json` (ZCode CLI lock). Product runtime never consumes.

## 5. Dynamic capabilities hook (ADR-0191)

`CapabilityRegistry.register_dynamic` + `load_dynamic_capabilities(path)` (`capability_registry.py:93/168`): external **data-only** declarations, `induced.*` prefix, planned|unavailable only, budget 128. This is the sanctioned precedent for "external packs extend the capability vocabulary without code" — a certification-gated *native-capable* path would be a deliberate policy change to a frozen seam.

## 6. Feature flags / kill switches (inventory)

- `EXTENSIONS_ENABLED` (master switch, default False — `app/core/config.py:171`), `EXTENSIONS_DIRS/ALLOW/BLOCK/BUILTIN_IDS`, `EXTENSIONS_ACTIVATE_UNTRUSTED`, `EXTENSION_PERMISSION_GRANTS`, `EXTENSION_FEATURE_FLAGS` (per-ext JSON), `EXTENSION_SETTINGS_JSON`, `EXTENSION_SECRETS_JSON`, `EXTENSION_NETWORK_ALLOW`, `EXTENSION_ARTIFACT_ROOTS`, `EXTENSION_TRUSTED_PUBLISHERS`, `EXTENSIONS_TRUST_SIGNED`, `EXTENSIONS_ALLOW_UNSIGNED_DEV` (audit **#1339**: no prod fail-fast), `EXTENSIONS_MAX_WORKER_CRASHES`, `EXTENSION_TRUST_STORE_PATH`, `EXTENSION_REGISTRY_DIR/URLS`, `EXTENSIONS_INSTALL_ROOT`, `EXTENSIONS_ISOLATION_BACKEND`, `EXTENSION_VERSION_PIN`, `EXTENSIONS_KEEP_VERSIONS`, `EXTENSION_STREAM_WINDOW`, `EXTENSION_MAX_STREAM_EVENTS` (`config.py:169-231`).
- Harness/related: `GIS_SKILL_POLICY` (`policy.py:46`), `GIS_CAPABILITY_GRAPH_V8` (`capability_graph.py:36`), `ALLOW_DYNAMIC_SKILLS` (`tools/skills.py:309`).
- Manifest-level: `feature_flags` map + dependency `feature_flag` gates (`host.py:565`, `:681`).

## 7. Docs that already describe this

`docs/extension-platform/`: architecture.md (projection principle table, module map, state machine), manifest-reference.md, permissions-and-trust.md, security-boundary.md, packaging.md, marketplace.md, compatibility.md, testing.md (canonical lifecycle test fixture), diagnostics-cli.md, authoring-{tools,algorithms,providers,cartography,workflow-packs}.md, ogc-stac.md, limitations.md (honest gaps). ADRs: 0104 (V1), 0105 (V2), 0119/0128 (V3), 0131 (V4 metrics); related: 0181 (CapabilityGraph V1→0137 V8), 0182 (Skill library), 0191 (trajectory→skill induction + dynamic capabilities).
