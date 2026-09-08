# 08 — Test Strategy

## 1. Map to existing conventions (raw 80 §D)

- `pytest.ini`: `testpaths=tests`, `pythonpath=.`, **`asyncio_mode=auto`** (async tests need no decorator),
  `timeout=60`/`timeout_method=thread`, coverage gate `--cov-fail-under=75` via full lane.
- Markers: `heavy`/`perf` (auto-skipped unless requested, `tests/conftest.py:pytest_collection_modifyitems`),
  `cartography`, `real_services`. Extension-platform tests stay in the default lane (zero network, zero LLM,
  like the conformance corpus lane, raw 80 §D).
- `tests/conftest.py` env baseline (`_ENV_BASELINE`, #663-B) and auth-bypass pinning apply; extension tests
  must set pack dirs via tmp_path fixtures, never real `extensions/` mutation at module scope.
- Contract tests live at tests root (no tests/contract/); new unit tests go in the existing
  `tests/unit/` subtree (373 entries, incl. `unit/gis_harness/`, `unit/lib/`).

## 2. Test files to create

```
tests/unit/extensions_platform/test_manifest.py            # schema validation, unknown-field rejection, version fields
tests/unit/extensions_platform/test_discovery.py           # bounded scan, determinism, no-import guarantee, caps
tests/unit/extensions_platform/test_lifecycle.py           # 10-stage state machine, states, quarantine
tests/unit/extensions_platform/test_lifecycle_deps.py      # dep resolution, cycle detection = error, skip semantics
tests/unit/extensions_platform/test_projection_rollback.py # journal + per-registry unregister, no-zombie asserts
tests/unit/extensions_platform/test_namespacing.py         # <ns>_ tools, <ns>.<id> algorithms/recipes, <ns>: adapters
tests/unit/extensions_platform/test_permissions.py         # projection-time enforcement vs descriptor fields
tests/unit/extensions_platform/test_diagnostics.py         # codes stable, errors block / warnings degrade
tests/unit/extensions_platform/test_compatibility.py       # matrix checks, CORE/EXTENSION/manifest version gates
tests/unit/extensions_platform/test_conformance_corpus.py  # generator ≥2000 cases, determinism double-run
tests/unit/extensions_platform/test_sdk_builders.py        # sdk/tool.py, algorithm.py, recipe.py, cartography.py, provider.py
tests/unit/extensions_platform/test_cli.py                 # list/inspect/doctor/load/unload/migrate exit codes + output
tests/unit/extensions_platform/test_ogc_hardening.py       # WMS honesty, STAC config, from_fabric_descriptor
tests/test_extension_platform_contract.py                  # tests-root contract tier entry (frozen seams hold)
```

## 3. Conformance corpus approach

- Deterministic generator (no LLM/IO), modeled on `app/evaluation/conformance.py`
  (`_expand_family` :616-661, `build_conformance_corpus` :663-686, duplicate-id fail-fast):
  family rows = (pack entry descriptor × scope variants × utterance variants), stable ids
  `XP-<pack>-<family>-<i>-<scope>-<utterance>`.
- **≥2,000 cases minimum** asserted in the default lane (precedent: ≥20,000 corpus runs in ~20 s offline,
  `tests/unit/gis_harness/test_conformance_corpus.py:29-53`); stratified sample + domain slices as fast
  feedback (mirroring `cases[::37]`, :56-73).
- Assertions per case: intent→capability→algorithm→tool resolution chain closes; namespacing preserved;
  routing order deterministic under the 11-key tuple semantics (`recipes.py:900-1005`); warning-code
  expectations like the anti-claim/contract builders (`app/evaluation/anti_claim.py:320-327`).

## 4. Registry parity / load-unload tests

- **Parity**: pack loaded via platform == same entries registered via raw registry calls — compare
  descriptors + registry fingerprints (order-independent, `registry.py:994-1005`) and runtime-manifest
  fingerprint before/after (staleness flags STALE_PLAN, `session_plan.py:157-174`).
- **Load-unload idempotence**: load→unload→load leaves registry fingerprint and entry set identical;
  unload asserts no dangling references via each registry's `validate()` (dangling refs are fatal in core:
  `registry_validation.py:120-186`, `runtime_manifest.py:397-410`).
- **No-zombie**: force a mid-register failure (e.g. invalid vocab raising in `ToolRegistry.register`,
  `registry.py:486-491`) → assert every authoritative registry byte-identical to pre-load.
- **Validation/eval rebuild parity**: extension entries visible to full-registry rebuild paths
  (`runtime_manifest.py:263-264`, `evaluation/runner.py:175-176`) — closes the vanish-on-rebuild gap
  (raw 10 §6.8).
- **Pi projection**: extension tier≤2 tools appear in `registered_surface_names` filter semantics
  (`pi_native_surface.py:256-273`); tier≥3 rejected at bridge gates (`agent_pi_bridge.py:495-506`);
  callable via `webgis_execute` classification (`pi_native_surface.py:73-150`).

## 5. SSRF / security tests

- `validate_url` matrix: RFC1918/loopback/link-local/metadata IPs, IPv4-mapped IPv6, `.local/.internal`,
  scheme allowlist (`security.py:55-65,:117,:153-159`); redirect re-validation hop-by-hop
  (`SSRFSafeHTTPAdapter` :316-331); byte cap via `bounded_get` (:350-387); same-origin cursor guard
  (:464-489).
- New: GDAL href pre-validation — STAC asset href → `validate_url` before `/vsicurl` open (regression for
  `rs/stac_client.py:212`); SafeHttp facade enforced for `network:true` extension tools.
- Secrets: `sanitize_profile_dict`/`redact_url` on pack diagnostics output (`security.py:218-274`);
  manifests containing literal secrets = validation error; plaintext-profile status quo documented, not
  regressed.
- Permission enforcement: tool declaring `network`/`requires_credentials` beyond manifest grant fails
  projection (`descriptor.py:183-220`).

## 6. Example pack as integration fixture

- `extensions/example-gis-tools/` (raw 07 §5) is exercised end-to-end: discover→…→activate via the CLI
  module, then one dispatch through `ToolRegistry.dispatch` and one recipe resolution — offline, in the
  default lane. Marks: none needed (fast); any network-touching case behind `real_services`.
- Corpus + parity tests import the fixture pack as their data source, keeping one canonical example.

## 7. Local verification commands (mirror `scripts/ci-local.sh`, raw 80 §F)

```bash
# Lint lane
ruff check --output-format=github app/ tests/ main.py manage.py

# Fast contract tier (--no-cov; add the new contract file)
pytest tests/test_tool_meta_contract.py tests/test_subagent_context_isolation_436.py \
       tests/test_ci_local_gate_contract.py tests/test_ci_perf_coverage_contract.py \
       tests/test_extension_platform_contract.py --no-cov -q

# Full default lane (includes tests/unit/extensions_platform/; coverage gate 75)
pytest --cov=app --cov-report=term-missing --cov-fail-under=75 --timeout=60 \
       --timeout-method=thread -m "not perf and not cartography and not real_services" -q

# Extension-platform slice during development
pytest tests/unit/extensions_platform --no-cov --timeout=60 -q
```

Notes: ruff scope is `E4, E7, E9, F` with default line length (E501 unselected, raw 80 §F) — keep new code
clean under those rules; no mypy exists, so type-correctness is enforced by tests, not a typecheck lane.
