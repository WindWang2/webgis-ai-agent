# Limitations (honest list)

What the GIS Extension Platform (ADR-0104 V1 + ADR-0105 V2) deliberately
does **not** do. Each item is a real boundary in the code on this branch,
documented as such — no aspiration, no marketing.

## Security

- **In-process extensions are trusted code.** The default execution mode
  (`execution.mode=in_process`, or no `execution` section at all) imports
  the extension into the host interpreter with the same memory and OS
  permissions as the core. Trust levels and the permission system gate the
  SDK-provided channels only; `EXTENSIONS_BLOCK` (quarantine before
  import) remains the only hard containment control for that code. Details
  in [security-boundary.md](security-boundary.md).
- **Worker isolation is not a kernel sandbox.** `execution.mode=worker`
  gives process isolation + capability broker + resource enforcement — a
  separate interpreter with sanitized env, rlimits, output caps, and a
  default-deny broker. But worker code still runs **as the service user**:
  syscalls are not filtered, and arbitrary `os.*` calls inside worker code
  (opening files, opening sockets directly) are not blocked by the
  platform. Only the SDK/broker channels are gated. See
  [security-boundary.md](security-boundary.md) for the full threat model
  and the "claims we do NOT make" section.
- **In-process extensions do not pass through the capability broker.** The
  broker (network allowlist, artifact roots, secret provisioning) gates
  worker extensions only. An in-process extension with the `network`
  permission uses the platform HTTP helpers as in V1; artifact and secret
  access via `ctx.get_secret` is provisioning-gated, but filesystem access
  outside SDK helpers is unconstrained.

## Extension types

- **`model_provider` is GIS domain inference only.** V2 implements the
  type for GIS reasoning models (segmentation / detection / classification
  services), projected as typed invoke tools. LLM chat transport is **not**
  extensible: ADR-0102's `ModelDescriptorRegistry` stays a closed,
  config-driven surface and extensions cannot attach LLM transports.

## Ecosystem

- **No marketplace or distribution service.** V2 adds content signing
  (HMAC), verification, SBOM, and a certification CLI, but there is still
  no discovery/update channel. Packs arrive as directories on the
  operator's filesystem; trust is operator config
  (`EXTENSIONS_BUILTIN_IDS` / `EXTENSIONS_ALLOW` / publisher keys), and
  fingerprints remain tamper *detection*, not provenance.
- **Signing is shared-key HMAC, not asymmetric.** `hmac-sha256` over the
  content fingerprint means publisher ≈ operator: anyone who can verify a
  signature holds the same secret used to create it. This is
  authentication of "the operator approved this content", not independent
  publisher identity. Asymmetric signatures are follow-up (see
  ADR-0105 Non-goals).
- **Recipe routing weights are not externalizable.** Recipe selection
  scoring in gis_harness is core policy. Extensions contribute recipes and
  a `priority` tie-break; they cannot tune routing/weighting.

## Provider API surface

- **The core provider ABC is unchanged; V2 mixins are not yet dispatched.**
  `GeospatialDataSourceAdapter` is still exactly the seven sync methods.
  V2 adds the optional SDK mixins `StreamingVectorProvider`,
  `TileProvider`, `RasterWindowProvider` (detected via
  `extended_provider_capabilities()`), but data_fabric does **not yet
  dispatch to them** — an adapter implementing them gains the capability
  label, not the runtime behavior. Dispatch wiring is an explicit ADR-0105
  follow-up.

## Host model

- **Single-process host.** No locks, no cross-process coordination, no
  distributed activation/registry protocol. Multiple server workers each
  discover and activate independently (each spawning its own extension
  worker processes). A distributed host protocol is ADR-0105 follow-up.
- **In-process health checks are synchronous and unbounded.** The host
  invokes `diagnostics_entry` synchronously for in-process extensions with
  no timeout or budget — a hanging check hangs activation. (V2 closes this
  for the worker path: worker health is a bounded RPC under
  `execution.call_timeout_s`.)
- **Deactivate keeps in-process code loaded.** For in-process extensions
  nothing changed: projections roll back, but modules stay in
  `sys.modules` until `unload()`/`reload()`. Worker-mode extensions exit
  their process on deactivate — nothing of them remains in the host.
- **Rollback is operator-driven.** The host keeps no version copies.
  Upgrades refuse version regressions unless `allow_downgrade=True`, and
  rollback means the operator restores the old pack directory and calls
  `reload(allow_downgrade=True)`. There is no automatic rollback store.
- **Worker calls are serial.** One worker serves one extension with at
  most one in-flight call; a second concurrent call fails with typed
  `operation_in_flight` (and `deactivate` during a call is refused the
  same way).
- **Worker model providers cannot stream.** The single-frame RPC cannot
  carry an event stream; manifests declaring worker mode plus a
  `streaming` provider capability are rejected at parse time, and
  `invoke_model_provider(..., stream=True)` on a worker provider is a
  typed refusal.

## Secrets

- **Provisioning is operator-managed; no vault integration.** Secrets
  arrive as `EXTENSION_SECRETS_JSON` (`{ext_id: {ref: value}}`) — plain
  env-config in the host process. There is no lease, rotation, audit
  sink, or vault backend; "provisioning as authorization" is the entire
  policy surface.

## Related core gaps (documented, not hidden)

- WCS and OGC API Tiles have no data_fabric adapters; XYZ is frontend-only.
  See the status table in [ogc-stac.md](ogc-stac.md).
- STAC asset materialization and PMTiles/S3 content fetch remain
  metadata-only seams in the catalog.
- The SSRF gate (core data fabric **and** the worker broker) validates
  before connection and is hostname-level; full DNS-rebinding defense
  would additionally require IP pinning at connect time (noted in
  `validate_url`). The broker inherits exactly this posture, no more.

## What is explicitly stable, despite the above

The load-bearing invariants — atomic activation with zero zombie entries,
fail-closed manifest handling, namespace isolation, declaration ↔
registration reconciliation, and the deterministic compatibility
judgments — are pinned by the conformance corpus (now 2032 cases /
2034 tests, including the `v2_contract` family) and are not listed as
limitations. If you find a deviation, it is a bug, not a policy; see
[testing.md](testing.md) for the lane that proves it.

## Resolved in V2 (previously listed here)

- ~~No `model_provider` extension type~~ — implemented in ADR-0105
  (Wave 10); removed from `RESERVED_FUTURE_TYPES`.
- ~~Post-startup lifecycle is diagnostic-only~~ — the projection-change
  hook recompiles the runtime manifest and refreshes tool args on every
  activate/deactivate/rollback (Wave 9), for in-process and worker
  extensions alike.
- ~~Worker-path health unbounded~~ — worker health is a bounded RPC
  (in-process checks remain sync-unbounded, documented above).

## Additions from the Round-1 review (2026-09-08)

- **Bytecode blind spot.** Fingerprints exclude `__pycache__`/`*.pyc`
  (import side effects). A writer with pack-dir write access could plant a
  crafted `.pyc` whose source header matches the untouched `.py`; the
  altered bytecode would execute with a stable fingerprint. V2's worker
  mode mitigates the *host* exposure (the host never imports pack code)
  and the handshake re-checks the fingerprint, but the blind spot itself
  remains — for in-process mode unchanged. Mitigation is OS-level pack-dir
  permissions; hash-pinned bytecode is future work.
- **Namespace ownership is per-id, not reserved.** Two extension ids may
  share a namespace (`foo.bar`, `foo.bar_baz`); all collisions fail closed
  (tools/algorithms/providers/recipes/themes and cartography `type`
  slots), but a namespace is not exclusively owned by one extension id.
- **`CORE_RELEASE_VERSION` is manual.** The core-version window compares
  against a constant in `app/extensions_platform/api_version.py` that must
  be bumped together with the app version on release, or windows silently
  shift.
- **Health checks are unbounded sync calls (in-process).**
  `diagnostics_entry` runs in-process with no timeout; a hostile
  diagnostics function of a *trusted* extension can hang the caller. Only
  enable packs you trust.
- **Deactivate keeps modules loaded (in-process).** `deactivate()` rolls
  back all projections but the imported module stays in `sys.modules`
  (fast re-activation); `unload()`/`reload()` actually purge it.

## V2 review additions (2026-09-09)

- **The conformance corpus does not spawn workers.** The 2032-case corpus
  pins manifest-layer contracts deterministically (including the whole
  `v2_contract` accept/reject matrix); worker *behavior* — real
  subprocesses, handshake, crash isolation, rlimits, broker RPC — is
  pinned by dedicated real-subprocess integration suites instead
  (`test_worker_integration.py`, `test_resource_limits.py`,
  `test_broker.py`, `test_worker_server.py`). Two lanes, both offline.
- **Broker network policy is hostname-level.** `EXTENSION_NETWORK_ALLOW`
  matches hostnames, not IPs; combined with the pre-connect SSRF gate this
  is the same DNS-rebinding posture as the core gate (see above). It is a
  coarse filter plus an authoritative private-address check — not egress
  firewalling.
- **`EXTENSIONS_MAX_WORKER_CRASHES` counts crashes per discovered
  record**, not over all time: the counter lives on the
  `ExtensionRecord`, so a fresh discovery that replaces a non-active
  - 一次**优雅 deactivate**（worker 存活时主动停用）同样把计数清零；崩溃路径不清零（worker 已死，计数必须跨代次存活才能到达 quarantine）。
  record (or `host.reset()`) clears it — while `enable()` alone does not.
  An extension that crashes twice, gets re-discovered, and crashes twice
  again has not yet reached the default quarantine threshold — quarantine
  is a state-machine event, not a rate limit.

## V3 additions (ADR-0120) — what is now real, and what still is not

Resolved in V3 (previously listed here):

- ~~Signing is shared-key HMAC, not asymmetric~~ — Ed25519 via
  `cryptography` with a file-backed trust store (rotation, retired, and
  revocation semantics). HMAC v1 verification is preserved byte-for-byte
  for existing packs. `retired` keys produce `signed_retired`: signature
  mathematically valid, **never** eligible for trust elevation.
- ~~No marketplace or distribution service~~ — a server-side registry
  (content-addressed blobs, file-locked publish, read-only HTTP API) and a
  distribution installer with a shared preflight, crash-safe two-rename
  swap with startup recovery, and versioned rollback. See
  [marketplace.md](marketplace.md).
- ~~Class-instance projections must be in-process~~ — worker mode now
  supports algorithms (serializable descriptors; execution stays in worker
  tools), data providers (7-method RPC proxy with dynamically inherited
  mixins), cartography and workflow packs (declarative payloads). Requires
  `api_version >= 1.2.0`; 1.1 manifests keep the V2 rejection.
- ~~Worker model providers cannot stream~~ — streaming rides protocol
  v3 (credit flow control, cooperative cancel, per-frame idle timeout that
  does **not** count as worker crashes). Runtime-gated on the negotiated
  protocol, not just the manifest version.
- ~~data_fabric does not dispatch the provider mixins~~ —
  `fabric_bridge` + `DataFabricManager.stream_catalog_item_features`
  dispatch to `StreamingVectorProvider`/`TileProvider`/`RasterWindowProvider`
  when present; without the mixin the behavior is an honest typed refusal
  (tiles/raster) or a logged degrade-to-sync-query (streaming). Existing
  query paths are byte-identical.
- ~~Single-process host, no cross-process coordination~~ — partially:
  hosts now share an install root + registry with a file-lock publish
  protocol and a `.refresh` notification signal. There is still **no
  distributed activation/registry consensus**; each host discovers
  independently and the signal is advisory.

Still true in V3 (unchanged or newly precise):

- **Worker isolation with bubblewrap is namespace-level OS confinement,
  not a kernel sandbox.** `--unshare-all` makes direct socket use
  unreachable (the broker becomes the only egress channel — enforced, not
  advisory) and the bind set is minimal (the repo root, `.env`, and local
  data are not visible). But seccomp/LSM syscall filtering is still out of
  scope; `EXTENSIONS_ISOLATION_BACKEND` defaults to `process`, and a
  per-spawn bwrap failure is a typed activation failure — the platform
  never silently falls back.
- **Rollback is operator-driven** — but now versioned: the installer keeps
  `versions/` archives and rollback runs the same preflight (a revoked
  version cannot be rolled back to).
- **Revocation propagation is lazy.** Revocations take effect at
  discover/verify/install/rollback/refresh checks. A long-running host
  that never performs these operations and never calls
  `refresh_revocations()` keeps serving a revoked extension; the `.refresh`
  signal and trust-store mtime checks are advisory polling, not push.
- **In-process health checks remain synchronous and unbounded**, and
  in-process `deactivate` still keeps modules in `sys.modules`. Drain is a
  worker-mode concept (in-process drain is an explicit no-op).
- **The conformance corpus does not spawn workers** (unchanged lane split);
  V3 adds real-subprocess suites for streaming, projection, isolation and
  the end-to-end completion proof (`test_v3_completion_proof.py`).
- **Fingerprint blind spots unchanged**: `__pycache__`/`.pyc` exclusion,
  no permission-bit/symlink-attribute coverage in the fingerprint itself;
  `package_layout` certification now *detects* symlinks but the signature
  still does not cover metadata.
