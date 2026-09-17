# GAP ANALYSIS — exists today vs what Pack SDK + certification v2 needs

Legend: ✅ exists (reuse) · 🟡 partial (small additive work) · ❌ missing (build, inside existing platform).

## What the platform already gives us (do NOT rebuild)

- ✅ Pack format & discovery: `GisExtensionManifest` (fail-closed, namespaced, versioned) + bounded discovery + content fingerprint (`manifest.py`, `discovery.py`).
- ✅ Aggregate "one pack declares many capability kinds": manifest sections for tools/algorithms/data_providers/cartography_items/workflow_packs/model_providers; `ExtensionContext.register_*` projects each into the authoritative registry with ledger rollback (`context.py`).
- ✅ Declared↔implemented reconciliation at activation: `host._reconcile_declarations` (`host.py:848`) — undeclared registration = error, declared-but-missing = degraded.
- ✅ Signing/SBOM/trust store/revocation/marketplace/install (V3): `signing.py`, `sbom.py`, `trust_store.py`, `marketplace/`, `distribution.py`.
- ✅ Isolation & resource budgets: worker process + broker + rlimits + frame budgets + bwrap option (`worker/`, `broker.py`).
- ✅ Deterministic behavioral corpus machinery: `conformance.py` (expectation grammar, generator/executor pattern).
- ✅ Certify CLI (one-shot): `certification.py::certify_extension` + `cli.py certify`.
- ✅ Authoring-time checks: `run_authoring_checks` (numerical smoke), `validate_tool_algorithm_parity` (`sdk/algorithm.py`).
- ✅ Capability vocabulary & graph: frozen `CapabilityRegistry`, dynamic `induced.*` data-only hook, read-only `CapabilityGraph` V8.
- ✅ GIS Skill packs (content axis): `SkillPack`, YAML library, `SkillPolicy` trust tiers, promotion (`gis_harness/skills/`).

## Gaps against "Pack SDK + automatic capability certification (v2)"

1. ❌ **Staged pipeline (declared → schema → implementation → test → runtime probe).** Today `certify_extension` is a flat check list; the closest staging is manifest parse → activate → health. Missing: an explicit stage machine with per-stage evidence (pass/fail/warn + details per stage) that can run headlessly in CI AND at runtime probe time.
   - 🟡 Schema stage: settings_schema JSON-schema size check exists (`manifest.py:399`); no validation of tool `parameters` schemas at certify time (only at projection).
   - 🟡 Test stage: `NumericalSmokeCase`/`run_authoring_checks` exist but are authoring-side only; certification does not execute them; no pack-shipped test discovery.
   - ❌ Runtime probe stage: only `lifecycle_smoke` (health). No probe that *invokes* a projected tool/algorithm/provider with pinned arguments and asserts a result contract (worker mode gives the isolation to do this safely).
2. ❌ **Persistent, fingerprint-bound certificate artifact.** `certify_extension` returns a dict; nothing stores it. Needed: `certification.json` (deterministic, bound to `compute_fingerprint` output, schema-versioned) written next to the pack (or in install-root metadata), re-validated at discover/activate/upgrade; invalidation on fingerprint change.
3. ❌ **Certification as a gate** — today informational only. Missing: host policy (`EXTENSIONS_REQUIRE_CERTIFIED`, default off for backward compat) that refuses activation of uncertified/failed packs; plus projection of certification status into `status_report`, CLI, and CapabilityGraph node attributes (`origin=extension:<id>`, `certified`, `trust`).
4. 🟡 **Pack-level SDK aggregate.** Per-section specs exist; missing one `GisPackSpec`-style builder (validate whole pack: intra-pack parity already exists for algo→tools; extend to tools→capabilities, providers→permissions, skills→domain vocab; duplicate projected names across sections partially covered at `manifest.py:489` only for `<pid>_invoke`).
5. 🟡 **Skill packs inside the extension manifest.** GIS Skills are YAML library assets loaded fail-loud from `library/` at import; extensions cannot ship skills. Needed: manifest `skills` section + `ctx.register_skill(spec)` projecting a namespaced `SkillContract` into an overlay consumed by `SkillResolver` — WITHOUT mutating core assets at runtime (SkillPolicy红线) and without a second registry (add an additive `register_external`-style seam on the existing loader/registry singleton).
6. 🟡 **Resource profile as first-class, mode-independent concept.** Budgets exist only in `execution` (worker). Needed: named resource profiles (e.g. `light|heavy|isolated`) that (a) map to `ToolRegistry` `timeout/cost/tier` at projection for in-process packs, (b) map to `ExecutionDeclaration` budgets for worker packs, (c) are enforced at the single enforcement point the platform already owns (projection + worker spawn).
7. ❌ **Certified capability semantics.** `CapabilityRegistry` is a frozen seam: extension tools may only reference capability ids; only data-only `induced.*` dynamic entries can extend the vocabulary. Decision needed (see DECISIONS.md): keep the freeze (recommended for v2: certified packs register *tools/algorithm descriptors* that the graph already indexes) vs. introduce a governed `certified_extension.*` native-capability lane.
8. 🟡 **Upgrade/uninstall certificate lifecycle.** `deactivate/unload/reload/upgrade` + installer preflight exist; certificate (once persisted) must be invalidated on fingerprint change and re-issued by upgrade preflight (hook point: `distribution.py` preflight + `host.upgrade`).
9. 🟡 **Windows story for probes.** rlimits/bwrap are POSIX; on Windows, certification probes must run under wall-clock timeout + output caps with honest typed degradation (mirror `docs/extension-platform/limitations.md` honesty rules), and tests must self-skip subprocess-dependent cases (baseline shows 8 env-failures on this machine).

## Minimal-additive integration points (adapter/projection, NOT a new registry)

| Need | Integration point | Change shape |
|---|---|---|
| Stage pipeline | new `app/extensions_platform/certification_pipeline.py` (or extend `certification.py`) | new module; reuse `DiagnosticCode`, `conformance.py` expectation grammar, `sdk.algorithm.run_authoring_checks`, worker probes via `worker/client.py` |
| Certificate artifact | write `certification.json` in pack dir (excluded from fingerprint like `signature.json` — needs a constant next to `discovery.SIGNATURE_FILENAME`) | additive constant + reader in discovery/host policy |
| Gate | `HostPolicy.require_certified` + check in `host.activate` before `LOADING` | ~10 lines in host.py (additive, flag-gated) |
| Whole-pack SDK | new `app/extensions_platform/sdk/pack.py` (`GisPackSpec` + `ctx.register_pack(spec)` fan-out to existing register_*) | new file; zero changes to context.py pipeline |
| Skills in packs | manifest `skills` section (api floor gate like `_validate_v2_features`) + `ctx.register_skill` → additive seam on `gis_harness/skills` loader (namespaced overlay registry object owned by the same module — not a parallel system) | small additive |
| Graph projection | capability_graph node attribute enrichment at build time (read host record views); OR defer while #1336 owns the file | read-only |
| Resource profiles | map profile → projection kwargs (`timeout/cost`) + `ExecutionDeclaration` defaults; enforcement stays at existing points | additive mapping table |
| Uninstall/upgrade cert | hook certificate re-issue into `distribution.preflight` + `host.upgrade`/`reload` | additive calls |
