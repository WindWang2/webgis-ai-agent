# 00-baseline — Quality/Reliability/Security Platform V2

- 日期：2026-09-09
- baseline commit：`445ad30e`（origin/master，PR #1162 merge 之后）
- worktree：`/home/kevin/projects/webgis/quality-v2`
- branch：`feat/quality-v2-reliability-security`

## 基线事实（全部来自最新 origin/master 实测，非硬编码）

### findings 基线（docs/quality/quality-manifest.json，manifest_version=1，指纹 938b5bf1…）

| code | count | severity |
|---|---|---|
| TOOL_DESCRIPTOR_INCOMPLETE | 152 | low |
| TOOL_UNTESTED | 30 | medium |
| ALGO_HEAVY_NO_VARIANTS | 23 | medium |
| ALGO_NO_CONFORMANCE | 20 | medium |
| CAPABILITY_NO_CONFORMANCE | 16 | medium |
| **合计** | **241** | |

- 富化闸 PASS：side_effect/tags 84%（阈 83%）、latency/memory 100%（95%）、capabilities 62%（60%）。
- TOOL_UNTESTED 30 个清单：apply_layer_style, cancel_execution_run, control_floating_chart, cross_pcf_analysis, describe_artifact, detect_change_cva, detect_ratio_change, execute_execution_plan, find_artifacts_by_role, geary_c, get_execution_run, get_lineage, ica_transform, interpolation_model_compare, list_workspace_snapshots, mantel_test_analysis, mgwr_regression, mnf_transform, quadrat_analysis, restore_workspace_snapshot, save_workspace_snapshot, search_datasets, space_time_k_analysis, temporal_changepoint, webgis_checkpoint, webgis_compile_maplibre, webgis_map_combine, webgis_rollback, webgis_validate, webgis_world_state
- ALGO_NO_CONFORMANCE 20 个：admin.boundary.local, admin.boundary_lookup, data.federated.chain, data.ingest.pipeline, geometry.center_statistics, geometry.clip, geometry.dissolve, geometry.spatial_join, network.isochrone, network.route_external_api, network.service_area.simple, network.traffic_status_external, network.transit_route_external, poi.area_search, poi.query.local, raster.cog.convert, raster.source.dem, stats.category.breakdown, workspace.inspection.readonly, workspace.snapshot.durable

### 机制基线

- 生成链：`app/lib/quality/manifest.py`（唯一编译器）→ `scripts/gen_quality_manifest.py [--check]` → `docs/quality/QUALITY_MANIFEST.md` + `quality-manifest.json` → 字节闸 `tests/quality/test_quality_manifest_gate.py`。
- findings 当前**无 gate**（纯线索）；唯一棘轮是 GATE_THRESHOLDS 富化覆盖率。
- discovery 是**静态 token 匹配**（app/lib/quality/discovery.py:140-147）→ "测试提到工具名"即算 tested，test-only façade 风险。
- capability 双真相源：显式声明 vs `_derive_capability_backfill`（registry.py:53-79）。
- xfail KNOWN-GAP 4 个（strict=False）：CRS 裸异常、bowtie 静默统计、legend 突变丢弃、SVG 标注无截断。
- 安全 manifest 显式缺口：SEC-KG-01（artifact ownership）、SEC-KG-02（templates/knowledge delete authZ，零回归）。
- WS/SSE 无契约快照（api_compat.py:15-17 自认边界）。
- 存储：驱动级分支（database.py:13-48），无 differential harness；006be00c 实录 SQLite 无 FK 误绿；alembic head=0031_revision_indexes。
- 无 hypothesis/property/fuzz 设施；无 random order；~26 处固定墙钟断言（f2124e68 0.05→0.2s、d2232d5f 结构基线人工抬 53000）。
- cancellation 认证：52 个重计算文件仅 14 个有检查点。
- chaos：12 个 FAULTS，子系统 CACHE/CANCEL/INGEST/LLM/LOCK/REGISTRY，无 STORAGE 类。
- 本地 runner：scripts/quality_runner.py 9 车道；报告目录 .agent-work/quality-v1（gitignored）。
- 生成物再生成 = 手动纪律（c114d021/a2dce8a8 merge-ref 模式），无 stale detector。

### 其他 9 个并行 Epic 的边界（不跨界）

- harness-v5 / geocompute-v6 / lakehouse-v6 / query-v6 / science-v4 各自 worktree 在跑；本 Epic 只拥有 docs/quality/**、quality manifests、quality test framework、contract snapshots、security certification、local runner；对 app/tools、app/lib/gis、app/services 仅做 findings 修复所需的最小语义变更。
