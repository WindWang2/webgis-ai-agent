# DECISIONS — recommended design for GIS Pack SDK + certification v2

Precedent style: ADR-0104/0105/0119 house rules — projection not parallel, fail closed, honest boundaries, additive-only core changes, every rule test-pinned.

## D1 — Reuse the extension platform as THE pack system; no second registry

A "Pack" IS an extension whose manifest uses the full declared-section surface. The Pack SDK is a *convenience aggregate*, not a new loader:
- New `app/extensions_platform/sdk/pack.py`: `GisPackSpec` holding the existing `ToolExtensionSpec / AlgorithmExtensionSpec / ProviderExtensionSpec / CartographyItemSpec / WorkflowPackSpec / (new) SkillExtensionSpec` + intra-pack parity validation (extends `validate_tool_algorithm_parity`), plus a single `activate_pack(ctx)` helper that fans out to `ctx.register_*` in dependency-safe order (tools ← algorithms ← workflow packs; capabilities referenced must exist).
- `ExtensionContext` gains ONE additive method `register_pack(spec)` (or packs just call the helpers in their `activate()`); the projection pipeline, ledger, reconciliation, and rollback stay byte-identical.
Rationale: ADR-0104 D1 ("projection rather than parallel") is the repo's constitution; the audit culture will reject any second registry.

## D2 — Manifest: additive sections behind an API floor, not a new format

Keep `GisExtensionManifest` as the only contract. Add optional sections:
- `pack: PackDeclaration` — `kind` (algorithm_pack | data_pack | cartography_pack | skill_pack | mixed), `resource_profile: Literal["light","heavy","isolated"] = "light"`, `certification: CertificationDeclaration`.
- `skills: list[SkillDeclaration]` — id (namespaced by projection), yaml_asset path inside pack, domain (must be in `SKILL_DOMAINS`), trust tier ≤ candidate.
- `certification: CertificationDeclaration` — `required_checks: list[str]` (subset of the fixed check vocab), `probes: list[ProbeDeclaration]` (`target: tool|algorithm|provider`, `target_id`, pinned `arguments`, `expect` using the `conformance.py` expectation grammar `expect_key/expect_value/tolerance`, optional `runs_in_worker: bool = true`).
- Gate exactly like `_validate_v2_features` (`manifest.py:424`): new features require `api_version >= 1.3.0` → bump `CORE_API_VERSION` minor (additive, per ADR-0105 Wave-1 precedent); old manifests byte-identical behavior.
Rationale: schema_version + api floor machinery already exists and is corpus-pinned; a separate pack format would be a second contract.

## D3 — Certification pipeline: staged, deterministic, fingerprint-bound, persisted

Extend `app/extensions_platform/certification.py` (or sibling `certification_pipeline.py`) with an explicit stage enum whose evidence is accumulated per stage:
1. `declared` — manifest parse + section budgets + (new) pack/skills/certification sections (already deterministic via pydantic).
2. `schema` — settings_schema + every declared tool `parameters` JSON-schema well-formedness (pure checks).
3. `implementation` — activate against a **scratch ToolRegistry** (the pattern used by `tests/unit/extensions_platform/test_example_pack.py`) + `_reconcile_declarations` + parity checks from D1. Real registries untouched for AlgorithmRegistry/CapabilityRegistry side-effect-free projection? NOTE: AlgorithmRegistry etc. are process singletons — certification MUST run activation in a fresh subprocess (worker server already gives exactly this: same loader rules) OR accept scratch-ToolRegistry + singleton-check like the CLI does today. Recommended: run stages 3–5 inside a **worker subprocess even for in-process packs** — this is the certification-time isolation story and needs no new sandbox (ADR-0105 worker semantics; on Windows fall back to scratch-registry in-process with honest `isolated=false` in the report).
4. `test` — run declared `NumericalSmokeCase`s (`run_authoring_checks`) + optional pack-shipped pytest files executed via `pytest --collect-only`-bounded runner in the same probe subprocess; deterministic, offline (CI lane responsibility for anything heavier).
5. `runtime_probe` — for each `ProbeDeclaration`: dispatch the projected tool through the projected registry (or worker RPC) with pinned args, compare with the conformance expectation grammar; wall-clock + output caps enforced; probe evidence recorded.
- Output: `{extension_id, fingerprint, schema_version, stages: [...], certified, trust, execution_mode, checks}` — byte-stable (fixed order, no timestamps — reuse `certification.py` docstring rules).
- Persist to `certification.json` in the pack dir (or `<install_root>/.certs/<id>-<fp12>.json` when read-only pack dirs): add constant e.g. `CERTIFICATION_FILENAME` beside `discovery.SIGNATURE_FILENAME` and **exclude it from `compute_fingerprint`** (same loop-prevention argument as signature.json — discovery.py:38-42 docstring).
- Invalidation: host compares stored `fingerprint` vs `record.fingerprint` at `discover`/`activate`/`reload`/`upgrade`; mismatch ⇒ uncertified (re-run pipeline via CLI or upgrade preflight).

## D4 — Gate: policy flag, default OFF (backward compat), projected everywhere

- `HostPolicy.require_certified: bool = False`, setting `EXTENSIONS_REQUIRE_CERTIFIED` (settings_bridge + config.py additive block). When true: `host.activate` refuses non-certified packs (typed diagnostic `CERTIFICATION_REQUIRED` — new DiagnosticCode, additive) BEFORE loading; deactivate of previously-activated packs unaffected.
- Projection of status: (a) `host.status_report` gains per-extension `certified` field (host.py:1943 additive); (b) CLI `certify --write`; (c) CapabilityGraph enrichment **read-only at build time** — add `certified`/`origin` node attributes sourced from host record views; if PR #1336 still owns `capability_graph.py`, ship this as a follow-up patch to avoid overlap (PARALLEL_OWNERSHIP.md).
- Do NOT gate ToolRegistry catalog membership by certification in v2 — tier/domains already control surface; gating happens at activation (simpler, honest).

## D5 — Skills in packs project into the GIS Skill Library, not a new store

- `ctx.register_skill(SkillExtensionSpec)` validates against `SkillContract` (ADR-0182) and registers a **namespaced overlay** entry via ONE additive seam on the existing library module (`app/services/gis_harness/skills/loader.py` / resolver-facing registry): e.g. `register_external_skill(contract, source_extension_id)` with dup-id = typed error, core ids never shadowable, runtime never writes `library/` files (SkillPolicy红线 "runtime期不修改 core 技能资产").
- Projection follows the same declaration↔registration reconciliation as other sections (`_require_declared("skills", ...)`).
- SkillPolicy trust mapping: pack skills enter as `candidate` (never `core` unless shipped in-repo); `TRUSTED_PACKS` untouched.
Rationale: gives skill packs a real home while keeping one skill registry and the existing policy/promotion machinery authoritative.

## D6 — Resource profile: one declaration, existing enforcement points

- `pack.resource_profile` maps: `light` → tool `cost="light"`, `timeout=_TOOL_TIMEOUT_S` default; `heavy` → `cost="heavy"` + explicit `timeout` required + probe `runs_in_worker=true`; `isolated` → requires `execution.mode="worker"` (manifest-level cross-field validation).
- Enforcement stays where it already lives: projection kwargs into `ToolRegistry.register` (timeout/cost/tier ceiling ≤2), `ExecutionDeclaration` caps for worker mode, broker default-deny for network/artifacts/secrets. No new enforcement layer.
- Honest Windows note: memory/CPU caps are POSIX-only; on Windows enforcement = wall-clock + output caps, report carries `resource_enforcement="wall_clock_only"` (mirror limitations.md style).

## D7 — Uninstall / upgrade semantics (reuse ledger + installer)

- Uninstall = `host.deactivate` (ledger reverse rollback, zero residue — pinned by conformance corpus) + `unload` (sys.modules purge) + optional pack-dir removal by operator/`ExtensionInstaller`.
- Upgrade = existing `host.upgrade` (`host.py:1701`) / installer preflight + **certificate re-issue**: preflight requires `certified=true` for the NEW fingerprint when `require_certified`; version pins and downgrade gate unchanged.
- Certified-state can never outlive content: fingerprint binding (D3) makes stale certificates structurally impossible to reuse.

## D8 — What we deliberately do NOT do in v2

- No native-capability lane for extension packs (CapabilityRegistry stays frozen for extensions; the `induced.*` data-only hook remains the only vocabulary extension — revisit via ADR if ever needed).
- No marketplace UI / HTTP write paths (operator CLI only, per ADR-0128).
- No changes to `app/tools/skills.py` (dynamic code skills) — that is audit-issues #1337/#1338 territory and a different axis.
- No in-process broker coverage (documented limitation stands; certification probes run isolated instead).
