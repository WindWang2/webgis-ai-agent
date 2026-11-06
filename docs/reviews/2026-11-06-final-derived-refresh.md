# Final generated-artifact refresh

Run from the reviewed worktree using its approved environment and independent installed frontend dependencies, after all source changes and upstream integration. Do not source the original checkout's environment. Regenerate owning artifacts first; update the dependency ledger only last. These are the actual owning entry points used during this task; no manual generated-payload edits or debt-baseline updates.

```bash
.venv/bin/python scripts/gen_config_schema.py
.venv/bin/python scripts/gen_app_inventory.py
.venv/bin/python scripts/gen_api_docs.py
.venv/bin/python scripts/generate_endpoint_scope_matrix.py
.venv/bin/python scripts/situation_inspect.py --dump-schema
.venv/bin/python -m app.lib.cartography.ts_projection
.venv/bin/python -m app.lib.cartography.basemap_profile_projection
.venv/bin/python -m app.lib.cartography.catalog_docs
.venv/bin/python -m app.lib.gis.execution_catalog_docs
API_SNAPSHOT_UPDATE=1 .venv/bin/python -m pytest -q --no-cov tests/quality/test_api_compatibility.py
API_CONTRACT_FIELDS_UPDATE=1 .venv/bin/python -m pytest -q --no-cov tests/unit/api_contract/test_field_contract.py
REALTIME_SNAPSHOT_UPDATE=1 .venv/bin/python -m pytest -q --no-cov tests/quality/test_realtime_contract.py
.venv/bin/python scripts/gen_quality_manifest.py
.venv/bin/python scripts/gen_drift_report.py
.venv/bin/python scripts/gen_security_manifest.py
.venv/bin/python scripts/gen_resource_certification.py
.venv/bin/python scripts/gen_chaos_registry.py
.venv/bin/python scripts/gen_determinism_certification.py
.venv/bin/python scripts/gen_science_benchmark_manifest.py
.venv/bin/python scripts/gen_frontend_behavior.py
.venv/bin/python scripts/gen_capability_catalog.py
.venv/bin/python scripts/gen_quality_report.py
.venv/bin/python scripts/gen_release_readiness.py
.venv/bin/python scripts/check_generated_staleness.py --update
```

The release-readiness artifact records generation-time commit identity and runs its declared checks; its --check deliberately normalizes that identity to avoid a self-referential commit loop. Regenerate after source/policy changes, not solely because HEAD advanced. Inspect API/schema diffs, including intentional report-share DELETE, typed bodies and story zoom minimum0; generated snapshots are evidence of the source contract, not permission to hide an unexplained breaking change. Real-time update deliberately skips the updated snapshot test once; rerun without the update variable.

```bash
.venv/bin/python scripts/gen_config_schema.py --check
.venv/bin/python scripts/gen_app_inventory.py --check
.venv/bin/python scripts/generate_endpoint_scope_matrix.py --check
.venv/bin/python -m app.lib.cartography.catalog_docs --check
.venv/bin/python -m app.lib.gis.execution_catalog_docs --check
.venv/bin/python scripts/gen_quality_manifest.py --check
.venv/bin/python scripts/gen_drift_report.py --check
.venv/bin/python scripts/gen_security_manifest.py --check
.venv/bin/python scripts/gen_resource_certification.py --check
.venv/bin/python scripts/gen_chaos_registry.py --check
.venv/bin/python scripts/gen_determinism_certification.py --check
.venv/bin/python scripts/gen_science_benchmark_manifest.py --check
.venv/bin/python scripts/gen_frontend_behavior.py --check
.venv/bin/python scripts/gen_capability_catalog.py --check
.venv/bin/python scripts/gen_quality_report.py --check
.venv/bin/python scripts/gen_release_readiness.py --check
.venv/bin/python scripts/check_generated_staleness.py
.venv/bin/python -m pytest -q --no-cov tests/quality/test_api_compatibility.py tests/unit/api_contract/test_field_contract.py tests/quality/test_realtime_contract.py tests/quality/test_generated_artifact_graph.py tests/test_endpoint_scope_matrix.py tests/test_api_docs_drift.py tests/unit/test_platform_build_info.py tests/unit/test_execution_catalog_docs_drift.py tests/test_gis_situation_contract.py tests/test_gis_situation_observation_turn.py
```

Action IR golden ownership is separate from the dependency ledger: if the final source causes the expected, independently reviewed digest/semantic_changed delta, its owning command is `.venv/bin/python -m tests.unit.gis.test_action_golden --regen`, then rerun `tests/unit/gis/test_action_golden.py` without regeneration. Cartography corpus uses `GOLDEN_CORPUS_UPDATE=1` on the exact failed case and deliberately fails after writing; inspect that precise diff, then rerun without the variable. Do not regenerate a broad corpus to conceal a rendering discrepancy.

Previous successful artifacts evidence before the final new source batch: `/tmp/frontend-derived-generators-final.log`, `/tmp/frontend-final-derived-verified2.log`, `/tmp/frontend-generated-graph-final3.log` (5 graph tests). Final upstream-integrated checks remain root-owned.
