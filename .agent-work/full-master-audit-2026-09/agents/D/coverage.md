# Agent D — Platform / Performance / Security / Integration — Coverage Census

Repo: `webgis-ai-agent` @ `master 2aabdc43` (2026-09-11)
Baseline read: `AUDIT_REMEDIATION_REPORT.md` (39 findings fixed: SEC-01..06, CORE-01..09, GIS-01..10, FRONT-01..14) + `git log --oneline -40`. None of those 39 are re-reported; two findings below flag *residual instances* of a fixed class with distinct code sites (D-4) and *defects inside the fixed guard itself* (D-3).

## 1. Census totals (production code in scope)

| Area | Files | Risk split (H/M/L) | Method |
|---|---|---|---|
| app/core/** | 14 | 8 H, 6 M | full read (auth, config, database, rate_limiter, signing, network, bridge_secret, client_ip line-by-line; auth_metrics, sre_metrics, base_layers, exception, logging_config structure+key paths) |
| app/main.py + app/api/routes/** | 32 (31 routes + `__init__`) | 12 H, 14 M, 6 L | line-by-line: main.py, auth, upload, static, task, pi_tools, ws, ws_collab, explorer, report, knowledge, config, metrics, local_data, layer, jobs, extensions_marketplace, templates, health; targeted read+grep on chat (SSE/guards/exec), project (sync-DB scan), mapspec_mutations, data_fabric, geocompute, lakehouse, workflow_runtime, map, raster, chat_resume, workflow_resume, analysis_graph |
| app/models/** | 10 | 2 H (db_model, upload), 8 M/L | db_model owner/auth columns read; rest structure |
| app/schemas/** | 9 | 3 M, 6 L | pagination + template/ref_descriptor key paths; rest structure |
| app/utils/** | 7 | 2 H (security.py, path.py), 2 M (sse.py full read, geojson), 3 L | |
| app/adapters/** | 2 | 2 M | base.py full; gov adapter SSRF path verified (DataFabricSecurity.validate_url) |
| app/db/** | 1 | L | empty shim |
| app/tasks/** (explorer task_chain) | 2 | L | structure only (Celery chain wiring, referenced from task_queue include list) |
| app/skills/** | 6 (runtime upload dir) | L | write target of /config/skills/upload; structure |
| app/extensions_platform/** | 45 | 6 H (loader, permissions, signing, distribution, trust_store, broker), 12 M, 27 L | loader/permissions/distribution full; signing/trust_store/broker/manifest/discovery key paths; rest structure |
| app/services root layer (unassigned) | 71 | 10 H (session_data, session_data_redis, task_queue, distributed_lock, s3_blob_store, report_export, report_service, runtime_validator, skill_creator, artifact_lifecycle), 25 M, 36 L | session_data/task_queue/runtime_validator full or near-full; pattern sweep (subprocess/shell=True/pickle/yaml.load/eval/raw-SQL-f-string/outbound HTTP) over all 71 + rag/ + explorer/; spot reads of the rest |
| app/services/rag/** | 5 | 2 H (faiss_store, vector_store-adjacent), 3 M | SEC-02/SEC-04 fixes verified in place; fcntl import issue found |
| app/services/explorer/** | 11 | 2 H (fetch_stage, orchestrator owner check), 9 M | fetch_stage full (SSRF path via adapter) |
| app/services raster root files | raster_store.py, raster_tile_service.py, raster_cartography_converter.py (+cog under GIS-08 fixed) | M | targeted |
| migrations/** | 40 | graph-validated all; 12 recent revisions read | alembic ScriptDirectory walk: 39 revisions, 1 head, 0 dup ids, 0 orphans; last-10 upgrade/downgrade symmetry reviewed |
| scripts/** | 25 py + quality/ dir | 6 H (check_generated_staleness, check_integration_preflight, allocate_adr, allocate_migration, gen_drift_report, gen_quality_manifest, quality_runner) | full read of the 7 gate scripts + their impl modules (app/lib/quality/artifact_graph.py, app/lib/integration/{adr,migrations_coord,ownership}.py); executed staleness + preflight live |
| docs/integration/** | ownership.json, adr-link-baseline.json, generated-artifacts ledger, frontend-behavior.json, RELEASE_READINESS.* | M | structure + live validation via preflight |
| Config: pyproject.toml, pytest.ini, alembic.ini, docker-compose{,.prod,.prod.secure}.yml, .env.example, requirements*.txt, Dockerfile, Dockerfile.prod | 10 | H | read; ENV=production + JWT required in prod compose verified; Dockerfile.prod USER appuser verified |
| tests/ | spot-check only | — | gate wiring (tests/quality/test_api_compatibility.py snapshot mechanism) |

Total production files enumerated: ~250 across scope; 100% entered census. Executed live: `alembic heads` (via ScriptDirectory), `check_generated_staleness.py` (RED, 16), `check_integration_preflight.py` (FAIL, generated_staleness RED), `gen_drift_report.py --check` (crash — see D-1), `gen_quality_manifest.py --check` (crash — D-1), remaining gen `--check` scripts, import probes for fcntl (D-7).

## 2. Explicit non-goals honored
- No repo files modified; no artifacts generated outside `.agent-work/full-master-audit-2026-09/agents/D/`.
- Excluded dirs owned by other agents (chat/, gis_harness/, tools/, modelops/, geocompute/, workflow_runtime/, data_fabric/, lakehouse/, network/, temporal/, spatial_decision/, mapspec/, gis_world_state/, planning/, collab/, jobs/ service internals, cartography_runtime/) — only their API-layer interfaces reviewed.

## 3. Verified-fixed (no re-report)
SEC-01 (report_export URL fetcher), SEC-02 (FAISS tenant fail-closed), SEC-03 (ws auth), SEC-04 (batch re-embed), SEC-05 (reltuples), SEC-06 (task owner_token), CORE-01..09, GIS-01/02/05/08 touchpoints seen in code with regression tests referenced. AUTH_DISABLED/ALLOW_PUBLIC_REGISTER/CORS-`*`/sqlite-in-prod fail-fast validators present and prod compose sets ENV=production.
